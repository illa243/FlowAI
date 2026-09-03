from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

import pytest

from flowai import codex_adapter
from flowai.codex_adapter import AgentRun
from flowai.engine import WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.quality_control import (
    ConvergencePolicy,
    ConvergenceTracker,
    OperationIntentError,
    QAContractError,
    build_retry_contract,
    normalize_issue,
    normalize_task_review,
    operation_progress_from_activity,
    protected_artifact_regressions,
    validate_operation_intent,
)


@pytest.fixture(autouse=True)
def fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", None)


def edge(
    source: FlowNode,
    target: FlowNode,
    *,
    port: str = "out",
    source_path: str = "data",
    target_variable: str = "input",
) -> FlowEdge:
    item = FlowEdge.create(source.id, target.id, port)
    item.source_path = source_path
    item.target_variable = target_variable
    return item


def test_qa_true_score_1_is_rejected() -> None:
    with pytest.raises(QAContractError, match="pass_threshold"):
        normalize_task_review(
            {"verdict": True, "score": 1, "must_fix": [], "issues": []},
            pass_threshold=80,
        )


def test_same_defect_different_wording_has_one_stable_id() -> None:
    first = normalize_issue(
        {
            "category": "clean_plate",
            "description": "Visible seam on the lower-right patch boundary",
            "target_files": ["position_E21/ai_clean_plate_registered.png"],
        }
    )
    second = normalize_issue(
        {
            "category": "clean_plate",
            "description": "У нижньому правому куті досі видно шов патча",
            "target_files": ["position_E21/ai_clean_plate_registered.png"],
        }
    )
    ghost = normalize_issue(
        {
            "category": "clean_plate",
            "description": "Ghost fragment remains",
            "target_files": ["position_E21/ai_clean_plate_registered.png"],
        }
    )
    assert first["defect_id"] == second["defect_id"]
    assert first["defect_id"] != ghost["defect_id"]


def test_same_defect_different_wording_pauses_after_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews = iter(
        [
            "Visible seam on the clean plate patch boundary",
            "На clean plate усе ще видно шов у межі патча",
        ]
    )

    def responder(call: dict[str, Any]) -> str:
        if call["model"] == "reviewer-model":
            return json.dumps(
                {
                    "verdict": False,
                    "score": 45,
                    "reason": "Потрібне виправлення",
                    "must_fix": [next(reviews)],
                },
                ensure_ascii=False,
            )
        return "ok"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    entry = FlowNode.create("entry")
    entry.config["text"] = "Перевір candidate"
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    result.config.update(
        {"retry_guard_enabled": True, "retry_guard_threshold": 2, "false_limit": 3}
    )
    workflow = Workflow(
        name="Stable defects",
        workspace=str(tmp_path),
        nodes=[entry, reviewer, result],
        edges=[
            edge(
                entry,
                reviewer,
                source_path="text",
                target_variable="work",
            ),
            edge(reviewer, result, source_path="data", target_variable="review"),
            edge(
                result,
                reviewer,
                port="false",
                source_path="data.retry_context",
                target_variable="work",
            ),
        ],
    )

    runner = WorkflowRunner(workflow)
    runner.run()

    waiting = runner.outputs[result.id]
    assert waiting.status == "waiting"
    request = waiting.data["request"]
    assert request["type"] == "retry_attention"
    assert request["repeat_count"] == 2
    assert request["repeated_defect_ids"]


def test_qa_score_is_scoped_to_task_and_hash(tmp_path: Path) -> None:
    reviewer = FlowNode.create("task_reviewer")
    workflow = Workflow(
        name="Scores", workspace=str(tmp_path), nodes=[reviewer], edges=[]
    )
    runner = WorkflowRunner(workflow)
    failed = normalize_task_review(
        {
            "verdict": False,
            "score": 45,
            "must_fix": ["Visible seam"],
        }
    )
    passed = normalize_task_review(
        {"verdict": True, "score": 90, "must_fix": [], "issues": []}
    )

    runner._record_qa_score(
        reviewer, failed, task_id="E21", artifact_hash="HASH-1"
    )
    e21 = runner._record_qa_score(
        reviewer, passed, task_id="E21", artifact_hash="HASH-2"
    )
    e22 = runner._record_qa_score(
        reviewer, passed, task_id="E22", artifact_hash="HASH-3"
    )

    assert [item["evaluated_artifact_hash"] for item in runner.checkpoint.qa_scores["E21"]] == [
        "HASH-1",
        "HASH-2",
    ]
    assert e21["score_delta_explanation"]["score_delta"] == 45
    assert e21["score_delta_explanation"]["fixed_defect_ids"]
    assert e22["score_delta_explanation"]["kind"] == "first_score_for_task"
    assert e22["score_delta_explanation"]["score_delta"] is None


