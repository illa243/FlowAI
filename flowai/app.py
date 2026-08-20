from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .logging_setup import (
    UiHangWatchdog,
    configure_logging,
    flush_logs,
    install_exception_hooks,
)
from .ui.main_window import MainWindow
from .ui.theme import APP_STYLE

LOGGER = logging.getLogger(__name__)


def main() -> int:
    paths = configure_logging()
    install_exception_hooks(paths)
    LOGGER.info("FlowAI process starting; Python %s", sys.version.replace("\n", " "))

    app = QApplication(sys.argv)
    app.setApplicationName("FlowAI")
    app.setOrganizationName("FlowAI")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()

    watchdog = UiHangWatchdog(paths.crash)
    heartbeat = QTimer(app)
    heartbeat.setInterval(1000)
    heartbeat.timeout.connect(watchdog.heartbeat)
    heartbeat.start()
    watchdog.start()

    try:
        exit_code = app.exec()
        LOGGER.info("FlowAI event loop exited with code %s", exit_code)
        return exit_code
    finally:
        heartbeat.stop()
        watchdog.stop()
        flush_logs()
