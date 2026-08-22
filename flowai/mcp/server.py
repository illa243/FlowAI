from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..persistence import load_workflow
from .drafts import DraftStore
from .guides import list_guides as available_guides
from .guides import read_guide as guide_text
from .schema import describe_kind, node_kinds


def build_server() -> FastMCP:
    server = FastMCP("flowai")
    store = DraftStore()

    @server.tool()
    def list_node_kinds() -> list[dict[str, Any]]:
        """Усі типи блоків FlowAI з портами, полями конфігу та призначенням."""
        return node_kinds()

    @server.tool()
    def describe_node_kind(kind: str) -> dict[str, Any]:
        """Детальний опис одного типу блока FlowAI."""
        return describe_kind(kind)

    @server.tool()
    def create_flow(name: str, workspace: str = "") -> str:
        """Створити порожню чернетку Flow і повернути її draft_id."""
        return store.create(name, workspace)

    @server.tool()
    def load_flow(path: str) -> str:
        """Завантажити конкретний готовий Flow у чернетку для редагування."""
        return store.load(path)

    @server.tool()
    def read_draft(draft_id: str) -> dict[str, Any]:
        """Прочитати поточний стан чернетки разом з ID, координатами й ребрами."""
        return store.snapshot(draft_id)

    @server.tool()
    def set_flow_config(draft_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Змінити назву, робочу папку або інші відомі властивості Flow."""
        store.set_flow_config(draft_id, values)
        workflow = store.get(draft_id)
        return {
            "name": workflow.name,
            "workspace": workflow.workspace,
            "additional_folders": workflow.additional_folders,
            "grill_summary": workflow.grill_summary,
        }

    @server.tool()
    def add_node(draft_id: str, kind: str, title: str = "") -> str:
        """Додати блок до чернетки й повернути його node_id."""
        return store.add_node(draft_id, kind, title)

    @server.tool()
    def set_node_config(
        draft_id: str, node_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Оновити лише відомі поля конфігурації блока."""
        store.set_config(draft_id, node_id, values)
        return store.get(draft_id).node(node_id).config

    @server.tool()
    def set_node_title(draft_id: str, node_id: str, title: str) -> str:
        """Перейменувати один блок, не змінюючи його ID та конфігурацію."""
        store.set_node_title(draft_id, node_id, title)
        return store.get(draft_id).node(node_id).title

    @server.tool()
    def set_node_position(
        draft_id: str, node_id: str, x: float, y: float
    ) -> dict[str, float]:
        """Перемістити блок для читабельного маршруту без перетинів ліній."""
        store.set_node_position(draft_id, node_id, x, y)
        node = store.get(draft_id).node(node_id)
        return {"x": node.x, "y": node.y}

    @server.tool()
    def set_tasks(draft_id: str, node_id: str, prompts: list[str]) -> list[str]:
        """Задати послідовні промпти блока Tasks Manager."""
        store.set_tasks(draft_id, node_id, prompts)
        return [str(task) for task in prompts]

    @server.tool()
    def connect_nodes(
        draft_id: str,
        source: str,
        target: str,
        source_port: str = "out",
        target_variable: str = "input",
    ) -> str:
        """З'єднати вихід source із входом target у чернетці Flow."""
        return store.connect(
            draft_id, source, target, source_port, target_variable
        )

    @server.tool()
    def remove_node(draft_id: str, node_id: str) -> None:
        """Видалити блок і всі його вхідні та вихідні зв'язки."""
        store.remove_node(draft_id, node_id)

    @server.tool()
    def remove_edge(draft_id: str, edge_id: str) -> None:
        """Видалити один зв'язок за його ID."""
        store.remove_edge(draft_id, edge_id)

    @server.tool()
    def set_edge_config(
        draft_id: str, edge_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Змінити мапінг, умову, підпис або порт наявного зв'язку."""
        store.set_edge_config(draft_id, edge_id, values)
        return next(
            edge
            for edge in store.snapshot(draft_id)["edges"]
            if edge["id"] == edge_id
        )

    @server.tool()
    def set_edge_control_points(
        draft_id: str, edge_id: str, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        """Прокласти маршрут лінії через точки, щоб уникнути перетинів."""
        store.set_edge_control_points(draft_id, edge_id, points)
        return next(
            edge["control_points"]
            for edge in store.snapshot(draft_id)["edges"]
            if edge["id"] == edge_id
        )

    @server.tool()
    def auto_layout(draft_id: str) -> dict[str, dict[str, float]]:
        """Автоматично розкласти блоки чернетки по колонках і рядках."""
        store.auto_layout(draft_id)
        return {
            node.id: {"x": node.x, "y": node.y}
            for node in store.get(draft_id).nodes
        }

    @server.tool()
    def validate_flow(draft_id: str) -> list[str]:
        """Перевірити чернетку тією самою валідацією, що й FlowAI."""
        return store.validate(draft_id)

    @server.tool()
    def save_flow(draft_id: str, path: str) -> str:
        """Перевірити й зберегти чернетку як файл .flowai.json."""
        target = store.save(draft_id, path)
        store.drop(draft_id)
        return target

    @server.tool()
    def list_flows(directory: str) -> list[dict[str, str]]:
        """Готові Flow у папці — як еталони стилю та структури."""
        root = Path(directory).expanduser()
        found: list[dict[str, str]] = []
        for path in sorted(root.rglob("*.flowai.json")):
            if any(part in {".git", ".venv", "_archive"} for part in path.parts):
                continue
            try:
                workflow = load_workflow(path)
            except Exception:  # noqa: BLE001, S112 - skip malformed references
                continue
            found.append(
                {
                    "path": str(path),
                    "name": workflow.name,
                    "nodes": str(len(workflow.nodes)),
                }
            )
        return found

    @server.tool()
    def read_flow(path: str) -> dict[str, Any]:
        """Прочитати готовий Flow як структурований словник."""
        return store.read(path)

    @server.tool()
    def list_guides() -> list[dict[str, str]]:
        """Перелічити md-довідники, доступні агенту-складачу Flow."""
        return available_guides()

    @server.tool()
    def read_guide(name: str, section: str = "") -> str:
        """Прочитати довідник повністю або лише вказаний розділ."""
        return guide_text(name, section)

    return server
