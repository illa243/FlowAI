from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.models import FlowNode, Workflow
from flowai.persistence import save_workflow
from flowai.ui import flow_composer_dialog as composer_module
from flowai.ui.flow_composer_dialog import ComposerWorker, FlowComposerDialog


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_composer_defaults_to_grill_enabled(tmp_path: Path) -> None:
    application()
    dialog = FlowComposerDialog()
    assert dialog.grill.isChecked() is True
    assert dialog.model.count() > 0
    assert [dialog.reasoning.itemData(index) for index in range(dialog.reasoning.count())] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert dialog.reasoning.currentData() == "medium"
    attachment = tmp_path / "reference.png"
    attachment.write_bytes(b"image")
    dialog.attachments.add_paths([str(attachment)])
    assert dialog.attachments.paths() == [str(attachment)]
    dialog.deleteLater()


def test_edit_flow_dialog_targets_existing_flow_and_disables_grill_by_default(
    tmp_path: Path,
) -> None:
    application()
    target = tmp_path / "selected.flowai.json"
    save_workflow(Workflow(name="Selected", nodes=[FlowNode.create("entry")]), target)

    dialog = FlowComposerDialog(
        edit_path=target,
        initial_workspace=tmp_path,
    )

    assert dialog.edit_mode is True
    assert dialog.edit_path == target.resolve()
    assert dialog.windowTitle() == "Edit Flow — AI"
    assert dialog.grill.isChecked() is False
    assert dialog.reasoning.currentData() == "medium"
    assert dialog.model.count() > 0
    assert dialog.compose_button.text() == "Змінити Flow"
    dialog.deleteLater()


def test_composer_worker_forwards_selected_reasoning_effort(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict] = []

    class CodexStub:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def run_agent(self, **kwargs):
            calls.append(kwargs)

        def cancel_active(self) -> bool:
            return True

    monkeypatch.setattr(composer_module, "CodexAdapter", CodexStub)
    target = tmp_path / "composed.flowai.json"
    workflow = Workflow(name="Generated", nodes=[FlowNode.create("entry")])
    save_workflow(workflow, target)
    document = tmp_path / "brief.md"
    document.write_text("# Brief", encoding="utf-8")
    picture = tmp_path / "reference.png"
    picture.write_bytes(b"image")
    worker = ComposerWorker(
        "Створи Flow",
        "gpt-5.6-sol",
        tmp_path,
        target,
        reasoning_effort="xhigh",
        attachments=[document, picture],
    )

    worker.run()

    assert len(calls) == 1
    assert calls[0]["reasoning_effort"] == "xhigh"
    assert calls[0]["attachments"] == [document, picture]
    assert str(document) in calls[0]["prompt"]
    assert str(picture) in calls[0]["prompt"]


def test_edit_worker_loads_and_changes_the_exact_selected_flow(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict] = []
    target = tmp_path / "selected.flowai.json"
    workflow = Workflow(name="Before", nodes=[FlowNode.create("entry")])
    save_workflow(workflow, target)

    class CodexStub:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def run_agent(self, **kwargs):
            calls.append(kwargs)
            edited = Workflow.from_dict(workflow.to_dict())
            edited.name = "After"
            save_workflow(edited, target)

        def cancel_active(self) -> bool:
            return True

    monkeypatch.setattr(composer_module, "CodexAdapter", CodexStub)
    completed: list[str] = []
    worker = ComposerWorker(
        "Перейменуй Flow",
        "gpt-5.6-terra",
        tmp_path,
        target,
        reasoning_effort="high",
        edit_existing=True,
    )
    worker.completed.connect(completed.append)

    worker.run()

    assert completed == [str(target)]
    assert calls[0]["reasoning_effort"] == "high"
    assert "load_flow" in calls[0]["developer_instructions"]
    assert "не створюй новий Flow" in calls[0]["developer_instructions"]
    assert str(target) in calls[0]["prompt"]
