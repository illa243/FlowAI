from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .calibration import CALIBRATION_SCHEMA

FLOW_FORMAT_VERSION = 2
DEFAULT_NODE_WIDTH = 220.0
DEFAULT_NODE_HEIGHT = 130.0


class UnsupportedFlowFormat(ValueError):
    """Файл проєкту не може бути представлений поточною моделлю."""


NODE_LABELS = {
    "entry": "Entry prompt",
    "tasks_manager": "Tasks Manager",
    "prompt_reviewer": "Prompt Reviewer",
    "executor": "Task Executor",
    "task_reviewer": "Task Reviewer",
    "result": "Result",
    "calibrator": "Calibration Stop",
    "work_reviewer": "Work Reviewer",
}


NODE_COLORS = {
    "entry": "#3B82F6",
    "tasks_manager": "#2563EB",
    "prompt_reviewer": "#0891B2",
    "executor": "#7C3AED",
    "task_reviewer": "#D97706",
    "result": "#16A34A",
    "calibrator": "#E11D48",
    "work_reviewer": "#DB2777",
}


# Ноди, які запускають окремий потік Codex.
AGENT_KINDS = frozenset(
    {
        "prompt_reviewer",
        "executor",
        "task_reviewer",
        "work_reviewer",
        "calibrator",
    }
)

# Ноди без портів: не беруть участі в маршруті.
SIDECAR_KINDS = frozenset({"work_reviewer"})

# Ноди з входом, але без виходів: маршрут на них закінчується.
TERMINAL_KINDS: frozenset[str] = frozenset()

# Ці ноди ніколи не стартують Flow самостійно.
NEVER_SEEDED = frozenset({"calibrator"})

RESULT_PORTS = ("true", "false", "exhausted")
TASK_MANAGER_PORTS = ("next", "done")
DEFAULT_PORT = "out"


PROMPT_REVIEW_SCHEMA: dict[str, Any] = {
    "improved_prompt": "string",
    "notes": ["string"],
}

TASK_REVIEW_SCHEMA: dict[str, Any] = {
    "verdict": True,
    "score": 100,
    "pass_threshold": 80,
    "reason": "string",
    "issues": [
        {
            "defect_id": "stable-id",
            "category": "visual_preference|visual_mismatch|technical_blocker|missing_requirement",
            "severity": "info|warning|blocking",
            "description": "string",
            "target_files": ["string"],
            "target_regions": [{"id": "string", "x": 0, "y": 0, "w": 0, "h": 0}],
            "rule_id": "stable-machine-rule",
            "must_fix": "string",
        }
    ],
    "must_fix": ["string"],
    "evidence_files": ["string"],
    "checks": [
        {
            "check_id": "stable-check-id",
            "status": "pass|fail",
            "target_files": ["string"],
            "evidence_files": ["string"],
        }
    ],
    "system_error": None,
}


def _agent_defaults(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "instructions": "",
        "instruction_files": [],
        "prompt": "",
        "prompt_source": "template",
        "sandbox": "workspace-write",
        "workspace": "",
        "additional_folders": [],
        "output_format": "text",
        "output_schema": {},
        "attachments": [],
        "skills": [],
        # timeout_seconds тут колись був, але рушій його не читав ніколи.
        # Довгу роботу зупиняє користувач, а не таймер, тож поле прибрано,
        # щоб настройка не обіцяла поведінки, якої немає.
        "retries": 0,
        "memory": "thread",
        "context_soft_limit": 80_000,
        "prompt_cache_enabled": False,
        "qa_cache_enabled": False,
        "deterministic_qa_enabled": False,
        "read_only_audit": True,
    }
    base.update(overrides)
    return base


def new_managed_task(prompt: str = "") -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "prompt": str(prompt),
        "attachments": [],
    }