def test_retry_contract_protects_passed_hashes(tmp_path: Path) -> None:
    clean_plate = tmp_path / "clean_plate.png"
    shadow = tmp_path / "shadow.png"
    clean_plate.write_bytes(b"clean-v1")
    shadow.write_bytes(b"shadow-v1")
    contract = build_retry_contract(
        {
            "verdict": False,
            "score": 60,
            "issues": [
                {
                    "category": "clean_plate",
                    "description": "Visible seam",
                    "target_files": [clean_plate.name],
                }
            ],
            "checks": [
                {
                    "check_id": "clean_plate.patch_seam",
                    "status": "fail",
                    "target_files": [clean_plate.name],
                },
                {
                    "check_id": "shadow.registration",
                    "status": "pass",
                    "target_files": [shadow.name],
                },
            ],
        },
        workspace=tmp_path,
        task_id="E21",
    )

    assert contract["failed_checks"] == ["clean_plate.patch_seam"]
    assert contract["protected_passed_checks"] == ["shadow.registration"]
    assert str(shadow.resolve()) in contract["immutable_hashes"]
    clean_plate.write_bytes(b"clean-v2")
    assert protected_artifact_regressions(contract, tmp_path) == []
    shadow.write_bytes(b"shadow-regressed")
    assert protected_artifact_regressions(contract, tmp_path)[0]["path"] == str(
        shadow.resolve()
    )


def test_legacy_retry_keeps_engine_state_out_of_executor_work(tmp_path: Path) -> None:
    review = normalize_task_review(
        {
            "verdict": False,
            "score": 45,
            "reason": "Потрібні правки",
            "must_fix": [
                "Visible clean plate seam must be reconstructed",
                "progress.json checkpoint and next_step_allowed are stale",
            ],
        },
        strict=False,
    )
    contract = build_retry_contract(review, workspace=tmp_path, task_id="E21")

    assert contract["failed_checks"] == ["missing_requirement.patch_seam"]
    assert [issue["category"] for issue in contract["system_issues"]] == [
        "engine_state"
    ]


def test_operation_intent_rejects_passed_shadow_and_accepts_clean_plate(
    tmp_path: Path,
) -> None:
    contract = {
        "retry_contract_hash": "ABC",
        "failed_checks": ["clean_plate.patch_seam"],
        "protected_passed_checks": ["shadow.registration"],
        "immutable_files": ["shadow.png"],
    }
    common = {
        "retry_contract_hash": "ABC",
        "metric": "seam_pixels",
        "acceptable_threshold": 0,
        "max_operations": 200,
        "no_improvement_patience": 20,
        "checkpoint_every": 10,
    }
    with pytest.raises(OperationIntentError):
        validate_operation_intent(
            {
                **common,
                "target_check": "shadow.registration",
                "output_files": ["shadow.png"],
            },
            contract=contract,
            workspace=tmp_path,
            policy={"max_iterations": 500},
        )

    accepted = validate_operation_intent(
        {
            **common,
            "target_check": "clean_plate.patch_seam",
            "output_files": ["clean_plate.png"],
        },
        contract=contract,
        workspace=tmp_path,
        policy={"max_iterations": 500},
    )
    assert accepted["target_check"] == "clean_plate.patch_seam"
    assert accepted["output_files"] == [str((tmp_path / "clean_plate.png").resolve())]


def test_iterative_command_cannot_start_without_operation_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CommandAdapter:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def run_agent(self, **kwargs: Any) -> AgentRun:
            kwargs["on_activity"](
                {
                    "kind": "CommandExecutionThreadItem",
                    "summary": "python tools/fit_clean_plate.py",
                    "detail": {"command": "python tools/fit_clean_plate.py"},
                    "phase": "started",
                }
            )
            return AgentRun(text="should not complete", thread_id="command-thread")

        def cancel_active(self) -> bool:
            return True

    from flowai import engine as engine_module

    monkeypatch.setattr(engine_module, "CodexAdapter", CommandAdapter)
    entry = FlowNode.create("entry")
    entry.config["json"] = {
        "retry_context": {
            "retry_contract_hash": "ABCDEF0123456789",
            "failed_checks": ["clean_plate.patch_seam"],
            "protected_passed_checks": ["shadow.registration"],
            "editable_files": ["clean_plate.png"],
            "immutable_files": ["shadow.png"],
            "immutable_hashes": {},
        }
    }
    executor = FlowNode.create("executor")
    executor.config["operation_intent_required"] = True
    workflow = Workflow(
        name="Objective guard",
        workspace=str(tmp_path),
        nodes=[entry, executor],
        edges=[edge(entry, executor, source_path="data", target_variable="prompt")],
    )

    runner = WorkflowRunner(workflow)
    runner.run()

    waiting = runner.outputs[executor.id]
    assert waiting.status == "waiting"
    assert waiting.data["request"]["type"] == "operation_intent_rejected"
    assert "fit_clean_plate.py" in waiting.data["request"]["command"]


