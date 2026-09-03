from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from flowai.calibration import (
    CalibrationReport,
    NodeOptimizationReview,
    OptimizationFinding,
    ProposedEdit,
    RejectionImage,
    RejectionPoint,
    load_report,
    save_report,
)
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.ui.calibration_dialog import CalibrationDialog, RejectionPointCard
from flowai.ui.diff_view import DiffView, build_rows
from flowai.ui.inspector import Inspector
from flowai.ui.main_window import MainWindow
from flowai.ui.toast import ToastAction
from flowai.workspaces import WorkspaceSession

MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_report(**overrides: object) -> CalibrationReport:
    report = CalibrationReport(
        node_id="stop-1",
        node_title="Calibration Stop",
        task_id="task-1",
        task_title="Зробити карту",
        workflow_name="Карти",
        attempt=1,
        threshold=1,
        verdict_reason="Пропорції поламані",
        summary="Скіл не описує сітку",
        root_cause="У SKILL.md немає правила",
        points=[
            RejectionPoint(
                title="Сітка з'їхала",
                detail="Об'єкти не в вузлах",
                images=[
                    RejectionImage(path="C:/out/map.png", note="Ліва частина")
                ],
            ),
            RejectionPoint(title="Тіні різні"),
        ],
        skills_used=["birds-map"],
        skills_missing=["image-cutout"],
        edits=[
            ProposedEdit(
                target="task_prompt",
                task_id="task-1",
                label="Уточнити сітку",
                before="Зробити карту",
                after="Зробити карту з об'єктами у вузлах",
            )
        ],
    )
    for key, value in overrides.items():
        setattr(report, key, value)
    return report


def make_dialog(report: CalibrationReport) -> CalibrationDialog:
    return CalibrationDialog(
        report,
        models=MODELS,
        default_model="gpt-5.6-terra",
        default_effort="medium",
    )


def test_build_rows_marks_an_unchanged_line() -> None:
    rows = build_rows("однаково", "однаково")
    assert [row.kind for row in rows] == ["equal"]
    assert rows[0].left == "однаково"
    assert rows[0].right == "однаково"


def test_build_rows_marks_a_replacement() -> None:
    rows = build_rows("було так", "стало інакше")
    assert [row.kind for row in rows] == ["replace"]


def test_build_rows_marks_a_deletion() -> None:
    rows = build_rows("перший\nдругий", "перший")
    assert [row.kind for row in rows] == ["equal", "delete"]
    assert rows[1].right == ""
    assert rows[1].right_number == 0


def test_build_rows_marks_an_insertion() -> None:
    rows = build_rows("перший", "перший\nдругий")
    assert [row.kind for row in rows] == ["equal", "insert"]
    assert rows[1].left == ""
    assert rows[1].left_number == 0


def test_build_rows_numbers_both_sides() -> None:
    rows = build_rows("a\nb\nc", "a\nB\nc")
    assert [(row.left_number, row.right_number) for row in rows] == [
        (1, 1),
        (2, 2),
        (3, 3),
    ]


def test_diff_view_starts_accepted() -> None:
    view = DiffView(
        ProposedEdit(
            target="task_prompt", label="Уточнити", before="старе", after="нове"
        )
    )
    assert view.accepted is True
    assert view.checkbox.isChecked() is True


def test_unchecking_the_view_updates_the_edit() -> None:
    edit = ProposedEdit(
        target="task_prompt", label="Уточнити", before="старе", after="нове"
    )
    view = DiffView(edit)
    view.checkbox.setChecked(False)
    assert view.accepted is False
    assert edit.accepted is False


def test_diff_view_fills_the_table_with_both_sides() -> None:
    view = DiffView(
        ProposedEdit(
            target="task_prompt",
            label="Уточнити",
            before="перший\nдругий",
            after="перший\nтретій",
        )
    )
    assert view.table.rowCount() == 2
    assert view.table.item(0, 1).text() == "перший"
    assert view.table.item(1, 1).text() == "другий"
    assert view.table.item(1, 3).text() == "третій"


def test_diff_view_shows_the_label_and_rationale() -> None:
    view = DiffView(
        ProposedEdit(
            target="skill_file",
            label="Додати правило сітки",
            rationale="Інакше об'єкти лягають між вузлами",
            before="a",
            after="b",
            path="C:/skills/birds-map/SKILL.md",
            skill="birds-map",
        )
    )
    assert "Додати правило сітки" in view.checkbox.text()
    assert "birds-map / SKILL.md" in view.path_label.text()
    assert "між вузлами" in view.rationale_label.text()


def test_dialog_has_two_tabs_in_the_right_order() -> None:
    dialog = make_dialog(make_report())
    assert dialog.tabs.count() == 2
    assert dialog.tabs.tabText(0) == "Чому відхилено"
    assert dialog.tabs.tabText(1).startswith("Пропоновані правки")
    assert dialog.tabs.currentIndex() == 0


