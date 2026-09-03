from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.engine import InterventionRequired, RunCheckpoint, WorkflowRunner
from flowai.models import FlowNode, Workflow
from flowai.persistence import load_workflow
from flowai.ui.main_window import ResultConfirmationDialog
from flowai.ui_workflow import (
    PhotoshopAutomation,
    ReferenceAnalysisCacheError,
    append_ui_learning,
    normalize_ui_tasks,
    validate_declared_output_paths,
    validate_reference_analysis_cache,
    verify_variant_manifest,
    workspace_child,
)


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _runner(tmp_path: Path, *nodes: FlowNode) -> WorkflowRunner:
    workflow = Workflow(name="UI test", workspace=str(tmp_path), nodes=list(nodes))
    return WorkflowRunner(workflow, project_path=tmp_path / "ui.flowai.json")


def test_game_ui_template_is_valid() -> None:
    path = Path(__file__).parents[1] / "examples" / "game_ui_workflow.flowai.json"
    workflow = load_workflow(path)

    assert workflow.validate() == []
    assert workflow.find("ui-tasks").config["task_source"] == "input_once"
    assert workflow.find("asset-result").config["final_task_result"] is True
    assert workflow.find("variant-result").config["final_task_result"] is False
    assert workflow.additional_folders == [r"C:\Users\illia\Desktop\UI_refs"]
    assert workflow.find("ui-planner").config["skills"][0]["name"] == "modern-ui"
    assert workflow.find("concept-executor").config["reference_cache"][
        "mode"
    ] == "sha256_once"
    assert all(edge.control_points or edge.id in {"e01", "e03", "e05", "e07", "e08", "e10", "e12", "e14", "e16", "e18", "e21", "e23"} for edge in workflow.edges)