def test_optimizer_loop_early_stops_and_reports_progress() -> None:
    tracker = ConvergenceTracker(
        ConvergencePolicy(
            max_iterations=100,
            no_improvement_patience=3,
            min_delta=0.1,
        )
    )
    states = [tracker.observe(value) for value in (10.0, 9.95, 9.94, 9.93)]
    assert states[-1]["should_stop"] is True
    assert states[-1]["stop_reason"] == "no_improvement"
    assert states[-1]["iteration"] == 4

    progress = operation_progress_from_activity(
        {"aggregated_output": "progress iteration=40/500 metrics=(2, 4) best=(1, 3)"}
    )
    assert progress == {
        "iteration": 40,
        "max_iterations": 500,
        "best_metric": "(1, 3)",
    }


def test_result_true_atomically_advances_project_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    progress_path = tmp_path / "progress.json"
    progress_path.write_text('{"status": "waiting"}', encoding="utf-8")

    def responder(call: dict[str, Any]) -> str:
        return json.dumps(
            {"verdict": True, "score": 95, "reason": "PASS", "must_fix": []}
        )

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [{"id": "t1", "prompt": "Перевірити"}]
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    result.config["transition_adapter"] = {
        "type": "json_merge",
        "path": "progress.json",
        "merge": {"status": "approved", "task": "{task_id}"},
        "append_unique": {"approved": "{task_id}"},
    }
    workflow = Workflow(
        name="Atomic receipt",
        workspace=str(tmp_path),
        nodes=[manager, reviewer, result],
        edges=[
            edge(manager, reviewer, port="next", target_variable="work"),
            edge(reviewer, result, source_path="data", target_variable="review"),
            edge(result, manager, port="true", target_variable="input"),
        ],
    )

    checkpoint = WorkflowRunner(workflow).run()

    state = json.loads(progress_path.read_text(encoding="utf-8"))
    assert state == {"status": "approved", "task": "t1", "approved": ["t1"]}
    receipt = checkpoint.task_transition_receipts[f"{manager.id}:t1"]
    assert Path(receipt["receipt_path"]).is_file()
    assert receipt["state_patch"]["state_sha256"]


def test_transition_adapter_uses_staged_state_and_task_specific_appends(
    tmp_path: Path,
) -> None:
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "status": "awaiting_confirmation",
                "staged_position": 2,
                "staged_element_id": "E19",
            }
        ),
        encoding="utf-8",
    )
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [{"id": "t1", "prompt": "Approve E19"}]
    result = FlowNode.create("result")
    result.config["transition_adapter"] = {
        "type": "json_merge",
        "path": "progress.json",
        "default_merge": {
            "status": "approved",
            "active_position": "{state.staged_position}",
            "active_element_id": "{state.staged_element_id}",
            "approved_at": "{receipt.confirmed_at}",
            "next_step": "{next_task_number}",
        },
        "default_append_unique": {
            "approved_positions": "{state.staged_position}",
            "approved_element_ids": "{state.staged_element_id}",
        },
        "task_append_unique": {
            "t1": {
                "approved_task_ids": "{task_id}",
                "approved_positions": "{state.staged_position}",
                "approved_element_ids": "{state.staged_element_id}",
            }
        },
    }
    workflow = Workflow(
        name="Staged state transition",
        workspace=str(tmp_path),
        nodes=[manager, result],
        edges=[],
    )
    runner = WorkflowRunner(workflow)
    patch = runner._apply_task_transition_adapter(
        result,
        manager,
        "t1",
        {
            "receipt_id": "receipt-1",
            "confirmed_at": "2026-08-25T22:30:00+03:00",
            "approved_artifact_hash": "abc123",
        },
        tmp_path,
    )

    state = json.loads(progress_path.read_text(encoding="utf-8"))
    assert state == {
        "status": "approved",
        "staged_position": 2,
        "staged_element_id": "E19",
        "active_position": 2,
        "active_element_id": "E19",
        "approved_at": "2026-08-25T22:30:00+03:00",
        "next_step": 2,
        "approved_task_ids": ["t1"],
        "approved_positions": [2],
        "approved_element_ids": ["E19"],
    }
    assert patch["append_unique"]["approved_element_ids"] == "E19"


