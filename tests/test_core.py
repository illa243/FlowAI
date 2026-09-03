from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Self

import pytest

from flowai import codex_adapter
from flowai import engine as engine_module
from flowai.engine import InterventionRequired, RunCheckpoint, WorkflowRunner
from flowai.models import (
    NODE_COLORS,
    NODE_LABELS,
    FlowEdge,
    FlowNode,
    UnsupportedFlowFormat,
    Workflow,
)
from flowai.persistence import load_workflow, save_workflow
from flowai.templating import render_template, safe_eval
from flowai.work_review import PROTOCOL_NAME, REPORT_NAME


def test_agent_defaults_carry_an_empty_skill_list() -> None:
    node = FlowNode.create("executor")
    assert node.config["skills"] == []


def test_old_flow_files_get_the_skill_field() -> None:
    raw = {
        "format_version": 2,
        "name": "Старий",
        "nodes": [
            {
                "id": "a" * 32,
                "kind": "executor",
                "title": "Виконавець",
                "config": {},
            }
        ],
        "edges": [],
    }
    workflow = Workflow.from_dict(raw)
    assert workflow.nodes[0].config["skills"] == []


def test_build_input_prepends_pinned_skills() -> None:
    import openai_codex

    adapter = codex_adapter.CodexAdapter()
    adapter._module = openai_codex
    items = adapter._build_input(
        "текст",
        [],
        [{"name": "image-cutout", "path": "C:/skills/image-cutout"}],
    )
    assert isinstance(items, list)
    assert type(items[0]).__name__ == "SkillInput"
    assert items[0].name == "image-cutout"
    assert type(items[-1]).__name__ == "TextInput"


def test_build_input_without_skills_is_a_plain_string() -> None:
    import openai_codex

    adapter = codex_adapter.CodexAdapter()
    adapter._module = openai_codex
    assert adapter._build_input("текст", [], []) == "текст"


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", None)


class Pipeline:
    """Стандартний конвеєр: Entry → Prompt Reviewer → Executor → Reviewer → Result."""

    def __init__(self, workspace: Path, *, with_reviewer_block: bool = False) -> None:
        self.workflow = Workflow(name="Тестовий Flow", workspace=str(workspace))
        self.entry = FlowNode.create("entry")
        self.entry.config["text"] = "Напиши функцію"
        self.improver = FlowNode.create("prompt_reviewer")
        self.improver.config["model"] = "improver-model"
        self.executor = FlowNode.create("executor")
        self.executor.config["model"] = "executor-model"
        self.reviewer = FlowNode.create("task_reviewer")
        self.reviewer.config["model"] = "reviewer-model"
        self.result = FlowNode.create("result")
        self.result.config["template"] = "{{work}}"

        self.workflow.nodes = [
            self.entry,
            self.improver,
            self.executor,
            self.reviewer,
            self.result,
        ]
        self.watcher: FlowNode | None = None
        if with_reviewer_block:
            self.watcher = FlowNode.create("work_reviewer")
            self.watcher.config["model"] = "watcher-model"
            self.workflow.nodes.append(self.watcher)

        self.workflow.edges = [
            self.edge(self.entry, self.improver, "text", "entry_prompt"),
            self.edge(self.improver, self.executor, "text", "prompt"),
            self.edge(self.executor, self.reviewer, "text", "work"),
            self.edge(self.executor, self.result, "text", "work"),
            self.edge(self.reviewer, self.result, "data", "review"),
        ]
        back = FlowEdge.create(self.result.id, self.executor.id, "false")
        back.source_path = "data.retry_context"
        back.target_variable = "prompt"
        self.workflow.edges.append(back)

    @staticmethod
    def edge(source: FlowNode, target: FlowNode, path: str, variable: str) -> FlowEdge:
        item = FlowEdge.create(source.id, target.id)
        item.source_path = path
        item.target_variable = variable
        return item


def verdict_script(*verdicts: bool, default: bool = True) -> Any:
    """Рев'ювер відповідає за списком; решта агентів — звичайним текстом."""
    remaining = list(verdicts)

    def responder(call: dict[str, Any]) -> str:
        model = call["model"]
        if model == "reviewer-model":
            accepted = remaining.pop(0) if remaining else default
            return json.dumps(
                {
                    "verdict": accepted,
                    "score": 90 if accepted else 30,
                    "reason": "Все добре" if accepted else "Немає обробки помилок",
                    "must_fix": [] if accepted else ["Додати обробку помилок"],
                },
                ensure_ascii=False,
            )
        if model == "improver-model":
            return json.dumps(
                {"improved_prompt": "Уточнена задача", "notes": ["додано критерії"]},
                ensure_ascii=False,
            )
        return f"[{model}] результат"

    return responder


def test_template_and_safe_expression() -> None:
    context = {"inputs": {"score": 91}, "score": 91, "status": "ready"}
    assert render_template("{{score}} / {{status}}", context) == "91 / ready"
    assert safe_eval('score >= 80 and status == "ready"', context) is True


def test_existing_input_files_avoids_realpath_for_arbitrary_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated = tmp_path / "generated.png"
    generated.write_bytes(b"image")

    def fail_resolve(*args: object, **kwargs: object) -> Path:
        raise AssertionError("Path.resolve must not run while scanning agent text")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    files = WorkflowRunner._existing_input_files(
        {
            "message": "This is a normal agent status message, not a path.",
            "files": ["generated.png", str(generated)],
        },
        tmp_path,
    )

    assert files == [str(generated.absolute())]


def test_existing_input_files_handles_cyclic_containers(tmp_path: Path) -> None:
    generated = tmp_path / "generated.png"
    generated.write_bytes(b"image")
    cyclic: list[object] = ["generated.png"]
    cyclic.append(cyclic)

    assert WorkflowRunner._existing_input_files(cyclic, tmp_path) == [
        str(generated.absolute())
    ]


