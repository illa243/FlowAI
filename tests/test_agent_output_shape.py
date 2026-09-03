"""Контракт форми виходу агентної ноди.

Ребро `data.response` може віддавати доказ далі лише тоді, коли рушій
гарантує цей ключ. Текстова нода, у відповіді якої трапився JSON-фрагмент,
не має мовчки лишати наступну ноду без матеріалу.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowai import codex_adapter
from flowai.engine import WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.templating import resolve_path


@pytest.fixture(autouse=True)
def fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", None)


def edge(
    source: FlowNode,
    target: FlowNode,
    *,
    port: str = "out",
    source_path: str = "data",
    target_variable: str = "input",
) -> FlowEdge:
    item = FlowEdge.create(source.id, target.id, port)
    item.source_path = source_path
    item.target_variable = target_variable
    return item


def run_text_executor(tmp_path: Path, answer: str) -> dict:
    entry = FlowNode.create("entry")
    executor = FlowNode.create("executor")
    executor.config["output_format"] = "text"
    workflow = Workflow(
        name="Shape",
        workspace=str(tmp_path),
        nodes=[entry, executor],
        edges=[edge(entry, executor, source_path="text", target_variable="prompt")],
    )
    runner = WorkflowRunner(workflow)
    runner.run()
    return runner.outputs[executor.id].data


def test_text_node_keeps_response_when_the_answer_mentions_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = (
        'Крок E19 виконано. Записав step_result.json: '
        '{"element_id": "E19", "status": "awaiting_confirmation"}. Board готовий.'
    )
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: answer)

    data = run_text_executor(tmp_path, answer)

    assert data["response"] == answer


def test_text_node_keeps_response_when_the_answer_holds_a_fenced_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = 'Готово.\nПідсумок:\n```json\n{"ok": true}\n```\nФайли на диску.'
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: answer)

    data = run_text_executor(tmp_path, answer)

    assert data["response"] == answer


def test_a_templated_input_is_not_pasted_into_the_prompt_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: "ок")
    entry = FlowNode.create("entry")
    entry.config["text"] = "ЗВІТ-МАРКЕР-Ω виконано"
    reviewer = FlowNode.create("executor")
    reviewer.config.update(
        {
            "prompt_source": "template",
            "prompt": "# Що перевірити\n{{work}}",
            "output_format": "text",
        }
    )
    workflow = Workflow(
        name="Duplicate",
        workspace=str(tmp_path),
        nodes=[entry, reviewer],
        edges=[edge(entry, reviewer, source_path="text", target_variable="work")],
    )

    WorkflowRunner(workflow).run()

    prompt = next(
        call["prompt"]
        for call in codex_adapter.FAKE_CALLS
        if "Що перевірити" in call["prompt"]
    )
    assert prompt.count("ЗВІТ-МАРКЕР-Ω") == 1, (
        "значення вже підставлене через {{work}} — «# Вхідні дані» дублює його"
    )


def test_an_input_without_a_placeholder_still_reaches_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: "ок")
    entry = FlowNode.create("entry")
    entry.config["text"] = "ДОДАТКОВИЙ-МАРКЕР-Ω"
    reviewer = FlowNode.create("executor")
    reviewer.config.update(
        {
            "prompt_source": "template",
            "prompt": "# Завдання без підстановки",
            "output_format": "text",
        }
    )
    workflow = Workflow(
        name="Kept",
        workspace=str(tmp_path),
        nodes=[entry, reviewer],
        edges=[edge(entry, reviewer, source_path="text", target_variable="context")],
    )

    WorkflowRunner(workflow).run()

    prompt = next(
        call["prompt"]
        for call in codex_adapter.FAKE_CALLS
        if "Завдання без підстановки" in call["prompt"]
    )
    assert "ДОДАТКОВИЙ-МАРКЕР-Ω" in prompt


def test_the_next_node_receives_the_report_through_data_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = 'Готово: {"element_id": "E19"}'
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: answer)

    data = run_text_executor(tmp_path, answer)

    assert resolve_path({"data": data}, "data.response", default=None) == answer