def test_task_reviewer_compacts_large_file_telemetry() -> None:
    inputs = {
        "work": {
            "response": "E19 is ready",
            "_generated_files": [f"generated-{index}.png" for index in range(50)],
            "_modified_files": [f"modified-{index}.json" for index in range(5)],
            "_file_ledger": [{"path": f"file-{index}"} for index in range(100)],
        }
    }

    compact = WorkflowRunner._compact_review_inputs(inputs, file_sample_limit=3)

    assert compact["work"]["response"] == "E19 is ready"
    assert compact["work"]["_generated_files"] == {
        "count": 50,
        "sample": ["generated-0.png", "generated-1.png", "generated-2.png"],
    }
    assert compact["work"]["_modified_files"]["count"] == 5
    assert compact["work"]["_file_ledger"]["entry_count"] == 100
    assert "file-99" not in json.dumps(compact)


def test_committed_transition_receipt_is_not_replayed_against_later_state(
    tmp_path: Path,
) -> None:
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "status": "awaiting_confirmation",
                "staged_position": 2,
                "staged_element_id": "E19",
                "approved_positions": [1],
                "approved_element_ids": ["E21"],
            }
        ),
        encoding="utf-8",
    )
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [{"id": "e21-task", "prompt": "Approve E21"}]
    result = FlowNode.create("result")
    result.config["transition_adapter"] = {
        "type": "json_merge",
        "path": "progress.json",
        "default_merge": {
            "status": "approved",
            "active_element_id": "{state.staged_element_id}",
        },
    }
    workflow = Workflow(
        name="Receipt replay guard",
        workspace=str(tmp_path),
        nodes=[manager, result],
        edges=[edge(result, manager, port="true", target_variable="input")],
    )
    receipts = tmp_path / ".flowai" / "runtime" / "receipts"
    receipts.mkdir(parents=True)
    receipt_path = receipts / "receipt-e21.json"
    receipt = {
        "receipt_id": "receipt-e21",
        "status": "approved",
        "manager_id": manager.id,
        "task_id": "e21-task",
        "result_node_id": result.id,
        "branch": "true",
        "verdict": True,
        "state_patch": {
            "type": "json_merge",
            "path": str(progress_path),
            "merge": {"status": "approved", "active_element_id": "E21"},
            "append_unique": {"approved_element_ids": "E21"},
            "state_sha256": "already-committed",
        },
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    progress_before = progress_path.read_bytes()
    receipt_before = receipt_path.read_bytes()

    WorkflowRunner(workflow)

    assert progress_path.read_bytes() == progress_before
    assert receipt_path.read_bytes() == receipt_before


def test_read_only_node_cannot_silently_mutate_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MutatingAdapter:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def run_agent(self, **_kwargs: Any) -> AgentRun:
            (tmp_path / "forbidden.txt").write_text("changed", encoding="utf-8")
            return AgentRun(
                text=json.dumps({"improved_prompt": "ok", "notes": []}),
                thread_id="mutation-thread",
            )

        def cancel_active(self) -> bool:
            return True

    from flowai import engine as engine_module

    monkeypatch.setattr(engine_module, "CodexAdapter", MutatingAdapter)
    entry = FlowNode.create("entry")
    prompt = FlowNode.create("prompt_reviewer")
    workflow = Workflow(
        name="Read only audit",
        workspace=str(tmp_path),
        nodes=[entry, prompt],
        edges=[edge(entry, prompt, source_path="text", target_variable="entry_prompt")],
    )

    runner = WorkflowRunner(workflow)
    runner.run()

    waiting = runner.outputs[prompt.id]
    assert waiting.status == "waiting"
    request = waiting.data["request"]
    assert request["type"] == "read_only_mutation"
    assert Path(request["audit_path"]).is_file()


def test_task_thread_does_not_leak_previous_task_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(call: dict[str, Any]) -> str:
        if call["model"] == "reviewer-model":
            return json.dumps(
                {"verdict": True, "score": 90, "reason": "PASS", "must_fix": []}
            )
        return "done"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Перший"},
        {"id": "t2", "prompt": "Другий"},
    ]
    executor = FlowNode.create("executor")
    executor.config.update({"model": "executor-model", "memory": "task_thread"})
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    workflow = Workflow(
        name="Task threads",
        workspace=str(tmp_path),
        nodes=[manager, executor, reviewer, result],
        edges=[
            edge(manager, executor, port="next", target_variable="prompt"),
            edge(executor, reviewer, source_path="data", target_variable="work"),
            edge(reviewer, result, source_path="data", target_variable="review"),
            edge(result, manager, port="true", target_variable="input"),
        ],
    )

    checkpoint = WorkflowRunner(workflow).run()

    first = checkpoint.thread_ids[f"{executor.id}:t1"]
    second = checkpoint.thread_ids[f"{executor.id}:t2"]
    assert first != second
    executor_calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "executor-model"
    ]
    assert [call["resumed"] for call in executor_calls] == [False, False]


