from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.app import warm_codex_sdk
from flowai.ui.branding import APP_ICON_PATH, application_icon, export_windows_icon


def test_warm_codex_sdk_loads_the_module() -> None:
    """SDK має бути в пам'яті ще до того, як інтерфейс стане інтерактивним."""
    for name in [item for item in sys.modules if item.startswith("openai_codex")]:
        del sys.modules[name]
    assert warm_codex_sdk() is True
    assert "openai_codex" in sys.modules


def test_warm_codex_sdk_survives_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без встановленого SDK програма має стартувати, а не падати."""
    for name in [item for item in sys.modules if item.startswith("openai_codex")]:
        del sys.modules[name]
    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "openai_codex":
            raise ImportError("немає такого модуля")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert warm_codex_sdk() is False


def test_application_icon_is_packaged() -> None:
    QApplication.instance() or QApplication([])
    assert APP_ICON_PATH.is_file()
    assert application_icon().isNull() is False


def test_application_icon_can_be_exported_for_windows(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    target = export_windows_icon(tmp_path / "FlowAI.ico")
    assert target.is_file()
    assert target.read_bytes().startswith(b"\x00\x00\x01\x00")
