from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QPointF,
    QSettings,
    Qt,
    QThread,
    QTimer,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QKeyEvent, QKeySequence, QPixmap, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QSizePolicy

from flowai import codex_adapter
from flowai.models import NODE_COLORS, FlowEdge, FlowNode, Workflow
from flowai.persistence import load_workflow, save_workflow
from flowai.ui import main_window as main_window_module
from flowai.ui.canvas import FlowScene, FlowView
from flowai.ui.inspector import (
    ExpandablePlainTextEdit,
    FullScreenTextEditorDialog,
    Inspector,
    ManagedTasksWidget,
)
from flowai.ui.main_window import (
    GeneratedFilesDialog,
    MainWindow,
    ResultConfirmationDialog,
    ResultLimitDialog,
    RunWorker,
    WorkflowSettingsDialog,
)
from flowai.ui.results_dialog import ResultsDialog
from flowai.ui.stats_dialog import StatsDialog
from flowai.ui.workspace_sidebar import (
    ResponsiveListWidget,
    WorkspaceCard,
    WorkspaceSidebar,
)
from flowai.workspaces import WorkspaceSession


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


class ThreadAwareMainWindow(MainWindow):
    def __init__(self) -> None:
        self.run_callbacks_on_main_thread: list[bool] = []
        super().__init__(check_account_on_start=False, restore_workspaces=False)

    @Slot(str, object)
    def _handle_run_event(self, session_id: str, event: object) -> None:
        self.run_callbacks_on_main_thread.append(QThread.isMainThread())
        super()._handle_run_event(session_id, event)


def schema_aware_responder(call: dict) -> str:
    """Фейковий агент, який відповідає за схемою, вказаною в промпті."""
    prompt = call["prompt"]
    if '"verdict"' in prompt:
        return json.dumps(
            {"verdict": True, "score": 95, "reason": "Готово", "must_fix": []},
            ensure_ascii=False,
        )
    if '"improved_prompt"' in prompt:
        return json.dumps(
            {"improved_prompt": "Уточнена задача", "notes": []}, ensure_ascii=False
        )
    return "Готовий результат"


class MouseEventStub:
    def __init__(self, x: float, y: float) -> None:
        self._position = QPointF(x, y)
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.RightButton

    def position(self) -> QPointF:
        return self._position

    def accept(self) -> None:
        self.accepted = True


class PortMouseEventStub:
    def __init__(self, position: QPointF) -> None:
        self._position = position
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def scenePos(self) -> QPointF:
        return self._position

    def accept(self) -> None:
        self.accepted = True


class NodeMouseEventStub(PortMouseEventStub):
    def __init__(self, local_position: QPointF, scene_position: QPointF) -> None:
        super().__init__(scene_position)
        self._local_position = local_position

    def pos(self) -> QPointF:
        return self._local_position


def test_right_mouse_drag_pans_canvas() -> None:
    app = application()
    scene = FlowScene()
    view = FlowView(scene)
    view.resize(400, 300)
    view.show()
    app.processEvents()
    horizontal = view.horizontalScrollBar()
    vertical = view.verticalScrollBar()
    horizontal.setValue((horizontal.minimum() + horizontal.maximum()) // 2)
    vertical.setValue((vertical.minimum() + vertical.maximum()) // 2)
    start_horizontal = horizontal.value()
    start_vertical = vertical.value()

    press = MouseEventStub(100, 100)
    move = MouseEventStub(140, 125)
    release = MouseEventStub(140, 125)
    view.mousePressEvent(press)
    view.mouseMoveEvent(move)
    view.mouseReleaseEvent(release)

    assert horizontal.value() == start_horizontal - 40
    assert vertical.value() == start_vertical - 25
    assert press.accepted and move.accepted and release.accepted
    assert view._panning is False
    view.close()


def test_f2_requests_rename_for_selected_node_and_workspace() -> None:
    application()
    node = FlowNode.create("entry")
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[node]))
    view = FlowView(scene)
    scene.node_items[node.id].setSelected(True)
    renamed_nodes: list[FlowNode] = []
    view.rename_requested.connect(renamed_nodes.append)
    node_event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_F2,
        Qt.KeyboardModifier.NoModifier,
    )
    view.keyPressEvent(node_event)
    assert renamed_nodes == [node]

    session = WorkspaceSession("Проєкт")
    sidebar = WorkspaceSidebar()
    sidebar.set_sessions([session], session.id)
    renamed_sessions: list[str] = []
    sidebar.list_widget.rename_requested.connect(renamed_sessions.append)
    workspace_event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_F2,
        Qt.KeyboardModifier.NoModifier,
    )
    sidebar.list_widget.keyPressEvent(workspace_event)
    assert renamed_sessions == [session.id]
    sidebar.spinner_timer.stop()


def test_workspace_sidebar_reuses_cards_for_state_updates() -> None:
    application()
    session = WorkspaceSession("Проєкт")
    sidebar = WorkspaceSidebar()
    sidebar.set_sessions([session], session.id)
    card = sidebar._cards[session.id]

    session.run_state = "running"
    sidebar.set_sessions([session], session.id)

    assert sidebar._cards[session.id] is card
    assert card.status_text.text() == "Виконується"
    sidebar.spinner_timer.stop()
    sidebar.deleteLater()


def test_reentrant_sidebar_refresh_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application()
    session = WorkspaceSession("Проєкт")
    sidebar = WorkspaceSidebar()
    original_sync = sidebar._sync_sessions
    sync_calls: list[bool] = []

    def reentrant_sync(
        sessions: list[WorkspaceSession], selected_id: str | None
    ) -> None:
        sync_calls.append(True)
        if len(sync_calls) == 1:
            sidebar.set_sessions(sessions, selected_id)
        original_sync(sessions, selected_id)

    monkeypatch.setattr(sidebar, "_sync_sessions", reentrant_sync)
    sidebar.set_sessions([session], session.id)

    assert sync_calls == [True]
    QApplication.processEvents()
    assert sync_calls == [True, True]
    sidebar.spinner_timer.stop()
    sidebar.deleteLater()


def test_run_worker_throttles_activity_bursts_but_keeps_completion() -> None:
    application()
    worker = RunWorker("session", Workflow(name="Тест"), None)
    messages: list[tuple[str, str, object]] = []
    worker.message.connect(
        lambda session_id, message_type, payload: messages.append(
            (session_id, message_type, payload)
        )
    )

    for index in range(100):
        worker._forward_event(
            {
                "type": "agent_activity",
                "node_id": "node",
                "message": f"step {index}",
            }
        )
    worker._forward_event(
        {
            "type": "agent_activity",
            "node_id": "node",
            "phase": "completed",
            "message": "done",
        }
    )

    assert len(messages) == 2
    assert messages[-1][2]["phase"] == "completed"


def test_agent_activity_does_not_rebuild_workspace_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    sidebar_refreshes: list[bool] = []
    action_refreshes: list[bool] = []
    monkeypatch.setattr(
        window, "_refresh_workspace_sidebar", lambda: sidebar_refreshes.append(True)
    )
    monkeypatch.setattr(
        window, "_update_workspace_actions", lambda: action_refreshes.append(True)
    )

    for index in range(100):
        window._handle_run_event(
            session.id,
            {
                "type": "agent_activity",
                "node_id": "node",
                "message": f"step {index}",
            },
        )

    assert sidebar_refreshes == []
    assert action_refreshes == []
    session.dirty = False
    window.dirty = False
    window.close()


def test_inspector_shows_pinned_skills_for_an_agent_node() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    node = FlowNode.create("executor")
    node.config["skills"] = [{"name": "birds-map", "path": "C:/s/birds-map"}]
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    assert window.inspector.skill_pins.skills() == [
        {"name": "birds-map", "path": "C:/s/birds-map"}
    ]
    window.close()


def test_inspector_saves_a_newly_pinned_skill() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    node = FlowNode.create("executor")
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    window.inspector.skill_pins.add_skill("image-cutout", "C:/s/image-cutout")
    assert node.config["skills"] == [
        {"name": "image-cutout", "path": "C:/s/image-cutout"}
    ]
    window.close()


def test_inspector_does_not_pin_the_same_skill_twice() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    node = FlowNode.create("executor")
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    window.inspector.skill_pins.add_skill("image-cutout", "C:/s/image-cutout")
    window.inspector.skill_pins.add_skill("image-cutout", "C:/s/image-cutout")
    assert len(node.config["skills"]) == 1
    window.close()


