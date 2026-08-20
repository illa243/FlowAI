from __future__ import annotations

import json
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Self

import pytest

from flowai import codex_adapter
from flowai import engine as engine_module
from flowai.engine import InterventionRequired, RunCheckpoint, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, UnsupportedFlowFormat, Workflow
from flowai.persistence import load_workflow, save_workflow
from flowai.templating import render_template, safe_eval
from flowai.work_review import PROTOCOL_NAME, REPORT_NAME


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
    target = save_workflow(pipeline.workflow, tmp_path / "flow")
    restored = load_workflow(target)
    assert restored.format_version == 2
    assert [node.kind for node in restored.nodes] == [
        node.kind for node in pipeline.workflow.nodes
    ]
    back = next(edge for edge in restored.edges if edge.source_port == "false")
    assert back.target == pipeline.executor.id


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
    assert any(event["type"] == "run_cancelled" for event in events)


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


def test_merge_items_project_selects_quest_slots_before_generation() -> None:
    project_path = (
        Path(__file__).resolve().parents[1]
        / "!_projects"
        / "Merge_items_generator.flowai.json"
    )
    workflow = load_workflow(project_path)

    assert workflow.validate() == []
    planner = workflow.nodes_of_kind("prompt_reviewer")[0]
    executor = workflow.nodes_of_kind("executor")[0]
    reviewer = workflow.nodes_of_kind("task_reviewer")[0]
    result = workflow.nodes_of_kind("result")[0]

    required_counts = planner.config["output_schema"]["slot_plan"]["required_counts"]
    assert required_counts == {"restore": 11, "remove": 2, "quests": 10}
    assert planner.title == "Вибір квестових предметів"
    assert any(
        str(path).endswith("FIRST_LOCATION_FLAMBE_AUDIT.md")
        for path in planner.config["attachments"]
    )
    assert "substitution_reason" in planner.config["instructions"]

    assert any(
        edge.source == planner.id
        and edge.target == executor.id
        and edge.source_path == "data.slot_plan"
        and edge.target_variable == "slot_plan"
        for edge in workflow.edges
    )
    assert any(
        edge.source == executor.id
        and edge.target == reviewer.id
        and edge.source_path == "data.slot_plan"
        and edge.target_variable == "slot_plan"
        for edge in workflow.edges
    )
    assert any(
        edge.source == result.id
        and edge.target == executor.id
        and edge.source_port == "false"
        and edge.source_path == "data.retry_context"
        for edge in workflow.edges
    )
    assert "previous_review.slot_plan" in executor.config["instructions"]
    assert executor.config["required_output_path"].endswith("final_broken.png")
    assert reviewer.config["output_schema"]["counts"]["expected_restore"] == 11
    assert reviewer.config["output_schema"]["counts"]["expected_remove"] == 2


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


def test_non_json_reviewer_answer_fails_the_node(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow, run_directory=tmp_path / "runs")
    runner.run()
    failed = runner.outputs[pipeline.reviewer.id]
    assert failed.status == "failed"
    assert "verdict" in failed.error


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
