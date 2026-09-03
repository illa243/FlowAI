from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..models import (
    NODE_LABELS,
    FlowEdge,
    FlowNode,
    Workflow,
    managed_task_title,
    normalize_managed_tasks,
)
from ..skills import list_skills
from .attachments import AttachmentListWidget
from .motion import AnimatedDialog

AGENT_FIELDS = {
    "model",
    "reasoning",
    "sandbox",
    "memory",
    "additional_folders",
    "instructions",
    "instruction_files",
    "prompt_source",
    "prompt",
    "output_format",
    "output_schema",
    "attachments",
    "skills",
    "retries",
}

KIND_FIELDS: dict[str, set[str]] = {
    "entry": {"entry_text", "entry_json", "attachments"},
    "tasks_manager": {"tasks", "task_source", "plan_save_path"},
    "prompt_reviewer": set(AGENT_FIELDS),
    "executor": set(AGENT_FIELDS),
    "task_reviewer": AGENT_FIELDS | {"criteria_node"},
    "work_reviewer": AGENT_FIELDS | {"monitor_all", "monitored_nodes", "report_path"},
    "calibrator": AGENT_FIELDS
    | {"false_threshold", "auto_skip", "threshold_hint"},
    "result": {
        "template",
        "save_path",
        "true_limit",
        "false_limit",
        "task_attempt_limit",
        "wait_for_confirmation",
        "confirmation_mode",
        "confirmation_ports",
        "final_task_result",
        "retry_guard_enabled",
        "retry_guard_threshold",
        "learning_enabled",
    },
}