def test_cycle_through_result_is_allowed(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    assert pipeline.workflow.validate() == []


def test_cycle_without_result_is_rejected() -> None:
    workflow = Workflow()
    first = FlowNode.create("executor")
    second = FlowNode.create("prompt_reviewer")
    workflow.nodes = [first, second]
    workflow.edges = [
        FlowEdge.create(first.id, second.id),
        FlowEdge.create(second.id, first.id),
    ]
    assert any("цикл" in error for error in workflow.validate())


def test_tasks_manager_processes_each_task_and_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_file = tmp_path / "first.md"
    second_file = tmp_path / "second.png"
    first_file.write_text("first", encoding="utf-8")
    second_file.write_bytes(b"second")

    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {
            "id": "task-one",
            "prompt": "Проаналізуй перше завдання",
            "attachments": [str(first_file)],
        },
        {
            "id": "task-two",
            "prompt": "Виконай друге завдання",
            "attachments": [str(second_file)],
        },
    ]
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    result.config["true_limit"] = 1

    to_reviewer = FlowEdge.create(manager.id, reviewer.id, "next")
    to_reviewer.source_path = "data"
    to_reviewer.target_variable = "input"
    to_result = FlowEdge.create(reviewer.id, result.id)
    to_result.source_path = "data"
    to_result.target_variable = "review"
    next_task = FlowEdge.create(result.id, manager.id, "true")
    next_task.source_path = "data"
    next_task.target_variable = "input"
    workflow = Workflow(
        name="Tasks",
        workspace=str(tmp_path),
        nodes=[manager, reviewer, result],
        edges=[to_reviewer, to_result, next_task],
    )
    assert workflow.validate() == []
    assert workflow.ports_of(manager.id) == ("next", "done")
    assert workflow.result_port_limit(result, "true") == 2

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True, True))
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(
        workflow,
        run_directory=tmp_path / "runs",
        on_event=events.append,
    )
    checkpoint = runner.run()

    assert checkpoint.iterations[manager.id] == 3
    assert checkpoint.iterations[reviewer.id] == 2
    assert checkpoint.port_counts[f"{result.id}:true"] == 2
    progress = checkpoint.task_progress[manager.id]
    assert progress["active_task_id"] == ""
    assert progress["completed_task_ids"] == ["task-one", "task-two"]
    assert set(checkpoint.task_transition_receipts) == {
        f"{manager.id}:task-one",
        f"{manager.id}:task-two",
    }
    assert all(
        receipt["confirmed_by_user"] is False
        for receipt in checkpoint.task_transition_receipts.values()
    )
    assert set(progress["times"]) == {"task-one", "task-two"}
    assert all(record["seconds"] >= 0 for record in progress["times"].values())
    assert runner.outputs[manager.id].data["branch"] == "done"
    progress = [event for event in events if event["type"] == "tasks_progress"]
    assert [event["completed_count"] for event in progress] == [0, 1, 2]
    reviewer_calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "reviewer-model"
    ]
    assert len(reviewer_calls) == 2
    assert "Проаналізуй перше завдання" in reviewer_calls[0]["prompt"]
    assert reviewer_calls[0]["attachments"][0] == str(first_file)
    assert reviewer_calls[0]["attachments"][1].endswith(".json")
    assert "Виконай друге завдання" in reviewer_calls[1]["prompt"]
    assert reviewer_calls[1]["attachments"][0] == str(second_file)
    assert reviewer_calls[1]["attachments"][1].endswith(".json")


def test_result_without_task_reviewer_is_rejected() -> None:
    workflow = Workflow()
    entry = FlowNode.create("entry")
    result = FlowNode.create("result")
    workflow.nodes = [entry, result]
    workflow.edges = [FlowEdge.create(entry.id, result.id)]
    assert any("Task Reviewer" in error for error in workflow.validate())


def test_sidecar_node_cannot_be_wired() -> None:
    workflow = Workflow()
    entry = FlowNode.create("entry")
    watcher = FlowNode.create("work_reviewer")
    workflow.nodes = [entry, watcher]
    workflow.edges = [FlowEdge.create(entry.id, watcher.id)]
    assert any("не має входів" in error for error in workflow.validate())


def test_format_one_is_rejected_with_a_clear_message() -> None:
    legacy = {
        "format_version": 1,
        "name": "Старий Flow",
        "nodes": [{"id": "a", "kind": "input", "title": "Вхід"}],
        "edges": [],
    }
    with pytest.raises(UnsupportedFlowFormat) as info:
        Workflow.from_dict(legacy)
    assert "більше не підтримується" in str(info.value)


def test_round_trip_persistence(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.executor.width = 360
    pipeline.executor.height = 240
    target = save_workflow(pipeline.workflow, tmp_path / "flow")
    restored = load_workflow(target)
    assert restored.format_version == 2
    assert [node.kind for node in restored.nodes] == [
        node.kind for node in pipeline.workflow.nodes
    ]
    restored_executor = restored.node(pipeline.executor.id)
    assert restored_executor.width == 360
    assert restored_executor.height == 240
    back = next(edge for edge in restored.edges if edge.source_port == "false")
    assert back.target == pipeline.executor.id

    old_layout = pipeline.workflow.to_dict()
    for raw_node in old_layout["nodes"]:
        raw_node.pop("width")
        raw_node.pop("height")
    restored_old_layout = Workflow.from_dict(old_layout)
    assert all(node.width == 220 for node in restored_old_layout.nodes)
    assert all(node.height == 130 for node in restored_old_layout.nodes)


def test_loop_reruns_executor_until_reviewer_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_adapter, "FAKE_RESPONDER", verdict_script(False, False, True)
    )
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    checkpoint = runner.run()

    assert checkpoint.iterations[pipeline.executor.id] == 3
    assert checkpoint.iterations[pipeline.result.id] == 3
    assert checkpoint.port_counts[f"{pipeline.result.id}:false"] == 2
    assert checkpoint.port_counts[f"{pipeline.result.id}:true"] == 1
    assert runner.outputs[pipeline.result.id].data["verdict"] is True
    reviewer_calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "reviewer-model"
    ]
    assert len(reviewer_calls) == 3


def test_structured_must_fix_and_candidate_path_reach_retry_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews = [
        {
            "verdict": False,
            "score": 70,
            "candidate_path": str(tmp_path / "candidate.png"),
            "reason": "Локальна правка",
            "must_fix": [
                {
                    "id": "car_shadow",
                    "type": "visual",
                    "bbox": [10, 20, 110, 220],
                    "restore_from": "source.png",
                    "allowed_change": "лише тінь",
                    "acceptance": "контур авто незмінний",
                }
            ],
        },
        {
            "verdict": True,
            "score": 96,
            "candidate_path": str(tmp_path / "candidate.png"),
            "reason": "Прийнято",
            "must_fix": [],
        },
    ]

    def responder(call: dict[str, Any]) -> str:
        if call["model"] == "reviewer-model":
            return json.dumps(reviews.pop(0), ensure_ascii=False)
        if call["model"] == "improver-model":
            return json.dumps({"improved_prompt": "Task", "notes": []})
        return "work"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    runner.run()

    executor_calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "executor-model"
    ]
    assert len(executor_calls) == 2
    retry_prompt = executor_calls[1]["prompt"]
    assert "car_shadow" in retry_prompt
    assert '"bbox"' in retry_prompt
    encoded_candidate = json.dumps(str(tmp_path / "candidate.png"))[1:-1]
    assert encoded_candidate in retry_prompt


def test_executor_keeps_one_codex_thread_across_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(False, True))
    pipeline = Pipeline(tmp_path)
    WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs").run()

    calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "executor-model"
    ]
    assert len(calls) == 2
    assert calls[0]["resumed"] is False
    assert calls[1]["resumed"] is True
    assert calls[0]["thread_id"] == calls[1]["thread_id"]


