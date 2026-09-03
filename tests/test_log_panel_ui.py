from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from flowai.ui import log_panel as log_panel_module
from flowai.ui.log_panel import LogPanel


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def entry(text: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": "12:00:00",
        "text": text,
        "color": "#7C3AED",
        "file_paths": [],
    }
    payload.update(extra)
    return payload


def sample_image(target: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xFF7C3AED)
    assert image.save(str(target))
    return target


def test_paths_become_clickable_anchors(tmp_path: Path) -> None:
    application()
    target = tmp_path / "звіт.md"
    target.write_text("дані", encoding="utf-8")
    panel = LogPanel()
    panel.append_entry(entry(f"Готово: {target}", file_paths=[str(target)]))
    panel.flush_log()
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


def test_a_burst_of_entries_costs_one_document_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application()
    panel = LogPanel()
    writes: list[str] = []
    monkeypatch.setattr(panel.view, "append_html", writes.append)

    for index in range(200):
        panel.append_entry(entry(f"крок {index}"))
    assert writes == []

    panel.flush_log()
    assert len(writes) == 1
    assert "крок 0" in writes[0]
    assert "крок 199" in writes[0]

    panel.flush_log()
    assert len(writes) == 1
    panel.deleteLater()


def test_every_entry_starts_on_its_own_line() -> None:
    application()
    panel = LogPanel()
    for batch in ("A", "B", "C"):
        for index in range(2):
            panel.append_entry(entry(f"{batch}{index}"))
        panel.flush_log()

    lines = panel.view.toPlainText().split("\n")
    assert [line.split("] ")[-1] for line in lines] == [
        "A0",
        "A1",
        "B0",
        "B1",
        "C0",
        "C1",
    ]
    panel.deleteLater()


def test_entries_keep_their_order_inside_a_batch() -> None:
    application()
    panel = LogPanel()
    for index in range(5):
        panel.append_entry(entry(f"крок {index}"))
    panel.flush_log()
    text = panel.view.toPlainText()
    positions = [text.index(f"крок {index}") for index in range(5)]
    assert positions == sorted(positions)
    panel.deleteLater()


def test_repeated_previews_are_decoded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    picture = sample_image(tmp_path / "кадр.png")
    decoded: list[str] = []
    original = log_panel_module.load_thumbnail

    def counted(path: Path) -> QImage | None:
        decoded.append(str(path))
        return original(path)

    monkeypatch.setattr(log_panel_module, "load_thumbnail", counted)
    panel = LogPanel()
    for _ in range(10):
        panel.append_entry(entry("кадр", image_paths=[str(picture)]))
    panel.flush_log()
    panel.render_entries([entry("кадр", image_paths=[str(picture)])])

    assert decoded == [str(picture)]
    assert "<img" in panel.view.toHtml()
    panel.deleteLater()


def test_a_rewritten_preview_is_decoded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application()
    picture = sample_image(tmp_path / "кадр.png")
    decoded: list[str] = []
    original = log_panel_module.load_thumbnail

    def counted(path: Path) -> QImage | None:
        decoded.append(str(path))
        return original(path)

    monkeypatch.setattr(log_panel_module, "load_thumbnail", counted)
    panel = LogPanel()
    panel.append_entry(entry("кадр", image_paths=[str(picture)]))
    panel.flush_log()

    replacement = QImage(16, 16, QImage.Format.Format_RGB32)
    replacement.fill(0xFF10B981)
    assert replacement.save(str(picture))
    os.utime(picture, (0, 0))

    panel.append_entry(entry("кадр", image_paths=[str(picture)]))
    panel.flush_log()

    assert len(decoded) == 2
    panel.deleteLater()


def test_the_document_stays_bounded_without_a_rebuild() -> None:
    application()
    panel = LogPanel()
    for index in range(20_000):
        panel.append_entry(entry(f"крок {index}"))
    panel.flush_log()
    assert panel.view.document().blockCount() <= 10_000
    assert "крок 19999" in panel.view.toPlainText()
    panel.deleteLater()


def scrolling_panel(height: int = 200) -> LogPanel:
    """Панель із реальною висотою — без показу смуги прокрутки не існує."""
    panel = LogPanel()
    panel.resize(400, height)
    panel.show()
    return panel


def fill(panel: LogPanel, count: int = 80) -> None:
    for index in range(count):
        panel.append_entry(entry(f"крок {index}"))
    panel.flush_log()


def test_the_newest_entry_is_fully_visible() -> None:
    application()
    panel = scrolling_panel()
    fill(panel)
    bar = panel.view.verticalScrollBar()
    assert bar.value() == bar.maximum()
    panel.deleteLater()


def test_the_tail_stays_visible_when_the_activity_line_appears() -> None:
    application()
    panel = scrolling_panel()
    fill(panel)
    panel.set_activity("Виконує: python gen.py", "#7C3AED")
    panel.flush_activity()
    application().processEvents()
    bar = panel.view.verticalScrollBar()
    assert bar.value() == bar.maximum()
    panel.deleteLater()


def test_the_tail_stays_visible_when_the_panel_is_resized() -> None:
    application()
    panel = scrolling_panel()
    fill(panel)
    panel.resize(400, 120)
    application().processEvents()
    bar = panel.view.verticalScrollBar()
    assert bar.value() == bar.maximum()
    panel.deleteLater()


def test_reading_history_is_not_interrupted_by_new_entries() -> None:
    application()
    panel = scrolling_panel()
    fill(panel)
    bar = panel.view.verticalScrollBar()
    bar.setValue(0)
    panel.append_entry(entry("свіжий крок"))
    panel.flush_log()
    assert bar.value() == 0
    panel.deleteLater()


def test_returning_to_the_bottom_re_enables_following() -> None:
    application()
    panel = scrolling_panel()
    fill(panel)
    bar = panel.view.verticalScrollBar()
    bar.setValue(0)
    bar.setValue(bar.maximum())
    panel.append_entry(entry("свіжий крок"))
    panel.flush_log()
    assert bar.value() == bar.maximum()
    panel.deleteLater()


def test_clearing_drops_the_queued_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    application()
    panel = LogPanel()
    writes: list[str] = []
    monkeypatch.setattr(panel.view, "append_html", writes.append)
    panel.append_entry(entry("крок"))
    panel.clear()
    panel.flush_log()
    assert writes == []
    panel.deleteLater()
