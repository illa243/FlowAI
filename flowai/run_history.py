from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    target.write_text(
        json.dumps(
            {
                "project_path": str(project_path.resolve()) if project_path else "",
                "request": request,
                "checkpoint": checkpoint.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
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