def test_fresh_memory_starts_a_new_thread_every_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(False, True))
    pipeline = Pipeline(tmp_path)
    pipeline.executor.config["memory"] = "fresh"
    WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs").run()

    calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "executor-model"
    ]
    assert [call["resumed"] for call in calls] == [False, False]
    assert calls[0]["thread_id"] != calls[1]["thread_id"]


def test_false_limit_pauses_and_added_attempts_resume_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_adapter, "FAKE_RESPONDER", verdict_script(False, False, True)
    )
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["false_limit"] = 1

    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    checkpoint = runner.run()

    waiting = runner.outputs[pipeline.result.id]
    assert waiting.status == "waiting"
    request = waiting.data["request"]
    assert request["type"] == "result_limit"
    assert request["port"] == "false"
    assert request["used"] == 1
    assert request["limit"] == 1
    assert "Немає обробки помилок" in request["reason"]
    executor_runs_before = checkpoint.iterations[pipeline.executor.id]

    resumed = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            pipeline.result.id: {
                "action": "add_attempts",
                "count": 2,
                "note": "Не чіпай публічний API",
            }
        },
        run_directory=tmp_path / "runs",
    )
    final = resumed.run()

    assert resumed.outputs[pipeline.result.id].data["verdict"] is True
    assert final.iterations[pipeline.executor.id] > executor_runs_before
    # Покращувач промпту не запускався вдруге — продовжили з чекпоінта.
    assert final.iterations[pipeline.improver.id] == 1
    assert any(
        "Не чіпай публічний API" in call["prompt"]
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "executor-model"
    )


def test_result_can_pause_for_manual_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True))
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["wait_for_confirmation"] = True

    first = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    checkpoint = first.run()
    waiting = first.outputs[pipeline.result.id]

    assert waiting.status == "waiting"
    assert waiting.data["request"]["type"] == "result_confirmation"
    assert waiting.data["request"]["score"] == 90
    assert waiting.data["request"]["review"]["verdict"] is True
    assert checkpoint.port_counts == {}

    resumed = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={pipeline.result.id: {"action": "continue"}},
        run_directory=tmp_path / "runs",
    )
    final = resumed.run()

    assert resumed.outputs[pipeline.result.id].data["branch"] == "true"
    assert final.port_counts[f"{pipeline.result.id}:true"] == 1


def test_manual_result_feedback_reaches_the_false_branch_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(False, True))
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["wait_for_confirmation"] = True

    first = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    checkpoint = first.run()
    assert first.outputs[pipeline.result.id].status == "waiting"

    feedback = "Збережи дах і перемалюй лише контур навколо теплиці"
    events: list[dict[str, Any]] = []
    resumed = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            pipeline.result.id: {
                "action": "continue_with_feedback",
                "note": feedback,
            }
        },
        run_directory=tmp_path / "runs",
        on_event=events.append,
    )
    updated = resumed.run()

    executor_prompts = [
        str(call["prompt"])
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "executor-model"
    ]
    assert len(executor_prompts) == 2
    assert feedback in executor_prompts[-1]
    reviewer_prompts = [
        str(call["prompt"])
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "reviewer-model"
    ]
    assert len(reviewer_prompts) == 2
    assert feedback in reviewer_prompts[-1]
    reviewer_events = [
        event
        for event in events
        if event["type"] == "agent_prompt"
        and event["node_id"] == pipeline.reviewer.id
    ]
    assert feedback in reviewer_events[-1]["instructions"]
    scope = f"result:{pipeline.result.id}"
    assert updated.user_requirements[scope] == [feedback]
    restored = RunCheckpoint.from_dict(updated.to_dict())
    assert restored.user_requirements[scope] == [feedback]
    successful_results = [
        item
        for item in updated.history[pipeline.result.id]
        if item["status"] == "success"
    ]
    assert successful_results[-1]["data"]["forced"] is True
    assert f"{pipeline.result.id}:false" not in updated.port_counts


def test_manual_result_feedback_persists_for_later_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_adapter, "FAKE_RESPONDER", verdict_script(False, False, True)
    )
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["wait_for_confirmation"] = True

    first = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    checkpoint = first.run()
    feedback = "Кіоск і всі логічно пов'язані товари є однією групою"

    second = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            pipeline.result.id: {
                "action": "continue_with_feedback",
                "note": feedback,
            }
        },
        run_directory=tmp_path / "runs",
    )
    second_checkpoint = second.run()
    assert second.outputs[pipeline.result.id].status == "waiting"

    third = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(second_checkpoint.to_dict()),
        intervention_responses={pipeline.result.id: {"action": "continue"}},
        run_directory=tmp_path / "runs",
    )
    third.run()

    executor_prompts = [
        str(call["prompt"])
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "executor-model"
    ]
    reviewer_prompts = [
        str(call["prompt"])
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "reviewer-model"
    ]
    assert len(executor_prompts) == 3
    assert len(reviewer_prompts) == 3
    assert feedback in executor_prompts[-1]
    assert feedback in reviewer_prompts[-1]


def test_result_limits_can_be_updated_while_an_agent_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_executor = threading.Event()
    executor_started = threading.Event()
    verdicts = [False, False, True]

    def responder(call: dict[str, Any]) -> str:
        if call["model"] == "executor-model" and not executor_started.is_set():
            executor_started.set()
            assert release_executor.wait(2)
        if call["model"] == "reviewer-model":
            verdict = verdicts.pop(0)
            return json.dumps(
                {"verdict": verdict, "score": 90, "reason": "QA", "must_fix": []}
            )
        if call["model"] == "improver-model":
            return json.dumps({"improved_prompt": "Task", "notes": []})
        return "work"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["false_limit"] = 1
    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    outcome: list[RunCheckpoint] = []
    thread = threading.Thread(target=lambda: outcome.append(runner.run()))
    thread.start()
    assert executor_started.wait(2)

    assert runner.update_node_config(pipeline.result.id, {"false_limit": 2}) is True
    release_executor.set()
    thread.join(5)

    assert not thread.is_alive()
    assert outcome[0].port_counts[f"{pipeline.result.id}:false"] == 2
    assert runner.outputs[pipeline.result.id].data["verdict"] is True


def test_result_confirmation_can_be_enabled_while_an_agent_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_executor = threading.Event()
    executor_started = threading.Event()

    def responder(call: dict[str, Any]) -> str:
        if call["model"] == "executor-model":
            executor_started.set()
            assert release_executor.wait(2)
            return "work"
        if call["model"] == "reviewer-model":
            return json.dumps(
                {"verdict": True, "score": 90, "reason": "QA", "must_fix": []}
            )
        if call["model"] == "improver-model":
            return json.dumps({"improved_prompt": "Task", "notes": []})
        return "work"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    outcome: list[RunCheckpoint] = []
    thread = threading.Thread(target=lambda: outcome.append(runner.run()))
    thread.start()
    assert executor_started.wait(2)

    assert (
        runner.update_node_config(
            pipeline.result.id, {"wait_for_confirmation": True}
        )
        is True
    )
    release_executor.set()
    thread.join(5)

    assert not thread.is_alive()
    assert outcome[0].port_counts == {}
    waiting = runner.outputs[pipeline.result.id]
    assert waiting.status == "waiting"
    assert waiting.data["request"]["type"] == "result_confirmation"


