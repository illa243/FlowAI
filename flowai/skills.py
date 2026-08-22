from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

SKILLS_ROOT = Path.home() / ".codex" / "skills"
FLOWAI_DIR = SKILLS_ROOT / ".flowai"
CATEGORIES_FILE = FLOWAI_DIR / "categories.json"
BACKUPS_DIR = FLOWAI_DIR / "backups"
DEFAULT_CATEGORY = "Без категорії"
SYSTEM_FOLDER = ".system"

# Скільки символів опису лишати в каталозі, який їде в промпт агента.
DESCRIPTION_LIMIT = 240

LOGGER = logging.getLogger(__name__)
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_frontmatter(text: str) -> tuple[str, str]:
    """Витягти `name` і `description` із YAML-заголовка SKILL.md."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return "", ""
    values = {key: _unquote(value) for key, value in _FIELD.findall(match.group(1))}
    return values.get("name", ""), values.get("description", "")


@dataclass(slots=True)
class SkillEntry:
    """Один скіл Codex так, як його бачить FlowAI."""

    name: str
    description: str
    path: Path
    scope: str = "user"
    enabled: bool = True
    error: str = ""
    category: str = ""
    tools: list[str] = field(default_factory=list)

    @property
    def skill_file(self) -> Path:
        return self.path / "SKILL.md"

    @property
    def reference_files(self) -> list[Path]:
        """Markdown із `references/` — саме його дозволено правити Рев'юеру."""
        directory = self.path / "references"
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.md"))

    @property
    def markdown_files(self) -> list[Path]:
        found = [self.skill_file] if self.skill_file.is_file() else []
        found.extend(self.reference_files)
        return found

    @property
    def editable(self) -> bool:
        """Системні скіли ставить Codex — правити й видаляти їх не можна."""
        return self.scope != "system"


def _entry_from_directory(directory: Path, scope: str) -> SkillEntry:
    skill_file = directory / "SKILL.md"
    if not skill_file.is_file():
        return SkillEntry(
            name=directory.name,
            description="",
            path=directory,
            scope=scope,
            error=f"У папці немає SKILL.md: {directory}",
        )
    try:
        text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return SkillEntry(
            name=directory.name,
            description="",
            path=directory,
            scope=scope,
            error=str(exc),
        )
    name, description = read_frontmatter(text)
    return SkillEntry(
        name=name or directory.name,
        description=description,
        path=directory,
        scope=scope,
    )


def scan_skills(root: Path = SKILLS_ROOT) -> list[SkillEntry]:
    """Прочитати скіли з диска — відкат, коли SDK не відповідає."""
    if not root.is_dir():
        return []
    entries: list[SkillEntry] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        if directory.name == SYSTEM_FOLDER:
            for nested in sorted(directory.iterdir()):
                if nested.is_dir():
                    entries.append(_entry_from_directory(nested, "system"))
            continue
        if directory.name.startswith("."):
            continue
        entries.append(_entry_from_directory(directory, "user"))
    return sorted(entries, key=lambda entry: entry.name.casefold())


def catalogue_text(entries: list[SkillEntry]) -> str:
    """Каталог скілів одним рядком на скіл для промпта Рев'юера."""
    lines: list[str] = []
    for entry in entries:
        if not entry.enabled or entry.error:
            continue
        description = entry.description.strip().replace("\n", " ")
        if len(description) > DESCRIPTION_LIMIT:
            description = description[:DESCRIPTION_LIMIT] + "…"
        lines.append(f"- `{entry.name}` — {description}")
    return "\n".join(lines)


def _entry_from_record(record: dict[str, Any]) -> SkillEntry:
    raw_path = str(record.get("path") or "")
    return SkillEntry(
        name=str(record.get("name") or Path(raw_path).name),
        description=str(record.get("description") or ""),
        path=Path(raw_path),
        scope=str(record.get("scope") or "user"),
        enabled=bool(record.get("enabled", True)),
    )


def list_skills(codex: Any | None = None, root: Path = SKILLS_ROOT) -> list[SkillEntry]:
    """Каталог скілів: спершу SDK, якщо він мовчить — диск."""
    records: list[dict[str, Any]] = []
    if codex is not None and hasattr(codex, "list_skills"):
        try:
            records = codex.list_skills()
        except Exception:
            LOGGER.exception("Не вдалося отримати каталог скілів із SDK")
            records = []
    if not records:
        return scan_skills(root)
    entries = [_entry_from_record(record) for record in records]
    return sorted(entries, key=lambda entry: entry.name.casefold())


