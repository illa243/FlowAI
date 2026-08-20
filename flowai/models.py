from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

FLOW_FORMAT_VERSION = 2


class UnsupportedFlowFormat(ValueError):
    """Файл проєкту не може бути представлений поточною моделлю."""


NODE_LABELS = {
    "entry": "Entry prompt",
    "prompt_reviewer": "Prompt Reviewer",
    "executor": "Task Executor",
    "task_reviewer": "Task Reviewer",
    "result": "Result",
    "work_reviewer": "Work Reviewer",
}


NODE_COLORS = {
    "entry": "#3B82F6",
    "prompt_reviewer": "#0891B2",
    "executor": "#7C3AED",
    "task_reviewer": "#D97706",
    "result": "#16A34A",
    "work_reviewer": "#DB2777",
}


# Ноди, які запускають окремий потік Codex.
AGENT_KINDS = frozenset(
    {"prompt_reviewer", "executor", "task_reviewer", "work_reviewer"}
)

# Ноди без портів: не беруть участі в маршруті.
SIDECAR_KINDS = frozenset({"work_reviewer"})

RESULT_PORTS = ("true", "false")
DEFAULT_PORT = "out"


PROMPT_REVIEW_SCHEMA: dict[str, Any] = {
    "improved_prompt": "string",
    "notes": ["string"],
}

TASK_REVIEW_SCHEMA: dict[str, Any] = {
    "verdict": True,
    "score": 0,
    "reason": "string",
    "must_fix": ["string"],
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
        "timeout_seconds": 1800,
        "retries": 0,
        "memory": "thread",
    }
    base.update(overrides)
    return base


def _default_config(kind: str) -> dict[str, Any]:
    defaults: dict[str, dict[str, Any]] = {
        "entry": {
            "text": "Опишіть завдання для ланцюга агентів",
            "json": {},
            "attachments": [],
        },
        "prompt_reviewer": _agent_defaults(
            instructions=(
                "Ти покращуєш вхідний промпт перед тим, як його виконає інший агент. "
                "Врахуй, через які блоки пройде задача далі, і зроби промпт "
                "однозначним, перевірюваним і достатнім для виконання."
            ),
            prompt=(
                "# Промпт користувача\n{{entry_prompt}}\n\n"
                "# Ланцюг блоків, які працюватимуть далі\n{{flow_chain}}\n\n"
                "Поверни покращений промпт і перелік того, що ти змінив."
            ),
            output_format="json",
            output_schema=dict(PROMPT_REVIEW_SCHEMA),
            sandbox="read-only",
        ),
        "executor": _agent_defaults(
            instructions=(
                "Виконай поставлену задачу повністю. Якщо тобі повертають задачу на "
                "переробку, виправ саме те, що вказав рев'ювер, і не зламай решту."
            ),
            prompt="{{prompt}}",
            prompt_source="input",
        ),
        "task_reviewer": _agent_defaults(
            instructions=(
                "Ти перевіряєш, чи виконана робота задовольняє поставленій задачі. "
                "Будь конкретним: якщо відхиляєш, must_fix має містити дії, "
                "а не загальні побажання."
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
        ),
        "result": {
            "template": "{{work}}",
            "save_path": "",
            "true_limit": 1,
            "false_limit": 3,
            "wait_for_confirmation": False,
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
        config = _default_config(kind)
        config.update(raw.get("config") or {})
        return cls(
            id=str(raw["id"]),
            kind=kind,
            title=str(raw.get("title") or NODE_LABELS[kind]),
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
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
        if node.kind == "result":
            return RESULT_PORTS
        return (DEFAULT_PORT,)

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

        if len(self.nodes_of_kind("work_reviewer")) > 1:
            errors.append("У Flow може бути лише один блок Work Reviewer")

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
            format_version=version,
            nodes=[FlowNode.from_dict(item) for item in raw.get("nodes", [])],
            edges=[FlowEdge.from_dict(item) for item in raw.get("edges", [])],
        )

    def resolved_workspace(self, project_path: Path | None = None) -> Path:
        if self.workspace.strip():
            return Path(self.workspace).expanduser().resolve()
        if project_path:
            return project_path.resolve().parent
        return Path.cwd().resolve()

    def resolved_additional_folders(
        self, project_path: Path | None = None
    ) -> list[Path]:
        workspace = self.resolved_workspace(project_path)
        resolved: list[Path] = []
        for raw_path in self.additional_folders:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = workspace / path
            path = path.resolve()
            if path != workspace and path not in resolved:
                resolved.append(path)
        return resolved
