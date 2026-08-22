from __future__ import annotations

import sys
from pathlib import Path

from flowai.mcp.client_config import flowai_server_config
from flowai.mcp.drafts import DraftStore
from flowai.mcp.guides import list_guides, read_guide
from flowai.mcp.schema import describe_kind, node_kinds
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.persistence import load_workflow, save_workflow


def test_node_kinds_cover_all_blocks() -> None:
    kinds = {item["kind"] for item in node_kinds()}
    assert kinds == {
        "entry",
        "tasks_manager",
        "prompt_reviewer",
        "executor",
        "task_reviewer",
        "result",
        "work_reviewer",
        "calibrator",
    }


def test_result_description_includes_exhausted_port() -> None:
    described = describe_kind("result")
    assert described["ports"] == ["true", "false", "exhausted"]
    assert "task_attempt_limit" in described["config_fields"]


def test_draft_store_builds_valid_flow(tmp_path: Path) -> None:
    store = DraftStore()
    draft_id = store.create("Тестовий Flow")
    manager = store.add_node(draft_id, "tasks_manager")
    executor = store.add_node(draft_id, "executor")
    reviewer = store.add_node(draft_id, "task_reviewer")
    result = store.add_node(draft_id, "result")
    store.set_tasks(draft_id, manager, ["Перше завдання", "Друге завдання"])
    store.connect(draft_id, manager, executor, "next")
    store.connect(draft_id, executor, reviewer)
    store.connect(draft_id, reviewer, result)
    store.connect(draft_id, result, manager, "true")
    store.connect(draft_id, result, manager, "exhausted")
    store.auto_layout(draft_id)

    assert store.validate(draft_id) == []
    target = tmp_path / "новий.flowai.json"
    store.save(draft_id, str(target))
    saved = load_workflow(target)
    assert len(saved.nodes) == 4
    assert saved.nodes[0].x != saved.nodes[1].x


def test_node_guide_is_always_available() -> None:
    names = {item["name"] for item in list_guides()}
    assert "node-guide" in names
    text = read_guide("node-guide")
    assert "Tasks Manager" in text
    assert "QA — обов’язковий шлюз, а не паралельна гілка" in text
    assert "Generator → Result" in text
    assert "Геометрія зв'язків і перетини ліній" in text
    assert "перетиналися багато разів" in text
    assert "set_edge_control_points" in text


def test_read_guide_can_return_single_section() -> None:
    text = read_guide("node-guide", section="Result")
    assert text.strip().startswith("#")


def test_server_config_points_at_current_interpreter() -> None:
    config = flowai_server_config()
    server = config["mcp_servers"]["flowai"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "flowai.mcp"]


def test_schema_describes_the_calibration_node() -> None:
    from flowai.mcp.schema import describe_kind

    described = describe_kind("calibrator")
    assert described["label"] == "Calibration Stop"
    assert described["color"] == "#E11D48"
    assert described["ports"] == []
    assert "false_threshold" in described["config"]


def test_calibration_guide_is_listed() -> None:
    names = {entry["name"] for entry in list_guides()}
    assert "calibration" in names


def test_draft_store_edits_an_existing_flow_without_replacing_untouched_ids(
    tmp_path: Path,
) -> None:
    source = FlowNode.create("entry")
    target = FlowNode.create("executor")
    edge = FlowEdge.create(source.id, target.id)
    workflow = Workflow(
        name="Original",
        nodes=[source, target],
        edges=[edge],
    )
    path = save_workflow(workflow, tmp_path / "selected.flowai.json")
    store = DraftStore()

    draft_id = store.load(str(path))
    store.set_flow_config(draft_id, {"name": "Edited"})
    store.set_node_title(draft_id, target.id, "Focused executor")
    store.set_node_position(draft_id, target.id, 720, 260)
    store.set_edge_config(draft_id, edge.id, {"target_variable": "prompt"})
    store.set_edge_control_points(
        draft_id,
        edge.id,
        [{"x": 420, "y": 80}, {"x": 420, "y": 260}],
    )
    removable = store.add_node(draft_id, "work_reviewer")
    store.remove_node(draft_id, removable)
    store.save(draft_id, str(path))

    edited = load_workflow(path)
    assert edited.name == "Edited"
    assert [node.id for node in edited.nodes] == [source.id, target.id]
    assert edited.node(target.id).title == "Focused executor"
    assert (edited.node(target.id).x, edited.node(target.id).y) == (720.0, 260.0)
    assert edited.edges[0].id == edge.id
    assert edited.edges[0].target_variable == "prompt"
    assert edited.edges[0].control_points == [
        {"x": 420.0, "y": 80.0},
        {"x": 420.0, "y": 260.0},
    ]
