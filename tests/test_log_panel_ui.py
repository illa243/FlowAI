from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.log_panel import LogPanel


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_paths_become_clickable_anchors(tmp_path: Path) -> None:
    application()
    target = tmp_path / "звіт.md"
    target.write_text("дані", encoding="utf-8")
    panel = LogPanel()
    panel.append_entry(
        {
            "timestamp": "12:00:00",
            "text": f"Готово: {target}",
            "color": "#7C3AED",
            "file_paths": [str(target)],
        }
    )
    html_text = panel.view.toHtml()
    assert "flowai-file:" in html_text
    panel.deleteLater()


def test_activity_line_shows_and_hides() -> None:
    application()
    panel = LogPanel()
    assert panel.activity_label.isVisible() is False
    panel.set_activity("Виконує: python gen.py", "#7C3AED")
    panel.flush_activity()
    assert panel.activity_label.text().endswith("python gen.py")
    panel.set_activity("", "")
    panel.flush_activity()
    assert panel.activity_label.isVisible() is False
    panel.deleteLater()
