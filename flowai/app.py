from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .logging_setup import (
    UiHangWatchdog,
    configure_logging,
    flush_logs,
    install_exception_hooks,
)
from .ui.branding import application_icon, configure_windows_app_id
from .ui.main_window import MainWindow
from .ui.platform import install_dark_titlebar
from .ui.theme import build_style
from .ui.typography import load_fonts

LOGGER = logging.getLogger(__name__)


def warm_codex_sdk() -> bool:
    """Завантажити Codex SDK до того, як інтерфейс стане інтерактивним.

    Імпорт SDK будує кількасот pydantic-моделей — близько секунди чистого
    Python-коду, який тримає GIL. Якщо лишити його лінивим, ця секунда
    припадає на фоновий потік оновлення акаунта саме тоді, коли користувач
    уже клікає по нодах, і весь інтерфейс завмирає посеред перетягування.
    Тут ми платимо цю ціну один раз на старті, де вона очікувана.
    """
    began = time.perf_counter()
    try:
        import openai_codex  # noqa: F401
    except ImportError:
        LOGGER.info("Codex SDK не встановлено — прогрів пропущено")
        return False
    LOGGER.info("Codex SDK прогріто за %.0f мс", (time.perf_counter() - began) * 1000)
    return True


def main() -> int:
    paths = configure_logging()
    install_exception_hooks(paths)
    LOGGER.info("FlowAI process starting; Python %s", sys.version.replace("\n", " "))
    warm_codex_sdk()

    configure_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("FlowAI")
    app.setOrganizationName("FlowAI")
    app.setWindowIcon(application_icon())
    app.setStyle("Fusion")
    ui_family, mono_family = load_fonts()
    base_font = QFont(ui_family, 10)
    base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(base_font)
    app.setStyleSheet(build_style(ui_family, mono_family))
    titlebar_filter = install_dark_titlebar(app)
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
        app.removeEventFilter(titlebar_filter)
        heartbeat.stop()
        watchdog.stop()
        flush_logs()
