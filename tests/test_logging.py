from __future__ import annotations

import logging
import sys
from pathlib import Path

from flowai.logging_setup import (
    UiHangWatchdog,
    configure_logging,
    flush_logs,
    record_unhandled_exception,
)


def test_application_and_crash_logs_are_separate(tmp_path: Path) -> None:
    paths = configure_logging(tmp_path)
    logging.getLogger("flowai.test").info("regular diagnostic message")

    try:
        raise RuntimeError("synthetic crash")
    except RuntimeError:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        assert exc_type is not None and exc_value is not None
        record_unhandled_exception("logging test", exc_type, exc_value, exc_traceback)

    flush_logs()
    assert "regular diagnostic message" in paths.application.read_text(encoding="utf-8")
    crash_text = paths.crash.read_text(encoding="utf-8")
    assert "Unhandled exception in logging test" in crash_text
    assert "synthetic crash" in crash_text
    assert "regular diagnostic message" not in crash_text


def test_ui_watchdog_writes_thread_dump_to_crash_log(tmp_path: Path) -> None:
    paths = configure_logging(tmp_path)
    watchdog = UiHangWatchdog(paths.crash)
    watchdog._write_dump(16.0)

    crash_text = paths.crash.read_text(encoding="utf-8")
    assert "UI HANG DETECTED" in crash_text
    assert "16.0s without heartbeat" in crash_text
