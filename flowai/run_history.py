from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .runtime_state import atomic_write_json

if TYPE_CHECKING:
    from .engine import RunCheckpoint

LOGGER = logging.getLogger(__name__)
RUN_FILE = "flowai-run.json"
CHECKPOINT_FILE = "flowai-checkpoint.json"
MAX_RUNS = 50


def save_checkpoint(
    directory: Path,
    checkpoint: RunCheckpoint,
    *,
    project_path: Path | None,
    request: dict[str, Any],
) -> Path:
    """Зберегти стан планувальника для продовження після перезапуску."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CHECKPOINT_FILE
    atomic_write_json(
        target,
        {
            "project_path": str(project_path.resolve()) if project_path else "",
            "request": request,
            "checkpoint": checkpoint.to_dict(),
        },
    )
    return target


def load_checkpoint(
    directory: Path,
) -> tuple[RunCheckpoint, dict[str, Any]] | None:
    from .engine import RunCheckpoint

    try:
        payload = json.loads(
            (directory / CHECKPOINT_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("checkpoint")
    if not isinstance(state, dict):
        return None
    request = payload.get("request")
    return RunCheckpoint.from_dict(state), (
        request if isinstance(request, dict) else {}
    )


def clear_checkpoint(directory: Path) -> None:
    """Прибрати чекпоінт після завершення або скасування запуску."""
    (directory / CHECKPOINT_FILE).unlink(missing_ok=True)


def recover_checkpoint_from_run_log(path: Path) -> RunCheckpoint | None:
    """Recover the last safe scheduler state, including legacy event-only logs."""
    from .engine import RunCheckpoint

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    raw_checkpoint = payload.get("checkpoint")
    if isinstance(raw_checkpoint, dict):
        checkpoint = RunCheckpoint.from_dict(raw_checkpoint)
        events = payload.get("events")
        if (
            checkpoint.run_state == "failed"
            and not checkpoint.active_node_id
            and not checkpoint.queue
            and isinstance(events, list)
        ):
            failed_index = next(
                (
                    index
                    for index in range(len(events) - 1, -1, -1)
                    if isinstance(events[index], dict)
                    and events[index].get("type") == "node_failed"
                    and str(events[index].get("node_id") or "")
                ),
                -1,
            )
            if failed_index >= 0:
                failed_id = str(events[failed_index]["node_id"])
                started = next(
                    (
                        event
                        for event in reversed(events[:failed_index])
                        if isinstance(event, dict)
                        and event.get("type") == "node_started"
                        and str(event.get("node_id") or "") == failed_id
                    ),
                    None,
                )
                if isinstance(started, dict):
                    raw_inputs = started.get("inputs")
                    inputs = dict(raw_inputs) if isinstance(raw_inputs, dict) else {}
                    checkpoint.active_node_id = failed_id
                    checkpoint.active_inputs = inputs
                    checkpoint.pending_inputs[failed_id] = dict(inputs)
                    checkpoint.queue = [failed_id]
                    checkpoint.active_operation = {
                        "node_id": failed_id,
                        "node_title": str(started.get("node_title") or ""),
                        "iteration": started.get("iteration"),
                        "started_at": str(started.get("timestamp") or ""),
                        "recovered_after_failure": True,
                    }
                    checkpoint.run_state = "stopped_resumable"
                    checkpoint.saved_at = datetime.now(UTC).isoformat()
        return checkpoint

    events = payload.get("events")
    if not isinstance(events, list):
        return None
    checkpoint = RunCheckpoint(started=True, run_state="recovered")
    last_active_id = ""
    last_active_inputs: dict[str, Any] = {}
    for cursor, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        checkpoint.event_cursor = cursor
        event_type = str(event.get("type") or "")
        node_id = str(event.get("node_id") or "")
        if event_type == "node_started" and node_id:
            checkpoint.steps += 1
            last_active_id = node_id
            raw_inputs = event.get("inputs")
            last_active_inputs = dict(raw_inputs) if isinstance(raw_inputs, dict) else {}
            checkpoint.active_operation = {
                "node_id": node_id,
                "node_title": str(event.get("node_title") or ""),
                "iteration": event.get("iteration"),
                "started_at": str(event.get("timestamp") or ""),
            }
        elif event_type == "agent_activity" and node_id == last_active_id:
            message = str(event.get("message") or "").strip()
            activity_kind = str(event.get("kind") or "")
            generic = message.casefold() in {
                "reasoningthreaditem",
                "agentmessagethreaditem",
                "inprogress",
                "completed",
            }
            if message and not generic:
                checkpoint.active_operation.update(
                    {
                        "activity": message,
                        "activity_kind": activity_kind,
                        "activity_phase": str(event.get("phase") or ""),
                        "event_cursor": cursor,
                    }
                )
            if "command" in activity_kind.casefold() or (
                "python" in message.casefold() and ".py" in message.casefold()
            ):
                checkpoint.active_operation["operation"] = message
        elif event_type in {"node_finished", "node_failed", "node_cancelled"}:
            if (
                node_id
                and event_type != "node_cancelled"
                and node_id == last_active_id
            ):
                last_active_id = ""
                last_active_inputs = {}
                checkpoint.active_operation = {}
            raw_result = event.get("result")
            if node_id and isinstance(raw_result, dict):
                checkpoint.outputs[node_id] = dict(raw_result)
                checkpoint.history.setdefault(node_id, []).append(dict(raw_result))
                checkpoint.iterations[node_id] = (
                    checkpoint.iterations.get(node_id, 0) + 1
                )
                data = raw_result.get("data")
                receipt = (
                    data.get("task_transition_receipt")
                    if isinstance(data, dict)
                    else None
                )
                if isinstance(receipt, dict):
                    manager_id = str(receipt.get("manager_id") or "")
                    task_id = str(receipt.get("task_id") or "")
                    if manager_id and task_id:
                        checkpoint.task_transition_receipts[
                            f"{manager_id}:{task_id}"
                        ] = dict(receipt)
        elif event_type == "tasks_progress" and node_id:
            states = event.get("task_states")
            source = states if isinstance(states, list) else []
            checkpoint.task_progress[node_id] = {
                "active_task_id": str(event.get("active_task_id") or ""),
                "completed_task_ids": [
                    str(item.get("id"))
                    for item in source
                    if isinstance(item, dict) and item.get("status") == "completed"
                ],
                "failed_task_ids": [
                    str(item.get("id"))
                    for item in source
                    if isinstance(item, dict) and item.get("status") == "failed"
                ],
            }
    if last_active_id:
        checkpoint.active_node_id = last_active_id
        checkpoint.active_inputs = last_active_inputs
        checkpoint.queue = [last_active_id]
        checkpoint.pending_inputs[last_active_id] = dict(last_active_inputs)
        checkpoint.run_state = "stopped_resumable"
    checkpoint.saved_at = datetime.now(UTC).isoformat()
    return checkpoint


def create_diagnostic_snapshot(
    workspace: Path,
    *,
    project_path: Path | None,
    run_directory: Path,
    extra_paths: list[Path] | None = None,
) -> Path:
    """Record hashes and provenance without copying or deleting user artifacts."""
    root = workspace.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = root / ".flowai" / "runtime" / "diagnostics" / stamp / "manifest.json"
    candidates = [
        run_directory / RUN_FILE,
        run_directory / CHECKPOINT_FILE,
        *(extra_paths or []),
    ]
    if project_path is not None:
        candidates.insert(0, project_path)
    files: list[dict[str, Any]] = []
    for path in candidates:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            continue
        digest = sha256()
        try:
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            continue
        files.append(
            {
                "path": str(resolved),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest.hexdigest().upper(),
            }
        )
    atomic_write_json(
        target,
        {
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": str(root),
            "project_path": str(project_path.resolve()) if project_path else "",
            "run_directory": str(run_directory.resolve()),
            "files": files,
        },
    )
    return target


def find_pending_run(project_path: Path) -> Path | None:
    """Знайти найновіший запуск проєкту, що чекає на користувача."""
    runs = project_path.resolve().parent / "runs"
    if not runs.is_dir():
        return None
    wanted = str(project_path.resolve())
    for directory in sorted(
        runs.iterdir(), key=lambda item: item.name, reverse=True
    ):
        if not directory.is_dir():
            continue
        try:
            payload = json.loads(
                (directory / CHECKPOINT_FILE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if str(payload.get("project_path", "")) == wanted:
            return directory
    return None


def load_runs(directory: Path, limit: int = MAX_RUNS) -> list[list[dict[str, Any]]]:
    """Прочитати збережені запуски, найновіші першими."""
    if not directory.is_dir():
        return []
    files = sorted(
        directory.glob(f"*/{RUN_FILE}"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    runs: list[list[dict[str, Any]]] = []
    for path in files[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            LOGGER.warning("Не вдалося прочитати журнал запуску %s", path)
            continue
        events = payload.get("events")
        if isinstance(events, list):
            runs.append([item for item in events if isinstance(item, dict)])
    return runs