def test_result_node_has_no_skill_field() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    node = FlowNode.create("result")
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    assert window.inspector.skill_pins.isVisible() is False
    window.close()


def test_calibrator_node_has_no_output_ports() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    workflow = Workflow()
    node = FlowNode.create("calibrator")
    workflow.nodes.append(node)
    window.scene.set_workflow(workflow)
    item = window.scene.node_items[node.id]
    assert item.output_ports == {}
    assert item.input_port is not None
    window.close()


def test_inspector_shows_the_calibration_threshold() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    node = FlowNode.create("calibrator")
    node.config["false_threshold"] = 3
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    assert window.inspector.false_threshold.value() == 3
    window.inspector.false_threshold.setValue(2)
    assert node.config["false_threshold"] == 2
    window.close()


def test_inspector_warns_when_exhausted_becomes_unreachable() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    workflow = Workflow()
    result = FlowNode.create("result")
    result.config["task_attempt_limit"] = 3
    stop = FlowNode.create("calibrator")
    stop.config["false_threshold"] = 1
    workflow.nodes.extend([result, stop])
    workflow.edges.append(FlowEdge.create(result.id, stop.id, "false"))
    window.inspector.set_workflow(workflow)
    window.inspector.set_object(stop)
    assert "EXHAUSTED" in window.inspector.threshold_hint.text()
    assert window.inspector.threshold_hint.isVisible() is True
    window.close()


def test_inspector_hides_the_warning_when_exhausted_can_fire() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    workflow = Workflow()
    result = FlowNode.create("result")
    result.config["task_attempt_limit"] = 2
    stop = FlowNode.create("calibrator")
    stop.config["false_threshold"] = 5
    workflow.nodes.extend([result, stop])
    workflow.edges.append(FlowEdge.create(result.id, stop.id, "false"))
    window.inspector.set_workflow(workflow)
    window.inspector.set_object(stop)
    assert window.inspector.threshold_hint.isVisible() is False
    window.close()


def test_responsive_lists_follow_available_aspect_ratio() -> None:
    app = application()
    responsive = ResponsiveListWidget()
    responsive.resize(520, 120)
    responsive.show()
    app.processEvents()
    assert responsive.horizontal_layout is True
    assert responsive.flow() == ResponsiveListWidget.Flow.LeftToRight

    responsive.resize(180, 520)
    app.processEvents()
    assert responsive.horizontal_layout is False
    assert responsive.flow() == ResponsiveListWidget.Flow.TopToBottom
    responsive.close()


def test_dock_layers_are_compact_and_keep_only_controls() -> None:
    application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )

    assert window.workspace_dock.windowTitle() == ""
    assert window.nodes_dock.windowTitle() == ""
    assert window.inspector_dock.windowTitle() == "Parameters"
    assert window.log_dock.windowTitle() == ""
    assert window.workspace_sidebar.summary.isHidden()
    visible_texts = {
        label.text() for label in window.findChildren(QLabel) if not label.isHidden()
    }
    assert "Робочі середовища" not in visible_texts
    assert "Додати ноду" not in visible_texts
    assert "Властивості" not in visible_texts
    assert "З'єднання" not in visible_texts
    assert len(window.node_buttons) == 8
    assert any("Tasks Manager" in button.text() for button in window.node_buttons)
    assert {button.button_color for button in window.node_buttons} == set(
        NODE_COLORS.values()
    )
    window.close()


def test_main_toolbar_contains_settings_run_stop_files_and_account() -> None:
    application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )

    toolbar_actions = window.main_toolbar.actions()
    assert (
        window.main_toolbar.toolButtonStyle()
        == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )
    assert window.new_action not in toolbar_actions
    assert window.open_action not in toolbar_actions
    assert window.save_action not in toolbar_actions
    assert window.settings_action not in toolbar_actions
    assert window.edit_flow_action not in toolbar_actions
    assert window.run_action not in toolbar_actions
    assert window.stop_action not in toolbar_actions
    assert window.files_action not in toolbar_actions
    assert window.settings_action.text() == "Settings"
    assert window.settings_action.icon().isNull() is False
    assert window.run_action.text() == "▶ Run"
    assert window.stop_action.text() == "■ Stop"
    assert window.files_action.text() == "Files"
    assert window.settings_button.defaultAction() is window.settings_action
    assert window.settings_button.text() == "Settings"
    assert window.settings_button.minimumWidth() >= 90
    assert window.edit_flow_button.defaultAction() is window.edit_flow_action
    assert window.edit_flow_button.text() == "Edit Flow"
    assert window.edit_flow_button.objectName() == "editFlowButton"
    assert window.edit_flow_action.icon().isNull() is False
    assert window.run_button.defaultAction() is window.run_action
    assert window.stop_button.defaultAction() is window.stop_action
    assert window.files_button.defaultAction() is window.files_action
    assert window.run_button.objectName() == "runButton"
    assert window.stop_button.objectName() == "stopButton"
    assert window.files_button.objectName() == "filesButton"
    window.close()


def test_ai_edited_flow_replaces_selected_flow_and_keeps_undo_state() -> None:
    application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.new_workflow()
    session = window.current_workspace
    assert session is not None and session.workflow is not None
    original = session.workflow.to_dict()
    edited = Workflow.from_dict(original)
    edited.name = "AI edited"

    window._apply_ai_edited_workflow(session, edited)

    assert window.scene.workflow.name == "AI edited"
    assert session.saved_history_state == edited.to_dict()
    assert session.dirty is False
    assert session.undo_history[-1] == original
    assert window.edit_flow_action.isEnabled() is True
    window.close()


def test_edit_flow_ai_passes_the_selected_flow_path_and_reloads_it(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.new_workflow()
    session = window.current_workspace
    assert session is not None and session.workflow is not None
    target = save_workflow(session.workflow, tmp_path / "selected.flowai.json")
    session.project_path = target
    window.project_path = target
    opened: list[Path] = []

    class EditDialogStub:
        def __init__(self, _parent, *, edit_path: Path, initial_workspace: Path):
            opened.append(edit_path)
            assert initial_workspace == tmp_path.resolve()
            self.saved_path = str(edit_path)

        def exec(self):
            edited = load_workflow(target)
            edited.name = "Changed by selected AI edit"
            save_workflow(edited, target)
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window_module, "FlowComposerDialog", EditDialogStub)
    monkeypatch.setattr(window, "save_workflow", lambda: True)

    window.edit_flow_with_ai()

    assert opened == [target.resolve()]
    assert window.scene.workflow.name == "Changed by selected AI edit"
    window.close()


def test_run_and_stop_actions_follow_the_selected_workspace() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    first = window.current_workspace
    assert first is not None
    first.run_thread = object()
    first.run_state = "running"
    window._update_workspace_actions()
    assert window.run_action.isEnabled() is False
    assert window.stop_action.isEnabled() is True
    assert window.files_action.isEnabled() is True

    window.new_workflow()
    second = window.current_workspace
    assert second is not None
    assert window.run_action.isEnabled() is True
    assert window.stop_action.isEnabled() is False

    window.select_workspace(first.id)
    assert window.run_action.isEnabled() is False
    assert window.stop_action.isEnabled() is True
    first.run_thread = None
    first.run_state = "idle"
    for session in window.workspace_sessions:
        session.dirty = False
    window.dirty = False
    window.close()


def test_live_result_limits_are_forwarded_to_the_running_worker() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    result = next(node for node in window.scene.workflow.nodes if node.kind == "result")

    class WorkerStub:
        def __init__(self) -> None:
            self.updates: list[tuple[str, dict]] = []

        def update_result_config(self, node_id: str, updates: dict) -> bool:
            self.updates.append((node_id, updates))
            return True

    worker = WorkerStub()
    session.run_worker = worker
    session.run_thread = object()
    session.run_state = "running"
    window.inspector.set_object(result)
    window._update_workspace_actions()
    window.inspector.false_limit.setValue(8)
    window.inspector.wait_for_confirmation.setChecked(True)

    assert worker.updates
    assert worker.updates[-1][0] == result.id
    assert worker.updates[-1][1]["false_limit"] == 8
    assert worker.updates[-1][1]["wait_for_confirmation"] is True
    session.run_worker = None
    session.run_thread = None
    session.run_state = "idle"
    session.dirty = False
    window.dirty = False
    window.close()