def test_edits_tab_shows_the_edit_count() -> None:
    assert "1" in make_dialog(make_report()).tabs.tabText(1)


def test_every_point_gets_a_note_field() -> None:
    dialog = make_dialog(make_report())
    assert len(dialog.point_cards) == 2
    assert isinstance(dialog.point_cards[0], RejectionPointCard)
    dialog.point_cards[0].note.setPlainText("Виправити вручну")
    dialog.commit_notes()
    assert dialog.report.points[0].user_note == "Виправити вручну"


def test_image_note_is_shown_next_to_the_path() -> None:
    card = make_dialog(make_report()).point_cards[0]
    assert card.image_rows[0].text().find("Ліва частина") >= 0
    assert card.image_rows[0].toolTip() == "C:/out/map.png"


def test_missing_skills_are_offered_for_pinning() -> None:
    dialog = make_dialog(make_report())
    assert [box.text() for box in dialog.skill_boxes] == ["image-cutout"]
    dialog.skill_boxes[0].setChecked(True)
    assert dialog.pinned_skills == ["image-cutout"]


def test_apply_records_the_decision() -> None:
    dialog = make_dialog(make_report())
    dialog.apply_button.click()
    assert dialog.decision == "apply"


def test_regenerate_records_model_and_effort() -> None:
    dialog = make_dialog(make_report())
    dialog.model_combo.setCurrentText("gpt-5.6-sol")
    dialog.effort_combo.setCurrentText("high")
    dialog.regenerate_button.click()
    assert dialog.decision == "regenerate"
    assert dialog.model == "gpt-5.6-sol"
    assert dialog.effort == "high"


def test_retry_records_the_decision() -> None:
    dialog = make_dialog(make_report())
    dialog.retry_button.click()
    assert dialog.decision == "retry"


def test_apply_is_disabled_without_edits() -> None:
    assert make_dialog(make_report(edits=[])).apply_button.isEnabled() is False


def test_optimizer_sections_use_authoritative_executor_and_qa_names() -> None:
    report = make_report(
        node_reviews=[
            NodeOptimizationReview(
                node_id="exec",
                node_title="Застарілий Executor",
                score=72,
                summary="Є повторне читання.",
                findings=[
                    OptimizationFinding(
                        action="Повторне читання",
                        assessment="suboptimal",
                        evidence="Кроки 2 і 5",
                        better_alternative="Повторно використати результат",
                        expected_gain="Швидше на один виклик",
                    )
                ],
                recommendations=["Кешувати прочитане"],
            ),
            NodeOptimizationReview(
                node_id="qa",
                node_title="Застарілий QA",
                score=84,
                summary="Перевірка доказова.",
            ),
        ],
        edits=[
            ProposedEdit(
                target="node_instructions",
                node_id="exec",
                label="Скоротити Executor",
                before="довго",
                after="коротко",
            )
        ],
    )
    dialog = CalibrationDialog(
        report,
        models=MODELS,
        default_model="gpt-5.6-sol",
        default_effort="high",
        node_titles={"exec": "Executor", "qa": "QA"},
        node_order=["exec", "qa"],
    )

    assert dialog.node_section_titles["exec"].text() == "Executor"
    assert dialog.node_section_titles["qa"].text() == "QA"
    assert dialog.node_section_frames["exec"].isAncestorOf(dialog.diff_views[0])
    qa_texts = [
        label.text()
        for label in dialog.node_section_frames["qa"].findChildren(QLabel)
    ]
    assert "Оцінка ефективності: 84/100" in qa_texts
    assert "Правки не рекомендовані" in qa_texts


def test_stale_node_edit_is_visible_but_cannot_be_applied() -> None:
    stale = ProposedEdit(
        target="node_instructions",
        node_id="gone-node",
        label="Не застосовувати до видаленої ноди",
        before="a",
        after="b",
    )
    dialog = CalibrationDialog(
        make_report(node_reviews=[], edits=[stale]),
        models=MODELS,
        default_model="gpt-5.6-sol",
        default_effort="high",
        node_titles={"exec": "Executor", "qa": "QA"},
        node_order=["exec", "qa"],
    )

    stale_view = next(view for view in dialog.diff_views if view.edit is stale)
    assert stale.accepted is False
    assert stale_view.checkbox.isChecked() is False
    assert dialog.apply_button.isEnabled() is False


