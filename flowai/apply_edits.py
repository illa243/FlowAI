from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .calibration import CalibrationReport, ProposedEdit
from .models import Workflow, normalize_managed_tasks
from .skills import BACKUPS_DIR, SKILLS_ROOT, SkillEntry, backup_skill, scan_skills


@dataclass(slots=True)
class AppliedEdit:
    """Результат однієї спроби застосувати правку."""

    edit: ProposedEdit
    ok: bool
    message: str = ""


def _skill_for_path(path: Path, skills_root: Path) -> SkillEntry | None:
    resolved = path.resolve()
    for entry in scan_skills(skills_root):
        try:
            resolved.relative_to(entry.path.resolve())
        except ValueError:
            continue
        return entry
    return None


def _replace_once(text: str, before: str, after: str) -> tuple[str, str]:
    """Замінити рівно один випадок; повернути текст і повідомлення."""
    count = text.count(before)
    if count == 0:
        return text, "Фрагмент «було» не знайдено — файл змінився після аналізу"
    if count > 1:
        return text, f"Фрагмент «було» трапляється {count} разів — неоднозначно"
    return text.replace(before, after, 1), ""


def _apply_skill_edit(
    edit: ProposedEdit, skills_root: Path, backups_root: Path
) -> AppliedEdit:
    path = Path(edit.path)
    if not path.is_absolute() or not path.is_file():
        return AppliedEdit(edit, False, f"Файл не знайдено: {edit.path}")
    entry = _skill_for_path(path, skills_root)
    if entry is None:
        return AppliedEdit(
            edit, False, "Файл лежить поза каталогом скілів — правку відхилено"
        )
    resolved = path.resolve()
    skill_root = entry.path.resolve()
    references_root = skill_root / "references"
    allowed = resolved == skill_root / "SKILL.md" or (
        resolved.suffix.casefold() == ".md"
        and resolved.parent == references_root
    )
    if not allowed:
        return AppliedEdit(
            edit,
            False,
            "Правити можна лише SKILL.md і references/*.md",
        )
    if not entry.editable:
        return AppliedEdit(
            edit, False, f"Скіл «{entry.name}» встановив Codex — його не правимо"
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return AppliedEdit(edit, False, str(exc))
    updated, message = _replace_once(text, edit.before, edit.after)
    if message:
        return AppliedEdit(edit, False, message)
    backup_skill(entry, root=backups_root)
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return AppliedEdit(edit, False, str(exc))
    return AppliedEdit(edit, True, f"Оновлено {path.name}")


def _apply_task_edit(edit: ProposedEdit, workflow: Workflow) -> AppliedEdit:
    for node in workflow.nodes_of_kind("tasks_manager"):
        tasks = normalize_managed_tasks(node.config.get("tasks"))
        for task in tasks:
            if str(task["id"]) != edit.task_id:
                continue
            updated, message = _replace_once(
                str(task.get("prompt", "")), edit.before, edit.after
            )
            if message:
                if str(task.get("prompt", "")).strip() == edit.before.strip():
                    updated = edit.after
                else:
                    return AppliedEdit(edit, False, message)
            task["prompt"] = updated
            node.config["tasks"] = tasks
            return AppliedEdit(edit, True, "Промпт завдання оновлено")
    return AppliedEdit(edit, False, f"Завдання {edit.task_id} не знайдено")


def _apply_node_edit(edit: ProposedEdit, workflow: Workflow) -> AppliedEdit:
    node = workflow.find(edit.node_id)
    if node is None:
        return AppliedEdit(edit, False, f"Блок {edit.node_id} не знайдено")
    key = "prompt" if edit.target == "node_prompt" else "instructions"
    updated, message = _replace_once(
        str(node.config.get(key, "")), edit.before, edit.after
    )
    if message:
        if str(node.config.get(key, "")).strip() == edit.before.strip():
            updated = edit.after
        else:
            return AppliedEdit(edit, False, message)
    node.config[key] = updated
    return AppliedEdit(edit, True, f"Оновлено «{node.title}»")


def apply_edits(
    report: CalibrationReport,
    workflow: Workflow,
    *,
    skills_root: Path = SKILLS_ROOT,
    backups_root: Path = BACKUPS_DIR,
) -> list[AppliedEdit]:
    """Записати відмічені правки окремо, не блокуючи решту при помилці."""
    applied: list[AppliedEdit] = []
    for edit in report.accepted_edits():
        if edit.target == "skill_file":
            applied.append(_apply_skill_edit(edit, skills_root, backups_root))
        elif edit.target == "task_prompt":
            applied.append(_apply_task_edit(edit, workflow))
        else:
            applied.append(_apply_node_edit(edit, workflow))
    return applied


def pin_skills(
    workflow: Workflow,
    node_id: str,
    names: list[str],
    *,
    skills_root: Path = SKILLS_ROOT,
) -> list[str]:
    """Закріпити відомі скіли за нодою без дублікатів."""
    node = workflow.find(node_id)
    if node is None:
        return []
    known = {entry.name: entry for entry in scan_skills(skills_root)}
    current = [
        dict(item)
        for item in node.config.get("skills", [])
        if isinstance(item, dict)
    ]
    pinned: list[str] = []
    for name in names:
        entry = known.get(name)
        if entry is None or any(item.get("name") == name for item in current):
            continue
        current.append({"name": entry.name, "path": str(entry.path)})
        pinned.append(entry.name)
    node.config["skills"] = current
    return pinned