def test_system_lock_pauses_and_unlock_resumes_running_projects() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None

    class WorkerStub:
        def __init__(self) -> None:
            self.pauses: list[str] = []
            self.resumes: list[str] = []

        def pause(self, reason: str) -> None:
            self.pauses.append(reason)

        def resume(self, reason: str) -> None:
            self.resumes.append(reason)

    worker = WorkerStub()
    session.run_worker = worker
    session.run_thread = object()
    session.run_state = "running"
    waiting_worker = WorkerStub()
    waiting = WorkspaceSession(
        display_name="Waiting",
        workflow=Workflow(),
        load_state="loaded",
        run_state="needs_attention",
    )
    waiting.run_worker = waiting_worker
    window.workspace_sessions.append(waiting)

    window._set_system_pause_reason("lock", True)
    assert session.run_state == "paused"
    assert len(worker.pauses) == 1
    assert waiting.run_state == "needs_attention"
    assert waiting_worker.pauses == []
    window._set_system_pause_reason("lock", False)
    assert session.run_state == "running"
    assert len(worker.resumes) == 1
    assert waiting.run_state == "needs_attention"
    assert waiting_worker.resumes == []

    session.run_worker = None
    session.run_thread = None
    session.run_state = "idle"
    waiting.run_worker = None
    waiting.run_state = "idle"
    waiting.dirty = False
    session.dirty = False
    window.dirty = False
    window.close()


def test_close_is_blocked_while_any_project_is_running(monkeypatch) -> None:
    application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    session.run_thread = object()
    session.run_state = "running"
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message, *args: warnings.append((title, message)),
    )

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert warnings and session.display_name in warnings[0][1]
    session.run_thread = None
    session.run_state = "idle"
    session.dirty = False
    window.dirty = False
    window.close()


def test_workspace_and_node_docks_can_expand_horizontally() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)

    assert window.isDockNestingEnabled() is True
    for dock in (window.workspace_dock, window.nodes_dock):
        assert dock.maximumWidth() > 10_000
        assert dock.minimumWidth() == 96
        assert dock.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        assert dock.allowedAreas() == Qt.DockWidgetArea.AllDockWidgetAreas

    node_text = " ".join(
        label.text() for label in window.nodes_dock.findChildren(QLabel)
    )
    assert "Натисніть порт" not in node_text
    assert "Delete —" not in node_text
    window.close()


def test_top_dock_can_be_resized_horizontally_with_its_handle() -> None:
    app = application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.resize(1200, 800)
    window.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, window.nodes_dock)
    window.show()
    app.processEvents()

    dock = window.nodes_dock
    assert dock.resize_handle.isVisibleTo(dock)
    dock.set_docked_width(320)
    app.processEvents()
    assert dock.width() == 320
    assert dock.minimumWidth() < 320

    dock.set_docked_width(500)
    app.processEvents()
    assert dock.width() == 500

    dock.reset_docked_width()
    app.processEvents()
    assert dock.width() == window.width()
    window.close()


def test_floating_dock_has_native_maximize_and_close_buttons() -> None:
    app = application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.show()
    window.nodes_dock.setFloating(True)
    app.processEvents()

    flags = window.nodes_dock.windowFlags()
    assert flags & Qt.WindowType.WindowMaximizeButtonHint
    assert flags & Qt.WindowType.WindowCloseButtonHint
    window.nodes_dock.showMaximized()
    app.processEvents()
    assert window.nodes_dock.isMaximized()
    window.nodes_dock.setFloating(False)
    window.close()


def test_ctrl_z_and_ctrl_y_restore_current_workflow() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    initial_count = len(window.scene.workflow.nodes)

    assert window.undo_action.shortcut() == QKeySequence("Ctrl+Z")
    assert window.redo_action.shortcut() == QKeySequence("Ctrl+Y")

    window.add_node("executor")
    added_node_id = window.scene.workflow.nodes[-1].id
    assert len(window.scene.workflow.nodes) == initial_count + 1

    window.undo()
    assert len(window.scene.workflow.nodes) == initial_count
    assert added_node_id not in window.scene.node_items

    window.redo()
    assert len(window.scene.workflow.nodes) == initial_count + 1
    assert added_node_id in window.scene.node_items

    for workspace in window.workspace_sessions:
        workspace.dirty = False
    window.dirty = False
    window.close()


def test_inspector_configures_md_instructions_and_prompt_transfer() -> None:
    application()
    inspector = Inspector()
    agent = FlowNode.create("executor")
    inspector.set_object(agent)
    inspector.instruction_files.addItem("C:/instructions/agent.md")
    inspector.node_folders.addItem("C:/projects/shared")
    inspector.prompt_source.setCurrentIndex(inspector.prompt_source.findData("input"))
    inspector._save_node()

    assert agent.config["instruction_files"] == ["C:/instructions/agent.md"]
    assert agent.config["additional_folders"] == ["C:/projects/shared"]
    assert agent.config["prompt_source"] == "input"
    assert inspector.prompt_edit.isEnabled() is False

    edge = FlowEdge.create("source", "target")
    inspector.set_object(edge)
    inspector._set_prompt_transfer()
    assert edge.source_path == "text"
    assert edge.target_variable == "prompt"
    assert edge.transform == ""
    inspector.close()


def test_workflow_settings_manage_project_folders() -> None:
    application()
    workflow = Workflow(
        workspace="C:/projects/main",
        additional_folders=["C:/projects/shared", "C:/projects/assets"],
    )
    dialog = WorkflowSettingsDialog(workflow)

    assert dialog.workspace_edit.text() == "C:/projects/main"
    assert dialog.additional_folders() == [
        "C:/projects/shared",
        "C:/projects/assets",
    ]

    dialog.additional_folders_list.item(0).setSelected(True)
    dialog._remove_additional_folders()
    assert dialog.additional_folders() == ["C:/projects/assets"]
    dialog.workspace_edit.clear()
    assert dialog.workspace_edit.text() == ""
    dialog.close()


def test_workspace_card_separates_selection_and_run_status() -> None:
    application()
    session = WorkspaceSession("Фоновий Flow")
    unloaded = WorkspaceCard(session, selected=False)
    assert unloaded.rail.property("state") == "idle"

    session.workflow = Workflow(name="Фоновий Flow")
    session.load_state = "loaded"
    session.run_state = "running"
    background = WorkspaceCard(session, selected=False)
    assert background.rail.property("state") == "loaded"
    assert background.status_icon.text() in {"◐", "◓", "◑", "◒"}

    selected = WorkspaceCard(session, selected=True)
    assert selected.property("selected") is True
    assert selected.rail.property("state") == "selected"
    assert selected.status_icon.toolTip() == "Виконується"


def test_switching_workspaces_preserves_each_flow_state() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    assert window.current_workspace is None

    window.new_workflow()
    first = window.current_workspace
    assert first is not None
    first.workflow.name = "Перший"
    first.display_name = "Перший"
    window._mark_dirty()
    window._append_session_log(first, "Журнал першого")

    window.new_workflow()
    second = window.current_workspace
    assert second is not None and second.id != first.id
    window.select_workspace(first.id)

    assert window.current_workspace is first
    assert window.scene.workflow.name == "Перший"
    assert "Журнал першого" in window.log_panel.view.toPlainText()
    assert window.workspace_sidebar.list_widget.count() == 2

    window._set_workspace_name(first, "Перейменований проєкт")
    assert first.display_name == "Перейменований проєкт"
    assert first.workflow.name == "Перейменований проєкт"
    assert first.custom_name is True

    first.dirty = False
    second.dirty = False
    window.dirty = False
    window.close()


def test_background_flow_sets_unread_result(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", schema_aware_responder)
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    first = window.current_workspace
    assert first is not None
    first.project_path = tmp_path / "first.flowai.json"
    window.project_path = first.project_path

    window.run_workflow()
    assert first.run_thread is not None
    loop = QEventLoop()
    first.run_thread.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)

    window.new_workflow()
    second = window.current_workspace
    loop.exec()

    assert first.run_thread is None
    assert first.run_state == "completed"
    assert first.unread_result is True
    assert window.current_workspace is second

    shown: list[str] = []
    monkeypatch.setattr(
        MainWindow, "show_run_results", lambda self: shown.append("shown")
    )
    window.select_workspace(first.id)
    QApplication.processEvents()
    assert first.unread_result is False
    assert shown == ["shown"]
    for session in window.workspace_sessions:
        session.dirty = False
    window.dirty = False
    window.close()


