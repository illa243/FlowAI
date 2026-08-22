from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLOWAI_FAKE_CODEX", "1")

from PySide6.QtWidgets import QApplication

from flowai.calibration import CalibrationReport, RejectionPoint
from flowai.grill import (
    MATERIALS_OPTIONS,
    MATERIALS_QUESTION,
    GrillOutcome,
    GrillQuestion,
)
from flowai.models import FlowNode, Workflow
from flowai.ui.grill_dialog import GrillDialog


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _workflow() -> Workflow:
    workflow = Workflow(name="Тест")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Стара задача", "attachments": []}
    ]
    workflow.nodes = [manager]
    return workflow


def test_question_options_are_vertical_buttons(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(_workflow(), "gpt-5.6-terra", tmp_path)
    dialog.show_question(
        GrillQuestion(
            text="Який ринок?",
            options=["Мобільні ігри", "Веб", "Своя відповідь"],
            rationale="Потрібна конкретика",
        )
    )
    assert [button.text() for button in dialog.option_buttons] == [
        "Мобільні ігри",
        "Веб",
        "Своя відповідь",
    ]
    dialog.deleteLater()


def test_ready_page_shows_diff(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(_workflow(), "gpt-5.6-terra", tmp_path)
    dialog.show_outcome(
        GrillOutcome(
            summary="Ринок: мобільні ігри",
            rewritten_tasks={"t1": "Нова задача"},
        )
    )
    assert "Стара задача" in dialog.diff_text()
    assert "Нова задача" in dialog.diff_text()
    dialog.deleteLater()


def make_calibration() -> CalibrationReport:
    return CalibrationReport(
        node_id="stop-1",
        node_title="Calibration Stop",
        task_id="task-1",
        task_title="Зробити карту",
        workflow_name="Карти",
        attempt=1,
        threshold=1,
        points=[RejectionPoint(title="Сітка з'їхала")],
    )


def test_dialog_passes_calibration_to_the_worker(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(
        _workflow(),
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
        generated_files=["C:/out/map.png"],
    )
    dialog._start_worker()
    assert dialog._worker is not None
    assert dialog._worker.calibration is not None
    assert dialog._worker.generated_files == ["C:/out/map.png"]
    dialog._stop_thread()
    if dialog._thread is not None:
        dialog._thread.wait(3000)
    dialog.close()


def test_dialog_titles_itself_as_a_regeneration(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(
        _workflow(),
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    assert "Regenerate" in dialog.windowTitle()
    dialog.close()


def test_dialog_keeps_the_plain_title_without_calibration(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(_workflow(), "gpt-5.6-terra", tmp_path)
    assert dialog.windowTitle() == "GrillMe"
    dialog.close()


def test_materials_question_renders_three_buttons(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(
        _workflow(),
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    dialog.show_question(
        GrillQuestion(text=MATERIALS_QUESTION, options=list(MATERIALS_OPTIONS))
    )
    assert len(dialog.option_buttons) == 3
    assert dialog.question_text.text() == MATERIALS_QUESTION
    dialog.close()