def load_categories(path: Path = CATEGORIES_FILE) -> dict[str, str]:
    """Мапа «ім'я скіла → категорія». Биті дані мовчки ігноруються."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if str(key) and str(value)
    }


def save_categories(mapping: dict[str, str], path: Path = CATEGORIES_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def categorized(
    entries: list[SkillEntry], mapping: dict[str, str]
) -> dict[str, list[SkillEntry]]:
    """Розкласти скіли по секціях; безкатегорійні — останньою секцією."""
    groups: dict[str, list[SkillEntry]] = {}
    for entry in entries:
        name = mapping.get(entry.name, "").strip() or DEFAULT_CATEGORY
        entry.category = name
        groups.setdefault(name, []).append(entry)
    ordered = sorted(
        (name for name in groups if name != DEFAULT_CATEGORY),
        key=str.casefold,
    )
    result = {name: groups[name] for name in ordered}
    if DEFAULT_CATEGORY in groups:
        result[DEFAULT_CATEGORY] = groups[DEFAULT_CATEGORY]
    for items in result.values():
        items.sort(key=lambda entry: entry.name.casefold())
    return result


def backup_skill(entry: SkillEntry, root: Path = BACKUPS_DIR) -> Path:
    """Скопіювати скіл цілком перед тим, як щось у ньому міняти."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    target = root / entry.name / stamp
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(entry.path, target)
    return target


def list_backups(name: str, root: Path = BACKUPS_DIR) -> list[Path]:
    """Копії одного скіла, найновіші останніми."""
    directory = root / name
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir())


def restore_skill(backup: Path, entry: SkillEntry) -> None:
    """Повернути скіл зі збереженої копії, замінивши поточний вміст."""
    if not entry.editable:
        raise PermissionError(
            f"Скіл «{entry.name}» встановив Codex — його не можна відновлювати"
        )
    if not backup.is_dir():
        raise FileNotFoundError(f"Копію не знайдено: {backup}")
    if entry.path.exists():
        shutil.rmtree(entry.path)
    shutil.copytree(backup, entry.path)


def delete_skill(entry: SkillEntry) -> None:
    """Відправити скіл у Кошик Windows — без безслідного стирання."""
    if not entry.editable:
        raise PermissionError(
            f"Скіл «{entry.name}» встановив Codex — його не можна видалити"
        )
    from send2trash import send2trash

    send2trash(str(entry.path))


def import_skill(source: Path, root: Path = SKILLS_ROOT) -> SkillEntry:
    """Додати скіл у каталог із готової папки або .zip-архіву."""
    root.mkdir(parents=True, exist_ok=True)
    if source.is_file() and source.suffix.casefold() == ".zip":
        return _import_archive(source, root)
    if not source.is_dir():
        raise ValueError(f"Очікується папка скіла або .zip: {source}")
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"У папці немає SKILL.md: {source}")
    target = root / source.name
    if target.exists():
        raise FileExistsError(f"Скіл «{source.name}» уже є в каталозі")
    shutil.copytree(source, target)
    return _entry_from_directory(target, "user")


def _import_archive(archive: Path, root: Path) -> SkillEntry:
    with zipfile.ZipFile(archive) as bundle:
        names: list[str] = []
        for raw_name in bundle.namelist():
            normalized = raw_name.replace("\\", "/")
            member = PurePosixPath(normalized)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError("Архів містить небезпечний шлях")
            if normalized and not normalized.endswith("/"):
                names.append(normalized)
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1:
            raise ValueError(
                "Архів має містити рівно одну папку скіла верхнього рівня"
            )
        skill_name = roots.pop()
        if f"{skill_name}/SKILL.md" not in names:
            raise ValueError(f"В архіві немає {skill_name}/SKILL.md")
        target = root / skill_name
        if target.exists():
            raise FileExistsError(f"Скіл «{skill_name}» уже є в каталозі")
        bundle.extractall(root)
    return _entry_from_directory(target, "user")


def _step_text(step: dict[str, Any]) -> str:
    """Усе, де може трапитись шлях: команда, шлях файлу, текст кроку."""
    detail = step.get("detail")
    parts: list[str] = [str(step.get("summary", ""))]
    if isinstance(detail, dict):
        for key in ("command", "path", "file_path", "filePath", "text"):
            value = detail.get(key)
            if isinstance(value, str):
                parts.append(value)
        for change in detail.get("changes") or []:
            if isinstance(change, dict):
                parts.append(str(change.get("path", "")))
    return " ".join(parts)


def skills_used(steps: list[dict[str, Any]], root: Path = SKILLS_ROOT) -> list[str]:
    """Повернути скіли, які агент справді відкривав під час свого ходу."""
    known = {entry.name.casefold(): entry.name for entry in scan_skills(root)}
    if not known:
        return []
    prefix = str(root).casefold().replace("\\", "/").rstrip("/") + "/"
    found: list[str] = []
    for step in steps:
        haystack = _step_text(step).casefold().replace("\\\\", "\\")
        haystack = haystack.replace("\\", "/")
        start = 0
        while True:
            position = haystack.find(prefix, start)
            if position < 0:
                break
            start = position + len(prefix)
            tail = haystack[start:]
            candidate = tail.split("/", 1)[0].strip("'\"` ")
            real = known.get(candidate)
            if real is not None and real not in found:
                found.append(real)
    return found