def test_runner_waits_while_system_is_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = Workflow(name="Paused", workspace=str(tmp_path))
    entry = FlowNode.create("entry")
    executor = FlowNode.create("executor")
    workflow.nodes = [entry, executor]
    workflow.edges = [Pipeline.edge(entry, executor, "text", "prompt")]
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: "done")
    runner = WorkflowRunner(workflow)
    runner.pause("locked")

    thread = threading.Thread(target=runner.run)
    thread.start()
    time.sleep(0.05)
    assert codex_adapter.FAKE_CALLS == []

    runner.resume("unlocked")
    thread.join(3)
    assert not thread.is_alive()
    assert len(codex_adapter.FAKE_CALLS) == 1


def test_cancel_interrupts_the_active_agent_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    interrupted = threading.Event()

    class InterruptibleAdapter:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def run_agent(self, **_kwargs: Any) -> Any:
            started.set()
            assert interrupted.wait(3)
            raise RuntimeError("interrupted")

        def cancel_active(self) -> bool:
            interrupted.set()
            return True

    monkeypatch.setattr(engine_module, "CodexAdapter", InterruptibleAdapter)
    workflow = Workflow(name="Cancel", workspace=str(tmp_path))
    entry = FlowNode.create("entry")
    executor = FlowNode.create("executor")
    workflow.nodes = [entry, executor]
    workflow.edges = [Pipeline.edge(entry, executor, "text", "prompt")]
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(workflow, on_event=events.append)

    thread = threading.Thread(target=runner.run)
    thread.start()
    assert started.wait(2)
    runner.cancel()
    thread.join(5)

    assert not thread.is_alive()
    assert any(event["type"] == "node_cancelled" for event in events)
    assert any(event["type"] == "run_stopped" for event in events)


def test_required_artifact_is_fingerprinted_and_source_is_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"protected-source")
    output = tmp_path / "candidate.png"
    output.write_bytes(b"old")

    def responder(call: dict[str, Any]) -> str:
        if call["model"] != "executor-model":
            return "ok"
        temporary = tmp_path / "candidate.tmp.png"
        temporary.write_bytes(b"new-artifact")
        temporary.replace(output)
        return json.dumps({"candidate_path": str(output)})

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    workflow = Workflow(name="Artifact", workspace=str(tmp_path))
    entry = FlowNode.create("entry")
    entry.config["text"] = "edit"
    executor = FlowNode.create("executor")
    executor.config.update(
        {
            "model": "executor-model",
            "output_format": "json",
            "required_output_path": str(output),
            "protected_source_path": str(source),
            "protected_source_sha256": sha256(source.read_bytes()).hexdigest().upper(),
        }
    )
    workflow.nodes = [entry, executor]
    workflow.edges = [Pipeline.edge(entry, executor, "text", "prompt")]

    runner = WorkflowRunner(workflow)
    runner.run()

    result = runner.outputs[executor.id]
    assert result.status == "success"
    assert result.data["candidate_path"] == str(output.resolve())
    assert (
        result.data["artifact"]["sha256"] == sha256(b"new-artifact").hexdigest().upper()
    )
    assert source.read_bytes() == b"protected-source"


def test_required_artifact_must_be_updated_in_the_current_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "candidate.png"
    output.write_bytes(b"stale")
    monkeypatch.setattr(
        codex_adapter,
        "FAKE_RESPONDER",
        lambda _call: json.dumps({"candidate_path": str(output)}),
    )
    workflow = Workflow(name="Stale artifact", workspace=str(tmp_path))
    entry = FlowNode.create("entry")
    entry.config["text"] = "edit"
    executor = FlowNode.create("executor")
    executor.config.update(
        {
            "model": "executor-model",
            "output_format": "json",
            "required_output_path": str(output),
        }
    )
    workflow.nodes = [entry, executor]
    workflow.edges = [Pipeline.edge(entry, executor, "text", "prompt")]

    runner = WorkflowRunner(workflow)
    runner.run()

    assert runner.outputs[executor.id].status == "failed"
    assert "не оновила артефакт" in runner.outputs[executor.id].error


def test_merge_items_project_uses_the_five_stage_tasks_manager_loop() -> None:
    project_path = (
        Path(__file__).resolve().parents[1]
        / "!_projects"
        / "Merge_items_generator"
        / "Merge_items_generator.flowai.json"
    )
    workflow = load_workflow(project_path)

    assert workflow.validate() == []
    manager = workflow.nodes_of_kind("tasks_manager")[0]
    planner = workflow.nodes_of_kind("prompt_reviewer")[0]
    executor = workflow.nodes_of_kind("executor")[0]
    reviewer = workflow.nodes_of_kind("task_reviewer")[0]
    result = workflow.nodes_of_kind("result")[0]

    tasks = manager.config["tasks"]
    assert [task["id"] for task in tasks] == [
        "task_01_select_restore_remove",
        "task_02_define_damage_language",
        "task_03_build_immutable_base",
        "task_04_extract_restored_assets",
        "task_05_generate_broken_and_assemble",
    ]
    assert all(len(task["attachments"]) >= 3 for task in tasks)
    assert "RESTORE" in tasks[0]["prompt"]
    assert "ENVIRONMENT OVERLAYS" in tasks[2]["prompt"]

    assert any(
        edge.source == manager.id
        and edge.target == planner.id
        and edge.source_port == "next"
        for edge in workflow.edges
    )
    assert any(
        edge.source == result.id
        and edge.target == manager.id
        and edge.source_port == "true"
        for edge in workflow.edges
    )
    assert any(
        edge.source == result.id
        and edge.target == executor.id
        and edge.source_port == "false"
        and edge.source_path == "data.retry_context"
        for edge in workflow.edges
    )
    assert planner.config["output_schema"]["stage"] == "1/5|2/5|3/5|4/5|5/5"
    assert executor.config["output_schema"]["task_id"] == "task_01...task_05"
    assert reviewer.config["criteria_node"] == manager.id
    assert reviewer.config["sandbox"] == "read-only"
    assert "score>=95" in reviewer.config["instructions"]
    assert result.config["true_limit"] == 5


def test_forced_branch_skips_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(False, False))
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["false_limit"] = 1
    pipeline.result.config["save_path"] = "final.txt"

    checkpoint = WorkflowRunner(
        pipeline.workflow, run_directory=tmp_path / "runs"
    ).run()

    resumed = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            pipeline.result.id: {"action": "force_branch", "branch": "true"}
        },
        run_directory=tmp_path / "runs",
    )
    resumed.run()

    result = resumed.outputs[pipeline.result.id]
    assert result.data["branch"] == "true"
    assert result.data["forced"] is True
    assert (tmp_path / "final.txt").read_text(encoding="utf-8").strip()


