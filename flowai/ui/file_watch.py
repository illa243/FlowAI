from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

LOGGER = logging.getLogger(__name__)

IGNORED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "runs",
    }
)
MAX_SIZE_BYTES = 64 * 1024 * 1024
MAX_WATCHED_DIRECTORIES = 400
RESCAN_INTERVAL_MS = 900


def is_interesting(path: Path) -> bool:
    """Return whether a path may be shown as an intermediate result."""
    if any(part.casefold() in IGNORED_PARTS for part in path.parts):
        return False
    if path.name.startswith("~") or path.suffix.casefold() in {
        ".tmp",
        ".part",
        ".lock",
    }:
        return False
    try:
        if path.is_file() and path.stat().st_size > MAX_SIZE_BYTES:
            return False
    except OSError:
        return False
    return True


def _walk(root: Path) -> Iterator[tuple[Path, list[Path]]]:
    """Walk a root while pruning service directories before descending."""
    for directory, names, files in os.walk(root, onerror=lambda error: None):
        base = Path(directory)
        names[:] = [name for name in names if is_interesting(base / name)]
        yield base, [base / name for name in files]


class RunFileWatcher(QObject):
    """Watch working folders while a Flow is running."""

    file_ready = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _path: self._schedule())
        self._known: set[str] = set()
        self._roots: list[Path] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RESCAN_INTERVAL_MS)
        self._timer.timeout.connect(self.rescan)

    def start(self, roots: list[Path]) -> None:
        self.stop()
        self._roots = [Path(root).resolve() for root in roots if Path(root).is_dir()]
        directories: list[str] = []
        for root in self._roots:
            for directory, _files in _walk(root):
                if len(directories) >= MAX_WATCHED_DIRECTORIES:
                    break
                directories.append(str(directory))
        if directories:
            self._watcher.addPaths(list(dict.fromkeys(directories)))
        self._known = {str(path) for path in self._collect()}

    def stop(self) -> None:
        self._timer.stop()
        for group in (self._watcher.files(), self._watcher.directories()):
            if group:
                self._watcher.removePaths(group)
        self._known.clear()
        self._roots = []

    def _schedule(self) -> None:
        self._timer.start()

    def _watch_new_directories(self) -> None:
        watched = set(self._watcher.directories())
        additions: list[str] = []
        for root in self._roots:
            for directory, _files in _walk(root):
                text = str(directory)
                if text in watched:
                    continue
                additions.append(text)
                watched.add(text)
                if len(watched) >= MAX_WATCHED_DIRECTORIES:
                    break
        if additions:
            self._watcher.addPaths(additions)

    def _collect(self) -> list[Path]:
        found: list[Path] = []
        for root in self._roots:
            try:
                for _directory, files in _walk(root):
                    found.extend(
                        path
                        for path in files
                        if path.is_file() and is_interesting(path)
                    )
            except OSError:
                LOGGER.debug("Не вдалося обійти %s", root, exc_info=True)
        return found

    def rescan(self) -> None:
        self._watch_new_directories()
        for path in self._collect():
            text = str(path)
            if text in self._known:
                continue
            self._known.add(text)
            self.file_ready.emit(text)
