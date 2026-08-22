from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.branding import APP_USER_MODEL_ID, start_menu_shortcut
from flowai.ui.toast import ToastAction, Toaster, build_toast_xml


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_xml_carries_title_body_and_tag() -> None:
    xml = build_toast_xml("FlowAI", "Задачу відхилено", [], tag="session-1")
    assert "<toast" in xml
    assert "FlowAI" in xml
    assert "Задачу відхилено" in xml
    assert 'launch="session-1|open"' in xml


def test_xml_renders_every_action() -> None:
    xml = build_toast_xml(
        "FlowAI",
        "Задачу відхилено",
        [ToastAction("edits", "Показати правки")],
        tag="session-1",
    )
    assert 'content="Показати правки"' in xml
    assert 'arguments="session-1|edits"' in xml


def test_xml_escapes_markup_in_the_body() -> None:
    xml = build_toast_xml("FlowAI", 'Пункт <b> та "лапки"', [], tag="s")
    assert "<b>" not in xml.split("<text>")[2]
    assert "&lt;b&gt;" in xml


def test_toaster_reports_availability_without_crashing() -> None:
    assert isinstance(Toaster(APP_USER_MODEL_ID).available(), bool)


def test_toaster_show_returns_false_when_winrt_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    monkeypatch.setattr(toaster, "_notifier", None)
    monkeypatch.setattr(toaster, "_available", False)
    assert toaster.show("FlowAI", "тест", tag="s", actions=[]) is False


def test_start_menu_shortcut_points_into_programs() -> None:
    target = start_menu_shortcut()
    assert target.name == "FlowAI.lnk"
    assert "Start Menu" in str(target) or "Меню" in str(target)


def test_activation_signal_parses_the_argument() -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    seen: list[tuple[str, str]] = []
    toaster.activated.connect(lambda tag, action: seen.append((tag, action)))
    toaster._handle_argument("session-7|edits")
    assert seen == [("session-7", "edits")]


def test_activation_without_an_action_defaults_to_open() -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    seen: list[tuple[str, str]] = []
    toaster.activated.connect(lambda tag, action: seen.append((tag, action)))
    toaster._handle_argument("session-7")
    assert seen == [("session-7", "open")]