def test_runaway_loop_hits_the_step_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(default=False))
    pipeline = Pipeline(tmp_path)
    pipeline.result.config["false_limit"] = 10_000

    runner = WorkflowRunner(
        pipeline.workflow, run_directory=tmp_path / "runs", max_steps=12
    )
    with pytest.raises(Exception) as info:
        runner.run()
    assert "ліміт кроків" in str(info.value)


def test_entry_attachments_reach_the_executor(tmp_path: Path) -> None:
    picture = tmp_path / "mock.png"
    picture.write_bytes(b"\x89PNG\r\n")
    pipeline = Pipeline(tmp_path)
    pipeline.entry.config["attachments"] = [str(picture)]
    pipeline.workflow.edges.append(
        Pipeline.edge(
            pipeline.entry, pipeline.executor, "data.attachments", "attachments"
        )
    )

    WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs").run()

    call = next(
        item for item in codex_adapter.FAKE_CALLS if item["model"] == "executor-model"
    )
    assert str(picture) in call["attachments"]


def test_task_reviewer_uses_the_referenced_criteria_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True))
    pipeline = Pipeline(tmp_path)
    pipeline.reviewer.config["criteria_node"] = pipeline.entry.id

    WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs").run()

    call = next(
        item for item in codex_adapter.FAKE_CALLS if item["model"] == "reviewer-model"
    )
    assert "Напиши функцію" in call["prompt"]


def test_task_reviewer_defaults_to_the_improved_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True))
    pipeline = Pipeline(tmp_path)

    WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs").run()

    call = next(
        item for item in codex_adapter.FAKE_CALLS if item["model"] == "reviewer-model"
    )
    assert "Уточнена задача" in call["prompt"]


def test_non_json_reviewer_answer_pauses_with_invalid_contract(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    runner.run()
    waiting = runner.outputs[pipeline.reviewer.id]
    assert waiting.status == "waiting"
    assert waiting.data["request"]["type"] == "invalid_qa_contract"
    assert waiting.data["request"]["errors"]


def test_prompt_reviewer_sees_the_downstream_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True))
    pipeline = Pipeline(tmp_path)

    WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs").run()

    call = next(
        item for item in codex_adapter.FAKE_CALLS if item["model"] == "improver-model"
    )
    assert pipeline.executor.short_id in call["prompt"]
    assert pipeline.result.short_id in call["prompt"]


def test_work_review_protocol_records_every_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(False, True))
    pipeline = Pipeline(tmp_path, with_reviewer_block=True)
    run_directory = tmp_path / "runs" / "one"

    WorkflowRunner(pipeline.workflow, run_directory=run_directory).run()

    protocol = (run_directory / PROTOCOL_NAME).read_text(encoding="utf-8")
    assert pipeline.executor.short_id in protocol
    assert pipeline.entry.short_id in protocol
    assert protocol.count("### Прохід") >= 7
    assert "**Промпт**" in protocol
    assert "Кроки агента" in protocol
    assert pipeline.watcher is not None
    assert f"## {pipeline.watcher.short_id}" not in protocol

    report = (run_directory / REPORT_NAME).read_text(encoding="utf-8")
    assert "watcher-model" in report


def test_work_reviewer_can_watch_a_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True))
    pipeline = Pipeline(tmp_path, with_reviewer_block=True)
    assert pipeline.watcher is not None
    pipeline.watcher.config["monitor_all"] = False
    pipeline.watcher.config["monitored_nodes"] = [pipeline.executor.id]
    run_directory = tmp_path / "runs" / "subset"

    WorkflowRunner(pipeline.workflow, run_directory=run_directory).run()

    protocol = (run_directory / PROTOCOL_NAME).read_text(encoding="utf-8")
    assert f"## {pipeline.executor.short_id}" in protocol
    assert f"## {pipeline.entry.short_id}" not in protocol


def test_intervention_keeps_the_protocol_across_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_adapter, "FAKE_RESPONDER", verdict_script(False, False, True)
    )
    pipeline = Pipeline(tmp_path, with_reviewer_block=True)
    pipeline.result.config["false_limit"] = 1
    run_directory = tmp_path / "runs" / "paused"

    checkpoint = WorkflowRunner(pipeline.workflow, run_directory=run_directory).run()
    passes_before = (
        (run_directory / PROTOCOL_NAME).read_text(encoding="utf-8").count("### Прохід")
    )

    resumed = WorkflowRunner(
        pipeline.workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            pipeline.result.id: {"action": "add_attempts", "count": 2}
        },
        run_directory=run_directory,
    )
    resumed.run()

    protocol = (run_directory / PROTOCOL_NAME).read_text(encoding="utf-8")
    assert protocol.count("### Прохід") > passes_before


def test_result_without_verdict_reports_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Рев'ювер у графі є, але ребро в Result не несе вердикту."""
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script(True))
    workflow = Workflow(name="Без вердикту", workspace=str(tmp_path))
    entry = FlowNode.create("entry")
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    workflow.nodes = [entry, reviewer, result]
    to_reviewer = FlowEdge.create(entry.id, reviewer.id)
    to_reviewer.source_path = "text"
    to_reviewer.target_variable = "work"
    to_result = FlowEdge.create(reviewer.id, result.id)
    to_result.source_path = "text"
    to_result.target_variable = "work"
    workflow.edges = [to_reviewer, to_result]

    runner = WorkflowRunner(workflow, run_directory=tmp_path / "runs")
    runner.run()
    assert runner.outputs[result.id].status == "failed"
    assert "вердикт" in runner.outputs[result.id].error


def test_intervention_required_carries_its_request() -> None:
    error = InterventionRequired({"question": "Що робимо?", "type": "result_limit"})
    assert error.request["type"] == "result_limit"
    assert "Що робимо?" in str(error)


def test_interrupted_turn_raises_instead_of_returning_empty_text() -> None:
    """Перерваний хід не має тихо ставати успішним результатом ноди."""

    class FakeStatus:
        value = "interrupted"

    class FakeResult:
        status = FakeStatus()
        final_response = ""
        items: tuple[Any, ...] = ()

    with pytest.raises(codex_adapter.TurnInterrupted):
        codex_adapter.agent_run_from_turn(FakeResult(), thread_id="thread-1")


def test_codex_cleanup_does_not_mask_a_finished_run() -> None:
    class BrokenClient:
        def close(self) -> None:
            raise OSError(22, "Invalid argument")

    adapter = codex_adapter.CodexAdapter()
    adapter._client = BrokenClient()
    adapter._client_handle = object()

    adapter.__exit__(None, None, None)

    assert adapter._client is None
    assert adapter._client_handle is None


