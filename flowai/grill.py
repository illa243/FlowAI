from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codex_adapter import CodexAdapter
from .models import Workflow, normalize_managed_tasks
from .templating import extract_json

OWN_ANSWER = "Своя відповідь"
MATERIALS_QUESTION = (
    "Використовувати згенеровані матеріали чи розпочати розробку спочатку?"
)
MATERIALS_OPTIONS = [
    "Використати згенеровані матеріали й доробити їх",
    "Розпочати розробку спочатку, не спираючись на них",
    OWN_ANSWER,
]
FRESH_START_MARKER = "Розпочати розробку спочатку"

INSTRUCTIONS = (
    "Ти проводиш співбесіду з користувачем перед запуском ланцюга агентів. "
    "Твоя мета — вибити з нього все, чого бракує, щоб завдання стали "
    "однозначними й перевірюваними. Став РІВНО ОДНЕ питання за раз. "
    "Кожне питання супроводжуй 2-4 конкретними варіантами відповіді — "
    "не абстрактними, а такими, які справді змінюють результат. "
    "Не питай те, на що вже є відповідь у завданнях або в історії розмови. "
    "Коли інформації достатньо, поверни done=true."
)

QUESTION_SCHEMA = {
    "done": False,
    "question": "string",
    "options": ["string"],
    "rationale": "string",
}

SUMMARY_SCHEMA = {
    "summary": "string",
    "tasks": {"id завдання": "новий промпт"},
    "entry": "string",
}


@dataclass(slots=True)
class GrillQuestion:
    text: str
    options: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class GrillOutcome:
    summary: str = ""
    rewritten_tasks: dict[str, str] = field(default_factory=dict)
    rewritten_entry: str = ""


