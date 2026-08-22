from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_launcher_uses_pythonw() -> None:
    """Консольний python.exe тримає вікно cmd відкритим на весь сеанс."""
    text = (PROJECT_ROOT / "start-flowai.cmd").read_text(encoding="utf-8")
    assert "pythonw.exe" in text
    assert 'python.exe" -m flowai' not in text
    assert '%FLOWAI_PYTHON%" -m flowai' not in text


def test_entry_point_is_gui_script() -> None:
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.gui-scripts]" in text
    assert "[project.scripts]" not in text


def test_installed_shortcut_uses_flowai_icon() -> None:
    text = (PROJECT_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "flowai.ui.branding" in text
    assert "$shortcut.IconLocation" in text


def test_no_new_console_is_spawned() -> None:
    text = (PROJECT_ROOT / "flowai" / "codex_adapter.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE_NEW_CONSOLE" not in text
    assert "cmd.exe" not in text