def normalize_managed_tasks(raw: Any) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    source = raw if isinstance(raw, list) else []
    for item in source:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or uuid4().hex)
        if any(existing["id"] == task_id for existing in tasks):
            task_id = uuid4().hex
        task = dict(item)
        task.update(
            {
                "id": task_id,
                "title": str(item.get("title", "")),
                "prompt": str(item.get("prompt", "")),
                "screen": str(item.get("screen", "")),
                "states": [
                    str(state)
                    for state in item.get("states", [])
                    if str(state)
                ],
                "acceptance_criteria": [
                    str(rule)
                    for rule in item.get("acceptance_criteria", [])
                    if str(rule)
                ],
                "attachments": [
                    str(path) for path in item.get("attachments", []) if str(path)
                ],
                "export_profile": str(
                    item.get("export_profile") or "baseline"
                ),
            }
        )
        tasks.append(task)
    return tasks or [new_managed_task()]


def managed_task_title(task: dict[str, Any], index: int) -> str:
    configured = str(task.get("title") or "").strip()
    if configured:
        return configured[:54]
    first_line = next(
        (
            line.strip()
            for line in str(task.get("prompt", "")).splitlines()
            if line.strip()
        ),
        "",
    )
    return first_line[:54] or f"Завдання {index + 1}"


def task_states_from_progress(
    tasks: list[dict[str, Any]], progress: dict[str, Any]
) -> list[dict[str, Any]]:
    """Показові стани завдань зі збереженого прогресу, нічого не змінюючи.

    Рушій рахує те саме по ходу запуску; ця функція потрібна, щоб підняти
    картинку з чекпоінта після перезапуску FlowAI.
    """
    failed = {str(item) for item in progress.get("failed_task_ids", [])}
    completed = {
        str(item)
        for item in progress.get("completed_task_ids", [])
        if str(item) not in failed
    }
    active_id = str(progress.get("active_task_id", ""))
    times = progress.get("times") or {}
    states: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id = str(task["id"])
        record = times.get(task_id) or {}
        states.append(
            {
                "id": task_id,
                "title": managed_task_title(task, index),
                "status": (
                    "failed"
                    if task_id in failed
                    else "completed"
                    if task_id in completed
                    else "running"
                    if task_id == active_id
                    else "pending"
                ),
                "seconds": round(float(record.get("seconds", 0.0) or 0.0), 3),
            }
        )
    return states


