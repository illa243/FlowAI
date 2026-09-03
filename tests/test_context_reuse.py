"""Що саме FlowAI кладе в тред агента і скільки разів.

Повторно надіслане вкладення лишається в контексті назавжди: compaction
закріплює user-повідомлення й не може їх прибрати. Тому ці тести стежать
не за результатом ходу, а за складом входу.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_calibration import build_flow, rejecting_responder

from flowai import codex_adapter
from flowai.codex_adapter import input_fingerprints, normalize_items
from flowai.engine import RunCheckpoint, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", None)


def adapter() -> codex_adapter.CodexAdapter:
    import openai_codex

    instance = codex_adapter.CodexAdapter()
    instance._module = openai_codex
    return instance


def picture(directory: Path, name: str = "map.png", body: bytes = b"\x89PNG") -> Path:
    path = directory / name
    path.write_bytes(body)
    return path


def kinds(items: object) -> list[str]:
    return [type(item).__name__ for item in items]  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Адаптер: що потрапляє у вхід ходу
# --------------------------------------------------------------------------


def test_a_fresh_thread_receives_the_skill_and_the_picture(tmp_path: Path) -> None:
    image = picture(tmp_path)
    items = adapter()._build_input(
        "текст",
        [image],
        [{"name": "cutout", "path": str(tmp_path / "SKILL.md")}],
        {},
    )
    assert kinds(items) == ["SkillInput", "TextInput", "LocalImageInput"]


def test_a_resumed_thread_does_not_repeat_what_it_already_holds(
    tmp_path: Path,
) -> None:
    image = picture(tmp_path)
    skills = [{"name": "cutout", "path": str(tmp_path / "SKILL.md")}]
    delivered = input_fingerprints([image], skills)
    result = adapter()._build_input("текст", [image], skills, delivered)
    assert isinstance(result, str), "нічого нового — має лишитися лише текст"
    assert result.startswith("текст")


def test_the_carried_note_names_what_was_not_repeated(tmp_path: Path) -> None:
    image = picture(tmp_path)
    skills = [{"name": "cutout", "path": str(tmp_path / "SKILL.md")}]
    delivered = input_fingerprints([image], skills)
    result = adapter()._build_input("текст", [image], skills, delivered)
    assert "cutout" in result
    assert str(image) in result


def test_a_rewritten_picture_is_sent_again(tmp_path: Path) -> None:
    image = picture(tmp_path)
    delivered = input_fingerprints([image], [])
    image.write_bytes(b"\x89PNG\x00\x00\x00\x00 a different picture")
    items = adapter()._build_input("текст", [image], [], delivered)
    assert "LocalImageInput" in kinds(items)


def test_a_rewritten_skill_file_is_sent_again(tmp_path: Path) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("правила", encoding="utf-8")
    skills = [{"name": "cutout", "path": str(skill_file)}]
    delivered = input_fingerprints([], skills)
    skill_file.write_text("правила з новим пунктом", encoding="utf-8")
    items = adapter()._build_input("текст", [], skills, delivered)
    assert "SkillInput" in kinds(items)


def test_only_pictures_that_really_enter_the_thread_get_a_fingerprint(
    tmp_path: Path,
) -> None:
    image = picture(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("звіт", encoding="utf-8")
    missing = tmp_path / "no-such.png"
    marks = input_fingerprints([image, report, missing], [])
    assert list(marks) == [f"file:{image}"]


# --------------------------------------------------------------------------
# Рушій: другий хід тієї самої ноди
# --------------------------------------------------------------------------


def looping_flow(workspace: Path, image: Path) -> tuple[Workflow, FlowNode]:
    workflow = Workflow(name="Повтор", workspace=str(workspace))
    entry = FlowNode.create("entry")
    entry.config["text"] = "Зроби крок"
    executor = FlowNode.create("executor")
    executor.config["model"] = "executor-model"
    executor.config["attachments"] = [str(image)]
    executor.config["skills"] = [
        {"name": "cutout", "path": str(workspace / "SKILL.md")}
    ]
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    result.config["max_iterations"] = 3
    start = FlowEdge.create(entry.id, executor.id)
    start.source_path = "text"
    start.target_variable = "prompt"
    again = FlowEdge.create(result.id, executor.id, "false")
    again.source_path = "data.retry_context"
    again.target_variable = "prompt"
    review = FlowEdge.create(reviewer.id, result.id)
    review.source_path = "data"
    review.target_variable = "review"
    workflow.nodes.extend([entry, executor, reviewer, result])
    workflow.edges.extend(
        [start, FlowEdge.create(executor.id, reviewer.id), review, again]
    )
    return workflow, executor


def test_the_second_turn_of_a_thread_does_not_resend_the_picture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = picture(tmp_path)
    (tmp_path / "SKILL.md").write_text("правила", encoding="utf-8")
    seen: list[int] = []

    def responder(payload: object) -> str:
        call = dict(payload)  # type: ignore[arg-type]
        if call.get("model") == "executor-model":
            seen.append(len(call["attachments"]))
            return "зроблено"
        verdict = len(seen) >= 2
        return json.dumps(
            {"verdict": verdict, "score": 50, "reason": "", "must_fix": []},
            ensure_ascii=False,
        )

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    workflow, _ = looping_flow(tmp_path, image)
    WorkflowRunner(workflow, run_directory=tmp_path / "run").run()
    assert seen[0] == 1, "перший хід має принести картинку"
    assert seen[1:] == [0] * len(seen[1:]), "далі та сама картинка вже в треді"


def test_the_checkpoint_remembers_what_the_thread_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = picture(tmp_path)
    (tmp_path / "SKILL.md").write_text("правила", encoding="utf-8")

    def responder(payload: object) -> str:
        call = dict(payload)  # type: ignore[arg-type]
        if call.get("model") == "executor-model":
            return "зроблено"
        return json.dumps(
            {"verdict": True, "score": 90, "reason": "", "must_fix": []},
            ensure_ascii=False,
        )

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    workflow, executor = looping_flow(tmp_path, image)
    checkpoint = WorkflowRunner(workflow, run_directory=tmp_path / "run").run()
    thread_id = checkpoint.thread_ids[executor.id]
    assert f"file:{image}" in checkpoint.thread_inputs[thread_id]
    assert "skill:cutout" in checkpoint.thread_inputs[thread_id]
    restored = RunCheckpoint.from_dict(checkpoint.to_dict())
    assert restored.thread_inputs == checkpoint.thread_inputs


# --------------------------------------------------------------------------
# Кроки агента: корисне навантаження не має жити в протоколі
# --------------------------------------------------------------------------


def test_a_generated_image_payload_is_not_kept_in_the_step() -> None:
    payload = "iVBORw0KGgo" + "A" * 2_500_000
    steps = normalize_items(
        [{"type": "imageGeneration", "summary": "completed", "result": payload}]
    )
    kept = steps[0]["detail"]["result"]
    assert len(kept) < 5000
    assert "скорочено" in kept
    assert len(json.dumps(steps, ensure_ascii=False)) < 20_000


def test_a_short_value_survives_untouched() -> None:
    steps = normalize_items(
        [{"type": "commandExecution", "command": "ls", "aggregated_output": "ok"}]
    )
    assert steps[0]["detail"]["aggregated_output"] == "ok"


def test_paths_survive_the_shortening() -> None:
    steps = normalize_items(
        [
            {
                "type": "fileChange",
                "changes": [{"path": "C:/out/map.png"}],
                "diff": "x" * 900_000,
            }
        ]
    )
    assert codex_adapter.paths_from_item(steps[0]["detail"]) == ["C:/out/map.png"]


# --------------------------------------------------------------------------
# Калібрація: промпт має вміщатися в транспорт, а збій має бути видимим
# --------------------------------------------------------------------------

TRANSPORT_LIMIT = 1_048_576


def test_the_calibration_prompt_fits_the_transport_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, nodes = build_flow(tmp_path)
    runner = WorkflowRunner(workflow, run_directory=tmp_path / "run")
    heavy = [
        {
            "kind": "imageGeneration",
            "summary": "completed",
            "detail": {"result": "A" * 2_500_000},
        }
        for _ in range(3)
    ]
    runner.checkpoint.protocol_steps[nodes["executor"].id] = heavy
    runner.checkpoint.protocol_steps[nodes["reviewer"].id] = heavy
    runner.run()
    prompt = next(
        call["prompt"]
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "calibrator-model"
    )
    assert len(prompt) < TRANSPORT_LIMIT


def test_a_failed_calibration_analysis_is_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(payload: object) -> str:
        call = dict(payload)  # type: ignore[arg-type]
        if call.get("model") == "calibrator-model":
            raise RuntimeError("JSON-RPC error -32602: Input exceeds the maximum")
        return rejecting_responder(payload)

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    workflow, _ = build_flow(tmp_path)
    events: list[dict] = []
    WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    ).run()
    announced = [event for event in events if event["type"] == "calibration_failed"]
    assert announced, "мовчазний збій аналізу — саме те, що ховало помилку"
    assert "-32602" in announced[0]["message"]


# --------------------------------------------------------------------------
# Той самий файл, прикріплений двічі
# --------------------------------------------------------------------------


def test_a_pinned_skill_is_not_inlined_into_the_instructions_as_well(
    tmp_path: Path,
) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Правила вирізання\nне чіпай оригінал", encoding="utf-8")
    node = FlowNode.create("executor")
    node.config["skills"] = [{"name": "cutout", "path": str(skill_file)}]
    node.config["instruction_files"] = [str(skill_file)]
    node.config["instructions"] = "власна інструкція ноди"
    text = WorkflowRunner._compose_agent_instructions(node, tmp_path)
    assert "власна інструкція ноди" in text
    assert "не чіпай оригінал" not in text


def test_an_instruction_file_that_is_not_a_skill_stays(tmp_path: Path) -> None:
    guide = tmp_path / "GUIDE.md"
    guide.write_text("# Порядок здачі\nспершу перевір", encoding="utf-8")
    node = FlowNode.create("executor")
    node.config["instruction_files"] = [str(guide)]
    text = WorkflowRunner._compose_agent_instructions(node, tmp_path)
    assert "спершу перевір" in text


# --------------------------------------------------------------------------
# Мертва настройка
# --------------------------------------------------------------------------


def test_agent_nodes_have_no_dead_timeout_setting() -> None:
    for kind in ("executor", "task_reviewer", "prompt_reviewer", "calibrator"):
        assert "timeout_seconds" not in FlowNode.create(kind).config


def test_an_old_flow_loses_the_dead_timeout_setting() -> None:
    node = FlowNode.from_dict(
        {
            "id": "e" * 32,
            "kind": "executor",
            "title": "Executor",
            "config": {"timeout_seconds": 7200, "model": "executor-model"},
        }
    )
    assert "timeout_seconds" not in node.config
    assert node.config["model"] == "executor-model"
