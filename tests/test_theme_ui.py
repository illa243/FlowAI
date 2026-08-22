from __future__ import annotations

import os
from itertools import pairwise

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QWidget

from flowai.ui.controls import AnimatedButton
from flowai.ui.design import COLORS, DURATION, RADII, TYPE
from flowai.ui.icons import icon
from flowai.ui.motion import pulse
from flowai.ui.platform import apply_dark_titlebar
from flowai.ui.theme import build_style
from flowai.ui.typography import load_fonts


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_fonts_load_and_report_families() -> None:
    application()
    ui_family, mono_family = load_fonts()
    assert ui_family in {"Inter", "Segoe UI"}
    assert mono_family in {"JetBrains Mono", "Consolas"}


def test_apply_dark_titlebar_never_raises() -> None:
    application()
    widget = QWidget()
    widget.show()
    assert apply_dark_titlebar(widget) in {True, False}
    widget.close()


def test_design_tokens_cover_required_keys() -> None:
    for key in (
        "bg",
        "surface",
        "surface_raised",
        "border",
        "text",
        "accent",
        "focus",
    ):
        assert key in COLORS and COLORS[key].startswith("#")
    assert RADII["sm"] == 8 and RADII["md"] == 12 and RADII["lg"] == 16
    assert DURATION["fast"] < DURATION["base"] < DURATION["slow"]
    assert TYPE["body"][0] == 13


def test_build_style_uses_given_families_and_tokens() -> None:
    style = build_style("Inter", "JetBrains Mono")
    assert "Inter" in style
    assert "JetBrains Mono" in style
    assert COLORS["accent"] in style
    assert "border-radius: 12px" in style


def test_icon_renders_non_null() -> None:
    application()
    result = icon("play", "#FFFFFF", 18)
    assert not result.isNull()


def test_animated_button_animates_hover() -> None:
    application()
    button = AnimatedButton("Run", variant="primary", icon_name="play")
    assert button.hover_progress == 0.0
    button.enterEvent(QEnterEvent(QPoint(1, 1), QPoint(1, 1), QPoint(1, 1)))
    assert button._hover_animation.endValue() == 1.0
    assert button.minimumHeight() == 34
    button.deleteLater()


def test_animated_button_reserves_text_and_icon_width() -> None:
    application()
    button = AnimatedButton("Settings", variant="ghost", icon_name="settings")
    assert button.minimumSizeHint().width() >= 90
    assert button.iconSize().height() == 16
    button.deleteLater()


def test_pulse_is_continuous_and_bounded() -> None:
    values = [pulse(step / 10) for step in range(30)]
    assert all(0.0 <= value <= 1.0 for value in values)
    assert all(
        abs(second - first) < 0.35 for first, second in pairwise(values)
    )
