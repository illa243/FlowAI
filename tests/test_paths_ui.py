from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from flowai.ui.paths import copy_path, path_menu


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_copy_path_puts_text_in_clipboard(tmp_path: Path) -> None:
    application()
    target = tmp_path / "file.txt"
    target.write_text("дані", encoding="utf-8")
    copy_path(str(target))
    assert QGuiApplication.clipboard().text() == str(target)


def test_path_menu_has_expected_actions(tmp_path: Path) -> None:
    application()
    image = tmp_path / "picture.png"
    image.write_bytes(b"")
    menu = path_menu(str(image))
    titles = [action.text() for action in menu.actions() if action.text()]
    assert "Відкрити" in titles
    assert "Показати в Провіднику" in titles
    assert "Копіювати шлях" in titles
    assert "Копіювати картинку" in titles
