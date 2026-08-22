from __future__ import annotations

import re
from pathlib import Path

from .persistence import FLOW_SUFFIX

RUNS_DIR = "runs"
ARTIFACTS_DIR = "artifacts"
REPORTS_DIR = "reports"
TOOLS_DIR = "tools"

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def project_root(project_path: Path | None) -> Path:
    """Return the only directory in which a Flow may create new content."""
    if project_path is not None:
        return project_path.expanduser().resolve().parent
    return Path.cwd().resolve()


def flow_stem(path: Path) -> str:
    name = path.name
    if name.casefold().endswith(FLOW_SUFFIX):
        return name[: -len(FLOW_SUFFIX)]
    return path.stem


def safe_project_name(name: str) -> str:
    cleaned = _INVALID_NAME.sub("_", name).strip(" .")
    return cleaned or "Flow"


def isolated_flow_path(selected: Path) -> Path:
    """Place a Flow in a same-named project directory unless already isolated."""
    selected = selected.expanduser()
    folder = safe_project_name(flow_stem(selected))
    if selected.parent.name.casefold() == folder.casefold():
        return selected
    return selected.parent / folder / selected.name


def relocated_project_path(path: Path) -> Path:
    """Find the conventional new location of a project moved from a flat folder."""
    if path.is_file():
        return path
    nested = path.parent / safe_project_name(flow_stem(path)) / path.name
    if nested.is_file():
        return nested
    collection = path.parent / "!_projects"
    if collection.is_dir():
        matches = list(collection.glob(f"*/{path.name}"))
        if len(matches) == 1:
            return matches[0]
    return path


def is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def local_output_path(raw_path: str, root: Path, category: str = ARTIFACTS_DIR) -> Path:
    """Resolve output inside the Flow project, remapping legacy external paths."""
    root = root.resolve()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return (root / path).resolve()
    path = path.resolve()
    if is_within(path, root):
        return path
    parent_parts = [part for part in path.parent.parts if part not in {path.anchor, ""}]
    tail = parent_parts[-2:]
    return root.joinpath(category, *tail, path.name).resolve()
