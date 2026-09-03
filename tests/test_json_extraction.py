"""Витягання JSON із відповіді агента.

Фолбек, який перебирає кожну можливу кінцеву позицію, коштує O(n²) і на
довгій відповіді з незакритою дужкою з'їдає хвилини на порожньому місці.
"""

from __future__ import annotations

import time

from flowai.templating import extract_json


def test_a_clean_object_is_parsed() -> None:
    assert extract_json('{"verdict": true, "score": 90}') == {
        "verdict": True,
        "score": 90,
    }


def test_a_fenced_block_is_unwrapped() -> None:
    assert extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_an_object_after_prose_is_found() -> None:
    assert extract_json('Ось результат:\n{"ok": true}') == {"ok": True}


def test_prose_after_the_object_is_ignored() -> None:
    assert extract_json('{"ok": true}\nБільше нічого не змінював.') == {"ok": True}


def test_a_brace_inside_a_string_does_not_end_the_object() -> None:
    assert extract_json('{"note": "закрив } дужку"}') == {"note": "закрив } дужку"}


def test_an_escaped_quote_inside_a_string_is_survived() -> None:
    assert extract_json(r'{"note": "лапка \" всередині"}') == {
        "note": 'лапка " всередині'
    }


def test_a_list_is_parsed() -> None:
    assert extract_json('[{"id": "E19"}]') == [{"id": "E19"}]


def test_plain_prose_yields_nothing() -> None:
    assert extract_json("Виконано без JSON у відповіді.") is None


def test_an_unterminated_object_yields_nothing() -> None:
    assert extract_json('Звіт: {"report": "почав і не закрив') is None


def test_a_long_unterminated_answer_does_not_take_minutes() -> None:
    text = 'Опис роботи.\n{"report": "' + "a" * 200_000 + "  # не закрито"

    started = time.perf_counter()
    result = extract_json(text)
    elapsed = time.perf_counter() - started

    assert result is None
    assert elapsed < 1.0, f"розбір зайняв {elapsed:.1f} с — фолбек досі квадратичний"
