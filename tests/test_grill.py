from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowai import codex_adapter
from flowai.calibration import CalibrationReport, RejectionPoint
from flowai.codex_adapter import CodexAdapter
from flowai.grill import (
    MATERIALS_OPTIONS,
    MATERIALS_QUESTION,
    OWN_ANSWER,
    GrillSession,
)
from flowai.models import FlowNode, Workflow


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()


def _workflow(attachment: Path | None = None) -> tuple[Workflow, FlowNode]:
    workflow = Workflow(name="Тест")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {
            "id": "t1",
            "prompt": "Зроби аналіз ринку",
            "attachments": [str(attachment)] if attachment is not None else [],
        }
    ]
    workflow.nodes = [manager]
    return workflow, manager


def test_session_asks_then_finishes(tmp_path: Path) -> None:
    replies = [
        json.dumps(
            {
                "done": False,
                "question": "Який ринок аналізуємо?",
                "options": ["Мобільні ігри", "Веб"],
                "rationale": "Без ринку задача нечітка",
            }
        ),
        json.dumps({"done": True, "question": "", "options": [], "rationale": ""}),
        json.dumps(
            {
                "summary": "Ринок: мобільні ігри",
                "tasks": {"t1": "Зроби аналіз ринку мобільних ігор"},
                "entry": "",
            }
        ),
    ]
    codex_adapter.FAKE_RESPONDER = lambda call: replies.pop(0)

    attachment = tmp_path / "market.md"
    attachment.write_text("# Ринок", encoding="utf-8")
    workflow, _manager = _workflow(attachment)
    with CodexAdapter() as codex:
        session = GrillSession(
            workflow,
            codex,
            "gpt-5.6-terra",
            tmp_path,
            reasoning_effort="high",
        )
        question = session.next_question()
        assert question is not None
        assert question.options[-1] == "Своя відповідь"
        session.answer("Мобільні ігри")
        assert session.next_question() is None
        outcome = session.finish()
    assert outcome.rewritten_tasks["t1"].endswith("мобільних ігор")
    assert "мобільні ігри" in outcome.summary.lower()
    assert all(call["reasoning_effort"] == "high" for call in codex_adapter.FAKE_CALLS)
    assert all(
        call["attachments"] == [str(attachment)]
        for call in codex_adapter.FAKE_CALLS
    )
    assert str(attachment) in codex_adapter.FAKE_CALLS[0]["prompt"]


class FakeCodex:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, object]] = []

    def run_agent(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        from flowai.codex_adapter import AgentRun

        text = self.answers.pop(0) if self.answers else '{"done": true}'
        return AgentRun(text=text, thread_id="grill-thread")


def make_workflow() -> Workflow:
    workflow = Workflow(name="Карти")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "task-1", "prompt": "Зробити карту", "attachments": []}
    ]
    workflow.nodes.append(manager)
    return workflow


def make_calibration() -> CalibrationReport:
    return CalibrationReport(
        node_id="stop-1",
        node_title="Calibration Stop",
        task_id="task-1",
        task_title="Зробити карту",
        workflow_name="Карти",
        attempt=1,
        threshold=1,
        verdict_reason="Пропорції поламані",
        root_cause="У скілі немає правила сітки",
        points=[
            RejectionPoint(
                title="Сітка з'їхала",
                detail="Об'єкти не в вузлах",
                user_note="Хочу крок сітки 64 px",
            )
        ],
        skills_used=["birds-map"],
        skills_missing=["image-cutout"],
    )


def test_materials_question_offers_a_free_answer_last() -> None:
    assert MATERIALS_OPTIONS[-1] == OWN_ANSWER
    assert len(MATERIALS_OPTIONS) == 3
    assert "спочатку" in MATERIALS_QUESTION


def test_first_question_is_always_about_materials(tmp_path: Path) -> None:
    session = GrillSession(
        make_workflow(),
        FakeCodex(['{"done": true}']),
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
        generated_files=["C:/out/map.png"],
    )
    question = session.next_question()
    assert question is not None
    assert question.text == MATERIALS_QUESTION
    assert question.options == MATERIALS_OPTIONS


