"""Чого Game UI Flow не робив: старт не з того кінця і надто широкі гальма."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowai.engine import InterventionRequired, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.persistence import load_workflow
from flowai.ui_workflow import (
    PhotoshopAutomation,
    PhotoshopAutomationError,
    blocking_defect_ids,
    has_non_overridable_issues,
)

TEMPLATE = Path(__file__).parents[1] / "examples" / "game_ui_workflow.flowai.json"


def _runner(tmp_path: Path, *nodes: FlowNode, edges: list[FlowEdge] | None = None):
    workflow = Workflow(
        name="UI test",
        workspace=str(tmp_path),
        nodes=list(nodes),
        edges=list(edges or []),
    )
    return WorkflowRunner(workflow, project_path=tmp_path / "ui.flowai.json")


# --- старт Flow -------------------------------------------------------------


def test_the_real_template_starts_only_at_its_entry() -> None:
    """Синтетичний Flow цього не ловив — перевіряємо справжній шаблон."""

    workflow = load_workflow(TEMPLATE)
    runner = WorkflowRunner(workflow, project_path=TEMPLATE)

    assert runner._initial_queue() == ["ui-entry"]


def test_a_mid_chain_node_fed_only_by_results_waits_for_its_turn(
    tmp_path: Path,
) -> None:
    entry = FlowNode.create("entry")
    executor = FlowNode.create("executor")
    result = FlowNode.create("result")
    later = FlowNode.create("executor")
    edges = [
        FlowEdge.create(entry.id, executor.id),
        FlowEdge.create(executor.id, result.id),
        FlowEdge.create(result.id, later.id, "true"),
        FlowEdge.create(result.id, executor.id, "false"),
    ]
    runner = _runner(tmp_path, entry, executor, result, later, edges=edges)

    assert runner._initial_queue() == [entry.id]


def test_a_loop_head_no_entry_reaches_is_still_seeded(tmp_path: Path) -> None:
    """Не перестаратися: петля без входу мусить лишитись стартовою."""

    entry = FlowNode.create("entry")
    executor = FlowNode.create("executor")
    result = FlowNode.create("result")
    edges = [
        FlowEdge.create(executor.id, result.id),
        FlowEdge.create(result.id, executor.id, "false"),
    ]
    runner = _runner(tmp_path, entry, executor, result, edges=edges)

    assert set(runner._initial_queue()) == {entry.id, executor.id}


# --- що QA вважає непереборним ---------------------------------------------


def test_a_visual_preference_without_severity_can_be_overridden() -> None:
    review = {
        "verdict": False,
        "issues": [
            {
                "defect_id": "ROUND-CORNERS",
                "category": "visual_preference",
                "description": "Кути завеликі",
            }
        ],
    }

    assert has_non_overridable_issues(review) is False
    assert blocking_defect_ids(review) == []


def test_an_explicit_blocking_severity_still_wins() -> None:
    review = {
        "verdict": False,
        "issues": [
            {
                "defect_id": "ROUND-CORNERS",
                "category": "visual_preference",
                "severity": "blocking",
                "description": "Кути завеликі",
            }
        ],
    }

    assert has_non_overridable_issues(review) is True


def test_asset_approval_offers_the_override_dialog_for_a_bare_preference(
    tmp_path: Path,
) -> None:
    """Предиката мало: користувач має справді побачити діалог із override."""

    node = FlowNode.create("result")
    node.config.update(
        {
            "wait_for_confirmation": True,
            "confirmation_mode": "asset_approval",
            "confirmation_ports": ["true"],
        }
    )
    runner = _runner(tmp_path, node)
    review = {
        "verdict": False,
        "issues": [
            {
                "defect_id": "ROUND-CORNERS",
                "category": "visual_preference",
                "description": "Кути завеликі",
            }
        ],
    }

    with pytest.raises(InterventionRequired) as raised:
        runner._execute_result(
            node, {"review": review}, {"inputs": {"review": review}}, tmp_path
        )

    assert raised.value.request["type"] == "result_confirmation"
    assert raised.value.request["allow_visual_override"] is True


def test_a_technical_blocker_without_severity_still_blocks() -> None:
    review = {
        "verdict": False,
        "issues": [
            {
                "defect_id": "BROKEN-LINK",
                "category": "technical_blocker",
                "description": "Смарт-обʼєкт загублено",
            }
        ],
    }

    assert has_non_overridable_issues(review) is True
    assert blocking_defect_ids(review) == ["BROKEN-LINK"]


# --- що таке регресія -------------------------------------------------------


def _review(score: float, *defect_ids: str) -> dict:
    return {
        "verdict": False,
        "score": score,
        "issues": [
            {
                "defect_id": defect_id,
                "category": "missing_requirement",
                "severity": "blocking",
                "must_fix": f"fix {defect_id}",
            }
            for defect_id in defect_ids
        ],
    }


def _guarded_result(tmp_path: Path) -> tuple[WorkflowRunner, FlowNode]:
    result = FlowNode.create("result")
    result.config.update(
        {"retry_guard_enabled": True, "retry_guard_threshold": 2, "false_limit": 10}
    )
    return _runner(tmp_path, result), result


def test_a_lower_score_alone_is_not_a_regression(tmp_path: Path) -> None:
    """Бали можуть просісти й через нові зауваження — це не регресія."""

    runner, node = _guarded_result(tmp_path)
    runner._execute_result(
        node, {"review": _review(80, "A")}, {"inputs": {}}, tmp_path
    )

    second = runner._execute_result(
        node, {"review": _review(79, "B")}, {"inputs": {}}, tmp_path
    )

    assert second.data["branch"] == "false"


def test_a_defect_that_returns_after_being_fixed_is_a_regression(
    tmp_path: Path,
) -> None:
    runner, node = _guarded_result(tmp_path)
    runner._execute_result(
        node, {"review": _review(60, "A")}, {"inputs": {}}, tmp_path
    )
    runner._execute_result(
        node, {"review": _review(70, "B")}, {"inputs": {}}, tmp_path
    )

    with pytest.raises(InterventionRequired) as raised:
        runner._execute_result(
            node, {"review": _review(90, "A")}, {"inputs": {}}, tmp_path
        )

    assert raised.value.request["type"] == "retry_attention"
    assert raised.value.request["regression"] is True
    assert raised.value.request["regressed_defect_ids"] == ["A"]


# --- звіт Photoshop ---------------------------------------------------------


def test_a_stale_photoshop_report_is_not_accepted_as_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Імʼя звіту залежить лише від шляху PSD, тож старий файл лежить на місці."""

    psd = tmp_path / "tasks" / "screen" / "psd" / "screen.psd"
    psd.parent.mkdir(parents=True)
    psd.write_bytes(b"first build")
    automation = PhotoshopAutomation(tmp_path)

    def writing_run_jsx(_jsx: str, *, name: str) -> Path:
        automation.runtime.mkdir(parents=True, exist_ok=True)
        token = name.removeprefix("validate-").removesuffix(".jsx")
        (automation.runtime / f"validation-{token}.json").write_text(
            json.dumps({"opened": True, "layer_comp_count": 3}), encoding="utf-8"
        )
        return automation.runtime / name

    monkeypatch.setattr(automation, "run_jsx", writing_run_jsx)
    assert automation.validate_psd(psd)["layer_comp_count"] == 3

    def silent_run_jsx(_jsx: str, *, name: str) -> Path:
        return automation.runtime / name

    monkeypatch.setattr(automation, "run_jsx", silent_run_jsx)
    psd.write_bytes(b"second build")

    with pytest.raises(PhotoshopAutomationError):
        automation.validate_psd(psd)


# --- мертвий конфіг ---------------------------------------------------------


def test_the_template_has_no_dead_attempt_limit() -> None:
    """Порт exhausted ніде не зʼєднаний, тож лічильник спроб не працює.

    Дефолт ноди чіпати нічого: він потрібен Flow, які цей порт заводять.
    Питання лише в тому, щоб шаблон не перевизначав його на видиме 99.
    """

    workflow = load_workflow(TEMPLATE)
    raw = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    assert not any(edge.source_port == "exhausted" for edge in workflow.edges)
    assert all(
        "task_attempt_limit" not in (node.get("config") or {})
        for node in raw["nodes"]
    )
