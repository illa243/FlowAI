"""Ціна одного сканування робочих тек.

Сторож працює в головному потоці Qt і повторюється кілька разів на секунду.
Кожен зайвий обхід дерева — це відібраний у інтерфейсу час.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.file_watch import RunFileWatcher, is_interesting


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def workspace(tmp_path: Path) -> Path:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "board.png").write_bytes(b"x")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "cut_step.py").write_text("pass", encoding="utf-8")
    return tmp_path


def test_package_caches_are_not_interesting(tmp_path: Path) -> None:
    assert is_interesting(tmp_path / "tools" / ".uv-cache" / "wheel.whl") is False
    assert is_interesting(tmp_path / "tools" / ".uv" / "state.json") is False
    assert is_interesting(tmp_path / ".mypy_cache" / "cache.json") is False
    assert is_interesting(tmp_path / "artifacts" / "board.png") is True


def test_a_cached_wheel_never_reaches_the_results_panel(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    cache = root / "tools" / ".uv-cache"
    cache.mkdir()
    application()
    watcher = RunFileWatcher()
    seen: list[str] = []
    watcher.file_ready.connect(seen.append)
    watcher.start([root])

    (cache / "numpy.whl").write_bytes(b"wheel")
    (root / "artifacts" / "cutout.png").write_bytes(b"png")
    watcher.rescan()

    assert any("cutout.png" in item for item in seen)
    assert not any("numpy.whl" in item for item in seen)
    watcher.stop()


def test_one_rescan_walks_every_directory_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)
    (root / "artifacts" / "qa").mkdir()
    application()
    watcher = RunFileWatcher()
    watcher.start([root])

    visited: list[str] = []
    real_scandir = os.scandir

    def counting_scandir(path: object = ".") -> object:
        visited.append(str(path))
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "scandir", counting_scandir)
    watcher.rescan()
    monkeypatch.undo()

    directories = {root, root / "artifacts", root / "artifacts" / "qa", root / "tools"}
    assert len(visited) == len(directories), (
        f"дерево обійдено {len(visited)} разів замість {len(directories)}: "
        "сканування дублюється"
    )
    watcher.stop()