def test_background_flow_delivers_ui_events_on_the_main_thread(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", schema_aware_responder)
    application()
    window = ThreadAwareMainWindow()
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    session.project_path = tmp_path / "thread-safe.flowai.json"
    window.project_path = session.project_path

    window.run_workflow()
    assert session.run_thread is not None
    loop = QEventLoop()
    session.run_thread.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()

    assert session.run_thread is None
    assert window.run_callbacks_on_main_thread
    assert all(window.run_callbacks_on_main_thread)
    session.dirty = False
    window.dirty = False
    window.close()


def test_agent_prompt_event_keeps_large_text_out_of_the_ui_log() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None

    window._handle_run_event(
        session.id,
        {
            "type": "agent_prompt",
            "node_title": "Executor",
            "instructions": "secret-instructions" * 10_000,
            "prompt": "large-prompt" * 10_000,
            "attachments": ["one.png", "two.png"],
        },
    )

    visible = window.log_panel.view.toPlainText()
    assert "промпт сформовано; вкладень: 2" in visible
    assert "secret-instructions" not in visible
    assert "large-prompt" not in visible
    session.dirty = False
    window.dirty = False
    window.close()


def test_result_node_exposes_true_false_and_exhausted_ports() -> None:
    application()
    result = FlowNode.create("result")
    watcher = FlowNode.create("work_reviewer")
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[result, watcher]))

    item = scene.node_items[result.id]
    assert set(item.output_ports) == {"true", "false", "exhausted"}
    assert item.output_ports["true"].label.startswith("TRUE")
    assert item.output_ports["false"].label.startswith("FALSE")
    assert item.output_ports["exhausted"].label.startswith("EXHAUSTED")

    # Блок-спостерігач не бере участі в маршруті.
    sidecar = scene.node_items[watcher.id]
    assert sidecar.output_ports == {}
    assert sidecar.input_port is None


def test_connecting_from_the_false_port_records_it_on_the_edge() -> None:
    application()
    result = FlowNode.create("result")
    executor = FlowNode.create("executor")
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[result, executor]))

    scene.port_clicked(scene.node_items[result.id].output_ports["false"])
    scene.port_clicked(scene.node_items[executor.id].input_port)

    assert len(scene.workflow.edges) == 1
    assert scene.workflow.edges[0].source_port == "false"


def test_double_click_adds_a_persistent_draggable_edge_control_point() -> None:
    application()
    source = FlowNode.create("executor", 0, 0)
    target = FlowNode.create("executor", 520, 0)
    edge = FlowEdge.create(source.id, target.id)
    workflow = Workflow(nodes=[source, target], edges=[edge])
    scene = FlowScene()
    scene.set_workflow(workflow)
    edge_item = scene.edge_items[edge.id]
    original_middle = edge_item.path().pointAtPercent(0.5)
    event = PortMouseEventStub(original_middle)

    edge_item.mouseDoubleClickEvent(event)

    assert event.accepted is True
    assert len(edge.control_points) == 1
    assert len(edge_item.control_point_items) == 1
    point = edge_item.control_point_items[0]
    point.setPos(point.pos() + QPointF(0, 140))
    assert edge.control_points[0]["y"] == round(point.pos().y(), 2)
    assert edge_item.path().boundingRect().bottom() >= point.pos().y()

    restored = Workflow.from_dict(workflow.to_dict())
    assert restored.edges[0].control_points == edge.control_points

    point.setSelected(True)
    scene.delete_selection()
    assert edge.control_points == []
    assert edge.id in scene.edge_items


def test_node_can_be_resized_in_both_axes_and_dimensions_are_persisted() -> None:
    application()
    source = FlowNode.create("executor", 0, 0)
    target = FlowNode.create("executor", 520, 0)
    edge = FlowEdge.create(source.id, target.id)
    workflow = Workflow(nodes=[source, target], edges=[edge])
    scene = FlowScene()
    scene.set_workflow(workflow)
    item = scene.node_items[source.id]
    changes: list[bool] = []
    scene.model_changed.connect(lambda: changes.append(True))

    assert item._resize_mode_at(QPointF(220, 60)) == "width"
    assert item._resize_mode_at(QPointF(100, 130)) == "height"
    press = NodeMouseEventStub(QPointF(220, 130), QPointF(220, 130))
    move = NodeMouseEventStub(QPointF(360, 240), QPointF(360, 240))
    release = NodeMouseEventStub(QPointF(360, 240), QPointF(360, 240))
    item.mousePressEvent(press)
    item.mouseMoveEvent(move)
    item.mouseReleaseEvent(release)

    assert press.accepted is True
    assert move.accepted is True
    assert release.accepted is True
    assert item.boundingRect().size().width() == 360
    assert item.boundingRect().size().height() == 240
    assert source.width == 360
    assert source.height == 240
    assert changes == [True]
    output = item.output_port_item("out")
    assert output is not None
    assert output.pos() == QPointF(360, 120)
    assert scene.edge_items[edge.id].path().pointAtPercent(0) == output.scenePos()

    restored = Workflow.from_dict(workflow.to_dict())
    restored_source = restored.node(source.id)
    assert restored_source.width == 360
    assert restored_source.height == 240


def test_node_selection_and_move_updates_flush_after_interaction() -> None:
    application()
    node = FlowNode.create("tasks_manager", 0, 0)
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[node]))
    item = scene.node_items[node.id]
    selections: list[object] = []
    changes: list[bool] = []
    scene.selection_object_changed.connect(selections.append)
    scene.model_changed.connect(lambda: changes.append(True))

    scene.begin_node_interaction(item)
    item.setSelected(True)
    item.setPos(10, 10)
    item.setPos(20, 20)

    assert selections == []
    assert changes == []
    scene.end_node_interaction(item)
    assert selections == [node]
    assert changes == [True]
    assert (node.x, node.y) == (20.0, 20.0)


def test_dragging_multi_selection_flushes_once_after_interaction() -> None:
    """Qt рухає всі вибрані ноди, але зміна моделі має бути однією на жест.

    Інакше кожна невибрана під курсором нода емітить model_changed на кожну
    подію руху миші, а це тягне за собою перебудову сайдбару й заголовка.
    """
    application()
    dragged = FlowNode.create("executor", 0, 0)
    companion = FlowNode.create("task_reviewer", 400, 0)
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[dragged, companion]))
    dragged_item = scene.node_items[dragged.id]
    companion_item = scene.node_items[companion.id]
    changes: list[bool] = []
    scene.model_changed.connect(lambda: changes.append(True))

    dragged_item.setSelected(True)
    companion_item.setSelected(True)
    scene.begin_node_interaction(dragged_item)
    for step in range(1, 11):
        offset = step * 5
        dragged_item.setPos(offset, offset)
        companion_item.setPos(400 + offset, offset)

    assert changes == [], "Під час жесту модель не має емітити зміни"
    scene.end_node_interaction(dragged_item)
    assert changes == [True]
    assert (dragged.x, dragged.y) == (50.0, 50.0)
    assert (companion.x, companion.y) == (450.0, 50.0)


def test_drag_disables_node_shadow_and_restores_it() -> None:
    """Тінь — розмиття на кожен кадр, тож на час перетягування вона вимикається."""
    application()
    dragged = FlowNode.create("executor", 0, 0)
    idle = FlowNode.create("task_reviewer", 400, 0)
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[dragged, idle]))
    dragged_item = scene.node_items[dragged.id]
    idle_item = scene.node_items[idle.id]
    assert dragged_item.graphicsEffect().isEnabled() is True

    scene.begin_node_interaction(dragged_item)
    assert dragged_item.graphicsEffect().isEnabled() is False
    assert idle_item.graphicsEffect().isEnabled() is True

    scene.end_node_interaction(dragged_item)
    assert dragged_item.graphicsEffect().isEnabled() is True


