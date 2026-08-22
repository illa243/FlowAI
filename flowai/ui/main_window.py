from __future__ import annotations

import copy
import ctypes
import ctypes.wintypes
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..apply_edits import apply_edits, pin_skills
from ..calibration import CalibrationReport, save_report
from ..codex_auth import CodexRateLimit, CodexUser, logout_codex_user, read_codex_user
from ..engine import RunCheckpoint, WorkflowRunner
from ..grill import GrillOutcome
from ..logging_setup import log_paths, record_background_exception
from ..models import (
    NODE_COLORS,
    NODE_LABELS,
    FlowEdge,
    FlowNode,
    UnsupportedFlowFormat,
    Workflow,
    normalize_managed_tasks,
)
from ..persistence import FLOW_SUFFIX, load_workflow, save_workflow
from ..project_layout import isolated_flow_path, relocated_project_path
from ..run_history import (
    clear_checkpoint,
    find_pending_run,
    load_checkpoint,
    save_checkpoint,
)
from ..skills import SKILLS_ROOT
from ..workspaces import WorkspaceSession
from .branding import APP_USER_MODEL_ID, application_icon
from .calibration_dialog import CalibrationDialog
from .canvas import FlowScene, FlowView
from .controls import AnimatedButton
from .file_watch import RunFileWatcher
from .flow_composer_dialog import FlowComposerDialog, NewFlowChoiceDialog
from .grill_dialog import GrillDialog
from .icons import icon
from .inspector import Inspector
from .log_panel import LogPanel
from .login_dialog import ChatGPTLoginDialog
from .motion import AnimatedDialog
from .paths import open_file, path_menu
from .results_dialog import ResultsDialog
from .run_start_dialog import RunStartDialog
from .skills_page import SkillsPage
from .stats_dialog import StatsDialog
from .toast import ToastAction, Toaster
from .workspace_sidebar import ResponsiveListWidget, WorkspaceSidebar

HISTORY_LIMIT = 100
DOCK_MINIMUM_WIDTH = 96
WIDGET_SIZE_MAX = 16_777_215
MAX_UI_LOG_CHARS = 500_000
MAX_NODE_RESULT_PREVIEW = 4_000
LAYOUT_SAVE_DELAY_MS = 300
ACTIVITY_EVENT_MIN_INTERVAL_SECONDS = 0.1
WORKSPACE_UI_EVENT_TYPES = frozenset(
    {
        "run_started",
        "node_started",
        "node_finished",
        "node_failed",
        "node_cancelled",
        "node_waiting",
        "work_review_started",
        "work_review_finished",
        "work_review_failed",
        "intervention_required",
        "run_paused",
        "run_resumed",
        "run_finished",
        "run_failed",
        "run_cancelled",
    }
)

LOGGER = logging.getLogger(__name__)


class DockWidthHandle(QWidget):
    """Vertical grip used to resize a dock attached to the top or bottom."""

    def __init__(self, dock: ResizableDockWidget) -> None:
        super().__init__()
        self.dock = dock
        self._start_x: float | None = None
        self._start_width = 0
        self.setObjectName("dockWidthHandle")
        self.setFixedWidth(7)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Тягніть, щоб змінити ширину. Подвійний клік — на всю ширину")

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_x = event.globalPosition().x()
            self._start_width = self.dock.width()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._start_x is not None:
            delta = event.globalPosition().x() - self._start_x
            self.dock.set_docked_width(round(self._start_width + delta))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._start_x is not None:
            self._start_x = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dock.reset_docked_width()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ResizableDockWidget(QDockWidget):
    """Dock whose visible width can also be resized in horizontal dock areas."""

    layout_geometry_changed = Signal()

    def __init__(self, title: str, parent: QMainWindow) -> None:
        super().__init__(title, parent)
        self._horizontal_width: int | None = None
        self._horizontal_resize_enabled = False
        self.resize_handle = DockWidthHandle(self)
        self.resize_handle.hide()
        self._updating_window_flags = False
        self.setMinimumWidth(DOCK_MINIMUM_WIDTH)
        self.dockLocationChanged.connect(self.sync_resize_mode)
        self.topLevelChanged.connect(self._top_level_changed)

    def _top_level_changed(self, floating: bool) -> None:
        self.sync_resize_mode()
        if not floating or self._updating_window_flags:
            return
        flags = (
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        if flags == self.windowFlags():
            return
        self._updating_window_flags = True
        try:
            self.setWindowFlags(flags)
            self.show()
        finally:
            self._updating_window_flags = False

    def set_resizable_widget(self, content: QWidget) -> None:
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        wrapper = QWidget()
        wrapper.setMinimumWidth(0)
        wrapper.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content, 1)
        layout.addWidget(self.resize_handle)
        super().setWidget(wrapper)

    def sync_resize_mode(self, area: Qt.DockWidgetArea | None = None) -> None:
        window = self.parentWidget()
        if area is None and isinstance(window, QMainWindow):
            area = window.dockWidgetArea(self)
        horizontal = not self.isFloating() and area in {
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        }
        self._horizontal_resize_enabled = horizontal
        self.resize_handle.setVisible(horizontal)
        if horizontal and self._horizontal_width is not None:
            self.setMaximumWidth(self._horizontal_width)
        else:
            self.setMaximumWidth(WIDGET_SIZE_MAX)

    def set_docked_width(self, width: int) -> None:
        window = self.parentWidget()
        maximum = (
            max(DOCK_MINIMUM_WIDTH, window.width())
            if isinstance(window, QMainWindow)
            else WIDGET_SIZE_MAX
        )
        self._horizontal_width = max(DOCK_MINIMUM_WIDTH, min(int(width), maximum))
        if self._horizontal_resize_enabled:
            self.setMaximumWidth(self._horizontal_width)
            self.setMinimumWidth(self._horizontal_width)
            if isinstance(window, QMainWindow) and window.layout() is not None:
                window.layout().activate()
            self.setMinimumWidth(DOCK_MINIMUM_WIDTH)
            self.updateGeometry()
        self.layout_geometry_changed.emit()

    def reset_docked_width(self) -> None:
        self._horizontal_width = None
        if self._horizontal_resize_enabled:
            window = self.parentWidget()
            self.setMaximumWidth(WIDGET_SIZE_MAX)
            if isinstance(window, QMainWindow):
                self.setMinimumWidth(max(DOCK_MINIMUM_WIDTH, window.width()))
                if window.layout() is not None:
                    window.layout().activate()
                self.setMinimumWidth(DOCK_MINIMUM_WIDTH)
            self.updateGeometry()
        self.layout_geometry_changed.emit()

    def moveEvent(self, event: Any) -> None:
        super().moveEvent(event)
        if self.isFloating():
            self.layout_geometry_changed.emit()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.isFloating():
            self.layout_geometry_changed.emit()


