from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowai import codex_adapter
from flowai.apply_edits import apply_edits, pin_skills
from flowai.calibration import (
    CALIBRATION_FILE,
    CALIBRATION_SCHEMA,
    EDIT_TARGETS,
    CalibrationReport,
    ProposedEdit,
    RejectionPoint,
    load_report,
    parse_report,
    save_report,
)
from flowai.engine import RunCheckpoint, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.run_history import (
    CHECKPOINT_FILE,
    clear_checkpoint,
    find_pending_run,
    load_checkpoint,
    save_checkpoint,
)

CONTEXT = {
    "node_id": "n1",
    "node_title": "Calibration Stop",
    "task_id": "t1",
    "task_title": "Зробити карту",
    "workflow_name": "Карти",
    "attempt": 1,
    "threshold": 1,
    "reason": "Пропорції поламані",
    "must_fix": ["Вирівняти сітку"],
    "skills_used": ["birds-map"],
}


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", None)


def test_schema_names_every_edit_target() -> None:
    assert EDIT_TARGETS == {
        "skill_file",
        "task_prompt",
        "node_prompt",
        "node_instructions",
    }
    assert "edits" in CALIBRATION_SCHEMA
    assert "points" in CALIBRATION_SCHEMA


def test_parse_report_reads_a_full_answer() -> None:
    payload = {
        "summary": "Скіл не описує масштаб",
        "root_cause": "У SKILL.md немає правила про сітку",
        "skills_used": ["birds-map"],
        "skills_missing": ["image-cutout"],
        "points": [
            {
                "title": "Сітка з'їхала",
                "detail": "Об'єкти не в вузлах",
                "images": [{"path": "C:/out/map.png", "note": "Ліва частина"}],
            }
        ],
        "edits": [
            {
                "target": "skill_file",
                "path": "C:/skills/birds-map/SKILL.md",
                "skill": "birds-map",
                "label": "Додати правило сітки",
                "rationale": "Інакше агент кладе об'єкти між вузлами",
                "before": "## Композиція\nСтав об'єкти красиво.",
                "after": "## Композиція\nСтав об'єкти рівно у вузли сітки.",
            }
        ],
    }
    report = parse_report(payload, **CONTEXT)
    assert report.summary == "Скіл не описує масштаб"
    assert report.skills_missing == ["image-cutout"]
    assert report.points[0].title == "Сітка з'їхала"
    assert report.points[0].images[0].note == "Ліва частина"
    assert report.points[0].user_note == ""
    assert report.edits[0].target == "skill_file"
    assert report.edits[0].accepted is True
    assert report.edits[0].display_path == "birds-map / SKILL.md"


def test_parse_report_falls_back_to_must_fix_when_points_are_missing() -> None:
    report = parse_report({"summary": "Погано"}, **CONTEXT)
    assert [point.title for point in report.points] == ["Вирівняти сітку"]
    assert report.edits == []


def test_parse_report_drops_edits_with_an_unknown_target() -> None:
    payload = {
        "edits": [
            {"target": "scripts", "before": "a", "after": "b", "label": "ні"},
            {
                "target": "task_prompt",
                "before": "a",
                "after": "b",
                "label": "так",
                "task_id": "t1",
            },
        ]
    }
    report = parse_report(payload, **CONTEXT)
    assert [edit.label for edit in report.edits] == ["так"]


def test_parse_report_drops_edits_that_change_nothing() -> None:
    payload = {
        "edits": [
            {
                "target": "task_prompt",
                "before": "однаково",
                "after": "однаково",
                "label": "порожня правка",
                "task_id": "t1",
            }
        ]
    }
    assert parse_report(payload, **CONTEXT).edits == []


def test_parse_report_survives_a_non_dict_answer() -> None:
    report = parse_report("агент відповів текстом", **CONTEXT)
    assert report.analysis_error
    assert [point.title for point in report.points] == ["Вирівняти сітку"]


def test_report_round_trips_through_disk(tmp_path: Path) -> None:
    report = parse_report(
        {
            "summary": "Стисло",
            "points": [{"title": "Пункт", "detail": "Опис", "images": []}],
            "edits": [
                {
                    "target": "node_instructions",
                    "node_id": "exec-1",
                    "label": "Додати вимогу",
                    "before": "було",
                    "after": "стало",
                }
            ],
        },
        **CONTEXT,
    )
    report.points[0].user_note = "Моє бачення"
    report.edits[0].accepted = False
    path = save_report(report, tmp_path)
    restored = load_report(tmp_path)
    assert path.name == CALIBRATION_FILE
    assert restored is not None
    assert restored.points[0].user_note == "Моє бачення"
    assert restored.edits[0].accepted is False
    assert restored.task_title == "Зробити карту"
    assert json.loads(path.read_text(encoding="utf-8"))["attempt"] == 1