def test_transport_close_restarts_codex_and_requests_a_fresh_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai_codex

    monkeypatch.delenv("FLOWAI_FAKE_CODEX", raising=False)
    stream_closed: list[bool] = []
    restarted: list[bool] = []

    class BrokenStream:
        def __iter__(self) -> Self:
            return self

        def __next__(self) -> Any:
            raise openai_codex.TransportClosedError("stdout closed")

        def close(self) -> None:
            stream_closed.append(True)

    class BrokenTurn:
        id = "turn-1"

        def stream(self) -> BrokenStream:
            return BrokenStream()

    class BrokenThread:
        id = "thread-1"

        def turn(self, _run_input: object) -> BrokenTurn:
            return BrokenTurn()

    class BrokenClient:
        def thread_start(self, **_kwargs: object) -> BrokenThread:
            return BrokenThread()

    adapter = codex_adapter.CodexAdapter()
    adapter._module = openai_codex
    adapter._client = BrokenClient()
    monkeypatch.setattr(adapter, "_restart_client", lambda: restarted.append(True))

    with pytest.raises(codex_adapter.TurnInterrupted) as caught:
        adapter.run_agent(
            prompt="Перевір результат",
            developer_instructions="",
            model="gpt-5.6-sol",
            sandbox="read-only",
            workspace=Path.cwd(),
        )

    assert caught.value.reset_thread is True
    assert restarted == [True]
    assert stream_closed == [True]


def test_runner_recovers_one_transport_failure_without_configured_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = FlowNode.create("task_reviewer")
    workflow = Workflow(name="QA recovery", workspace=str(tmp_path), nodes=[node])
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(workflow, on_event=events.append)
    runner.checkpoint.thread_ids[node.id] = "broken-thread"
    calls = 0

    def execute(*_args: object) -> engine_module.NodeResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise codex_adapter.TurnInterrupted(
                "transport closed", reset_thread=True
            )
        return engine_module.NodeResult(node.id, "success", text="QA завершено")

    monkeypatch.setattr(runner, "_execute_node", execute)

    result = runner._execute_with_retries(
        node=node,
        inputs={},
        workspace=tmp_path,
        codex=None,
    )

    assert result.status == "success"
    assert calls == 2
    assert node.id not in runner.checkpoint.thread_ids
    retry = next(event for event in events if event["type"] == "node_retry")
    assert "новому треді" in retry["message"]


def test_completed_turn_returns_agent_run() -> None:
    class FakeStatus:
        value = "completed"

    class FakeResult:
        status = FakeStatus()
        final_response = "готово"
        items: tuple[Any, ...] = ()

    run = codex_adapter.agent_run_from_turn(FakeResult(), thread_id="thread-1")
    assert run.text == "готово"
    assert run.thread_id == "thread-1"


def test_pause_does_not_interrupt_active_turn(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow)
    interrupts: list[str] = []

    class FakeCodex:
        def cancel_active(self) -> bool:
            interrupts.append("called")
            return True

    runner._active_codex = FakeCodex()  # type: ignore[assignment]
    runner.pause("тест")
    assert interrupts == []
    assert not runner._resume_event.is_set()
    runner.resume("тест")
    assert runner._resume_event.is_set()


def test_tasks_manager_measures_time_per_task(tmp_path: Path) -> None:
    workflow = Workflow(name="Черга", workspace=str(tmp_path))
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Перше", "attachments": []},
        {"id": "t2", "prompt": "Друге", "attachments": []},
    ]
    workflow.nodes = [manager]
    runner = WorkflowRunner(workflow)

    first = runner._execute_tasks_manager(manager)
    assert first.data["task"]["id"] == "t1"
    assert first.data["tasks"][0]["seconds"] == 0.0

    time.sleep(0.05)
    second = runner._execute_tasks_manager(manager)
    states = {item["id"]: item for item in second.data["tasks"]}
    assert states["t1"]["status"] == "completed"
    assert states["t1"]["seconds"] >= 0.05
    assert states["t2"]["status"] == "running"
    assert second.data["total_seconds"] >= 0.05


def test_agent_run_collects_token_usage() -> None:
    class FakeStatus:
        value = "completed"

    class FakeBreakdown:
        input_tokens = 100
        cached_input_tokens = 20
        output_tokens = 30
        reasoning_output_tokens = 10
        total_tokens = 160

    class FakeUsage:
        last = FakeBreakdown()
        model_context_window = 400000

    class FakeResult:
        status = FakeStatus()
        final_response = "готово"
        items: tuple[Any, ...] = ()
        usage = FakeUsage()

    run = codex_adapter.agent_run_from_turn(FakeResult(), thread_id="t")
    assert run.usage["total_tokens"] == 160
    assert run.usage["reasoning_output_tokens"] == 10
    assert run.context_window == 400000


def test_node_result_carries_usage(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow)
    checkpoint = runner.run()
    executor_output = checkpoint.outputs[pipeline.executor.id]
    assert executor_output["data"]["usage"]["total_tokens"] > 0
    assert executor_output["data"]["usage"]["context_window"] == 400000


