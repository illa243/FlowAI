from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..workspaces import WorkspaceSession
from .motion import AnimatedDialog
from .paths import open_file, path_menu

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
THUMBNAIL_HEIGHT = 46
PREVIEW_SIZE = QSize(420, 280)


def _preview_pixmap(path: Path, size: QSize = PREVIEW_SIZE) -> QPixmap:
    """Decode a bounded preview instead of loading a huge source at full size."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid():
        reader.setScaledSize(
            source_size.scaled(size, Qt.AspectRatioMode.KeepAspectRatio)
        )
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


class ResultImageGallery(QScrollArea):
    """Large, directly visible previews for image results."""

    def __init__(
        self, paths: list[str] | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.paths: list[str] = []
        self.previews: list[QLabel] = []
        self.setObjectName("resultImageGallery")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(220)
        self.setMaximumHeight(440)
        self.set_paths(paths or [])

    def set_paths(self, paths: list[str]) -> None:
        self.paths = [
            raw
            for raw in paths
            if Path(raw).suffix.casefold() in IMAGE_SUFFIXES and Path(raw).is_file()
        ]
        self.previews = []
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        for index, raw in enumerate(self.paths):
            path = Path(raw)
            card = QFrame()
            card.setObjectName("resultImageCard")
            card_layout = QVBoxLayout(card)
            preview = QLabel()
            preview.setObjectName("resultImagePreview")
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setMinimumSize(220, 160)
            preview.setToolTip(raw)
            preview.setCursor(Qt.CursorShape.PointingHandCursor)
            pixmap = _preview_pixmap(path)
            if not pixmap.isNull():
                preview.setPixmap(pixmap)
            preview.mouseDoubleClickEvent = (  # type: ignore[method-assign]
                lambda _event, selected=raw: open_file(selected)
            )
            preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            preview.customContextMenuRequested.connect(
                lambda position, selected=raw, widget=preview: path_menu(
                    selected, widget
                ).exec(widget.mapToGlobal(position))
            )
            caption = QLabel(path.name)
            caption.setObjectName("mutedLabel")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            caption.setToolTip(raw)
            card_layout.addWidget(preview, 1)
            card_layout.addWidget(caption)
            grid.addWidget(card, index // 2, index % 2)
            self.previews.append(preview)
        grid.setRowStretch((len(self.paths) + 1) // 2, 1)
        self.setWidget(content)
        self.setHidden(not self.paths)


class ResultsDialog(AnimatedDialog):
    """Підсумок завершеного Flow: що вийшло і де це лежить."""

    def __init__(
        self, session: WorkspaceSession, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"Результати — {session.display_name}")
        self.setMinimumSize(960, 700)

        layout = QVBoxLayout(self)
        layout.addWidget(self._headline())

        self.gallery = ResultImageGallery()
        layout.addWidget(self.gallery, 1)

        self.files = QTreeWidget()
        self.files.setObjectName("generatedFilesTree")
        self.files.setColumnCount(2)
        self.files.setHeaderLabels(["Файл", "Повний шлях"])
        self.files.setColumnWidth(0, 300)
        self.files.setAlternatingRowColors(False)
        self.files.setIconSize(QSize(THUMBNAIL_HEIGHT, THUMBNAIL_HEIGHT))
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.files, 1)

        self.all_files_button = QPushButton("Усі файли запуску")
        self.stats_button = QPushButton("Статистика")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(
            self.all_files_button, QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.addButton(self.stats_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _headline(self) -> QLabel:
        tasks = [
            state for states in self.session.task_states.values() for state in states
        ]
        done = sum(1 for item in tasks if item.get("status") == "completed")
        failed = sum(1 for item in tasks if item.get("status") == "failed")
        seconds = sum(float(item.get("seconds", 0.0) or 0.0) for item in tasks)
        parts = [f"Flow «{self.session.display_name}» завершено"]
        if tasks:
            parts.append(f"виконано {done}/{len(tasks)}")
            if failed:
                parts.append(f"провалено {failed}")
            parts.append(f"час завдань {seconds:.1f} с")
        label = QLabel(" · ".join(parts))
        label.setObjectName("sectionTitle")
        label.setWordWrap(True)
        return label

    def result_paths(self) -> list[str]:
        paths: list[str] = []
        for group in self.session.generated_file_groups:
            for raw in group.get("result", []):
                text = str(raw)
                if text and text not in paths:
                    paths.append(text)
        return paths

    def _fill(self) -> None:
        self.files.clear()
        paths = self.result_paths()
        self.gallery.set_paths(paths)
        if not paths:
            empty = QTreeWidgetItem(["Фінальних файлів немає", ""])
            empty.setForeground(0, QColor("#94A3B8"))
            self.files.addTopLevelItem(empty)
            return
        for raw in paths:
            path = Path(raw)
            item = QTreeWidgetItem([path.name or raw, raw])
            item.setData(0, Qt.ItemDataRole.UserRole, raw)
            item.setForeground(0, QColor("#E5E7EB"))
            item.setForeground(1, QColor("#94A3B8"))
            if path.suffix.casefold() in IMAGE_SUFFIXES and path.is_file():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    scaled = pixmap.scaledToHeight(
                        THUMBNAIL_HEIGHT,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    item.setIcon(0, QIcon(scaled))
            self.files.addTopLevelItem(item)

    def _context_menu(self, position: QPoint) -> None:
        item = self.files.itemAt(position)
        if item is None:
            return
        raw_path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if raw_path:
            path_menu(raw_path, self).exec(
                self.files.viewport().mapToGlobal(position)
            )