def test_inspector_auto_skip_round_trips_and_disables_threshold() -> None:
    workflow = Workflow(name="Optimizer")
    result = FlowNode.create("result")
    optimizer = FlowNode.create("calibrator")
    workflow.nodes.extend([result, optimizer])
    workflow.edges.append(FlowEdge.create(result.id, optimizer.id, "false"))
    inspector = Inspector()
    inspector.set_workflow(workflow)
    inspector.set_object(optimizer)

    assert inspector.auto_skip.isChecked() is False
    assert inspector.false_threshold.isEnabled() is True
    inspector.auto_skip.setChecked(True)
    assert optimizer.config["auto_skip"] is True
    assert inspector.false_threshold.isEnabled() is False
    assert inspector.threshold_hint.isVisible() is False
    inspector.auto_skip.setChecked(False)
    assert optimizer.config["auto_skip"] is False
    assert inspector.false_threshold.isEnabled() is True


def test_analysis_error_is_shown_as_a_banner() -> None:
    dialog = make_dialog(make_report(analysis_error="Агент упав"))
    assert dialog.error_banner.isVisible() is True
    assert "Агент упав" in dialog.error_banner.text()


def test_no_banner_when_the_analysis_succeeded() -> None:
    assert make_dialog(make_report()).error_banner.isVisible() is False


def build_session_workflow() -> tuple[Workflow, dict[str, FlowNode]]:
    workflow = Workflow(name="Карти")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "task-1", "prompt": "Зробити карту", "attachments": []}
    ]
    executor = FlowNode.create("executor")
    reviewer = FlowNode.create("task_reviewer")
    result = FlowNode.create("result")
    stop = FlowNode.create("calibrator")
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
    return workflow, {
        "manager": manager,
        "executor": executor,
        "reviewer": reviewer,
        "stop": stop,
    }


def setup_window(
    tmp_path: Path,
) -> tuple[MainWindow, WorkspaceSession, dict[str, FlowNode]]:
    window = MainWindow(check_account_on_start=False, restore_workspaces=False)
    workflow, nodes = build_session_workflow()
    session = WorkspaceSession(
        display_name=workflow.name, workflow=workflow, load_state="loaded"
    )
    session.run_directory = tmp_path
    window.workspace_sessions.append(session)
    window.current_workspace_id = session.id
    window.scene.set_workflow(workflow)
    return window, session, nodes


def calibration_request(report: CalibrationReport, node_id: str, tmp_path: Path) -> dict:
    return {
        "type": "calibration",
        "node_id": node_id,
        "report": report.to_dict(),
        "report_path": str(tmp_path / "calibration.json"),
    }


def test_apply_writes_the_task_prompt_and_queues_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, nodes = setup_window(tmp_path)
    report = make_report(node_id=nodes["stop"].id)
    save_report(report, tmp_path)
    monkeypatch.setattr(
        CalibrationDialog, "exec", lambda self: self._decide("apply") or 1
    )
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    window._show_calibration(
        session, calibration_request(report, nodes["stop"].id, tmp_path)
    )
    assert nodes["manager"].config["tasks"][0]["prompt"] == (
        "Зробити карту з об'єктами у вузлах"
    )
    assert session.intervention_responses[nodes["stop"].id] == {
        "action": "retry_task"
    }
    session.dirty = False
    window.close()


def test_apply_routes_optimizer_diffs_to_executor_and_qa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, nodes = setup_window(tmp_path)
    nodes["executor"].title = "Executor"
    nodes["reviewer"].title = "QA"
    nodes["executor"].config["instructions"] = "executor old"
    nodes["reviewer"].config["instructions"] = "qa old"
    nodes["reviewer"].config["prompt"] = "qa prompt old"
    nodes["stop"].config["reviewed_nodes"] = [
        nodes["executor"].id,
        nodes["reviewer"].id,
    ]
    report = make_report(
        node_id=nodes["stop"].id,
        edits=[
            ProposedEdit(
                target="node_instructions",
                node_id=nodes["executor"].id,
                label="Executor instructions",
                before="executor old",
                after="executor new",
            ),
            ProposedEdit(
                target="node_instructions",
                node_id=nodes["reviewer"].id,
                label="QA instructions",
                before="qa old",
                after="qa new",
            ),
            ProposedEdit(
                target="node_prompt",
                node_id=nodes["reviewer"].id,
                label="QA prompt",
                before="qa prompt old",
                after="qa prompt new",
            ),
            ProposedEdit(
                target="node_instructions",
                node_id=nodes["manager"].id,
                label="Стороння нода",
                before="",
                after="не застосовувати",
            ),
        ],
    )
    monkeypatch.setattr(
        CalibrationDialog, "exec", lambda self: self._decide("apply") or 1
    )
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)

    window._show_calibration(
        session, calibration_request(report, nodes["stop"].id, tmp_path)
    )

    assert nodes["executor"].config["instructions"] == "executor new"
    assert nodes["reviewer"].config["instructions"] == "qa new"
    assert nodes["reviewer"].config["prompt"] == "qa prompt new"
    assert "instructions" not in nodes["manager"].config
    session.dirty = False
    window.close()


