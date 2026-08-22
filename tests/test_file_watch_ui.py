from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.file_watch import RunFileWatcher, is_interesting


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ignores_service_folders(tmp_path: Path) -> None:
    assert is_interesting(tmp_path / "картинка.png") is True
    assert is_interesting(tmp_path / ".git" / "index") is False
    assert is_interesting(tmp_path / "__pycache__" / "a.pyc") is False
    assert is_interesting(tmp_path / "runs" / "flowai-run.json") is False


def test_watcher_reports_new_file(tmp_path: Path) -> None:
    application()
    watcher = RunFileWatcher()
    seen: list[str] = []
    watcher.file_ready.connect(seen.append)
    watcher.start([tmp_path])
    (tmp_path / "новий.png").write_bytes(b"x")
    watcher.rescan()
    assert any("новий.png" in item for item in seen)
    watcher.stop()
