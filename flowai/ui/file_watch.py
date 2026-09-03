from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

LOGGER = logging.getLogger(__name__)

IGNORED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".uv",
        ".uv-cache",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        "__pycache__",
        "node_modules",
        "runs",
    }
)
IGNORED_SUFFIXES = frozenset({".tmp", ".part", ".lock"})
MAX_SIZE_BYTES = 64 * 1024 * 1024
MAX_WATCHED_DIRECTORIES = 400
# Сторож живе в головному потоці Qt. Проміжні результати не потребують
# реакції за частки секунди, а кожне сканування відбирає час у інтерфейсу.
RESCAN_INTERVAL_MS = 2000


def _ignored_name(name: str) -> bool:
    return name.casefold() in IGNORED_PARTS


def _ignored_file_name(name: str) -> bool:
    return name.startswith("~") or Path(name).suffix.casefold() in IGNORED_SUFFIXES


def is_interesting(path: Path) -> bool:
    """Return whether a path may be shown as an intermediate result."""
    if any(_ignored_name(part) for part in path.parts):
        return False
    if _ignored_file_name(path.name):
        return False
    try:
        if path.is_file() and path.stat().st_size > MAX_SIZE_BYTES:
            return False
    except OSError:
        return False
    return True


def scan(root: Path) -> tuple[list[Path], list[Path]]:
    """Обійти теку один раз і повернути (теки, файли).

    Раніше сторож ходив по дереву двічі — окремо заради нових тек і окремо
    заради нових файлів — і на кожному файлі робив зайвий `stat`. `DirEntry`
    віддає тип і розмір із того самого читання каталогу.
    """
    directories: list[Path] = [root]
    files: list[Path] = []
    pending: list[Path] = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if _ignored_name(name):
                                continue
                            child = Path(entry.path)
                            directories.append(child)
                            pending.append(child)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if _ignored_file_name(name):
                            continue
                        if entry.stat().st_size > MAX_SIZE_BYTES:
                            continue
                    except OSError:
                        continue
                    files.append(Path(entry.path))
        except OSError:
            LOGGER.debug("Не вдалося обійти %s", current, exc_info=True)
    return directories, files


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
        directories, files = self._scan_roots()
        if directories:
            self._watcher.addPaths(directories)
        self._known = {str(path) for path in files}

    def stop(self) -> None:
        self._timer.stop()
        for group in (self._watcher.files(), self._watcher.directories()):
            if group:
                self._watcher.removePaths(group)
        self._known.clear()
        self._roots = []

    def _schedule(self) -> None:
        self._timer.start()

    def _scan_roots(self) -> tuple[list[str], list[Path]]:
        """Один обхід на корінь: і теки для стеження, і файли для показу."""
        directories: list[str] = []
        files: list[Path] = []
        for root in self._roots:
            found_directories, found_files = scan(root)
            directories.extend(str(item) for item in found_directories)
            files.extend(found_files)
        return list(dict.fromkeys(directories)), files

    def rescan(self) -> None:
        directories, files = self._scan_roots()
        watched = set(self._watcher.directories())
        additions = [item for item in directories if item not in watched]
        room = MAX_WATCHED_DIRECTORIES - len(watched)
        if additions and room > 0:
            self._watcher.addPaths(additions[:room])
        for path in files:
            text = str(path)
            if text in self._known:
                continue
            self._known.add(text)
            self.file_ready.emit(text)