class GrillSession:
    """Prompt clarification session run before a Flow starts."""

    def __init__(
        self,
        workflow: Workflow,
        codex: CodexAdapter,
        model: str,
        workspace: Path,
        reasoning_effort: str = "medium",
        calibration: Any | None = None,
        generated_files: list[str] | None = None,
    ) -> None:
        self.workflow = workflow
        self.codex = codex
        self.model = model
        self.workspace = workspace
        self.reasoning_effort = reasoning_effort
        self.calibration = calibration
        self.generated_files = [str(path) for path in generated_files or []]
        self.history: list[tuple[str, str]] = []
        self._thread_id = ""
        self._done = False
        self._last_question = ""
        self._asked_materials = False

    def _attachment_paths(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for node in self.workflow.nodes:
            values: list[object] = list(node.config.get("attachments") or [])
            if node.kind == "tasks_manager":
                for task in normalize_managed_tasks(node.config.get("tasks")):
                    values.extend(task.get("attachments") or [])
            for value in values:
                path = Path(str(value)).expanduser()
                key = str(path)
                if key and key not in seen and path.is_file():
                    paths.append(path)
                    seen.add(key)
        return paths

    @staticmethod
    def _append_attachments(lines: list[str], values: object) -> None:
        if not isinstance(values, (list, tuple)):
            return
        paths = [str(value).strip() for value in values if str(value).strip()]
        if paths:
            lines.append("Вкладення:\n" + "\n".join(f"- {path}" for path in paths))

    def _flow_context(self) -> str:
        lines: list[str] = [f"# Flow «{self.workflow.name}»"]
        for node in self.workflow.nodes:
            lines.append(f"\n## Блок {node.title} ({node.kind}, id {node.id})")
            if node.kind == "tasks_manager":
                for index, task in enumerate(
                    normalize_managed_tasks(node.config.get("tasks")), start=1
                ):
                    lines.append(f"\n### Завдання {index} (id {task['id']})")
                    lines.append(str(task.get("prompt", "")))
                    self._append_attachments(lines, task.get("attachments"))
                continue
            if node.kind == "entry":
                lines.append(str(node.config.get("text", "")))
                self._append_attachments(lines, node.config.get("attachments"))
                continue
            instructions = str(node.config.get("instructions", "")).strip()
            if instructions:
                lines.append(f"Інструкції: {instructions}")
        return "\n".join(lines)

    def _history_text(self) -> str:
        if not self.history:
            return "Питань ще не було."
        return "\n".join(
            f"- Питання: {question}\n  Відповідь: {answer}"
            for question, answer in self.history
        )

    def _calibration_text(self) -> str:
        """Описати відхилення та зафіксоване бачення користувача."""
        report = self.calibration
        if report is None:
            return ""
        lines = [
            "# Чому попередня спроба не пройшла перевірку",
            f"Завдання: {report.task_title} (id {report.task_id})",
        ]
        if report.verdict_reason.strip():
            lines.append(f"Вердикт рев'ювера: {report.verdict_reason}")
        if report.root_cause.strip():
            lines.append(f"Причина: {report.root_cause}")
        for index, point in enumerate(report.points, start=1):
            lines.append(f"{index}. {point.title}")
            if point.detail.strip():
                lines.append(f"   {point.detail}")
            for image in point.images:
                lines.append(f"   Ілюстрація: {image.path} — {image.note}")
        notes = report.user_notes_text()
        if notes:
            lines.extend(
                [
                    "",
                    "# Бачення користувача — це рішення, а не побажання",
                    notes,
                ]
            )
        if report.skills_used:
            lines.append(
                "\nАгент працював зі скілами: " + ", ".join(report.skills_used)
            )
        if report.skills_missing:
            lines.append(
                "Рев'ювер вважає, що бракувало скілів: "
                + ", ".join(report.skills_missing)
            )
        if self.generated_files:
            lines.extend(
                [
                    "",
                    "# Файли, які створила попередня спроба",
                    *(f"- {path}" for path in self.generated_files),
                ]
            )
        return "\n".join(lines)

    def _ask(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        run = self.codex.run_agent(
            prompt=prompt,
            developer_instructions=INSTRUCTIONS
            + "\n\nВідповідай лише JSON за схемою:\n"
            + json.dumps(schema, ensure_ascii=False, indent=2),
            model=self.model,
            sandbox="read-only",
            workspace=self.workspace,
            reasoning_effort=self.reasoning_effort,
            attachments=self._attachment_paths(),
            resume_thread_id=self._thread_id,
        )
        self._thread_id = run.thread_id or self._thread_id
        parsed = extract_json(run.text)
        if not isinstance(parsed, dict):
            raise ValueError("Агент GrillMe повернув не JSON")  # noqa: TRY004
        return parsed

    def next_question(self) -> GrillQuestion | None:
        if self._done:
            return None
        if self.calibration is not None and not self._asked_materials:
            self._asked_materials = True
            self._last_question = MATERIALS_QUESTION
            return GrillQuestion(
                text=MATERIALS_QUESTION,
                options=list(MATERIALS_OPTIONS),
                rationale="Від цієї відповіді залежить увесь наступний промпт",
            )
        prompt = (
            f"{self._flow_context()}\n\n"
            f"{self._calibration_text()}\n\n"
            f"# Що вже з'ясовано\n{self._history_text()}\n\n"
            "Постав наступне питання або поверни done=true."
        )
        parsed = self._ask(prompt, QUESTION_SCHEMA)
        question_text = str(parsed.get("question", "")).strip()
        if bool(parsed.get("done")) or not question_text:
            self._done = True
            self._last_question = ""
            return None
        options = [
            str(item).strip()
            for item in parsed.get("options", [])
            if str(item).strip() and str(item).strip() != OWN_ANSWER
        ]
        options.append(OWN_ANSWER)
        self._last_question = question_text
        return GrillQuestion(
            text=question_text,
            options=options,
            rationale=str(parsed.get("rationale", "")).strip(),
        )

    def answer(self, text: str) -> None:
        self.record(self._last_question, text)

    def record(self, question: str, answer: str) -> None:
        self.history.append((question, answer))
        self._last_question = ""

    def finish(self) -> GrillOutcome:
        demand = ""
        if self.calibration is not None:
            demand = (
                f"Промпт завдання {self.calibration.task_id} треба переписати "
                "обов'язково — саме воно не пройшло перевірку. Решту завдань "
                "чіпай лише тоді, коли домовленості справді їх стосуються.\n"
            )
            if any(
                FRESH_START_MARKER in answer for _question, answer in self.history
            ):
                demand += (
                    "Користувач вирішив почати спочатку: у промпті має бути "
                    "явна вказівка не спиратись на вже створені файли, "
                    "з їхнім переліком. Самі файли не чіпаємо.\n"
                )
        prompt = (
            f"{self._flow_context()}\n\n"
            f"{self._calibration_text()}\n\n"
            f"# Домовленості з користувачем\n{self._history_text()}\n\n"
            f"{demand}"
            "Перепиши промпти тих завдань, яких стосуються домовленості. "
            "Не чіпай завдання, яких це не стосується — не включай їх у tasks. "
            "Збережи мову й структуру оригіналу, додай конкретику. "
            "У summary стисло перекажи ухвалені рішення."
        )
        parsed = self._ask(prompt, SUMMARY_SCHEMA)
        tasks = parsed.get("tasks")
        rewritten = (
            {str(key): str(value) for key, value in tasks.items()}
            if isinstance(tasks, dict)
            else {}
        )
        return GrillOutcome(
            summary=str(parsed.get("summary", "")).strip(),
            rewritten_tasks=rewritten,
            rewritten_entry=str(parsed.get("entry", "")).strip(),
        )
