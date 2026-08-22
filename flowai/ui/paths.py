from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QImage
from PySide6.QtWidgets import QMenu, QWidget

from .icons import icon

LOGGER = logging.getLogger(__name__)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})


def is_image(path: str) -> bool:
    return Path(path).suffix.casefold() in IMAGE_SUFFIXES


def open_file(path: str) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def reveal_in_explorer(path: str) -> bool:
    """Open the containing folder and select the file when supported."""
    target = Path(path)
    if not target.exists():
        return False
    if sys.platform == "win32":
        try:
            subprocess.Popen(
                ["explorer", f"/select,{target}"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            LOGGER.warning("Не вдалося відкрити Провідник для %s", target)
            return False
        return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))


def copy_path(path: str) -> None:
    QGuiApplication.clipboard().setText(str(path))


def copy_image(path: str) -> bool:
    image = QImage(str(path))
    if image.isNull():
        return False
    QGuiApplication.clipboard().setImage(image)
    return True


def path_menu(path: str, parent: QWidget | None = None) -> QMenu:
    """Build the standard context menu shared by all rendered paths."""
    menu = QMenu(parent)
    exists = Path(path).exists()

    open_action = QAction(icon("external-link"), "Відкрити", menu)
    open_action.setEnabled(exists)
    open_action.triggered.connect(lambda: open_file(path))
    menu.addAction(open_action)

    reveal_action = QAction(icon("folder"), "Показати в Провіднику", menu)
    reveal_action.setEnabled(exists)
    reveal_action.triggered.connect(lambda: reveal_in_explorer(path))
    menu.addAction(reveal_action)
    menu.addSeparator()

    copy_action = QAction("Копіювати шлях", menu)
    copy_action.triggered.connect(lambda: copy_path(path))
    menu.addAction(copy_action)

    if is_image(path):
        image_action = QAction("Копіювати картинку", menu)
        image_action.setEnabled(exists)
        image_action.triggered.connect(lambda: copy_image(path))
        menu.addAction(image_action)
    return menu
