from __future__ import annotations

from pathlib import Path

from flowai.engine import WorkflowRunner
from flowai.models import FlowNode, Workflow
from flowai.project_layout import (
    isolated_flow_path,
    local_output_path,
    relocated_project_path,
)


def test_saved_flow_uses_its_own_directory_as_the_only_workspace(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Project" / "Project.flowai.json"
    legacy_source = tmp_path / "source"
    legacy_source.mkdir()
    workflow = Workflow(workspace=str(legacy_source))

    assert workflow.resolved_workspace(project) == project.parent.resolve()
    assert workflow.resolved_additional_folders(project) == [legacy_source.resolve()]


def test_external_output_path_is_remapped_inside_project_artifacts(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    external = tmp_path / "source" / "flow_outputs" / "result.png"

    resolved = local_output_path(str(external), project)

    assert resolved.is_relative_to(project)
    assert resolved.parts[-4:] == (
        "artifacts",
        "source",
        "flow_outputs",
        "result.png",
    )


def test_flat_flow_path_is_isolated_and_old_registry_path_is_relocated(
    tmp_path: Path,
) -> None:
    flat = tmp_path / "My Flow.flowai.json"
    isolated = isolated_flow_path(flat)
    isolated.parent.mkdir()
    isolated.write_text("{}", encoding="utf-8")

    assert isolated == tmp_path / "My Flow" / "My Flow.flowai.json"
    assert relocated_project_path(flat) == isolated


def test_every_agent_receives_project_local_output_rule(tmp_path: Path) -> None:
    node = FlowNode.create("executor")
    instructions = WorkflowRunner._compose_agent_instructions(node, tmp_path)

    assert str(tmp_path) in instructions
    assert "Усі нові файли" in instructions
    assert "лише всередині цієї теки" in instructions
    assert "використовуй tools" in instructions