def test_drag_disables_shadows_of_the_whole_selection() -> None:
    application()
    dragged = FlowNode.create("executor", 0, 0)
    companion = FlowNode.create("task_reviewer", 400, 0)
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[dragged, companion]))
    dragged_item = scene.node_items[dragged.id]
    companion_item = scene.node_items[companion.id]
    dragged_item.setSelected(True)
    companion_item.setSelected(True)

    scene.begin_node_interaction(dragged_item)
    assert companion_item.graphicsEffect().isEnabled() is False
    scene.end_node_interaction(dragged_item)
    assert companion_item.graphicsEffect().isEnabled() is True


def test_task_editors_are_reused_instead_of_rebuilt() -> None:
    """Перестворення редакторів завдань блокувало UI-потік на десятки мс."""
    application()
    widget = ManagedTasksWidget()
    widget.set_tasks(
        [
            {"id": "t1", "prompt": "Перше", "attachments": []},
            {"id": "t2", "prompt": "Друге", "attachments": []},
        ]
    )
    first, second = widget.sections

    widget.set_tasks(
        [
            {"id": "t1", "prompt": "Оновлене", "attachments": []},
            {"id": "t2", "prompt": "Друге", "attachments": []},
        ]
    )
    assert widget.sections[0] is first
    assert widget.sections[1] is second
    assert widget.sections[0].prompt.toPlainText() == "Оновлене"
    assert [item["id"] for item in widget.tasks()] == ["t1", "t2"]

    widget.set_tasks([{"id": "t1", "prompt": "Одне", "attachments": []}])
    assert widget.sections == [first]

    widget.set_tasks(
        [
            {"id": "t1", "prompt": "Одне", "attachments": []},
            {"id": "t3", "prompt": "Третє", "attachments": []},
        ]
    )
    assert widget.sections[0] is first
    assert len(widget.sections) == 2
    assert widget.tasks()[1]["id"] == "t3"
    widget.deleteLater()


def test_reselecting_tasks_manager_keeps_its_editors() -> None:
    application()
    node = FlowNode.create("tasks_manager")
    node.config["tasks"] = [
        {"id": f"t{index}", "prompt": f"Завдання {index}", "attachments": []}
        for index in range(8)
    ]
    inspector = Inspector()
    inspector.set_workflow(Workflow(nodes=[node]))
    inspector.set_object(node)
    editors = list(inspector.tasks_editor.sections)
    assert len(editors) == 8

    inspector.set_object(node)
    assert inspector.tasks_editor.sections == editors
    inspector.deleteLater()


def test_generated_files_dialog_is_dark_and_not_striped() -> None:
    application()
    session = WorkspaceSession(display_name="Тест")
    session.generated_file_groups = [
        {
            "node_id": "abc123",
            "node_title": "Task Executor",
            "iteration": 1,
            "color": "#7C3AED",
            "intermediate": [],
            "result": [],
        }
    ]
    dialog = GeneratedFilesDialog(session)
    assert dialog.tree.alternatingRowColors() is False
    heading = dialog.tree.topLevelItem(0)
    assert heading.foreground(0).color().name() == "#7c3aed"
    child = heading.child(0).child(0)
    assert child.foreground(0).color().name() == "#e5e7eb"
    dialog.deleteLater()


def test_tasks_node_keeps_seconds_in_states() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Черга")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Перше", "attachments": []}
    ]
    workflow.nodes = [manager]
    scene.set_workflow(workflow)
    scene.set_task_states(
        manager.id,
        [{"id": "t1", "title": "Перше", "status": "completed", "seconds": 12.5}],
    )
    item = scene.node_items[manager.id]
    assert item.task_states[0]["seconds"] == 12.5


def test_result_node_has_three_ports_and_attempt_field() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Тест")
    result = FlowNode.create("result")
    workflow.nodes = [result]
    scene.set_workflow(workflow)
    item = scene.node_items[result.id]
    assert set(item.output_ports) == {"true", "false", "exhausted"}

    inspector = Inspector()
    inspector.set_workflow(workflow)
    inspector.set_object(result)
    assert inspector.task_attempt_limit.value() == 2


def test_run_with_failed_tasks_reports_partial_success() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    session.task_states = {
        "n1": [
            {"id": "t1", "title": "Перше", "status": "completed", "seconds": 1.0},
            {"id": "t2", "title": "Друге", "status": "failed", "seconds": 2.0},
        ]
    }
    window._run_completed(session.id, None)
    assert session.run_state == "completed_with_failures"
    session.dirty = False
    assert session.status_text == "Виконано з провалами"
    window.dirty = False
    window.close()


def test_stats_dialog_lists_nodes_with_time_and_tokens() -> None:
    application()
    session = WorkspaceSession(display_name="Тест")
    session.run_events = [
        {
            "type": "node_finished",
            "node_id": "n1",
            "node_title": "Task Executor",
            "result": {
                "duration_seconds": 4.0,
                "data": {"usage": {"total_tokens": 200, "context_window": 1000}},
            },
        }
    ]
    dialog = StatsDialog(session)
    heading = dialog.tree.topLevelItem(0)
    assert heading.text(0).startswith("Task Executor")
    assert heading.text(1) == "1"
    assert "4.0" in heading.text(2)
    assert "200" in heading.text(4)
    dialog.deleteLater()


def test_results_dialog_shows_final_files_only() -> None:
    application()
    session = WorkspaceSession(display_name="Тест")
    session.generated_file_groups = [
        {
            "node_id": "n1",
            "node_title": "Task Executor",
            "iteration": 1,
            "color": "#7C3AED",
            "intermediate": ["C:/tmp/step.png"],
            "result": ["C:/tmp/final.md"],
        }
    ]
    dialog = ResultsDialog(session)
    assert dialog.result_paths() == ["C:/tmp/final.md"]
    dialog.deleteLater()


def test_switching_to_finished_workspace_requests_results(monkeypatch) -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    session.run_state = "completed"
    session.unread_result = True
    shown: list[str] = []
    monkeypatch.setattr(
        MainWindow, "show_run_results", lambda self: shown.append("shown")
    )
    window.select_workspace(session.id)
    QApplication.processEvents()
    assert shown == ["shown"]
    session.dirty = False
    window.dirty = False
    window.close()


def test_dragging_an_output_shows_preview_and_connects_to_input() -> None:
    application()
    source = FlowNode.create("executor", 0, 0)
    target = FlowNode.create("executor", 420, 100)
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[source, target]))
    output = next(iter(scene.node_items[source.id].output_ports.values()))
    input_port = scene.node_items[target.id].input_port
    assert input_port is not None

    press = PortMouseEventStub(output.scenePos())
    move = PortMouseEventStub(input_port.scenePos())
    release = PortMouseEventStub(input_port.scenePos())
    output.mousePressEvent(press)
    assert scene.connection_preview is not None
    output.mouseMoveEvent(move)
    assert scene.connection_preview is not None
    assert scene.connection_preview.path().pointAtPercent(1.0) == input_port.scenePos()
    assert input_port._connection_target is True
    output.mouseMoveEvent(PortMouseEventStub(QPointF(700, 700)))
    assert input_port._connection_target is False
    output.mouseMoveEvent(move)
    assert input_port._connection_target is True
    output.mouseReleaseEvent(release)

    assert press.accepted and move.accepted and release.accepted
    assert scene.connection_preview is None
    assert input_port._connection_target is False
    assert len(scene.workflow.edges) == 1
    assert scene.workflow.edges[0].source == source.id
    assert scene.workflow.edges[0].target == target.id


def test_dropping_connection_away_from_an_input_cancels_preview() -> None:
    application()
    source = FlowNode.create("executor", 0, 0)
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[source]))
    output = next(iter(scene.node_items[source.id].output_ports.values()))

    output.mousePressEvent(PortMouseEventStub(output.scenePos()))
    output.mouseMoveEvent(PortMouseEventStub(QPointF(700, 700)))
    output.mouseReleaseEvent(PortMouseEventStub(QPointF(700, 700)))

    assert scene.connection_preview is None
    assert scene.workflow.edges == []


def test_port_labels_follow_the_run_counters() -> None:
    application()
    result = FlowNode.create("result")
    result.config["false_limit"] = 3
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[result]))

    scene.apply_port_counts({f"{result.id}:false": 2, f"{result.id}:true": 0})
    assert scene.node_items[result.id].output_ports["false"].label == "FALSE 2/3"


