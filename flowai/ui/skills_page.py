from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..skills import (
    CATEGORIES_FILE,
    DEFAULT_CATEGORY,
    SKILLS_ROOT,
    SkillEntry,
    categorized,
    delete_skill,
    import_skill,
    list_backups,
    list_skills,
    load_categories,
    restore_skill,
    save_categories,
)
from .controls import AnimatedButton
from .design import COLORS
from .paths import path_menu


class SkillsPage(QWidget):
    """Каталог скілів Codex: читання, категорії, вимкнення, видалення."""

    changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        root: Path = SKILLS_ROOT,
        categories_path: Path = CATEGORIES_FILE,
        codex: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.categories_path = categories_path
        self.codex = codex
        self.entries: list[SkillEntry] = []

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Скіли лежать у "
            f"{root}. Категорії зберігає FlowAI окремо — файли скілів "
            "не змінюються."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setObjectName("skillsTree")
        self.tree.setHeaderLabels(["Скіл", "Опис"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.file_picker = QComboBox()
        self.file_picker.currentIndexChanged.connect(self._show_selected_file)
        right_layout.addWidget(self.file_picker)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setFont(QFont("Cascadia Mono", 10))
        right_layout.addWidget(self.viewer, 1)
        splitter.addWidget(right)
        splitter.setSizes([320, 520])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.category_button = AnimatedButton("Категорія…", "secondary")
        self.category_button.clicked.connect(self._ask_category)
        self.enable_button = AnimatedButton("Вимкнути", "secondary")
        self.enable_button.clicked.connect(self._toggle_enabled)
        self.import_button = AnimatedButton("Додати з папки/zip", "secondary")
        self.import_button.clicked.connect(self._import)
        self.restore_button = AnimatedButton("Повернути копію", "ghost")
        self.restore_button.clicked.connect(self._restore)
        self.delete_button = AnimatedButton("Видалити", "ghost")
        self.delete_button.clicked.connect(self._delete)
        for button in (
            self.category_button,
            self.enable_button,
            self.import_button,
            self.restore_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        """Перечитати каталог і перебудувати дерево, зберігши вибір."""
        selected = self.current_entry()
        wanted = selected.name if selected is not None else ""
        self.entries = list_skills(self.codex, root=self.root)
        groups = categorized(self.entries, load_categories(self.categories_path))
        self.tree.clear()
        restore_item: QTreeWidgetItem | None = None
        for category, items in groups.items():
            heading = QTreeWidgetItem([category, ""])
            font = heading.font(0)
            font.setBold(True)
            heading.setFont(0, font)
            heading.setForeground(0, QColor(COLORS["accent"]))
            self.tree.addTopLevelItem(heading)
            for entry in items:
                label = entry.name if entry.enabled else f"{entry.name} (вимкнено)"
                child = QTreeWidgetItem([label, entry.description])
                child.setData(0, Qt.ItemDataRole.UserRole, entry.name)
                child.setToolTip(0, entry.error or str(entry.path))
                if entry.error:
                    child.setForeground(0, QColor(COLORS["danger"]))
                elif not entry.enabled:
                    child.setForeground(0, QColor(COLORS["text_dim"]))
                heading.addChild(child)
                if entry.name == wanted:
                    restore_item = child
            heading.setExpanded(True)
        if restore_item is not None:
            self.tree.setCurrentItem(restore_item)
        self._update_buttons()

    def current_entry(self) -> SkillEntry | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        name = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        return next((entry for entry in self.entries if entry.name == name), None)

    def set_category(self, name: str, category: str) -> None:
        mapping = load_categories(self.categories_path)
        if category.strip():
            mapping[name] = category.strip()
        else:
            mapping.pop(name, None)
        save_categories(mapping, self.categories_path)
        self.refresh()
        self.changed.emit()

    def _selection_changed(self, *args: object) -> None:
        entry = self.current_entry()
        self.file_picker.blockSignals(True)
        self.file_picker.clear()
        if entry is not None:
            for path in entry.markdown_files:
                self.file_picker.addItem(path.name, str(path))
        self.file_picker.blockSignals(False)
        self._show_selected_file()
        self._update_buttons()

    def _show_selected_file(self, *args: object) -> None:
        path = str(self.file_picker.currentData() or "")
        if not path:
            entry = self.current_entry()
            self.viewer.setPlainText(entry.error if entry else "")
            return
        try:
            self.viewer.setPlainText(
                Path(path).read_text(encoding="utf-8-sig", errors="replace")
            )
        except OSError as exc:
            self.viewer.setPlainText(str(exc))

    def _update_buttons(self) -> None:
        entry = self.current_entry()
        editable = entry is not None and entry.editable
        self.delete_button.setEnabled(bool(editable))
        self.category_button.setEnabled(entry is not None)
        self.enable_button.setEnabled(entry is not None)
        self.restore_button.setEnabled(
            entry is not None and bool(list_backups(entry.name))
        )
        if entry is not None:
            self.enable_button.setText("Вимкнути" if entry.enabled else "Увімкнути")

    def _ask_category(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        known = sorted(
            {value for value in load_categories(self.categories_path).values()}
        )
        current = entry.category if entry.category != DEFAULT_CATEGORY else ""
        name, accepted = QInputDialog.getItem(
            self,
            "Категорія скіла",
            f"У яку секцію віднести «{entry.name}»?",
            known or ["Images", "Code", "Design", "Data"],
            known.index(current) if current in known else 0,
            True,
        )
        if accepted:
            self.set_category(entry.name, name)

    def _toggle_enabled(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        if self.codex is None or not self.codex.set_skill_enabled(
            entry.name, not entry.enabled
        ):
            QMessageBox.information(
                self,
                "Codex недоступний",
                "Змінити стан скіла можна лише при активному з'єднанні "
                "з Codex. Запустіть будь-який Flow і спробуйте знову.",
            )
            return
        self.refresh()
        self.changed.emit()

    def _import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Архів скіла", str(Path.home()), "Архіви (*.zip)"
        )
        if not path:
            path = QFileDialog.getExistingDirectory(
                self, "Папка скіла", str(Path.home())
            )
        if not path:
            return
        try:
            entry = import_skill(Path(path), root=self.root)
        except (OSError, ValueError, FileExistsError) as exc:
            QMessageBox.warning(self, "Не вдалося додати скіл", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(
            self, "Скіл додано", f"«{entry.name}» тепер у каталозі"
        )

    def _restore(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        backups = list_backups(entry.name)
        if not backups:
            return
        names = [path.name for path in backups]
        name, accepted = QInputDialog.getItem(
            self,
            "Повернути копію",
            f"Яку копію «{entry.name}» відновити?",
            names,
            len(names) - 1,
            False,
        )
        if not accepted:
            return
        restore_skill(backups[names.index(name)], entry)
        self.refresh()
        self.changed.emit()

    def _delete(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Видалити скіл?",
            f"Папка «{entry.path}» піде в Кошик Windows.\n"
            "Скіл зникне з усіх ваших проєктів і з Codex CLI.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_skill(entry)
        except (OSError, PermissionError) as exc:
            QMessageBox.warning(self, "Не вдалося видалити", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _context_menu(self, position: Any) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        path_menu(str(entry.skill_file), self).exec(
            self.tree.viewport().mapToGlobal(position)
        )