class PopupFieldLabel(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("popupFieldLabel")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class ExplicitVisibilityLabel(QLabel):
    """Віддавати явний стан show/hide ще до показу батьківського вікна."""

    def isVisible(self) -> bool:
        return not self.isHidden()


class SkillPinWidget(QWidget):
    """Скіли, які Codex завантажить для ноди до першого кроку."""

    skills_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[dict[str, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.list = QListWidget()
        self.list.setMaximumHeight(96)
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        add = QPushButton("Закріпити скіл")
        remove = QPushButton("Прибрати")
        add.clicked.connect(self._choose)
        remove.clicked.connect(self._remove)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        layout.addLayout(buttons)

    def set_skills(self, values: list[dict[str, str]]) -> None:
        self._values = [
            {
                "name": str(item.get("name", "")),
                "path": str(item.get("path", "")),
            }
            for item in values
            if isinstance(item, dict) and str(item.get("name", ""))
        ]
        self._render()

    def skills(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._values]

    def add_skill(self, name: str, path: str) -> None:
        """Закріпити скіл один раз, ідентифікуючи його за іменем."""
        if not name or any(item["name"] == name for item in self._values):
            return
        self._values.append({"name": name, "path": path})
        self._render()
        self.skills_changed.emit()

    def _render(self) -> None:
        self.list.clear()
        for item in self._values:
            row = QListWidgetItem(item["name"])
            row.setToolTip(item["path"])
            self.list.addItem(row)

    def _choose(self) -> None:
        entries = [entry for entry in list_skills(None) if entry.enabled]
        if not entries:
            QMessageBox.information(
                self,
                "Скілів не знайдено",
                "У ~/.codex/skills немає жодного скіла.",
            )
            return
        names = [f"{entry.name} — {entry.description[:70]}" for entry in entries]
        choice, accepted = QInputDialog.getItem(
            self,
            "Закріпити скіл",
            "Який скіл має завантажити агент?",
            names,
            0,
            False,
        )
        if not accepted:
            return
        entry = entries[names.index(choice)]
        self.add_skill(entry.name, str(entry.path))

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        self._values.pop(row)
        self._render()
        self.skills_changed.emit()


class FullScreenTextEditorDialog(AnimatedDialog):
    """Maximized editor used by large text fields in Parameters."""

    def __init__(
        self,
        title: str,
        value: str,
        parent: QWidget | None = None,
        *,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("fullScreenTextEditor")
        self.setWindowTitle(f"Редактор — {title}")
        self.setModal(True)

        self.editor = QPlainTextEdit(value)
        self.editor.setObjectName("fullScreenTextEditorField")
        self.editor.setReadOnly(read_only)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("Застосувати")
        save_button.setObjectName("primaryButton")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Скасувати")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.setVisible(not read_only)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.buttons)
        self.resize(1100, 760)

    def text(self) -> str:
        return self.editor.toPlainText()

    def exec(self) -> int:
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        return super().exec()


class ExpandablePlainTextEdit(QPlainTextEdit):
    """Plain-text editor with a compact maximize control in its top-right corner."""

    def __init__(self, field_title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field_title = field_title
        self.setAccessibleName(field_title)
        self.setViewportMargins(0, 0, 28, 0)

        self.expand_button = QToolButton(self)
        self.expand_button.setObjectName("expandTextButton")
        self.expand_button.setToolTip(f"Відкрити «{field_title}» на весь екран")
        self.expand_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_button.setAutoRaise(True)
        self.expand_button.setFixedSize(22, 22)
        self.expand_button.setIconSize(self.expand_button.size() * 0.64)
        self.expand_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton)
        )
        self.expand_button.clicked.connect(self.open_fullscreen_editor)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        margin = self.frameWidth() + 4
        self.expand_button.move(
            self.width() - self.expand_button.width() - margin,
            margin,
        )
        self.expand_button.raise_()

    def open_fullscreen_editor(self) -> None:
        dialog = FullScreenTextEditorDialog(
            self.field_title,
            self.toPlainText(),
            self,
            read_only=self.isReadOnly(),
        )
        dialog.editor.setFont(self.font())
        if dialog.exec() != QDialog.DialogCode.Accepted or self.isReadOnly():
            return
        position = self.textCursor().position()
        self.setPlainText(dialog.text())
        cursor = self.textCursor()
        cursor.setPosition(min(position, len(self.toPlainText())))
        self.setTextCursor(cursor)
        self.setFocus()


class ManagedTaskEditor(QFrame):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, task: dict[str, Any], index: int) -> None:
        super().__init__()
        self.task_id = str(task.get("id") or uuid4().hex)
        self.index = index
        self._loading = False
        self.setObjectName("managedTaskEditor")

        self.heading = QLabel()
        self.heading.setObjectName("managedTaskHeading")
        self.remove_button = QToolButton()
        self.remove_button.setObjectName("removeManagedTaskButton")
        self.remove_button.setText("×")
        self.remove_button.setToolTip("Видалити завдання")
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        header = QHBoxLayout()
        header.addWidget(self.heading, 1)
        header.addWidget(self.remove_button)

        self.prompt = ExpandablePlainTextEdit(f"Промпт завдання {index + 1}")
        self.prompt.setMinimumHeight(105)
        self.prompt.setPlaceholderText("Опишіть окреме завдання для AI-ланцюга")
        self.prompt.setPlainText(str(task.get("prompt", "")))

        self.attachments = AttachmentListWidget()
        self.attachments.setMaximumHeight(180)
        add_files = QPushButton("Додати файли")
        add_files.clicked.connect(self._add_files)
        remove_files = QPushButton("Прибрати")
        remove_files.clicked.connect(self.attachments.remove_selected_paths)
        file_buttons = QHBoxLayout()
        file_buttons.addWidget(add_files)
        file_buttons.addWidget(remove_files)
        self.attachments.set_paths(
            [str(path) for path in task.get("attachments", []) if str(path)]
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.prompt)
        layout.addWidget(self.attachments)
        layout.addLayout(file_buttons)

        self.prompt.textChanged.connect(self._content_changed)
        self.attachments.paths_changed.connect(self._content_changed)
        self.refresh_heading(index)

    def value(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "prompt": self.prompt.toPlainText(),
            "attachments": self.attachments.paths(),
        }

    def set_task(self, task: dict[str, Any]) -> None:
        """Наповнити наявний редактор новим завданням.

        Створення редактора коштує дорого: у ноді з вісьмома завданнями
        повна перебудова блокує UI-потік на десятки, а на холодному старті —
        на сотні мілісекунд. Саме через це канвас підвисав одразу після
        вибору Tasks Manager.
        """
        self._loading = True
        try:
            self.task_id = str(task.get("id") or uuid4().hex)
            prompt = str(task.get("prompt", ""))
            if self.prompt.toPlainText() != prompt:
                self.prompt.setPlainText(prompt)
            paths = [str(path) for path in task.get("attachments", []) if str(path)]
            if self.attachments.paths() != paths:
                self.attachments.set_paths(paths)
        finally:
            self._loading = False

    def refresh_heading(self, index: int) -> None:
        self.index = index
        self.prompt.field_title = f"Промпт завдання {index + 1}"
        self.prompt.setAccessibleName(self.prompt.field_title)
        self.prompt.expand_button.setToolTip(
            f"Відкрити «{self.prompt.field_title}» на весь екран"
        )
        self.heading.setText(f"{index + 1}. {managed_task_title(self.value(), index)}")

    def _content_changed(self) -> None:
        if self._loading:
            return
        self.refresh_heading(self.index)
        self.changed.emit()

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Файли для завдання",
            "",
            "Усі файли (*);;Картинки (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        self.attachments.add_paths([str(Path(path).resolve()) for path in paths])


class ManagedTasksWidget(QWidget):
    tasks_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.loading = False
        self.sections: list[ManagedTaskEditor] = []
        self.sections_layout = QVBoxLayout()
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(8)
        self.add_button = QPushButton("＋ Додати завдання")
        self.add_button.setObjectName("addManagedTaskButton")
        self.add_button.clicked.connect(self.add_task)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.sections_layout)
        layout.addWidget(self.add_button)
        self.set_tasks([])

    def set_tasks(self, tasks: Any) -> None:
        """Показати завдання, перевикористовуючи вже створені редактори.

        Раніше кожен виклик знищував і будував наново всі секції, тож вибір
        ноди Tasks Manager підвішував інтерфейс. Тепер створюються лише ті
        редактори, яких бракує, а зайві прибираються.
        """
        self.loading = True
        try:
            wanted = normalize_managed_tasks(tasks)
            while len(self.sections) > len(wanted):
                section = self.sections.pop()
                self.sections_layout.removeWidget(section)
                section.setParent(None)
                section.deleteLater()
            for index, task in enumerate(wanted):
                if index < len(self.sections):
                    self.sections[index].set_task(task)
                else:
                    self._append_section(task)
            self._refresh_sections()
        finally:
            self.loading = False

    def tasks(self) -> list[dict[str, Any]]:
        return [section.value() for section in self.sections]

    def add_task(self) -> None:
        self._append_section({"id": uuid4().hex, "prompt": "", "attachments": []})
        self._refresh_sections()
        self._emit_changed()

    def _append_section(self, task: dict[str, Any]) -> None:
        section = ManagedTaskEditor(task, len(self.sections))
        section.changed.connect(self._section_changed)
        section.remove_requested.connect(self._remove_section)
        self.sections.append(section)
        self.sections_layout.addWidget(section)

    def _remove_section(self, section: ManagedTaskEditor) -> None:
        if len(self.sections) <= 1 or section not in self.sections:
            return
        self.sections.remove(section)
        self.sections_layout.removeWidget(section)
        section.deleteLater()
        self._refresh_sections()
        self._emit_changed()

    def _section_changed(self) -> None:
        self._refresh_sections()
        self._emit_changed()

    def _refresh_sections(self) -> None:
        multiple = len(self.sections) > 1
        for index, section in enumerate(self.sections):
            section.refresh_heading(index)
            section.remove_button.setEnabled(multiple)

    def _emit_changed(self) -> None:
        if not self.loading:
            self.tasks_changed.emit()


class PopupComboBox(NoWheelComboBox):
    """Editable combo that also opens when its text area is clicked."""

    def __init__(self) -> None:
        super().__init__()
        self.setEditable(True)
        if self.lineEdit() is not None:
            self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if (
            watched is self.lineEdit()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            QTimer.singleShot(0, self.showPopup)
        return super().eventFilter(watched, event)


class Inspector(QWidget):
    changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current: FlowNode | FlowEdge | None = None
        self.workflow: Workflow | None = None
        self.loading = False
        self.execution_locked = False

        self.stack = QStackedWidget()
        self.empty_page = self._build_empty_page()
        self.node_page = self._build_node_page()
        self.edge_page = self._build_edge_page()
        self.stack.addWidget(self.empty_page)
        self.stack.addWidget(self.node_page)
        self.stack.addWidget(self.edge_page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

    def set_workflow(self, workflow: Workflow | None) -> None:
        self.workflow = workflow

    def focus_first_task(self) -> None:
        if self.tasks_editor.sections:
            self.tasks_editor.sections[0].prompt.setFocus()

    def _build_empty_page(self) -> QWidget:
        return QWidget()

    def _build_node_page(self) -> QWidget:
        content = QWidget()
        self.node_form = QFormLayout(content)
        self.node_form.setContentsMargins(8, 8, 8, 12)
        self.node_form.setSpacing(7)

        self.node_kind = QLabel()
        self.node_kind.setObjectName("mutedLabel")

        self.node_id = QLabel()
        self.node_id.setObjectName("mutedLabel")
        self.node_id.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.title_edit = QLineEdit()
        self.node_form.addRow("Назва", self.title_edit)

        self.entry_text = self._plain(110, "Вхідний промпт")
        self.node_form.addRow("Вхідний промпт", self.entry_text)
        self.entry_json = self._plain(90, "Початковий JSON")
        self.node_form.addRow("Початковий JSON", self.entry_json)

        self.tasks_editor = ManagedTasksWidget()
        self.node_form.addRow("Завдання", self.tasks_editor)
        self.task_source = NoWheelComboBox()
        self.task_source.addItem("Статичний список", "static")
        self.task_source.addItem("З входу один раз", "input_once")
        self.node_form.addRow("Джерело Tasks", self.task_source)
        self.plan_save_path = QLineEdit()
        self.plan_save_path.setPlaceholderText("ui_project_spec.json")
        self.node_form.addRow("Файл погодженого плану", self.plan_save_path)

        self.model_combo = PopupComboBox()
        self.model_combo.addItems(["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
        self.model_label = PopupFieldLabel("Модель")
        self.model_label.setToolTip("Натисніть, щоб відкрити список моделей")
        self.model_label.clicked.connect(
            lambda: QTimer.singleShot(0, self.model_combo.showPopup)
        )
        self.node_form.addRow(self.model_label, self.model_combo)

        self.reasoning_combo = NoWheelComboBox()
        self.reasoning_combo.addItems(["none", "low", "medium", "high", "xhigh", "max"])
        self.node_form.addRow("Міркування", self.reasoning_combo)

        self.sandbox_combo = NoWheelComboBox()
        self.sandbox_combo.addItem("Лише читання", "read-only")
        self.sandbox_combo.addItem("Запис у workspace", "workspace-write")
        self.sandbox_combo.addItem("Повний доступ", "full-access")
        self.node_form.addRow("Доступ", self.sandbox_combo)

        self.memory_combo = NoWheelComboBox()
        self.memory_combo.addItem("Той самий тред", "thread")
        self.memory_combo.addItem("Окремий тред для Task", "task_thread")
        self.memory_combo.addItem("Новий тред щоразу", "fresh")
        self.node_form.addRow("Пам'ять між спробами", self.memory_combo)

        self.node_folders = QListWidget()
        self.node_folders.setMaximumHeight(90)
        self.node_folders.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        node_folder_controls = self._list_with_buttons(
            self.node_folders,
            ("Додати папку", self._add_node_folder),
            ("Прибрати", self._remove_node_folders),
        )
        self.node_form.addRow("Додаткові папки блоку", node_folder_controls)

        self.instructions_edit = self._plain(120, "Постійні інструкції")
        self.node_form.addRow("Постійні інструкції", self.instructions_edit)

        self.instruction_files = QListWidget()
        self.instruction_files.setMaximumHeight(90)
        instruction_file_controls = self._list_with_buttons(
            self.instruction_files,
            ("Додати MD", self._add_instruction_files),
            ("Прибрати", self._remove_instruction_file),
        )
        self.node_form.addRow("MD-інструкції", instruction_file_controls)

        self.prompt_source = NoWheelComboBox()
        self.prompt_source.addItem("Поле «Промпт»", "template")
        self.prompt_source.addItem("Вхід «prompt» від попереднього блоку", "input")
        self.node_form.addRow("Джерело промпту", self.prompt_source)
        self.prompt_edit = self._plain(130, "Промпт")
        self.node_form.addRow("Промпт", self.prompt_edit)

        self.output_format = NoWheelComboBox()
        self.output_format.addItem("Текст", "text")
        self.output_format.addItem("JSON", "json")
        self.node_form.addRow("Формат відповіді", self.output_format)
        self.output_schema = self._plain(100, "Схема JSON")
        self.node_form.addRow("Схема JSON", self.output_schema)

        self.attachments = AttachmentListWidget()
        self.attachments.setMaximumHeight(260)
        attachment_controls = self._list_with_buttons(
            self.attachments,
            ("Додати", self._add_attachments),
            ("Прибрати", self._remove_attachment),
        )
        self.node_form.addRow("Файли і картинки", attachment_controls)

        self.skill_pins = SkillPinWidget()
        self.node_form.addRow("Скіли", self.skill_pins)

        self.retries_spin = NoWheelSpinBox()
        self.retries_spin.setRange(0, 5)
        self.node_form.addRow("Повторні спроби", self.retries_spin)

        self.criteria_combo = NoWheelComboBox()
        self.node_form.addRow("Блок-еталон", self.criteria_combo)

        self.template_edit = self._plain(110, "Шаблон результату")
        self.node_form.addRow("Шаблон результату", self.template_edit)

        save_box = QWidget()
        save_layout = QHBoxLayout(save_box)
        save_layout.setContentsMargins(0, 0, 0, 0)
        self.save_path = QLineEdit()
        choose_save = QPushButton("…")
        choose_save.setFixedWidth(34)
        choose_save.clicked.connect(self._choose_save_path)
        save_layout.addWidget(self.save_path)
        save_layout.addWidget(choose_save)
        self.node_form.addRow("Зберегти у файл", save_box)

        self.true_limit = NoWheelSpinBox()
        self.true_limit.setRange(1, 99)
        self.node_form.addRow("Ліміт проходів TRUE", self.true_limit)
        self.false_limit = NoWheelSpinBox()
        self.false_limit.setRange(1, 99)
        self.node_form.addRow("Ліміт проходів FALSE", self.false_limit)
        self.task_attempt_limit = NoWheelSpinBox()
        self.task_attempt_limit.setRange(1, 99)
        self.task_attempt_limit.setToolTip(
            "Скільки разів одне завдання Tasks Manager може піти на переробку, "
            "перш ніж спрацює жовтий вихід EXHAUSTED"
        )
        self.node_form.addRow("Ліміт спроб на завдання", self.task_attempt_limit)

        self.false_threshold = NoWheelSpinBox()
        self.false_threshold.setRange(1, 20)
        self.false_threshold.setValue(2)
        self.false_threshold.setToolTip(
            "Після якого за рахунком FALSE зупиняти Flow і показувати "
            "рекомендації"
        )
        self.node_form.addRow("Зупиняти після FALSE №", self.false_threshold)

        self.auto_skip = QCheckBox("AutoSkip")
        self.auto_skip.setToolTip(
            "Повністю пропустити аналіз Calibration Stop: без виклику моделі, "
            "звіту та очікування підтвердження"
        )
        self.node_form.addRow(self.auto_skip)

        self.threshold_hint = ExplicitVisibilityLabel("")
        self.threshold_hint.setObjectName("mutedLabel")
        self.threshold_hint.setWordWrap(True)
        self.node_form.addRow("", self.threshold_hint)

        self.wait_for_confirmation = QCheckBox(
            "Очікувати підтвердження перед переходом"
        )
        self.node_form.addRow(self.wait_for_confirmation)
        self.confirmation_mode = NoWheelComboBox()
        self.confirmation_mode.addItem("Стандартний", "standard")
        self.confirmation_mode.addItem("Погодження UI-плану", "plan_approval")
        self.confirmation_mode.addItem("Вибір PNG-варіантів", "variant_selection")
        self.confirmation_mode.addItem("Погодження PSD", "asset_approval")
        self.node_form.addRow("Режим підтвердження", self.confirmation_mode)
        self.confirmation_ports = QLineEdit()
        self.confirmation_ports.setPlaceholderText("true,false")
        self.node_form.addRow("Підтверджувати порти", self.confirmation_ports)
        self.final_task_result = QCheckBox("Фінальний Result поточного task")
        self.node_form.addRow(self.final_task_result)
        self.retry_guard_enabled = QCheckBox("Pause при повторному QA-дефекті")
        self.node_form.addRow(self.retry_guard_enabled)
        self.retry_guard_threshold = NoWheelSpinBox()
        self.retry_guard_threshold.setRange(2, 10)
        self.node_form.addRow("Поріг повторів дефекту", self.retry_guard_threshold)
        self.learning_enabled = QCheckBox("Оновлювати локальне UI-навчання")
        self.node_form.addRow(self.learning_enabled)

        self.monitor_all = QCheckBox("Спостерігати всі блоки Flow")
        self.node_form.addRow(self.monitor_all)
        self.monitored_nodes = QListWidget()
        self.monitored_nodes.setMaximumHeight(140)
        self.node_form.addRow("Блоки під наглядом", self.monitored_nodes)
        self.report_path = QLineEdit()
        self.report_path.setPlaceholderText("runs/<запуск>/work-review-report.md")
        self.node_form.addRow("Файл звіту", self.report_path)

        self.node_rows: dict[str, tuple[QWidget | None, QWidget]] = {}
        fields = {
            "entry_text": self.entry_text,
            "entry_json": self.entry_json,
            "tasks": self.tasks_editor,
            "task_source": self.task_source,
            "plan_save_path": self.plan_save_path,
            "model": self.model_combo,
            "reasoning": self.reasoning_combo,
            "sandbox": self.sandbox_combo,
            "memory": self.memory_combo,
            "additional_folders": node_folder_controls,
            "instructions": self.instructions_edit,
            "instruction_files": instruction_file_controls,
            "prompt_source": self.prompt_source,
            "prompt": self.prompt_edit,
            "output_format": self.output_format,
            "output_schema": self.output_schema,
            "attachments": attachment_controls,
            "skills": self.skill_pins,
            "retries": self.retries_spin,
            "criteria_node": self.criteria_combo,
            "template": self.template_edit,
            "save_path": save_box,
            "true_limit": self.true_limit,
            "false_limit": self.false_limit,
            "task_attempt_limit": self.task_attempt_limit,
            "false_threshold": self.false_threshold,
            "auto_skip": self.auto_skip,
            "threshold_hint": self.threshold_hint,
            "wait_for_confirmation": self.wait_for_confirmation,
            "confirmation_mode": self.confirmation_mode,
            "confirmation_ports": self.confirmation_ports,
            "final_task_result": self.final_task_result,
            "retry_guard_enabled": self.retry_guard_enabled,
            "retry_guard_threshold": self.retry_guard_threshold,
            "learning_enabled": self.learning_enabled,
            "monitor_all": self.monitor_all,
            "monitored_nodes": self.monitored_nodes,
            "report_path": self.report_path,
        }
        for key, widget in fields.items():
            index = self.node_form.getWidgetPosition(widget)[0]
            label = self.node_form.itemAt(index, QFormLayout.ItemRole.LabelRole)
            self.node_rows[key] = (label.widget() if label else None, widget)

        self.title_edit.textEdited.connect(self._save_node)
        self.entry_text.textChanged.connect(self._save_node)
        self.entry_json.textChanged.connect(self._save_node)
        self.tasks_editor.tasks_changed.connect(self._save_node)
        self.task_source.currentIndexChanged.connect(self._save_node)
        self.plan_save_path.textEdited.connect(self._save_node)
        self.model_combo.currentTextChanged.connect(self._save_node)
        self.reasoning_combo.currentTextChanged.connect(self._save_node)
        self.sandbox_combo.currentIndexChanged.connect(self._save_node)
        self.memory_combo.currentIndexChanged.connect(self._save_node)
        self.instructions_edit.textChanged.connect(self._save_node)
        self.prompt_source.currentIndexChanged.connect(self._prompt_source_changed)
        self.prompt_edit.textChanged.connect(self._save_node)
        self.output_format.currentIndexChanged.connect(self._save_node)
        self.output_schema.textChanged.connect(self._save_node)
        self.retries_spin.valueChanged.connect(self._save_node)
        self.criteria_combo.currentIndexChanged.connect(self._save_node)
        self.template_edit.textChanged.connect(self._save_node)
        self.save_path.textEdited.connect(self._save_node)
        self.true_limit.valueChanged.connect(self._save_node)
        self.false_limit.valueChanged.connect(self._save_node)
        self.task_attempt_limit.valueChanged.connect(self._save_node)
        self.false_threshold.valueChanged.connect(self._save_node)
        self.auto_skip.toggled.connect(self._auto_skip_toggled)
        self.wait_for_confirmation.toggled.connect(self._save_node)
        self.confirmation_mode.currentIndexChanged.connect(self._save_node)
        self.confirmation_ports.textEdited.connect(self._save_node)
        self.final_task_result.toggled.connect(self._save_node)
        self.retry_guard_enabled.toggled.connect(self._save_node)
        self.retry_guard_threshold.valueChanged.connect(self._save_node)
        self.learning_enabled.toggled.connect(self._save_node)
        self.attachments.paths_changed.connect(self._save_node)
        self.skill_pins.skills_changed.connect(self._save_node)
        self.monitor_all.toggled.connect(self._monitor_all_toggled)
        self.monitored_nodes.itemChanged.connect(self._save_node)
        self.report_path.textEdited.connect(self._save_node)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        return page

    def _build_edge_page(self) -> QWidget:
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(8, 8, 8, 12)
        form.setSpacing(7)

        self.edge_port = QLabel()
        self.edge_port.setObjectName("mutedLabel")
        self.edge_label = QLineEdit()
        form.addRow("Підпис", self.edge_label)
        self.source_path = QLineEdit()
        self.source_path.setPlaceholderText("data.summary")
        form.addRow("Що передати", self.source_path)
        self.target_variable = QLineEdit()
        self.target_variable.setPlaceholderText("work")
        form.addRow("Вхідна змінна", self.target_variable)
        self.edge_condition = QLineEdit()
        self.edge_condition.setPlaceholderText('source.status == "success"')
        form.addRow("Умова переходу", self.edge_condition)
        self.edge_transform = self._plain(100, "Перетворення")
        self.edge_transform.setPlaceholderText("Необов'язковий шаблон: {{value}}")
        form.addRow("Перетворення", self.edge_transform)

        prompt_preset = QPushButton("Передати відповідь як промпт наступному блоку")
        prompt_preset.clicked.connect(self._set_prompt_transfer)
        form.addRow(prompt_preset)
        review_preset = QPushButton("Передати вердикт у Result")
        review_preset.clicked.connect(self._set_review_transfer)
        form.addRow(review_preset)

        self.edge_label.textEdited.connect(self._save_edge)
        self.source_path.textEdited.connect(self._save_edge)
        self.target_variable.textEdited.connect(self._save_edge)
        self.edge_condition.textEdited.connect(self._save_edge)
        self.edge_transform.textChanged.connect(self._save_edge)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        return page

    @staticmethod
    def _plain(height: int, title: str) -> ExpandablePlainTextEdit:
        editor = ExpandablePlainTextEdit(title)
        editor.setMinimumHeight(height)
        return editor

    @staticmethod
    def _list_with_buttons(
        widget: QListWidget,
        add: tuple[str, Any],
        remove: tuple[str, Any],
    ) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        buttons = QHBoxLayout()
        add_button = QPushButton(add[0])
        add_button.clicked.connect(add[1])
        remove_button = QPushButton(remove[0])
        remove_button.clicked.connect(remove[1])
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        layout.addLayout(buttons)
        return container

    # ------------------------------------------------------------------

    def set_object(self, selected: FlowNode | FlowEdge | None) -> None:
        self.current = selected
        self.loading = True
        try:
            if isinstance(selected, FlowNode):
                self._load_node(selected)
                self.stack.setCurrentWidget(self.node_page)
            elif isinstance(selected, FlowEdge):
                self._load_edge(selected)
                self.stack.setCurrentWidget(self.edge_page)
            else:
                self.stack.setCurrentWidget(self.empty_page)
        finally:
            self.loading = False
        self._apply_execution_lock()

    def set_execution_locked(self, locked: bool) -> None:
        self.execution_locked = bool(locked)
        self._apply_execution_lock()

    def _apply_execution_lock(self) -> None:
        editable = not self.execution_locked
        self.edge_page.setEnabled(editable)
        self.title_edit.setEnabled(editable)
        for _label, widget in getattr(self, "node_rows", {}).values():
            widget.setEnabled(editable)
        for widget in (
            getattr(self, "edge_label", None),
            getattr(self, "source_path", None),
            getattr(self, "target_variable", None),
            getattr(self, "edge_condition", None),
            getattr(self, "edge_transform", None),
        ):
            if widget is not None:
                widget.setEnabled(editable)
        if self.execution_locked and isinstance(self.current, FlowNode):
            if self.current.kind == "result":
                self.true_limit.setEnabled(True)
                self.false_limit.setEnabled(True)
                self.task_attempt_limit.setEnabled(True)
                self.wait_for_confirmation.setEnabled(True)
        elif editable:
            self._update_prompt_field_state()
            if (
                isinstance(self.current, FlowNode)
                and self.current.kind == "calibrator"
            ):
                self.false_threshold.setEnabled(
                    not self.auto_skip.isChecked()
                )
            if (
                isinstance(self.current, FlowNode)
                and self.current.kind == "work_reviewer"
            ):
                self.monitored_nodes.setEnabled(not self.monitor_all.isChecked())

    def _node_choices(self, exclude: str = "") -> list[tuple[str, str]]:
        if self.workflow is None:
            return []
        return [
            (f"{node.short_id} · {NODE_LABELS[node.kind]} «{node.title}»", node.id)
            for node in self.workflow.nodes
            if node.id != exclude
        ]

    def _load_node(self, node: FlowNode) -> None:
        self.node_kind.setText(NODE_LABELS.get(node.kind, node.kind))
        self.node_id.setText(node.short_id)
        self.node_id.setToolTip(node.id)
        self.title_edit.setText(node.title)

        self.entry_text.setPlainText(str(node.config.get("text", "")))
        self.entry_json.setPlainText(
            json.dumps(node.config.get("json") or {}, ensure_ascii=False, indent=2)
        )
        self.tasks_editor.set_tasks(node.config.get("tasks"))
        self._set_combo_data(
            self.task_source, str(node.config.get("task_source", "static"))
        )
        self.plan_save_path.setText(
            str(node.config.get("plan_save_path", "ui_project_spec.json"))
        )
        self.model_combo.setCurrentText(str(node.config.get("model", "gpt-5.6-terra")))
        self.reasoning_combo.setCurrentText(
            str(node.config.get("reasoning_effort", "medium"))
        )
        self._set_combo_data(
            self.sandbox_combo, str(node.config.get("sandbox", "read-only"))
        )
        self._set_combo_data(
            self.memory_combo, str(node.config.get("memory", "thread"))
        )

        folders = [
            str(item) for item in node.config.get("additional_folders", []) if str(item)
        ]
        legacy_workspace = str(node.config.get("workspace", "")).strip()
        if legacy_workspace and legacy_workspace not in folders:
            folders.insert(0, legacy_workspace)
        self.node_folders.clear()
        self.node_folders.addItems(folders)

        self.instructions_edit.setPlainText(str(node.config.get("instructions", "")))
        self.instruction_files.clear()
        self.instruction_files.addItems(
            [str(item) for item in node.config.get("instruction_files", [])]
        )
        self._set_combo_data(
            self.prompt_source, str(node.config.get("prompt_source", "template"))
        )
        self.prompt_edit.setPlainText(str(node.config.get("prompt", "")))
        self._set_combo_data(
            self.output_format, str(node.config.get("output_format", "text"))
        )
        self.output_schema.setPlainText(
            json.dumps(
                node.config.get("output_schema") or {}, ensure_ascii=False, indent=2
            )
        )
        self.attachments.set_paths(
            [str(item) for item in node.config.get("attachments", [])]
        )
        self.skill_pins.set_skills(node.config.get("skills", []))
        self.retries_spin.setValue(int(node.config.get("retries", 0)))

        self.criteria_combo.clear()
        self.criteria_combo.addItem("Авто (найближчий блок вгору по графу)", "")
        for label, node_id in self._node_choices(exclude=node.id):
            self.criteria_combo.addItem(label, node_id)
        self._set_combo_data(
            self.criteria_combo, str(node.config.get("criteria_node", ""))
        )

        self.template_edit.setPlainText(str(node.config.get("template", "")))
        self.save_path.setText(str(node.config.get("save_path", "")))
        self.true_limit.setValue(int(node.config.get("true_limit", 1)))
        self.false_limit.setValue(int(node.config.get("false_limit", 3)))
        self.task_attempt_limit.setValue(
            int(node.config.get("task_attempt_limit", 2))
        )
        self.wait_for_confirmation.setChecked(
            bool(node.config.get("wait_for_confirmation", False))
        )
        self._set_combo_data(
            self.confirmation_mode,
            str(node.config.get("confirmation_mode", "standard")),
        )
        ports = node.config.get("confirmation_ports", ["true", "false"])
        self.confirmation_ports.setText(
            ",".join(str(item) for item in ports)
            if isinstance(ports, list)
            else str(ports)
        )
        self.final_task_result.setChecked(
            bool(node.config.get("final_task_result", True))
        )
        self.retry_guard_enabled.setChecked(
            bool(node.config.get("retry_guard_enabled", False))
        )
        self.retry_guard_threshold.setValue(
            max(2, int(node.config.get("retry_guard_threshold", 2)))
        )
        self.learning_enabled.setChecked(
            bool(node.config.get("learning_enabled", False))
        )

        watch_all = bool(node.config.get("monitor_all", True))
        self.monitor_all.setChecked(watch_all)
        selected = {
            str(item) for item in node.config.get("monitored_nodes", []) if item
        }
        self.monitored_nodes.clear()
        for label, node_id in self._node_choices(exclude=node.id):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, node_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if watch_all or node_id in selected
                else Qt.CheckState.Unchecked
            )
            self.monitored_nodes.addItem(item)
        self.monitored_nodes.setEnabled(not watch_all)
        self.report_path.setText(str(node.config.get("report_path", "")))

        self.false_threshold.setValue(
            max(1, int(node.config.get("false_threshold", 2)))
        )
        self.auto_skip.setChecked(bool(node.config.get("auto_skip", False)))
        self.false_threshold.setEnabled(not self.auto_skip.isChecked())
        self._show_node_fields(node.kind)
        self._update_threshold_hint(node)
        self._update_prompt_field_state()

    def _show_node_fields(self, kind: str) -> None:
        visible = KIND_FIELDS.get(kind, set())
        for key, (label, widget) in self.node_rows.items():
            widget.setVisible(key in visible)
            if label:
                label.setVisible(key in visible)

    def _load_edge(self, edge: FlowEdge) -> None:
        names = {
            "true": "TRUE",
            "false": "FALSE",
            "next": "NEXT",
            "done": "DONE",
        }
        self.edge_port.setText(names.get(edge.source_port, "звичайний вихід"))
        self.edge_label.setText(edge.label)
        self.source_path.setText(edge.source_path)
        self.target_variable.setText(edge.target_variable)
        self.edge_condition.setText(edge.condition)
        self.edge_transform.setPlainText(edge.transform)

    def _save_node(self, *args: Any) -> None:
        if self.loading or not isinstance(self.current, FlowNode):
            return
        node = self.current
        if self.execution_locked:
            if node.kind != "result":
                return
            node.config["true_limit"] = self.true_limit.value()
            node.config["false_limit"] = self.false_limit.value()
            node.config["task_attempt_limit"] = self.task_attempt_limit.value()
            node.config["wait_for_confirmation"] = (
                self.wait_for_confirmation.isChecked()
            )
            self.changed.emit(node)
            return
        node.title = self.title_edit.text().strip() or NODE_LABELS[node.kind]

        if node.kind == "entry":
            node.config["text"] = self.entry_text.toPlainText()
            parsed = self._parse_json(self.entry_json)
            if parsed is not None:
                node.config["json"] = parsed
            node.config["attachments"] = self._list_values(self.attachments)
        elif node.kind == "tasks_manager":
            node.config["tasks"] = self.tasks_editor.tasks()
            node.config["task_source"] = self.task_source.currentData()
            node.config["plan_save_path"] = (
                self.plan_save_path.text().strip() or "ui_project_spec.json"
            )
        elif node.kind == "result":
            node.config["template"] = self.template_edit.toPlainText()
            node.config["save_path"] = self.save_path.text().strip()
            node.config["true_limit"] = self.true_limit.value()
            node.config["false_limit"] = self.false_limit.value()
            node.config["task_attempt_limit"] = self.task_attempt_limit.value()
            node.config["wait_for_confirmation"] = (
                self.wait_for_confirmation.isChecked()
            )
            node.config["confirmation_mode"] = self.confirmation_mode.currentData()
            node.config["confirmation_ports"] = [
                item.strip().lower()
                for item in self.confirmation_ports.text().replace(";", ",").split(",")
                if item.strip().lower() in {"true", "false", "exhausted"}
            ] or ["true", "false"]
            node.config["final_task_result"] = self.final_task_result.isChecked()
            node.config["retry_guard_enabled"] = self.retry_guard_enabled.isChecked()
            node.config["retry_guard_threshold"] = self.retry_guard_threshold.value()
            node.config["learning_enabled"] = self.learning_enabled.isChecked()
        else:
            node.config.update(
                {
                    "model": self.model_combo.currentText().strip(),
                    "reasoning_effort": self.reasoning_combo.currentText(),
                    "sandbox": self.sandbox_combo.currentData(),
                    "memory": self.memory_combo.currentData(),
                    "workspace": "",
                    "additional_folders": self._list_values(self.node_folders),
                    "instructions": self.instructions_edit.toPlainText(),
                    "instruction_files": self._list_values(self.instruction_files),
                    "prompt": self.prompt_edit.toPlainText(),
                    "prompt_source": self.prompt_source.currentData(),
                    "output_format": self.output_format.currentData(),
                    "retries": self.retries_spin.value(),
                    "attachments": self._list_values(self.attachments),
                    "skills": self.skill_pins.skills(),
                }
            )
            schema = self._parse_json(self.output_schema)
            if schema is not None:
                node.config["output_schema"] = schema
            if node.kind == "task_reviewer":
                node.config["criteria_node"] = self.criteria_combo.currentData() or ""
            elif node.kind == "work_reviewer":
                node.config["monitor_all"] = self.monitor_all.isChecked()
                node.config["monitored_nodes"] = self._checked_values(
                    self.monitored_nodes
                )
                node.config["report_path"] = self.report_path.text().strip()
            elif node.kind == "calibrator":
                node.config["false_threshold"] = self.false_threshold.value()
                node.config["auto_skip"] = self.auto_skip.isChecked()
                self._update_threshold_hint(node)

        self.changed.emit(node)

    def _update_threshold_hint(self, node: FlowNode) -> None:
        """Попередити, що за цього порога EXHAUSTED не спрацює."""
        self.threshold_hint.clear()
        if (
            node.kind != "calibrator"
            or self.workflow is None
            or bool(node.config.get("auto_skip", False))
        ):
            self.threshold_hint.setVisible(False)
            return
        threshold = max(1, int(node.config.get("false_threshold", 2)))
        limits = [
            max(1, int(source.config.get("task_attempt_limit", 2)))
            for edge in self.workflow.incoming(node.id)
            if (source := self.workflow.find(edge.source)) is not None
            and source.kind == "result"
        ]
        if limits and threshold <= min(limits):
            self.threshold_hint.setText(
                f"Поріг {threshold} спрацює раніше за ліміт спроб "
                f"{min(limits)} — вихід EXHAUSTED і червоний хрестик "
                "лишаться запобіжником і в цьому Flow не задіються."
            )
            self.threshold_hint.setVisible(True)
        else:
            self.threshold_hint.setVisible(False)

    def _auto_skip_toggled(self, checked: bool) -> None:
        self.false_threshold.setEnabled(not checked)
        self._save_node()

    def _save_edge(self, *args: Any) -> None:
        if (
            self.loading
            or self.execution_locked
            or not isinstance(self.current, FlowEdge)
        ):
            return
        edge = self.current
        edge.label = self.edge_label.text().strip()
        edge.source_path = self.source_path.text().strip() or "data"
        edge.target_variable = self.target_variable.text().strip() or "input"
        edge.condition = self.edge_condition.text().strip()
        edge.transform = self.edge_transform.toPlainText()
        self.changed.emit(edge)

    # ------------------------------------------------------------------

    @staticmethod
    def _list_values(widget: QListWidget) -> list[str]:
        if isinstance(widget, AttachmentListWidget):
            return widget.paths()
        return [widget.item(index).text() for index in range(widget.count())]

    @staticmethod
    def _checked_values(widget: QListWidget) -> list[str]:
        values: list[str] = []
        for index in range(widget.count()):
            item = widget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                values.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return values

    @staticmethod
    def _parse_json(editor: QPlainTextEdit) -> Any | None:
        try:
            parsed = json.loads(editor.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            editor.setProperty("invalid", True)
            editor.style().unpolish(editor)
            editor.style().polish(editor)
            editor.setToolTip(str(exc))
            return None
        editor.setProperty("invalid", False)
        editor.style().unpolish(editor)
        editor.style().polish(editor)
        editor.setToolTip("")
        return parsed

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _monitor_all_toggled(self, checked: bool) -> None:
        self.monitored_nodes.setEnabled(not checked)
        was_loading = self.loading
        if checked:
            self.loading = True
            for index in range(self.monitored_nodes.count()):
                self.monitored_nodes.item(index).setCheckState(Qt.CheckState.Checked)
            self.loading = was_loading
        if not was_loading:
            self._save_node()

    def _add_node_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Додаткова папка агента", self._folder_dialog_start()
        )
        if not directory:
            return
        resolved = str(Path(directory).resolve())
        if resolved not in self._list_values(self.node_folders):
            self.node_folders.addItem(resolved)
            self._save_node()

    def _remove_node_folders(self) -> None:
        for item in self.node_folders.selectedItems():
            self.node_folders.takeItem(self.node_folders.row(item))
        self._save_node()

    def _folder_dialog_start(self) -> str:
        if self.node_folders.count():
            return self.node_folders.item(0).text()
        return str(Path.cwd())

    def _prompt_source_changed(self, *args: Any) -> None:
        self._update_prompt_field_state()
        self._save_node()

    def _update_prompt_field_state(self) -> None:
        use_template = self.prompt_source.currentData() != "input"
        self.prompt_edit.setEnabled(use_template)
        row = self.node_rows.get("prompt") if hasattr(self, "node_rows") else None
        if row and row[0]:
            row[0].setEnabled(use_template)

    def _add_instruction_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Додати постійні MD-інструкції",
            self._folder_dialog_start(),
            "Markdown (*.md *.markdown)",
        )
        existing = set(self._list_values(self.instruction_files))
        for path in paths:
            resolved = str(Path(path).resolve())
            if resolved not in existing:
                self.instruction_files.addItem(resolved)
                existing.add(resolved)
        self._save_node()

    def _remove_instruction_file(self) -> None:
        for item in self.instruction_files.selectedItems():
            self.instruction_files.takeItem(self.instruction_files.row(item))
        self._save_node()

    def _choose_save_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Файл результату", self.save_path.text()
        )
        if path:
            self.save_path.setText(path)
            self._save_node()

    def _add_attachments(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Прикріпити файли і картинки",
            "",
            "Усі файли (*);;Картинки (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        existing = set(self._list_values(self.attachments))
        self.attachments.add_paths([path for path in paths if path not in existing])

    def _remove_attachment(self) -> None:
        self.attachments.remove_selected_paths()

    def _set_prompt_transfer(self) -> None:
        if not isinstance(self.current, FlowEdge):
            return
        self.source_path.setText("text")
        self.target_variable.setText("prompt")
        self.edge_transform.clear()
        self._save_edge()

    def _set_review_transfer(self) -> None:
        if not isinstance(self.current, FlowEdge):
            return
        self.source_path.setText("data")
        self.target_variable.setText("review")
        self.edge_transform.clear()
        self._save_edge()