def test_load_report_returns_none_when_nothing_is_saved(tmp_path: Path) -> None:
    assert load_report(tmp_path) is None


def test_load_report_returns_none_for_a_broken_file(tmp_path: Path) -> None:
    (tmp_path / CALIBRATION_FILE).write_text("не json", encoding="utf-8")
    assert load_report(tmp_path) is None


def test_user_notes_text_collects_only_filled_notes() -> None:
    report = CalibrationReport(
        node_id="n1",
        node_title="Stop",
        task_id="t1",
        task_title="Задача",
        workflow_name="Flow",
        attempt=1,
        threshold=1,
        points=[
            RejectionPoint(title="Раз", user_note="Виправити так"),
            RejectionPoint(title="Два", user_note="   "),
        ],
    )
    assert report.user_notes_text() == "- Раз: Виправити так"


def test_accepted_edits_filters_by_flag() -> None:
    report = CalibrationReport(
        node_id="n1",
        node_title="Stop",
        task_id="t1",
        task_title="Задача",
        workflow_name="Flow",
        attempt=1,
        threshold=1,
        edits=[
            ProposedEdit(target="task_prompt", label="так", before="a", after="b"),
            ProposedEdit(
                target="task_prompt",
                label="ні",
                before="a",
                after="b",
                accepted=False,
            ),
        ],
    )
    assert [edit.label for edit in report.accepted_edits()] == ["так"]


def build_flow(workspace: Path) -> tuple[Workflow, dict[str, FlowNode]]:
    workflow = Workflow(name="Калібрація", workspace=str(workspace))
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "task-1", "prompt": "Зробити карту", "attachments": []}
    ]
    executor = FlowNode.create("executor")
    executor.config["model"] = "executor-model"
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    stop = FlowNode.create("calibrator")
    stop.config["model"] = "calibrator-model"
    workflow.nodes.extend([manager, executor, reviewer, result, stop])
    workflow.edges.extend(
        [
            FlowEdge.create(manager.id, executor.id, "next"),
            FlowEdge.create(executor.id, reviewer.id),
            FlowEdge.create(reviewer.id, result.id),
            FlowEdge.create(result.id, manager.id, "true"),
            FlowEdge.create(result.id, executor.id, "false"),
            FlowEdge.create(result.id, stop.id, "false"),
        ]
    )
    for edge in workflow.edges:
        if edge.source == reviewer.id:
            edge.source_path = "data"
            edge.target_variable = "review"
    return workflow, {
        "manager": manager,
        "executor": executor,
        "reviewer": reviewer,
        "result": result,
        "stop": stop,
    }


def rejecting_responder(payload: object) -> str:
    call = dict(payload)  # type: ignore[arg-type]
    model = call.get("model")
    if model == "reviewer-model":
        return json.dumps(
            {
                "verdict": False,
                "score": 3,
                "reason": "Пропорції поламані",
                "must_fix": ["Вирівняти сітку"],
            },
            ensure_ascii=False,
        )
    if model == "calibrator-model":
        return json.dumps(
            {
                "summary": "Скіл не описує сітку",
                "root_cause": "У SKILL.md немає правила",
                "skills_missing": ["birds-map"],
                "points": [
                    {"title": "Сітка з'їхала", "detail": "", "images": []}
                ],
                "edits": [
                    {
                        "target": "task_prompt",
                        "task_id": "task-1",
                        "label": "Уточнити сітку",
                        "before": "Зробити карту",
                        "after": "Зробити карту з об'єктами у вузлах сітки",
                    }
                ],
            },
            ensure_ascii=False,
        )
    return "готово"


def test_calibrator_pauses_the_flow_on_the_first_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, nodes = build_flow(tmp_path)
    events: list[dict] = []
    runner = WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    )
    runner.run()
    request = next(
        event["request"]
        for event in events
        if event["type"] == "intervention_required"
    )
    assert request["type"] == "calibration"
    assert request["node_id"] == nodes["stop"].id
    report = request["report"]
    assert report["task_id"] == "task-1"
    assert report["points"][0]["title"] == "Сітка з'їхала"
    assert report["edits"][0]["target"] == "task_prompt"
    assert (tmp_path / "run" / "calibration.json").is_file()


def test_calibrator_resumes_the_reviewer_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, _nodes = build_flow(tmp_path)
    WorkflowRunner(workflow, run_directory=tmp_path / "run").run()
    reviewer_call = next(
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "reviewer-model"
    )
    calibrator_call = next(
        call
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "calibrator-model"
    )
    assert calibrator_call["resumed"] is True
    assert calibrator_call["thread_id"] == reviewer_call["thread_id"]


