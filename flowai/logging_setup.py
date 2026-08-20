from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import TextIO

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

APP_LOG_NAME = "flowai.log"
CRASH_LOG_NAME = "crash.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5

_HANDLER_TAG = "flowai_file_handler"
_CRASH_HANDLER_TAG = "flowai_crash_handler"
_CRASH_LOGGER_NAME = "flowai.crash"
_HOOKS_INSTALLED = False
_FAULT_STREAM: TextIO | None = None
_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class LogPaths:
    directory: Path
    application: Path
    crash: Path


def default_log_directory() -> Path:
    override = os.environ.get("FLOWAI_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "FlowAI" / "logs"


def log_paths(directory: Path | None = None) -> LogPaths:
    target = (directory or default_log_directory()).expanduser().resolve()
    return LogPaths(target, target / APP_LOG_NAME, target / CRASH_LOG_NAME)


def _tagged_rotating_handler(
    path: Path,
    *,
    tag: str,
    level: int,
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    setattr(handler, tag, True)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)s %(name)s "
            "[%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def _replace_tagged_handler(
    logger: logging.Logger,
    *,
    tag: str,
    path: Path,
    level: int,
) -> None:
    desired = str(path.resolve())
    for handler in list(logger.handlers):
        if not getattr(handler, tag, False):
            continue
        current = str(Path(getattr(handler, "baseFilename", "")).resolve())
        if current == desired:
            return
        logger.removeHandler(handler)
        handler.close()
    logger.addHandler(_tagged_rotating_handler(path, tag=tag, level=level))


def configure_logging(directory: Path | None = None) -> LogPaths:
    paths = log_paths(directory)
    paths.directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(min(root.level or logging.INFO, logging.INFO))
    _replace_tagged_handler(
        root,
        tag=_HANDLER_TAG,
        path=paths.application,
        level=logging.INFO,
    )

    crash_logger = logging.getLogger(_CRASH_LOGGER_NAME)
    crash_logger.setLevel(logging.ERROR)
    crash_logger.propagate = False
    _replace_tagged_handler(
        crash_logger,
        tag=_CRASH_HANDLER_TAG,
        path=paths.crash,
        level=logging.ERROR,
    )
    logging.getLogger(__name__).info("File logging initialized: %s", paths.directory)
    return paths


def _crash_logger() -> logging.Logger:
    return logging.getLogger(_CRASH_LOGGER_NAME)


def record_unhandled_exception(
    context: str,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    _crash_logger().critical(
        "Unhandled exception in %s",
        context,
        exc_info=(exc_type, exc_value, exc_traceback),
    )


def record_background_exception(context: str, exc: BaseException) -> None:
    logging.getLogger(__name__).error(
        "Background operation failed: %s",
        context,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def _qt_message_handler(
    message_type: QtMsgType,
    context: object,
    message: str,
) -> None:
    category = getattr(context, "category", "") or "qt"
    logger = logging.getLogger(f"qt.{category}")
    if message_type == QtMsgType.QtDebugMsg:
        logger.debug(message)
    elif message_type == QtMsgType.QtInfoMsg:
        logger.info(message)
    elif message_type == QtMsgType.QtWarningMsg:
        logger.warning(message)
    elif message_type == QtMsgType.QtCriticalMsg:
        logger.error(message)
    elif message_type == QtMsgType.QtFatalMsg:
        _crash_logger().critical("Qt fatal message [%s]: %s", category, message)


def install_exception_hooks(paths: LogPaths | None = None) -> LogPaths:
    global _FAULT_STREAM, _HOOKS_INSTALLED
    configured = paths or configure_logging()
    if _HOOKS_INSTALLED:
        return configured

    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def sys_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        record_unhandled_exception("main thread", exc_type, exc_value, exc_traceback)
        original_sys_hook(exc_type, exc_value, exc_traceback)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "unknown thread"
        record_unhandled_exception(
            f"thread {name}",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )
        original_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
    qInstallMessageHandler(_qt_message_handler)

    try:
        _FAULT_STREAM = configured.crash.open("a", encoding="utf-8", buffering=1)
        _FAULT_STREAM.write(
            f"\n--- Native crash capture enabled {datetime.now().astimezone().isoformat()} ---\n"
        )
        faulthandler.enable(file=_FAULT_STREAM, all_threads=True)
    except (OSError, RuntimeError):
        logging.getLogger(__name__).exception("Could not enable native crash capture")

    _HOOKS_INSTALLED = True
    return configured


def flush_logs() -> None:
    for logger in (logging.getLogger(), _crash_logger()):
        for handler in logger.handlers:
            handler.flush()
    if _FAULT_STREAM is not None:
        _FAULT_STREAM.flush()


class UiHangWatchdog:
    """Write all Python stacks when the Qt event loop stops sending heartbeats."""

    def __init__(self, crash_path: Path, *, timeout_seconds: float = 15.0) -> None:
        self.crash_path = crash_path
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self._heartbeat_at = time.monotonic()
        self._stop = threading.Event()
        self._reported = False
        self._thread = threading.Thread(
            target=self._monitor,
            name="FlowAI-UI-Watchdog",
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def heartbeat(self) -> None:
        self._heartbeat_at = time.monotonic()
        self._reported = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _monitor(self) -> None:
        interval = min(1.0, self.timeout_seconds / 2.0)
        while not self._stop.wait(interval):
            delay = time.monotonic() - self._heartbeat_at
            if delay < self.timeout_seconds or self._reported:
                continue
            self._reported = True
            self._write_dump(delay)

    def _write_dump(self, delay: float) -> None:
        with _WRITE_LOCK:
            try:
                self.crash_path.parent.mkdir(parents=True, exist_ok=True)
                with self.crash_path.open("a", encoding="utf-8") as stream:
                    stream.write(
                        "\n--- UI HANG DETECTED "
                        f"{datetime.now().astimezone().isoformat()} "
                        f"({delay:.1f}s without heartbeat) ---\n"
                    )
                    faulthandler.dump_traceback(file=stream, all_threads=True)
            except (OSError, RuntimeError):
                logging.getLogger(__name__).exception("Could not write UI hang dump")
        logging.getLogger(__name__).error(
            "Qt event loop did not respond for %.1f seconds; stacks written to %s",
            delay,
            self.crash_path,
        )