def test_prompt_and_qa_caches_reuse_unchanged_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(call: dict[str, Any]) -> str:
        if call["model"] == "prompt-model":
            return json.dumps({"improved_prompt": "prepared", "notes": []})
        return json.dumps(
            {"verdict": True, "score": 91, "reason": "PASS", "must_fix": []}
        )

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    entry = FlowNode.create("entry")
    prompt = FlowNode.create("prompt_reviewer")
    prompt.config["model"] = "prompt-model"
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    workflow = Workflow(
        name="Caches",
        workspace=str(tmp_path),
        nodes=[entry, prompt, reviewer],
        edges=[
            edge(entry, prompt, source_path="text", target_variable="entry_prompt"),
            edge(prompt, reviewer, source_path="text", target_variable="work"),
        ],
    )

    first_events: list[dict[str, Any]] = []
    second_events: list[dict[str, Any]] = []
    WorkflowRunner(workflow, on_event=first_events.append).run()
    WorkflowRunner(workflow, on_event=second_events.append).run()

    assert [call["model"] for call in codex_adapter.FAKE_CALLS].count("prompt-model") == 1
    assert [call["model"] for call in codex_adapter.FAKE_CALLS].count(
        "reviewer-model"
    ) == 1
    hits = [event for event in second_events if event["type"] == "agent_cache_hit"]
    assert {event["node_id"] for event in hits} == {prompt.id, reviewer.id}


def test_source_and_skill_paths_are_not_generated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.png"
    skill = tmp_path / "SKILL.md"
    source.write_bytes(b"source")
    skill.write_text("rules", encoding="utf-8")
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: "done")
    entry = FlowNode.create("entry")
    executor = FlowNode.create("executor")
    executor.config.update(
        {
            "attachments": [str(source)],
            "skills": [{"name": "local", "path": str(skill)}],
        }
    )
    workflow = Workflow(
        name="Ledger",
        workspace=str(tmp_path),
        nodes=[entry, executor],
        edges=[edge(entry, executor, source_path="text", target_variable="prompt")],
    )

    runner = WorkflowRunner(workflow)
    runner.run()

    data = runner.outputs[executor.id].data
    assert "_generated_files" not in data
    assert "_file_ledger" not in data


def test_legacy_flow_loads_with_safe_defaults() -> None:
    legacy = FlowNode.from_dict(
        {
            "id": "legacy-reviewer",
            "kind": "task_reviewer",
            "config": {"output_schema": {"verdict": "boolean"}},
        }
    )
    current = FlowNode.create("task_reviewer")
    assert legacy.config["strict_review_contract"] is False
    assert "score" in legacy.config["output_schema"]
    assert current.config["strict_review_contract"] is True


def test_current_problem_flow_migrates_with_retry_guard() -> None:
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / "!_projects"
        / "ai-flow-20260821-162833"
        / "ai-flow-20260821-162833.flowai.json"
    )
    workflow = Workflow.from_dict(json.loads(path.read_text(encoding="utf-8")))
    executor = workflow.nodes_of_kind("executor")[0]
    reviewer = workflow.nodes_of_kind("task_reviewer")[0]
    result = workflow.nodes_of_kind("result")[0]
    optimizer = workflow.nodes_of_kind("calibrator")[0]
    assert workflow.validate() == []
    assert executor.config["memory"] == "task_thread"
    assert executor.config["operation_intent_required"] is True
    assert executor.config["legacy_retry_upgrade_enabled"] is True
    assert "shadow" in executor.config["legacy_retry_protected_checks"]
    assert any(
        str(path).endswith("e21_retry_accepted_cutout_ai_snapshot.png")
        for path in executor.config["legacy_retry_protected_files"]
    )
    assert reviewer.config["strict_review_contract"] is True
    assert reviewer.config["pass_threshold"] == 80
    assert result.config["retry_guard_enabled"] is True
    assert result.config["retry_guard_threshold"] == 2
    assert result.config["transition_adapter"]["path"].endswith("progress.json")
    assert optimizer.config["false_threshold"] == 2
