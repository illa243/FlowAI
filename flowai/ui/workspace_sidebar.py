from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..workspaces import WorkspaceSession

SPINNER_FRAMES = ("◐", "◓", "◑", "◒")


class ResponsiveListWidget(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._horizontal: bool | None = None
        self.setWrapping(False)
        self.setMovement(QListWidget.Movement.Static)

    @property
    def horizontal_layout(self) -> bool:
        return bool(self._horizontal)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_orientation()

    def _update_orientation(self) -> None:
        horizontal = self.viewport().width() > self.viewport().height()
        if horizontal == self._horizontal:
            return
        self._horizontal = horizontal
        self.setFlow(
            QListWidget.Flow.LeftToRight if horizontal else QListWidget.Flow.TopToBottom
        )
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if horizontal
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if horizontal
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )


class WorkspaceListWidget(ResponsiveListWidget):
    rename_requested = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F2 and self.currentItem() is not None:
            session_id = str(self.currentItem().data(Qt.ItemDataRole.UserRole))
            self.rename_requested.emit(session_id)
            event.accept()
            return
        super().keyPressEvent(event)


class WorkspaceCard(QFrame):
    def __init__(self, session: WorkspaceSession, *, selected: bool) -> None:
        super().__init__()
        self.session = session
        self.selected = selected

        self.rail = QFrame()
        self.rail.setFixedWidth(4)
        self.rail.setObjectName("workspaceRail")

        self.name_label = QLabel()
        self.name_label.setObjectName("workspaceName")
        self.name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )
        self.status_text = QLabel()
        self.status_text.setObjectName("workspaceStatusText")

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 3, 0, 3)
        labels.setSpacing(1)
        labels.addWidget(self.name_label)
        labels.addWidget(self.status_text)

        self.status_icon = QLabel()
        self.status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_icon.setFixedWidth(24)
        self.status_icon.setObjectName("workspaceStatusIcon")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 8, 4)
        layout.setSpacing(9)
        layout.addWidget(self.rail)
        layout.addLayout(labels, 1)
        layout.addWidget(self.status_icon)

        self.refresh()

    def refresh(self, spinner_frame: int = 0) -> None:
        rail_color = (
            "#22C55E"
            if self.selected
            else ("#F59E0B" if self.session.is_loaded else "#64748B")
        )
        background = "#24324A" if self.selected else "#172033"
        border = "#4ADE80" if self.selected else "#334155"
        self.setStyleSheet(
            "QFrame {"
            f"background: {background}; border: 1px solid {border}; border-radius: 9px;"
            "}"
            f"QFrame#workspaceRail {{ background: {rail_color}; border: none; "
            "border-radius: 2px; }"
            "QLabel { border: none; background: transparent; }"
            "QLabel#workspaceName { color: #F8FAFC; font-weight: 600; }"
            "QLabel#workspaceStatusText { color: #94A3B8; font-size: 9pt; }"
        )
        dirty = " *" if self.session.dirty else ""
        self.name_label.setText(f"{self.session.display_name}{dirty}")
        self.name_label.setToolTip(self.session.display_name)
        self.status_text.setText(self.session.status_text)
        self.setToolTip(
            f"{self.session.display_name}\n{self.session.path_text}\n{self.session.status_text}"
        )
        self._update_status_icon(spinner_frame)

    def _update_status_icon(self, spinner_frame: int) -> None:
        state = self.session.run_state
        if state == "running":
            icon, tooltip, color = (
                SPINNER_FRAMES[spinner_frame % len(SPINNER_FRAMES)],
                "Виконується",
                "#60A5FA",
            )
        elif state == "needs_attention":
            icon, tooltip, color = "⚠", "Очікує на вашу відповідь", "#FBBF24"
        elif state == "failed":
            icon, tooltip, color = "✕", "Помилка виконання", "#F87171"
        elif state == "paused":
            icon, tooltip, color = "Ⅱ", "Призупинено", "#FBBF24"
        elif self.session.unread_result:
            icon, tooltip, color = "●", "Завершено — є новий результат", "#A78BFA"
        else:
            icon, tooltip, color = "", self.session.status_text, "#94A3B8"
        self.status_icon.setText(icon)
        self.status_icon.setToolTip(tooltip)
        self.status_icon.setStyleSheet(
            f"color: {color}; font-size: 17px; font-weight: 700; border: none;"
        )


