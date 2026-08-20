from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - лише для типів
    from .models import FlowNode, Workflow

PROTOCOL_NAME = "work-review.md"
REPORT_NAME = "work-review-report.md"

# Скільки символів тексту зберігати в одному записі протоколу.
TEXT_LIMIT = 6000


def _fence(text: str, language: str = "text") -> str:
    body = text.strip()
    if not body:
        return "_(порожньо)_"
    if len(body) > TEXT_LIMIT:
        body = body[:TEXT_LIMIT] + "\n… (обрізано)"
    fence = "```"
    while fence in body:
        fence += "`"
    return f"{fence}{language}\n{body}\n{fence}"


class WorkReviewProtocol:
    """Протокол роботи блоків Flow, який веде сам рушій.

    Файл створюється до першої ноди, доповнюється після кожного проходу і
    в кінці віддається блоку Work Reviewer на аналіз. Агентів ні про що не
    просимо — усі дані рушій має сам.
    """

    def __init__(self, path: Path, workflow_name: str) -> None:
        self.path = path
        self.workflow_name = workflow_name
        self.started_at = datetime.now().astimezone()
        self.finished_at: datetime | None = None
        self.status = "running"
        self._headers: dict[str, str] = {}
        self._sections: dict[str, list[str]] = {}
        self._order: list[str] = []

    @classmethod
    def monitored_ids(cls, workflow: Workflow, reviewer: FlowNode) -> list[str]:
        """Які блоки веде протокол: за замовчуванням усі, крім наглядача."""
        candidates = [node.id for node in workflow.nodes if node.id != reviewer.id]
        if bool(reviewer.config.get("monitor_all", True)):
            return candidates
        selected = {
            str(item) for item in reviewer.config.get("monitored_nodes", []) if item
        }
        return [node_id for node_id in candidates if node_id in selected]

    def begin(
        self,
        workflow: Workflow,
        monitored_ids: list[str],
        records: dict[str, list[str]] | None = None,
    ) -> Path:
        from .models import NODE_LABELS

        self._headers.clear()
        self._sections.clear()
        self._order = list(monitored_ids)
        for node_id in self._order:
            node = workflow.find(node_id)
            if node is None:
                continue
            self._headers[node_id] = (
                f"{node.short_id} · {NODE_LABELS[node.kind]} «{node.title}»"
            )
            self._sections[node_id] = list((records or {}).get(node_id, []))
        self._write()
        return self.path

    def record(
        self,
        *,
        node: FlowNode,
        iteration: int,
        result_status: str,
        duration_seconds: float,
        prompt: str = "",
        instructions: str = "",
        attachments: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        text: str = "",
        error: str = "",
        thread_id: str = "",
        resumed: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if node.id not in self._sections:
            return

        lines = [f"### Прохід {iteration} · {result_status} · {duration_seconds} с"]
        if node.is_agent:
            lines.append(
                f"- Модель: `{node.config.get('model', '')}` · "
                f"міркування: `{node.config.get('reasoning_effort', '')}` · "
                f"доступ: `{node.config.get('sandbox', '')}`"
            )
        if thread_id:
            state = "продовжено" if resumed else "новий"
            lines.append(f"- Codex-тред: `{thread_id}` ({state})")
        if attachments:
            lines.append(
                "- Вкладення: " + ", ".join(f"`{item}`" for item in attachments)
            )
        for key, value in (extra or {}).items():
            lines.append(f"- {key}: {value}")

        if prompt:
            lines.append("\n**Промпт**\n" + _fence(prompt))
        if instructions:
            lines.append("\n**Постійні інструкції**\n" + _fence(instructions))
        if steps:
            lines.append("\n**Кроки агента**")
            for index, step in enumerate(steps, start=1):
                lines.append(
                    f"{index}. `{step.get('kind', '?')}` — {step.get('summary', '')}"
                )
        if text:
            lines.append("\n**Результат**\n" + _fence(text))
        if error:
            lines.append("\n**Помилка**\n" + _fence(error))

        self._sections[node.id].append("\n".join(lines))
        self._write()

    def snapshot(self) -> dict[str, list[str]]:
        """Записи протоколу для збереження в чекпоінті запуску."""
        return {node_id: list(items) for node_id, items in self._sections.items()}

    def finish(self, status: str) -> Path:
        self.status = status
        self.finished_at = datetime.now().astimezone()
        self._write()
        return self.path

    def _write(self) -> None:
        stamp = self.started_at.strftime("%Y-%m-%d %H:%M:%S")
        head = [
            f"# Протокол роботи Flow «{self.workflow_name}»",
            "",
            f"- Запуск: {stamp}",
            f"- Статус: {self.status}",
        ]
        if self.finished_at is not None:
            head.append(
                f"- Завершено: {self.finished_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        head.extend(
            [
                "",
                "Кожен блок Flow має власну секцію нижче, названу його коротким id.",
                "Записи додає рушій FlowAI автоматично після кожного проходу блоку:",
                "промпт, постійні інструкції, шляхи вкладень, реальні кроки агента",
                "та отриманий результат.",
                "",
                "## Спостережувані блоки",
                "",
            ]
        )
        for node_id in self._order:
            header = self._headers.get(node_id)
            if header:
                head.append(f"- {header}")

        body: list[str] = ["", "---", ""]
        for node_id in self._order:
            header = self._headers.get(node_id)
            if not header:
                continue
            body.append(f"## {header}")
            body.append("")
            records = self._sections.get(node_id) or []
            if not records:
                body.append("_Блок не виконувався._")
                body.append("")
                continue
            for record in records:
                body.append(record)
                body.append("")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(head + body).rstrip() + "\n", encoding="utf-8")
