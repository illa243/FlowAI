from __future__ import annotations

import json
from pathlib import Path

from .models import Workflow

FLOW_SUFFIX = ".flowai.json"


def save_workflow(workflow: Workflow, path: str | Path) -> Path:
    target = Path(path)
    if not target.name.lower().endswith(FLOW_SUFFIX):
        target = target.with_name(target.name + FLOW_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_workflow(path: str | Path) -> Workflow:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    return Workflow.from_dict(raw)
