from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)
PATH_ROLE = Qt.ItemDataRole.UserRole
IMAGE_ROLE = Qt.ItemDataRole.UserRole + 1


class ImagePreviewDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.original = QPixmap(str(path))
        self.setWindowTitle(path.name)
        self.setStyleSheet("background: #080B12; color: white;")

        title = QLabel(path.name)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        close = QPushButton("Закрити")
        close.setObjectName("dangerButton")
        close.clicked.connect(self.accept)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(200, 160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.addLayout(header)
        layout.addWidget(self.image, 1)
        self._update_pixmap()

    def open_full_screen(self) -> None:
        self.showFullScreen()
        self.exec()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self) -> None:
        if self.original.isNull() or self.image.size().isEmpty():
            self.image.setText(f"Не вдалося відкрити зображення:\n{self.path}")
            return
        self.image.setPixmap(
            self.original.scaled(
                self.image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class AttachmentListWidget(QListWidget):
    paths_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(112, 72))
        self.setSpacing(4)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def paths(self) -> list[str]:
        return [
            str(self.item(index).data(PATH_ROLE) or self.item(index).text())
            for index in range(self.count())
        ]

    def set_paths(self, paths: list[str]) -> None:
        self.clear()
        self.add_paths(paths, emit=False)

    def add_paths(self, paths: list[str], *, emit: bool = True) -> None:
        existing = set(self.paths())
        changed = False
        for raw in paths:
            path_text = str(raw)
            if not path_text or path_text in existing:
                continue
            self.addItem(self._make_item(path_text))
            existing.add(path_text)
            changed = True
        if changed and emit:
            self.paths_changed.emit()

    def remove_selected_paths(self) -> None:
        selected = self.selectedItems()
        if not selected:
            return
        for item in selected:
            self.takeItem(self.row(item))
        self.paths_changed.emit()

    def _make_item(self, path_text: str) -> QListWidgetItem:
        path = Path(path_text).expanduser()
        item = QListWidgetItem(path.name or path_text)
        item.setData(PATH_ROLE, path_text)
        item.setToolTip(path_text)
        is_image = path.suffix.casefold() in IMAGE_SUFFIXES
        item.setData(IMAGE_ROLE, is_image)
        if is_image and path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                thumbnail = pixmap.scaled(
                    self.iconSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item.setIcon(QIcon(thumbnail))
                item.setSizeHint(QSize(220, 82))
        return item

    def _item_clicked(self, item: QListWidgetItem) -> None:
        if not bool(item.data(IMAGE_ROLE)):
            return
        path = Path(str(item.data(PATH_ROLE))).expanduser()
        if path.is_file():
            ImagePreviewDialog(path, self).open_full_screen()

    def mouseReleaseEvent(self, event: Any) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            self._item_clicked(item)

    def _show_context_menu(self, position: QPoint) -> None:
        item = self.itemAt(position)
        if item is None:
            return
        self.setCurrentItem(item)
        path = Path(str(item.data(PATH_ROLE))).expanduser()
        menu = QMenu(self)
        reveal = menu.addAction("Відкрити у Провіднику")
        copy_path = menu.addAction("Скопіювати шлях")
        menu.addSeparator()
        remove = menu.addAction("Видалити зі списку")
        chosen = menu.exec(self.viewport().mapToGlobal(position))
        if chosen is reveal:
            target = path.parent if path.parent != Path("") else Path.cwd()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))
        elif chosen is copy_path:
            QApplication.clipboard().setText(str(path))
        elif chosen is remove:
            self.takeItem(self.row(item))
            self.paths_changed.emit()

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.remove_selected_paths()
            event.accept()
            return
        super().keyPressEvent(event)
