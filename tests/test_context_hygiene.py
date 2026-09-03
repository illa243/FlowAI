"""Гігієна контексту: жодне значення не має тихо роздути промпт.

Один випадок уже коштував запуску: підстановка без стелі перетворила вихід
ноди на промпт у 3.38 млн символів, і транспорт відмовив. Ці тести описують
межі, за якими це не може повторитися для будь-якої ноди й будь-якого шаблону.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from flowai import codex_adapter
from flowai.engine import PROMPT_VALUE_CHARACTER_LIMIT, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.templating import render_template, stringify


@pytest.fixture(autouse=True)
def fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", lambda _call: "ок")


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


# --- межа підстановки ---------------------------------------------------


def test_a_value_is_clipped_when_a_limit_is_given() -> None:
    rendered = stringify(["x" * 5_000], limit=500)

    assert len(rendered) < 800
    assert "скорочено" in rendered


def test_without_a_limit_data_passes_through_untouched() -> None:
    payload = {"report": "y" * 5_000}

    assert stringify(payload) == stringify(payload, limit=None)
    assert "y" * 5_000 in stringify(payload)


def test_an_edge_transform_keeps_the_exact_value() -> None:
    context = {"inputs": {}, "value": {"path": "z" * 5_000}}

    rendered = render_template("{{value.path}}", context)

    assert rendered == "z" * 5_000


def test_a_template_can_be_rendered_with_a_ceiling() -> None:
    context = {"value": "z" * 5_000}

    rendered = render_template("{{value}}", context, value_limit=200)

    assert len(rendered) < 500
    assert "скорочено" in rendered


# --- межі промпту -------------------------------------------------------


def big_input_workflow(tmp_path: Path, template: str) -> tuple[Workflow, FlowNode]:
    entry = FlowNode.create("entry")
    entry.config["json"] = {"blob": "Ω" * (PROMPT_VALUE_CHARACTER_LIMIT + 50_000)}
    agent = FlowNode.create("executor")
    agent.config.update(
        {"prompt_source": "template", "prompt": template, "output_format": "text"}
    )
    workflow = Workflow(
        name="Budget",
        workspace=str(tmp_path),
        nodes=[entry, agent],
        edges=[edge(entry, agent, source_path="data", target_variable="work")],
    )
    return workflow, agent


def agent_prompt(marker: str) -> str:
    return next(
        call["prompt"] for call in codex_adapter.FAKE_CALLS if marker in call["prompt"]
    )


def test_a_huge_substituted_value_cannot_flood_the_prompt(tmp_path: Path) -> None:
    workflow, _ = big_input_workflow(tmp_path, "# Робота\n{{work}}")

    WorkflowRunner(workflow).run()

    prompt = agent_prompt("# Робота")
    assert len(prompt) < PROMPT_VALUE_CHARACTER_LIMIT + 20_000


def test_a_huge_unreferenced_input_cannot_flood_the_prompt(tmp_path: Path) -> None:
    workflow, _ = big_input_workflow(tmp_path, "# Робота без підстановки")

    WorkflowRunner(workflow).run()

    prompt = agent_prompt("# Робота без підстановки")
    assert len(prompt) < PROMPT_VALUE_CHARACTER_LIMIT + 20_000


# --- телеметрія ---------------------------------------------------------


def collect(workflow: Workflow) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    WorkflowRunner(workflow, on_event=events.append).run()
    return events


def test_every_prompt_reports_how_much_context_it_costs(tmp_path: Path) -> None:
    entry = FlowNode.create("entry")
    entry.config["text"] = "коротке завдання"
    agent = FlowNode.create("executor")
    workflow = Workflow(
        name="Telemetry",
        workspace=str(tmp_path),
        nodes=[entry, agent],
        edges=[edge(entry, agent, source_path="text", target_variable="prompt")],
    )

    events = collect(workflow)

    prompts = [item for item in events if item.get("type") == "agent_prompt"]
    assert prompts, "нода мала повідомити про сформований промпт"
    measured = prompts[0]
    assert measured["prompt_characters"] > 0
    assert measured["input_characters"] >= measured["prompt_characters"]
    assert measured["input_character_limit"] > 0


def test_a_prompt_close_to_the_limit_warns_before_it_breaks(tmp_path: Path) -> None:
    workflow, agent = big_input_workflow(tmp_path, "# Робота\n{{work}}")
    agent.config["input_character_limit"] = PROMPT_VALUE_CHARACTER_LIMIT + 10_000

    events = collect(workflow)

    warnings = [
        item for item in events if item.get("type") == "context_budget_warning"
    ]
    assert warnings, "вхід майже вичерпав ліміт, а Flow про це не сказав"
    assert warnings[0]["input_characters"] > 0


def test_a_long_file_list_is_summarised_for_any_agent_node(tmp_path: Path) -> None:
    entry = FlowNode.create("entry")
    entry.config["json"] = {
        "_generated_files": [f"C:/out/file-{index}.png" for index in range(400)]
    }
    agent = FlowNode.create("executor")
    agent.config.update(
        {
            "prompt_source": "template",
            "prompt": "# Робота\n{{work}}",
            "output_format": "text",
        }
    )
    workflow = Workflow(
        name="Long list",
        workspace=str(tmp_path),
        nodes=[entry, agent],
        edges=[edge(entry, agent, source_path="data", target_variable="work")],
    )

    WorkflowRunner(workflow).run()

    prompt = agent_prompt("# Робота")
    assert "file-0.png" in prompt, "перші шляхи мають лишитися як зразок"
    assert "file-399.png" not in prompt, "увесь перелік у промпті не потрібен"
    assert '"count": 400' in prompt, "кількість файлів має лишитися видимою"


def test_a_downstream_node_still_gets_the_full_list_in_the_data(
    tmp_path: Path,
) -> None:
    entry = FlowNode.create("entry")
    entry.config["json"] = {
        "_generated_files": [f"C:/out/file-{index}.png" for index in range(400)]
    }
    agent = FlowNode.create("executor")
    workflow = Workflow(
        name="Full data",
        workspace=str(tmp_path),
        nodes=[entry, agent],
        edges=[edge(entry, agent, source_path="data", target_variable="prompt")],
    )

    runner = WorkflowRunner(workflow)
    runner.run()

    kept = runner.outputs[entry.id].data["_generated_files"]
    assert len(kept) == 400, "стискаємо лише вигляд у промпті, не самі дані"


# --- журнал мутацій -----------------------------------------------------


def test_the_mutation_ledger_stays_out_of_the_node_output(tmp_path: Path) -> None:
    entry = FlowNode.create("entry")
    entry.config["text"] = "зроби файл"
    agent = FlowNode.create("executor")
    workflow = Workflow(
        name="Ledger",
        workspace=str(tmp_path),
        nodes=[entry, agent],
        edges=[edge(entry, agent, source_path="text", target_variable="prompt")],
    )

    def responder(_call: dict[str, Any]) -> str:
        (tmp_path / "artifact.png").write_bytes(b"png")
        return "готово"

    codex_adapter.FAKE_RESPONDER = responder
    runner = WorkflowRunner(workflow)
    runner.run()

    data = runner.outputs[agent.id].data
    assert "artifact.png" in str(data["_generated_files"])
    assert "_file_ledger" not in data, "повний ledger ніхто не читає — він лише вага"


def test_a_repeated_pass_replaces_the_ledger_instead_of_piling_it_up(
    tmp_path: Path,
) -> None:
    workflow = Workflow(name="Ledger growth", workspace=str(tmp_path))
    runner = WorkflowRunner(workflow)
    first = [{"kind": "generated", "path": f"a-{index}"} for index in range(5)]
    second = [{"kind": "generated", "path": f"b-{index}"} for index in range(5)]

    runner._record_file_ledger("node-1", first)
    runner._record_file_ledger("node-1", second)

    kept = runner.checkpoint.file_ledgers["node-1"]
    assert len(kept) == 5, (
        f"ledger виріс до {len(kept)}: кожен прохід накопичується без межі"
    )
    assert kept[0]["path"] == "b-0", "у чекпоінті має лишатися останній прохід"
