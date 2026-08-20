from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..codex_auth import CodexUser, read_codex_user_from_client

LOGGER = logging.getLogger(__name__)


class LoginWorker(QObject):
    browser_url_ready = Signal(str)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = threading.Event()
        self._handle: Any = None
        self._handle_lock = threading.Lock()

    @Slot()
    def run(self) -> None:
        try:
            from openai_codex import Codex

            with Codex() as codex:
                if self._cancel_requested.is_set():
                    self.cancelled.emit()
                    return
                handle = codex.login_chatgpt()
                with self._handle_lock:
                    self._handle = handle
                self.browser_url_ready.emit(handle.auth_url)

                # The queued browser signal is processed by the UI after wait() has
                # registered for completion, which keeps cancellation notifications safe.
                result = handle.wait()
                if self._cancel_requested.is_set():
                    self.cancelled.emit()
                elif result.success:
                    user = read_codex_user_from_client(codex, refresh_token=True)
                    if user is None:
                        self.failed.emit(
                            "Вхід завершено, але профіль ChatGPT не знайдено"
                        )
                    else:
                        self.completed.emit(user)
                else:
                    self.failed.emit(result.error or "Не вдалося увійти в ChatGPT")
        except Exception as exc:  # noqa: BLE001 - SDK/process boundary
            if self._cancel_requested.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        finally:
            with self._handle_lock:
                self._handle = None

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._handle_lock:
            handle = self._handle
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                LOGGER.warning("Codex login cancellation failed", exc_info=True)


class ChatGPTLoginDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.user: CodexUser | None = None
        self.error_message = ""
        self._result_state = "pending"
        self._started = False
        self._thread: QThread | None = None
        self._worker: LoginWorker | None = None

        self.setWindowTitle("Вхід у ChatGPT")
        self.setModal(True)
        self.setFixedSize(430, 210)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )

        title = QLabel("Очікуємо на вхід у ChatGPT")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label = QLabel(
            "Завершіть авторизацію у вікні браузера.\n"
            "FlowAI автоматично продовжить після успішного входу."
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("mutedLabel")
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)

        self.cancel_button = QPushButton("СКАСУВАТИ")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.clicked.connect(self.cancel_login)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(self.status_label)
        layout.addWidget(progress)
        layout.addWidget(self.cancel_button)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._start_login)

    def _start_login(self) -> None:
        thread = QThread(self)
        worker = LoginWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.browser_url_ready.connect(self._open_browser)
        worker.completed.connect(self._on_completed)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        worker.completed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._finish_dialog)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(str)
    def _open_browser(self, url: str) -> None:
        if self._result_state != "pending":
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.status_label.setText(
                "Не вдалося відкрити браузер автоматично. Спробуйте ще раз."
            )

    @Slot(object)
    def _on_completed(self, user: object) -> None:
        if isinstance(user, CodexUser):
            self.user = user
            self._result_state = "success"
            self.status_label.setText("Вхід успішний")

    @Slot()
    def _on_cancelled(self) -> None:
        self._result_state = "cancelled"

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.error_message = message
        self._result_state = "failed"

    @Slot()
    def _finish_dialog(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        if self._result_state == "success":
            self.accept()
        else:
            self.reject()

    @Slot()
    def cancel_login(self) -> None:
        if self._result_state != "pending":
            return
        self.status_label.setText("Скасовуємо вхід…")
        self.cancel_button.setEnabled(False)
        worker = self._worker
        if worker is not None:
            worker.cancel()
        else:
            self._result_state = "cancelled"
            self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._result_state == "pending":
            event.ignore()
            return
        super().closeEvent(event)