def test_attention_pulsing_starts_and_stops() -> None:
    application()
    result = FlowNode.create("result")
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[result]))

    scene.set_attention(result.id, True)
    assert scene.node_items[result.id].attention is True
    assert scene._running_timer.isActive() is True

    scene.set_attention(result.id, False)
    assert scene._running_timer.isActive() is False


def test_node_displays_live_and_completed_runtime() -> None:
    application()
    node = FlowNode.create("executor")
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[node]))
    item = scene.node_items[node.id]

    scene.set_node_runtime(node.id, 1.5, time.monotonic() - 2.0)
    scene.set_node_status(node.id, "running")

    assert scene._running_timer.isActive() is True
    assert item.elapsed_seconds() >= 3.4
    assert item.formatted_duration() != "—"
    assert item._running_pulse(0.4) > item._running_pulse(0.0)

    scene.set_node_runtime(node.id, 4.25)
    scene.set_node_status(node.id, "success")
    assert scene._running_timer.isActive() is False
    assert item.formatted_duration() == "4.2 с"

    scene.reset_statuses()
    assert item.formatted_duration() == "—"


def test_active_node_shows_reasoning_stage_and_separate_times() -> None:
    application()
    node = FlowNode.create("executor")
    node.config["model"] = "gpt-5.6-sol"
    node.config["reasoning_effort"] = "high"
    scene = FlowScene()
    scene.set_workflow(Workflow(nodes=[node]))
    item = scene.node_items[node.id]

    scene.set_node_status(node.id, "running")
    scene.set_node_stage(node.id, 3, 6, "Підключення до моделі")
    scene.set_node_runtime(node.id, 0.0, time.monotonic(), [2.5])

    assert "Міркування: high" in item._subtitle()
    assert (item.stage_current, item.stage_total) == (3, 6)
    assert item.stage_name == "Підключення до моделі"
    assert item.duration_history == [2.5]
    assert len(item._time_lines()) == 2


def test_main_window_tracks_each_node_runtime_separately() -> None:
    application()
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    node = window.scene.workflow.nodes[0]

    window._handle_run_event(
        session.id,
        {"type": "node_started", "node_id": node.id, "node_title": node.title},
    )
    assert node.id in session.node_started_at
    assert window.scene.node_items[node.id].status == "running"

    window._handle_run_event(
        session.id,
        {
            "type": "node_finished",
            "node_id": node.id,
            "node_title": node.title,
            "result": {"duration_seconds": 2.25, "text": "ok"},
        },
    )
    window._handle_run_event(
        session.id,
        {"type": "node_started", "node_id": node.id, "node_title": node.title},
    )
    window._handle_run_event(
        session.id,
        {
            "type": "node_finished",
            "node_id": node.id,
            "node_title": node.title,
            "result": {"duration_seconds": 1.25, "text": "ok"},
        },
    )

    assert session.node_durations[node.id] == 1.25
    assert session.node_duration_history[node.id] == [2.25, 1.25]
    assert node.id not in session.node_started_at
    assert window.scene.node_items[node.id].formatted_duration() == "1.2 с"

    session.dirty = False
    window.dirty = False
    window.close()


def test_node_console_messages_use_node_color_and_underline_files(
    tmp_path: Path,
) -> None:
    application()
    output = tmp_path / "generated.png"
    output.write_bytes(b"result")
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None
    node = window.scene.workflow.nodes[0]

    window._handle_run_event(
        session.id,
        {
            "type": "node_finished",
            "node_id": node.id,
            "node_title": node.title,
            "result": {
                "duration_seconds": 1.0,
                "text": "created",
                "data": {"output_path": str(output)},
            },
        },
    )

    plain = window.log_panel.view.toPlainText()
    assert f"{node.short_id}: готово" in plain
    assert "flowai-file:" in window.log_panel.view.toHtml()

    id_offset = plain.index(node.short_id)
    color_cursor = QTextCursor(window.log_panel.view.document())
    color_cursor.setPosition(id_offset)
    assert color_cursor.charFormat().foreground().color().name().upper() == "#3B82F6"
    session.dirty = False
    window.dirty = False
    window.close()


def test_files_dialog_groups_node_files_in_generation_order(tmp_path: Path) -> None:
    application()
    source = tmp_path / "source.png"
    source.write_bytes(b"input")
    intermediate = tmp_path / "crop-01.png"
    intermediate.write_bytes(b"crop")
    result = tmp_path / "final.png"
    result.write_bytes(b"result")
    qa_preview = tmp_path / "qa-preview.png"
    qa_preview.write_bytes(b"qa")

    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None and session.workflow is not None
    node = session.workflow.nodes_of_kind("executor")[0]

    window._handle_run_event(
        session.id,
        {
            "type": "agent_prompt",
            "node_id": node.id,
            "node_title": node.title,
            "attachments": [str(source)],
            "message": "prompt",
        },
    )
    window._handle_run_event(
        session.id,
        {
            "type": "node_finished",
            "node_id": node.id,
            "node_title": node.title,
            "iteration": 2,
            "result": {
                "duration_seconds": 1.0,
                "text": "created",
                "data": {
                    "_generated_files": [
                        str(source),
                        str(intermediate),
                        str(result),
                    ],
                    "candidate_path": str(result),
                    "artifact": {"path": str(result)},
                },
            },
        },
    )

    assert session.generated_file_groups == [
        {
            "node_id": node.id,
            "node_title": node.title,
            "iteration": 2,
            "color": NODE_COLORS[node.kind],
            "intermediate": [str(intermediate.resolve())],
            "result": [str(result.resolve())],
        }
    ]

    reviewer = session.workflow.nodes_of_kind("task_reviewer")[0]
    window._handle_run_event(
        session.id,
        {
            "type": "node_finished",
            "node_id": reviewer.id,
            "node_title": reviewer.title,
            "iteration": 1,
            "result": {
                "duration_seconds": 0.5,
                "text": "checked",
                "data": {"_generated_files": [str(qa_preview)]},
            },
        },
    )

    dialog = GeneratedFilesDialog(session)
    assert dialog.tree.topLevelItemCount() == 2
    heading = dialog.tree.topLevelItem(0)
    assert heading is not None
    assert node.title in heading.text(0)
    assert node.id in heading.text(0)
    assert "прохід 2" in heading.text(0)
    assert heading.child(0).text(0) == "Проміжні файли"
    assert heading.child(0).child(0).text(1) == str(intermediate.resolve())
    assert heading.child(1).text(0) == "Результат"
    assert heading.child(1).child(0).text(1) == str(result.resolve())
    assert heading.child(1).child(0).foreground(0).color().name() == "#e5e7eb"
    second_heading = dialog.tree.topLevelItem(1)
    assert second_heading is not None
    assert reviewer.title in second_heading.text(0)
    assert second_heading.child(0).text(0) == "Проміжні файли"
    assert second_heading.child(1).text(0) == "Результат"
    assert second_heading.child(1).child(0).text(0) == "Фінальний файл не вказано"
    assert (
        second_heading.foreground(0).color().name().upper()
        == NODE_COLORS[reviewer.kind]
    )
    dialog.close()
    session.dirty = False
    window.dirty = False
    window.close()


def test_first_watched_file_creates_active_node_group(tmp_path: Path) -> None:
    application()
    generated = tmp_path / "script-output.png"
    generated.write_bytes(b"image")
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    window.new_workflow()
    session = window.current_workspace
    assert session is not None and session.workflow is not None
    executor = session.workflow.nodes_of_kind("executor")[0]
    session.active_file_node_id = executor.id
    session.active_file_iteration = 1

    window._intermediate_file(session.id, str(generated))

    assert len(session.generated_file_groups) == 1
    assert session.generated_file_groups[0]["node_id"] == executor.id
    assert session.generated_file_groups[0]["intermediate"] == [str(generated)]
    session.dirty = False
    window.dirty = False
    window.close()


def test_result_limit_dialog_builds_an_add_attempts_response() -> None:
    application()
    dialog = ResultLimitDialog(
        {
            "node_title": "Result",
            "port": "false",
            "used": 3,
            "limit": 3,
            "reason": "Немає тестів",
        }
    )
    dialog.attempts.setValue(4)
    dialog.note.setPlainText("Спершу напиши тести")
    dialog._accept()

    assert dialog.response == {
        "action": "add_attempts",
        "count": 4,
        "note": "Спершу напиши тести",
    }
    dialog.close()


