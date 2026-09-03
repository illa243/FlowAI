from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from flowai.ui.main_window import MainWindow


def test_projects_panel_recovers_hidden_floating_state_and_keeps_registry(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "panels.ini"), QSettings.Format.IniFormat)
    registry = json.dumps(
        [
            {
                "id": "saved-project",
                "path": str(tmp_path / "project.flowai.json"),
                "display_name": "Збережений проєкт",
                "custom_name": True,
            }
        ],
        ensure_ascii=False,
    )
    settings.setValue("workspace_registry", registry)
    windows: list[MainWindow] = []

    def open_window(*, restore_layout: bool) -> MainWindow:
        window = MainWindow(
            check_account_on_start=False,
            restore_workspaces=True,
            restore_layout=restore_layout,
            settings=settings,
        )
        windows.append(window)
        window.account_refresh_timer.stop()
        window.show()
        app.processEvents()
        return window

    try:
        original = open_window(restore_layout=False)
        original.persist_layout = True
        original.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, original.workspace_dock
        )
        original.workspace_dock.setFloating(True)
        original.workspace_dock.hide()
        app.processEvents()
        original._persist_layout()
        original.persist_layout = False
        original.close()

        recovered = open_window(restore_layout=True)
        assert recovered.workspace_dock.isHidden()
        assert recovered.workspace_dock.isFloating()
        other_areas = {
            dock.objectName(): recovered.dockWidgetArea(dock)
            for dock in (
                recovered.nodes_dock,
                recovered.inspector_dock,
                recovered.log_dock,
            )
        }
        assert [
            action.text()
            for action in recovered.view_menu.actions()
            if action.isCheckable()
        ] == ["Проєкти", "Ноди", "Властивості", "Журнал виконання"]

        recovered.restore_projects_panel_action.trigger()
        app.processEvents()
        assert recovered.workspace_dock.isVisible()
        assert not recovered.workspace_dock.isFloating()
        assert (
            recovered.dockWidgetArea(recovered.workspace_dock)
            == Qt.DockWidgetArea.BottomDockWidgetArea
        )
        assert recovered.workspace_dock.height() >= 150
        assert recovered.workspace_dock.windowTitle() == ""
        for dock in (
            recovered.nodes_dock,
            recovered.inspector_dock,
            recovered.log_dock,
        ):
            assert recovered.dockWidgetArea(dock) == other_areas[dock.objectName()]
        recovered._persist_layout()
        recovered.persist_layout = False
        recovered.close()

        restored = open_window(restore_layout=True)
        assert restored.workspace_dock.isVisible()
        assert not restored.workspace_dock.isFloating()
        assert (
            restored.dockWidgetArea(restored.workspace_dock)
            == Qt.DockWidgetArea.BottomDockWidgetArea
        )
        assert restored.workspace_sidebar.list_widget.count() == 1
        assert restored.workspace_sessions[0].id == "saved-project"
        assert settings.value("workspace_registry") == registry
    finally:
        for window in windows:
            window.persist_layout = False
            window.persist_workspace_registry = False
            window.layout_save_timer.stop()
            window.account_refresh_timer.stop()
            window.close()
