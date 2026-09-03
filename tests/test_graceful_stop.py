"""STOP, який дає поточній ноді дороблювати й лишає запуск відновлюваним."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_core import Pipeline, verdict_script

from flowai import codex_adapter
from flowai.engine import WorkflowRunner


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", verdict_script())


def _stop_after_first_node(tmp_path: Path) -> tuple[WorkflowRunner, list[dict[str, Any]]]:
    pipeline = Pipeline(tmp_path)
    events: list[dict[str, Any]] = []
    runner: WorkflowRunner | None = None

    def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "node_started" and runner is not None:
            runner.request_stop()

    runner = WorkflowRunner(
        pipeline.workflow,
        project_path=tmp_path / "flow.flowai.json",
        on_event=on_event,
    )
    runner.run()
    return runner, events


def test_the_running_node_is_allowed_to_finish(tmp_path: Path) -> None:
    _, events = _stop_after_first_node(tmp_path)

    finished = [event for event in events if event.get("type") == "node_finished"]
    assert finished, "нода, яка вже працювала, мусить дорахувати до кінця"
    assert not any(event.get("type") == "node_cancelled" for event in events)


def test_a_soft_stop_is_not_a_cancel(tmp_path: Path) -> None:
    _, events = _stop_after_first_node(tmp_path)

    types = {event.get("type") for event in events}
    assert "run_stopped" in types
    assert "run_cancelled" not in types
    assert "run_finished" not in types


def test_the_rest_of_the_flow_stays_in_the_queue(tmp_path: Path) -> None:
    runner, _ = _stop_after_first_node(tmp_path)

    assert runner.checkpoint.queue, "черга має пережити зупинку, інакше нічого продовжувати"
    assert runner.checkpoint.started is True


def test_a_soft_stop_never_interrupts_the_agent_turn(tmp_path: Path) -> None:
    """cancel() рве активний хід Codex; request_stop() не має цього робити."""

    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(
        pipeline.workflow, project_path=tmp_path / "flow.flowai.json"
    )

    runner.request_stop()

    assert runner._stop.is_set() is False


def test_a_stopped_run_continues_from_its_checkpoint(tmp_path: Path) -> None:
    """Головне: те, що лишилось у чекпоінті, справді доводить Flow до кінця."""

    stopped, _ = _stop_after_first_node(tmp_path)
    pipeline = Pipeline(tmp_path)
    pipeline.workflow.nodes = stopped.workflow.nodes
    pipeline.workflow.edges = stopped.workflow.edges

    events: list[dict[str, Any]] = []
    resumed = WorkflowRunner(
        pipeline.workflow,
        project_path=tmp_path / "flow.flowai.json",
        checkpoint=stopped.checkpoint,
        on_event=events.append,
    )
    resumed.run()

    types = {event.get("type") for event in events}
    assert "run_finished" in types
    assert not resumed.checkpoint.queue


def test_a_soft_stop_releases_a_paused_flow(tmp_path: Path) -> None:
    """STOP має працювати і на паузі — інакше він мовчки нічого не робить.

    На паузі рушій стоїть у бар'єрі `_wait_until_resumed`, і без зняття цього
    бар'єра м'яка зупинка ніколи не дійшла б до межі нод.
    """

    pipeline = Pipeline(tmp_path)
    events: list[dict[str, Any]] = []
    runner: WorkflowRunner | None = None

    def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        finished = event.get("type") == "node_finished"
        if finished and runner is not None and not runner._graceful.is_set():
            runner.pause()
            runner.request_stop()

    runner = WorkflowRunner(
        pipeline.workflow,
        project_path=tmp_path / "flow.flowai.json",
        on_event=on_event,
    )
    runner.run()

    types = {event.get("type") for event in events}
    assert "run_stopped" in types, "зупинка на паузі мусить довести запуск до кінця"
    assert "run_cancelled" not in types
    assert runner.checkpoint.queue, "і лишити решту Flow для продовження"


def test_a_hard_cancel_still_works_after_a_soft_stop(tmp_path: Path) -> None:
    """Аварійний вихід треба зберегти: інакше нода-довгожитель незупинна."""

    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(
        pipeline.workflow, project_path=tmp_path / "flow.flowai.json"
    )

    runner.request_stop()
    runner.cancel()

    assert runner._stop.is_set() is True