def test_retry_continues_without_touching_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, nodes = setup_window(tmp_path)
    report = make_report(node_id=nodes["stop"].id)
    monkeypatch.setattr(
        CalibrationDialog, "exec", lambda self: self._decide("retry") or 1
    )
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    window._show_calibration(
        session, calibration_request(report, nodes["stop"].id, tmp_path)
    )
    assert nodes["manager"].config["tasks"][0]["prompt"] == "Зробити карту"
    assert session.intervention_responses[nodes["stop"].id] == {
        "action": "continue"
    }
    session.dirty = False
    window.close()


def test_user_notes_are_saved_back_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, nodes = setup_window(tmp_path)
    report = make_report(node_id=nodes["stop"].id)

    def choose(dialog: CalibrationDialog) -> int:
        dialog.point_cards[0].note.setPlainText("Моє бачення")
        dialog._decide("retry")
        return 1

    monkeypatch.setattr(CalibrationDialog, "exec", choose)
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    window._show_calibration(
        session, calibration_request(report, nodes["stop"].id, tmp_path)
    )
    saved = load_report(tmp_path)
    assert saved is not None
    assert saved.points[0].user_note == "Моє бачення"
    session.dirty = False
    window.close()


def test_pinned_skill_lands_on_the_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "image-cutout").mkdir(parents=True)
    (skills_root / "image-cutout" / "SKILL.md").write_text(
        "---\nname: image-cutout\ndescription: Фон\n---\n", encoding="utf-8"
    )
    window, session, nodes = setup_window(tmp_path)
    report = make_report(node_id=nodes["stop"].id)

    def choose(dialog: CalibrationDialog) -> int:
        dialog.skill_boxes[0].setChecked(True)
        dialog._decide("apply")
        return 1

    monkeypatch.setattr(CalibrationDialog, "exec", choose)
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    monkeypatch.setattr("flowai.ui.main_window.SKILLS_ROOT", skills_root)
    window._show_calibration(
        session, calibration_request(report, nodes["stop"].id, tmp_path)
    )
    assert nodes["executor"].config["skills"][0]["name"] == "image-cutout"
    session.dirty = False
    window.close()


def test_stop_cancels_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, nodes = setup_window(tmp_path)
    report = make_report(node_id=nodes["stop"].id)
    monkeypatch.setattr(CalibrationDialog, "exec", lambda self: 0)
    window._show_calibration(
        session, calibration_request(report, nodes["stop"].id, tmp_path)
    )
    assert session.run_state == "cancelled"
    assert session.pending_intervention is None
    session.dirty = False
    window.close()


def test_notification_prefers_the_toast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, _nodes = setup_window(tmp_path)
    sent: list[tuple[str, str, list[ToastAction]]] = []
    monkeypatch.setattr(
        window.toaster,
        "show",
        lambda title, body, *, tag, actions: sent.append((title, body, actions))
        or True,
    )
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    window._notify_user(
        session,
        "FlowAI",
        "Задачу відхилено",
        actions=[ToastAction("edits", "Показати правки")],
    )
    assert sent[0][0] == "FlowAI"
    assert sent[0][2][0].id == "edits"
    session.dirty = False
    window.close()


def test_notification_falls_back_to_the_tray(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, _nodes = setup_window(tmp_path)
    balloons: list[str] = []
    monkeypatch.setattr(window.toaster, "show", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        window.tray_icon,
        "showMessage",
        lambda title, message, icon, timeout: balloons.append(message),
    )
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    monkeypatch.setattr(
        "flowai.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
        staticmethod(lambda: True),
    )
    window._notify_user(session, "FlowAI", "Задачу відхилено")
    assert balloons == ["Задачу відхилено"]
    session.dirty = False
    window.close()


def test_toast_action_opens_the_edits_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, _nodes = setup_window(tmp_path)
    monkeypatch.setattr(MainWindow, "select_workspace", lambda self, _id: None)
    window._toast_activated(session.id, "edits")
    assert window.calibration_open_tab == 1
    window._toast_activated(session.id, "open")
    assert window.calibration_open_tab == 0
    session.dirty = False
    window.close()


def test_calibration_notification_names_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, session, nodes = setup_window(tmp_path)
    report = make_report(node_id=nodes["stop"].id)
    session.run_state = "needs_attention"
    session.pending_intervention = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "question": "Рев'ювер відхилив «Зробити карту»",
    }
    sent: list[str] = []
    monkeypatch.setattr(
        window, "_notify_user", lambda *args, **kwargs: sent.append(args[2])
    )
    monkeypatch.setattr(window, "_update_workspace_actions", lambda: None)
    window._run_thread_finished(session.id)
    assert any("Зробити карту" in message for message in sent)
    session.pending_intervention = None
    session.run_state = "idle"
    session.dirty = False
    window.close()