def test_materials_question_is_not_sent_to_the_agent(tmp_path: Path) -> None:
    codex = FakeCodex(['{"done": true}'])
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    session.next_question()
    assert codex.calls == []


def test_second_question_reaches_the_agent_with_the_answer(tmp_path: Path) -> None:
    codex = FakeCodex(
        ['{"done": false, "question": "Який крок сітки?", "options": ["64"]}']
    )
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    session.next_question()
    session.answer(MATERIALS_OPTIONS[0])
    question = session.next_question()
    assert question is not None
    assert question.text == "Який крок сітки?"
    assert MATERIALS_QUESTION in str(codex.calls[0]["prompt"])


def test_calibration_context_reaches_the_prompt(tmp_path: Path) -> None:
    codex = FakeCodex(['{"done": true}'])
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    session.next_question()
    session.answer(MATERIALS_OPTIONS[1])
    session.next_question()
    prompt = str(codex.calls[0]["prompt"])
    assert "Пропорції поламані" in prompt
    assert "Хочу крок сітки 64 px" in prompt
    assert "birds-map" in prompt
    assert "image-cutout" in prompt


def test_generated_files_are_listed_for_a_fresh_start(tmp_path: Path) -> None:
    codex = FakeCodex(['{"done": true}'])
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
        generated_files=["C:/out/map.png"],
    )
    session.next_question()
    session.answer(MATERIALS_OPTIONS[1])
    session.next_question()
    assert "C:/out/map.png" in str(codex.calls[0]["prompt"])


def test_finish_demands_the_failed_task_be_rewritten(tmp_path: Path) -> None:
    codex = FakeCodex(['{"summary": "готово", "tasks": {}}'])
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    session.finish()
    assert "task-1" in str(codex.calls[0]["prompt"])
    assert "обов'язково" in str(codex.calls[0]["prompt"])


def test_session_without_calibration_behaves_as_before(tmp_path: Path) -> None:
    codex = FakeCodex(
        ['{"done": false, "question": "Перше?", "options": ["так"]}']
    )
    session = GrillSession(make_workflow(), codex, "gpt-5.6-terra", tmp_path)
    question = session.next_question()
    assert question is not None
    assert question.text == "Перше?"


def test_review_feedback_context_is_grilled_without_the_materials_question(
    tmp_path: Path,
) -> None:
    codex = FakeCodex(
        [
            json.dumps(
                {
                    "done": False,
                    "question": "Лишити дах?",
                    "options": ["Так", "Ні"],
                    "rationale": "Є конфлікт",
                },
                ensure_ascii=False,
            )
        ]
    )
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        review_feedback={
            "node_title": "Показати результат",
            "verdict": False,
            "score": 72,
            "reason": "Контур перетинає дерево",
            "must_fix": ["Перемалювати контур"],
            "candidate_path": "C:/out/review.png",
            "user_note": "Дах не змінювати",
            "user_requirements": [
                "Теплиця та рослини всередині є однією групою"
            ],
        },
    )

    question = session.next_question()

    assert question is not None
    assert question.text == "Лишити дах?"
    prompt = str(codex.calls[0]["prompt"])
    assert "Контур перетинає дерево" in prompt
    assert "Дах не змінювати" in prompt
    assert "Теплиця та рослини всередині є однією групою" in prompt
    assert MATERIALS_QUESTION not in prompt


def test_review_feedback_finish_returns_an_instruction_not_prompt_rewrites(
    tmp_path: Path,
) -> None:
    codex = FakeCodex(
        [
            json.dumps(
                {
                    "summary": "Узгоджено геометрію",
                    "feedback": "Перемалюй контур, але не змінюй дах",
                },
                ensure_ascii=False,
            )
        ]
    )
    session = GrillSession(
        make_workflow(),
        codex,
        "gpt-5.6-terra",
        tmp_path,
        review_feedback={
            "verdict": False,
            "reason": "Контур неточний",
            "user_note": "Дах не змінювати",
        },
    )

    outcome = session.finish()

    assert outcome.feedback == "Перемалюй контур, але не змінюй дах"
    assert outcome.rewritten_tasks == {}
    assert "Не змінюй Flow" in str(codex.calls[0]["prompt"])
