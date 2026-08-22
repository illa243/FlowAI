from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..calibration import ProposedEdit
from .design import COLORS

ADDED_BACKGROUND = "#12341F"
REMOVED_BACKGROUND = "#3A1620"
ROW_HEIGHT = 20
MAX_VISIBLE_ROWS = 18


@dataclass(slots=True)
class DiffRow:
    """Один рядок split-diff: що було ліворуч і що стало праворуч."""

    kind: str
    left: str = ""
    right: str = ""
    left_number: int = 0
    right_number: int = 0


def build_rows(before: str, after: str) -> list[DiffRow]:
    """Порівняти два тексти по рядках, як у SVN або GitHub."""
    left_lines = before.splitlines() or [""]
    right_lines = after.splitlines() or [""]
    matcher = SequenceMatcher(None, left_lines, right_lines, autojunk=False)
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append(
                    DiffRow(
                        kind="equal",
                        left=left_lines[i1 + offset],
                        right=right_lines[j1 + offset],
                        left_number=i1 + offset + 1,
                        right_number=j1 + offset + 1,
                    )
                )
            continue
        removed = left_lines[i1:i2]
        added = right_lines[j1:j2]
        for offset in range(max(len(removed), len(added))):
            has_left = offset < len(removed)
            has_right = offset < len(added)
            if has_left and has_right:
                kind = "replace"
            elif has_left:
                kind = "delete"
            else:
                kind = "insert"
            rows.append(
                DiffRow(
                    kind=kind,
                    left=removed[offset] if has_left else "",
                    right=added[offset] if has_right else "",
                    left_number=i1 + offset + 1 if has_left else 0,
                    right_number=j1 + offset + 1 if has_right else 0,
                )
            )
    return rows


class DiffView(QWidget):
    """Одна правка з оригіналом, зміною та галочкою застосування."""

    toggled = Signal(bool)

    def __init__(self, edit: ProposedEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = edit

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(4)

        self.checkbox = QCheckBox(edit.label or "Правка")
        self.checkbox.setChecked(edit.accepted)
        self.checkbox.toggled.connect(self._toggled)
        layout.addWidget(self.checkbox)

        self.path_label = QLabel(edit.display_path)
        self.path_label.setObjectName("mutedLabel")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.path_label)

        self.rationale_label = QLabel(edit.rationale)
        self.rationale_label.setObjectName("mutedLabel")
        self.rationale_label.setWordWrap(True)
        self.rationale_label.setVisible(bool(edit.rationale.strip()))
        layout.addWidget(self.rationale_label)

        self.rows = build_rows(edit.before, edit.after)
        self.table = QTableWidget(len(self.rows), 4)
        self.table.setObjectName("diffTable")
        self.table.setHorizontalHeaderLabels(["", "Було", "", "Стало"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._fill_table()
        layout.addWidget(self.table)

    @property
    def accepted(self) -> bool:
        return self.checkbox.isChecked()

    def _toggled(self, checked: bool) -> None:
        self.edit.accepted = checked
        self.table.setEnabled(checked)
        self.toggled.emit(checked)

    def _fill_table(self) -> None:
        mono = QFont("Cascadia Mono", 9)
        for index, row in enumerate(self.rows):
            cells = [
                (0, str(row.left_number or ""), COLORS["text_dim"], ""),
                (
                    1,
                    row.left,
                    COLORS["text"],
                    REMOVED_BACKGROUND
                    if row.kind in {"replace", "delete"}
                    else "",
                ),
                (2, str(row.right_number or ""), COLORS["text_dim"], ""),
                (
                    3,
                    row.right,
                    COLORS["text"],
                    ADDED_BACKGROUND
                    if row.kind in {"replace", "insert"}
                    else "",
                ),
            ]
            for column, text, foreground, background in cells:
                item = QTableWidgetItem(text)
                item.setFont(mono)
                item.setForeground(QColor(foreground))
                if background:
                    item.setBackground(QColor(background))
                self.table.setItem(index, column, item)
            self.table.setRowHeight(index, ROW_HEIGHT)
        visible = min(len(self.rows), MAX_VISIBLE_ROWS)
        self.table.setMinimumHeight(ROW_HEIGHT * (visible + 1) + 8)
