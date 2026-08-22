from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import DEFAULT_PORT, FlowEdge, FlowNode, Workflow, new_managed_task
from ..persistence import load_workflow, save_workflow

COLUMN_WIDTH = 320.0
ROW_HEIGHT = 210.0


class DraftStore:
    """Flow drafts an agent builds incrementally before saving."""

    def __init__(self) -> None:
        self._drafts: dict[str, Workflow] = {}

    def create(self, name: str, workspace: str = "") -> str:
        draft_id = uuid4().hex
        self._drafts[draft_id] = Workflow(
            name=name or "Новий Flow", workspace=workspace
        )
        return draft_id

    def load(self, path: str) -> str:
        """Load an existing Flow into an editable, isolated draft."""
        source = Path(path).expanduser().resolve()
        workflow = load_workflow(source)
        draft_id = uuid4().hex
        self._drafts[draft_id] = workflow
        return draft_id

    def get(self, draft_id: str) -> Workflow:
        if draft_id not in self._drafts:
            raise ValueError(f"Чернетка {draft_id} не знайдена")
        return self._drafts[draft_id]

    def drop(self, draft_id: str) -> None:
        self._drafts.pop(draft_id, None)

    def add_node(self, draft_id: str, kind: str, title: str = "") -> str:
        workflow = self.get(draft_id)
        node = FlowNode.create(kind)
        if title:
            node.title = title
        workflow.nodes.append(node)
        return node.id

    def set_flow_config(self, draft_id: str, values: dict[str, Any]) -> None:
        workflow = self.get(draft_id)
        allowed = {"name", "workspace", "additional_folders", "grill_summary"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                "Невідомі поля Flow: " + ", ".join(sorted(unknown))
            )
        if "name" in values:
            workflow.name = str(values["name"]).strip() or workflow.name
        if "workspace" in values:
            workflow.workspace = str(values["workspace"])
        if "additional_folders" in values:
            folders = values["additional_folders"]
            if not isinstance(folders, list):
                raise ValueError("additional_folders має бути списком шляхів")
            workflow.additional_folders = [str(item) for item in folders]
        if "grill_summary" in values:
            workflow.grill_summary = str(values["grill_summary"])

    def set_node_title(self, draft_id: str, node_id: str, title: str) -> None:
        self.get(draft_id).node(node_id).title = title.strip() or "Блок"

    def set_node_position(
        self, draft_id: str, node_id: str, x: float, y: float
    ) -> None:
        node = self.get(draft_id).node(node_id)
        node.x = float(x)
        node.y = float(y)

    def set_config(self, draft_id: str, node_id: str, values: dict[str, Any]) -> None:
        workflow = self.get(draft_id)
        node = workflow.node(node_id)
        unknown = set(values) - set(node.config)
        if unknown:
            raise ValueError(
                f"У блока «{node.title}» немає полів: "
                f"{', '.join(sorted(unknown))}"
            )
        node.config.update(values)

    def set_tasks(self, draft_id: str, node_id: str, prompts: list[str]) -> None:
        workflow = self.get(draft_id)
        node = workflow.node(node_id)
        if node.kind != "tasks_manager":
            raise ValueError("Завдання можна задати лише блоку Tasks Manager")
        node.config["tasks"] = [new_managed_task(prompt) for prompt in prompts]

    def remove_node(self, draft_id: str, node_id: str) -> None:
        workflow = self.get(draft_id)
        workflow.node(node_id)
        workflow.nodes = [node for node in workflow.nodes if node.id != node_id]
        workflow.edges = [
            edge
            for edge in workflow.edges
            if edge.source != node_id and edge.target != node_id
        ]

    def remove_edge(self, draft_id: str, edge_id: str) -> None:
        workflow = self.get(draft_id)
        self._edge(workflow, edge_id)
        workflow.edges = [edge for edge in workflow.edges if edge.id != edge_id]

    def set_edge_config(
        self, draft_id: str, edge_id: str, values: dict[str, Any]
    ) -> None:
        workflow = self.get(draft_id)
        edge = self._edge(workflow, edge_id)
        allowed = {
            "source_port",
            "source_path",
            "target_variable",
            "condition",
            "transform",
            "label",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                "Невідомі поля зв'язку: " + ", ".join(sorted(unknown))
            )
        for name, value in values.items():
            setattr(edge, name, str(value))

    def set_edge_control_points(
        self, draft_id: str, edge_id: str, points: list[dict[str, float]]
    ) -> None:
        workflow = self.get(draft_id)
        edge = self._edge(workflow, edge_id)
        normalized: list[dict[str, float]] = []
        for point in points:
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                raise ValueError("Кожна точка маршруту повинна мати x та y")
            normalized.append({"x": float(point["x"]), "y": float(point["y"])})
        edge.control_points = normalized

    def connect(
        self,
        draft_id: str,
        source: str,
        target: str,
        source_port: str = DEFAULT_PORT,
        target_variable: str = "input",
    ) -> str:
        workflow = self.get(draft_id)
        workflow.node(source)
        workflow.node(target)
        edge = FlowEdge.create(source, target, source_port)
        edge.target_variable = target_variable
        workflow.edges.append(edge)
        return edge.id

    def auto_layout(self, draft_id: str) -> None:
        """Lay nodes out in columns following topological order."""
        workflow = self.get(draft_id)
        try:
            order = workflow.topological_order()
        except ValueError:
            order = [node.id for node in workflow.nodes]
        depth: dict[str, int] = {}
        for node_id in order:
            incoming = [
                edge.source
                for edge in workflow.incoming(node_id)
                if (source := workflow.find(edge.source)) is not None
                and source.kind != "result"
            ]
            depth[node_id] = (
                0 if not incoming else max(depth.get(item, 0) for item in incoming) + 1
            )
        rows: dict[int, int] = {}
        for node in workflow.nodes:
            column = depth.get(node.id, 0)
            row = rows.get(column, 0)
            rows[column] = row + 1
            node.x = 80.0 + column * COLUMN_WIDTH
            node.y = 80.0 + row * ROW_HEIGHT

    def validate(self, draft_id: str) -> list[str]:
        return self.get(draft_id).validate()

    def snapshot(self, draft_id: str) -> dict[str, Any]:
        return self.get(draft_id).to_dict()

    def save(self, draft_id: str, path: str) -> str:
        workflow = self.get(draft_id)
        errors = workflow.validate()
        if errors:
            raise ValueError("Flow не валідний:\n" + "\n".join(errors))
        target = Path(path).expanduser()
        if not target.name.endswith(".flowai.json"):
            target = target.with_suffix(".flowai.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        save_workflow(workflow, target)
        return str(target)

    @staticmethod
    def read(path: str) -> dict[str, Any]:
        return load_workflow(Path(path)).to_dict()

    @staticmethod
    def _edge(workflow: Workflow, edge_id: str) -> FlowEdge:
        for edge in workflow.edges:
            if edge.id == edge_id:
                return edge
        raise ValueError(f"Зв'язок {edge_id} не знайдено")