def test_result_limit_dialog_can_force_the_other_branch() -> None:
    application()
    dialog = ResultLimitDialog(
        {"node_title": "Result", "port": "false", "used": 3, "limit": 3}
    )
    dialog.force.setChecked(True)
    assert dialog.attempts.isEnabled() is False
    dialog._accept()

    assert dialog.response is not None
    assert dialog.response["action"] == "force_branch"
    assert dialog.response["branch"] == "true"
    dialog.close()


def test_result_confirmation_dialog_returns_continue() -> None:
    application()
    dialog = ResultConfirmationDialog(
        {"node_title": "Result", "type": "result_confirmation", "files": []}
    )
    dialog._accept()
    assert dialog.response == {"action": "continue"}
    dialog.close()


def test_inspector_shows_only_the_fields_of_the_selected_kind() -> None:
    application()
    inspector = Inspector()
    workflow = Workflow()
    entry = FlowNode.create("entry")
    reviewer = FlowNode.create("task_reviewer")
    result = FlowNode.create("result")
    workflow.nodes = [entry, reviewer, result]
    inspector.set_workflow(workflow)

    inspector.set_object(entry)
    assert inspector.node_rows["entry_text"][1].isVisibleTo(inspector.node_page)
    assert not inspector.node_rows["true_limit"][1].isVisibleTo(inspector.node_page)

    inspector.set_object(result)
    assert inspector.node_rows["true_limit"][1].isVisibleTo(inspector.node_page)
    assert not inspector.node_rows["model"][1].isVisibleTo(inspector.node_page)

    inspector.set_object(reviewer)
    assert inspector.node_rows["criteria_node"][1].isVisibleTo(inspector.node_page)
    # Випадайка еталона містить усі інші блоки плюс «Авто».
    assert inspector.criteria_combo.count() == 3
    inspector.close()


def test_tasks_manager_parameters_add_edit_files_and_remove(tmp_path: Path) -> None:
    application()
    attachment = tmp_path / "brief.md"
    attachment.write_text("brief", encoding="utf-8")
    node = FlowNode.create("tasks_manager")
    node.config["tasks"][0]["prompt"] = "Перше завдання"
    inspector = Inspector()
    inspector.set_workflow(Workflow(nodes=[node]))
    inspector.set_object(node)

    assert inspector.node_rows["tasks"][1].isVisibleTo(inspector.node_page)
    assert len(inspector.tasks_editor.sections) == 1
    assert inspector.tasks_editor.sections[0].heading.text().endswith("Перше завдання")

    inspector.tasks_editor.add_button.click()
    assert len(node.config["tasks"]) == 2
    second = inspector.tasks_editor.sections[1]
    second.prompt.setPlainText("Друге завдання")
    second.attachments.add_paths([str(attachment)])
    assert node.config["tasks"][1]["prompt"] == "Друге завдання"
    assert node.config["tasks"][1]["attachments"] == [str(attachment)]

    second.remove_button.click()
    assert len(inspector.tasks_editor.sections) == 1
    assert len(node.config["tasks"]) == 1
    assert inspector.tasks_editor.sections[0].remove_button.isEnabled() is False
    inspector.close()


def test_tasks_manager_canvas_has_next_done_ports_and_task_states() -> None:
    application()
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "one", "prompt": "Перше", "attachments": []},
        {"id": "two", "prompt": "Друге", "attachments": []},
        {"id": "three", "prompt": "Третє", "attachments": []},
    ]
    result = FlowNode.create("result")
    back = FlowEdge.create(result.id, manager.id, "true")
    workflow = Workflow(nodes=[manager, result], edges=[back])
    scene = FlowScene()
    scene.set_workflow(workflow)

    item = scene.node_items[manager.id]
    assert set(item.output_ports) == {"next", "done"}
    assert item.output_ports["next"].label == "NEXT"
    assert item.output_ports["done"].label == "DONE"
    assert item.boundingRect().height() > 130
    result_item = scene.node_items[result.id]
    assert result_item.output_ports["true"].label == "TRUE 0/3"

    scene.set_task_states(
        manager.id,
        [
            {"id": "one", "title": "Перше", "status": "completed"},
            {"id": "two", "title": "Друге", "status": "running"},
            {"id": "three", "title": "Третє", "status": "pending"},
        ],
    )
    assert item.has_active_task() is True
    assert item.task_states[0]["status"] == "completed"
    assert scene._running_timer.isActive() is True

    scene.apply_port_counts({f"{result.id}:true": 1})
    assert result_item.output_ports["true"].label == "TRUE 1/3"
    result.config["false_limit"] = 5
    scene.refresh_item(result)
    assert result_item.output_ports["true"].label == "TRUE 1/3"
    assert result_item.output_ports["false"].label == "FALSE 0/5"


def test_parameters_title_and_model_field_open_the_model_list() -> None:
    app = application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.new_workflow()
    node = next(node for node in window.scene.workflow.nodes if node.kind == "executor")
    window.inspector.set_object(node)
    window.show()
    app.processEvents()

    assert window.inspector_dock.windowTitle() == "Parameters"
    popup_requests: list[str] = []
    window.inspector.model_combo.showPopup = lambda: popup_requests.append("popup")
    QTest.mouseClick(
        window.inspector.model_label,
        Qt.MouseButton.LeftButton,
        pos=window.inspector.model_label.rect().center(),
    )
    app.processEvents()
    assert popup_requests == ["popup"]

    line_edit = window.inspector.model_combo.lineEdit()
    assert line_edit is not None
    QTest.mouseClick(
        line_edit,
        Qt.MouseButton.LeftButton,
        pos=line_edit.rect().center(),
    )
    app.processEvents()
    assert popup_requests == ["popup", "popup"]
    for session in window.workspace_sessions:
        session.dirty = False
    window.dirty = False
    window.close()


def test_inspector_writes_result_limits_and_reviewer_reference() -> None:
    application()
    inspector = Inspector()
    workflow = Workflow()
    entry = FlowNode.create("entry")
    reviewer = FlowNode.create("task_reviewer")
    result = FlowNode.create("result")
    workflow.nodes = [entry, reviewer, result]
    inspector.set_workflow(workflow)

    inspector.set_object(result)
    inspector.true_limit.setValue(2)
    inspector.false_limit.setValue(5)
    inspector.wait_for_confirmation.setChecked(True)
    assert result.config["true_limit"] == 2
    assert result.config["false_limit"] == 5
    assert result.config["wait_for_confirmation"] is True

    inspector.set_object(reviewer)
    index = inspector.criteria_combo.findData(entry.id)
    inspector.criteria_combo.setCurrentIndex(index)
    assert reviewer.config["criteria_node"] == entry.id
    inspector.close()


def test_parameters_lock_during_run_but_result_controls_remain_live() -> None:
    application()
    inspector = Inspector()
    workflow = Workflow()
    executor = FlowNode.create("executor")
    result = FlowNode.create("result")
    workflow.nodes = [executor, result]
    inspector.set_workflow(workflow)

    inspector.set_object(executor)
    inspector.set_execution_locked(True)
    assert inspector.model_combo.isEnabled() is False
    assert inspector.prompt_edit.isEnabled() is False

    inspector.set_object(result)
    assert inspector.template_edit.isEnabled() is False
    assert inspector.true_limit.isEnabled() is True
    assert inspector.false_limit.isEnabled() is True
    assert inspector.wait_for_confirmation.isEnabled() is True
    inspector.false_limit.setValue(7)
    inspector.wait_for_confirmation.setChecked(True)
    assert result.config["false_limit"] == 7
    assert result.config["wait_for_confirmation"] is True

    inspector.set_execution_locked(False)
    assert inspector.template_edit.isEnabled() is True
    inspector.close()


def test_canvas_uses_high_fps_only_while_running() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Тест")
    node = FlowNode.create("executor")
    workflow.nodes = [node]
    scene.set_workflow(workflow)
    assert not scene._running_timer.isActive()
    scene.set_node_status(node.id, "running")
    assert scene._running_timer.isActive()
    assert scene._running_timer.interval() <= 17
    scene.set_node_status(node.id, "success")
    assert not scene._running_timer.isActive()


