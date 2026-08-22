from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUIDES_DIR = PROJECT_ROOT / "guides"
ALWAYS = {"node-guide": PROJECT_ROOT / "FLOWAI_NODE_GUIDE.md"}


def _title_and_summary(text: str, fallback: str) -> tuple[str, str]:
    title = fallback
    summary = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") and title == fallback:
            title = stripped.lstrip("#").strip()
            continue
        if not stripped.startswith("#"):
            summary = stripped[:200]
            break
    return title, summary


def _files() -> dict[str, Path]:
    found = dict(ALWAYS)
    if GUIDES_DIR.is_dir():
        for path in sorted(GUIDES_DIR.glob("*.md")):
            found.setdefault(path.stem, path)
    return {name: path for name, path in found.items() if path.is_file()}


def guides_root() -> Path:
    return GUIDES_DIR


def list_guides() -> list[dict[str, str]]:
    """List available Markdown guidance for agents that compose Flow files."""
    entries: list[dict[str, str]] = []
    for name, path in _files().items():
        text = path.read_text(encoding="utf-8", errors="replace")
        title, summary = _title_and_summary(text, name)
        entries.append(
            {"name": name, "title": title, "summary": summary, "path": str(path)}
        )
    return entries


def read_guide(name: str, section: str = "") -> str:
    """Read a complete guide or one matching Markdown section."""
    path = _files().get(name)
    if path is None:
        raise ValueError(f"Довідник «{name}» не знайдено")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not section:
        return text
    pattern = re.compile(
        rf"^#{{1,6}}\s*.*{re.escape(section)}.*$", re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"Розділ «{section}» у довіднику «{name}» не знайдено")
    heading = match.group(0).lstrip()
    level = len(heading) - len(heading.lstrip("#"))
    tail = text[match.start() :]
    following = re.compile(rf"^#{{1,{level}}}\s", re.MULTILINE)
    next_match = following.search(tail, pos=len(match.group(0)))
    return tail[: next_match.start()] if next_match else tail
