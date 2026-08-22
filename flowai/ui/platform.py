from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

LOGGER = logging.getLogger(__name__)
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19


def apply_dark_titlebar(widget: QWidget) -> bool:
    """Зробити системну шапку вікна темною; поза Windows нічого не робити."""
    if sys.platform != "win32":
        return False
    try:
        handle = wintypes.HWND(int(widget.winId()))
    except (RuntimeError, TypeError, ValueError):
        return False
    value = ctypes.c_int(1)
    for attribute in (
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY,
    ):
        try:
            status = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle,
                ctypes.c_int(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except (AttributeError, OSError):
            LOGGER.debug("DwmSetWindowAttribute недоступний", exc_info=True)
            return False
        if status == 0:
            return True
    return False


class DarkTitleBarFilter(QObject):
    """Чіпляє темну шапку кожному вікну верхнього рівня при першому показі."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._done: set[int] = set()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            key = id(watched)
            if key not in self._done:
                self._done.add(key)
                apply_dark_titlebar(watched)
        return super().eventFilter(watched, event)


def install_dark_titlebar(app: QApplication) -> DarkTitleBarFilter:
    dark_filter = DarkTitleBarFilter(app)
    app.installEventFilter(dark_filter)
    return dark_filter