def test_threshold_two_lets_the_flow_retry_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, nodes = build_flow(tmp_path)
    nodes["stop"].config["false_threshold"] = 2
    events: list[dict] = []
    WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    ).run()
    executor_calls = [
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "executor-model"
    ]
    assert len(executor_calls) == 2
    assert any(event["type"] == "intervention_required" for event in events)


def test_calibrator_is_not_seeded_into_the_initial_queue(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    queue = WorkflowRunner(workflow, run_directory=tmp_path / "run")._initial_queue()
    assert nodes["manager"].id in queue
    assert nodes["stop"].id not in queue


def test_retry_task_response_resets_the_attempt_counter(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    runner = WorkflowRunner(workflow, run_directory=tmp_path / "run")
    key = f"{nodes['stop'].id}:task-1"
    runner.checkpoint.task_progress[nodes["manager"].id] = {
        "active_task_id": "task-1"
    }
    runner.checkpoint.calibration_attempts[key] = 1
    runner.intervention_responses[nodes["stop"].id] = {"action": "retry_task"}
    result = runner._execute_calibrator(
        nodes["stop"], {}, {}, tmp_path, None
    )
    assert result.data["action"] == "retry_task"
    assert runner.checkpoint.calibration_attempts.get(key, 0) == 0


def test_broken_agent_answer_still_produces_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(payload: object) -> str:
        call = dict(payload)  # type: ignore[arg-type]
        if call.get("model") == "reviewer-model":
            return json.dumps(
                {
                    "verdict": False,
                    "score": 1,
                    "reason": "Погано",
                    "must_fix": ["Переробити все"],
                },
                ensure_ascii=False,
            )
        if call.get("model") == "calibrator-model":
            return "я подумав і вирішив відповісти текстом"
        return "готово"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    workflow, _nodes = build_flow(tmp_path)
    events: list[dict] = []
    WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    ).run()
    request = next(
        event["request"]
        for event in events
        if event["type"] == "intervention_required"
    )
    assert request["report"]["analysis_error"]
    assert request["report"]["points"][0]["title"] == "Переробити все"


def test_checkpoint_round_trips(tmp_path: Path) -> None:
    checkpoint = RunCheckpoint(started=True, steps=4, queue=["a", "b"])
    checkpoint.calibration_attempts["node:task"] = 1
    request = {"type": "calibration", "node_id": "node"}
    path = save_checkpoint(
        tmp_path,
        checkpoint,
        project_path=tmp_path / "flow.flowai.json",
        request=request,
    )
    restored = load_checkpoint(tmp_path)
    assert path.name == CHECKPOINT_FILE
    assert restored is not None
    state, saved_request = restored
    assert state.steps == 4
    assert state.queue == ["a", "b"]
    assert state.calibration_attempts == {"node:task": 1}
    assert saved_request == request


def test_find_pending_run_picks_the_newest(tmp_path: Path) -> None:
    project = tmp_path / "flow.flowai.json"
    project.write_text("{}", encoding="utf-8")
    older = tmp_path / "runs" / "20260101-000000-000000"
    newer = tmp_path / "runs" / "20260201-000000-000000"
    for directory in (older, newer):
        directory.mkdir(parents=True)
        save_checkpoint(
            directory,
            RunCheckpoint(started=True),
            project_path=project,
            request={"type": "calibration"},
        )
    assert find_pending_run(project) == newer


def test_find_pending_run_ignores_cleared_checkpoints(tmp_path: Path) -> None:
    project = tmp_path / "flow.flowai.json"
    project.write_text("{}", encoding="utf-8")
    directory = tmp_path / "runs" / "20260101-000000-000000"
    directory.mkdir(parents=True)
    save_checkpoint(
        directory,
        RunCheckpoint(started=True),
        project_path=project,
        request={"type": "calibration"},
    )
    clear_checkpoint(directory)
    assert find_pending_run(project) is None


def test_find_pending_run_ignores_other_projects(tmp_path: Path) -> None:
    project = tmp_path / "flow.flowai.json"
    other = tmp_path / "other.flowai.json"
    project.write_text("{}", encoding="utf-8")
    directory = tmp_path / "runs" / "20260101-000000-000000"
    directory.mkdir(parents=True)
    save_checkpoint(
        directory,
        RunCheckpoint(started=True),
        project_path=other,
        request={"type": "calibration"},
    )
    assert find_pending_run(project) is None


def test_load_checkpoint_returns_none_for_a_broken_file(tmp_path: Path) -> None:
    (tmp_path / CHECKPOINT_FILE).write_text("не json", encoding="utf-8")
    assert load_checkpoint(tmp_path) is None


def test_apply_rewrites_a_task_prompt(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="task_prompt",
            task_id="task-1",
            label="Уточнити",
            before="Зробити карту",
            after="Зробити карту з сіткою",
        )
    ]
    applied = apply_edits(report, workflow, skills_root=tmp_path / "skills")
    assert [item.ok for item in applied] == [True]
    assert nodes["manager"].config["tasks"][0]["prompt"] == (
        "Зробити карту з сіткою"
    )


