"""Що бачить і що отримує користувач, коли тисне STOP."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from flowai.engine import RunCheckpoint
from flowai.run_history import CHECKPOINT_FILE
from flowai.ui.main_window import MainWindow


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeWorker:
    def __init__(self) -> None:
        self.soft = 0
        self.hard = 0

    def request_stop(self) -> None:
        self.soft += 1

    def cancel(self) -> None:
        self.hard += 1


def _window_with_running_flow(tmp_path: Path) -> tuple[MainWindow, Any, FakeWorker]:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    session.project_path = tmp_path / "flow.flowai.json"
    # Розкладка як у справжньому проєкті: find_pending_run шукає саме в runs/.
    session.run_directory = tmp_path / "runs" / "20260825-120000-000000"
    session.run_directory.mkdir(parents=True, exist_ok=True)
    session.checkpoint = RunCheckpoint(started=True)
    session.run_state = "running"
    worker = FakeWorker()
    session.run_worker = worker
    return window, session, worker


def _close(window: MainWindow, session: Any) -> None:
    session.dirty = False
    window.dirty = False
    session.run_worker = None
    session.run_state = "idle"
    session.stop_requested = False
    session.stop_pending = False
    window.close()


def test_stop_interrupts_the_turn_and_keeps_a_resumable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, worker = _window_with_running_flow(tmp_path)
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: shown.append(str(args[2]) if len(args) > 2 else ""),
    )

    window.stop_workflow()

    assert worker.soft == 1, "STOP має просити переривання зі збереженням"
    assert worker.hard == 0, "перший STOP не є аварійним завершенням процесу"
    assert session.stop_pending is True
    assert shown and "checkpoint" in shown[0].casefold()
    _close(window, session)


def test_stop_lifts_the_pause_so_the_flow_can_actually_reach_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STOP на паузі знімає бар'єр, тож стан «paused» більше не відповідає рушію."""

    window, session, worker = _window_with_running_flow(tmp_path)
    session.run_state = "paused"
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window.stop_workflow()

    assert worker.soft == 1
    assert session.run_state != "paused", "кнопка не має лишатись у стані «Resume»"
    assert session.stop_pending is True
    _close(window, session)


def test_a_pending_question_outranks_the_stop_label(tmp_path: Path) -> None:
    """Питання до користувача важливіше за напис про майбутню зупинку."""

    window, session, _ = _window_with_running_flow(tmp_path)
    session.stop_pending = True
    session.run_state = "paused"
    session.pending_intervention = {"node_id": "n1", "question": "Що робимо?"}

    assert "відповідь" in session.status_text.casefold()
    session.pending_intervention = None
    _close(window, session)


def test_a_second_stop_offers_the_hard_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нода-довгожитель інакше була б незупинною."""

    window, session, worker = _window_with_running_flow(tmp_path)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )

    window.stop_workflow()
    window.stop_workflow()

    assert worker.soft == 1
    assert worker.hard == 1
    assert session.stop_requested is True
    _close(window, session)


def test_a_stopped_run_keeps_its_checkpoint_on_disk(tmp_path: Path) -> None:
    """Саме цим зупинка відрізняється від скасування."""

    window, session, _ = _window_with_running_flow(tmp_path)
    session.run_events = [{"type": "run_stopped", "message": "Flow зупинено"}]
    session.stop_pending = True

    window._run_completed(session.id, session.checkpoint)

    assert (session.run_directory / CHECKPOINT_FILE).is_file()
    assert session.run_state == "stopped"
    _close(window, session)


def test_a_cancelled_run_still_clears_its_checkpoint(tmp_path: Path) -> None:
    window, session, _ = _window_with_running_flow(tmp_path)
    session.run_events = [{"type": "run_cancelled", "message": "Flow зупинено"}]

    window._run_completed(session.id, session.checkpoint)

    assert not (session.run_directory / CHECKPOINT_FILE).exists()
    assert session.run_state == "cancelled"
    _close(window, session)


def test_restarting_the_app_finds_the_stopped_run(tmp_path: Path) -> None:
    """Друга половина обіцянки: продовжити після перезаходу в програму."""

    window, session, _ = _window_with_running_flow(tmp_path)
    session.run_events = [{"type": "run_stopped", "message": "Flow зупинено"}]
    window._run_completed(session.id, session.checkpoint)

    fresh = window.workspace_sessions[0]
    fresh.checkpoint = None
    fresh.run_directory = None
    fresh.run_state = "idle"
    window._restore_pending_run(fresh)

    assert fresh.checkpoint is not None
    assert fresh.run_state == "stopped", "це не пауза — питання до користувача немає"
    assert fresh.pending_intervention is None
    _close(window, session)


def test_run_asks_before_discarding_a_stopped_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, _ = _window_with_running_flow(tmp_path)
    session.run_worker = None
    session.run_state = "stopped"
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    directory = session.run_directory

    window.run_workflow()

    assert session.run_thread is None, "скасування не має нічого запускати"
    assert session.checkpoint is not None, "і не має стирати збережений прогрес"
    assert session.run_directory == directory
    _close(window, session)


def test_a_stopped_flow_does_not_block_closing_the_app(tmp_path: Path) -> None:
    """Сенс усього: вийти з програми й повернутися пізніше."""

    window, session, _ = _window_with_running_flow(tmp_path)
    session.run_worker = None
    session.run_state = "stopped"
    session.stop_requested = False
    session.stop_pending = False

    blocked = [
        item.display_name
        for item in window.workspace_sessions
        if item.run_thread
        or (
            item.run_state in {"running", "paused"}
            and not window._is_attention_paused(item)
        )
        or item.stop_requested
    ]

    assert blocked == []
    _close(window, session)
