from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import NODE_COLORS
from ..run_history import load_runs
from ..run_stats import NodeStat, RunStats, collect_stats, merge_stats
from ..workspaces import WorkspaceSession
from .motion import AnimatedDialog

HEADERS = [
    "Блок",
    "Запусків",
    "Сумарно",
    "У середньому",
    "Токени",
    "% контексту",
]


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} с"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes):02d}:{remainder:04.1f}"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}:{minutes:02d}:{int(remainder):02d}"


class StatsDialog(AnimatedDialog):
    """Час, спроби, токени й використання контексту по блоках."""

    def __init__(
        self, session: WorkspaceSession, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"Stats — {session.display_name}")
        self.setMinimumSize(880, 540)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItem("Цей запуск", "current")
        self.scope.addItem("Усі запуски цього Flow", "history")
        self.scope.currentIndexChanged.connect(self.refresh)
        controls.addWidget(QLabel("Обсяг:"))
        controls.addWidget(self.scope)
        controls.addStretch()
        layout.addLayout(controls)

        self.tree = QTreeWidget()
        self.tree.setObjectName("generatedFilesTree")
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setColumnWidth(0, 300)
        self.tree.setAlternatingRowColors(False)
        layout.addWidget(self.tree, 1)

        self.summary = QLabel("")
        self.summary.setObjectName("mutedLabel")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _colors(self) -> dict[str, str]:
        workflow = self.session.workflow
        if workflow is None:
            return {}
        return {
            node.id: NODE_COLORS.get(node.kind, "#CBD5E1")
            for node in workflow.nodes
        }

    def _stats(self) -> RunStats:
        colors = self._colors()
        if self.scope.currentData() == "history":
            directory = self.session.run_directory
            root = Path(directory).parent if directory else None
            runs = load_runs(root) if root else []
            if not runs:
                return collect_stats(self.session.run_events, colors)
            return merge_stats([collect_stats(events, colors) for events in runs])
        return collect_stats(self.session.run_events, colors)

    def refresh(self) -> None:
        stats = self._stats()
        self.tree.clear()
        if not stats.nodes:
            empty = QTreeWidgetItem(
                ["Даних про запуски ще немає", "", "", "", "", ""]
            )
            empty.setForeground(0, QColor("#94A3B8"))
            self.tree.addTopLevelItem(empty)
            self.summary.setText("")
            return
        for node in stats.nodes:
            self.tree.addTopLevelItem(self._node_item(node))
        parts = [f"Сумарний час блоків: {format_seconds(stats.total_seconds)}"]
        if stats.tasks_total_seconds:
            parts.append(f"час завдань: {format_seconds(stats.tasks_total_seconds)}")
        if stats.run_count > 1:
            parts.append(f"запусків у вибірці: {stats.run_count}")
        self.summary.setText(" · ".join(parts))

    def _node_item(self, node: NodeStat) -> QTreeWidgetItem:
        heading = QTreeWidgetItem(
            [
                node.title,
                str(node.runs),
                format_seconds(node.total_seconds),
                format_seconds(node.average_seconds),
                f"{node.total_tokens:,}".replace(",", " "),
                f"{node.context_percent:.1f}%" if node.context_window else "—",
            ]
        )
        color = QColor(node.color)
        font = heading.font(0)
        font.setBold(True)
        heading.setFont(0, font)
        heading.setForeground(0, color)
        for column in range(1, len(HEADERS)):
            heading.setForeground(column, QColor("#E5E7EB"))
        if node.failures:
            heading.setToolTip(0, f"Помилок: {node.failures}")
        for index, seconds in enumerate(node.attempts, start=1):
            attempt = QTreeWidgetItem(
                [f"Спроба {index}", "", format_seconds(seconds), "", "", ""]
            )
            attempt.setForeground(0, QColor("#E5E7EB"))
            attempt.setForeground(2, QColor("#94A3B8"))
            heading.addChild(attempt)
        heading.setExpanded(True)
        return heading
