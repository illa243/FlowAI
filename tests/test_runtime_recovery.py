from __future__ import annotations

import json
import threading
from pathlib import Path

from flowai.engine import RunCheckpoint
from flowai.run_history import (
    create_diagnostic_snapshot,
    recover_checkpoint_from_run_log,
    save_checkpoint,
)
from flowai.runtime_state import (
    atomic_write_json,
    diff_workspace,
    index_legacy_attempt_files,
    snapshot_workspace,
)


def test_atomic_checkpoint_is_valid_under_concurrent_events(tmp_path: Path) -> None:
    directory = tmp_path / "runs" / "one"
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            checkpoint = RunCheckpoint(steps=index, event_cursor=index)
            save_checkpoint(
                directory,
                checkpoint,
                project_path=tmp_path / "flow.flowai.json",
                request={"index": index},
            )
        except OSError as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    payload = json.loads((directory / "flowai-checkpoint.json").read_text("utf-8"))
    assert isinstance(payload["checkpoint"]["event_cursor"], int)
    assert list(directory.glob("*.tmp")) == []


def test_stop_recovery_requeues_the_last_active_node(tmp_path: Path) -> None:
    run_log = tmp_path / "flowai-run.json"
    run_log.write_text(
        json.dumps(
            {
                "status": "stopped",
                "events": [
                    {
                        "type": "node_started",
                        "node_id": "executor-E21",
                        "inputs": {"prompt": {"task_id": "previous"}},
                    },
                    {
                        "type": "node_finished",
                        "node_id": "executor-E21",
                        "result": {"status": "success", "data": {}},
                    },
                    {
                        "type": "node_started",
                        "node_id": "executor-E21",
                        "inputs": {"prompt": {"task_id": "E21"}},
                    },
                    {
                        "type": "agent_activity",
                        "node_id": "executor-E21",
                        "message": "python tools/repair.py",
                        "kind": "commandExecution",
                        "phase": "started",
                    },
                    {
                        "type": "node_cancelled",
                        "node_id": "executor-E21",
                        "result": {"status": "cancelled", "data": {}},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    checkpoint = recover_checkpoint_from_run_log(run_log)

    assert checkpoint is not None
    assert checkpoint.active_node_id == "executor-E21"
    assert checkpoint.queue == ["executor-E21"]
    assert checkpoint.pending_inputs["executor-E21"]["prompt"]["task_id"] == "E21"
    assert checkpoint.run_state == "stopped_resumable"
    assert checkpoint.event_cursor == 5
    assert checkpoint.active_operation["activity"] == "python tools/repair.py"


def test_failed_checkpoint_requeues_the_failed_node_for_manual_recovery(
    tmp_path: Path,
) -> None:
    run_log = tmp_path / "flowai-run.json"
    checkpoint = RunCheckpoint(
        started=True,
        run_state="failed",
        task_progress={"manager": {"active_task_id": "E19"}},
    )
    run_log.write_text(
        json.dumps(
            {
                "status": "failed",
                "events": [
                    {
                        "type": "node_started",
                        "node_id": "qa",
                        "node_title": "QA",
                        "iteration": 8,
                        "inputs": {"work": {"candidate_path": "E19/review_board.png"}},
                    },
                    {
                        "type": "node_failed",
                        "node_id": "qa",
                        "result": {"status": "failed", "error": "input too large"},
                    },
                ],
                "checkpoint": checkpoint.to_dict(),
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_checkpoint_from_run_log(run_log)

    assert recovered is not None
    assert recovered.run_state == "stopped_resumable"
    assert recovered.active_node_id == "qa"
    assert recovered.queue == ["qa"]
    assert recovered.pending_inputs["qa"]["work"]["candidate_path"].endswith(
        "review_board.png"
    )
    assert recovered.active_operation["recovered_after_failure"] is True


def test_diagnostic_snapshot_is_versioned_inside_project(tmp_path: Path) -> None:
    project = tmp_path / "project.flowai.json"
    run_directory = tmp_path / "runs" / "one"
    run_directory.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    (run_directory / "flowai-run.json").write_text("{}", encoding="utf-8")
    (run_directory / "flowai-checkpoint.json").write_text("{}", encoding="utf-8")

    first = create_diagnostic_snapshot(
        tmp_path,
        project_path=project,
        run_directory=run_directory,
    )
    second = create_diagnostic_snapshot(
        tmp_path,
        project_path=project,
        run_directory=run_directory,
    )

    assert first != second
    assert first.is_file() and second.is_file()
    first.resolve().relative_to(tmp_path.resolve())
    manifest = json.loads(first.read_text(encoding="utf-8"))
    assert {Path(item["path"]).name for item in manifest["files"]} == {
        "project.flowai.json",
        "flowai-run.json",
        "flowai-checkpoint.json",
    }
    assert all(item["sha256"] for item in manifest["files"])


def test_file_ledger_ignores_engine_runtime_but_detects_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    before = snapshot_workspace(tmp_path, hash_all=True, ignore_runtime=True)
    atomic_write_json(tmp_path / ".flowai" / "runtime" / "packet.json", {"ok": True})
    atomic_write_json(tmp_path / "runs" / "one" / "flowai-checkpoint.json", {})
    artifact = tmp_path / "artifact.png"
    artifact.write_bytes(b"artifact")

    ledger = diff_workspace(
        tmp_path,
        before,
        hash_changed=True,
        ignore_runtime=True,
    )

    assert [Path(item["path"]).name for item in ledger["generated"]] == [
        "artifact.png"
    ]


def test_workspace_snapshot_ignores_atomic_run_checkpoint_temp(tmp_path: Path) -> None:
    temporary = (
        tmp_path
        / "runs"
        / "one"
        / ".flowai-checkpoint.json.0123456789abcdef.tmp"
    )
    temporary.parent.mkdir(parents=True)
    temporary.write_text("temporary", encoding="utf-8")

    snapshot = snapshot_workspace(tmp_path, ignore_runtime=True)

    assert temporary.relative_to(tmp_path).as_posix() not in snapshot


def test_legacy_attempts_are_indexed_without_moving_files(tmp_path: Path) -> None:
    task = tmp_path / "tasks" / "E21"
    task.mkdir(parents=True)
    current = task / "review_board.png"
    candidate = task / "clean_plate_retry_candidate.png"
    snapshot = task / "accepted_shadow_snapshot.png"
    current.write_bytes(b"current")
    candidate.write_bytes(b"candidate")
    snapshot.write_bytes(b"snapshot")

    index_path, pointer_path = index_legacy_attempt_files(
        tmp_path,
        task_id="E21",
        artifact_directory=task,
        current_names=[current.name],
    )

    assert current.read_bytes() == b"current"
    assert candidate.read_bytes() == b"candidate"
    assert snapshot.read_bytes() == b"snapshot"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    roles = {item["name"]: item["role"] for item in index["files"]}
    assert roles == {
        "accepted_shadow_snapshot.png": "protected_snapshot",
        "clean_plate_retry_candidate.png": "legacy_candidate",
        "review_board.png": "current",
    }
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["current_files"][0]["name"] == "review_board.png"