def _default_config(kind: str) -> dict[str, Any]:
    defaults: dict[str, dict[str, Any]] = {
        "entry": {
            "text": "Опишіть завдання для ланцюга агентів",
            "json": {},
            "attachments": [],
        },
        "tasks_manager": {
            "tasks": [new_managed_task()],
            "task_source": "static",
            "plan_save_path": "ui_project_spec.json",
        },
        "prompt_reviewer": _agent_defaults(
            instructions=(
                "Ти покращуєш вхідний промпт перед тим, як його виконає інший агент. "
                "Врахуй, через які блоки пройде задача далі, і зроби промпт "
                "однозначним, перевірюваним і достатнім для виконання. Якщо "
                "Flow передав trusted receipt попереднього Result TRUE, не "
                "перетворюй старий pre-confirmation marker на неможливу стартову "
                "умову й не доручай агенту редагувати progress: state transition "
                "належить рушію."
            ),
            prompt=(
                "# Промпт користувача\n{{entry_prompt}}\n\n"
                "# Домовленості з користувачем (GrillMe)\n{{grill_summary}}\n"
                "Це рішення користувача. Не переглядай і не викидай їх — "
                "лише зроби промпт чіткішим навколо них.\n\n"
                "# Ланцюг блоків, які працюватимуть далі\n{{flow_chain}}\n\n"
                "Поверни покращений промпт і перелік того, що ти змінив."
            ),
            output_format="json",
            output_schema=dict(PROMPT_REVIEW_SCHEMA),
            sandbox="read-only",
            compact_flow_context=True,
            prompt_cache_enabled=True,
        ),
        "executor": _agent_defaults(
            instructions=(
                "Виконай поставлену задачу повністю. Якщо тобі повертають задачу на "
                "переробку, виправ саме те, що вказав рев'ювер, і не зламай решту. "
                "Trusted receipt попереднього Result TRUE є системним доказом "
                "переходу. Не редагуй статус проходження вручну: його атомарно "
                "оновлює FlowAI; у разі суперечності поверни engine_state blocker."
            ),
            prompt="{{prompt}}",
            prompt_source="input",
            # Generic/legacy executors keep one node thread. Flows with a
            # Tasks Manager opt into task_thread explicitly so tasks cannot
            # contaminate each other's context.
            memory="thread",
            operation_policy={
                "max_iterations": 500,
                "no_improvement_patience": 50,
                "min_delta": 0.0001,
                "checkpoint_every": 10,
            },
            operation_intent_required=False,
            legacy_retry_upgrade_enabled=False,
            legacy_retry_protected_checks=[],
            legacy_retry_protected_files=[],
        ),
        "task_reviewer": _agent_defaults(
            instructions=(
                "Ти перевіряєш, чи виконана робота задовольняє поставленій задачі. "
                "Будь конкретним: якщо відхиляєш, must_fix має містити дії, "
                "а не загальні побажання. Trusted receipt підтверджує тільки "
                "завершення попереднього task; не відхиляй поточний результат "
                "лише через старий pre-confirmation marker, якщо receipt містить "
                "успішний state patch. QA ніколи не редагує файли."
            ),
            prompt=(
                "# Задача, відносно якої перевіряємо\n{{criteria}}\n\n"
                "# Результат виконавця\n{{work}}\n\n"
                "Винеси вердикт за схемою відповіді."
            ),
            output_format="json",
            output_schema=dict(TASK_REVIEW_SCHEMA),
            sandbox="read-only",
            criteria_node="",
            strict_review_contract=True,
            pass_threshold=80,
            qa_correction_attempts=1,
            qa_cache_enabled=True,
            deterministic_qa_enabled=True,
        ),
        "result": {
            "template": "{{work}}",
            "save_path": "",
            "true_limit": 1,
            "false_limit": 3,
            "task_attempt_limit": 2,
            "wait_for_confirmation": False,
            "confirmation_mode": "standard",
            "confirmation_ports": ["true", "false"],
            "final_task_result": True,
            "retry_guard_enabled": False,
            "retry_guard_threshold": 2,
            "regression_threshold": 1,
            "retry_contract_enabled": True,
            "transition_adapter": {},
            "learning_enabled": False,
        },
        "work_reviewer": _agent_defaults(
            instructions=(
                "Ти аналізуєш протокол роботи Flow. Для кожного блоку оціни, "
                "наскільки оптимально він працював: зайві кроки, повтори, "
                "недостатній чи надмірний контекст, причини переробок."
            ),
            prompt=(
                "# Протокол роботи Flow\n"
                "Протокол додано файлом: {{protocol_path}}\n\n"
                "Проаналізуй роботу кожного блоку і дай конкретні рекомендації."
            ),
            sandbox="read-only",
            monitor_all=True,
            monitored_nodes=[],
            report_path="",
            review_triggers=["run_finished"],
            learning_log_path="learnings/ui_learnings.jsonl",
            project_profile_path="learnings/ui_project_profile.md",
            skill_proposals_path="learnings/skill-proposals",
        ),
        "calibrator": _agent_defaults(
            instructions=(
                "Ти щойно відхилив роботу виконавця. Тепер поясни, чому "
                "якість гірша за очікувану, і запропонуй конкретні правки. "
                "Розділяй симптом і причину: пункт відхилення описує, що не "
                "так у результаті, а edits міняють те, через що це сталося — "
                "текст скіла або промпт. У before клади ТОЧНИЙ фрагмент, "
                "який зараз є у файлі, інакше правку неможливо застосувати. "
                "Не чіпай scripts/ і assets/ скілів."
                " Спочатку класифікуй root_cause_category як artifact, "
                "agent_strategy, engine_state або tool_failure. Для engine_state "
                "не маскуй дефект новими prompt-правилами й не пропонуй edits: "
                "поверни системну рекомендацію для Attention."
            ),
            prompt=(
                "# Скіли, які агент справді відкривав\n{{skills_used}}\n\n"
                "# Каталог доступних скілів\n{{skills_catalogue}}\n\n"
                "# Промпт завдання, яке провалилось\n{{task_prompt}}\n\n"
                "# Постійні інструкції блоку-виконавця\n"
                "{{node_instructions}}\n\n"
                "# Промпт блоку-виконавця\n{{node_prompt}}\n\n"
                "# Файли, які створив виконавець\n{{generated_files}}\n\n"
                "Поверни JSON за схемою відповіді."
            ),
            sandbox="read-only",
            output_format="json",
            output_schema=dict(CALIBRATION_SCHEMA),
            memory="thread",
            false_threshold=2,
            auto_skip=False,
            reviewed_nodes=[],
            thread_source="",
            reviewer_node="",
        ),
    }
    if kind not in defaults:
        raise UnsupportedFlowFormat(f"Невідомий тип ноди: {kind}")
    return dict(defaults[kind])