class WorkspaceSidebar(QWidget):
    workspace_selected = Signal(str)
    add_requested = Signal()
    action_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sessions: list[WorkspaceSession] = []
        self._selected_id: str | None = None
        self._cards: dict[str, WorkspaceCard] = {}
        self._rebuilding = False
        self._pending: tuple[list[WorkspaceSession], str | None] | None = None
        self._spinner_frame = 0

        self.summary = QLabel("Немає активних Flow")
        self.summary.setObjectName("mutedLabel")
        self.summary.hide()

        self.add_button = QPushButton("＋  Додати Flow")
        self.add_button.clicked.connect(self.add_requested)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Пошук проєкту…")
        self.search.textChanged.connect(self._apply_filter)

        self.list_widget = WorkspaceListWidget()
        self.list_widget.setObjectName("workspaceList")
        self.list_widget.setSpacing(5)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.itemClicked.connect(self._item_clicked)
        self.list_widget.customContextMenuRequested.connect(self._open_context_menu)
        self.list_widget.setStyleSheet(
            "QListWidget#workspaceList { background: transparent; border: none; }"
            "QListWidget#workspaceList::item { background: transparent; border: none; }"
            "QListWidget#workspaceList::item:selected { background: transparent; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        layout.addWidget(self.add_button)
        layout.addWidget(self.search)
        layout.addWidget(self.list_widget, 1)

        self.spinner_timer = QTimer(self)
        self.spinner_timer.setInterval(140)
        self.spinner_timer.timeout.connect(self._advance_spinner)
        self.spinner_timer.start()

    def set_sessions(
        self, sessions: Iterable[WorkspaceSession], selected_id: str | None
    ) -> None:
        """Перебудувати список середовищ.

        Перебудова може викликати обробку відкладених подій (наприклад,
        події фонового запуску), а ті знову просять оновити панель. Тому
        повторний виклик не перебудовує список одразу, а лише запам'ятовує
        останній стан — інакше вкладений clear() видаляє елементи, з якими
        ще працює зовнішній виклик.
        """
        self._pending = (list(sessions), selected_id)
        if self._rebuilding:
            return
        self._rebuilding = True
        try:
            while self._pending is not None:
                pending_sessions, pending_selected = self._pending
                self._pending = None
                self._rebuild(pending_sessions, pending_selected)
        finally:
            self._rebuilding = False

    def _rebuild(
        self, sessions: list[WorkspaceSession], selected_id: str | None
    ) -> None:
        self._sessions = sessions
        self._selected_id = selected_id
        self._cards.clear()
        blocked = self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            current: QListWidgetItem | None = None
            for session in self._sessions:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, session.id)
                item.setSizeHint(QSize(220, 62))
                card = WorkspaceCard(session, selected=session.id == selected_id)
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, card)
                self._cards[session.id] = card
                if session.id == selected_id:
                    current = item
            if current is not None:
                self.list_widget.setCurrentItem(current)
        finally:
            self.list_widget.blockSignals(blocked)
        self._update_summary()
        self._apply_filter(self.search.text())

    def _update_summary(self) -> None:
        running = sum(session.run_state == "running" for session in self._sessions)
        attention = sum(
            session.run_state == "needs_attention" for session in self._sessions
        )
        parts: list[str] = []
        if running:
            parts.append(f"Працює: {running}")
        if attention:
            parts.append(f"Потребує уваги: {attention}")
        summary = " · ".join(parts) if parts else "Немає активних Flow"
        self.summary.setText(summary)
        self.list_widget.setToolTip(summary)

    def _apply_filter(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            session = self._session(str(item.data(Qt.ItemDataRole.UserRole)))
            visible = session is not None and (
                not query
                or query in session.display_name.casefold()
                or query in session.path_text.casefold()
            )
            item.setHidden(not visible)

    def _item_clicked(self, item: QListWidgetItem) -> None:
        self.workspace_selected.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def _open_context_menu(self, position: object) -> None:
        item = self.list_widget.itemAt(position)
        if item is None:
            return
        session_id = str(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        actions = [
            ("Відкрити", "open"),
            ("Перейменувати", "rename"),
            ("Зберегти", "save"),
            ("Зберегти як…", "save_as"),
            ("Показати у Провіднику", "reveal"),
            ("Вивантажити з пам’яті", "unload"),
            ("Прибрати зі списку", "remove"),
        ]
        for label, key in actions:
            action = menu.addAction(label)
            action.setData(key)
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(position))
        if chosen is not None:
            self.action_requested.emit(session_id, str(chosen.data()))

    def _advance_spinner(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(SPINNER_FRAMES)
        for card in self._cards.values():
            if card.session.run_state == "running":
                card._update_status_icon(self._spinner_frame)

    def _session(self, session_id: str) -> WorkspaceSession | None:
        return next(
            (session for session in self._sessions if session.id == session_id), None
        )
