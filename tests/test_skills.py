from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from flowai.codex_adapter import CodexAdapter
from flowai.skills import (
    DEFAULT_CATEGORY,
    SkillEntry,
    backup_skill,
    catalogue_text,
    categorized,
    delete_skill,
    import_skill,
    list_backups,
    list_skills,
    load_categories,
    read_frontmatter,
    restore_skill,
    save_categories,
    scan_skills,
    skills_used,
)


def make_skill(root: Path, name: str, description: str, *, body: str = "") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n{body}\n",
        encoding="utf-8",
    )
    return directory


def test_read_frontmatter_extracts_name_and_description() -> None:
    text = "---\nname: birds-map\ndescription: Розкладає карту\n---\n\n# Birds\n"
    assert read_frontmatter(text) == ("birds-map", "Розкладає карту")


def test_read_frontmatter_survives_quoted_values() -> None:
    text = '---\nname: "figma-use"\ndescription: "**MANDATORY** prerequisite"\n---\n'
    assert read_frontmatter(text) == ("figma-use", "**MANDATORY** prerequisite")


def test_read_frontmatter_returns_empty_without_frontmatter() -> None:
    assert read_frontmatter("# Просто заголовок\n") == ("", "")


def test_scan_skills_finds_every_skill_folder(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Прибирає фон")
    make_skill(tmp_path, "birds-map", "Розкладає карту")
    entries = scan_skills(tmp_path)
    assert [entry.name for entry in entries] == ["birds-map", "image-cutout"]
    assert entries[0].description == "Розкладає карту"
    assert entries[0].scope == "user"


def test_scan_skills_marks_system_folder(tmp_path: Path) -> None:
    make_skill(tmp_path / ".system", "imagegen", "Генерує картинки")
    entries = scan_skills(tmp_path)
    assert [(entry.name, entry.scope) for entry in entries] == [
        ("imagegen", "system")
    ]
    assert entries[0].editable is False


def test_scan_skills_reports_broken_skill(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir(parents=True)
    entries = scan_skills(tmp_path)
    assert entries[0].name == "broken"
    assert "SKILL.md" in entries[0].error


def test_reference_files_are_listed(tmp_path: Path) -> None:
    directory = make_skill(tmp_path, "birds-map", "Розкладає карту")
    (directory / "references").mkdir()
    (directory / "references" / "grammar.md").write_text("текст", encoding="utf-8")
    (directory / "scripts").mkdir()
    (directory / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    entry = scan_skills(tmp_path)[0]
    assert [path.name for path in entry.reference_files] == ["grammar.md"]
    assert [path.name for path in entry.markdown_files] == ["SKILL.md", "grammar.md"]


def test_catalogue_text_is_compact_and_named(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Прибирає фон із фото")
    text = catalogue_text(scan_skills(tmp_path))
    assert "image-cutout" in text
    assert "Прибирає фон із фото" in text
    assert text.startswith("- ")


def test_default_category_is_named() -> None:
    assert DEFAULT_CATEGORY == "Без категорії"


def test_skill_entry_defaults() -> None:
    entry = SkillEntry(name="x", description="y", path=Path("z"))
    assert entry.enabled is True
    assert entry.scope == "user"
    assert entry.editable is True


class FakeClient:
    def __init__(self, payload: object, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    def request(self, method: str, params: object = None) -> object:
        self.calls.append((method, params))
        if self.fail:
            raise RuntimeError("метод недоступний")
        return self.payload


def test_adapter_lists_skills_through_the_sdk() -> None:
    adapter = CodexAdapter()
    adapter._client = object()
    adapter._client_handle = FakeClient(
        {
            "data": [
                {
                    "cwd": "d:/proj",
                    "errors": [],
                    "skills": [
                        {
                            "name": "image-cutout",
                            "description": "Прибирає фон",
                            "path": "C:/skills/image-cutout",
                            "scope": "user",
                            "enabled": True,
                        }
                    ],
                }
            ]
        }
    )
    assert adapter.list_skills() == [
        {
            "name": "image-cutout",
            "description": "Прибирає фон",
            "path": "C:/skills/image-cutout",
            "scope": "user",
            "enabled": True,
        }
    ]


def test_adapter_returns_nothing_when_the_method_is_missing() -> None:
    adapter = CodexAdapter()
    adapter._client = object()
    adapter._client_handle = FakeClient({}, fail=True)
    assert adapter.list_skills() == []


def test_adapter_writes_the_enabled_flag() -> None:
    adapter = CodexAdapter()
    adapter._client = object()
    handle = FakeClient({"effectiveEnabled": False})
    adapter._client_handle = handle
    assert adapter.set_skill_enabled("birds-map", False) is True
    assert handle.calls == [
        ("skills/config/write", {"name": "birds-map", "enabled": False})
    ]


class StubCodex:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def list_skills(self, cwd: object = None) -> list[dict[str, object]]:
        return self.records


def test_list_skills_prefers_the_sdk(tmp_path: Path) -> None:
    make_skill(tmp_path, "on-disk-only", "Не має значення")
    codex = StubCodex(
        [
            {
                "name": "image-cutout",
                "description": "Прибирає фон",
                "path": str(tmp_path / "image-cutout"),
                "scope": "user",
                "enabled": False,
            }
        ]
    )
    entries = list_skills(codex, root=tmp_path)
    assert [entry.name for entry in entries] == ["image-cutout"]
    assert entries[0].enabled is False


def test_list_skills_falls_back_to_disk(tmp_path: Path) -> None:
    make_skill(tmp_path, "birds-map", "Розкладає карту")
    entries = list_skills(None, root=tmp_path)
    assert [entry.name for entry in entries] == ["birds-map"]


def test_categories_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "categories.json"
    save_categories({"image-cutout": "Images"}, path=target)
    assert load_categories(path=target) == {"image-cutout": "Images"}
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "image-cutout": "Images"
    }


def test_load_categories_survives_broken_file(tmp_path: Path) -> None:
    target = tmp_path / "categories.json"
    target.write_text("не json", encoding="utf-8")
    assert load_categories(path=target) == {}


def test_categorized_groups_and_sorts(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Фон")
    make_skill(tmp_path, "steam-image", "Стім")
    make_skill(tmp_path, "develop-web-game", "Гра")
    entries = scan_skills(tmp_path)
    groups = categorized(entries, {"image-cutout": "Images", "steam-image": "Images"})
    assert list(groups) == ["Images", DEFAULT_CATEGORY]
    assert [entry.name for entry in groups["Images"]] == [
        "image-cutout",
        "steam-image",
    ]
    assert [entry.name for entry in groups[DEFAULT_CATEGORY]] == [
        "develop-web-game"
    ]


def test_backup_copies_the_whole_skill(tmp_path: Path) -> None:
    directory = make_skill(tmp_path / "skills", "birds-map", "Карта")
    (directory / "references").mkdir()
    (directory / "references" / "grammar.md").write_text("було", encoding="utf-8")
    entry = scan_skills(tmp_path / "skills")[0]
    backup = backup_skill(entry, root=tmp_path / "backups")
    assert (backup / "SKILL.md").is_file()
    assert (backup / "references" / "grammar.md").read_text(
        encoding="utf-8"
    ) == "було"
    assert list_backups("birds-map", root=tmp_path / "backups") == [backup]


def test_restore_puts_the_old_text_back(tmp_path: Path) -> None:
    directory = make_skill(tmp_path / "skills", "birds-map", "Карта")
    entry = scan_skills(tmp_path / "skills")[0]
    backup = backup_skill(entry, root=tmp_path / "backups")
    (directory / "SKILL.md").write_text("зіпсовано", encoding="utf-8")
    restore_skill(backup, entry)
    restored = (directory / "SKILL.md").read_text(encoding="utf-8")
    assert "зіпсовано" not in restored
    assert "birds-map" in restored


def test_delete_refuses_system_skills(tmp_path: Path) -> None:
    make_skill(tmp_path / ".system", "imagegen", "Картинки")
    entry = scan_skills(tmp_path)[0]
    with pytest.raises(PermissionError):
        delete_skill(entry)
    assert entry.path.is_dir()


def test_import_copies_a_folder(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source", "new-skill", "Новий")
    target_root = tmp_path / "skills"
    target_root.mkdir()
    entry = import_skill(source, root=target_root)
    assert entry.name == "new-skill"
    assert (target_root / "new-skill" / "SKILL.md").is_file()


def test_import_unpacks_a_zip(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source", "zipped-skill", "Із архіву")
    archive = tmp_path / "zipped-skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(source / "SKILL.md", "zipped-skill/SKILL.md")
    target_root = tmp_path / "skills"
    target_root.mkdir()
    entry = import_skill(archive, root=target_root)
    assert entry.name == "zipped-skill"
    assert (target_root / "zipped-skill" / "SKILL.md").is_file()


def test_import_refuses_a_folder_without_skill_md(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target_root = tmp_path / "skills"
    target_root.mkdir()
    with pytest.raises(ValueError, match="SKILL.md"):
        import_skill(source, root=target_root)


def test_import_refuses_to_overwrite(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source", "birds-map", "Нова версія")
    target_root = tmp_path / "skills"
    make_skill(target_root, "birds-map", "Стара версія")
    with pytest.raises(FileExistsError):
        import_skill(source, root=target_root)


def test_skills_used_reads_command_steps(tmp_path: Path) -> None:
    make_skill(tmp_path, "birds-map", "Карта")
    make_skill(tmp_path, "image-cutout", "Фон")
    steps = [
        {
            "kind": "commandExecution",
            "summary": "powershell",
            "detail": {
                "command": (
                    "powershell.exe -Command \"Get-Content -LiteralPath "
                    f"'{tmp_path}\\birds-map\\SKILL.md' -Raw\""
                )
            },
        },
        {
            "kind": "commandExecution",
            "summary": "powershell",
            "detail": {
                "command": (
                    f"Get-Content '{tmp_path}\\birds-map\\references"
                    "\\grammar.md'"
                )
            },
        },
        {"kind": "agentMessage", "summary": "Пишу код", "detail": {}},
    ]
    assert skills_used(steps, root=tmp_path) == ["birds-map"]


def test_skills_used_is_case_insensitive_and_slash_agnostic(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Фон")
    steps = [
        {
            "kind": "fileRead",
            "summary": "",
            "detail": {
                "path": str(tmp_path).upper().replace("\\", "/")
                + "/Image-Cutout/SKILL.md"
            },
        }
    ]
    assert skills_used(steps, root=tmp_path) == ["image-cutout"]


def test_skills_used_returns_empty_for_no_steps(tmp_path: Path) -> None:
    assert skills_used([], root=tmp_path) == []