@dataclass(slots=True)
class FlowNode:
    id: str
    kind: str
    title: str
    x: float = 0.0
    y: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    width: float = DEFAULT_NODE_WIDTH
    height: float = DEFAULT_NODE_HEIGHT

    @property
    def short_id(self) -> str:
        return self.id[:6]

    @property
    def is_agent(self) -> bool:
        return self.kind in AGENT_KINDS

    @classmethod
    def create(cls, kind: str, x: float = 0.0, y: float = 0.0) -> FlowNode:
        if kind not in NODE_LABELS:
            raise UnsupportedFlowFormat(f"Невідомий тип ноди: {kind}")
        return cls(
            id=uuid4().hex,
            kind=kind,
            title=NODE_LABELS[kind],
            x=x,
            y=y,
            config=_default_config(kind),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FlowNode:
        kind = str(raw["kind"])
        if kind not in NODE_LABELS:
            raise UnsupportedFlowFormat(
                f"Тип ноди «{kind}» більше не підтримується. "
                "Цей Flow створено у форматі 1 — створіть його заново."
            )
        supplied = dict(raw.get("config") or {})
        config = _default_config(kind)
        config.update(supplied)
        if kind == "task_reviewer":
            # Старі Flow зберігали коротку schema без issues/checks. Додаємо
            # нові поля, не стираючи власних назв і додаткових полів користувача.
            schema = dict(TASK_REVIEW_SCHEMA)
            raw_schema = supplied.get("output_schema")
            if isinstance(raw_schema, dict):
                schema.update(raw_schema)
            config["output_schema"] = schema
            # Нові ноди строгі одразу. Старі ноди лишаються сумісними, доки
            # користувач або цільова міграція явно не увімкне контракт.
            if "strict_review_contract" not in supplied:
                config["strict_review_contract"] = False
        # Ретирована настройка: рушій не читав її ніколи, тож зі старих Flow
        # її теж прибираємо, щоб вона не виглядала робочою.
        config.pop("timeout_seconds", None)
        if kind == "tasks_manager":
            config["tasks"] = normalize_managed_tasks(config.get("tasks"))
        return cls(
            id=str(raw["id"]),
            kind=kind,
            title=str(raw.get("title") or NODE_LABELS[kind]),
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
            width=float(raw.get("width", DEFAULT_NODE_WIDTH)),
            height=float(raw.get("height", DEFAULT_NODE_HEIGHT)),
            config=config,
        )


@dataclass(slots=True)
class FlowEdge:
    id: str
    source: str
    target: str
    source_port: str = DEFAULT_PORT
    source_path: str = "data"
    target_variable: str = "input"
    condition: str = ""
    transform: str = ""
    label: str = ""
    control_points: list[dict[str, float]] = field(default_factory=list)

    @classmethod
    def create(
        cls, source: str, target: str, source_port: str = DEFAULT_PORT
    ) -> FlowEdge:
        return cls(
            id=uuid4().hex, source=source, target=target, source_port=source_port
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FlowEdge:
        control_points: list[dict[str, float]] = []
        for point in raw.get("control_points", []):
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                continue
            try:
                x = float(point["x"])
                y = float(point["y"])
            except (TypeError, ValueError):
                continue
            control_points.append({"x": x, "y": y})
        return cls(
            id=str(raw["id"]),
            source=str(raw["source"]),
            target=str(raw["target"]),
            source_port=str(raw.get("source_port", DEFAULT_PORT)) or DEFAULT_PORT,
            source_path=str(raw.get("source_path", "data")),
            target_variable=str(raw.get("target_variable", "input")),
            condition=str(raw.get("condition", "")),
            transform=str(raw.get("transform", "")),
            label=str(raw.get("label", "")),
            control_points=control_points,
        )


@dataclass(slots=True)
class Workflow:
    name: str = "Новий Flow"
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    workspace: str = ""
    additional_folders: list[str] = field(default_factory=list)
    grill_summary: str = ""
    format_version: int = FLOW_FORMAT_VERSION

    def node(self, node_id: str) -> FlowNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def find(self, node_id: str) -> FlowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def incoming(self, node_id: str) -> list[FlowEdge]:
        return [edge for edge in self.edges if edge.target == node_id]

    def outgoing(self, node_id: str, port: str | None = None) -> list[FlowEdge]:
        return [
            edge
            for edge in self.edges
            if edge.source == node_id and (port is None or edge.source_port == port)
        ]

    def nodes_of_kind(self, kind: str) -> list[FlowNode]:
        return [node for node in self.nodes if node.kind == kind]

    def routed_nodes(self) -> list[FlowNode]:
        return [node for node in self.nodes if node.kind not in SIDECAR_KINDS]

    def remove_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [
            edge
            for edge in self.edges
            if edge.source != node_id and edge.target != node_id
        ]

    def ports_of(self, node_id: str) -> tuple[str, ...]:
        node = self.find(node_id)
        if node is None:
            return ()
        if node.kind in SIDECAR_KINDS:
            return ()
        if node.kind in TERMINAL_KINDS:
            return ()
        if node.kind == "result":
            return RESULT_PORTS
        if node.kind == "tasks_manager":
            return TASK_MANAGER_PORTS
        return (DEFAULT_PORT,)

    def result_port_limit(self, node: FlowNode, port: str) -> int:
        if node.kind == "result" and port == "exhausted":
            return 10**6
        configured = max(1, int(node.config.get(f"{port}_limit", 1)))
        if node.kind != "result" or port != "true":
            return configured
        managed_count = 0
        for edge in self.outgoing(node.id, "true"):
            target = self.find(edge.target)
            if target is None or target.kind != "tasks_manager":
                continue
            managed_count = max(
                managed_count,
                len(normalize_managed_tasks(target.config.get("tasks"))),
            )
        return max(configured, managed_count)

    def exhausted_target(self, node_id: str) -> FlowNode | None:
        """Tasks Manager, у який веде жовтий вихід Result, якщо він з'єднаний."""
        for edge in self.outgoing(node_id, "exhausted"):
            target = self.find(edge.target)
            if target is not None and target.kind == "tasks_manager":
                return target
        return None

    def calibrator_for(self, node_id: str) -> FlowNode | None:
        """Нода калібрації, підвішена на вихід FALSE вказаного Result."""
        for edge in self.outgoing(node_id, "false"):
            target = self.find(edge.target)
            if target is not None and target.kind == "calibrator":
                return target
        return None

    def validate(self) -> list[str]:
        errors: list[str] = []
        node_ids = {node.id for node in self.nodes}
        if not self.nodes:
            errors.append("Flow не містить нод")

        for edge in self.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                errors.append(f"З'єднання {edge.id} посилається на відсутню ноду")
                continue
            if edge.source == edge.target:
                errors.append("Ноду не можна з'єднати саму із собою")
            if not edge.target_variable.strip():
                errors.append("Для кожного з'єднання потрібна вхідна змінна")
            allowed = self.ports_of(edge.source)
            source = self.node(edge.source)
            if not allowed:
                errors.append(
                    f"Блок «{source.title}» не має вихідних портів "
                    "і не може брати участі в маршруті"
                )
            elif edge.source_port not in allowed:
                errors.append(
                    f"У блока «{source.title}» немає виходу «{edge.source_port}»"
                )
            target = self.node(edge.target)
            if target.kind in SIDECAR_KINDS:
                errors.append(
                    f"Блок «{target.title}» не має входів "
                    "і не може брати участі в маршруті"
                )

        for edge in self.edges:
            if edge.source_port != "exhausted":
                continue
            target = self.find(edge.target)
            if target is None or target.kind != "tasks_manager":
                errors.append(
                    "Вихід EXHAUSTED можна з'єднати лише з блоком Tasks Manager"
                )

        for edge in self.edges:
            target = self.find(edge.target)
            if target is None or target.kind != "calibrator":
                continue
            source = self.find(edge.source)
            if (
                source is None
                or source.kind != "result"
                or edge.source_port != "false"
            ):
                errors.append(
                    f"Блок «{target.title}» (Calibration Stop) можна з'єднати "
                    "лише з виходом FALSE блока Result"
                )

        for edge in self.edges:
            source = self.find(edge.source)
            if source is None or source.kind != "calibrator":
                continue
            target = self.find(edge.target)
            if target is None or target.kind != "executor":
                errors.append(
                    f"Вихід блока «{source.title}» (Calibration Stop) можна "
                    "з'єднати лише з блоком Executor"
                )

        for node in self.nodes_of_kind("result"):
            attached = [
                edge.target
                for edge in self.outgoing(node.id, "false")
                if (found := self.find(edge.target)) is not None
                and found.kind == "calibrator"
            ]
            if len(attached) > 1:
                errors.append(
                    f"До блока «{node.title}» можна підключити "
                    "лише один Calibration Stop"
                )
            if attached and any(
                (target := self.find(edge.target)) is not None
                and target.kind == "executor"
                for edge in self.outgoing(node.id, "false")
            ):
                errors.append(
                    f"Блок «{node.title}» не повинен вести напряму в Executor, "
                    "коли до FALSE підключено Calibration Stop. Використайте "
                    "маршрут Result.FALSE → Calibration Stop → Executor"
                )

        if len(self.nodes_of_kind("work_reviewer")) > 1:
            errors.append("У Flow може бути лише один блок Work Reviewer")

        for node in self.nodes_of_kind("tasks_manager"):
            tasks = normalize_managed_tasks(node.config.get("tasks"))
            if str(node.config.get("task_source") or "static") != "input_once":
                for index, task in enumerate(tasks):
                    if not str(task.get("prompt", "")).strip():
                        errors.append(
                            f"У блоці «{node.title}» завдання {index + 1} не має промпту"
                        )
            if not self.outgoing(node.id, "next"):
                errors.append(f"Блок «{node.title}» потребує з'єднання з виходу NEXT")
            has_result_return = any(
                edge.source_port == "true"
                and (source := self.find(edge.source)) is not None
                and source.kind == "result"
                for edge in self.incoming(node.id)
            )
            if not has_result_return:
                errors.append(
                    f"До блока «{node.title}» потрібно повернути вихід TRUE блока Result"
                )

        for node in self.nodes_of_kind("task_reviewer"):
            reference = str(node.config.get("criteria_node", "")).strip()
            if reference and reference not in node_ids:
                errors.append(
                    f"Блок «{node.title}» посилається на видалений блок-еталон"
                )

        for node in self.nodes_of_kind("result"):
            if not self._has_upstream_kind(node.id, "task_reviewer"):
                errors.append(
                    f"Перед блоком «{node.title}» має бути Task Reviewer — "
                    "інакше немає звідки взяти вердикт"
                )

        try:
            self.topological_order()
        except ValueError as exc:
            errors.append(str(exc))
        return errors

    def _has_upstream_kind(self, node_id: str, kind: str) -> bool:
        seen: set[str] = set()
        stack = [edge.source for edge in self.incoming(node_id)]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self.find(current)
            if node is None:
                continue
            if node.kind == kind:
                return True
            stack.extend(edge.source for edge in self.incoming(current))
        return False

    def topological_order(self) -> list[str]:
        """Порядок нод у графі без зворотних ребер, що виходять із Result.

        Саме цей граф зобов'язаний бути ациклічним: цикл дозволений лише
        тоді, коли він проходить через Result із його лічильниками.
        """
        ids = [node.id for node in self.nodes]
        indegree = {node_id: 0 for node_id in ids}
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
        for edge in self.edges:
            if edge.source not in adjacency or edge.target not in indegree:
                continue
            source = self.find(edge.source)
            if source is not None and source.kind == "result":
                continue
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

        ready = [node_id for node_id in ids if indegree[node_id] == 0]
        # Calibration Stop is never seeded into the initial queue, but after a
        # Result.FALSE it must run before the retry branch.  Keeping it first in
        # the runtime topological order makes that guarantee independent of the
        # order in which nodes happen to be stored in the Flow JSON.
        ready.sort(
            key=lambda node_id: (
                0
                if (node := self.find(node_id)) is not None
                and node.kind == "calibrator"
                else 1,
                ids.index(node_id),
            )
        )
        ordered: list[str] = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(node_id)
            for target in adjacency[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if len(ordered) != len(ids):
            raise ValueError(
                "Flow містить цикл, який не проходить через блок Result. "
                "Зворотні зв'язки дозволені лише з виходів True/False."
            )
        return ordered

    def describe_chain(self, from_node_id: str) -> str:
        """Текстовий опис блоків, які працюватимуть після вказаного."""
        order = {
            node_id: index for index, node_id in enumerate(self.topological_order())
        }
        seen: set[str] = set()
        stack = [edge.target for edge in self.outgoing(from_node_id)]
        collected: list[FlowNode] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self.find(current)
            if node is None:
                continue
            collected.append(node)
            stack.extend(edge.target for edge in self.outgoing(current))
        collected.sort(key=lambda item: order.get(item.id, len(order)))

        lines: list[str] = []
        for node in collected:
            lines.append(f"- {node.short_id} · {NODE_LABELS[node.kind]} «{node.title}»")
            if node.kind == "result":
                lines.append(
                    f"  Ліміти проходів: True={node.config.get('true_limit', 1)}, "
                    f"False={node.config.get('false_limit', 3)}"
                )
                continue
            prompt = str(node.config.get("prompt", "")).strip()
            if prompt:
                lines.append(f"  Промпт блоку: {prompt}")
        return "\n".join(lines) or "Наступних блоків немає"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "name": self.name,
            "workspace": self.workspace,
            "additional_folders": list(self.additional_folders),
            "grill_summary": self.grill_summary,
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Workflow:
        version = int(raw.get("format_version", 1))
        if version > FLOW_FORMAT_VERSION:
            raise UnsupportedFlowFormat(
                f"Файл створено новішою версією FlowAI (формат {version}). "
                f"Поточна версія читає формат {FLOW_FORMAT_VERSION}."
            )
        if version < FLOW_FORMAT_VERSION:
            raise UnsupportedFlowFormat(
                f"Формат Flow {version} більше не підтримується. "
                "Набір нод повністю змінився у версії 0.3 — створіть Flow заново."
            )
        return cls(
            name=str(raw.get("name") or "Flow"),
            workspace=str(raw.get("workspace") or ""),
            additional_folders=[
                str(item) for item in raw.get("additional_folders", []) if str(item)
            ],
            grill_summary=str(raw.get("grill_summary") or ""),
            format_version=version,
            nodes=[FlowNode.from_dict(item) for item in raw.get("nodes", [])],
            edges=[FlowEdge.from_dict(item) for item in raw.get("edges", [])],
        )

    def resolved_workspace(self, project_path: Path | None = None) -> Path:
        if project_path:
            return project_path.resolve().parent
        if self.workspace.strip():
            return Path(self.workspace).expanduser().resolve()
        return Path.cwd().resolve()

    def resolved_additional_folders(
        self, project_path: Path | None = None
    ) -> list[Path]:
        workspace = self.resolved_workspace(project_path)
        resolved: list[Path] = []
        raw_paths = list(self.additional_folders)
        # Since format v2 ``workspace`` may point at a legacy source directory.
        # For a saved Flow its own folder is always the write root, while this
        # legacy location remains available only as an additional source.
        if project_path is not None and self.workspace.strip():
            raw_paths.insert(0, self.workspace)
        for raw_path in raw_paths:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = workspace / path
            path = path.resolve()
            if path != workspace and path not in resolved:
                resolved.append(path)
        return resolved