def test_apply_replaces_a_fragment_inside_node_instructions(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    nodes["executor"].config["instructions"] = "Роби добре.\nНе поспішай."
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="node_instructions",
            node_id=nodes["executor"].id,
            label="Уточнити",
            before="Роби добре.",
            after="Роби добре й перевіряй сітку.",
        )
    ]
    applied = apply_edits(report, workflow, skills_root=tmp_path / "skills")
    assert applied[0].ok is True
    assert nodes["executor"].config["instructions"] == (
        "Роби добре й перевіряй сітку.\nНе поспішай."
    )


def test_apply_rewrites_a_skill_file_and_backs_it_up(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    directory = skills_root / "birds-map"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: birds-map\ndescription: Карта\n---\n\n"
        "Став об'єкти красиво.\n",
        encoding="utf-8",
    )
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            skill="birds-map",
            path=str(directory / "SKILL.md"),
            label="Правило сітки",
            before="Став об'єкти красиво.",
            after="Став об'єкти у вузли сітки.",
        )
    ]
    applied = apply_edits(
        report,
        workflow,
        skills_root=skills_root,
        backups_root=tmp_path / "backups",
    )
    assert applied[0].ok is True
    assert "у вузли сітки" in (directory / "SKILL.md").read_text(encoding="utf-8")
    backups = list((tmp_path / "backups" / "birds-map").iterdir())
    assert len(backups) == 1
    assert "красиво" in (backups[0] / "SKILL.md").read_text(encoding="utf-8")


def test_apply_reports_a_fragment_that_no_longer_matches(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    directory = skills_root / "birds-map"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("зовсім інший текст", encoding="utf-8")
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            skill="birds-map",
            path=str(directory / "SKILL.md"),
            label="Правило",
            before="цього фрагмента там немає",
            after="нове",
        )
    ]
    applied = apply_edits(
        report,
        workflow,
        skills_root=skills_root,
        backups_root=tmp_path / "b",
    )
    assert applied[0].ok is False
    assert "не знайдено" in applied[0].message


def test_apply_refuses_a_path_outside_the_skills_root(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("таємниця", encoding="utf-8")
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            path=str(outside),
            label="Небезпечна правка",
            before="таємниця",
            after="зламано",
        )
    ]
    applied = apply_edits(
        report,
        workflow,
        skills_root=tmp_path / "skills",
        backups_root=tmp_path / "b",
    )
    assert applied[0].ok is False
    assert outside.read_text(encoding="utf-8") == "таємниця"


def test_apply_refuses_a_script_file(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    directory = skills_root / "birds-map" / "scripts"
    directory.mkdir(parents=True)
    script = directory / "run.py"
    script.write_text("print(1)", encoding="utf-8")
    (skills_root / "birds-map" / "SKILL.md").write_text(
        "---\nname: birds-map\ndescription: Карта\n---\n", encoding="utf-8"
    )
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            path=str(script),
            label="Правка коду",
            before="print(1)",
            after="print(2)",
        )
    ]
    applied = apply_edits(
        report,
        workflow,
        skills_root=skills_root,
        backups_root=tmp_path / "b",
    )
    assert applied[0].ok is False
    assert script.read_text(encoding="utf-8") == "print(1)"


def test_apply_skips_unchecked_edits(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="task_prompt",
            task_id="task-1",
            label="Не застосовувати",
            before="Зробити карту",
            after="Не має статися",
            accepted=False,
        )
    ]
    assert apply_edits(report, workflow, skills_root=tmp_path / "s") == []
    assert nodes["manager"].config["tasks"][0]["prompt"] == "Зробити карту"


def test_pin_skills_adds_them_to_the_executor(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "image-cutout").mkdir(parents=True)
    (skills_root / "image-cutout" / "SKILL.md").write_text(
        "---\nname: image-cutout\ndescription: Фон\n---\n", encoding="utf-8"
    )
    workflow, nodes = build_flow(tmp_path)
    pinned = pin_skills(
        workflow,
        nodes["executor"].id,
        ["image-cutout"],
        skills_root=skills_root,
    )
    assert pinned == ["image-cutout"]
    assert nodes["executor"].config["skills"][0]["name"] == "image-cutout"


def test_pin_skills_ignores_unknown_names(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    assert pin_skills(
        workflow,
        nodes["executor"].id,
        ["не-існує"],
        skills_root=tmp_path / "skills",
    ) == []
