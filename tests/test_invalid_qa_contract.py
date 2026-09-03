from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from flowai import codex_adapter
from flowai.engine import RunCheckpoint, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.quality_control import QAContractError, normalize_task_review
from flowai.run_history import load_checkpoint, save_checkpoint
from flowai.ui import main_window as ui


def conflicting_review() -> dict:
    return {
        "verdict": True,
        "score": 97,
        "must_fix": [],
        "issues": [{
            "category": "missing_requirement",
            "severity": "warning",
            "description": "Required evidence is missing",
        }],
    }


def test_qa_retry_resumes_checkpoint_and_keeps_blocking_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(
        codex_adapter, "FAKE_RESPONDER", lambda _call: json.dumps(conflicting_review())
    )
    entry = FlowNode.create("entry")
    entry.config["text"] = "Existing research result"
    qa = FlowNode.create("task_reviewer")
    qa.config.update(pass_threshold=85, qa_correction_attempts=1)
    edge = FlowEdge.create(entry.id, qa.id)
    edge.source_path = "text"
    edge.target_variable = "work"
    flow = Workflow(workspace=str(tmp_path), nodes=[entry, qa], edges=[edge])
    run_dir = tmp_path / "run"
    runner = WorkflowRunner(flow, run_directory=run_dir)
    runner.run()
    request = runner.outputs[qa.id].data["request"]
    assert request["type"] == "invalid_qa_contract"
    assert len(codex_adapter.FAKE_CALLS) == 2
    assert "missing_requirement" in codex_adapter.FAKE_CALLS[0]["developer_instructions"]
    assert "severity=warning" in codex_adapter.FAKE_CALLS[1]["prompt"]
    assert runner.checkpoint.queue == [qa.id]
    with pytest.raises(QAContractError, match="blocking issues"):
        normalize_task_review(conflicting_review(), pass_threshold=85)

    save_checkpoint(run_dir, runner.checkpoint, project_path=None, request=request)
    restored = load_checkpoint(run_dir)
    assert restored is not None
    checkpoint, _ = restored
    completed_entry = dict(checkpoint.outputs[entry.id])
    corrected = conflicting_review()
    corrected.update(verdict=False, must_fix=["Add the required evidence"])
    monkeypatch.setattr(
        codex_adapter, "FAKE_RESPONDER", lambda _call: json.dumps(corrected)
    )
    resumed = WorkflowRunner(
        flow, checkpoint=checkpoint, run_directory=run_dir,
        intervention_responses={qa.id: {"action": "retry_task", "note": "Check source support"}},
    )
    resumed.run()
    assert resumed.outputs[qa.id].status == "success"
    assert resumed.outputs[qa.id].data["verdict"] is False
    assert resumed.checkpoint.outputs[entry.id] == completed_entry
    assert resumed.checkpoint.iterations[entry.id] == 1
    assert "Check source support" in codex_adapter.FAKE_CALLS[-1]["prompt"]
    assert "Existing research result" in codex_adapter.FAKE_CALLS[-1]["prompt"]
    assert len(codex_adapter.FAKE_CALLS) == 3


def test_qa_dialog_explains_error_and_passes_it_to_retry() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = ui.RetryAttentionDialog({
        "type": "invalid_qa_contract", "node_title": "Research QA",
        "errors": ["verdict=true не може містити blocking issues"],
        "invalid_response": json.dumps(conflicting_review()),
    })
    assert "blocking issues" in dialog.details.toPlainText()
    assert "missing_requirement" in dialog.details.toPlainText()
    dialog.note.setPlainText("Перевір джерело")
    dialog._retry()
    assert dialog.response["action"] == "retry_task"
    assert "blocking issues" in dialog.response["note"]
    assert "Перевір джерело" in dialog.response["note"]
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize("action", ["dismiss", "retry_task", "stop"])
def test_saved_invalid_qa_request_is_actionable_without_losing_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = ui.MainWindow(
        check_account_on_start=False, restore_workspaces=False,
        settings=QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat),
    )
    window.account_refresh_timer.stop()
    try:
        window.new_workflow()
        session = window.current_workspace
        assert session is not None
        qa = next(n for n in window.scene.workflow.nodes if n.kind == "task_reviewer")
        request = {
            "type": "invalid_qa_contract", "node_id": qa.id, "node_title": qa.title,
            "errors": ["blocking issues"], "invalid_response": "invalid",
        }
        checkpoint = RunCheckpoint(started=True, queue=[qa.id])
        session.checkpoint = checkpoint
        session.run_directory = tmp_path / "run"
        session.pending_intervention = request
        session.run_state = "paused"
        opened = []
        resumed = []

        class FakeDialog:
            def __init__(self, received, parent):
                opened.append(received)
                self.response = {"action": action, "note": "Retry guidance"}

            def exec(self):
                return (QDialog.DialogCode.Accepted if action == "retry_task"
                        else QDialog.DialogCode.Rejected)

        monkeypatch.setattr(ui, "RetryAttentionDialog", FakeDialog)
        monkeypatch.setattr(window, "run_workflow", lambda *, resume: resumed.append(resume))
        window._show_pending_intervention(user_initiated=True)
        assert opened == [request]
        assert session.checkpoint is checkpoint
        assert checkpoint.queue == [qa.id]
        if action == "dismiss":
            assert session.run_state == "paused"
            assert session.pending_intervention is request
            assert load_checkpoint(session.run_directory)[1] == request
            window._show_pending_intervention(user_initiated=True)
            assert len(opened) == 2
        elif action == "retry_task":
            assert resumed == [True]
            assert session.intervention_responses[qa.id]["action"] == "retry_task"
            assert session.pending_intervention is None
        else:
            assert session.run_state == "stopped"
            assert session.pending_intervention is None
            assert load_checkpoint(session.run_directory)[1] == {}
    finally:
        session = window.current_workspace
        if session is not None:
            session.dirty = False
        window.dirty = False
        window.close()
        app.processEvents()
