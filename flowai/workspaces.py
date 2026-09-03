from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import Workflow


@dataclass(slots=True)
class WorkspaceSession:
    display_name: str
    project_path: Path | None = None
    workflow: Workflow | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    load_state: str = "unloaded"
    run_state: str = "idle"
    unread_result: bool = False
    dirty: bool = False
    custom_name: bool = False
    run_events: list[dict[str, Any]] = field(default_factory=list)
    node_statuses: dict[str, str] = field(default_factory=dict)
    node_durations: dict[str, float] = field(default_factory=dict)
    node_duration_history: dict[str, list[float]] = field(default_factory=dict)
    node_started_at: dict[str, float] = field(default_factory=dict)
    node_stages: dict[str, tuple[int, int, str]] = field(default_factory=dict)
    task_states: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    port_counts: dict[str, int] = field(default_factory=dict)
    # (нода, порт) останньої розсилки — щоб підсвітити саме ту лінію, що спрацювала.
    last_dispatch: tuple[str, str] = ("", "")
    checkpoint: Any = None
    run_directory: Any = None
    intervention_responses: dict[str, Any] = field(default_factory=dict)
    pending_intervention: dict[str, Any] | None = None
    canvas_transform: Any = None
    horizontal_scroll: int = 0
    vertical_scroll: int = 0
    selected_object: tuple[str, str] | None = None
    undo_history: list[dict[str, Any]] = field(default_factory=list)
    redo_history: list[dict[str, Any]] = field(default_factory=list)
    history_state: dict[str, Any] | None = None
    saved_history_state: dict[str, Any] | None = None
    run_thread: Any = None
    run_worker: Any = None
    file_watcher: Any = None
    log_entries: list[dict[str, Any]] = field(default_factory=list)
    generated_file_groups: list[dict[str, Any]] = field(default_factory=list)
    active_file_node_id: str = ""
    active_file_iteration: int = 0
    stop_requested: bool = False
    # Resumable STOP already requested; the active turn is being interrupted
    # and its node/inputs will be returned to the checkpoint queue.
    stop_pending: bool = False

    @property
    def is_loaded(self) -> bool:
        return self.load_state == "loaded" and self.workflow is not None

    @property
    def path_text(self) -> str:
        return str(self.project_path) if self.project_path else "Незбережений Flow"

    @property
    def status_text(self) -> str:
        if self.stop_requested:
            return "Зупиняється"
        # Питання до користувача важливіше за обіцянку майбутньої зупинки:
        # поки на нього не дадуть відповіді, Flow не зрушить із місця.
        if self.pending_intervention is not None and self.run_state in {
            "paused",
            "needs_attention",
        }:
            return "Пауза — очікує на вашу відповідь"
        if self.stop_pending:
            return "Перериває операцію зі збереженням"
        if self.run_state == "stopped":
            return "Зупинено — можна продовжити"
        if self.run_state == "running":
            return "Виконується"
        if self.run_state == "failed":
            return "Помилка виконання"
        if self.run_state == "paused":
            return "Призупинено"
        if self.unread_result:
            return "Є новий результат"
        if not self.is_loaded:
            if self.project_path is not None and not self.project_path.exists():
                return "Файл не знайдено"
            return "Не завантажено"
        if self.dirty:
            return "Є незбережені зміни"
        if self.run_state == "completed_with_failures":
            return "Виконано з провалами"
        if self.run_state == "completed":
            return "Виконано"
        if self.run_state == "cancelled":
            return "Зупинено"
        return "Завантажено"

    def registry_entry(self) -> dict[str, Any] | None:
        if self.project_path is None:
            return None
        return {
            "id": self.id,
            "path": str(self.project_path),
            "display_name": self.display_name,
            "custom_name": self.custom_name,
        }