def test_stream_activity_reaches_engine(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(
        pipeline.workflow, on_event=lambda event: events.append(event)
    )
    runner.run()
    activity = [item for item in events if item["type"] == "agent_activity"]
    assert activity, "Рушій має емітити хід агента"
    assert activity[0]["node_id"]
    assert activity[0]["message"]


def test_grill_summary_reaches_prompt_reviewer(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.workflow.grill_summary = "Ринок: мобільні ігри"
    pipeline.improver.config["prompt"] = "{{entry_prompt}}\n{{grill_summary}}"
    runner = WorkflowRunner(pipeline.workflow)
    runner.run()
    improver_call = next(
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "improver-model"
    )
    assert "Ринок: мобільні ігри" in improver_call["prompt"]


def test_result_has_exhausted_port_and_attempt_limit() -> None:
    result = FlowNode.create("result")
    assert result.config["task_attempt_limit"] == 2
    workflow = Workflow(nodes=[result])
    assert workflow.ports_of(result.id) == ("true", "false", "exhausted")


def test_exhausted_edge_must_target_tasks_manager() -> None:
    workflow = Workflow()
    result = FlowNode.create("result")
    executor = FlowNode.create("executor")
    workflow.nodes = [result, executor]
    workflow.edges = [FlowEdge.create(result.id, executor.id, "exhausted")]
    errors = workflow.validate()
    assert any("EXHAUSTED" in error for error in errors)


def test_exhausted_target_returns_manager() -> None:
    workflow = Workflow()
    result = FlowNode.create("result")
    manager = FlowNode.create("tasks_manager")
    workflow.nodes = [result, manager]
    workflow.edges = [FlowEdge.create(result.id, manager.id, "exhausted")]
    assert workflow.exhausted_target(result.id) is manager


def _task_budget_workflow(
    tmp_path: Path, *, with_exhausted: bool
) -> tuple[Workflow, FlowNode, FlowNode]:
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Перше", "attachments": []},
        {"id": "t2", "prompt": "Друге", "attachments": []},
    ]
    executor = FlowNode.create("executor")
    executor.config["model"] = "executor-model"
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    result.config["task_attempt_limit"] = 2
    result.config["false_limit"] = 99
    manager_to_executor = FlowEdge.create(manager.id, executor.id, "next")
    manager_to_executor.source_path = "data.prompt"
    manager_to_executor.target_variable = "prompt"
    executor_to_reviewer = FlowEdge.create(executor.id, reviewer.id)
    executor_to_reviewer.source_path = "data"
    executor_to_reviewer.target_variable = "work"
    reviewer_to_result = FlowEdge.create(reviewer.id, result.id)
    reviewer_to_result.source_path = "data"
    reviewer_to_result.target_variable = "review"
    retry = FlowEdge.create(result.id, executor.id, "false")
    retry.source_path = "data.retry_context"
    retry.target_variable = "prompt"
    edges = [
        manager_to_executor,
        executor_to_reviewer,
        reviewer_to_result,
        retry,
        FlowEdge.create(result.id, manager.id, "true"),
    ]
    if with_exhausted:
        edges.append(FlowEdge.create(result.id, manager.id, "exhausted"))
    workflow = Workflow(
        name="Бюджет завдань",
        workspace=str(tmp_path),
        nodes=[manager, executor, reviewer, result],
        edges=edges,
    )
    return workflow, manager, result


def test_task_exhausts_own_attempt_budget(tmp_path: Path) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )

    def always_reject(call: dict[str, Any]) -> str:
        if call["model"] == "reviewer-model":
            return json.dumps(
                {"verdict": False, "score": 1, "reason": "ні", "must_fix": ["фікс"]}
            )
        return "робота"

    codex_adapter.FAKE_RESPONDER = always_reject
    checkpoint = WorkflowRunner(workflow).run()

    progress = checkpoint.task_progress[manager.id]
    assert progress["failed_task_ids"] == ["t1", "t2"]
    assert checkpoint.task_attempts[f"{result.id}:t1"] == 2
    assert checkpoint.task_attempts[f"{result.id}:t2"] == 2
    assert all(
        state["status"] == "failed"
        for state in checkpoint.outputs[manager.id]["data"]["tasks"]
    )


def test_manual_feedback_does_not_spend_the_managed_task_attempt(
    tmp_path: Path,
) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    result.config["wait_for_confirmation"] = True

    def always_reject(call: dict[str, Any]) -> str:
        if call["model"] == "reviewer-model":
            return json.dumps(
                {"verdict": False, "score": 1, "reason": "ні", "must_fix": []}
            )
        return "робота"

    codex_adapter.FAKE_RESPONDER = always_reject
    first = WorkflowRunner(workflow)
    checkpoint = first.run()
    feedback = "Не розділяй логічну групу на окремі шари"

    resumed = WorkflowRunner(
        workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            result.id: {"action": "continue_with_feedback", "note": feedback}
        },
    )
    updated = resumed.run()

    assert resumed.outputs[result.id].status == "waiting"
    assert f"{result.id}:t1" not in updated.task_attempts
    assert f"{result.id}:false" not in updated.port_counts
    scope = f"task:{manager.id}:t1"
    assert updated.user_requirements[scope] == [feedback]
    assert updated.task_transition_receipts == {}
    executor_prompts = [
        str(call["prompt"])
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "executor-model"
    ]
    reviewer_prompts = [
        str(call["prompt"])
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "reviewer-model"
    ]
    assert feedback in executor_prompts[-1]
    assert feedback in reviewer_prompts[-1]


def test_confirmed_true_result_records_and_forwards_task_transition(
    tmp_path: Path,
) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    result.config["wait_for_confirmation"] = True
    candidate = tmp_path / "review-board.png"
    candidate.write_bytes(b"board")

    def accept(call: dict[str, Any]) -> str:
        if call["model"] == "reviewer-model":
            return json.dumps(
                {
                    "verdict": True,
                    "score": 100,
                    "reason": "готово",
                    "must_fix": [],
                    "candidate_path": str(candidate),
                },
                ensure_ascii=False,
            )
        return "робота"

    codex_adapter.FAKE_RESPONDER = accept
    first = WorkflowRunner(workflow)
    checkpoint = first.run()

    assert first.outputs[result.id].status == "waiting"
    assert checkpoint.task_transition_receipts == {}

    resumed = WorkflowRunner(
        workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={result.id: {"action": "continue"}},
    )
    updated = resumed.run()

    progress = updated.task_progress[manager.id]
    assert progress["completed_task_ids"] == ["t1"]
    assert progress["active_task_id"] == "t2"
    receipt = updated.task_transition_receipts[f"{manager.id}:t1"]
    assert receipt["status"] == "approved"
    assert receipt["manager_id"] == manager.id
    assert receipt["task_id"] == "t1"
    assert receipt["result_node_id"] == result.id
    assert receipt["branch"] == "true"
    assert receipt["verdict"] is True
    assert receipt["confirmed_by_user"] is True
    assert receipt["candidate_path"] == str(candidate)
    assert datetime.fromisoformat(receipt["confirmed_at"]).tzinfo is not None
    assert (
        resumed.outputs[manager.id].data["previous_task_transition"] == receipt
    )

    executor_call = next(
        call
        for call in reversed(codex_adapter.FAKE_CALLS)
        if call["model"] == "executor-model"
    )
    reviewer_call = next(
        call
        for call in reversed(codex_adapter.FAKE_CALLS)
        if call["model"] == "reviewer-model"
    )
    for call in (executor_call, reviewer_call):
        instructions = str(call["developer_instructions"])
        assert "# Підтверджений перехід Flow" in instructions
        assert '"task_id": "t1"' in instructions
        assert "FlowAI вже атомарно застосував" in instructions


def test_old_completed_true_result_recovers_transition_receipt(
    tmp_path: Path,
) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    result.config["wait_for_confirmation"] = True
    finished_at = "2026-08-24T15:41:53+00:00"
    checkpoint = RunCheckpoint(
        task_progress={
            manager.id: {
                "active_task_id": "t2",
                "completed_task_ids": ["t1"],
                "failed_task_ids": [],
            }
        },
        history={
            result.id: [
                {
                    "status": "success",
                    "finished_at": finished_at,
                    "data": {
                        "branch": "true",
                        "verdict": True,
                        "task_id": "t1",
                        "candidate_path": str(tmp_path / "board.png"),
                    },
                }
            ]
        },
    )

    WorkflowRunner(workflow, checkpoint=checkpoint)

    receipt = checkpoint.task_transition_receipts[f"{manager.id}:t1"]
    assert receipt["status"] == "approved"
    assert receipt["confirmed_by_user"] is True
    assert receipt["confirmed_at"] == finished_at
    assert receipt["recovered_from_history"] is True

    restored = RunCheckpoint.from_dict(checkpoint.to_dict())
    assert restored.task_transition_receipts == checkpoint.task_transition_receipts