def test_active_edge_animates_dashes() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Тест")
    first = FlowNode.create("executor")
    second = FlowNode.create("task_reviewer")
    edge = FlowEdge.create(first.id, second.id)
    workflow.nodes = [first, second]
    workflow.edges = [edge]
    scene.set_workflow(workflow)
    item = scene.edge_items[edge.id]
    assert item.is_active is False
    scene.set_active_edge(edge.id)
    assert item.is_active is True
    assert scene._running_timer.isActive()


def test_parameters_combo_and_spin_ignore_mouse_wheel() -> None:
    application()
    inspector = Inspector()

    class WheelStub:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    spin_event = WheelStub()
    spin_value = inspector.true_limit.value()
    inspector.true_limit.wheelEvent(spin_event)
    assert spin_event.ignored is True
    assert inspector.true_limit.value() == spin_value

    combo_event = WheelStub()
    combo_index = inspector.reasoning_combo.currentIndex()
    inspector.reasoning_combo.wheelEvent(combo_event)
    assert combo_event.ignored is True
    assert inspector.reasoning_combo.currentIndex() == combo_index
    inspector.close()


def test_large_parameter_fields_have_top_right_expand_button() -> None:
    app = application()
    inspector = Inspector()
    node = FlowNode.create("executor")
    inspector.set_workflow(Workflow(nodes=[node]))
    inspector.set_object(node)
    inspector.resize(520, 720)
    inspector.show()
    app.processEvents()

    editors = inspector.findChildren(ExpandablePlainTextEdit)
    assert {editor.accessibleName() for editor in editors} == {
        "Вхідний промпт",
        "Початковий JSON",
        "Постійні інструкції",
        "Промпт",
        "Схема JSON",
        "Шаблон результату",
        "Перетворення",
        "Промпт завдання 1",
    }
    button = inspector.instructions_edit.expand_button
    assert button.objectName() == "expandTextButton"
    assert button.x() > inspector.instructions_edit.width() - 40
    assert button.y() < 10
    inspector.close()


def test_fullscreen_text_editor_is_maximized() -> None:
    application()
    dialog = FullScreenTextEditorDialog("Постійні інструкції", "Початковий текст")
    maximized: list[bool] = []

    def inspect_and_close() -> None:
        maximized.append(dialog.isMaximized())
        dialog.reject()

    QTimer.singleShot(0, inspect_and_close)
    assert dialog.exec() == QDialog.DialogCode.Rejected
    assert maximized == [True]


def test_fullscreen_editor_applies_text_to_node_and_cancel_keeps_it(
    monkeypatch,
) -> None:
    application()
    inspector = Inspector()
    node = FlowNode.create("executor")
    node.config["instructions"] = "Старі інструкції"
    inspector.set_workflow(Workflow(nodes=[node]))
    inspector.set_object(node)

    def accept_with_new_text(dialog: FullScreenTextEditorDialog) -> int:
        dialog.editor.setPlainText("Нові постійні інструкції")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FullScreenTextEditorDialog, "exec", accept_with_new_text)
    inspector.instructions_edit.open_fullscreen_editor()
    assert inspector.instructions_edit.toPlainText() == "Нові постійні інструкції"
    assert node.config["instructions"] == "Нові постійні інструкції"

    def cancel_with_unsaved_text(dialog: FullScreenTextEditorDialog) -> int:
        dialog.editor.setPlainText("Не зберігати")
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(FullScreenTextEditorDialog, "exec", cancel_with_unsaved_text)
    inspector.instructions_edit.open_fullscreen_editor()
    assert inspector.instructions_edit.toPlainText() == "Нові постійні інструкції"
    assert node.config["instructions"] == "Нові постійні інструкції"
    inspector.close()


def test_attachment_list_uses_thumbnails_and_file_names(tmp_path: Path) -> None:
    application()
    picture = tmp_path / "reference.png"
    pixmap = QPixmap(32, 24)
    pixmap.fill(Qt.GlobalColor.red)
    assert pixmap.save(str(picture))
    document = tmp_path / "instructions.md"
    document.write_text("hello", encoding="utf-8")

    inspector = Inspector()
    node = FlowNode.create("executor")
    node.config["attachments"] = [str(picture), str(document)]
    inspector.set_workflow(Workflow(nodes=[node]))
    inspector.set_object(node)

    assert inspector.attachments.paths() == [str(picture), str(document)]
    assert inspector.attachments.item(0).text() == picture.name
    assert inspector.attachments.item(0).icon().isNull() is False
    assert inspector.attachments.item(1).text() == document.name
    assert inspector.attachments.item(1).icon().isNull() is True
    inspector.close()


def test_work_reviewer_subset_is_saved(tmp_path: Path) -> None:
    application()
    inspector = Inspector()
    workflow = Workflow()
    executor = FlowNode.create("executor")
    watcher = FlowNode.create("work_reviewer")
    workflow.nodes = [executor, watcher]
    inspector.set_workflow(workflow)

    inspector.set_object(watcher)
    assert inspector.monitor_all.isChecked() is True
    assert inspector.monitored_nodes.isEnabled() is False

    inspector.monitor_all.setChecked(False)
    inspector.monitored_nodes.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert watcher.config["monitor_all"] is False
    assert watcher.config["monitored_nodes"] == []
    inspector.close()


def test_open_dialog_remembers_the_last_directory(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "flowai-test.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        check_account_on_start=False,
        restore_workspaces=False,
        restore_layout=False,
        settings=settings,
    )
    assert window._last_directory() == Path.cwd()

    window.persist_layout = True
    window._remember_directory(tmp_path)
    assert window._last_directory() == tmp_path

    window.settings.remove("last_open_dir")
    window.close()


def test_layout_state_round_trips_through_settings(tmp_path: Path) -> None:
    application()
    settings = QSettings(str(tmp_path / "flowai-test.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        check_account_on_start=False,
        restore_workspaces=False,
        restore_layout=False,
        settings=settings,
    )
    window.persist_layout = True
    window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, window.inspector_dock)
    window.inspector_dock.set_docked_width(330)
    window._persist_layout()
    window.close()

    restored = MainWindow(
        check_account_on_start=False,
        restore_workspaces=False,
        restore_layout=True,
        settings=settings,
    )
    area = restored.dockWidgetArea(restored.inspector_dock)
    assert area == Qt.DockWidgetArea.BottomDockWidgetArea
    assert restored.inspector_dock._horizontal_width == 330
    assert restored.inspector_dock.maximumWidth() == 330

    restored.close()


def test_layout_changes_are_saved_before_window_closes(tmp_path: Path) -> None:
    app = application()
    settings = QSettings(
        str(tmp_path / "flowai-autosave-test.ini"), QSettings.Format.IniFormat
    )
    window = MainWindow(
        check_account_on_start=False,
        restore_workspaces=False,
        restore_layout=True,
        settings=settings,
    )
    window.show()
    app.processEvents()

    window.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, window.inspector_dock)
    window.inspector_dock.set_docked_width(344)
    QTest.qWait(400)

    assert settings.value("window_state") is not None
    assert (
        json.loads(str(settings.value("dock_horizontal_widths")))["inspectorDock"]
        == 344
    )

    restored = MainWindow(
        check_account_on_start=False,
        restore_workspaces=False,
        restore_layout=True,
        settings=settings,
    )
    assert (
        restored.dockWidgetArea(restored.inspector_dock)
        == Qt.DockWidgetArea.TopDockWidgetArea
    )
    assert restored.inspector_dock._horizontal_width == 344
    restored.close()
    window.close()


def test_full_access_agents_are_listed_for_the_warning() -> None:
    application()
    window = MainWindow(
        check_account_on_start=False, restore_workspaces=False, restore_layout=False
    )
    window.new_workflow()
    assert window._full_access_agents() == []

    executor = next(
        node for node in window.scene.workflow.nodes if node.kind == "executor"
    )
    watcher = next(
        node for node in window.scene.workflow.nodes if node.kind == "work_reviewer"
    )
    executor.config["sandbox"] = "full-access"
    watcher.config["sandbox"] = "full-access"
    assert window._full_access_agents() == [executor.title, watcher.title]

    for session in window.workspace_sessions:
        session.dirty = False
    window.dirty = False
    window.close()