class RunWorker(QObject):
    message = Signal(str, str, object)
    finished = Signal()

    def __init__(
        self,
        session_id: str,
        workflow: Workflow,
        project_path: Path | None,
        *,
        checkpoint: RunCheckpoint | None = None,
        intervention_responses: dict[str, Any] | None = None,
        run_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.runner = WorkflowRunner(
            workflow,
            project_path=project_path,
            on_event=self._forward_event,
            checkpoint=checkpoint,
            intervention_responses=intervention_responses,
            run_directory=run_directory,
        )
        self._last_activity_emit_at: dict[str, float] = {}

    def _forward_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "agent_activity" and event.get("phase") != "completed":
            node_id = str(event.get("node_id") or "")
            now = time.monotonic()
            last_emit = self._last_activity_emit_at.get(node_id, 0.0)
            if now - last_emit < ACTIVITY_EVENT_MIN_INTERVAL_SECONDS:
                return
            self._last_activity_emit_at[node_id] = now
        self.message.emit(self.session_id, "event", event)

    @Slot()
    def run(self) -> None:
        try:
            self.message.emit(self.session_id, "completed", self.runner.run())
        except Exception as exc:  # noqa: BLE001 - background worker boundary
            record_background_exception(f"workflow session {self.session_id}", exc)
            self.message.emit(self.session_id, "failed", str(exc))
        finally:
            self.message.emit(self.session_id, "finished", None)
            self.finished.emit()

    def cancel(self) -> None:
        self.runner.cancel()

    def pause(self, reason: str) -> None:
        self.runner.pause(reason)

    def resume(self, reason: str) -> None:
        self.runner.resume(reason)

    def update_result_config(self, node_id: str, updates: dict[str, Any]) -> bool:
        return self.runner.update_node_config(node_id, updates)


class AccountWorker(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action

    @Slot()
    def run(self) -> None:
        try:
            if self.action == "read":
                self.completed.emit(self.action, read_codex_user())
            elif self.action == "logout":
                logout_codex_user()
                self.completed.emit(self.action, None)
            else:
                raise ValueError(f"Невідома дія акаунта: {self.action}")
        except Exception as exc:  # noqa: BLE001 - SDK/process boundary
            self.failed.emit(self.action, str(exc))


def account_avatar_icon(initial: str, *, logged_in: bool) -> QIcon:
    pixmap = QPixmap(30, 30)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#7C3AED" if logged_in else "#334155"))
    painter.drawEllipse(1, 1, 28, 28)
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(11)
    painter.setFont(font)
    painter.drawText(
        pixmap.rect(), Qt.AlignmentFlag.AlignCenter, initial[:1].upper() or "C"
    )
    painter.end()
    return QIcon(pixmap)


def starter_workflow() -> Workflow:
    entry = FlowNode.create("entry", -640, -30)
    entry.config["text"] = "Опишіть, що потрібно зробити"

    improver = FlowNode.create("prompt_reviewer", -370, -30)
    executor = FlowNode.create("executor", -100, -30)
    reviewer = FlowNode.create("task_reviewer", 170, -30)
    result = FlowNode.create("result", 440, -30)
    watcher = FlowNode.create("work_reviewer", -100, 190)

    def link(
        source: FlowNode,
        target: FlowNode,
        path: str,
        variable: str,
        label: str = "",
        port: str = "out",
    ) -> FlowEdge:
        edge = FlowEdge.create(source.id, target.id, port)
        edge.source_path = path
        edge.target_variable = variable
        edge.label = label
        return edge

    edges = [
        link(entry, improver, "text", "entry_prompt", "промпт"),
        link(entry, executor, "data.attachments", "attachments", "файли"),
        link(improver, executor, "text", "prompt", "уточнений промпт"),
        link(executor, reviewer, "text", "work", "робота"),
        link(executor, result, "text", "work", "робота"),
        link(reviewer, result, "data", "review", "вердикт"),
    ]
    back = link(
        result,
        executor,
        "data.retry_context",
        "prompt",
        "структуровані правки",
        "false",
    )
    edges.append(back)

    return Workflow(
        name="Мій перший Flow",
        nodes=[entry, improver, executor, reviewer, result, watcher],
        edges=edges,
    )


class WorkflowSettingsDialog(AnimatedDialog):
    def __init__(
        self,
        workflow: Workflow,
        parent: QWidget | None = None,
        *,
        codex: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Налаштування Flow")
        self.setMinimumWidth(520)
        self.name_edit = QLineEdit(workflow.name)
        self.workspace_edit = QLineEdit(workflow.workspace)
        choose = QPushButton("Вибрати…")
        choose.clicked.connect(self._choose_workspace)
        clear_workspace = QPushButton("Прибрати")
        clear_workspace.clicked.connect(self.workspace_edit.clear)
        workspace_box = QWidget()
        workspace_layout = QHBoxLayout(workspace_box)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(self.workspace_edit)
        workspace_layout.addWidget(choose)
        workspace_layout.addWidget(clear_workspace)

        self.additional_folders_list = QListWidget()
        self.additional_folders_list.setMaximumHeight(120)
        self.additional_folders_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self.additional_folders_list.addItems(workflow.additional_folders)
        additional_box = QWidget()
        additional_layout = QVBoxLayout(additional_box)
        additional_layout.setContentsMargins(0, 0, 0, 0)
        additional_layout.addWidget(self.additional_folders_list)
        additional_buttons = QHBoxLayout()
        add_folder = QPushButton("Додати папку")
        remove_folder = QPushButton("Прибрати")
        add_folder.clicked.connect(self._add_additional_folder)
        remove_folder.clicked.connect(self._remove_additional_folders)
        additional_buttons.addWidget(add_folder)
        additional_buttons.addWidget(remove_folder)
        additional_layout.addLayout(additional_buttons)

        form = QFormLayout()
        form.addRow("Назва", self.name_edit)
        form.addRow("Папка джерел", workspace_box)
        form.addRow("Додаткові джерела", additional_box)
        hint = QLabel(
            "Тека зі збереженим Flow завжди є єдиною текою для запису. "
            "Вказані тут зовнішні папки доступні агентам лише як джерела; "
            "усі нові файли створюються всередині Flow-проєкту."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        flow_tab = QWidget()
        flow_layout = QVBoxLayout(flow_tab)
        flow_layout.setContentsMargins(0, 0, 0, 0)
        flow_layout.addLayout(form)
        flow_layout.addStretch()

        self.skills_page = SkillsPage(self, codex=codex)
        self.tabs = QTabWidget()
        self.tabs.addTab(flow_tab, "Flow")
        self.tabs.addTab(self.skills_page, "Skills")

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(buttons)
        self.setMinimumSize(880, 620)

    def _choose_workspace(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Робоча папка Flow",
            self.workspace_edit.text() or str(Path.cwd()),
        )
        if directory:
            self.workspace_edit.setText(directory)

    def _add_additional_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Додаткова папка Flow",
            self.workspace_edit.text() or str(Path.cwd()),
        )
        if not directory:
            return
        resolved = str(Path(directory).resolve())
        existing = {
            self.additional_folders_list.item(index).text()
            for index in range(self.additional_folders_list.count())
        }
        if resolved not in existing:
            self.additional_folders_list.addItem(resolved)

    def _remove_additional_folders(self) -> None:
        for item in self.additional_folders_list.selectedItems():
            self.additional_folders_list.takeItem(
                self.additional_folders_list.row(item)
            )

    def additional_folders(self) -> list[str]:
        return [
            self.additional_folders_list.item(index).text()
            for index in range(self.additional_folders_list.count())
        ]


class GeneratedFilesDialog(AnimatedDialog):
    """Chronological files produced by the currently selected workspace run."""

    def __init__(
        self, session: WorkspaceSession, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.session = session
        self._rendered_signature = ""
        self.setWindowTitle(f"Files — {session.display_name}")
        self.setMinimumSize(820, 520)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Файли показані в порядку завершення нод. Подвійний клік відкриває "
            "файл у стандартній програмі."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setObjectName("generatedFilesTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Нода / файл", "Повний шлях"])
        self.tree.setColumnWidth(0, 310)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.itemDoubleClicked.connect(self._open_file)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(600)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()

    def refresh(self) -> None:
        signature = json.dumps(
            self.session.generated_file_groups,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if signature == self._rendered_signature:
            return
        self._rendered_signature = signature
        self.tree.clear()
        if not self.session.generated_file_groups:
            empty = QTreeWidgetItem(["Файлів поточного запуску ще немає", ""])
            empty.setForeground(0, QColor("#94A3B8"))
            self.tree.addTopLevelItem(empty)
            return

        for group in self.session.generated_file_groups:
            node_id = str(group.get("node_id", ""))
            title = str(group.get("node_title") or "Нода")
            iteration = max(1, int(group.get("iteration", 1)))
            color = QColor(str(group.get("color") or "#CBD5E1"))
            heading = QTreeWidgetItem(
                [f"{title} · ID: {node_id} · прохід {iteration}", ""]
            )
            heading.setToolTip(0, node_id)
            heading_font = heading.font(0)
            heading_font.setBold(True)
            heading.setFont(0, heading_font)
            self._color_item(heading, color)
            self.tree.addTopLevelItem(heading)

            intermediate = [
                str(path) for path in group.get("intermediate", []) if str(path)
            ]
            result = [str(path) for path in group.get("result", []) if str(path)]
            if intermediate:
                self._add_section(heading, "Проміжні файли", intermediate, color)
            self._add_section(
                heading,
                "Результат",
                result,
                color,
                empty_text="Фінальний файл не вказано",
            )
            heading.setExpanded(True)

    def _add_section(
        self,
        parent: QTreeWidgetItem,
        title: str,
        paths: list[str],
        color: QColor,
        *,
        empty_text: str = "",
    ) -> None:
        section = QTreeWidgetItem([title, ""])
        section_font = section.font(0)
        section_font.setBold(True)
        section.setFont(0, section_font)
        self._color_item(section, color)
        parent.addChild(section)
        if not paths and empty_text:
            placeholder = QTreeWidgetItem([empty_text, ""])
            self._plain_item(placeholder)
            section.addChild(placeholder)
        for raw_path in paths:
            path = Path(raw_path)
            available = path.is_file()
            label = path.name or raw_path
            full_path = raw_path if available else f"{raw_path}  ·  файл недоступний"
            item = QTreeWidgetItem([label, full_path])
            item.setData(0, Qt.ItemDataRole.UserRole, raw_path)
            item.setToolTip(0, raw_path)
            item.setToolTip(1, raw_path)
            self._plain_item(item)
            section.addChild(item)
        section.setExpanded(True)

    @staticmethod
    def _color_item(item: QTreeWidgetItem, color: QColor) -> None:
        item.setForeground(0, color)
        item.setForeground(1, color)

    @staticmethod
    def _plain_item(item: QTreeWidgetItem) -> None:
        item.setForeground(0, QColor("#E5E7EB"))
        item.setForeground(1, QColor("#94A3B8"))

    def _open_file(self, item: QTreeWidgetItem, _column: int) -> None:
        raw_path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if raw_path:
            open_file(raw_path)

    def _context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        raw_path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if raw_path:
            path_menu(raw_path, self).exec(
                self.tree.viewport().mapToGlobal(position)
            )


class ResultLimitDialog(AnimatedDialog):
    """Гілка блока Result вичерпала ліміт проходів."""

    def __init__(self, request: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ліміт проходів вичерпано")
        self.setMinimumWidth(560)
        self.response: dict[str, Any] | None = None

        port = str(request.get("port", "")).upper()
        used = request.get("used", 0)
        limit = request.get("limit", 0)
        other = "FALSE" if port == "TRUE" else "TRUE"

        layout = QVBoxLayout(self)
        heading = QLabel(f"Блок «{request.get('node_title', 'Result')}»")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        summary = QLabel(
            f"Вибрано гілку <b>{port}</b>. Вона вже пройдена {used} з {limit} разів, "
            "тому Flow зупинився і чекає на ваше рішення."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        reason = str(request.get("reason", "")).strip()
        if reason:
            layout.addWidget(QLabel("Останній висновок рев'ювера:"))
            reason_view = QPlainTextEdit(reason)
            reason_view.setReadOnly(True)
            reason_view.setMaximumHeight(110)
            layout.addWidget(reason_view)

        must_fix = request.get("must_fix")
        if isinstance(must_fix, list) and must_fix:
            layout.addWidget(QLabel("Структуровані правки must_fix:"))
            fixes_view = QPlainTextEdit(
                json.dumps(must_fix, ensure_ascii=False, indent=2)
            )
            fixes_view.setReadOnly(True)
            fixes_view.setMaximumHeight(170)
            layout.addWidget(fixes_view)

        candidate_path = str(request.get("candidate_path", "")).strip()
        if candidate_path:
            candidate = QLabel(f"Поточний файл: <u>{candidate_path}</u>")
            candidate.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            candidate.setWordWrap(True)
            layout.addWidget(candidate)

        form = QFormLayout()
        self.attempts = QSpinBox()
        self.attempts.setRange(1, 20)
        self.attempts.setValue(2)
        form.addRow("Додати спроб", self.attempts)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText(
            "Необов'язково: підкажіть агенту, що саме він робить не так"
        )
        self.note.setMaximumHeight(90)
        form.addRow("Вказівка агенту", self.note)
        layout.addLayout(form)

        self.force = QCheckBox(
            f"Замість цього піти гілкою {other} (без списання ліміту)"
        )
        self.force.toggled.connect(self._force_toggled)
        layout.addWidget(self.force)

        buttons = QDialogButtonBox()
        self.continue_button = buttons.addButton(
            "Продовжити", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton("Зупинити Flow", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._other_branch = other.lower()

    def _force_toggled(self, checked: bool) -> None:
        self.attempts.setEnabled(not checked)

    def _accept(self) -> None:
        note = self.note.toPlainText().strip()
        if self.force.isChecked():
            self.response = {
                "action": "force_branch",
                "branch": self._other_branch,
                "note": note,
            }
        else:
            self.response = {
                "action": "add_attempts",
                "count": self.attempts.value(),
                "note": note,
            }
        self.accept()


class ResultConfirmationDialog(AnimatedDialog):
    """Manual checkpoint requested by a Result node."""

    def __init__(self, request: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Підтвердження проміжного результату")
        self.setMinimumWidth(580)
        self.response: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        heading = QLabel(f"Блок «{request.get('node_title', 'Result')}»")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        summary = QLabel(
            "Flow призупинено перед переходом далі. Перегляньте проміжні "
            "файли, після чого натисніть «Продовжити»."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        files = [str(item) for item in request.get("files", []) if str(item)]
        if files:
            layout.addWidget(QLabel("Проміжні файли:"))
            file_list = QListWidget()
            for path in files:
                item = QListWidgetItem(Path(path).name)
                item.setToolTip(path)
                item.setData(Qt.ItemDataRole.UserRole, path)
                file_list.addItem(item)
            file_list.itemDoubleClicked.connect(
                lambda item: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(item.data(Qt.ItemDataRole.UserRole)))
                )
            )
            file_list.setMaximumHeight(150)
            layout.addWidget(file_list)

        reason = str(request.get("reason", "")).strip()
        if reason:
            reason_view = QPlainTextEdit(reason)
            reason_view.setReadOnly(True)
            reason_view.setMaximumHeight(100)
            layout.addWidget(reason_view)

        buttons = QDialogButtonBox()
        continue_button = buttons.addButton(
            "Продовжити", QDialogButtonBox.ButtonRole.AcceptRole
        )
        continue_button.setObjectName("primaryButton")
        buttons.addButton("Зупинити Flow", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.response = {"action": "continue"}
        self.accept()


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        check_account_on_start: bool = True,
        restore_workspaces: bool = True,
        restore_layout: bool | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("FlowAI")
        self.setWindowIcon(application_icon())
        self.resize(1500, 920)
        self.setMinimumSize(1080, 700)
        self.setDockNestingEnabled(True)
        self.settings = (
            settings if settings is not None else QSettings("FlowAI", "FlowAI")
        )
        self.persist_workspace_registry = restore_workspaces
        self.persist_layout = (
            restore_workspaces if restore_layout is None else restore_layout
        )
        self.saved_geometry = (
            self.settings.value("window_geometry") if self.persist_layout else None
        )
        self.saved_window_state = (
            self.settings.value("window_state") if self.persist_layout else None
        )
        self._layout_restore_complete = False
        self.layout_save_timer = QTimer(self)
        self.layout_save_timer.setSingleShot(True)
        self.layout_save_timer.setInterval(LAYOUT_SAVE_DELAY_MS)
        self.layout_save_timer.timeout.connect(self._persist_layout)
        self.project_path: Path | None = None
        self.dirty = False
        self.workspace_sessions: list[WorkspaceSession] = []
        self.current_workspace_id: str | None = None
        self.account_thread: QThread | None = None
        self.account_worker: AccountWorker | None = None
        self.current_user: CodexUser | None = None
        self.current_run_events: list[dict[str, Any]] = []
        self.calibration_models = [
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
        ]
        self.intervention_dialog_open = False
        self._system_pause_reasons: set[str] = set()
        self._notification_target: tuple[str, str] | None = None
        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(300)
        self.history_timer.timeout.connect(self._commit_current_history)

        self.scene = FlowScene()
        self.view = FlowView(self.scene)
        self.central_stack = QStackedWidget()
        self.empty_workspace_page = self._build_empty_workspace_page()
        self.central_stack.addWidget(self.empty_workspace_page)
        self.central_stack.addWidget(self.view)
        self.setCentralWidget(self.central_stack)
        self.inspector = Inspector()
        self.log_panel = LogPanel()

        self._build_toolbar()
        self._build_workspace_sidebar()
        self._build_palette()
        self._build_inspector()
        self._build_log()
        self._build_menu()
        self._build_notifications()
        self._restore_layout()
        self._layout_restore_complete = True
        self._enable_layout_autosave()

        self.scene.selection_object_changed.connect(self.inspector.set_object)
        self.scene.model_changed.connect(self._mark_dirty)
        self.scene.message.connect(self.statusBar().showMessage)
        self.scene.attention_clicked.connect(
            lambda _node_id: QTimer.singleShot(
                0, lambda: self._show_pending_intervention(user_initiated=True)
            )
        )
        self.view.rename_requested.connect(self.rename_node)
        self.inspector.changed.connect(self._inspector_changed)

        if restore_workspaces:
            self._restore_workspace_registry()
        self._show_no_workspace()
        if check_account_on_start:
            QTimer.singleShot(100, self.refresh_account)
        self.account_refresh_timer = QTimer(self)
        self.account_refresh_timer.setInterval(5 * 60 * 1000)
        self.account_refresh_timer.timeout.connect(self.refresh_account)
        self.account_refresh_timer.start()
        self.statusBar().showMessage(
            "FlowAI готовий. Виберіть середовище або створіть Flow."
        )

    def _build_empty_workspace_page(self) -> QWidget:
        page = QWidget()
        title = QLabel("Виберіть робоче середовище")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Відкрийте збережений Flow або створіть новий проєкт")
        hint.setObjectName("mutedLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        open_button = QPushButton("Відкрити Flow")
        open_button.clicked.connect(self.open_workflow)
        new_button = QPushButton("Створити новий Flow")
        new_button.clicked.connect(self.new_workflow)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(open_button)
        buttons.addWidget(new_button)
        buttons.addStretch()
        layout = QVBoxLayout(page)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(8)
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Основні дії")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.main_toolbar = toolbar
        self.addToolBar(toolbar)

        self.new_action = QAction("Новий", self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self.new_workflow)
        self.open_action = QAction("Відкрити", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_workflow)
        self.save_action = QAction("Зберегти", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_workflow)

        self.settings_action = QAction(icon("settings"), "Settings", self)
        self.settings_action.triggered.connect(self.edit_workflow_settings)
        self.settings_button = AnimatedButton(
            "Settings", variant="ghost", icon_name="settings"
        )
        self.settings_button.setDefaultAction(self.settings_action)
        toolbar.addWidget(self.settings_button)

        self.edit_flow_action = QAction(icon("sparkles"), "Edit Flow", self)
        self.edit_flow_action.triggered.connect(self.edit_flow_with_ai)
        self.edit_flow_button = AnimatedButton(
            "Edit Flow", variant="ghost", icon_name="sparkles"
        )
        self.edit_flow_button.setObjectName("editFlowButton")
        self.edit_flow_button.setToolTip("Змінити вибраний Flow за допомогою AI")
        self.edit_flow_button.setDefaultAction(self.edit_flow_action)
        toolbar.addWidget(self.edit_flow_button)
        toolbar.addSeparator()

        self.run_action = QAction("▶ Run", self)
        self.run_action.triggered.connect(self.run_workflow)
        self.stop_action = QAction("■ Stop", self)
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stop_workflow)
        self.files_action = QAction("Files", self)
        self.files_action.setEnabled(False)
        self.files_action.triggered.connect(self.show_generated_files)
        self.stats_action = QAction("Stats", self)
        self.stats_action.setEnabled(False)
        self.stats_action.triggered.connect(self.show_run_stats)
        self.run_button = AnimatedButton("Run", variant="success", icon_name="play")
        self.run_button.setDefaultAction(self.run_action)
        self.stop_button = AnimatedButton(
            "Stop", variant="danger", icon_name="square"
        )
        self.stop_button.setDefaultAction(self.stop_action)
        self.files_button = AnimatedButton(
            "Files", variant="secondary", icon_name="folder"
        )
        self.files_button.setDefaultAction(self.files_action)
        self.stats_button = AnimatedButton(
            "Stats", variant="secondary", icon_name="chart"
        )
        self.stats_button.setDefaultAction(self.stats_action)
        for name, button in (
            ("runButton", self.run_button),
            ("stopButton", self.stop_button),
            ("filesButton", self.files_button),
            ("statsButton", self.stats_button),
        ):
            button.setObjectName(name)
            toolbar.addWidget(button)
        toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.account_button = QToolButton()
        self.account_button.setObjectName("accountButton")
        self.account_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.account_button.setIconSize(QSize(30, 30))
        self.account_button.setMinimumHeight(40)
        self.account_button.clicked.connect(self.account_button_clicked)
        toolbar.addWidget(self.account_button)
        self._update_account_button()

    def _build_workspace_sidebar(self) -> None:
        self.workspace_sidebar = WorkspaceSidebar()
        self.workspace_sidebar.workspace_selected.connect(self.select_workspace)
        self.workspace_sidebar.list_widget.rename_requested.connect(
            self.rename_workspace
        )
        self.workspace_sidebar.add_requested.connect(self.open_workflow)
        self.workspace_sidebar.action_requested.connect(self._workspace_action)
        dock = ResizableDockWidget("", self)
        dock.setObjectName("workspacesDock")
        dock.setAccessibleName("Середовища")
        dock.setToolTip("Робочі середовища")
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dock.set_resizable_widget(self.workspace_sidebar)
        self.workspace_dock = dock
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        dock.sync_resize_mode(Qt.DockWidgetArea.LeftDockWidgetArea)

    def _build_palette(self) -> None:
        dock = ResizableDockWidget("", self)
        dock.setObjectName("nodesDock")
        dock.setAccessibleName("Ноди")
        dock.setToolTip("Ноди")
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.node_list = ResponsiveListWidget()
        self.node_list.setObjectName("paletteList")
        self.node_list.setSpacing(4)
        self.node_list.setSelectionMode(ResponsiveListWidget.SelectionMode.NoSelection)
        self.node_buttons: list[AnimatedButton] = []
        for kind in [
            "entry",
            "tasks_manager",
            "prompt_reviewer",
            "executor",
            "task_reviewer",
            "result",
            "calibrator",
            "work_reviewer",
        ]:
            button = AnimatedButton(
                NODE_LABELS[kind],
                variant="secondary",
                icon_name="plus",
                color=NODE_COLORS[kind],
            )
            button.clicked.connect(
                lambda checked=False, node_kind=kind: self.add_node(node_kind)
            )
            item = QListWidgetItem()
            item.setSizeHint(QSize(188, 38))
            self.node_list.addItem(item)
            self.node_list.setItemWidget(item, button)
            self.node_buttons.append(button)
        layout.addWidget(self.node_list, 1)
        dock.set_resizable_widget(content)
        self.nodes_dock = dock
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        dock.sync_resize_mode(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.splitDockWidget(self.workspace_dock, dock, Qt.Orientation.Vertical)
        if not self.saved_window_state:
            self.resizeDocks(
                [self.workspace_dock, dock],
                [245, 245],
                Qt.Orientation.Horizontal,
            )
            self.resizeDocks(
                [self.workspace_dock, dock],
                [520, 300],
                Qt.Orientation.Vertical,
            )

    def _build_inspector(self) -> None:
        dock = ResizableDockWidget("Parameters", self)
        dock.setObjectName("inspectorDock")
        dock.setAccessibleName("Інспектор")
        dock.setToolTip("Властивості вибраного елемента")
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dock.set_resizable_widget(self.inspector)
        self.inspector_dock = dock
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.sync_resize_mode(Qt.DockWidgetArea.RightDockWidgetArea)
        if not self.saved_window_state:
            self.resizeDocks([dock], [370], Qt.Orientation.Horizontal)

    def _build_log(self) -> None:
        dock = ResizableDockWidget("", self)
        dock.setObjectName("logDock")
        dock.setAccessibleName("Журнал виконання")
        dock.setToolTip("Журнал виконання")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        controls = QHBoxLayout()
        controls.addStretch()
        clear = QPushButton("Очистити")
        clear.clicked.connect(self._clear_current_log)
        controls.addWidget(clear)
        layout.addLayout(controls)
        layout.addWidget(self.log_panel)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dock.set_resizable_widget(content)
        dock.setMinimumHeight(180)
        self.log_dock = dock
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.sync_resize_mode(Qt.DockWidgetArea.BottomDockWidgetArea)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction(self.new_action)
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.save_action)
        save_as = QAction("Зберегти як…", self)
        save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as.triggered.connect(self.save_workflow_as)
        file_menu.addAction(save_as)
        file_menu.addSeparator()
        file_menu.addAction("Вихід", self.close)

        edit_menu = self.menuBar().addMenu("Редагування")
        self.undo_action = QAction("Скасувати", self)
        self.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self.undo_action.triggered.connect(self.undo)
        edit_menu.addAction(self.undo_action)
        self.redo_action = QAction("Повторити", self)
        self.redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        self.redo_action.triggered.connect(self.redo)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        delete = QAction("Видалити вибране", self)
        delete.setShortcut(QKeySequence.StandardKey.Delete)
        delete.triggered.connect(self.scene.delete_selection)
        edit_menu.addAction(delete)
        edit_menu.addAction("Показати весь Flow", self._fit_graph)

        run_menu = self.menuBar().addMenu("Запуск")
        run_menu.addAction(self.run_action)
        run_menu.addAction(self.stop_action)

        help_menu = self.menuBar().addMenu("Довідка")
        help_menu.addAction("Відкрити папку логів", self._open_log_directory)
        help_menu.addSeparator()
        help_menu.addAction("Про FlowAI", self.show_about)

    def _build_notifications(self) -> None:
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("FlowAI")
        self.tray_icon.messageClicked.connect(self._notification_clicked)
        self.toaster = Toaster(APP_USER_MODEL_ID, self)
        self.toaster.activated.connect(self._toast_activated)
        self.calibration_open_tab = 0
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

    def _notify_user(
        self,
        session: WorkspaceSession,
        title: str,
        message: str,
        *,
        node_id: str = "",
        warning: bool = False,
        actions: list[ToastAction] | None = None,
    ) -> None:
        """Сповістити поза фокусом: спершу тост, потім трей-балон."""
        if self.isActiveWindow():
            return
        self._notification_target = (session.id, node_id)
        if self.toaster.show(
            title, message, tag=session.id, actions=list(actions or [])
        ):
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.tray_icon.showMessage(title, message, icon, 12_000)

    @Slot(str, str)
    def _toast_activated(self, tag: str, action: str) -> None:
        """Підняти FlowAI й відкрити запитану вкладку калібрації."""
        self.calibration_open_tab = 1 if action == "edits" else 0
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.select_workspace(tag)
        QTimer.singleShot(0, lambda: self._show_pending_intervention(True))

    def _notification_clicked(self) -> None:
        target = self._notification_target
        if target is None:
            return
        session_id, node_id = target
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.select_workspace(session_id)
        if not node_id:
            return
        item = self.scene.node_items.get(node_id)
        if item is None:
            return
        self.scene.clearSelection()
        item.setSelected(True)
        self.view.centerOn(item)
        self.view.setFocus()

    def add_node(self, kind: str) -> None:
        if self.current_workspace is None:
            return
        offset = len(self.scene.workflow.nodes) * 12
        self.scene.add_node(kind, self.view.center_position() + QPointF(offset, offset))

    def new_workflow(self) -> None:
        choice = "starter"
        if self.isVisible():
            chooser = NewFlowChoiceDialog(self)
            if chooser.exec() != QDialog.DialogCode.Accepted:
                return
            choice = chooser.choice
        if choice == "ai":
            composer = FlowComposerDialog(self)
            if (
                composer.exec() == QDialog.DialogCode.Accepted
                and composer.saved_path
            ):
                self._open_workflow_path(Path(composer.saved_path))
            return
        workflow = Workflow() if choice == "blank" else starter_workflow()
        session = WorkspaceSession(
            display_name=workflow.name,
            workflow=workflow,
            load_state="loaded",
            dirty=True,
        )
        self._initialize_workspace_history(session, saved=False)
        self.workspace_sessions.append(session)
        self.select_workspace(session.id)
        QTimer.singleShot(20, self._fit_graph)

    def edit_flow_with_ai(self) -> None:
        """Edit only the selected Flow through the AI composer and MCP draft."""
        session = self.current_workspace
        if (
            session is None
            or session.workflow is None
            or session.run_thread is not None
        ):
            return
        if session.project_path is None and not self.save_workflow():
            return
        # The MCP editor loads the exact on-disk Flow into an isolated draft.
        # Saving first includes any manual canvas changes and supplies a stable path.
        if not self.save_workflow() or self.project_path is None:
            return
        target = self.project_path.resolve()
        workspace = self.scene.workflow.resolved_workspace(target)
        dialog = FlowComposerDialog(
            self,
            edit_path=target,
            initial_workspace=workspace,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.saved_path:
            return
        try:
            edited = load_workflow(Path(dialog.saved_path))
            errors = edited.validate()
            if errors:
                raise ValueError("Змінений Flow не валідний:\n" + "\n".join(errors))
        except Exception as exc:  # noqa: BLE001 - worker/UI file boundary
            QMessageBox.critical(self, "Не вдалося відкрити змінений Flow", str(exc))
            return
        self._apply_ai_edited_workflow(session, edited)
        self.statusBar().showMessage(f"AI змінив вибраний Flow: {target}", 7000)

    def _apply_ai_edited_workflow(
        self, session: WorkspaceSession, edited: Workflow
    ) -> None:
        """Adopt an already-saved AI edit while keeping the old Flow undoable."""
        previous = self._workflow_snapshot(session.workflow or self.scene.workflow)
        current = self._workflow_snapshot(edited)
        if previous != current:
            session.undo_history.append(previous)
            if len(session.undo_history) > HISTORY_LIMIT:
                del session.undo_history[:-HISTORY_LIMIT]
        session.redo_history.clear()
        session.workflow = edited
        session.history_state = copy.deepcopy(current)
        session.saved_history_state = copy.deepcopy(current)
        session.display_name = edited.name
        session.selected_object = None
        session.dirty = False
        self.dirty = False
        self.scene.set_workflow(edited)
        self.inspector.set_workflow(edited)
        self._persist_workspace_registry()
        self._refresh_workspace_sidebar()
        self._update_title()
        self._update_history_actions()
        QTimer.singleShot(0, self._fit_graph)

    def open_workflow(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Відкрити FlowAI",
            str(self._last_directory()),
            "FlowAI (*.flowai.json);;JSON (*.json)",
        )
        if not path:
            return
        self._open_workflow_path(Path(path))

    def _open_workflow_path(self, path: Path) -> None:
        resolved = path.resolve()
        self._remember_directory(resolved.parent)
        existing = next(
            (
                session
                for session in self.workspace_sessions
                if session.project_path is not None
                and session.project_path.resolve() == resolved
            ),
            None,
        )
        if existing is None:
            existing = WorkspaceSession(
                display_name=resolved.stem, project_path=resolved
            )
            self.workspace_sessions.append(existing)
        self.select_workspace(existing.id)
        self._persist_workspace_registry()

    def save_workflow(self) -> bool:
        session = self.current_workspace
        if session is None or session.workflow is None:
            return False
        self._commit_current_history()
        if self.project_path is None:
            return self.save_workflow_as()
        try:
            self.project_path = save_workflow(self.scene.workflow, self.project_path)
        except Exception as exc:  # noqa: BLE001 - filesystem/UI boundary
            QMessageBox.critical(self, "Не вдалося зберегти Flow", str(exc))
            return False
        self.dirty = False
        session.project_path = self.project_path
        session.workflow = self.scene.workflow
        session.display_name = self.scene.workflow.name
        session.dirty = False
        session.load_state = "loaded"
        saved_state = self._workflow_snapshot(self.scene.workflow)
        session.history_state = copy.deepcopy(saved_state)
        session.saved_history_state = copy.deepcopy(saved_state)
        self._persist_workspace_registry()
        self._refresh_workspace_sidebar()
        self._update_title()
        self._update_history_actions()
        self.statusBar().showMessage(f"Збережено: {self.project_path}", 5000)
        return True

    def save_workflow_as(self) -> bool:
        session = self.current_workspace
        if session is None or session.workflow is None:
            return False
        initial = self.project_path or (
            self._last_directory() / f"{self.scene.workflow.name}{FLOW_SUFFIX}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Зберегти FlowAI",
            str(initial),
            "FlowAI (*.flowai.json)",
        )
        if not path:
            return False
        target = Path(path)
        if not target.name.lower().endswith(FLOW_SUFFIX):
            target = target.with_name(target.name + FLOW_SUFFIX)
        target = isolated_flow_path(target)
        self._remember_directory(target.resolve().parent)
        other = next(
            (
                item
                for item in self.workspace_sessions
                if item.id != session.id
                and item.project_path is not None
                and item.project_path.resolve() == target.resolve()
            ),
            None,
        )
        if other is not None:
            QMessageBox.warning(
                self,
                "Flow уже відкритий",
                "Інше робоче середовище вже використовує цей файл.",
            )
            return False
        self.project_path = target
        return self.save_workflow()

    def edit_workflow_settings(self) -> None:
        session = self.current_workspace
        if session is None or session.workflow is None:
            return
        dialog = WorkflowSettingsDialog(self.scene.workflow, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scene.workflow.name = dialog.name_edit.text().strip() or "Flow"
            self.scene.workflow.workspace = dialog.workspace_edit.text().strip()
            self.scene.workflow.additional_folders = dialog.additional_folders()
            session.display_name = self.scene.workflow.name
            self._mark_dirty()

    def run_workflow(self, resume: bool = False) -> None:
        session = self.current_workspace
        if (
            session is None
            or session.workflow is None
            or session.run_thread is not None
        ):
            return
        if (
            not resume
            and self.isVisible()
            and not bool(
                self.settings.value("run/skip_start_dialog", False, type=bool)
            )
        ):
            starter = RunStartDialog(self)
            if starter.exec() != QDialog.DialogCode.Accepted:
                return
            if starter.choice == "grill" and not self._run_grill(session):
                return
        errors = self.scene.workflow.validate()
        if errors:
            QMessageBox.warning(self, "Flow потребує виправлення", "\n".join(errors))
            return
        full_access = [] if resume else self._full_access_agents()
        if full_access:
            answer = QMessageBox.warning(
                self,
                "Повний доступ",
                "Ці агенти матимуть повний доступ до файлової системи:\n\n"
                + "\n".join(full_access)
                + "\n\nПродовжити?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        snapshot = Workflow.from_dict(copy.deepcopy(self.scene.workflow.to_dict()))
        if not resume:
            self.scene.reset_statuses()
            session.node_statuses = self.scene.node_statuses()
            session.node_durations.clear()
            session.node_duration_history.clear()
            session.node_started_at.clear()
            session.node_stages.clear()
            session.task_states.clear()
            session.port_counts.clear()
            self.log_panel.clear()
            session.log_text = ""
            session.log_entries.clear()
            session.generated_file_groups.clear()
            session.active_file_node_id = ""
            session.active_file_iteration = 0
            session.run_events = []
            session.checkpoint = None
            session.intervention_responses = {}
            session.pending_intervention = None
            session.run_directory = self._new_run_directory(session)
            self.current_run_events = session.run_events
            self._append_session_log(session, "▶ Запуск Workflow")
        else:
            self._append_session_log(session, "▶ Продовження Workflow після відповіді")

        session.run_state = "running"
        session.stop_requested = False
        session.unread_result = False
        self._refresh_workspace_sidebar()
        self._update_workspace_actions()
        LOGGER.info(
            "Starting workflow %r (session=%s, project=%s, resume=%s)",
            snapshot.name,
            session.id,
            session.project_path,
            resume,
        )

        thread = QThread(self)
        thread.setObjectName(f"FlowRun-{session.id[:8]}")
        thread.setProperty("flowai_session_id", session.id)
        worker = RunWorker(
            session.id,
            snapshot,
            session.project_path,
            checkpoint=session.checkpoint if resume else None,
            intervention_responses=session.intervention_responses if resume else None,
            run_directory=session.run_directory,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.message.connect(
            self._handle_run_worker_message,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        session.run_thread = thread
        session.run_worker = worker
        watcher = RunFileWatcher(self)
        watcher.file_ready.connect(
            lambda path, session_id=session.id: self._intermediate_file(
                session_id, path
            )
        )
        workflow = session.workflow
        roots = [workflow.resolved_workspace(session.project_path)]
        watcher.start(roots)
        session.file_watcher = watcher
        thread.start()

    def _grill_model(self, workflow: Workflow) -> str:
        reviewers = workflow.nodes_of_kind("prompt_reviewer")
        if reviewers:
            return str(reviewers[0].config.get("model") or "gpt-5.6-terra")
        return "gpt-5.6-terra"

    def _apply_grill_outcome(
        self, session: WorkspaceSession, outcome: GrillOutcome
    ) -> None:
        workflow = session.workflow
        if workflow is None:
            return
        for node in workflow.nodes_of_kind("tasks_manager"):
            tasks = normalize_managed_tasks(node.config.get("tasks"))
            for task in tasks:
                rewritten = outcome.rewritten_tasks.get(str(task["id"]))
                if rewritten is not None:
                    task["prompt"] = rewritten
            node.config["tasks"] = tasks
        if outcome.rewritten_entry:
            for node in workflow.nodes_of_kind("entry"):
                node.config["text"] = outcome.rewritten_entry
        workflow.grill_summary = outcome.summary
        self._mark_dirty()
        self._commit_current_history()

    def _run_grill(self, session: WorkspaceSession) -> bool:
        """Run GrillMe. Return True when the Flow may start."""
        workflow = session.workflow
        if workflow is None:
            return False
        while True:
            dialog = GrillDialog(
                workflow,
                self._grill_model(workflow),
                workflow.resolved_workspace(session.project_path),
                self,
            )
            dialog.exec()
            if dialog.decision == "run" and dialog.outcome is not None:
                self._apply_grill_outcome(session, dialog.outcome)
                return True
            if dialog.decision == "edit" and dialog.outcome is not None:
                self._apply_grill_outcome(session, dialog.outcome)
                managers = workflow.nodes_of_kind("tasks_manager")
                if managers:
                    item = self.scene.node_items.get(managers[0].id)
                    if item is not None:
                        self.scene.clearSelection()
                        item.setSelected(True)
                self.inspector.focus_first_task()
                if (
                    QMessageBox.question(
                        self,
                        "Продовжити уточнення?",
                        "Промпти оновлено. Запустити GrillMe ще раз "
                        "із вашими правками?",
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return False
                continue
            return False

    @Slot(str, str, object)
    def _handle_run_worker_message(
        self, session_id: str, message_type: str, payload: object
    ) -> None:
        if message_type == "event":
            self._handle_run_event(session_id, payload)
        elif message_type == "completed":
            self._run_completed(session_id, payload)
        elif message_type == "failed":
            self._run_failed(session_id, str(payload))
        elif message_type == "finished":
            self._run_thread_finished(session_id)
        else:
            LOGGER.warning(
                "Unknown worker message session=%s type=%s", session_id, message_type
            )

    def _full_access_agents(self) -> list[str]:
        """Назви агентських блоків, які отримають повний доступ до диска."""
        return [
            node.title
            for node in self.scene.workflow.nodes
            if node.is_agent and node.config.get("sandbox") == "full-access"
        ]

    def stop_workflow(self) -> None:
        session = self.current_workspace
        if (
            session is not None
            and session.run_worker is not None
            and not session.stop_requested
        ):
            session.stop_requested = True
            session.run_worker.cancel()
            self._append_session_log(session, "■ Миттєва зупинка всіх агентів…")
            self._update_workspace_actions()
            self._refresh_workspace_sidebar()
            self.statusBar().showMessage(
                "Codex отримав команду перервати активну генерацію"
            )

    def show_generated_files(self) -> None:
        session = self.current_workspace
        if session is None or not session.is_loaded:
            return
        GeneratedFilesDialog(session, self).exec()

    def show_run_stats(self) -> None:
        session = self.current_workspace
        if session is None:
            return
        StatsDialog(session, self).exec()

    def show_run_results(self) -> None:
        session = self.current_workspace
        if session is None:
            return
        dialog = ResultsDialog(session, self)
        dialog.all_files_button.clicked.connect(dialog.accept)
        dialog.all_files_button.clicked.connect(self.show_generated_files)
        dialog.stats_button.clicked.connect(dialog.accept)
        dialog.stats_button.clicked.connect(self.show_run_stats)
        dialog.exec()

    @Slot(str, object)
    def _handle_run_event(self, session_id: str, event: object) -> None:
        if not isinstance(event, dict):
            LOGGER.warning("Ignoring malformed run event for %s: %r", session_id, event)
            return
        session = self._workspace(session_id)
        if session is None:
            return
        session.run_events.append(event)
        if session.id == self.current_workspace_id:
            self.current_run_events = session.run_events
        event_type = event.get("type", "event")
        node_id = event.get("node_id")
        if event_type in {"node_started", "work_review_started"} and node_id:
            session.active_file_node_id = str(node_id)
            session.active_file_iteration = max(1, int(event.get("iteration", 1)))
        if (
            event_type
            in {
                "node_finished",
                "node_failed",
                "node_cancelled",
                "work_review_finished",
                "work_review_failed",
            }
            and session.file_watcher is not None
        ):
            session.file_watcher.rescan()
        if event_type in {"node_finished", "work_review_finished"}:
            self._record_generated_file_group(session, event)
        LOGGER.info(
            "Workflow event session=%s type=%s node=%s",
            session_id,
            event_type,
            node_id or "-",
        )
        status_map = {
            "node_started": "running",
            "node_finished": "success",
            "node_failed": "failed",
            "node_cancelled": "cancelled",
            "node_skipped": "skipped",
            "node_waiting": "waiting",
            "work_review_started": "running",
            "work_review_finished": "success",
            "work_review_failed": "failed",
        }
        if node_id:
            self._update_node_runtime(session, str(node_id), event_type, event)
        if node_id and event_type == "node_stage":
            stage = max(0, int(event.get("stage", 0)))
            total = max(0, int(event.get("stage_total", 0)))
            stage_name = str(event.get("stage_name", ""))
            session.node_stages[str(node_id)] = (stage, total, stage_name)
            if session.id == self.current_workspace_id:
                self.scene.set_node_stage(str(node_id), stage, total, stage_name)
        if node_id and event_type in status_map:
            session.node_statuses[str(node_id)] = status_map[event_type]
            if session.id == self.current_workspace_id:
                self.scene.set_node_runtime(
                    str(node_id),
                    session.node_durations.get(str(node_id), 0.0),
                    session.node_started_at.get(str(node_id)),
                    session.node_duration_history.get(str(node_id), []),
                )
                self.scene.set_node_status(str(node_id), status_map[event_type])
        counts = event.get("port_counts")
        if isinstance(counts, dict):
            session.port_counts = {
                str(key): int(value) for key, value in counts.items()
            }
            if session.id == self.current_workspace_id:
                self.scene.apply_port_counts(session.port_counts)
        task_states = event.get("task_states")
        if node_id and isinstance(task_states, list):
            clean_states = [
                {
                    "id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "status": str(item.get("status", "pending")),
                    "seconds": float(item.get("seconds", 0.0) or 0.0),
                }
                for item in task_states
                if isinstance(item, dict)
            ]
            session.task_states[str(node_id)] = clean_states
            if session.id == self.current_workspace_id:
                self.scene.set_task_states(str(node_id), clean_states)
        if (
            event_type == "node_started"
            and node_id
            and session.id == self.current_workspace_id
            and session.workflow is not None
        ):
            incoming = session.workflow.incoming(str(node_id))
            active = next(
                (
                    edge
                    for edge in reversed(incoming)
                    if session.node_statuses.get(edge.source)
                    in {"success", "waiting"}
                ),
                None,
            )
            self.scene.set_active_edge(active.id if active is not None else "")
        elif event_type in {"run_finished", "run_failed", "run_cancelled"}:
            if session.id == self.current_workspace_id:
                self.scene.set_active_edge("")

        title = event.get("node_title")
        message = event.get("message", "")
        node_key = str(node_id or "")
        color = self._node_color(session, node_key)
        prefix = node_key[:6] if node_key else "Flow"
        if event_type == "node_started":
            iteration = int(event.get("iteration", 1))
            self._append_session_log(
                session,
                f"{prefix}: запуск «{title}» · прохід {iteration}",
                color=color,
            )
        elif event_type == "node_stage":
            self._append_session_log(
                session,
                f"{prefix}: {event.get('stage', 0)}/{event.get('stage_total', 6)} "
                f"· {event.get('stage_name', message)}",
                color=color,
            )
        elif event_type == "node_finished":
            result = event.get("result") or {}
            result_text = self._log_preview(result.get("text", ""))
            files = self._existing_result_files(session, result)
            file_lines = "" if not files else "\nФайли:\n" + "\n".join(files)
            self._append_session_log(
                session,
                f"{prefix}: готово за {result.get('duration_seconds', 0)} с\n"
                f"{result_text}{file_lines}",
                color=color,
                file_paths=files,
            )
        elif event_type == "node_failed":
            result = event.get("result") or {}
            self._append_session_log(
                session,
                f"{prefix}: помилка — {result.get('error', 'Помилка')}",
                color=color,
            )
            session.run_state = "failed"
        elif event_type == "node_cancelled":
            self._append_session_log(
                session, f"{prefix}: виконання перервано", color=color
            )
        elif event_type == "node_skipped":
            self._append_session_log(
                session, f"{prefix}: «{title}» пропущено", color=color
            )
        elif event_type == "node_waiting":
            self._append_session_log(
                session, f"{prefix}: очікує на відповідь", color=color
            )
        elif event_type == "node_retry":
            self._append_session_log(
                session, f"{prefix}: повтор — {message}", color=color
            )
        elif event_type == "agent_prompt":
            attachment_count = len(event.get("attachments") or [])
            self._append_session_log(
                session,
                f"{prefix}: промпт сформовано; вкладень: {attachment_count}. "
                "Повний текст зберігається у протоколі запуску.",
                color=color,
            )
        elif event_type == "agent_step":
            self._append_session_log(
                session,
                f"{prefix}: {message}",
                color=color,
            )
        elif event_type == "agent_activity":
            paths = [str(item) for item in event.get("paths", []) if str(item)]
            if session.id == self.current_workspace_id:
                self.log_panel.set_activity(str(message), color)
            if str(event.get("phase")) == "completed":
                self._append_session_log(
                    session,
                    f"{prefix}: {message}",
                    color=color,
                    file_paths=paths,
                )
        elif event_type == "intervention_required":
            request = event.get("request")
            session.pending_intervention = request if isinstance(request, dict) else {}
            session.run_state = "needs_attention"
            if session.run_directory is not None and session.checkpoint is not None:
                save_checkpoint(
                    session.run_directory,
                    session.checkpoint,
                    project_path=session.project_path,
                    request=session.pending_intervention or {},
                )
            self._append_session_log(
                session,
                f"{prefix}: ⚠ Потрібне втручання — {message}",
                color=color,
            )
            if node_id and session.id == self.current_workspace_id:
                self.scene.set_attention(str(node_id), True)
        elif event_type == "run_paused":
            session.run_state = "paused"
            self._append_session_log(session, f"Ⅱ {message}")
        elif event_type == "run_resumed":
            session.run_state = "running"
            self._append_session_log(session, f"▶ {message}")
        elif event_type == "node_config_updated":
            self._append_session_log(session, f"{prefix}: {message}", color=color)
        elif event_type == "tasks_progress":
            total = float(event.get("total_seconds", 0.0) or 0.0)
            self._append_session_log(
                session,
                f"{prefix}: {message} "
                f"({event.get('completed_count', 0)}/{event.get('task_count', 0)}"
                f" · сумарно {total:.1f} с)",
                color=color,
            )
        elif event_type == "task_exhausted":
            self._append_session_log(session, f"{prefix}: ✖ {message}", color=color)
        elif event_type in {
            "work_review_started",
            "work_review_finished",
            "work_review_failed",
        }:
            files = [
                str(Path(str(event[key])).resolve())
                for key in ("report_path", "protocol_path")
                if event.get(key) and Path(str(event[key])).is_file()
            ]
            self._append_session_log(
                session,
                f"{prefix}: 🔍 {message}",
                color=color,
                file_paths=files,
            )
        elif event_type == "run_cancelled":
            session.run_state = "cancelled"
            session.stop_requested = False
        elif message:
            self._append_session_log(session, message)
        if event_type in {
            "node_finished",
            "node_failed",
            "node_cancelled",
            "run_finished",
            "run_failed",
            "run_cancelled",
        } and session.id == self.current_workspace_id:
            self.log_panel.set_activity("", "")
        if event_type in {
            "node_finished",
            "node_failed",
            "node_cancelled",
            "work_review_finished",
            "work_review_failed",
            "run_finished",
            "run_failed",
            "run_cancelled",
        }:
            session.active_file_node_id = ""
            session.active_file_iteration = 0
        if event_type in WORKSPACE_UI_EVENT_TYPES:
            self._refresh_workspace_sidebar()
            if session.id == self.current_workspace_id:
                self._update_workspace_actions()

    @staticmethod
    def _update_node_runtime(
        session: WorkspaceSession,
        node_id: str,
        event_type: str,
        event: dict[str, Any],
    ) -> None:
        if event_type in {"node_started", "work_review_started"}:
            session.node_durations[node_id] = 0.0
            session.node_started_at[node_id] = time.monotonic()
            return
        if event_type not in {
            "node_finished",
            "node_failed",
            "node_cancelled",
            "node_waiting",
            "work_review_finished",
            "work_review_failed",
        }:
            return

        started_at = session.node_started_at.pop(node_id, None)
        result = event.get("result")
        raw_duration = (
            result.get("duration_seconds") if isinstance(result, dict) else None
        )
        try:
            duration = float(raw_duration) if raw_duration is not None else 0.0
        except (TypeError, ValueError):
            duration = 0.0
        if (raw_duration is None or duration <= 0) and started_at is not None:
            duration = max(0.0, time.monotonic() - started_at)
        duration = max(0.0, duration)
        session.node_durations[node_id] = duration
        session.node_duration_history.setdefault(node_id, []).append(duration)

    @Slot(str, object)
    def _run_completed(self, session_id: str, outputs: object) -> None:
        session = self._workspace(session_id)
        if session is None:
            return
        if isinstance(outputs, RunCheckpoint):
            session.checkpoint = outputs
        if session.run_state == "needs_attention":
            session.stop_requested = False
            if session.run_directory is not None and session.checkpoint is not None:
                save_checkpoint(
                    session.run_directory,
                    session.checkpoint,
                    project_path=session.project_path,
                    request=session.pending_intervention or {},
                )
            self._save_run_log_for_session(session, "needs_attention")
            self._refresh_workspace_sidebar()
            return

        cancelled = any(
            event.get("type") == "run_cancelled" for event in session.run_events
        )
        failed = session.run_state == "failed" or any(
            event.get("type") == "run_failed" for event in session.run_events
        )
        if cancelled:
            session.run_state = "cancelled"
            status, message = "cancelled", "■ Flow зупинено"
        elif failed:
            session.run_state = "failed"
            status, message = "failed", "■ Flow завершився помилкою"
        else:
            failed_tasks = [
                state
                for states in session.task_states.values()
                for state in states
                if state.get("status") == "failed"
            ]
            if failed_tasks:
                session.run_state = "completed_with_failures"
                status = "completed_with_failures"
                message = f"■ Виконано, провалено завдань: {len(failed_tasks)}"
            else:
                session.run_state = "completed"
                status, message = "success", "■ Виконання завершено"
            session.unread_result = session.id != self.current_workspace_id
        self._append_session_log(session, message)
        if session.id == self.current_workspace_id:
            self.scene.set_active_edge("")
            self.log_panel.set_activity("", "")
        session.stop_requested = False
        if session.run_directory is not None:
            clear_checkpoint(session.run_directory)
        log_path = self._save_run_log_for_session(session, status)
        LOGGER.info(
            "Workflow finished session=%s status=%s log=%s",
            session_id,
            status,
            log_path,
        )
        if session.id == self.current_workspace_id:
            self.statusBar().showMessage(
                f"{message.removeprefix('■ ')}. Журнал: {log_path}", 9000
            )
        if status in {"success", "failed", "completed_with_failures"}:
            self._notify_user(
                session,
                "FlowAI: виконання завершено",
                f"{session.display_name}: {message.removeprefix('■ ')}",
                warning=status in {"failed", "completed_with_failures"},
            )
        self._refresh_workspace_sidebar()

    @Slot(str, str)
    def _run_failed(self, session_id: str, message: str) -> None:
        session = self._workspace(session_id)
        if session is None:
            return
        self._finalize_running_runtimes(session)
        session.run_state = "failed"
        session.active_file_node_id = ""
        session.active_file_iteration = 0
        if session.id == self.current_workspace_id:
            self.scene.set_active_edge("")
            self.log_panel.set_activity("", "")
        session.stop_requested = False
        if session.run_directory is not None:
            clear_checkpoint(session.run_directory)
        self._append_session_log(session, f"Критична помилка: {message}")
        self._save_run_log_for_session(session, "failed", message)
        LOGGER.error("Workflow failed session=%s: %s", session_id, message)
        self._notify_user(
            session,
            "FlowAI: помилка виконання",
            f"{session.display_name}: {message}",
            warning=True,
        )
        self._refresh_workspace_sidebar()
        if session.id == self.current_workspace_id:
            QMessageBox.critical(self, "Помилка виконання", message)

    def _run_thread_finished(self, session_id: str) -> None:
        session = self._workspace(session_id)
        if session is None:
            return
        if session.file_watcher is not None:
            session.file_watcher.stop()
            session.file_watcher = None
        session.run_thread = None
        session.run_worker = None
        session.active_file_node_id = ""
        session.active_file_iteration = 0
        session.stop_requested = False
        self._update_workspace_actions()
        self._refresh_workspace_sidebar()
        if self.current_user is not None:
            QTimer.singleShot(0, self.refresh_account)
        if session.run_state == "needs_attention" and session.pending_intervention:
            request = session.pending_intervention
            if request.get("type") == "calibration":
                report = request.get("report") or {}
                task = str(report.get("task_title") or "завдання")
                self._notify_user(
                    session,
                    "FlowAI: рев'ювер відхилив роботу",
                    f"{session.display_name}: «{task}» повернуто на переробку",
                    node_id=str(request.get("node_id") or ""),
                    warning=True,
                    actions=[ToastAction("edits", "Показати правки")],
                )
            else:
                self._notify_user(
                    session,
                    "FlowAI очікує на підтвердження",
                    f"{session.display_name}: "
                    f"{request.get('question', 'Потрібна відповідь')}",
                    node_id=str(request.get("node_id") or ""),
                    warning=True,
                )
        if (
            session.id == self.current_workspace_id
            and session.run_state == "needs_attention"
            and session.pending_intervention is not None
            and session.pending_intervention.get("type") != "result_confirmation"
        ):
            QTimer.singleShot(0, self._show_pending_intervention)

    def account_button_clicked(self) -> None:
        if self.current_user is None:
            self.start_login()
            return
        answer = QMessageBox.question(
            self,
            "Вийти з ChatGPT?",
            f"Ви дійсно хочете вийти з акаунта «{self.current_user.nickname}»?\n\n"
            "Це завершить спільну сесію Codex на цьому комп’ютері.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_account_action("logout")

    def start_login(self) -> None:
        dialog = ChatGPTLoginDialog(self)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted and dialog.user is not None:
            self.current_user = dialog.user
            self._update_account_button()
            self.statusBar().showMessage(
                f"Вхід виконано: {dialog.user.nickname}",
                7000,
            )
        elif dialog.error_message:
            QMessageBox.warning(self, "Не вдалося увійти", dialog.error_message)

    def refresh_account(self) -> None:
        self._run_account_action("read")

    def _run_account_action(self, action: str) -> None:
        if self.account_thread is not None:
            return
        self.account_button.setEnabled(False)
        if action == "read":
            self.account_button.setText("Перевіряємо…")
        else:
            self.account_button.setText("Виходимо…")

        thread = QThread(self)
        worker = AccountWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._account_action_completed)
        worker.failed.connect(self._account_action_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._account_thread_finished)
        thread.finished.connect(worker.deleteLater)
        self.account_thread = thread
        self.account_worker = worker
        thread.start()

    @Slot(str, object)
    def _account_action_completed(self, action: str, result: object) -> None:
        if action == "read":
            self.current_user = result if isinstance(result, CodexUser) else None
        elif action == "logout":
            self.current_user = None
            self.statusBar().showMessage("Вихід із ChatGPT виконано", 7000)
        self._update_account_button()

    @Slot(str, str)
    def _account_action_failed(self, action: str, message: str) -> None:
        self._update_account_button()
        if action == "logout":
            QMessageBox.warning(self, "Не вдалося вийти", message)
        else:
            self.account_button.setToolTip(f"Не вдалося перевірити Codex: {message}")

    @Slot()
    def _account_thread_finished(self) -> None:
        thread = self.account_thread
        self.account_thread = None
        self.account_worker = None
        self.account_button.setEnabled(True)
        if thread is not None:
            thread.deleteLater()

    def _update_account_button(self) -> None:
        if self.current_user is None:
            self.account_button.setText("Увійти в ChatGPT")
            self.account_button.setIcon(account_avatar_icon("C", logged_in=False))
            self.account_button.setToolTip("Увійти через обліковий запис ChatGPT")
            return
        user = self.current_user
        remaining = user.remaining_percent
        percent_badge = f" · {remaining}%" if remaining is not None else ""
        self.account_button.setText(f"{user.nickname}{percent_badge}")
        self.account_button.setIcon(account_avatar_icon(user.initial, logged_in=True))
        email_line = user.email
        if remaining is not None:
            email_line += f" · залишилось {remaining}%"
        tooltip_lines = [email_line]
        if user.plan_type:
            tooltip_lines.append(f"Тариф: {user.plan_type}")
        if user.rate_limits:
            tooltip_lines.append("")
            tooltip_lines.append("Ліміти Codex:")
            tooltip_lines.extend(
                self._rate_limit_tooltip(limit) for limit in user.rate_limits
            )
        tooltip_lines.extend(("", "Натисніть, щоб вийти"))
        self.account_button.setToolTip("\n".join(tooltip_lines))

    @staticmethod
    def _rate_limit_tooltip(limit: CodexRateLimit) -> str:
        duration = MainWindow._format_limit_duration(limit.window_duration_mins)
        line = f"{limit.display_name}"
        if duration:
            line += f" · {duration}"
        line += f": залишилось {limit.remaining_percent}%"
        if limit.resets_at is not None:
            try:
                reset_time = datetime.fromtimestamp(limit.resets_at).astimezone()
            except (OSError, OverflowError, ValueError):
                pass
            else:
                line += f" · скидання {reset_time:%d.%m %H:%M}"
        return line

    @staticmethod
    def _format_limit_duration(minutes: int | None) -> str:
        if minutes is None or minutes <= 0:
            return ""
        if minutes % (24 * 60) == 0:
            return f"{minutes // (24 * 60)} дн."
        if minutes % 60 == 0:
            return f"{minutes // 60} год."
        return f"{minutes} хв."

    @property
    def current_workspace(self) -> WorkspaceSession | None:
        if self.current_workspace_id is None:
            return None
        return self._workspace(self.current_workspace_id)

    def _workspace(self, session_id: str) -> WorkspaceSession | None:
        return next(
            (
                session
                for session in self.workspace_sessions
                if session.id == session_id
            ),
            None,
        )

    def select_workspace(self, session_id: str) -> None:
        session = self._workspace(session_id)
        if session is None:
            return
        if not session.is_loaded and not self._load_workspace_session(session):
            self._refresh_workspace_sidebar()
            return

        if session.id != self.current_workspace_id:
            self._capture_current_workspace()
        self.current_workspace_id = session.id
        if session.history_state is None:
            self._initialize_workspace_history(session, saved=not session.dirty)
        had_unread_result = session.unread_result
        session.unread_result = False
        self.project_path = session.project_path
        self.dirty = session.dirty
        self.current_run_events = session.run_events
        self.scene.set_workflow(session.workflow or Workflow())
        self.inspector.set_workflow(self.scene.workflow)
        self.scene.apply_node_runtimes(
            session.node_durations,
            session.node_started_at,
            session.node_duration_history,
        )
        self.scene.apply_node_statuses(session.node_statuses)
        self.scene.apply_node_stages(session.node_stages)
        self.scene.apply_task_states(session.task_states)
        if session.checkpoint is not None:
            session.port_counts = dict(session.checkpoint.port_counts)
        if session.port_counts:
            self.scene.apply_port_counts(session.port_counts)
        if session.run_state == "needs_attention" and session.pending_intervention:
            waiting_id = str(session.pending_intervention.get("node_id") or "")
            if waiting_id:
                self.scene.set_attention(waiting_id, True)
        self._render_session_log(session)
        self.central_stack.setCurrentWidget(self.view)

        if session.canvas_transform is not None:
            self.view.setTransform(session.canvas_transform)
            self.view.horizontalScrollBar().setValue(session.horizontal_scroll)
            self.view.verticalScrollBar().setValue(session.vertical_scroll)
        else:
            QTimer.singleShot(0, self._fit_graph)
        if session.selected_object is not None:
            object_type, object_id = session.selected_object
            item = (
                self.scene.node_items.get(object_id)
                if object_type == "node"
                else self.scene.edge_items.get(object_id)
            )
            if item is not None:
                item.setSelected(True)

        self._update_title()
        self._update_workspace_actions()
        self._update_history_actions()
        self._refresh_workspace_sidebar()
        if (
            session.run_state == "needs_attention"
            and session.run_thread is None
            and session.pending_intervention is not None
            and session.pending_intervention.get("type") != "result_confirmation"
        ):
            QTimer.singleShot(0, self._show_pending_intervention)
        if had_unread_result and session.run_state in {
            "completed",
            "completed_with_failures",
            "failed",
        }:
            QTimer.singleShot(0, self.show_run_results)

    def _capture_current_workspace(self) -> None:
        session = self.current_workspace
        if session is None or not session.is_loaded:
            return
        self._commit_current_history()
        session.workflow = self.scene.workflow
        session.project_path = self.project_path
        session.dirty = self.dirty
        session.node_statuses = self.scene.node_statuses()
        session.canvas_transform = self.view.transform()
        session.horizontal_scroll = self.view.horizontalScrollBar().value()
        session.vertical_scroll = self.view.verticalScrollBar().value()
        session.selected_object = None
        selected = self.scene.selectedItems()
        if len(selected) == 1:
            model = getattr(selected[0], "model", None)
            if isinstance(model, FlowNode):
                session.selected_object = ("node", model.id)
            elif isinstance(model, FlowEdge):
                session.selected_object = ("edge", model.id)

    def _load_workspace_session(self, session: WorkspaceSession) -> bool:
        if session.project_path is None:
            return False
        if not session.project_path.exists():
            QMessageBox.warning(
                self,
                "Файл не знайдено",
                f"Не вдалося знайти Flow:\n{session.project_path}",
            )
            return False
        try:
            session.workflow = load_workflow(session.project_path)
        except UnsupportedFlowFormat as exc:
            QMessageBox.warning(self, "Формат Flow не підтримується", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - user-supplied workflow file
            QMessageBox.critical(self, "Не вдалося відкрити Flow", str(exc))
            return False
        if session.custom_name:
            session.workflow.name = session.display_name
            session.dirty = True
        else:
            session.display_name = session.workflow.name
        session.load_state = "loaded"
        if not session.custom_name:
            session.dirty = False
        self._initialize_workspace_history(session, saved=not session.dirty)
        session.run_state = "idle"
        self._restore_pending_run(session)
        self._persist_workspace_registry()
        return True

    def _show_no_workspace(self) -> None:
        self.history_timer.stop()
        self.current_workspace_id = None
        self.project_path = None
        self.dirty = False
        self.current_run_events = []
        self.scene.set_workflow(Workflow())
        self.inspector.set_workflow(None)
        self.log_panel.clear()
        self.log_panel.set_activity("", "")
        self.inspector.set_object(None)
        self.central_stack.setCurrentWidget(self.empty_workspace_page)
        self._update_title()
        self._update_workspace_actions()
        self._update_history_actions()
        self._refresh_workspace_sidebar()

    def _restore_workspace_registry(self) -> None:
        raw = self.settings.value("workspace_registry", "[]")
        try:
            entries = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            entries = []
        if not isinstance(entries, list):
            return
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            path = relocated_project_path(Path(str(entry["path"])))
            key = str(path).casefold()
            if key in seen:
                continue
            seen.add(key)
            self.workspace_sessions.append(
                WorkspaceSession(
                    id=str(entry.get("id") or "") or uuid4().hex,
                    display_name=str(entry.get("display_name") or path.stem),
                    project_path=path,
                    custom_name=bool(entry.get("custom_name", False)),
                )
            )

    def _persist_workspace_registry(self) -> None:
        if not self.persist_workspace_registry:
            return
        entries = [
            entry
            for session in self.workspace_sessions
            if (entry := session.registry_entry()) is not None
        ]
        self.settings.setValue(
            "workspace_registry", json.dumps(entries, ensure_ascii=False)
        )

    def _last_directory(self) -> Path:
        if self.persist_layout:
            raw = str(self.settings.value("last_open_dir", "") or "")
            if raw:
                candidate = Path(raw)
                if candidate.is_dir():
                    return candidate
        current = self.current_workspace
        if current is not None and current.project_path is not None:
            return current.project_path.parent
        return Path.cwd()

    def _remember_directory(self, directory: Path) -> None:
        if not self.persist_layout:
            return
        self.settings.setValue("last_open_dir", str(directory))

    def _restore_layout(self) -> None:
        if self.saved_geometry:
            self.restoreGeometry(self.saved_geometry)
        if self.saved_window_state:
            self.restoreState(self.saved_window_state)
        for dock in self._resizable_docks():
            dock.sync_resize_mode(self.dockWidgetArea(dock))
        if not self.persist_layout:
            return
        raw_widths = str(self.settings.value("dock_horizontal_widths", "") or "")
        try:
            widths = json.loads(raw_widths) if raw_widths else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            widths = {}
        if not isinstance(widths, dict):
            return
        for dock in self._resizable_docks():
            width = widths.get(dock.objectName())
            if isinstance(width, int):
                dock.set_docked_width(width)

    def _enable_layout_autosave(self) -> None:
        for dock in self._resizable_docks():
            dock.dockLocationChanged.connect(self._schedule_layout_persist)
            dock.topLevelChanged.connect(self._schedule_layout_persist)
            dock.visibilityChanged.connect(self._schedule_layout_persist)
            dock.layout_geometry_changed.connect(self._schedule_layout_persist)

    def _schedule_layout_persist(self, *args: Any) -> None:
        if not getattr(self, "persist_layout", False) or not getattr(
            self, "_layout_restore_complete", False
        ):
            return
        timer = getattr(self, "layout_save_timer", None)
        if timer is not None:
            timer.start()

    def _persist_layout(self) -> None:
        if not self.persist_layout:
            return
        self.layout_save_timer.stop()
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        widths = {
            dock.objectName(): dock._horizontal_width
            for dock in self._resizable_docks()
            if dock._horizontal_width is not None
        }
        self.settings.setValue("dock_horizontal_widths", json.dumps(widths))
        self.settings.sync()

    def moveEvent(self, event: Any) -> None:
        super().moveEvent(event)
        self._schedule_layout_persist()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._schedule_layout_persist()

    def _resizable_docks(self) -> tuple[ResizableDockWidget, ...]:
        return tuple(
            dock
            for dock in (
                self.workspace_dock,
                self.nodes_dock,
                self.inspector_dock,
                self.log_dock,
            )
            if isinstance(dock, ResizableDockWidget)
        )

    def _refresh_workspace_sidebar(self) -> None:
        self.workspace_sidebar.set_sessions(
            self.workspace_sessions,
            self.current_workspace_id,
        )

    def _workspace_action(self, session_id: str, action: str) -> None:
        session = self._workspace(session_id)
        if session is None:
            return
        if action == "open":
            self.select_workspace(session_id)
            return
        if action == "rename":
            self.rename_workspace(session_id)
            return
        if action in {"save", "save_as"}:
            self.select_workspace(session_id)
            if self.current_workspace_id == session_id:
                self.save_workflow() if action == "save" else self.save_workflow_as()
            return
        if action == "reveal":
            target = session.project_path.parent if session.project_path else Path.cwd()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            return
        if session.run_thread is not None:
            QMessageBox.warning(
                self,
                "Flow виконується",
                "Спочатку зупиніть цей Flow і дочекайтеся завершення поточної операції.",
            )
            return
        if session.run_state == "needs_attention":
            QMessageBox.warning(
                self,
                "Flow очікує на відповідь",
                "Надайте відповідь або залиште середовище у списку для продовження пізніше.",
            )
            return
        if session.dirty:
            self.select_workspace(session_id)
            if not self._confirm_discard():
                return
        if action == "unload":
            session.workflow = None
            session.load_state = "unloaded"
            session.undo_history.clear()
            session.redo_history.clear()
            session.history_state = None
            session.saved_history_state = None
            session.log_text = ""
            session.node_statuses.clear()
            session.node_durations.clear()
            session.node_duration_history.clear()
            session.node_started_at.clear()
            session.node_stages.clear()
            session.task_states.clear()
            session.port_counts.clear()
            session.checkpoint = None
            if session.id == self.current_workspace_id:
                self._show_no_workspace()
        elif action == "remove":
            self.workspace_sessions = [
                item for item in self.workspace_sessions if item.id != session.id
            ]
            if session.id == self.current_workspace_id:
                self._show_no_workspace()
        self._persist_workspace_registry()
        self._refresh_workspace_sidebar()

    def rename_node(self, node: object) -> None:
        if not isinstance(node, FlowNode):
            return
        name, accepted = QInputDialog.getText(
            self,
            "Перейменувати блок",
            "Нова назва:",
            text=node.title,
        )
        cleaned = name.strip()
        if not accepted or not cleaned or cleaned == node.title:
            return
        node.title = cleaned
        self.scene.refresh_item(node)
        self.inspector.set_object(node)
        self._mark_dirty()

    def rename_workspace(self, session_id: str) -> None:
        session = self._workspace(session_id)
        if session is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Перейменувати середовище",
            "Нова назва:",
            text=session.display_name,
        )
        cleaned = name.strip()
        if not accepted or not cleaned or cleaned == session.display_name:
            return
        self._set_workspace_name(session, cleaned)

    def _set_workspace_name(self, session: WorkspaceSession, name: str) -> None:
        if session.workflow is not None and session.history_state is None:
            self._initialize_workspace_history(session, saved=not session.dirty)
        session.display_name = name
        session.custom_name = True
        if session.workflow is not None:
            session.workflow.name = name
            session.dirty = True
        if session.id == self.current_workspace_id:
            self._mark_dirty()
        else:
            self._commit_session_history(session)
        self._persist_workspace_registry()
        self._refresh_workspace_sidebar()

    def _show_calibration(
        self, session: WorkspaceSession, request: dict[str, Any]
    ) -> None:
        """Показати причину відхилення та запропоновані правки."""
        workflow = session.workflow
        if workflow is None:
            return
        report = CalibrationReport.from_dict(request.get("report") or {})
        node_id = str(request.get("node_id") or "")
        executor = next(iter(workflow.nodes_of_kind("executor")), None)
        dialog = CalibrationDialog(
            report,
            self,
            models=self.calibration_models,
            default_model=str(
                executor.config.get("model", "gpt-5.6-terra")
                if executor
                else "gpt-5.6-terra"
            ),
            default_effort=str(
                executor.config.get("reasoning_effort", "medium")
                if executor
                else "medium"
            ),
        )
        dialog.tabs.setCurrentIndex(self.calibration_open_tab)
        self.calibration_open_tab = 0
        self.intervention_dialog_open = True
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.intervention_dialog_open = False

        directory = session.run_directory
        if directory is not None:
            save_report(dialog.report, directory)

        if not accepted or dialog.decision == "stop":
            session.run_state = "cancelled"
            session.pending_intervention = None
            if node_id:
                self.scene.set_attention(node_id, False)
            self._append_session_log(session, "■ Flow зупинено користувачем")
            self._save_run_log_for_session(session, "cancelled")
            if directory is not None:
                clear_checkpoint(directory)
            self._refresh_workspace_sidebar()
            return

        if dialog.decision in {"apply", "regenerate"}:
            results = apply_edits(dialog.report, workflow)
            for item in results:
                marker = "✔" if item.ok else "✖"
                self._append_session_log(
                    session, f"{marker} {item.edit.label}: {item.message}"
                )
            if dialog.pinned_skills and executor is not None:
                pinned = pin_skills(
                    workflow,
                    executor.id,
                    dialog.pinned_skills,
                    skills_root=SKILLS_ROOT,
                )
                if pinned:
                    self._append_session_log(
                        session, "Закріплено скіли: " + ", ".join(pinned)
                    )
            session.dirty = True
            self._mark_dirty()

        if dialog.decision == "regenerate":
            self._start_regeneration(session, dialog)
            return

        action = "retry_task" if dialog.decision == "apply" else "continue"
        session.intervention_responses[node_id] = {"action": action}
        session.pending_intervention = None
        session.run_state = "idle"
        if node_id:
            self.scene.set_attention(node_id, False)
        self._refresh_workspace_sidebar()
        self.run_workflow(resume=True)

    def _start_regeneration(
        self, session: WorkspaceSession, dialog: CalibrationDialog
    ) -> None:
        """Перезібрати промпт з AI, спираючись на звіт калібрації."""
        workflow = session.workflow
        if workflow is None:
            return
        generated = [
            str(path)
            for group in session.generated_file_groups
            for path in list(group.get("intermediate", []))
            + list(group.get("result", []))
        ]
        grill = GrillDialog(
            workflow,
            dialog.model,
            workflow.resolved_workspace(session.project_path),
            self,
            reasoning_effort=dialog.effort,
            calibration=dialog.report,
            generated_files=list(dict.fromkeys(generated)),
        )
        accepted = grill.exec() == QDialog.DialogCode.Accepted
        if accepted and grill.outcome is not None:
            self._apply_grill_outcome(session, grill.outcome)
        node_id = str(dialog.report.node_id)
        session.intervention_responses[node_id] = {"action": "retry_task"}
        session.pending_intervention = None
        session.run_state = "idle"
        if node_id:
            self.scene.set_attention(node_id, False)
        self._refresh_workspace_sidebar()
        self.run_workflow(resume=True)

    def _show_pending_intervention(self, user_initiated: bool = False) -> None:
        session = self.current_workspace
        if (
            session is None
            or session.run_state != "needs_attention"
            or session.pending_intervention is None
            or session.run_thread is not None
            or self.intervention_dialog_open
        ):
            return
        request = session.pending_intervention
        if request.get("type") == "calibration":
            self._show_calibration(session, request)
            return
        node_id = str(request.get("node_id") or "")
        limit_request = request.get("type") == "result_limit"
        confirmation_request = request.get("type") == "result_confirmation"
        if confirmation_request and not user_initiated:
            return
        if not limit_request and not confirmation_request:
            self._append_session_log(
                session, f"⚠ Невідомий тип запиту: {request.get('type')}"
            )
            return
        dialog: ResultLimitDialog | ResultConfirmationDialog
        dialog = (
            ResultLimitDialog(request, self)
            if limit_request
            else ResultConfirmationDialog(request, self)
        )
        self.intervention_dialog_open = True
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.intervention_dialog_open = False
        if not accepted:
            # Користувач вибрав «Зупинити Flow».
            session.run_state = "cancelled"
            session.pending_intervention = None
            if session.run_directory is not None:
                clear_checkpoint(session.run_directory)
            if node_id:
                self.scene.set_attention(node_id, False)
            self._append_session_log(session, "■ Flow зупинено користувачем")
            self._save_run_log_for_session(session, "cancelled")
            self._refresh_workspace_sidebar()
            return
        if not node_id:
            return
        if dialog.response is None:
            return
        session.intervention_responses[node_id] = dialog.response
        session.pending_intervention = None
        session.run_state = "idle"
        self.scene.set_attention(node_id, False)
        self._refresh_workspace_sidebar()
        self.run_workflow(resume=True)

    def _restore_pending_run(self, session: WorkspaceSession) -> None:
        """Підняти призупинений запуск після перезапуску FlowAI."""
        if session.project_path is None or session.checkpoint is not None:
            return
        directory = find_pending_run(session.project_path)
        if directory is None:
            return
        restored = load_checkpoint(directory)
        if restored is None:
            return
        checkpoint, request = restored
        session.checkpoint = checkpoint
        session.run_directory = directory
        session.pending_intervention = request or None
        session.run_state = "needs_attention"
        node_id = str(request.get("node_id") or "")
        if node_id and session.id == self.current_workspace_id:
            self.scene.set_attention(node_id, True)
        self._append_session_log(
            session, f"■ Відновлено призупинений запуск: {directory}"
        )
        self._refresh_workspace_sidebar()

    @staticmethod
    def _workflow_snapshot(workflow: Workflow) -> dict[str, Any]:
        return copy.deepcopy(workflow.to_dict())

    def _initialize_workspace_history(
        self, session: WorkspaceSession, *, saved: bool
    ) -> None:
        if session.workflow is None:
            return
        state = self._workflow_snapshot(session.workflow)
        session.undo_history.clear()
        session.redo_history.clear()
        session.history_state = copy.deepcopy(state)
        session.saved_history_state = copy.deepcopy(state) if saved else None

    def _commit_session_history(self, session: WorkspaceSession) -> None:
        if session.workflow is None:
            return
        current = self._workflow_snapshot(session.workflow)
        if session.history_state is None:
            session.history_state = copy.deepcopy(current)
            return
        if current == session.history_state:
            return
        session.undo_history.append(copy.deepcopy(session.history_state))
        if len(session.undo_history) > HISTORY_LIMIT:
            del session.undo_history[:-HISTORY_LIMIT]
        session.history_state = copy.deepcopy(current)
        session.redo_history.clear()

    def _commit_current_history(self) -> None:
        self.history_timer.stop()
        session = self.current_workspace
        if session is None:
            return
        session.workflow = self.scene.workflow
        self._commit_session_history(session)
        self._update_history_actions()

    def _apply_history_state(
        self, session: WorkspaceSession, state: dict[str, Any]
    ) -> None:
        workflow = Workflow.from_dict(copy.deepcopy(state))
        session.workflow = workflow
        session.history_state = self._workflow_snapshot(workflow)
        session.display_name = workflow.name
        session.selected_object = None
        session.dirty = (
            session.saved_history_state is None
            or session.history_state != session.saved_history_state
        )
        if session.id == self.current_workspace_id:
            self.scene.set_workflow(workflow)
            self.scene.apply_node_runtimes(
                session.node_durations,
                session.node_started_at,
                session.node_duration_history,
            )
            self.scene.apply_node_statuses(session.node_statuses)
            self.scene.apply_node_stages(session.node_stages)
            self.scene.apply_task_states(session.task_states)
            self.dirty = session.dirty
            self._update_title()
        self._persist_workspace_registry()
        self._refresh_workspace_sidebar()
        self._update_history_actions()

    def undo(self) -> None:
        session = self.current_workspace
        if session is None or session.run_thread is not None:
            return
        self._commit_current_history()
        if not session.undo_history or session.workflow is None:
            return
        current = self._workflow_snapshot(session.workflow)
        target = session.undo_history.pop()
        session.redo_history.append(current)
        self._apply_history_state(session, target)
        self.statusBar().showMessage("Останню зміну скасовано", 2500)

    def redo(self) -> None:
        session = self.current_workspace
        if (
            session is None
            or session.run_thread is not None
            or not session.redo_history
            or session.workflow is None
        ):
            return
        current = self._workflow_snapshot(session.workflow)
        target = session.redo_history.pop()
        session.undo_history.append(current)
        self._apply_history_state(session, target)
        self.statusBar().showMessage("Зміну повторено", 2500)

    def _update_history_actions(self) -> None:
        if not hasattr(self, "undo_action"):
            return
        session = self.current_workspace
        available = bool(session and session.is_loaded and session.run_thread is None)
        has_pending_change = bool(
            available
            and session
            and session.workflow is not None
            and session.history_state is not None
            and self._workflow_snapshot(session.workflow) != session.history_state
        )
        self.undo_action.setEnabled(
            available and bool(session and (session.undo_history or has_pending_change))
        )
        self.redo_action.setEnabled(
            available and bool(session and session.redo_history)
        )

    def _inspector_changed(self, model: FlowNode | FlowEdge) -> None:
        session = self.current_workspace
        if (
            isinstance(model, FlowNode)
            and model.kind == "result"
            and session is not None
            and session.run_worker is not None
        ):
            session.run_worker.update_result_config(
                model.id,
                {
                    "true_limit": int(model.config.get("true_limit", 1)),
                    "false_limit": int(model.config.get("false_limit", 3)),
                    "task_attempt_limit": int(
                        model.config.get("task_attempt_limit", 2)
                    ),
                    "wait_for_confirmation": bool(
                        model.config.get("wait_for_confirmation", False)
                    ),
                },
            )
        self.scene.refresh_item(model)
        if session is not None:
            self.scene.apply_port_counts(session.port_counts)
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        session = self.current_workspace
        if session is None:
            return
        self.dirty = True
        session.dirty = True
        session.workflow = self.scene.workflow
        session.redo_history.clear()
        self.history_timer.start()
        self._update_title()
        self._update_history_actions()
        self._refresh_workspace_sidebar()

    def _update_title(self) -> None:
        session = self.current_workspace
        if session is None:
            self.setWindowTitle("FlowAI")
            return
        marker = " *" if self.dirty else ""
        self.setWindowTitle(f"FlowAI — {self.scene.workflow.name}{marker}")

    def _update_workspace_actions(self) -> None:
        session = self.current_workspace
        loaded = session is not None and session.is_loaded
        running = bool(session and session.run_thread is not None)
        waiting = bool(session and session.run_state == "needs_attention")
        self.save_action.setEnabled(loaded)
        self.settings_action.setEnabled(loaded and not running)
        self.edit_flow_action.setEnabled(loaded and not running and not waiting)
        self.run_action.setEnabled(loaded and not running and not waiting)
        self.stop_action.setEnabled(
            loaded and running and bool(session and not session.stop_requested)
        )
        self.files_action.setEnabled(loaded)
        self.stats_action.setEnabled(bool(loaded and session and session.run_events))
        self.inspector.setEnabled(loaded)
        self.inspector.set_execution_locked(running)
        for button in self.node_buttons:
            button.setEnabled(loaded and not running)
        self._update_history_actions()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Незбережені зміни",
            "Зберегти зміни перед продовженням?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_workflow()
        return True

    def _new_run_directory(self, session: WorkspaceSession) -> Path:
        base = session.project_path.parent if session.project_path else Path.cwd()
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        return base / "runs" / stamp

    def _fit_graph(self) -> None:
        if self.current_workspace is not None and self.scene.items():
            self.view.fitInView(
                self.scene.itemsBoundingRect().adjusted(-80, -80, 80, 80),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def _clear_current_log(self) -> None:
        self.log_panel.clear()
        session = self.current_workspace
        if session is not None:
            session.log_text = ""
            session.log_entries.clear()

    def _intermediate_file(self, session_id: str, path: str) -> None:
        session = self._workspace(session_id)
        if (
            session is None
            or session.workflow is None
            or not session.active_file_node_id
        ):
            return
        node = session.workflow.find(session.active_file_node_id)
        if node is None:
            return
        iteration = max(1, session.active_file_iteration)
        group = next(
            (
                candidate
                for candidate in reversed(session.generated_file_groups)
                if str(candidate.get("node_id") or "") == node.id
                and int(candidate.get("iteration", 1)) == iteration
            ),
            None,
        )
        if group is None:
            group = {
                "node_id": node.id,
                "node_title": node.title,
                "iteration": iteration,
                "color": NODE_COLORS.get(node.kind, "#CBD5E1"),
                "intermediate": [],
                "result": [],
            }
            session.generated_file_groups.append(group)
        intermediate = group.setdefault("intermediate", [])
        if path in intermediate:
            return
        intermediate.append(path)
        self._append_session_log(
            session,
            f"Проміжний файл: {path}",
            color=str(group.get("color") or ""),
            file_paths=[path],
        )

    def _append_session_log(
        self,
        session: WorkspaceSession,
        text: str,
        *,
        color: str = "",
        file_paths: list[str] | None = None,
    ) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {text}\n"
        log_entry = {
            "timestamp": timestamp,
            "text": text,
            "color": color,
            "file_paths": list(file_paths or []),
        }
        session.log_entries.append(log_entry)
        session.log_text += entry
        trimmed = len(session.log_text) > MAX_UI_LOG_CHARS
        if trimmed:
            session.log_text = (
                "[Журнал скорочено; повні дані дивіться у файлі запуску]\n"
                + session.log_text[-MAX_UI_LOG_CHARS:]
            )
            retained: list[dict[str, Any]] = []
            size = 0
            for candidate in reversed(session.log_entries):
                candidate_size = len(str(candidate.get("text", ""))) + 12
                if retained and size + candidate_size > MAX_UI_LOG_CHARS:
                    break
                retained.append(candidate)
                size += candidate_size
            session.log_entries = list(reversed(retained))
        if session.id == self.current_workspace_id:
            if trimmed:
                self._render_session_log(session)
            else:
                self.log_panel.append_entry(log_entry)

    def _render_session_log(self, session: WorkspaceSession) -> None:
        self.log_panel.render_entries(session.log_entries)

    @staticmethod
    def _node_color(session: WorkspaceSession, node_id: str) -> str:
        workflow = session.workflow
        node = workflow.find(node_id) if workflow is not None and node_id else None
        return NODE_COLORS.get(node.kind, "#CBD5E1") if node is not None else ""

    @staticmethod
    def _existing_result_files(
        session: WorkspaceSession, result: dict[str, Any]
    ) -> list[str]:
        workflow = session.workflow
        workspace = (
            workflow.resolved_workspace(session.project_path)
            if workflow is not None
            else Path.cwd()
        )
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for nested in value.values():
                    visit(nested)
                return
            if isinstance(value, (list, tuple, set)):
                for nested in value:
                    visit(nested)
                return
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                return
            candidate = Path(value.strip()).expanduser()
            if not candidate.is_absolute():
                candidate = workspace / candidate
            try:
                candidate = candidate.resolve()
                exists = candidate.is_file()
            except OSError:
                return
            path = str(candidate)
            if exists and path not in found:
                found.append(path)

        visit(result.get("data"))
        return found

    def _record_generated_file_group(
        self, session: WorkspaceSession, event: dict[str, Any]
    ) -> None:
        node_id = str(event.get("node_id") or "")
        workflow = session.workflow
        node = workflow.find(node_id) if workflow is not None and node_id else None
        if node is None:
            return

        result = event.get("result")
        result_dict = result if isinstance(result, dict) else {}
        data = result_dict.get("data")
        data_dict = data if isinstance(data, dict) else {}

        generated = self._existing_result_files(
            session, {"data": data_dict.get("_generated_files", [])}
        )
        attachment_paths: list[str] = []
        for previous in reversed(session.run_events):
            if (
                previous.get("type") == "agent_prompt"
                and str(previous.get("node_id") or "") == node_id
            ):
                attachment_paths = self._existing_result_files(
                    session, {"data": previous.get("attachments", [])}
                )
                break
        generated = [path for path in generated if path not in attachment_paths]

        final_values: list[Any] = []
        if node.kind == "executor":
            final_values.extend(
                data_dict.get(key)
                for key in (
                    "candidate_path",
                    "output_path",
                    "final_path",
                    "result_path",
                    "saved_to",
                )
            )
            artifact = data_dict.get("artifact")
            if isinstance(artifact, dict):
                final_values.append(artifact.get("path"))
        elif node.kind == "result":
            final_values.append(data_dict.get("saved_to"))
        else:
            reported_values = [
                data_dict.get(key)
                for key in ("output_path", "final_path", "result_path", "saved_to")
            ]
            reported = self._existing_result_files(session, {"data": reported_values})
            final_values.extend(path for path in reported if path in generated)

        if event.get("type") == "work_review_finished":
            generated.extend(
                self._existing_result_files(
                    session, {"data": [event.get("protocol_path")]}
                )
            )
            final_values.append(event.get("report_path"))

        final_files = self._existing_result_files(session, {"data": final_values})
        intermediate = [path for path in generated if path not in final_files]
        intermediate = list(dict.fromkeys(intermediate))
        final_files = list(dict.fromkeys(final_files))
        if not intermediate and not final_files:
            return

        previous_passes = sum(
            1
            for group in session.generated_file_groups
            if str(group.get("node_id") or "") == node_id
        )
        iteration = max(
            1,
            int(
                event.get("iteration")
                or (
                    session.active_file_iteration
                    if session.active_file_node_id == node_id
                    else 0
                )
                or previous_passes + 1
            ),
        )
        existing = next(
            (
                group
                for group in reversed(session.generated_file_groups)
                if str(group.get("node_id") or "") == node_id
                and int(group.get("iteration", 1)) == iteration
            ),
            None,
        )
        if existing is None:
            session.generated_file_groups.append(
                {
                    "node_id": node_id,
                    "node_title": node.title,
                    "iteration": iteration,
                    "color": NODE_COLORS.get(node.kind, "#CBD5E1"),
                    "intermediate": intermediate,
                    "result": final_files,
                }
            )
            return
        existing_intermediate = [
            str(path) for path in existing.get("intermediate", []) if str(path)
        ]
        existing_result = [
            str(path) for path in existing.get("result", []) if str(path)
        ]
        merged_result = list(dict.fromkeys([*existing_result, *final_files]))
        existing["intermediate"] = [
            path
            for path in dict.fromkeys([*existing_intermediate, *intermediate])
            if path not in merged_result
        ]
        existing["result"] = merged_result

    @staticmethod
    def _log_preview(value: object, limit: int = MAX_NODE_RESULT_PREVIEW) -> str:
        text = str(value)
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return f"{text[:limit]}\n… [скорочено {omitted} символів]"

    def _finalize_running_runtimes(self, session: WorkspaceSession) -> None:
        now = time.monotonic()
        for node_id, started_at in list(session.node_started_at.items()):
            duration = max(0.0, now - started_at)
            session.node_durations[node_id] = duration
            session.node_duration_history.setdefault(node_id, []).append(duration)
            session.node_started_at.pop(node_id, None)
            if session.node_statuses.get(node_id) == "running":
                session.node_statuses[node_id] = "failed"
            if session.id == self.current_workspace_id:
                self.scene.set_node_runtime(
                    node_id,
                    session.node_durations[node_id],
                    history=session.node_duration_history.get(node_id, []),
                )
                self.scene.set_node_status(
                    node_id, session.node_statuses.get(node_id, "failed")
                )

    def _save_run_log_for_session(
        self, session: WorkspaceSession, status: str, error: str = ""
    ) -> Path:
        directory = session.run_directory or self._new_run_directory(session)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "flowai-run.json"
        payload = {
            "workflow": session.display_name,
            "workspace_id": session.id,
            "status": status,
            "error": error,
            "events": session.run_events,
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    def show_about(self) -> None:
        paths = log_paths()
        QMessageBox.about(
            self,
            "Про FlowAI",
            "FlowAI 0.3\n\nЛокальний візуальний редактор автоматичних Codex-агентів. "
            "Працює через ваш вхід у ChatGPT без OpenAI API-ключа.\n\n"
            f"Логи: {paths.directory}",
        )

    def _open_log_directory(self) -> None:
        directory = log_paths().directory
        directory.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            QMessageBox.warning(
                self,
                "Не вдалося відкрити папку",
                f"Папка логів: {directory}",
            )

    def nativeEvent(self, event_type: Any, message: Any) -> tuple[bool, int]:
        if sys.platform == "win32":
            try:
                native = ctypes.wintypes.MSG.from_address(int(message))
                if native.message == 0x0218:  # WM_POWERBROADCAST
                    if native.wParam == 0x0004:  # PBT_APMSUSPEND
                        self._set_system_pause_reason("sleep", True)
                    elif native.wParam in {0x0007, 0x0012}:  # resume variants
                        self._set_system_pause_reason("sleep", False)
            except (TypeError, ValueError, OSError):
                LOGGER.debug("Could not decode native Windows event", exc_info=True)
        return super().nativeEvent(event_type, message)

    def _set_system_pause_reason(self, reason: str, paused: bool) -> None:
        was_paused = bool(self._system_pause_reasons)
        if paused:
            self._system_pause_reasons.add(reason)
        else:
            self._system_pause_reasons.discard(reason)
        is_paused = bool(self._system_pause_reasons)
        if was_paused == is_paused:
            return

        if is_paused:
            message = "ПК переходить у сон — Flow продовжиться після пробудження"
            for session in self.workspace_sessions:
                if session.run_worker is None or session.run_state != "running":
                    continue
                session.run_worker.pause(message)
                session.run_state = "paused"
        else:
            message = "ПК прокинувся — виконання Flow триває"
            for session in self.workspace_sessions:
                if session.run_worker is None or session.run_state != "paused":
                    continue
                session.run_worker.resume(message)
                session.run_state = "running"
        self._refresh_workspace_sidebar()
        self._update_workspace_actions()

    def closeEvent(self, event: QCloseEvent) -> None:
        running = [
            session.display_name
            for session in self.workspace_sessions
            if session.run_thread
            or session.run_state in {"running", "paused"}
            or session.stop_requested
        ]
        if running:
            QMessageBox.warning(
                self,
                "Flow виконується",
                "Спочатку зупиніть активні Flow:\n\n" + "\n".join(running),
            )
            event.ignore()
            return
        waiting = [
            session.display_name
            for session in self.workspace_sessions
            if session.run_state == "needs_attention"
        ]
        if waiting:
            QMessageBox.warning(
                self,
                "Flow очікує на відповідь",
                "Спочатку завершіть взаємодію в цих середовищах:\n\n"
                + "\n".join(waiting),
            )
            event.ignore()
            return
        original_id = self.current_workspace_id
        for session in [item for item in self.workspace_sessions if item.dirty]:
            self.select_workspace(session.id)
            if not self._confirm_discard():
                if original_id is not None:
                    self.select_workspace(original_id)
                event.ignore()
                return
        self._capture_current_workspace()
        self._persist_workspace_registry()
        self._persist_layout()
        self.tray_icon.hide()
        if self.account_thread is not None:
            self.account_thread.quit()
            self.account_thread.wait(5000)
        event.accept()