def test_false_or_exhausted_history_never_recovers_approval(
    tmp_path: Path,
) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    checkpoint = RunCheckpoint(
        task_progress={
            manager.id: {
                "active_task_id": "t2",
                "completed_task_ids": ["t1"],
                "failed_task_ids": [],
            }
        },
        history={
            result.id: [
                {
                    "status": "success",
                    "data": {
                        "branch": "false",
                        "verdict": False,
                        "task_id": "t1",
                    },
                },
                {
                    "status": "success",
                    "data": {
                        "branch": "exhausted",
                        "verdict": False,
                        "task_id": "t1",
                    },
                },
            ]
        },
    )

    WorkflowRunner(workflow, checkpoint=checkpoint)

    assert checkpoint.task_transition_receipts == {}


def test_forced_true_from_false_does_not_create_approval_receipt(
    tmp_path: Path,
) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    result.config["wait_for_confirmation"] = True
    codex_adapter.FAKE_RESPONDER = lambda call: (
        json.dumps(
            {"verdict": False, "score": 0, "reason": "ні", "must_fix": []}
        )
        if call["model"] == "reviewer-model"
        else "робота"
    )
    first = WorkflowRunner(workflow)
    checkpoint = first.run()

    resumed = WorkflowRunner(
        workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
        intervention_responses={
            result.id: {"action": "force_branch", "branch": "true"}
        },
    )
    updated = resumed.run()

    assert f"{manager.id}:t1" not in updated.task_transition_receipts


def test_stop_while_result_waits_does_not_create_transition_receipt(
    tmp_path: Path,
) -> None:
    workflow, _manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    result.config["wait_for_confirmation"] = True
    codex_adapter.FAKE_RESPONDER = lambda call: (
        json.dumps(
            {"verdict": True, "score": 100, "reason": "так", "must_fix": []}
        )
        if call["model"] == "reviewer-model"
        else "робота"
    )
    first = WorkflowRunner(workflow)
    checkpoint = first.run()
    stopped = WorkflowRunner(
        workflow,
        checkpoint=RunCheckpoint.from_dict(checkpoint.to_dict()),
    )
    stopped.cancel()

    updated = stopped.run()

    assert updated.task_transition_receipts == {}


def test_old_checkpoint_feedback_is_migrated_for_the_current_task(
    tmp_path: Path,
) -> None:
    workflow, manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=True
    )
    feedback = "Групуй кіоск разом із товарами"
    checkpoint = RunCheckpoint(
        task_progress={manager.id: {"active_task_id": "t1"}},
        history={
            result.id: [
                {
                    "status": "success",
                    "data": {
                        "branch": "false",
                        "user_note": feedback,
                    },
                }
            ]
        },
    )
    runner = WorkflowRunner(workflow, checkpoint=checkpoint)

    requirements = runner._requirements_for_result(result, manager, "t1")

    assert requirements == [feedback]
    assert checkpoint.user_requirements[f"task:{manager.id}:t1"] == [feedback]


def test_without_exhausted_edge_old_dialog_still_fires(tmp_path: Path) -> None:
    workflow, _manager, result = _task_budget_workflow(
        tmp_path, with_exhausted=False
    )
    result.config["task_attempt_limit"] = 1
    result.config["false_limit"] = 1
    codex_adapter.FAKE_RESPONDER = lambda call: (
        json.dumps({"verdict": False, "score": 1, "reason": "ні", "must_fix": []})
        if call["model"] == "reviewer-model"
        else "робота"
    )
    checkpoint = WorkflowRunner(workflow).run()
    waiting = [
        item for item in checkpoint.outputs.values() if item.get("status") == "waiting"
    ]
    assert waiting, "Без жовтого ребра має спрацювати старий діалог ліміту"


def test_fake_run_records_pinned_skills(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.executor.config["skills"] = [
        {"name": "birds-map", "path": str(tmp_path / "birds-map")}
    ]
    WorkflowRunner(pipeline.workflow).run()
    call = next(
        item for item in codex_adapter.FAKE_CALLS if item["model"] == "executor-model"
    )
    assert call["skills"] == ["birds-map"]


def test_calibrator_is_a_registered_node_kind() -> None:
    assert NODE_LABELS["calibrator"] == "Calibration Stop"
    assert NODE_COLORS["calibrator"] == "#E11D48"
    node = FlowNode.create("calibrator")
    assert node.config["false_threshold"] == 2
    assert node.config["sandbox"] == "read-only"
    assert node.config["output_format"] == "json"
    assert node.config["thread_source"] == ""
    assert node.is_agent is True


def test_calibrator_has_an_output_port() -> None:
    workflow = Workflow()
    node = FlowNode.create("calibrator")
    workflow.nodes.append(node)
    assert workflow.ports_of(node.id) == ("out",)


def test_calibrator_must_hang_on_the_false_port(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(FlowEdge.create(pipeline.result.id, stop.id, "true"))
    errors = pipeline.workflow.validate()
    assert any("Calibration Stop" in error and "FALSE" in error for error in errors)


def test_calibrator_on_the_false_port_validates(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.workflow.edges = [
        edge
        for edge in pipeline.workflow.edges
        if not (
            edge.source == pipeline.result.id
            and edge.target == pipeline.executor.id
            and edge.source_port == "false"
        )
    ]
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "false")
    )
    assert pipeline.workflow.validate() == []
    assert pipeline.workflow.calibrator_for(pipeline.result.id) is stop


def test_only_one_calibrator_per_result(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    for _ in range(2):
        stop = FlowNode.create("calibrator")
        pipeline.workflow.nodes.append(stop)
        pipeline.workflow.edges.append(
            FlowEdge.create(pipeline.result.id, stop.id, "false")
        )
    errors = pipeline.workflow.validate()
    assert any("лише один" in error for error in errors)


def test_calibrator_can_continue_to_executor(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.workflow.edges = [
        edge
        for edge in pipeline.workflow.edges
        if not (
            edge.source == pipeline.result.id
            and edge.target == pipeline.executor.id
            and edge.source_port == "false"
        )
    ]
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "false")
    )
    pipeline.workflow.edges.append(FlowEdge.create(stop.id, pipeline.executor.id, "out"))
    assert pipeline.workflow.validate() == []


def test_result_cannot_bypass_calibrator_on_false(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "false")
    )
    pipeline.workflow.edges.append(
        FlowEdge.create(stop.id, pipeline.executor.id, "out")
    )
    errors = pipeline.workflow.validate()
    assert any("не повинен вести напряму в Executor" in error for error in errors)


def test_calibrator_output_only_accepts_executor(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "false")
    )
    pipeline.workflow.edges.append(
        FlowEdge.create(stop.id, pipeline.reviewer.id, "out")
    )
    errors = pipeline.workflow.validate()
    assert any("лише з блоком Executor" in error for error in errors)