def _reference_cache_fixture(tmp_path: Path) -> tuple[dict[str, str], str]:
    source = tmp_path / "refs"
    source.mkdir()
    first = source / "A.png"
    second = source / "set" / "B.jpg"
    second.parent.mkdir()
    first.write_bytes(b"first-reference")
    second.write_bytes(b"second-reference")
    files = []
    for path in (first, second):
        relative = path.relative_to(source).as_posix()
        files.append(
            {
                "relative_path": relative,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        )
    digest = sha256()
    for item in files:
        digest.update(item["relative_path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    library_hash = digest.hexdigest()
    manifest = tmp_path / "reference-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "file_count": 2,
                "library_sha256": library_hash,
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    analysis = tmp_path / "analysis.md"
    analysis.write_text(
        f"# Cached analysis\n\nLibrary SHA-256: `{library_hash}`\n",
        encoding="utf-8",
    )
    return (
        {
            "mode": "sha256_once",
            "source_dir": str(source),
            "manifest_path": str(manifest),
            "analysis_path": str(analysis),
            "library_sha256": library_hash,
        },
        library_hash,
    )


def test_reference_analysis_cache_reuses_written_analysis(tmp_path: Path) -> None:
    config, library_hash = _reference_cache_fixture(tmp_path)

    receipt = validate_reference_analysis_cache(config, tmp_path)

    assert receipt["file_count"] == 2
    assert receipt["library_sha256"] == library_hash
    assert receipt["analysis_path"] == config["analysis_path"]
    assert "analysis" not in receipt


def test_reference_analysis_cache_detects_changed_source(tmp_path: Path) -> None:
    config, _library_hash = _reference_cache_fixture(tmp_path)
    (tmp_path / "refs" / "A.png").write_bytes(b"changed-reference")

    with pytest.raises(ReferenceAnalysisCacheError, match="змінився"):
        validate_reference_analysis_cache(config, tmp_path)


def test_runner_validates_same_reference_cache_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, library_hash = _reference_cache_fixture(tmp_path)
    first = FlowNode.create("executor")
    second = FlowNode.create("task_reviewer")
    first.config["reference_cache"] = config
    second.config["reference_cache"] = dict(config)
    runner = _runner(tmp_path, first, second)
    calls = 0

    def fake_validate(_config: dict[str, str], _workspace: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "source_dir": config["source_dir"],
            "manifest_path": config["manifest_path"],
            "analysis_path": config["analysis_path"],
            "file_count": 2,
            "library_sha256": library_hash,
        }

    monkeypatch.setattr("flowai.engine.validate_reference_analysis_cache", fake_validate)

    assert runner._reference_analysis_for_node(first, tmp_path)["file_count"] == 2
    assert runner._reference_analysis_for_node(second, tmp_path)["file_count"] == 2
    assert calls == 1


def test_dynamic_tasks_manager_freezes_approved_plan(tmp_path: Path) -> None:
    manager = FlowNode.create("tasks_manager")
    manager.config.update(
        {
            "task_source": "input_once",
            "plan_save_path": "ui_project_spec.json",
        }
    )
    runner = _runner(tmp_path, manager)
    plan = {
        "operation": "create",
        "assumptions": ["1920x1080"],
        "tasks": [
            {
                "id": "ui-kit",
                "title": "UI kit",
                "prompt": "Create shared UI kit",
                "states": ["normal", "disabled"],
            },
            {
                "id": "inventory",
                "title": "Inventory",
                "prompt": "Create inventory screen",
                "states": ["normal", "hover", "empty"],
            },
        ],
    }

    first = runner._execute_tasks_manager(
        manager, {"approved_plan": plan}, tmp_path
    )
    frozen_hash = first.data["ui_plan_hash"]
    saved = json.loads((tmp_path / "ui_project_spec.json").read_text(encoding="utf-8"))

    changed = {"tasks": [{"id": "wrong", "prompt": "Do not replace snapshot"}]}
    second = runner._execute_tasks_manager(
        manager, {"approved_plan": changed}, tmp_path
    )

    assert first.data["task"]["id"] == "ui-kit"
    assert second.data["task"]["id"] == "inventory"
    assert second.data["ui_plan_hash"] == frozen_hash
    assert [item["id"] for item in saved["tasks"]] == ["ui-kit", "inventory"]
    assert [item["id"] for item in runner.checkpoint.ui_plan_snapshots[manager.id]["tasks"]] == ["ui-kit", "inventory"]


def test_dynamic_manager_is_not_seeded_before_plan_approval(tmp_path: Path) -> None:
    entry = FlowNode.create("entry")
    manager = FlowNode.create("tasks_manager")
    manager.config["task_source"] = "input_once"
    runner = WorkflowRunner(
        Workflow(nodes=[entry, manager], workspace=str(tmp_path)),
        project_path=tmp_path / "ui.flowai.json",
    )

    assert runner._initial_queue() == [entry.id]


def test_checkpoint_round_trip_keeps_ui_state() -> None:
    checkpoint = RunCheckpoint(
        ui_plan_snapshots={"manager": {"plan_hash": "ABC", "tasks": []}},
        result_drafts={"result": {"note": "Keep", "selected_variant_ids": ["V02"]}},
        retry_guards={"result:task": {"repeat_count": 2}},
        learning_event_ids=["EVENT"],
        photoshop_reports={"builder": {"opened": True}},
        variant_manifests={"concept": {"round_id": "round-001"}},
    )

    restored = RunCheckpoint.from_dict(checkpoint.to_dict())

    assert restored.ui_plan_snapshots == checkpoint.ui_plan_snapshots
    assert restored.result_drafts == checkpoint.result_drafts
    assert restored.retry_guards == checkpoint.retry_guards
    assert restored.learning_event_ids == ["EVENT"]
    assert restored.photoshop_reports["builder"]["opened"] is True
    assert restored.variant_manifests["concept"]["round_id"] == "round-001"


def test_variant_selection_returns_structured_multiple_choice(tmp_path: Path) -> None:
    result = FlowNode.create("result")
    result.config.update(
        {
            "confirmation_mode": "variant_selection",
            "wait_for_confirmation": True,
            "confirmation_ports": ["true"],
            "final_task_result": False,
            "true_limit": 10,
        }
    )
    runner = WorkflowRunner(
        Workflow(nodes=[result], workspace=str(tmp_path)),
        intervention_responses={
            result.id: {
                "action": "select_variants",
                "selected_variant_ids": ["V01", "V03"],
                "note": "V01 layout, V03 controls",
            }
        },
    )
    inputs = {
        "review": {"verdict": True, "score": 91, "issues": []},
        "concepts": {
            "variants": [
                {"variant_id": "V01", "path": "V01.png", "sha256": "A"},
                {"variant_id": "V02", "path": "V02.png", "sha256": "B"},
                {"variant_id": "V03", "path": "V03.png", "sha256": "C"},
                {"variant_id": "V04", "path": "V04.png", "sha256": "D"},
            ]
        },
    }

    outcome = runner._execute_result(result, inputs, {"inputs": inputs}, tmp_path)

    assert outcome.data["branch"] == "true"
    assert outcome.data["selection_mode"] == "multiple"
    assert outcome.data["selected_variant_ids"] == ["V01", "V03"]
    assert outcome.data["approved_artifact_hash"]
    assert "V01 layout" in outcome.data["user_note"]


def test_confirmation_ports_skip_negative_technical_qa(tmp_path: Path) -> None:
    result = FlowNode.create("result")
    result.config.update(
        {
            "wait_for_confirmation": True,
            "confirmation_mode": "asset_approval",
            "confirmation_ports": ["true"],
            "false_limit": 10,
        }
    )
    runner = _runner(tmp_path, result)
    review = {
        "verdict": False,
        "score": 70,
        "issues": [
            {
                "defect_id": "PSD-GROUPS",
                "category": "technical_blocker",
                "severity": "blocking",
                "must_fix": "Add required groups",
            }
        ],
        "must_fix": ["Add required groups"],
    }

    outcome = runner._execute_result(
        result, {"review": review}, {"inputs": {"review": review}}, tmp_path
    )

    assert outcome.data["branch"] == "false"


def test_visual_preference_can_request_asset_override(tmp_path: Path) -> None:
    result = FlowNode.create("result")
    result.config.update(
        {
            "wait_for_confirmation": True,
            "confirmation_mode": "asset_approval",
            "confirmation_ports": ["true"],
            "true_limit": 10,
        }
    )
    review = {
        "verdict": False,
        "score": 88,
        "issues": [
            {
                "defect_id": "STYLE-01",
                "category": "visual_preference",
                "severity": "warning",
                "must_fix": "Consider a colder blue",
            }
        ],
    }
    runner = WorkflowRunner(
        Workflow(nodes=[result], workspace=str(tmp_path)),
        intervention_responses={result.id: {"action": "override_visual"}},
    )

    outcome = runner._execute_result(
        result, {"review": review}, {"inputs": {"review": review}}, tmp_path
    )

    assert outcome.data["branch"] == "true"
    assert outcome.data["action"] == "override_visual"


def test_retry_guard_pauses_on_second_same_defect(tmp_path: Path) -> None:
    result = FlowNode.create("result")
    result.config.update(
        {
            "retry_guard_enabled": True,
            "retry_guard_threshold": 2,
            "false_limit": 10,
        }
    )
    runner = _runner(tmp_path, result)
    review = {
        "verdict": False,
        "score": 60,
        "issues": [
            {
                "defect_id": "BUTTON-MISSING",
                "category": "missing_requirement",
                "severity": "blocking",
                "must_fix": "Restore the button",
            }
        ],
    }

    first = runner._execute_result(
        result, {"review": review}, {"inputs": {"review": review}}, tmp_path
    )
    assert first.data["branch"] == "false"

    with pytest.raises(InterventionRequired) as raised:
        runner._execute_result(
            result, {"review": review}, {"inputs": {"review": review}}, tmp_path
        )

    assert raised.value.request["type"] == "retry_attention"
    assert raised.value.request["repeated_defect_ids"] == ["BUTTON-MISSING"]


def test_learning_is_project_local_and_profile_is_refreshed(tmp_path: Path) -> None:
    log, profile = append_ui_learning(
        tmp_path,
        {
            "user_note": "Buttons must use the approved padding",
            "accepted": True,
            "candidate_path": "tasks/inventory/psd/inventory.psd",
            "review": {"verdict": True, "issues": []},
        },
    )

    assert log.parent == tmp_path / "learnings"
    assert "approved padding" in profile.read_text(encoding="utf-8")
    assert "inventory.psd" in profile.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        workspace_child(tmp_path, tmp_path.parent / "outside.txt")


def test_declared_outputs_cannot_escape_project(tmp_path: Path) -> None:
    inside = tmp_path / "tasks" / "screen" / "result.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"png")

    assert validate_declared_output_paths(
        {"candidate_path": "tasks/screen/result.png"}, tmp_path
    ) == [str(inside.resolve())]
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="межі проєкту"):
        validate_declared_output_paths({"candidate_path": str(outside)}, tmp_path)


def test_photoshop_validation_uses_project_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    psd = tmp_path / "tasks" / "screen" / "psd" / "screen.psd"
    psd.parent.mkdir(parents=True)
    psd.write_bytes(b"real-file-placeholder-for-mocked-photoshop")
    automation = PhotoshopAutomation(tmp_path)

    def fake_run_jsx(_jsx: str, *, name: str) -> Path:
        automation.runtime.mkdir(parents=True, exist_ok=True)
        token = name.removeprefix("validate-").removesuffix(".jsx")
        report = automation.runtime / f"validation-{token}.json"
        report.write_text(
            json.dumps(
                {
                    "opened": True,
                    "width": 1920,
                    "height": 1080,
                    "top_level_groups": ["Background", "Controls", "States"],
                    "layer_count": 12,
                    "layer_comp_count": 3,
                }
            ),
            encoding="utf-8",
        )
        return automation.runtime / name

    monkeypatch.setattr(automation, "run_jsx", fake_run_jsx)

    report = automation.validate_psd(psd)

    assert report["opened"] is True
    assert Path(report["report_path"]).is_relative_to(tmp_path)
    assert report["layer_comp_count"] == 3


def test_normalize_ui_tasks_keeps_screen_contract() -> None:
    tasks = normalize_ui_tasks(
        [
            {
                "id": "inventory",
                "title": "Inventory",
                "prompt": "Build it",
                "screen": "inventory",
                "states": ["normal", "empty"],
                "acceptance_criteria": ["Editable controls"],
            }
        ]
    )

    assert tasks[0]["screen"] == "inventory"
    assert tasks[0]["states"] == ["normal", "empty"]
    assert tasks[0]["export_profile"] == "baseline"


def test_variant_contract_preserves_frozen_hashes(tmp_path: Path) -> None:
    first_variants = []
    for index in range(1, 5):
        path = tmp_path / f"V{index:02d}.png"
        path.write_bytes(f"image-{index}".encode())
        first_variants.append(
            {"variant_id": f"V{index:02d}", "path": path.name, "direction": "test"}
        )
    first = verify_variant_manifest(
        {"round_id": "round-001", "variants": first_variants}, tmp_path
    )
    (tmp_path / "V03-new.png").write_bytes(b"new-image-3")
    second_variants = [dict(item) for item in first["variants"]]
    second_variants[2] = {
        "variant_id": "V03",
        "path": "V03-new.png",
        "direction": "fixed",
    }

    second = verify_variant_manifest(
        {"round_id": "round-002", "variants": second_variants},
        tmp_path,
        previous=first,
        retry_context={
            "retry_variant_ids": ["V03"],
            "frozen_variants": [
                item for item in first["variants"] if item["variant_id"] != "V03"
            ],
        },
    )

    assert second["variants"][0]["sha256"] == first["variants"][0]["sha256"]
    assert second["variants"][2]["sha256"] != first["variants"][2]["sha256"]

    (tmp_path / "V01.png").write_bytes(b"tampered")
    tampered_variants = [dict(item) for item in second_variants]
    tampered_variants[0].pop("sha256", None)
    with pytest.raises(ValueError, match="заморожений V01"):
        verify_variant_manifest(
            {"round_id": "round-003", "variants": tampered_variants},
            tmp_path,
            previous=second,
            retry_context={"frozen_variants": first["variants"]},
        )


def test_variant_dialog_returns_checkboxes_and_multi_note() -> None:
    application()
    dialog = ResultConfirmationDialog(
        {
            "node_title": "Choose concepts",
            "type": "result_confirmation",
            "confirmation_mode": "variant_selection",
            "verdict": True,
            "variants": [
                {"variant_id": "V01", "direction": "Compact"},
                {"variant_id": "V02", "direction": "Decorative"},
                {"variant_id": "V03", "direction": "Minimal"},
                {"variant_id": "V04", "direction": "Bold"},
            ],
            "files": [],
        }
    )
    dialog.variant_checks["V01"].setChecked(True)
    dialog.variant_checks["V03"].setChecked(True)
    dialog.feedback.setPlainText("V01 layout and V03 typography")

    dialog._accept()

    assert dialog.response == {
        "action": "select_variants",
        "selected_variant_ids": ["V01", "V03"],
        "selection_mode": "multiple",
        "note": "V01 layout and V03 typography",
    }


def test_plan_dialog_returns_edited_json() -> None:
    application()
    dialog = ResultConfirmationDialog(
        {
            "node_title": "Plan",
            "type": "result_confirmation",
            "confirmation_mode": "plan_approval",
            "verdict": True,
            "ui_project_spec": {"tasks": [{"id": "a", "prompt": "Before"}]},
            "files": [],
        }
    )
    assert dialog.plan_editor is not None
    dialog.plan_editor.setPlainText(
        json.dumps({"tasks": [{"id": "a", "prompt": "After"}]})
    )

    dialog._accept()

    assert dialog.response["action"] == "approve_plan"
    assert dialog.response["approved_plan"]["tasks"][0]["prompt"] == "After"


def test_asset_approval_labels_synthesis_png_separately_from_psd() -> None:
    application()
    synthesis = ResultConfirmationDialog(
        {
            "node_title": "Synthesis Approval",
            "type": "result_confirmation",
            "confirmation_mode": "asset_approval",
            "verdict": True,
            "candidate_path": "tasks/screen/synthesis/round-001/Synthesis.png",
            "files": [],
        }
    )
    psd = ResultConfirmationDialog(
        {
            "node_title": "Asset Approval",
            "type": "result_confirmation",
            "confirmation_mode": "asset_approval",
            "verdict": True,
            "candidate_path": "tasks/screen/psd/screen.psd",
            "files": [],
        }
    )

    assert synthesis.windowTitle() == "Підтвердження Synthesis PNG"
    assert synthesis.continue_button.text() == "Прийняти Synthesis PNG"
    assert psd.windowTitle() == "Підтвердження PSD"
    assert psd.continue_button.text() == "Прийняти PSD"
    synthesis.close()
    psd.close()
