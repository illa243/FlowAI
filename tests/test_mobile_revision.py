"""Мобільна ревізія Game UI Flow: Photoshop з першого кроку і контракт варіанта."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_core import Pipeline, verdict_script

from flowai import codex_adapter
from flowai.engine import WorkflowRunner
from flowai.persistence import load_workflow
from flowai.ui_workflow import (
    PhotoshopAutomation,
    PhotoshopAutomationError,
    find_variants,
)

TEMPLATE = Path(__file__).parents[1] / "examples" / "game_ui_workflow.flowai.json"


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script())


# --- Photoshop перед першим концептом ---------------------------------------


def test_photoshop_preflight_pauses_before_the_agent_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без Photoshop нема чим малювати вже перший раунд, а не лише PSD Builder."""

    def broken_preflight(self: PhotoshopAutomation) -> Path:
        raise PhotoshopAutomationError("Adobe Photoshop 2022 або новіший не знайдено")

    monkeypatch.setattr(PhotoshopAutomation, "preflight", broken_preflight)
    pipeline = Pipeline(tmp_path)
    pipeline.executor.config["photoshop_preflight"] = True
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(
        pipeline.workflow,
        project_path=tmp_path / "flow.flowai.json",
        on_event=events.append,
    )
    runner.run()

    requests = [
        event.get("request") or {}
        for event in events
        if event.get("type") == "intervention_required"
    ]
    assert requests, "нода мусить зупинитися до ходу агента, а не після нього"
    assert requests[0]["type"] == "photoshop_attention"
    assert not any(event.get("type") == "run_finished" for event in events)
    executor_calls = [
        call
        for call in codex_adapter.FAKE_CALLS
        if call.get("model") == "executor-model"
    ]
    assert not executor_calls, "хід агента без Photoshop — змарнований хід"


def test_photoshop_preflight_alone_demands_no_psd_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight — це лише «Photoshop є»; вимога кінцевого .psd лишається Builder-ноді."""

    monkeypatch.setattr(PhotoshopAutomation, "preflight", lambda self: tmp_path)
    pipeline = Pipeline(tmp_path)
    pipeline.executor.config["photoshop_preflight"] = True
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(
        pipeline.workflow,
        project_path=tmp_path / "flow.flowai.json",
        on_event=events.append,
    )
    runner.run()

    assert any(event.get("type") == "run_finished" for event in events)


def test_the_template_checks_photoshop_before_the_first_concept() -> None:
    """§6: перевірка виконується перед Concept Executor, а не перед PSD Builder."""

    workflow = load_workflow(TEMPLATE)

    assert workflow.find("concept-executor").config.get("photoshop_preflight") is True
    assert workflow.find("synthesis-executor").config.get("photoshop_preflight") is True
    # Builder зберігає повний контракт: preflight + validate_psd кандидата.
    assert workflow.find("psd-builder").config.get("photoshop_required") is True
    assert not workflow.find("psd-builder").config.get("photoshop_preflight")


# --- контракт варіанта §4.2 -------------------------------------------------


def test_find_variants_keeps_the_mobile_contract_fields() -> None:
    """board/layout/psd — частина контракту варіанта, а не декор manifest-а."""

    payload = {
        "work": {
            "variants": [
                {
                    "variant_id": "v01",
                    "path": "tasks/screen/concepts/round-001/V01.png",
                    "sha256": "AA",
                    "board_path": "tasks/screen/concepts/round-001/V01_board.png",
                    "board_sha256": "BB",
                    "layout_manifest_path": (
                        "tasks/screen/concepts/round-001/V01_layout.json"
                    ),
                    "psd_path": "tasks/screen/concepts/round-001/V01.psd",
                }
            ]
        }
    }

    variants = find_variants(payload)

    assert len(variants) == 1
    variant = variants[0]
    assert variant["variant_id"] == "V01"
    assert variant["board_path"].endswith("V01_board.png")
    assert variant["board_sha256"] == "BB"
    assert variant["layout_manifest_path"].endswith("V01_layout.json")
    assert variant["psd_path"].endswith("V01.psd")


def test_the_template_variant_schema_names_the_new_files() -> None:
    """output_schema — це те, що бачить агент; без полів контракту їх не буде."""

    workflow = load_workflow(TEMPLATE)
    schema = workflow.find("concept-executor").config["output_schema"]
    variant = schema["variants"][0]

    for field in ("board_path", "board_sha256", "layout_manifest_path", "psd_path"):
        assert field in variant, field
