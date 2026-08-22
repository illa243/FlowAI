# FlowAI Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Зробити так, щоб Flow калібрувався під задачу за одну ітерацію: Рев'ювер визначає, з якими скілами працював агент, пояснює по пунктах, чому якість гірша за очікувану, і пропонує конкретні правки скілів та промптів — а нова нода зупиняє Flow і показує все це у вікні з diff-ами, нотифікацією Windows і кнопкою Regenerate Prompt.

**Architecture:** Нова нода `calibrator` вішається на порт FALSE блока Result. Коли задача провалює перевірку K-й раз (K налаштовується, за замовчуванням 1), нода робить **другий хід у тому самому Codex-треді Task Reviewer** — той уже пам'ятає задачу, роботу й власний вердикт, тож йому лишається лише проаналізувати скіли та скласти правки. Рушій детермінований у частині фактів: він сам витягує зі кроків агента, які скіли той відкривав, і додає каталог усіх доступних скілів. Результат аналізу зберігається на диск разом із чекпоінтом запуску, показується у вікні на дві вкладки, і після «Застосувати» задача перезапускається з обнуленим лічильником спроб.

**Tech Stack:** Python 3.14, PySide6 6.9+, `openai-codex` SDK (методи `skills/list`, `skills/config/write`, тип входу `SkillInput`), `difflib` зі стандартної бібліотеки, Windows Toast через WinRT `ToastNotificationManager`, pytest.

## Global Constraints

- **`FLOW_FORMAT_VERSION` лишається 2.** `Workflow.from_dict` кидає виняток для будь-якої меншої версії, тож підняття версії зламає наявні `!_projects/*.flowai.json`. Нова нода й нові поля конфігурації додаються без зміни версії: старі файли їх просто не містять, а `_default_config` підставляє значення за замовчуванням.
- **Мова інтерфейсу та коментарів — українська.** Docstring-и та коментарі в коді пишуться українською, як у решті `flowai/`. Ідентифікатори — англійською.
- **Мова технічних назв нод — англійська.** `NODE_LABELS` містить англійські підписи (`"Task Reviewer"`, `"Result"`), нова нода теж.
- **Тести не мають ходити в мережу.** Фікстура `_fake_codex` ставить `FLOWAI_FAKE_CODEX=1`; UI-тести ставлять `QT_QPA_PLATFORM=offscreen` до імпорту PySide6.
- **Ніяких правок у `scripts/` та `assets/` скілів.** Reviewer має право пропонувати зміни лише до `SKILL.md` і `references/*.md`.
- **Скіли зі `scope == "system"` недоторканні** — їх ставить Codex; вимикати можна, видаляти й правити не можна.
- **Жоден запис на диск не відбувається без явного натискання кнопки користувачем.** Перед кожним записом у скіл робиться резервна копія.
- **`ruff check flowai tests` має проходити після кожної задачі.** Довжина рядка — 88 символів (типово для ruff).
- Робоча директорія всіх команд: `C:\Users\illia\Documents\DDA PF\FlowAI`. Python — `.venv\Scripts\python.exe`.

## Рішення, ухвалені під час grilling

| Питання | Рішення |
|---|---|
| Коли нода зупиняє Flow | Налаштовуваний поріг K у самій ноді, за замовчуванням 1 |
| Звідки відомо про скіли | Рушій детектує зі кроків агента + віддає каталог усіх скілів |
| Хто пише рекомендації | Нова нода-агент, що продовжує Codex-тред Task Reviewer |
| Що можна правити | `SKILL.md`, `references/*.md`, промпт задачі, `prompt` і `instructions` ноди |
| Куди лягають правки скіла | У глобальний скіл, по-хунково, із резервною копією |
| Компонування вікна | Одне вікно, дві вкладки |
| Після «Застосувати» | Перезапуск цієї задачі, лічильник спроб обнуляється |
| Обсяг Regenerate Prompt | Завалена задача обов'язково, решта — якщо домовленості їх стосуються |
| «Почати спочатку» і старі файли | Файли не чіпаємо, це лише інструкція в промпті |
| Нотифікація | Справжній Windows-тост із кнопками, відкат на трей-балон |
| Категорії скілів | Власний файл FlowAI + AI-розкладка одним кліком |
| Дії вкладки Skills | Читання, увімкнути/вимкнути, видалити в Кошик, додати з папки/zip та через AI |
| Закріплення скілів | Поле «Скіли» у ноді + кнопка «Закріпити» у вікні правок |
| Що переживає перезапуск | Відхилення + чекпоінт запуску на диску |
| Два лічильники | Калібрація має свій K і перехоплює першою; EXHAUSTED — запобіжник |
| Момент показу | Спочатку аналіз, потім одна нотифікація й повне вікно |

## File Structure

**Нові модулі:**

| Файл | Відповідальність |
|---|---|
| `flowai/skills.py` | Каталог скілів Codex: читання через SDK з відкатом на скан файлової системи, категорії, увімкнення/вимкнення, видалення, імпорт, резервні копії, детекція використаних скілів у кроках агента |
| `flowai/calibration.py` | Модель звіту калібрації: пункти відхилення, картинки, пропоновані правки; JSON-схема для агента, толерантний розбір, збереження й читання з диска |
| `flowai/ui/diff_view.py` | Віджет split-diff у стилі SVN/GitHub: зліва оригінал, справа зміни, підсвітка по рядках і словах, галочка на кожен хунк |
| `flowai/ui/calibration_dialog.py` | Вікно на дві вкладки: «Чому відхилено» і «Пропоновані правки» |
| `flowai/ui/skills_page.py` | Вкладка Skills у налаштуваннях: секції за категоріями, перегляд, вимкнення, видалення, імпорт |
| `flowai/ui/toast.py` | Windows-тост через WinRT із кнопками й відкатом на `QSystemTrayIcon` |
| `tests/test_skills.py` | Тести каталогу скілів |
| `tests/test_calibration.py` | Тести моделі звіту та рушія калібрації |
| `tests/test_calibration_ui.py` | Тести вікна калібрації та diff-віджета |
| `tests/test_skills_ui.py` | Тести вкладки Skills |

**Модифіковані файли:**

| Файл | Що змінюється |
|---|---|
| `flowai/models.py` | Нода `calibrator`: підпис, колір, порти, конфігурація, валідація; поле `skills` в агентських нодах |
| `flowai/codex_adapter.py` | `SkillInput` у `_build_input`, обгортки `list_skills` та `set_skill_enabled` навколо `client.request` |
| `flowai/engine.py` | `_execute_calibrator`, лічильник `calibration_attempts`, `thread_source`, детекція скілів у протоколі, виправлення початкової черги |
| `flowai/run_history.py` | `save_checkpoint` / `load_checkpoint` / `find_pending_run` |
| `flowai/ui/inspector.py` | Поле «Скіли», поля ноди калібрації, попередження про недосяжний EXHAUSTED |
| `flowai/ui/canvas.py` | Малювання ноди без вихідних портів |
| `flowai/ui/main_window.py` | Вкладки в налаштуваннях, відкриття вікна калібрації, нотифікація, збереження й відновлення чекпоінта |
| `flowai/grill.py` | Контекст калібрації, обов'язкове перше питання |
| `flowai/ui/grill_dialog.py` | Прийом контексту калібрації, вибір моделі й складності |
| `flowai/mcp/schema.py` | Опис нової ноди для MCP |
| `FLOWAI_NODE_GUIDE.md` | Розділ про ноду калібрації та поле «Скіли» |
| `install.ps1` | Ярлик у Меню «Пуск» із `System.AppUserModel.ID` |

---

## Фаза 0 — Каталог скілів

Ця фаза корисна сама по собі: після неї у вас є вкладка Skills, навіть якщо решту плану не виконано.

### Task 1: Читання каталогу скілів

**Files:**
- Create: `flowai/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: нічого
- Produces:
  - `SKILLS_ROOT: Path` — `Path.home() / ".codex" / "skills"`
  - `FLOWAI_DIR: Path`, `CATEGORIES_FILE: Path`, `BACKUPS_DIR: Path`
  - `DEFAULT_CATEGORY: str = "Без категорії"`
  - `class SkillEntry` — поля `name: str`, `description: str`, `path: Path`, `scope: str = "user"`, `enabled: bool = True`, `error: str = ""`; властивості `skill_file -> Path`, `reference_files -> list[Path]`, `markdown_files -> list[Path]`, `editable -> bool`
  - `read_frontmatter(text: str) -> tuple[str, str]`
  - `scan_skills(root: Path = SKILLS_ROOT) -> list[SkillEntry]`
  - `catalogue_text(entries: list[SkillEntry]) -> str`

- [ ] **Step 1: Написати падаючий тест розбору frontmatter і скану папки**

Створити `tests/test_skills.py`:

```python
from __future__ import annotations

from pathlib import Path

from flowai.skills import (
    DEFAULT_CATEGORY,
    SkillEntry,
    catalogue_text,
    read_frontmatter,
    scan_skills,
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
    assert [path.name for path in entry.markdown_files] == [
        "SKILL.md",
        "grammar.md",
    ]


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
```

- [ ] **Step 2: Запустити тест і переконатись, що він падає**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills.py -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.skills'`.

- [ ] **Step 3: Написати `flowai/skills.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_ROOT = Path.home() / ".codex" / "skills"
FLOWAI_DIR = SKILLS_ROOT / ".flowai"
CATEGORIES_FILE = FLOWAI_DIR / "categories.json"
BACKUPS_DIR = FLOWAI_DIR / "backups"
DEFAULT_CATEGORY = "Без категорії"
SYSTEM_FOLDER = ".system"

# Скільки символів опису лишати в каталозі, який їде в промпт агента.
DESCRIPTION_LIMIT = 240

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_frontmatter(text: str) -> tuple[str, str]:
    """Витягти `name` і `description` із YAML-заголовка SKILL.md.

    Повний YAML-парсер тут зайвий: у скілах ці два поля завжди однорядкові,
    а тягнути залежність заради двох рядків не варто.
    """
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
    """Прочитати скіли з диска — відкат на випадок, коли SDK не відповідає."""
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
    """Каталог скілів одним рядком на скіл — це їде в промпт Рев'юера."""
    lines: list[str] = []
    for entry in entries:
        if not entry.enabled or entry.error:
            continue
        description = entry.description.strip().replace("\n", " ")
        if len(description) > DESCRIPTION_LIMIT:
            description = description[:DESCRIPTION_LIMIT] + "…"
        lines.append(f"- `{entry.name}` — {description}")
    return "\n".join(lines)
```

- [ ] **Step 4: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills.py -v
```

Очікується: 9 passed.

- [ ] **Step 5: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/skills.py tests/test_skills.py && git commit -m "feat(skills): read the Codex skill catalogue from disk"
```

---

### Task 2: Каталог скілів через SDK та категорії

**Files:**
- Modify: `flowai/skills.py`
- Modify: `flowai/codex_adapter.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `SkillEntry`, `scan_skills` із Task 1; `CodexAdapter` із `flowai/codex_adapter.py`
- Produces:
  - `CodexAdapter.list_skills(cwd: Path | None = None) -> list[dict[str, Any]]` — сирі записи `SkillMetadata` як словники; порожній список, якщо метод недоступний
  - `CodexAdapter.set_skill_enabled(name: str, enabled: bool) -> bool`
  - `flowai.skills.list_skills(codex: Any | None = None, root: Path = SKILLS_ROOT) -> list[SkillEntry]`
  - `flowai.skills.load_categories(path: Path = CATEGORIES_FILE) -> dict[str, str]`
  - `flowai.skills.save_categories(mapping: dict[str, str], path: Path = CATEGORIES_FILE) -> None`
  - `flowai.skills.categorized(entries: list[SkillEntry], mapping: dict[str, str]) -> dict[str, list[SkillEntry]]`

- [ ] **Step 1: Написати падаючі тести**

Додати в `tests/test_skills.py`:

```python
import json

import pytest

from flowai.codex_adapter import CodexAdapter
from flowai.skills import (
    categorized,
    list_skills,
    load_categories,
    save_categories,
)


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
    groups = categorized(
        entries, {"image-cutout": "Images", "steam-image": "Images"}
    )
    assert list(groups) == ["Images", DEFAULT_CATEGORY]
    assert [entry.name for entry in groups["Images"]] == [
        "image-cutout",
        "steam-image",
    ]
    assert [entry.name for entry in groups[DEFAULT_CATEGORY]] == [
        "develop-web-game"
    ]
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills.py -v
```

Очікується: `AttributeError: 'CodexAdapter' object has no attribute 'list_skills'` та `ImportError: cannot import name 'list_skills'`.

- [ ] **Step 3: Додати доступ до клієнта й методи скілів у `flowai/codex_adapter.py`**

У `CodexAdapter.__init__` додати поле після `self._module: Any = None`:

```python
        self._client_handle: Any = None
```

У `CodexAdapter.__enter__`, одразу після `self._client.__enter__()`, додати:

```python
        # Низькорівневий клієнт уміє довільні JSON-RPC методи: типізованих
        # обгорток для skills/* у SDK ще немає, а самі методи вже є.
        self._client_handle = getattr(self._client, "_client", None)
```

У `CodexAdapter.__exit__`, поряд зі скиданням `self._client`, додати `self._client_handle = None`.

Додати два методи після `cancel_active`:

```python
    def list_skills(self, cwd: Path | None = None) -> list[dict[str, Any]]:
        """Каталог скілів очима самого Codex.

        Файлова система не знає ні про `enabled`, ні про плагінні скіли,
        тому питаємо SDK. Якщо метод недоступний — повертаємо порожньо,
        і виклик вище відкочується на скан диска.
        """
        handle = self._client_handle
        if handle is None or not hasattr(handle, "request"):
            return []
        params: dict[str, Any] = {}
        if cwd is not None:
            params["cwds"] = [str(cwd)]
        try:
            payload = handle.request("skills/list", params)
        except Exception:  # noqa: BLE001 - старий SDK просто не має методу
            LOGGER.info("SDK не підтримує skills/list — читаємо диск")
            return []
        records: list[dict[str, Any]] = []
        entries = payload.get("data") if isinstance(payload, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            for skill in entry.get("skills") or []:
                if isinstance(skill, dict):
                    records.append(skill)
        return records

    def set_skill_enabled(self, name: str, enabled: bool) -> bool:
        """Увімкнути або вимкнути скіл штатним механізмом Codex."""
        handle = self._client_handle
        if handle is None or not hasattr(handle, "request"):
            return False
        try:
            handle.request(
                "skills/config/write", {"name": name, "enabled": enabled}
            )
        except Exception:  # noqa: BLE001 - показуємо помилку в інтерфейсі
            LOGGER.exception("Не вдалося змінити стан скіла %s", name)
            return False
        return True
```

- [ ] **Step 4: Додати категорії та `list_skills` у `flowai/skills.py`**

Додати імпорти на початок файлу:

```python
import json
import logging
from typing import Any
```

і `LOGGER = logging.getLogger(__name__)` після констант.

Додати функції в кінець файлу:

```python
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
        except Exception:  # noqa: BLE001 - каталог не має валити інтерфейс
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


def save_categories(
    mapping: dict[str, str], path: Path = CATEGORIES_FILE
) -> None:
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
```

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills.py -v
```

Очікується: 17 passed.

- [ ] **Step 6: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/skills.py flowai/codex_adapter.py tests/test_skills.py && git commit -m "feat(skills): read the catalogue through the SDK and group it by category"
```

---

### Task 3: Резервні копії, видалення, імпорт та детекція використаних скілів

**Files:**
- Modify: `flowai/skills.py`
- Modify: `pyproject.toml`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `SkillEntry`, `BACKUPS_DIR` із Task 1
- Produces:
  - `backup_skill(entry: SkillEntry, root: Path = BACKUPS_DIR) -> Path`
  - `list_backups(name: str, root: Path = BACKUPS_DIR) -> list[Path]`
  - `restore_skill(backup: Path, entry: SkillEntry) -> None`
  - `delete_skill(entry: SkillEntry) -> None` — кидає `PermissionError` для `scope == "system"`
  - `import_skill(source: Path, root: Path = SKILLS_ROOT) -> SkillEntry` — приймає папку або `.zip`
  - `skills_used(steps: list[dict[str, Any]], root: Path = SKILLS_ROOT) -> list[str]`

- [ ] **Step 1: Додати `send2trash` у залежності**

У `pyproject.toml`, у список `dependencies`, додати рядок `"send2trash>=1.8",`.

```bash
.venv/Scripts/python.exe -m pip install "send2trash>=1.8"
```

- [ ] **Step 2: Написати падаючі тести**

Додати в `tests/test_skills.py`:

```python
import zipfile

from flowai.skills import (
    backup_skill,
    delete_skill,
    import_skill,
    list_backups,
    restore_skill,
    skills_used,
)


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
    assert "зіпсовано" not in (directory / "SKILL.md").read_text(encoding="utf-8")
    assert "birds-map" in (directory / "SKILL.md").read_text(encoding="utf-8")


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
                    f"'{tmp_path}\\\\birds-map\\\\SKILL.md' -Raw\""
                )
            },
        },
        {
            "kind": "commandExecution",
            "summary": "powershell",
            "detail": {
                "command": (
                    f"Get-Content '{tmp_path}\\\\birds-map\\\\references"
                    "\\\\grammar.md'"
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
```

- [ ] **Step 3: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills.py -v
```

Очікується: `ImportError: cannot import name 'backup_skill'`.

- [ ] **Step 4: Дописати `flowai/skills.py`**

Додати імпорти:

```python
import shutil
import zipfile
from datetime import datetime
```

Додати в кінець файлу:

```python
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
        names = [name for name in bundle.namelist() if not name.endswith("/")]
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


def skills_used(
    steps: list[dict[str, Any]], root: Path = SKILLS_ROOT
) -> list[str]:
    """Які скіли агент справді відкривав під час свого ходу.

    Агент читає скіли звичайним `Get-Content`, тож шлях до них видно
    у кроках протоколу. Це надійніше, ніж питати самого агента.
    """
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
```

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills.py -v
```

Очікується: 26 passed.

- [ ] **Step 6: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/skills.py tests/test_skills.py pyproject.toml && git commit -m "feat(skills): back up, delete, import skills and detect which ones an agent used"
```

---
### Task 4: Вкладка Skills у налаштуваннях

**Files:**
- Create: `flowai/ui/skills_page.py`
- Modify: `flowai/ui/main_window.py:429-521` (`WorkflowSettingsDialog`)
- Test: `tests/test_skills_ui.py`

**Interfaces:**
- Consumes: `flowai.skills.list_skills`, `load_categories`, `save_categories`, `categorized`, `delete_skill`, `import_skill`, `list_backups`, `restore_skill`, `SkillEntry`, `DEFAULT_CATEGORY`
- Produces:
  - `class SkillsPage(QWidget)` — сигнал `changed = Signal()`; методи `refresh() -> None`, `current_entry() -> SkillEntry | None`, `set_category(name: str, category: str) -> None`
  - `WorkflowSettingsDialog` стає вкладковим: `self.tabs: QTabWidget`, вкладки «Flow» і «Skills»

- [ ] **Step 1: Написати падаючий тест**

Створити `tests/test_skills_ui.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget

from flowai.models import Workflow
from flowai.skills import DEFAULT_CATEGORY, scan_skills
from flowai.ui.main_window import WorkflowSettingsDialog
from flowai.ui.skills_page import SkillsPage


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_skill(root: Path, name: str, description: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return directory


def test_page_groups_skills_into_sections(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Прибирає фон")
    make_skill(tmp_path, "develop-web-game", "Робить гру")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.set_category("image-cutout", "Images")
    page.refresh()
    headings = [
        page.tree.topLevelItem(index).text(0)
        for index in range(page.tree.topLevelItemCount())
    ]
    assert headings == ["Images", DEFAULT_CATEGORY]


def test_page_shows_the_skill_text_when_selected(tmp_path: Path) -> None:
    make_skill(tmp_path, "birds-map", "Розкладає карту")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(section.child(0))
    assert "birds-map" in page.viewer.toPlainText()
    assert page.current_entry() is not None
    assert page.current_entry().name == "birds-map"


def test_page_refuses_to_delete_a_system_skill(tmp_path: Path) -> None:
    make_skill(tmp_path / ".system", "imagegen", "Картинки")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(section.child(0))
    assert page.delete_button.isEnabled() is False


def test_page_enables_delete_for_a_user_skill(tmp_path: Path) -> None:
    make_skill(tmp_path, "birds-map", "Карта")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(section.child(0))
    assert page.delete_button.isEnabled() is True


def test_category_is_persisted(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Фон")
    target = tmp_path / "cat.json"
    page = SkillsPage(root=tmp_path, categories_path=target)
    page.set_category("image-cutout", "Images")
    assert "Images" in target.read_text(encoding="utf-8")


def test_settings_dialog_has_two_tabs(tmp_path: Path) -> None:
    dialog = WorkflowSettingsDialog(Workflow(name="Тест"))
    assert isinstance(dialog.tabs, QTabWidget)
    assert [
        dialog.tabs.tabText(index) for index in range(dialog.tabs.count())
    ] == ["Flow", "Skills"]


def test_settings_dialog_still_saves_the_workflow_name() -> None:
    dialog = WorkflowSettingsDialog(Workflow(name="Тест"))
    dialog.name_edit.setText("Нова назва")
    assert dialog.name_edit.text() == "Нова назва"
    assert dialog.additional_folders() == []


def test_broken_skill_is_shown_with_its_error(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir(parents=True)
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    child = section.child(0)
    assert "SKILL.md" in child.toolTip(0)
    assert scan_skills(tmp_path)[0].error
```

- [ ] **Step 2: Запустити тест і переконатись, що він падає**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills_ui.py -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.ui.skills_page'`.

- [ ] **Step 3: Написати `flowai/ui/skills_page.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..skills import (
    CATEGORIES_FILE,
    DEFAULT_CATEGORY,
    SKILLS_ROOT,
    SkillEntry,
    categorized,
    delete_skill,
    import_skill,
    list_backups,
    list_skills,
    load_categories,
    restore_skill,
    save_categories,
)
from .controls import AnimatedButton
from .design import COLORS
from .paths import path_menu


class SkillsPage(QWidget):
    """Каталог скілів Codex: читання, категорії, вимкнення, видалення."""

    changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        root: Path = SKILLS_ROOT,
        categories_path: Path = CATEGORIES_FILE,
        codex: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.categories_path = categories_path
        self.codex = codex
        self.entries: list[SkillEntry] = []

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Скіли лежать у "
            f"{root}. Категорії зберігає FlowAI окремо — файли скілів "
            "не змінюються."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setObjectName("skillsTree")
        self.tree.setHeaderLabels(["Скіл", "Опис"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.file_picker = QComboBox()
        self.file_picker.currentIndexChanged.connect(self._show_selected_file)
        right_layout.addWidget(self.file_picker)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setFont(QFont("Cascadia Mono", 10))
        right_layout.addWidget(self.viewer, 1)
        splitter.addWidget(right)
        splitter.setSizes([320, 520])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.category_button = AnimatedButton("Категорія…", "secondary")
        self.category_button.clicked.connect(self._ask_category)
        self.enable_button = AnimatedButton("Вимкнути", "secondary")
        self.enable_button.clicked.connect(self._toggle_enabled)
        self.import_button = AnimatedButton("Додати з папки/zip", "secondary")
        self.import_button.clicked.connect(self._import)
        self.restore_button = AnimatedButton("Повернути копію", "ghost")
        self.restore_button.clicked.connect(self._restore)
        self.delete_button = AnimatedButton("Видалити", "ghost")
        self.delete_button.clicked.connect(self._delete)
        for button in (
            self.category_button,
            self.enable_button,
            self.import_button,
            self.restore_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------
    # Дані
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Перечитати каталог і перебудувати дерево, зберігши вибір."""
        selected = self.current_entry()
        wanted = selected.name if selected is not None else ""
        self.entries = list_skills(self.codex, root=self.root)
        groups = categorized(self.entries, load_categories(self.categories_path))
        self.tree.clear()
        restore_item: QTreeWidgetItem | None = None
        for category, items in groups.items():
            heading = QTreeWidgetItem([category, ""])
            font = heading.font(0)
            font.setBold(True)
            heading.setFont(0, font)
            heading.setForeground(0, QColor(COLORS["accent"]))
            self.tree.addTopLevelItem(heading)
            for entry in items:
                label = entry.name if entry.enabled else f"{entry.name} (вимкнено)"
                child = QTreeWidgetItem([label, entry.description])
                child.setData(0, Qt.ItemDataRole.UserRole, entry.name)
                child.setToolTip(0, entry.error or str(entry.path))
                if entry.error:
                    child.setForeground(0, QColor(COLORS["danger"]))
                elif not entry.enabled:
                    child.setForeground(0, QColor(COLORS["text_dim"]))
                heading.addChild(child)
                if entry.name == wanted:
                    restore_item = child
            heading.setExpanded(True)
        if restore_item is not None:
            self.tree.setCurrentItem(restore_item)
        self._update_buttons()

    def current_entry(self) -> SkillEntry | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        name = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        return next(
            (entry for entry in self.entries if entry.name == name), None
        )

    def set_category(self, name: str, category: str) -> None:
        mapping = load_categories(self.categories_path)
        if category.strip():
            mapping[name] = category.strip()
        else:
            mapping.pop(name, None)
        save_categories(mapping, self.categories_path)
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------
    # Реакції
    # ------------------------------------------------------------------

    def _selection_changed(self, *args: object) -> None:
        entry = self.current_entry()
        self.file_picker.blockSignals(True)
        self.file_picker.clear()
        if entry is not None:
            for path in entry.markdown_files:
                self.file_picker.addItem(path.name, str(path))
        self.file_picker.blockSignals(False)
        self._show_selected_file()
        self._update_buttons()

    def _show_selected_file(self, *args: object) -> None:
        path = str(self.file_picker.currentData() or "")
        if not path:
            entry = self.current_entry()
            self.viewer.setPlainText(entry.error if entry else "")
            return
        try:
            self.viewer.setPlainText(
                Path(path).read_text(encoding="utf-8-sig", errors="replace")
            )
        except OSError as exc:
            self.viewer.setPlainText(str(exc))

    def _update_buttons(self) -> None:
        entry = self.current_entry()
        editable = entry is not None and entry.editable
        self.delete_button.setEnabled(bool(editable))
        self.category_button.setEnabled(entry is not None)
        self.enable_button.setEnabled(entry is not None)
        self.restore_button.setEnabled(
            entry is not None and bool(list_backups(entry.name))
        )
        if entry is not None:
            self.enable_button.setText(
                "Вимкнути" if entry.enabled else "Увімкнути"
            )

    def _ask_category(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        known = sorted(
            {value for value in load_categories(self.categories_path).values()}
        )
        current = entry.category if entry.category != DEFAULT_CATEGORY else ""
        name, accepted = QInputDialog.getItem(
            self,
            "Категорія скіла",
            f"У яку секцію віднести «{entry.name}»?",
            known or ["Images", "Code", "Design", "Data"],
            known.index(current) if current in known else 0,
            True,
        )
        if accepted:
            self.set_category(entry.name, name)

    def _toggle_enabled(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        if self.codex is None or not self.codex.set_skill_enabled(
            entry.name, not entry.enabled
        ):
            QMessageBox.information(
                self,
                "Codex недоступний",
                "Змінити стан скіла можна лише при активному з'єднанні "
                "з Codex. Запустіть будь-який Flow і спробуйте знову.",
            )
            return
        self.refresh()
        self.changed.emit()

    def _import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Архів скіла", str(Path.home()), "Архіви (*.zip)"
        )
        if not path:
            path = QFileDialog.getExistingDirectory(
                self, "Папка скіла", str(Path.home())
            )
        if not path:
            return
        try:
            entry = import_skill(Path(path), root=self.root)
        except (OSError, ValueError, FileExistsError) as exc:
            QMessageBox.warning(self, "Не вдалося додати скіл", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(
            self, "Скіл додано", f"«{entry.name}» тепер у каталозі"
        )

    def _restore(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        backups = list_backups(entry.name)
        if not backups:
            return
        names = [path.name for path in backups]
        name, accepted = QInputDialog.getItem(
            self,
            "Повернути копію",
            f"Яку копію «{entry.name}» відновити?",
            names,
            len(names) - 1,
            False,
        )
        if not accepted:
            return
        restore_skill(backups[names.index(name)], entry)
        self.refresh()
        self.changed.emit()

    def _delete(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Видалити скіл?",
            f"Папка «{entry.path}» піде в Кошик Windows.\n"
            "Скіл зникне з усіх ваших проєктів і з Codex CLI.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_skill(entry)
        except (OSError, PermissionError) as exc:
            QMessageBox.warning(self, "Не вдалося видалити", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def _context_menu(self, position: Any) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        path_menu(str(entry.skill_file), self).exec(
            self.tree.viewport().mapToGlobal(position)
        )
```

- [ ] **Step 4: Перебудувати `WorkflowSettingsDialog` на вкладки**

У `flowai/ui/main_window.py` додати імпорти: `QTabWidget` у список із `PySide6.QtWidgets` і `from .skills_page import SkillsPage`.

У `WorkflowSettingsDialog.__init__` замінити фінальні рядки:

```python
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
```

на:

```python
        flow_tab = QWidget()
        flow_layout = QVBoxLayout(flow_tab)
        flow_layout.setContentsMargins(0, 0, 0, 0)
        flow_layout.addLayout(form)
        flow_layout.addStretch()

        self.skills_page = SkillsPage(self, codex=codex)
        self.tabs = QTabWidget()
        self.tabs.addTab(flow_tab, "Flow")
        self.tabs.addTab(self.skills_page, "Skills")

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(buttons)
        self.setMinimumSize(880, 620)
```

Розширити підпис конструктора:

```python
    def __init__(
        self,
        workflow: Workflow,
        parent: QWidget | None = None,
        *,
        codex: Any | None = None,
    ) -> None:
```

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_skills_ui.py -v
```

Очікується: 8 passed.

- [ ] **Step 6: Прогнати весь набір, лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m pytest -q
```

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/skills_page.py flowai/ui/main_window.py tests/test_skills_ui.py && git commit -m "feat(ui): add a Skills tab to the settings dialog"
```

---

## Фаза 1 — Закріплення скілів за нодою

Фаза дає користь одразу: агент перестає витрачати 5-10 кроків на пошук скіла.

### Task 5: Поле `skills` у конфігурації ноди та `SkillInput` у адаптері

**Files:**
- Modify: `flowai/models.py:65-82` (`_agent_defaults`)
- Modify: `flowai/codex_adapter.py` (`_build_input`, `run_agent`)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `flowai.skills.SkillEntry`
- Produces:
  - `_agent_defaults` містить `"skills": []` — список словників `{"name": str, "path": str}`
  - `CodexAdapter.run_agent(..., skills: list[dict[str, str]] | None = None)`
  - `CodexAdapter._build_input(prompt, attachments, skills)` додає `SkillInput` перед текстом

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_agent_defaults_carry_an_empty_skill_list() -> None:
    node = FlowNode.create("executor")
    assert node.config["skills"] == []


def test_old_flow_files_get_the_skill_field(tmp_path: Path) -> None:
    raw = {
        "format_version": 2,
        "name": "Старий",
        "nodes": [
            {"id": "a" * 32, "kind": "executor", "title": "Виконавець", "config": {}}
        ],
        "edges": [],
    }
    workflow = Workflow.from_dict(raw)
    assert workflow.nodes[0].config["skills"] == []


def test_build_input_prepends_pinned_skills() -> None:
    import openai_codex

    adapter = codex_adapter.CodexAdapter()
    adapter._module = openai_codex
    items = adapter._build_input(
        "текст",
        [],
        [{"name": "image-cutout", "path": "C:/skills/image-cutout"}],
    )
    assert isinstance(items, list)
    assert type(items[0]).__name__ == "SkillInput"
    assert items[0].name == "image-cutout"
    assert type(items[-1]).__name__ == "TextInput"


def test_build_input_without_skills_is_a_plain_string() -> None:
    import openai_codex

    adapter = codex_adapter.CodexAdapter()
    adapter._module = openai_codex
    assert adapter._build_input("текст", [], []) == "текст"


def test_fake_run_records_pinned_skills(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.executor.config["skills"] = [
        {"name": "birds-map", "path": str(tmp_path / "birds-map")}
    ]
    pipeline.run()
    call = next(
        item
        for item in codex_adapter.FAKE_CALLS
        if item["model"] == "executor-model"
    )
    assert call["skills"] == ["birds-map"]
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_core.py -k "skill" -v
```

Очікується: `KeyError: 'skills'`.

- [ ] **Step 3: Додати поле в `flowai/models.py`**

У `_agent_defaults`, у словник `base`, після `"attachments": [],` додати:

```python
        "skills": [],
```

- [ ] **Step 4: Прокинути скіли через `flowai/codex_adapter.py`**

Замінити `_build_input`:

```python
    def _build_input(
        self,
        prompt: str,
        attachments: list[Path],
        skills: list[dict[str, str]] | None = None,
    ) -> Any:
        """Зібрати мультимодальний вхід; якщо SDK не вміє — лишити рядок.

        Закріплені скіли йдуть першими: Codex завантажує їх сам, і агент
        не витрачає кроки на пошук та читання SKILL.md вручну.
        """
        text_input = getattr(self._module, "TextInput", None)
        image_input = getattr(self._module, "LocalImageInput", None)
        skill_input = getattr(self._module, "SkillInput", None)
        if text_input is None:
            return prompt
        items: list[Any] = []
        if skill_input is not None:
            for skill in skills or []:
                name = str(skill.get("name", "")).strip()
                path = str(skill.get("path", "")).strip()
                if name and path:
                    items.append(skill_input(name=name, path=path))
        items.append(text_input(prompt))
        if image_input is not None:
            for path in attachments:
                if path.suffix.casefold() in IMAGE_SUFFIXES and path.is_file():
                    items.append(image_input(str(path)))
        if len(items) == 1:
            return prompt
        return items
```

У `run_agent` додати параметр `skills: list[dict[str, str]] | None = None` після `attachments`, передати його у фейковий прогін і в `_build_input`:

```python
        if os.environ.get("FLOWAI_FAKE_CODEX") == "1":
            return self._fake_run(
                prompt=prompt,
                model=model,
                reasoning_effort=reasoning_effort,
                attachments=attachments,
                resume_thread_id=resume_thread_id,
                on_activity=on_activity,
                skills=list(skills or []),
            )
```

```python
        run_input = self._build_input(prompt, attachments, list(skills or []))
```

У `_fake_run` додати параметр `skills: list[dict[str, str]]` і записати його в `call`:

```python
            "skills": [str(item.get("name", "")) for item in skills],
```

- [ ] **Step 5: Передати скіли з рушія**

У `flowai/engine.py`, у виклику `codex.run_agent` всередині `_execute_agent`, після `attachments=attachments,` додати:

```python
            skills=[
                {"name": str(item.get("name", "")), "path": str(item.get("path", ""))}
                for item in node.config.get("skills", [])
                if isinstance(item, dict)
            ],
```

- [ ] **Step 6: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_core.py -v
```

Очікується: усі тести проходять, зокрема 5 нових.

- [ ] **Step 7: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/models.py flowai/codex_adapter.py flowai/engine.py tests/test_core.py && git commit -m "feat(engine): pin skills to an agent node through SkillInput"
```

---

### Task 6: Редактор скілів у Інспекторі

**Files:**
- Modify: `flowai/ui/inspector.py:43-76` (`AGENT_FIELDS`, `KIND_FIELDS`), `_build_node_page`, `_load_node`, `_save_node`
- Test: `tests/test_workspaces_ui.py`

**Interfaces:**
- Consumes: `flowai.skills.list_skills`
- Produces:
  - `class SkillPinWidget(QWidget)` у `flowai/ui/inspector.py` — сигнал `skills_changed = Signal()`; методи `set_skills(values: list[dict[str, str]]) -> None`, `skills() -> list[dict[str, str]]`, `add_skill(name: str, path: str) -> None`
  - `Inspector.skill_pins: SkillPinWidget`
  - `AGENT_FIELDS` містить `"skills"`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_inspector_shows_pinned_skills_for_an_agent_node() -> None:
    window = MainWindow()
    node = FlowNode.create("executor")
    node.config["skills"] = [{"name": "birds-map", "path": "C:/s/birds-map"}]
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    assert window.inspector.skill_pins.skills() == [
        {"name": "birds-map", "path": "C:/s/birds-map"}
    ]
    window.close()


def test_inspector_saves_a_newly_pinned_skill() -> None:
    window = MainWindow()
    node = FlowNode.create("executor")
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    window.inspector.skill_pins.add_skill("image-cutout", "C:/s/image-cutout")
    assert node.config["skills"] == [
        {"name": "image-cutout", "path": "C:/s/image-cutout"}
    ]
    window.close()


def test_inspector_does_not_pin_the_same_skill_twice() -> None:
    window = MainWindow()
    node = FlowNode.create("executor")
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    window.inspector.skill_pins.add_skill("image-cutout", "C:/s/image-cutout")
    window.inspector.skill_pins.add_skill("image-cutout", "C:/s/image-cutout")
    assert len(node.config["skills"]) == 1
    window.close()


def test_result_node_has_no_skill_field() -> None:
    window = MainWindow()
    node = FlowNode.create("result")
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    assert window.inspector.skill_pins.isVisible() is False
    window.close()
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_workspaces_ui.py -k "skill" -v
```

Очікується: `AttributeError: 'Inspector' object has no attribute 'skill_pins'`.

- [ ] **Step 3: Додати віджет у `flowai/ui/inspector.py`**

Додати імпорт `from ..skills import list_skills` і клас перед `class Inspector`:

```python
class SkillPinWidget(QWidget):
    """Скіли, закріплені за нодою: Codex завантажить їх до першого кроку."""

    skills_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[dict[str, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.list = QListWidget()
        self.list.setMaximumHeight(96)
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        add = QPushButton("Закріпити скіл")
        remove = QPushButton("Прибрати")
        add.clicked.connect(self._choose)
        remove.clicked.connect(self._remove)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        layout.addLayout(buttons)

    def set_skills(self, values: list[dict[str, str]]) -> None:
        self._values = [
            {"name": str(item.get("name", "")), "path": str(item.get("path", ""))}
            for item in values
            if isinstance(item, dict) and str(item.get("name", ""))
        ]
        self._render()

    def skills(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._values]

    def add_skill(self, name: str, path: str) -> None:
        """Закріпити скіл; повторний виклик із тим самим іменем нічого не робить."""
        if not name or any(item["name"] == name for item in self._values):
            return
        self._values.append({"name": name, "path": path})
        self._render()
        self.skills_changed.emit()

    def _render(self) -> None:
        self.list.clear()
        for item in self._values:
            row = QListWidgetItem(item["name"])
            row.setToolTip(item["path"])
            self.list.addItem(row)

    def _choose(self) -> None:
        entries = [entry for entry in list_skills(None) if entry.enabled]
        if not entries:
            QMessageBox.information(
                self,
                "Скілів не знайдено",
                "У ~/.codex/skills немає жодного скіла.",
            )
            return
        names = [f"{entry.name} — {entry.description[:70]}" for entry in entries]
        choice, accepted = QInputDialog.getItem(
            self, "Закріпити скіл", "Який скіл має завантажити агент?", names, 0, False
        )
        if not accepted:
            return
        entry = entries[names.index(choice)]
        self.add_skill(entry.name, str(entry.path))

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        self._values.pop(row)
        self._render()
        self.skills_changed.emit()
```

Додати імпорти `QInputDialog`, `QMessageBox`, `QHBoxLayout` (якщо їх ще немає) у список із `PySide6.QtWidgets`.

- [ ] **Step 4: Підключити віджет до форми**

У `AGENT_FIELDS` додати `"skills",`.

У `_build_node_page`, після рядка з `self.attachments`, додати:

```python
        self.skill_pins = SkillPinWidget()
        self.node_form.addRow("Скіли", self.skill_pins)
```

У словник `fields` (той, що будує `self.node_rows`) додати `"skills": self.skill_pins,`.

У блок підключень сигналів додати:

```python
        self.skill_pins.skills_changed.connect(self._save_node)
```

У `_load_node`, у гілці агентських нод, додати:

```python
        self.skill_pins.set_skills(node.config.get("skills", []))
```

У `_save_node`, у словник `node.config.update({...})` для агентських нод, додати:

```python
                    "skills": self.skill_pins.skills(),
```

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_workspaces_ui.py -v
```

Очікується: усі проходять, зокрема 4 нових.

- [ ] **Step 6: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/inspector.py tests/test_workspaces_ui.py && git commit -m "feat(ui): pin skills to a node from the Inspector"
```

---
## Фаза 2 — Нода калібрації та рушій

### Task 7: Модель звіту калібрації

**Files:**
- Create: `flowai/calibration.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: нічого
- Produces:
  - `CALIBRATION_FILE: str = "calibration.json"`
  - `CALIBRATION_SCHEMA: dict[str, Any]` — схема відповіді агента
  - `EDIT_TARGETS: frozenset[str]` = `{"skill_file", "task_prompt", "node_prompt", "node_instructions"}`
  - `class RejectionImage` — `path: str`, `note: str`
  - `class RejectionPoint` — `title: str`, `detail: str`, `images: list[RejectionImage]`, `user_note: str = ""`
  - `class ProposedEdit` — `target: str`, `label: str`, `rationale: str`, `before: str`, `after: str`, `path: str = ""`, `node_id: str = ""`, `task_id: str = ""`, `skill: str = ""`, `accepted: bool = True`; властивість `display_path -> str`
  - `class CalibrationReport` — усі поля нижче + `to_dict()` / `from_dict()`
  - `parse_report(payload: Any, *, node_id, node_title, task_id, task_title, workflow_name, attempt, threshold, reason, must_fix, skills_used) -> CalibrationReport`
  - `save_report(report: CalibrationReport, directory: Path) -> Path`
  - `load_report(directory: Path) -> CalibrationReport | None`

- [ ] **Step 1: Написати падаючий тест**

Створити `tests/test_calibration.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from flowai.calibration import (
    CALIBRATION_FILE,
    CALIBRATION_SCHEMA,
    EDIT_TARGETS,
    CalibrationReport,
    ProposedEdit,
    RejectionPoint,
    load_report,
    parse_report,
    save_report,
)

CONTEXT = {
    "node_id": "n1",
    "node_title": "Calibration Stop",
    "task_id": "t1",
    "task_title": "Зробити карту",
    "workflow_name": "Карти",
    "attempt": 1,
    "threshold": 1,
    "reason": "Пропорції поламані",
    "must_fix": ["Вирівняти сітку"],
    "skills_used": ["birds-map"],
}


def test_schema_names_every_edit_target() -> None:
    assert EDIT_TARGETS == {
        "skill_file",
        "task_prompt",
        "node_prompt",
        "node_instructions",
    }
    assert "edits" in CALIBRATION_SCHEMA
    assert "points" in CALIBRATION_SCHEMA


def test_parse_report_reads_a_full_answer() -> None:
    payload = {
        "summary": "Скіл не описує масштаб",
        "root_cause": "У SKILL.md немає правила про сітку",
        "skills_used": ["birds-map"],
        "skills_missing": ["image-cutout"],
        "points": [
            {
                "title": "Сітка з'їхала",
                "detail": "Об'єкти не в вузлах",
                "images": [{"path": "C:/out/map.png", "note": "Ліва частина"}],
            }
        ],
        "edits": [
            {
                "target": "skill_file",
                "path": "C:/skills/birds-map/SKILL.md",
                "skill": "birds-map",
                "label": "Додати правило сітки",
                "rationale": "Інакше агент кладе об'єкти між вузлами",
                "before": "## Композиція\nСтав об'єкти красиво.",
                "after": "## Композиція\nСтав об'єкти рівно у вузли сітки.",
            }
        ],
    }
    report = parse_report(payload, **CONTEXT)
    assert report.summary == "Скіл не описує масштаб"
    assert report.skills_missing == ["image-cutout"]
    assert report.points[0].title == "Сітка з'їхала"
    assert report.points[0].images[0].note == "Ліва частина"
    assert report.points[0].user_note == ""
    assert report.edits[0].target == "skill_file"
    assert report.edits[0].accepted is True
    assert report.edits[0].display_path == "birds-map / SKILL.md"


def test_parse_report_falls_back_to_must_fix_when_points_are_missing() -> None:
    report = parse_report({"summary": "Погано"}, **CONTEXT)
    assert [point.title for point in report.points] == ["Вирівняти сітку"]
    assert report.edits == []


def test_parse_report_drops_edits_with_an_unknown_target() -> None:
    payload = {
        "edits": [
            {"target": "scripts", "before": "a", "after": "b", "label": "ні"},
            {
                "target": "task_prompt",
                "before": "a",
                "after": "b",
                "label": "так",
                "task_id": "t1",
            },
        ]
    }
    report = parse_report(payload, **CONTEXT)
    assert [edit.label for edit in report.edits] == ["так"]


def test_parse_report_drops_edits_that_change_nothing() -> None:
    payload = {
        "edits": [
            {
                "target": "task_prompt",
                "before": "однаково",
                "after": "однаково",
                "label": "порожня правка",
                "task_id": "t1",
            }
        ]
    }
    assert parse_report(payload, **CONTEXT).edits == []


def test_parse_report_survives_a_non_dict_answer() -> None:
    report = parse_report("агент відповів текстом", **CONTEXT)
    assert report.analysis_error
    assert [point.title for point in report.points] == ["Вирівняти сітку"]


def test_report_round_trips_through_disk(tmp_path: Path) -> None:
    report = parse_report(
        {
            "summary": "Стисло",
            "points": [{"title": "Пункт", "detail": "Опис", "images": []}],
            "edits": [
                {
                    "target": "node_instructions",
                    "node_id": "exec-1",
                    "label": "Додати вимогу",
                    "before": "було",
                    "after": "стало",
                }
            ],
        },
        **CONTEXT,
    )
    report.points[0].user_note = "Моє бачення"
    report.edits[0].accepted = False
    path = save_report(report, tmp_path)
    assert path.name == CALIBRATION_FILE
    restored = load_report(tmp_path)
    assert restored is not None
    assert restored.points[0].user_note == "Моє бачення"
    assert restored.edits[0].accepted is False
    assert restored.task_title == "Зробити карту"
    assert json.loads(path.read_text(encoding="utf-8"))["attempt"] == 1


def test_load_report_returns_none_when_nothing_is_saved(tmp_path: Path) -> None:
    assert load_report(tmp_path) is None


def test_load_report_returns_none_for_a_broken_file(tmp_path: Path) -> None:
    (tmp_path / CALIBRATION_FILE).write_text("не json", encoding="utf-8")
    assert load_report(tmp_path) is None


def test_user_notes_text_collects_only_filled_notes() -> None:
    report = CalibrationReport(
        node_id="n1",
        node_title="Stop",
        task_id="t1",
        task_title="Задача",
        workflow_name="Flow",
        attempt=1,
        threshold=1,
        points=[
            RejectionPoint(title="Раз", detail="", user_note="Виправити так"),
            RejectionPoint(title="Два", detail="", user_note="   "),
        ],
    )
    assert report.user_notes_text() == "- Раз: Виправити так"


def test_accepted_edits_filters_by_flag() -> None:
    report = CalibrationReport(
        node_id="n1",
        node_title="Stop",
        task_id="t1",
        task_title="Задача",
        workflow_name="Flow",
        attempt=1,
        threshold=1,
        edits=[
            ProposedEdit(
                target="task_prompt", label="так", before="a", after="b"
            ),
            ProposedEdit(
                target="task_prompt",
                label="ні",
                before="a",
                after="b",
                accepted=False,
            ),
        ],
    )
    assert [edit.label for edit in report.accepted_edits()] == ["так"]
```

- [ ] **Step 2: Запустити тест і переконатись, що він падає**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.calibration'`.

- [ ] **Step 3: Написати `flowai/calibration.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CALIBRATION_FILE = "calibration.json"

EDIT_TARGETS = frozenset(
    {"skill_file", "task_prompt", "node_prompt", "node_instructions"}
)

CALIBRATION_SCHEMA: dict[str, Any] = {
    "summary": "string",
    "root_cause": "string",
    "skills_used": ["string"],
    "skills_missing": ["string"],
    "points": [
        {
            "title": "string",
            "detail": "string",
            "images": [{"path": "string", "note": "string"}],
        }
    ],
    "edits": [
        {
            "target": "skill_file | task_prompt | node_prompt | node_instructions",
            "path": "абсолютний шлях для skill_file",
            "skill": "ім'я скіла для skill_file",
            "node_id": "id ноди для node_prompt і node_instructions",
            "task_id": "id завдання для task_prompt",
            "label": "string",
            "rationale": "string",
            "before": "точний фрагмент, який зараз у файлі",
            "after": "чим його замінити",
        }
    ],
}


@dataclass(slots=True)
class RejectionImage:
    """Картинка, якою рев'ювер ілюструє свій закид."""

    path: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "note": self.note}


@dataclass(slots=True)
class RejectionPoint:
    """Один пункт відхилення разом із баченням користувача."""

    title: str
    detail: str = ""
    images: list[RejectionImage] = field(default_factory=list)
    user_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "detail": self.detail,
            "images": [image.to_dict() for image in self.images],
            "user_note": self.user_note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RejectionPoint:
        images = [
            RejectionImage(
                path=str(item.get("path", "")), note=str(item.get("note", ""))
            )
            for item in raw.get("images") or []
            if isinstance(item, dict) and str(item.get("path", "")).strip()
        ]
        return cls(
            title=str(raw.get("title", "")).strip() or "Без назви",
            detail=str(raw.get("detail", "")),
            images=images,
            user_note=str(raw.get("user_note", "")),
        )


@dataclass(slots=True)
class ProposedEdit:
    """Одна правка: точний фрагмент «було» та його заміна."""

    target: str
    label: str = ""
    rationale: str = ""
    before: str = ""
    after: str = ""
    path: str = ""
    node_id: str = ""
    task_id: str = ""
    skill: str = ""
    accepted: bool = True

    @property
    def display_path(self) -> str:
        """Як цю правку підписати у списку файлів."""
        if self.target == "skill_file":
            name = Path(self.path).name or self.path
            return f"{self.skill} / {name}" if self.skill else name
        if self.target == "task_prompt":
            return "Промпт завдання"
        if self.target == "node_prompt":
            return "Промпт блоку"
        return "Постійні інструкції блоку"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "label": self.label,
            "rationale": self.rationale,
            "before": self.before,
            "after": self.after,
            "path": self.path,
            "node_id": self.node_id,
            "task_id": self.task_id,
            "skill": self.skill,
            "accepted": self.accepted,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProposedEdit:
        return cls(
            target=str(raw.get("target", "")),
            label=str(raw.get("label", "")),
            rationale=str(raw.get("rationale", "")),
            before=str(raw.get("before", "")),
            after=str(raw.get("after", "")),
            path=str(raw.get("path", "")),
            node_id=str(raw.get("node_id", "")),
            task_id=str(raw.get("task_id", "")),
            skill=str(raw.get("skill", "")),
            accepted=bool(raw.get("accepted", True)),
        )


@dataclass
class CalibrationReport:
    """Усе, що показує вікно калібрації, в одному об'єкті."""

    node_id: str
    node_title: str
    task_id: str
    task_title: str
    workflow_name: str
    attempt: int
    threshold: int
    verdict_reason: str = ""
    must_fix: list[str] = field(default_factory=list)
    summary: str = ""
    root_cause: str = ""
    points: list[RejectionPoint] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    skills_missing: list[str] = field(default_factory=list)
    edits: list[ProposedEdit] = field(default_factory=list)
    analysis_error: str = ""

    def accepted_edits(self) -> list[ProposedEdit]:
        return [edit for edit in self.edits if edit.accepted]

    def user_notes_text(self) -> str:
        """Бачення користувача одним блоком — це їде в GrillMe."""
        lines = [
            f"- {point.title}: {point.user_note.strip()}"
            for point in self.points
            if point.user_note.strip()
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_title": self.node_title,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "workflow_name": self.workflow_name,
            "attempt": self.attempt,
            "threshold": self.threshold,
            "verdict_reason": self.verdict_reason,
            "must_fix": list(self.must_fix),
            "summary": self.summary,
            "root_cause": self.root_cause,
            "points": [point.to_dict() for point in self.points],
            "skills_used": list(self.skills_used),
            "skills_missing": list(self.skills_missing),
            "edits": [edit.to_dict() for edit in self.edits],
            "analysis_error": self.analysis_error,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CalibrationReport:
        return cls(
            node_id=str(raw.get("node_id", "")),
            node_title=str(raw.get("node_title", "")),
            task_id=str(raw.get("task_id", "")),
            task_title=str(raw.get("task_title", "")),
            workflow_name=str(raw.get("workflow_name", "")),
            attempt=int(raw.get("attempt", 1)),
            threshold=int(raw.get("threshold", 1)),
            verdict_reason=str(raw.get("verdict_reason", "")),
            must_fix=[str(item) for item in raw.get("must_fix", [])],
            summary=str(raw.get("summary", "")),
            root_cause=str(raw.get("root_cause", "")),
            points=[
                RejectionPoint.from_dict(item)
                for item in raw.get("points", [])
                if isinstance(item, dict)
            ],
            skills_used=[str(item) for item in raw.get("skills_used", [])],
            skills_missing=[str(item) for item in raw.get("skills_missing", [])],
            edits=[
                ProposedEdit.from_dict(item)
                for item in raw.get("edits", [])
                if isinstance(item, dict)
            ],
            analysis_error=str(raw.get("analysis_error", "")),
        )


def _edit_from_payload(raw: dict[str, Any]) -> ProposedEdit | None:
    """Правка проходить, лише якщо ціль відома і текст справді змінюється."""
    target = str(raw.get("target", "")).strip()
    if target not in EDIT_TARGETS:
        return None
    before = str(raw.get("before", ""))
    after = str(raw.get("after", ""))
    if before == after:
        return None
    return ProposedEdit(
        target=target,
        label=str(raw.get("label", "")).strip() or "Правка",
        rationale=str(raw.get("rationale", "")),
        before=before,
        after=after,
        path=str(raw.get("path", "")),
        node_id=str(raw.get("node_id", "")),
        task_id=str(raw.get("task_id", "")),
        skill=str(raw.get("skill", "")),
    )


def parse_report(
    payload: Any,
    *,
    node_id: str,
    node_title: str,
    task_id: str,
    task_title: str,
    workflow_name: str,
    attempt: int,
    threshold: int,
    reason: str,
    must_fix: list[str],
    skills_used: list[str],
) -> CalibrationReport:
    """Скласти звіт із відповіді агента, не даючи їй завалити Flow.

    Якщо агент відповів не за схемою — вікно все одно має відкритися:
    у ньому будуть must_fix від Task Reviewer і позначка про помилку.
    """
    report = CalibrationReport(
        node_id=node_id,
        node_title=node_title,
        task_id=task_id,
        task_title=task_title,
        workflow_name=workflow_name,
        attempt=attempt,
        threshold=threshold,
        verdict_reason=reason,
        must_fix=list(must_fix),
        skills_used=list(skills_used),
    )
    if not isinstance(payload, dict):
        report.analysis_error = (
            "Агент відповів не за схемою — показано лише вердикт рев'ювера"
        )
    else:
        report.summary = str(payload.get("summary", "")).strip()
        report.root_cause = str(payload.get("root_cause", "")).strip()
        detected = [
            str(item) for item in payload.get("skills_used", []) if str(item)
        ]
        for name in detected:
            if name not in report.skills_used:
                report.skills_used.append(name)
        report.skills_missing = [
            str(item) for item in payload.get("skills_missing", []) if str(item)
        ]
        report.points = [
            RejectionPoint.from_dict(item)
            for item in payload.get("points", [])
            if isinstance(item, dict)
        ]
        for item in payload.get("edits", []):
            if not isinstance(item, dict):
                continue
            edit = _edit_from_payload(item)
            if edit is not None:
                report.edits.append(edit)
    if not report.points:
        report.points = [
            RejectionPoint(title=str(item)) for item in must_fix if str(item)
        ]
    if not report.points and reason.strip():
        report.points = [RejectionPoint(title=reason.strip())]
    return report


def save_report(report: CalibrationReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CALIBRATION_FILE
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_report(directory: Path) -> CalibrationReport | None:
    try:
        payload = json.loads(
            (directory / CALIBRATION_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return CalibrationReport.from_dict(payload)
```

- [ ] **Step 4: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -v
```

Очікується: 11 passed.

- [ ] **Step 5: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/calibration.py tests/test_calibration.py && git commit -m "feat(calibration): model the rejection report and proposed edits"
```

---

### Task 8: Нода `calibrator` у моделі Flow

**Files:**
- Modify: `flowai/models.py` (`NODE_LABELS`, `NODE_COLORS`, `AGENT_KINDS`, `_default_config`, `ports_of`, `validate`)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `CALIBRATION_SCHEMA` із `flowai/calibration.py`
- Produces:
  - `NODE_LABELS["calibrator"] = "Calibration Stop"`
  - `NODE_COLORS["calibrator"] = "#E11D48"`
  - `AGENT_KINDS` містить `"calibrator"`
  - `NEVER_SEEDED = frozenset({"calibrator"})`
  - `TERMINAL_KINDS = frozenset({"calibrator"})` — `ports_of` повертає `()`
  - Конфігурація ноди: усі поля `_agent_defaults` плюс `"false_threshold": 1`, `"thread_source": ""`, `"reviewer_node": ""`
  - `Workflow.calibrator_for(result_node_id: str) -> FlowNode | None`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
from flowai.models import NODE_COLORS, NODE_LABELS


def test_calibrator_is_a_registered_node_kind() -> None:
    assert NODE_LABELS["calibrator"] == "Calibration Stop"
    assert NODE_COLORS["calibrator"] == "#E11D48"
    node = FlowNode.create("calibrator")
    assert node.config["false_threshold"] == 1
    assert node.config["sandbox"] == "read-only"
    assert node.config["output_format"] == "json"
    assert node.config["thread_source"] == ""
    assert node.is_agent is True


def test_calibrator_has_no_output_ports(tmp_path: Path) -> None:
    workflow = Workflow()
    node = FlowNode.create("calibrator")
    workflow.nodes.append(node)
    assert workflow.ports_of(node.id) == ()


def test_calibrator_must_hang_on_the_false_port(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "true")
    )
    errors = pipeline.workflow.validate()
    assert any("Calibration Stop" in error and "FALSE" in error for error in errors)


def test_calibrator_on_the_false_port_validates(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "false")
    )
    assert pipeline.workflow.validate() == []
    assert pipeline.workflow.calibrator_for(pipeline.result.id) is stop


def test_only_one_calibrator_per_result(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    for _ in range(2):
        stop = FlowNode.create("calibrator")
        pipeline.workflow.nodes.append(stop)
        pipeline.workflow.edges.append(
            FlowEdge.create(pipeline.result.id, stop.id, "false")
        )
    errors = pipeline.workflow.validate()
    assert any("лише один" in error for error in errors)


def test_calibrator_cannot_be_a_source(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    stop = FlowNode.create("calibrator")
    pipeline.workflow.nodes.append(stop)
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, stop.id, "false")
    )
    pipeline.workflow.edges.append(
        FlowEdge.create(stop.id, pipeline.executor.id, "out")
    )
    errors = pipeline.workflow.validate()
    assert any("не має вихідних портів" in error for error in errors)
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_core.py -k "calibrator" -v
```

Очікується: `UnsupportedFlowFormat: Невідомий тип ноди: calibrator`.

- [ ] **Step 3: Зареєструвати ноду в `flowai/models.py`**

Додати імпорт на початок файлу:

```python
from .calibration import CALIBRATION_SCHEMA
```

У `NODE_LABELS` додати `"calibrator": "Calibration Stop",` після `"result"`.
У `NODE_COLORS` додати `"calibrator": "#E11D48",` після `"result"`.

Замінити `AGENT_KINDS` і додати два набори:

```python
# Ноди, які запускають окремий потік Codex.
AGENT_KINDS = frozenset(
    {"prompt_reviewer", "executor", "task_reviewer", "work_reviewer", "calibrator"}
)

# Ноди без портів: не беруть участі в маршруті.
SIDECAR_KINDS = frozenset({"work_reviewer"})

# Ноди з входом, але без виходів: маршрут на них закінчується.
TERMINAL_KINDS = frozenset({"calibrator"})

# Ноди, які ніколи не стартують Flow, навіть якщо їхній єдиний вхід — Result.
NEVER_SEEDED = frozenset({"calibrator"})
```

У `_default_config`, у словник `defaults`, після `"work_reviewer"` додати:

```python
        "calibrator": _agent_defaults(
            instructions=(
                "Ти щойно відхилив роботу виконавця. Тепер поясни, чому "
                "якість гірша за очікувану, і запропонуй конкретні правки. "
                "Розділяй симптом і причину: пункт відхилення описує, що не "
                "так у результаті, а edits міняють те, через що це сталося — "
                "текст скіла або промпт. У before клади ТОЧНИЙ фрагмент, "
                "який зараз є у файлі, інакше правку неможливо застосувати. "
                "Не чіпай scripts/ і assets/ скілів."
            ),
            prompt=(
                "# Скіли, які агент справді відкривав\n{{skills_used}}\n\n"
                "# Каталог доступних скілів\n{{skills_catalogue}}\n\n"
                "# Промпт завдання, яке провалилось\n{{task_prompt}}\n\n"
                "# Постійні інструкції блоку-виконавця\n{{node_instructions}}\n\n"
                "# Промпт блоку-виконавця\n{{node_prompt}}\n\n"
                "# Файли, які створив виконавець\n{{generated_files}}\n\n"
                "Поверни JSON за схемою відповіді."
            ),
            sandbox="read-only",
            output_format="json",
            output_schema=dict(CALIBRATION_SCHEMA),
            memory="thread",
            false_threshold=1,
            thread_source="",
            reviewer_node="",
        ),
```

У `Workflow.ports_of` додати після перевірки `SIDECAR_KINDS`:

```python
        if node.kind in TERMINAL_KINDS:
            return ()
```

Додати метод після `exhausted_target`:

```python
    def calibrator_for(self, node_id: str) -> FlowNode | None:
        """Нода калібрації, підвішена на вихід FALSE вказаного Result."""
        for edge in self.outgoing(node_id, "false"):
            target = self.find(edge.target)
            if target is not None and target.kind == "calibrator":
                return target
        return None
```

У `Workflow.validate`, після блоку перевірки порту `exhausted`, додати:

```python
        for edge in self.edges:
            target = self.find(edge.target)
            if target is None or target.kind != "calibrator":
                continue
            source = self.find(edge.source)
            if (
                source is None
                or source.kind != "result"
                or edge.source_port != "false"
            ):
                errors.append(
                    f"Блок «{target.title}» (Calibration Stop) можна з'єднати "
                    "лише з виходом FALSE блока Result"
                )

        for node in self.nodes_of_kind("result"):
            attached = [
                edge.target
                for edge in self.outgoing(node.id, "false")
                if (found := self.find(edge.target)) is not None
                and found.kind == "calibrator"
            ]
            if len(attached) > 1:
                errors.append(
                    f"До блока «{node.title}» можна підключити "
                    "лише один Calibration Stop"
                )
```

- [ ] **Step 4: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_core.py -v
```

Очікується: усі проходять, зокрема 6 нових.

- [ ] **Step 5: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/models.py tests/test_core.py && git commit -m "feat(models): register the Calibration Stop node kind"
```

---

### Task 9: Виконання ноди калібрації в рушії

**Files:**
- Modify: `flowai/engine.py` (`RunCheckpoint`, `run`, `_execute_node`, `_execute_agent`, новий `_execute_calibrator`)
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `parse_report`, `save_report`, `CalibrationReport` із Task 7; `skills_used`, `catalogue_text`, `list_skills` із Task 3; ноду `calibrator` із Task 8
- Produces:
  - `RunCheckpoint.calibration_attempts: dict[str, int]`
  - `WorkflowRunner._execute_calibrator(node, inputs, context, workspace, codex) -> NodeResult`
  - `WorkflowRunner._upstream_node_of_kind(node_id: str, kind: str) -> FlowNode | None`
  - Запит втручання типу `"calibration"` із ключами `node_id`, `node_title`, `type`, `question`, `report` (словник `CalibrationReport.to_dict()`), `report_path`
  - Відповідь користувача: `{"action": "retry_task"}`, `{"action": "continue"}` або `{"action": "stop"}`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_calibration.py`:

```python
import json as _json

import pytest

from flowai import codex_adapter
from flowai.engine import InterventionRequired, WorkflowRunner
from flowai.models import FlowEdge, FlowNode, Workflow


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", None)


def build_flow(workspace: Path) -> tuple[Workflow, dict[str, FlowNode]]:
    workflow = Workflow(name="Калібрація", workspace=str(workspace))
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "task-1", "prompt": "Зробити карту", "attachments": []}
    ]
    executor = FlowNode.create("executor")
    executor.config["model"] = "executor-model"
    reviewer = FlowNode.create("task_reviewer")
    reviewer.config["model"] = "reviewer-model"
    result = FlowNode.create("result")
    stop = FlowNode.create("calibrator")
    stop.config["model"] = "calibrator-model"
    workflow.nodes.extend([manager, executor, reviewer, result, stop])
    workflow.edges.extend(
        [
            FlowEdge.create(manager.id, executor.id, "next"),
            FlowEdge.create(executor.id, reviewer.id),
            FlowEdge.create(reviewer.id, result.id),
            FlowEdge.create(result.id, manager.id, "true"),
            FlowEdge.create(result.id, executor.id, "false"),
            FlowEdge.create(result.id, stop.id, "false"),
        ]
    )
    for edge in workflow.edges:
        if edge.source == reviewer.id:
            edge.source_path = "data"
            edge.target_variable = "review"
    return workflow, {
        "manager": manager,
        "executor": executor,
        "reviewer": reviewer,
        "result": result,
        "stop": stop,
    }


def rejecting_responder(payload: object) -> str:
    call = dict(payload)  # type: ignore[arg-type]
    model = call.get("model")
    if model == "reviewer-model":
        return _json.dumps(
            {
                "verdict": False,
                "score": 3,
                "reason": "Пропорції поламані",
                "must_fix": ["Вирівняти сітку"],
            },
            ensure_ascii=False,
        )
    if model == "calibrator-model":
        return _json.dumps(
            {
                "summary": "Скіл не описує сітку",
                "root_cause": "У SKILL.md немає правила",
                "skills_missing": ["birds-map"],
                "points": [{"title": "Сітка з'їхала", "detail": "", "images": []}],
                "edits": [
                    {
                        "target": "task_prompt",
                        "task_id": "task-1",
                        "label": "Уточнити сітку",
                        "before": "Зробити карту",
                        "after": "Зробити карту з об'єктами у вузлах сітки",
                    }
                ],
            },
            ensure_ascii=False,
        )
    return "готово"


def test_calibrator_pauses_the_flow_on_the_first_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, nodes = build_flow(tmp_path)
    events: list[dict] = []
    runner = WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    )
    runner.run()
    request = next(
        event["request"]
        for event in events
        if event["type"] == "intervention_required"
    )
    assert request["type"] == "calibration"
    assert request["node_id"] == nodes["stop"].id
    report = request["report"]
    assert report["task_id"] == "task-1"
    assert report["points"][0]["title"] == "Сітка з'їхала"
    assert report["edits"][0]["target"] == "task_prompt"
    assert (tmp_path / "run" / "calibration.json").is_file()


def test_calibrator_resumes_the_reviewer_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, _nodes = build_flow(tmp_path)
    runner = WorkflowRunner(workflow, run_directory=tmp_path / "run")
    runner.run()
    reviewer_call = next(
        call
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "reviewer-model"
    )
    calibrator_call = next(
        call
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "calibrator-model"
    )
    assert calibrator_call["resumed"] is True
    assert calibrator_call["thread_id"] == reviewer_call["thread_id"]


def test_threshold_two_lets_the_flow_retry_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, nodes = build_flow(tmp_path)
    nodes["stop"].config["false_threshold"] = 2
    events: list[dict] = []
    runner = WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    )
    runner.run()
    executor_calls = [
        call
        for call in codex_adapter.FAKE_CALLS
        if call["model"] == "executor-model"
    ]
    assert len(executor_calls) == 2
    assert any(
        event["type"] == "intervention_required" for event in events
    )


def test_calibrator_is_not_seeded_into_the_initial_queue(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    runner = WorkflowRunner(workflow, run_directory=tmp_path / "run")
    queue = runner._initial_queue()
    assert nodes["manager"].id in queue
    assert nodes["stop"].id not in queue


def test_retry_task_response_resets_the_attempt_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", rejecting_responder)
    workflow, nodes = build_flow(tmp_path)
    runner = WorkflowRunner(workflow, run_directory=tmp_path / "run")
    runner.run()
    key = f"{nodes['stop'].id}:task-1"
    assert runner.checkpoint.calibration_attempts[key] == 1
    resumed = WorkflowRunner(
        workflow,
        checkpoint=runner.checkpoint,
        intervention_responses={nodes["stop"].id: {"action": "retry_task"}},
        run_directory=tmp_path / "run",
    )
    resumed.checkpoint.calibration_attempts[key] = 1
    node = workflow.node(nodes["stop"].id)
    result = resumed._execute_calibrator(node, {}, {}, tmp_path, None)
    assert result.data["action"] == "retry_task"
    assert resumed.checkpoint.calibration_attempts.get(key, 0) == 0


def test_broken_agent_answer_still_produces_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(payload: object) -> str:
        call = dict(payload)  # type: ignore[arg-type]
        if call.get("model") == "reviewer-model":
            return _json.dumps(
                {
                    "verdict": False,
                    "score": 1,
                    "reason": "Погано",
                    "must_fix": ["Переробити все"],
                },
                ensure_ascii=False,
            )
        if call.get("model") == "calibrator-model":
            return "я подумав і вирішив відповісти текстом"
        return "готово"

    monkeypatch.setattr(codex_adapter, "FAKE_RESPONDER", responder)
    workflow, _nodes = build_flow(tmp_path)
    events: list[dict] = []
    runner = WorkflowRunner(
        workflow, on_event=events.append, run_directory=tmp_path / "run"
    )
    runner.run()
    request = next(
        event["request"]
        for event in events
        if event["type"] == "intervention_required"
    )
    assert request["report"]["analysis_error"]
    assert request["report"]["points"][0]["title"] == "Переробити все"
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -v
```

Очікується: `AttributeError: 'WorkflowRunner' object has no attribute '_initial_queue'`.

- [ ] **Step 3: Додати лічильник і початкову чергу в `flowai/engine.py`**

У `RunCheckpoint` додати поле після `task_attempts`:

```python
    calibration_attempts: dict[str, int] = field(default_factory=dict)
```

у `to_dict` — `"calibration_attempts": dict(self.calibration_attempts),`
у `from_dict` — `calibration_attempts=dict(raw.get("calibration_attempts") or {}),`

Додати імпорти:

```python
from .calibration import CalibrationReport, parse_report, save_report
from .models import NEVER_SEEDED
from .skills import catalogue_text, list_skills, skills_used
```

Винести побудову початкової черги в метод і викликати його в `run`:

```python
    def _initial_queue(self) -> list[str]:
        """Ноди, з яких стартує Flow.

        Зворотні ребра з Result не роблять ноду залежною — інакше Tasks
        Manager ніколи б не запустився. Але нода калібрації теж має єдиний
        вхід із Result, і саме тому її треба виключити явно: інакше вона
        стартувала б першим кроком, ще до жодного вердикту.
        """
        return [
            node.id
            for node in self.workflow.routed_nodes()
            if node.kind not in NEVER_SEEDED
            and not any(
                (source := self.workflow.find(edge.source)) is not None
                and source.kind != "result"
                for edge in self.workflow.incoming(node.id)
            )
        ]
```

У `run` замінити блок `if not self.checkpoint.started:` на:

```python
        if not self.checkpoint.started:
            self.checkpoint.queue = self._initial_queue()
            self.checkpoint.started = True
```

- [ ] **Step 4: Навчити `_execute_agent` продовжувати чужий тред**

У `_execute_agent` замінити обчислення `resume_id`:

```python
        memory = str(node.config.get("memory", "thread"))
        # Нода калібрації продовжує тред Task Reviewer: той уже пам'ятає
        # і задачу, і роботу, і власний вердикт.
        thread_source = str(node.config.get("thread_source", "")) or node.id
        resume_id = (
            self.checkpoint.thread_ids.get(thread_source, "")
            if memory == "thread"
            else ""
        )
```

і запис назад:

```python
        if run.thread_id and memory == "thread" and thread_source == node.id:
            self.checkpoint.thread_ids[node.id] = run.thread_id
```

Дозволити ноді калібрації повертати JSON без спеціальної перевірки — ніяких змін не треба, гілка `elif parsed is not None` уже це робить.

- [ ] **Step 5: Написати `_execute_calibrator`**

Додати метод у `WorkflowRunner` після `_execute_result`:

```python
    def _upstream_node_of_kind(self, node_id: str, kind: str) -> FlowNode | None:
        """Найближча нода вказаного типу вгору по графу."""
        seen: set[str] = set()
        stack = [edge.source for edge in self.workflow.incoming(node_id)]
        while stack:
            current = stack.pop(0)
            if current in seen:
                continue
            seen.add(current)
            node = self.workflow.find(current)
            if node is None:
                continue
            if node.kind == kind:
                return node
            stack.extend(
                edge.source for edge in self.workflow.incoming(current)
            )
        return None

    def _execute_calibrator(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        context: dict[str, Any],
        workspace: Path,
        codex: CodexAdapter | None,
    ) -> NodeResult:
        """Зупинити Flow і зібрати рекомендації, коли задача провалилась K разів."""
        manager = next(iter(self.workflow.nodes_of_kind("tasks_manager")), None)
        task_id = ""
        task_title = ""
        if manager is not None:
            progress = self.checkpoint.task_progress.get(manager.id, {})
            task_id = str(progress.get("active_task_id", ""))
            tasks = normalize_managed_tasks(manager.config.get("tasks"))
            for index, task in enumerate(tasks):
                if str(task["id"]) == task_id:
                    task_title = managed_task_title(task, index)
                    break

        response = self.intervention_responses.pop(node.id, None)
        if isinstance(response, dict):
            action = str(response.get("action", "continue"))
            if action == "retry_task":
                self.checkpoint.calibration_attempts.pop(
                    f"{node.id}:{task_id}", None
                )
                result_node = self._upstream_node_of_kind(node.id, "result")
                if result_node is not None and task_id:
                    self.checkpoint.task_attempts.pop(
                        f"{result_node.id}:{task_id}", None
                    )
            return NodeResult(
                node.id,
                "success",
                text="Калібрацію завершено",
                data={"action": action, "task_id": task_id},
            )

        key = f"{node.id}:{task_id}"
        attempt = self.checkpoint.calibration_attempts.get(key, 0) + 1
        self.checkpoint.calibration_attempts[key] = attempt
        with self._control_lock:
            threshold = max(1, int(node.config.get("false_threshold", 1)))
        if attempt < threshold:
            self._emit(
                "calibration_skipped",
                node=node,
                message=(
                    f"Відхилення {attempt} з {threshold} — даємо Flow "
                    "спробувати ще раз"
                ),
            )
            return NodeResult(
                node.id,
                "success",
                text=f"Відхилення {attempt} з {threshold}",
                data={"action": "wait", "attempt": attempt},
            )

        review = self._review_payload_from(inputs)
        reason = self._reason_from(inputs)
        must_fix = review.get("must_fix")
        if not isinstance(must_fix, list):
            must_fix = []

        reviewer = self._upstream_node_of_kind(node.id, "task_reviewer")
        executor = self._upstream_node_of_kind(node.id, "executor")
        if reviewer is not None:
            node.config["thread_source"] = reviewer.id
            node.config["reviewer_node"] = reviewer.id

        used = skills_used(self.outputs_steps_for(executor))
        catalogue = catalogue_text(list_skills(codex))
        generated = []
        if executor is not None:
            previous = self.checkpoint.outputs.get(executor.id, {})
            data = previous.get("data")
            if isinstance(data, dict):
                generated = [
                    str(path) for path in data.get("_generated_files", [])
                ]

        task_prompt = ""
        if manager is not None and task_id:
            for task in normalize_managed_tasks(manager.config.get("tasks")):
                if str(task["id"]) == task_id:
                    task_prompt = str(task.get("prompt", ""))
                    break

        analysis_context = dict(context)
        analysis_context.update(
            {
                "skills_used": "\n".join(f"- {name}" for name in used)
                or "Агент не відкрив жодного скіла",
                "skills_catalogue": catalogue or "Каталог порожній",
                "task_prompt": task_prompt,
                "node_instructions": str(
                    executor.config.get("instructions", "") if executor else ""
                ),
                "node_prompt": str(
                    executor.config.get("prompt", "") if executor else ""
                ),
                "generated_files": "\n".join(f"- {path}" for path in generated)
                or "Файлів не зафіксовано",
            }
        )

        self._emit(
            "calibration_started",
            node=node,
            message="Аналізую скіли й готую рекомендації",
        )
        payload: Any = None
        analysis_error = ""
        try:
            analysis = self._execute_agent(
                node, inputs, analysis_context, workspace, codex
            )
            payload = analysis.data
        except RunCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - вікно має відкритись у будь-якому разі
            LOGGER.exception("Аналіз калібрації не вдався")
            analysis_error = str(exc)

        report = parse_report(
            payload,
            node_id=node.id,
            node_title=node.title,
            task_id=task_id,
            task_title=task_title or "Активне завдання",
            workflow_name=self.workflow.name,
            attempt=attempt,
            threshold=threshold,
            reason=reason,
            must_fix=[str(item) for item in must_fix],
            skills_used=used,
        )
        if analysis_error:
            report.analysis_error = analysis_error
        if executor is not None:
            for edit in report.edits:
                if edit.target in {"node_prompt", "node_instructions"}:
                    edit.node_id = edit.node_id or executor.id
                if edit.target == "task_prompt":
                    edit.task_id = edit.task_id or task_id

        report_path = save_report(report, self._protocol_directory())
        raise InterventionRequired(
            {
                "node_id": node.id,
                "node_title": node.title,
                "type": "calibration",
                "question": (
                    f"Рев'ювер відхилив «{report.task_title}» — "
                    "перегляньте рекомендації"
                ),
                "report": report.to_dict(),
                "report_path": str(report_path),
            }
        )

    def outputs_steps_for(self, node: FlowNode | None) -> list[dict[str, Any]]:
        """Кроки останнього ходу вказаної ноди — звідти видно відкриті скіли."""
        if node is None:
            return []
        return list(self.checkpoint.protocol_steps.get(node.id, []))
```

Додати поле в `RunCheckpoint`, щоб кроки переживали паузу:

```python
    protocol_steps: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
```

у `to_dict` — `"protocol_steps": self.protocol_steps,`
у `from_dict` — `protocol_steps=dict(raw.get("protocol_steps") or {}),`

У `_execute_agent`, одразу після `self._last_steps = run.items`, додати:

```python
        self.checkpoint.protocol_steps[node.id] = list(run.items)
```

- [ ] **Step 6: Підключити ноду в диспетчер `_execute_node`**

У `_execute_node` додати гілку перед загальною агентською:

```python
        if node.kind == "calibrator":
            return self._execute_calibrator(node, inputs, context, workspace, codex)
```

- [ ] **Step 7: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -v
```

Очікується: 17 passed.

- [ ] **Step 8: Прогнати весь набір, лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m pytest -q
```

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/engine.py tests/test_calibration.py && git commit -m "feat(engine): stop the flow and analyse skills when a task is rejected"
```

---

### Task 10: Чекпоінт і звіт переживають перезапуск

**Files:**
- Modify: `flowai/run_history.py`
- Modify: `flowai/ui/main_window.py` (`_handle_event`, `_run_thread_finished`, відкриття проєкту)
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `RunCheckpoint` із `flowai/engine.py`, `load_report` із `flowai/calibration.py`
- Produces:
  - `flowai.run_history.CHECKPOINT_FILE: str = "flowai-checkpoint.json"`
  - `save_checkpoint(directory: Path, checkpoint: RunCheckpoint, *, project_path: Path | None, request: dict[str, Any]) -> Path`
  - `load_checkpoint(directory: Path) -> tuple[RunCheckpoint, dict[str, Any]] | None`
  - `find_pending_run(project_path: Path) -> Path | None` — найновіша папка `runs/*` із чекпоінтом у стані очікування
  - `clear_checkpoint(directory: Path) -> None`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_calibration.py`:

```python
from flowai.engine import RunCheckpoint
from flowai.run_history import (
    CHECKPOINT_FILE,
    clear_checkpoint,
    find_pending_run,
    load_checkpoint,
    save_checkpoint,
)


def test_checkpoint_round_trips(tmp_path: Path) -> None:
    checkpoint = RunCheckpoint(started=True, steps=4, queue=["a", "b"])
    checkpoint.calibration_attempts["node:task"] = 1
    request = {"type": "calibration", "node_id": "node"}
    path = save_checkpoint(
        tmp_path, checkpoint, project_path=tmp_path / "flow.flowai.json",
        request=request,
    )
    assert path.name == CHECKPOINT_FILE
    restored = load_checkpoint(tmp_path)
    assert restored is not None
    state, saved_request = restored
    assert state.steps == 4
    assert state.queue == ["a", "b"]
    assert state.calibration_attempts == {"node:task": 1}
    assert saved_request == request


def test_find_pending_run_picks_the_newest(tmp_path: Path) -> None:
    project = tmp_path / "flow.flowai.json"
    project.write_text("{}", encoding="utf-8")
    older = tmp_path / "runs" / "20260101-000000-000000"
    newer = tmp_path / "runs" / "20260201-000000-000000"
    for directory in (older, newer):
        directory.mkdir(parents=True)
        save_checkpoint(
            directory,
            RunCheckpoint(started=True),
            project_path=project,
            request={"type": "calibration"},
        )
    assert find_pending_run(project) == newer


def test_find_pending_run_ignores_cleared_checkpoints(tmp_path: Path) -> None:
    project = tmp_path / "flow.flowai.json"
    project.write_text("{}", encoding="utf-8")
    directory = tmp_path / "runs" / "20260101-000000-000000"
    directory.mkdir(parents=True)
    save_checkpoint(
        directory,
        RunCheckpoint(started=True),
        project_path=project,
        request={"type": "calibration"},
    )
    clear_checkpoint(directory)
    assert find_pending_run(project) is None


def test_find_pending_run_ignores_other_projects(tmp_path: Path) -> None:
    project = tmp_path / "flow.flowai.json"
    other = tmp_path / "other.flowai.json"
    project.write_text("{}", encoding="utf-8")
    directory = tmp_path / "runs" / "20260101-000000-000000"
    directory.mkdir(parents=True)
    save_checkpoint(
        directory,
        RunCheckpoint(started=True),
        project_path=other,
        request={"type": "calibration"},
    )
    assert find_pending_run(project) is None


def test_load_checkpoint_returns_none_for_a_broken_file(tmp_path: Path) -> None:
    (tmp_path / CHECKPOINT_FILE).write_text("не json", encoding="utf-8")
    assert load_checkpoint(tmp_path) is None
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k checkpoint -v
```

Очікується: `ImportError: cannot import name 'CHECKPOINT_FILE'`.

- [ ] **Step 3: Дописати `flowai/run_history.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - лише для типів
    from .engine import RunCheckpoint

LOGGER = logging.getLogger(__name__)
RUN_FILE = "flowai-run.json"
CHECKPOINT_FILE = "flowai-checkpoint.json"
MAX_RUNS = 50


def save_checkpoint(
    directory: Path,
    checkpoint: RunCheckpoint,
    *,
    project_path: Path | None,
    request: dict[str, Any],
) -> Path:
    """Зберегти стан планувальника, щоб Flow пережив закриття FlowAI.

    Без цього нотифікація безглузда: користувач повертається до вікна
    наступного дня, а продовжити запуск уже нізвідки.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CHECKPOINT_FILE
    target.write_text(
        json.dumps(
            {
                "project_path": str(project_path) if project_path else "",
                "request": request,
                "checkpoint": checkpoint.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def load_checkpoint(
    directory: Path,
) -> tuple[RunCheckpoint, dict[str, Any]] | None:
    from .engine import RunCheckpoint

    try:
        payload = json.loads(
            (directory / CHECKPOINT_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("checkpoint")
    if not isinstance(state, dict):
        return None
    request = payload.get("request")
    return RunCheckpoint.from_dict(state), (
        request if isinstance(request, dict) else {}
    )


def clear_checkpoint(directory: Path) -> None:
    """Прибрати чекпоінт: запуск завершено або скасовано."""
    (directory / CHECKPOINT_FILE).unlink(missing_ok=True)


def find_pending_run(project_path: Path) -> Path | None:
    """Найновіший запуск цього проєкту, який чекає на відповідь користувача."""
    runs = project_path.resolve().parent / "runs"
    if not runs.is_dir():
        return None
    wanted = str(project_path.resolve())
    for directory in sorted(runs.iterdir(), key=lambda item: item.name, reverse=True):
        if not directory.is_dir():
            continue
        try:
            payload = json.loads(
                (directory / CHECKPOINT_FILE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if str(payload.get("project_path", "")) == wanted:
            return directory
    return None
```

(Функція `load_runs` лишається без змін нижче.)

- [ ] **Step 4: Зберігати чекпоінт у `flowai/ui/main_window.py`**

Додати імпорт:

```python
from ..run_history import clear_checkpoint, find_pending_run, load_checkpoint, save_checkpoint
```

У `_handle_event`, у гілці `elif event_type == "intervention_required":`, після `session.pending_intervention = request ...` додати:

```python
            directory = session.run_directory
            if directory is not None and session.checkpoint is not None:
                save_checkpoint(
                    directory,
                    session.checkpoint,
                    project_path=session.project_path,
                    request=session.pending_intervention or {},
                )
```

У `_run_finished` та `_run_failed`, після `session.stop_requested = False`, додати:

```python
        if session.run_directory is not None:
            clear_checkpoint(session.run_directory)
```

- [ ] **Step 5: Відновлювати стан при відкритті проєкту**

Додати метод у `MainWindow` поряд із `_show_pending_intervention`:

```python
    def _restore_pending_run(self, session: WorkspaceSession) -> None:
        """Підняти призупинений запуск, якщо FlowAI перезапускали.

        Чекпоінт лежить поряд із журналом у `runs/<час>/` і містить стан
        планувальника разом із запитом, на який чекає користувач.
        """
        if session.project_path is None or session.checkpoint is not None:
            return
        directory = find_pending_run(session.project_path)
        if directory is None:
            return
        restored = load_checkpoint(directory)
        if restored is None:
            return
        checkpoint, request = restored
        session.checkpoint = checkpoint
        session.run_directory = directory
        session.pending_intervention = request or None
        session.run_state = "needs_attention"
        node_id = str(request.get("node_id") or "")
        if node_id and session.id == self.current_workspace_id:
            self.scene.set_attention(node_id, True)
        self._append_session_log(
            session, f"■ Відновлено призупинений запуск: {directory}"
        )
        self._refresh_workspace_sidebar()
```

Викликати його наприкінці методу, що завантажує Flow у сесію (там, де встановлюється `session.load_state = "loaded"`):

```python
        self._restore_pending_run(session)
```

- [ ] **Step 6: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -v
```

Очікується: 22 passed.

- [ ] **Step 7: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/run_history.py flowai/ui/main_window.py tests/test_calibration.py && git commit -m "feat(runs): persist the run checkpoint so a paused flow survives a restart"
```

---

### Task 11: Малювання ноди калібрації та поля Інспектора

**Files:**
- Modify: `flowai/ui/canvas.py` (побудова портів `NodeItem`)
- Modify: `flowai/ui/inspector.py` (`KIND_FIELDS`, `_build_node_page`, `_load_node`, `_save_node`)
- Test: `tests/test_workspaces_ui.py`

**Interfaces:**
- Consumes: `TERMINAL_KINDS` із `flowai/models.py`
- Produces:
  - `NodeItem` для `calibrator` має один вхідний порт і жодного вихідного
  - `Inspector.false_threshold: NoWheelSpinBox`
  - `Inspector.threshold_hint: QLabel` — попередження, що EXHAUSTED недосяжний
  - `KIND_FIELDS["calibrator"] = AGENT_FIELDS | {"false_threshold", "threshold_hint"}`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_calibrator_node_has_no_output_ports() -> None:
    window = MainWindow()
    workflow = Workflow()
    node = FlowNode.create("calibrator")
    workflow.nodes.append(node)
    window.scene.set_workflow(workflow)
    item = window.scene.node_items[node.id]
    assert item.output_ports == {}
    assert "in" in item.input_ports
    window.close()


def test_inspector_shows_the_calibration_threshold() -> None:
    window = MainWindow()
    node = FlowNode.create("calibrator")
    node.config["false_threshold"] = 3
    window.inspector.set_workflow(Workflow(nodes=[node]))
    window.inspector.set_object(node)
    assert window.inspector.false_threshold.value() == 3
    window.inspector.false_threshold.setValue(2)
    assert node.config["false_threshold"] == 2
    window.close()


def test_inspector_warns_when_exhausted_becomes_unreachable() -> None:
    window = MainWindow()
    workflow = Workflow()
    result = FlowNode.create("result")
    result.config["task_attempt_limit"] = 3
    stop = FlowNode.create("calibrator")
    stop.config["false_threshold"] = 1
    workflow.nodes.extend([result, stop])
    workflow.edges.append(FlowEdge.create(result.id, stop.id, "false"))
    window.inspector.set_workflow(workflow)
    window.inspector.set_object(stop)
    assert "EXHAUSTED" in window.inspector.threshold_hint.text()
    assert window.inspector.threshold_hint.isVisible() is True
    window.close()


def test_inspector_hides_the_warning_when_exhausted_can_fire() -> None:
    window = MainWindow()
    workflow = Workflow()
    result = FlowNode.create("result")
    result.config["task_attempt_limit"] = 2
    stop = FlowNode.create("calibrator")
    stop.config["false_threshold"] = 5
    workflow.nodes.extend([result, stop])
    workflow.edges.append(FlowEdge.create(result.id, stop.id, "false"))
    window.inspector.set_workflow(workflow)
    window.inspector.set_object(stop)
    assert window.inspector.threshold_hint.isVisible() is False
    window.close()
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_workspaces_ui.py -k "calibra" -v
```

Очікується: `AssertionError` на `item.output_ports` та `AttributeError: false_threshold`.

- [ ] **Step 3: Прибрати вихідні порти в `flowai/ui/canvas.py`**

У `NodeItem`, у методі, що будує порти (біля рядка 302, де створюється `exhausted_port`), обгорнути створення вихідних портів умовою. На початку блоку побудови вихідних портів додати:

```python
        from ..models import TERMINAL_KINDS

        if self.model.kind in TERMINAL_KINDS:
            # Маршрут на цій ноді закінчується: Flow зупиняється й чекає
            # на рішення користувача, тож виходів у неї немає.
            self.output_ports = {}
        elif self.model.kind == "result":
            ...
```

(решта гілок лишається як була, з тим самим тілом)

- [ ] **Step 4: Додати поля в `flowai/ui/inspector.py`**

У `KIND_FIELDS` додати:

```python
    "calibrator": AGENT_FIELDS | {"false_threshold", "threshold_hint"},
```

У `_build_node_page`, після `self.task_attempt_limit`, додати:

```python
        self.false_threshold = NoWheelSpinBox()
        self.false_threshold.setRange(1, 20)
        self.false_threshold.setValue(1)
        self.false_threshold.setToolTip(
            "Після якого за рахунком FALSE зупиняти Flow і показувати "
            "рекомендації"
        )
        self.node_form.addRow("Зупиняти після FALSE №", self.false_threshold)

        self.threshold_hint = QLabel("")
        self.threshold_hint.setObjectName("mutedLabel")
        self.threshold_hint.setWordWrap(True)
        self.node_form.addRow("", self.threshold_hint)
```

Додати обидва віджети у словник `fields`:

```python
            "false_threshold": self.false_threshold,
            "threshold_hint": self.threshold_hint,
```

Підключити сигнал:

```python
        self.false_threshold.valueChanged.connect(self._save_node)
```

У `_load_node`, у кінці, перед `self._show_node_fields(node.kind)`, додати:

```python
        self.false_threshold.setValue(
            max(1, int(node.config.get("false_threshold", 1)))
        )
        self._update_threshold_hint(node)
```

Додати метод:

```python
    def _update_threshold_hint(self, node: FlowNode) -> None:
        """Попередити, що при такому порозі жовтий вихід ніколи не спрацює."""
        self.threshold_hint.clear()
        if node.kind != "calibrator" or self.workflow is None:
            self.threshold_hint.setVisible(False)
            return
        threshold = max(1, int(node.config.get("false_threshold", 1)))
        limits = [
            max(1, int(source.config.get("task_attempt_limit", 2)))
            for edge in self.workflow.incoming(node.id)
            if (source := self.workflow.find(edge.source)) is not None
            and source.kind == "result"
        ]
        if limits and threshold <= min(limits):
            self.threshold_hint.setText(
                f"Поріг {threshold} спрацює раніше за ліміт спроб "
                f"{min(limits)} — вихід EXHAUSTED і червоний хрестик "
                "лишаться запобіжником і в цьому Flow не задіються."
            )
            self.threshold_hint.setVisible(True)
        else:
            self.threshold_hint.setVisible(False)
```

У `_save_node`, у блоці агентських нод, після `node.config.update({...})`, додати:

```python
            if node.kind == "calibrator":
                node.config["false_threshold"] = self.false_threshold.value()
                self._update_threshold_hint(node)
```

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_workspaces_ui.py -v
```

Очікується: усі проходять, зокрема 4 нових.

- [ ] **Step 6: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/canvas.py flowai/ui/inspector.py tests/test_workspaces_ui.py && git commit -m "feat(ui): draw the Calibration Stop node and edit its threshold"
```

---
## Фаза 3 — Вікно калібрації

### Task 12: Віджет split-diff

**Files:**
- Create: `flowai/ui/diff_view.py`
- Test: `tests/test_calibration_ui.py`

**Interfaces:**
- Consumes: `ProposedEdit` із `flowai/calibration.py`, `COLORS` із `flowai/ui/design.py`
- Produces:
  - `class DiffRow` — `kind: str` (`"equal" | "insert" | "delete" | "replace"`), `left: str`, `right: str`, `left_number: int`, `right_number: int`
  - `build_rows(before: str, after: str) -> list[DiffRow]`
  - `class DiffView(QWidget)` — конструктор `DiffView(edit: ProposedEdit, parent=None)`; сигнал `toggled = Signal(bool)`; властивість `accepted -> bool`; віджети `checkbox: QCheckBox`, `table: QTableWidget`

- [ ] **Step 1: Написати падаючий тест**

Створити `tests/test_calibration_ui.py`:

```python
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.calibration import ProposedEdit
from flowai.ui.diff_view import DiffView, build_rows


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_build_rows_marks_an_unchanged_line() -> None:
    rows = build_rows("однаково", "однаково")
    assert [row.kind for row in rows] == ["equal"]
    assert rows[0].left == "однаково"
    assert rows[0].right == "однаково"


def test_build_rows_marks_a_replacement() -> None:
    rows = build_rows("було так", "стало інакше")
    assert [row.kind for row in rows] == ["replace"]
    assert rows[0].left == "було так"
    assert rows[0].right == "стало інакше"


def test_build_rows_marks_a_deletion() -> None:
    rows = build_rows("перший\nдругий", "перший")
    assert [row.kind for row in rows] == ["equal", "delete"]
    assert rows[1].right == ""
    assert rows[1].right_number == 0


def test_build_rows_marks_an_insertion() -> None:
    rows = build_rows("перший", "перший\nдругий")
    assert [row.kind for row in rows] == ["equal", "insert"]
    assert rows[1].left == ""
    assert rows[1].left_number == 0


def test_build_rows_numbers_both_sides() -> None:
    rows = build_rows("a\nb\nc", "a\nB\nc")
    assert [(row.left_number, row.right_number) for row in rows] == [
        (1, 1),
        (2, 2),
        (3, 3),
    ]


def test_diff_view_starts_accepted() -> None:
    edit = ProposedEdit(
        target="task_prompt", label="Уточнити", before="старе", after="нове"
    )
    view = DiffView(edit)
    assert view.accepted is True
    assert view.checkbox.isChecked() is True


def test_unchecking_the_view_updates_the_edit() -> None:
    edit = ProposedEdit(
        target="task_prompt", label="Уточнити", before="старе", after="нове"
    )
    view = DiffView(edit)
    view.checkbox.setChecked(False)
    assert view.accepted is False
    assert edit.accepted is False


def test_diff_view_fills_the_table_with_both_sides() -> None:
    edit = ProposedEdit(
        target="task_prompt",
        label="Уточнити",
        before="перший\nдругий",
        after="перший\nтретій",
    )
    view = DiffView(edit)
    assert view.table.rowCount() == 2
    assert view.table.item(0, 1).text() == "перший"
    assert view.table.item(1, 1).text() == "другий"
    assert view.table.item(1, 3).text() == "третій"


def test_diff_view_shows_the_label_and_rationale() -> None:
    edit = ProposedEdit(
        target="skill_file",
        label="Додати правило сітки",
        rationale="Інакше об'єкти лягають між вузлами",
        before="a",
        after="b",
        path="C:/skills/birds-map/SKILL.md",
        skill="birds-map",
    )
    view = DiffView(edit)
    assert "Додати правило сітки" in view.checkbox.text()
    assert "birds-map / SKILL.md" in view.path_label.text()
    assert "між вузлами" in view.rationale_label.text()
```

- [ ] **Step 2: Запустити тест і переконатись, що він падає**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.ui.diff_view'`.

- [ ] **Step 3: Написати `flowai/ui/diff_view.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..calibration import ProposedEdit
from .design import COLORS

# Кольори рядків diff: тьмяні, щоб текст лишався читабельним на темному тлі.
ADDED_BACKGROUND = "#12341F"
REMOVED_BACKGROUND = "#3A1620"
ROW_HEIGHT = 20
MAX_VISIBLE_ROWS = 18


@dataclass(slots=True)
class DiffRow:
    """Один рядок split-diff: що було ліворуч і що стало праворуч."""

    kind: str
    left: str = ""
    right: str = ""
    left_number: int = 0
    right_number: int = 0


def build_rows(before: str, after: str) -> list[DiffRow]:
    """Порівняти два тексти по рядках, як це робить SVN або GitHub."""
    left_lines = before.splitlines() or [""]
    right_lines = after.splitlines() or [""]
    matcher = SequenceMatcher(None, left_lines, right_lines, autojunk=False)
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append(
                    DiffRow(
                        kind="equal",
                        left=left_lines[i1 + offset],
                        right=right_lines[j1 + offset],
                        left_number=i1 + offset + 1,
                        right_number=j1 + offset + 1,
                    )
                )
            continue
        removed = left_lines[i1:i2]
        added = right_lines[j1:j2]
        for offset in range(max(len(removed), len(added))):
            has_left = offset < len(removed)
            has_right = offset < len(added)
            if has_left and has_right:
                kind = "replace"
            elif has_left:
                kind = "delete"
            else:
                kind = "insert"
            rows.append(
                DiffRow(
                    kind=kind,
                    left=removed[offset] if has_left else "",
                    right=added[offset] if has_right else "",
                    left_number=i1 + offset + 1 if has_left else 0,
                    right_number=j1 + offset + 1 if has_right else 0,
                )
            )
    return rows


class DiffView(QWidget):
    """Одна правка: зліва оригінал, справа зміна, галочка «застосувати»."""

    toggled = Signal(bool)

    def __init__(self, edit: ProposedEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = edit

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(4)

        self.checkbox = QCheckBox(edit.label or "Правка")
        self.checkbox.setChecked(edit.accepted)
        self.checkbox.toggled.connect(self._toggled)
        layout.addWidget(self.checkbox)

        self.path_label = QLabel(edit.display_path)
        self.path_label.setObjectName("mutedLabel")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.path_label)

        self.rationale_label = QLabel(edit.rationale)
        self.rationale_label.setObjectName("mutedLabel")
        self.rationale_label.setWordWrap(True)
        self.rationale_label.setVisible(bool(edit.rationale.strip()))
        layout.addWidget(self.rationale_label)

        self.rows = build_rows(edit.before, edit.after)
        self.table = QTableWidget(len(self.rows), 4)
        self.table.setObjectName("diffTable")
        self.table.setHorizontalHeaderLabels(["", "Було", "", "Стало"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._fill_table()
        layout.addWidget(self.table)

    @property
    def accepted(self) -> bool:
        return self.checkbox.isChecked()

    def _toggled(self, checked: bool) -> None:
        self.edit.accepted = checked
        self.table.setEnabled(checked)
        self.toggled.emit(checked)

    def _fill_table(self) -> None:
        mono = QFont("Cascadia Mono", 9)
        for index, row in enumerate(self.rows):
            cells = [
                (0, str(row.left_number or ""), COLORS["text_dim"], ""),
                (
                    1,
                    row.left,
                    COLORS["text"],
                    REMOVED_BACKGROUND if row.kind in {"replace", "delete"} else "",
                ),
                (2, str(row.right_number or ""), COLORS["text_dim"], ""),
                (
                    3,
                    row.right,
                    COLORS["text"],
                    ADDED_BACKGROUND if row.kind in {"replace", "insert"} else "",
                ),
            ]
            for column, text, foreground, background in cells:
                item = QTableWidgetItem(text)
                item.setFont(mono)
                item.setForeground(QColor(foreground))
                if background:
                    item.setBackground(QColor(background))
                self.table.setItem(index, column, item)
            self.table.setRowHeight(index, ROW_HEIGHT)
        visible = min(len(self.rows), MAX_VISIBLE_ROWS)
        self.table.setMinimumHeight(ROW_HEIGHT * (visible + 1) + 8)
```

- [ ] **Step 4: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -v
```

Очікується: 9 passed.

- [ ] **Step 5: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/diff_view.py tests/test_calibration_ui.py && git commit -m "feat(ui): render proposed edits as a side-by-side diff"
```

---

### Task 13: Вікно калібрації на дві вкладки

**Files:**
- Create: `flowai/ui/calibration_dialog.py`
- Test: `tests/test_calibration_ui.py`

**Interfaces:**
- Consumes: `CalibrationReport`, `RejectionPoint` із `flowai/calibration.py`; `DiffView` із Task 12; `AnimatedButton` із `flowai/ui/controls.py`; `AnimatedDialog` із `flowai/ui/motion.py`; `path_menu`, `open_file` із `flowai/ui/paths.py`
- Produces:
  - `class RejectionPointCard(QFrame)` — конструктор `(point: RejectionPoint, index: int, parent=None)`; віджет `note: QPlainTextEdit`; метод `commit() -> None` записує текст у `point.user_note`
  - `class CalibrationDialog(AnimatedDialog)` — конструктор `(report: CalibrationReport, parent=None, *, models: list[str], default_model: str, default_effort: str)`
    - атрибути: `report`, `decision: str` (`"" | "apply" | "regenerate" | "retry" | "stop"`), `model: str`, `effort: str`, `pinned_skills: list[str]`
    - віджети: `tabs: QTabWidget`, `points_layout: QVBoxLayout`, `edits_layout: QVBoxLayout`, `model_combo`, `effort_combo`, `apply_button`, `regenerate_button`, `retry_button`
    - метод `commit_notes() -> None`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_calibration_ui.py`:

```python
from flowai.calibration import (
    CalibrationReport,
    RejectionImage,
    RejectionPoint,
)
from flowai.ui.calibration_dialog import CalibrationDialog, RejectionPointCard

MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]


def make_report(**overrides: object) -> CalibrationReport:
    report = CalibrationReport(
        node_id="stop-1",
        node_title="Calibration Stop",
        task_id="task-1",
        task_title="Зробити карту",
        workflow_name="Карти",
        attempt=1,
        threshold=1,
        verdict_reason="Пропорції поламані",
        summary="Скіл не описує сітку",
        root_cause="У SKILL.md немає правила",
        points=[
            RejectionPoint(
                title="Сітка з'їхала",
                detail="Об'єкти не в вузлах",
                images=[RejectionImage(path="C:/out/map.png", note="Ліва частина")],
            ),
            RejectionPoint(title="Тіні різні", detail=""),
        ],
        skills_used=["birds-map"],
        skills_missing=["image-cutout"],
        edits=[
            ProposedEdit(
                target="task_prompt",
                task_id="task-1",
                label="Уточнити сітку",
                before="Зробити карту",
                after="Зробити карту з об'єктами у вузлах",
            )
        ],
    )
    for key, value in overrides.items():
        setattr(report, key, value)
    return report


def make_dialog(report: CalibrationReport) -> CalibrationDialog:
    return CalibrationDialog(
        report,
        models=MODELS,
        default_model="gpt-5.6-terra",
        default_effort="medium",
    )


def test_dialog_has_two_tabs_in_the_right_order() -> None:
    dialog = make_dialog(make_report())
    assert dialog.tabs.count() == 2
    assert dialog.tabs.tabText(0) == "Чому відхилено"
    assert dialog.tabs.tabText(1).startswith("Пропоновані правки")
    assert dialog.tabs.currentIndex() == 0


def test_edits_tab_shows_the_edit_count() -> None:
    dialog = make_dialog(make_report())
    assert "1" in dialog.tabs.tabText(1)


def test_every_point_gets_a_note_field() -> None:
    dialog = make_dialog(make_report())
    cards = dialog.point_cards
    assert len(cards) == 2
    assert isinstance(cards[0], RejectionPointCard)
    cards[0].note.setPlainText("Виправити вручну")
    dialog.commit_notes()
    assert dialog.report.points[0].user_note == "Виправити вручну"


def test_image_note_is_shown_next_to_the_path() -> None:
    dialog = make_dialog(make_report())
    card = dialog.point_cards[0]
    assert card.image_rows[0].text().find("Ліва частина") >= 0
    assert card.image_rows[0].toolTip() == "C:/out/map.png"


def test_missing_skills_are_offered_for_pinning() -> None:
    dialog = make_dialog(make_report())
    assert [box.text() for box in dialog.skill_boxes] == ["image-cutout"]
    dialog.skill_boxes[0].setChecked(True)
    assert dialog.pinned_skills == ["image-cutout"]


def test_apply_records_the_decision() -> None:
    dialog = make_dialog(make_report())
    dialog.apply_button.click()
    assert dialog.decision == "apply"


def test_regenerate_records_model_and_effort() -> None:
    dialog = make_dialog(make_report())
    dialog.model_combo.setCurrentText("gpt-5.6-sol")
    dialog.effort_combo.setCurrentText("high")
    dialog.regenerate_button.click()
    assert dialog.decision == "regenerate"
    assert dialog.model == "gpt-5.6-sol"
    assert dialog.effort == "high"


def test_retry_records_the_decision() -> None:
    dialog = make_dialog(make_report())
    dialog.retry_button.click()
    assert dialog.decision == "retry"


def test_apply_is_disabled_without_edits() -> None:
    dialog = make_dialog(make_report(edits=[]))
    assert dialog.apply_button.isEnabled() is False


def test_analysis_error_is_shown_as_a_banner() -> None:
    dialog = make_dialog(make_report(analysis_error="Агент упав"))
    assert dialog.error_banner.isVisible() is True
    assert "Агент упав" in dialog.error_banner.text()


def test_no_banner_when_the_analysis_succeeded() -> None:
    dialog = make_dialog(make_report())
    assert dialog.error_banner.isVisible() is False
```

- [ ] **Step 2: Запустити тест і переконатись, що він падає**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.ui.calibration_dialog'`.

- [ ] **Step 3: Написати `flowai/ui/calibration_dialog.py`**

```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..calibration import CalibrationReport, RejectionPoint
from .controls import AnimatedButton
from .design import COLORS, SPACE
from .diff_view import DiffView
from .motion import AnimatedDialog
from .paths import is_image, open_file, path_menu

THUMBNAIL_WIDTH = 320
EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]


class RejectionPointCard(QFrame):
    """Один пункт відхилення разом із полем для вашого бачення."""

    def __init__(
        self, point: RejectionPoint, index: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.point = point
        self.image_rows: list[QLabel] = []
        self.setObjectName("rejectionCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["xs"])

        title = QLabel(f"{index}. {point.title}")
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        if point.detail.strip():
            detail = QLabel(point.detail)
            detail.setWordWrap(True)
            layout.addWidget(detail)

        for image in point.images:
            row = QLabel(f"{Path(image.path).name} — {image.note}")
            row.setObjectName("mutedLabel")
            row.setToolTip(image.path)
            row.setWordWrap(True)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            row.customContextMenuRequested.connect(
                lambda position, path=image.path, widget=row: path_menu(
                    path, widget
                ).exec(widget.mapToGlobal(position))
            )
            row.mouseDoubleClickEvent = (  # type: ignore[method-assign]
                lambda _event, path=image.path: open_file(path)
            )
            layout.addWidget(row)
            self.image_rows.append(row)
            if is_image(image.path) and Path(image.path).is_file():
                pixmap = QPixmap(image.path)
                if not pixmap.isNull():
                    preview = QLabel()
                    preview.setPixmap(
                        pixmap.scaledToWidth(
                            THUMBNAIL_WIDTH,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    layout.addWidget(preview)

        self.note = QPlainTextEdit(point.user_note)
        self.note.setPlaceholderText("Ваше бачення виправлення")
        self.note.setMaximumHeight(80)
        layout.addWidget(self.note)

    def commit(self) -> None:
        self.point.user_note = self.note.toPlainText()


class CalibrationDialog(AnimatedDialog):
    """Рев'ювер відхилив роботу: пояснення, ваші нотатки й пропоновані правки."""

    def __init__(
        self,
        report: CalibrationReport,
        parent: QWidget | None = None,
        *,
        models: list[str],
        default_model: str,
        default_effort: str,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.decision = ""
        self.model = default_model
        self.effort = default_effort
        self.point_cards: list[RejectionPointCard] = []
        self.diff_views: list[DiffView] = []
        self.skill_boxes: list[QCheckBox] = []

        self.setWindowTitle(f"Відхилено: {report.task_title}")
        self.setMinimumSize(1040, 720)

        root = QVBoxLayout(self)

        heading = QLabel(
            f"«{report.task_title}» — спроба {report.attempt} із порогом "
            f"{report.threshold}"
        )
        heading.setObjectName("sectionTitle")
        root.addWidget(heading)

        self.error_banner = QLabel("")
        self.error_banner.setWordWrap(True)
        self.error_banner.setStyleSheet(f"color: {COLORS['warning']};")
        self.error_banner.setVisible(bool(report.analysis_error))
        if report.analysis_error:
            self.error_banner.setText(
                f"Аналіз скілів не завершився: {report.analysis_error}"
            )
        root.addWidget(self.error_banner)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_points_tab(), "Чому відхилено")
        self.tabs.addTab(
            self._build_edits_tab(),
            f"Пропоновані правки ({len(report.edits)})",
        )
        self.tabs.setCurrentIndex(0)
        root.addWidget(self.tabs, 1)
        root.addLayout(self._build_buttons(models))

    # ------------------------------------------------------------------
    # Вкладки
    # ------------------------------------------------------------------

    def _scrolled(self) -> tuple[QScrollArea, QVBoxLayout]:
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(SPACE["md"])
        area.setWidget(inner)
        return area, layout

    def _build_points_tab(self) -> QWidget:
        area, layout = self._scrolled()
        self.points_layout = layout
        if self.report.verdict_reason.strip():
            reason = QLabel(self.report.verdict_reason)
            reason.setWordWrap(True)
            reason.setObjectName("mutedLabel")
            layout.addWidget(reason)
        for index, point in enumerate(self.report.points, start=1):
            card = RejectionPointCard(point, index)
            self.point_cards.append(card)
            layout.addWidget(card)
        layout.addStretch()
        return area

    def _build_edits_tab(self) -> QWidget:
        area, layout = self._scrolled()
        self.edits_layout = layout
        if self.report.root_cause.strip():
            cause = QLabel(f"Причина: {self.report.root_cause}")
            cause.setWordWrap(True)
            layout.addWidget(cause)
        if self.report.skills_used:
            used = QLabel(
                "Агент працював зі скілами: "
                + ", ".join(self.report.skills_used)
            )
            used.setObjectName("mutedLabel")
            used.setWordWrap(True)
            layout.addWidget(used)
        if self.report.skills_missing:
            missing = QLabel("Рев'ювер радить закріпити за блоком:")
            layout.addWidget(missing)
            for name in self.report.skills_missing:
                box = QCheckBox(name)
                self.skill_boxes.append(box)
                layout.addWidget(box)
        if not self.report.edits:
            empty = QLabel("Рев'ювер не запропонував конкретних правок.")
            empty.setObjectName("mutedLabel")
            layout.addWidget(empty)
        for edit in self.report.edits:
            view = DiffView(edit)
            self.diff_views.append(view)
            layout.addWidget(view)
        layout.addStretch()
        return area

    def _build_buttons(self, models: list[str]) -> QHBoxLayout:
        row = QHBoxLayout()
        self.apply_button = AnimatedButton("Застосувати правки", "primary")
        self.apply_button.setEnabled(bool(self.report.edits))
        self.apply_button.clicked.connect(lambda: self._decide("apply"))
        row.addWidget(self.apply_button)

        self.retry_button = AnimatedButton("Хай спробує сам", "secondary")
        self.retry_button.clicked.connect(lambda: self._decide("retry"))
        row.addWidget(self.retry_button)

        row.addStretch()

        self.model_combo = QComboBox()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(self.model)
        row.addWidget(QLabel("Модель"))
        row.addWidget(self.model_combo)

        self.effort_combo = QComboBox()
        self.effort_combo.addItems(EFFORTS)
        self.effort_combo.setCurrentText(self.effort)
        row.addWidget(QLabel("Складність"))
        row.addWidget(self.effort_combo)

        self.regenerate_button = AnimatedButton("Regenerate Prompt", "primary")
        self.regenerate_button.clicked.connect(lambda: self._decide("regenerate"))
        row.addWidget(self.regenerate_button)

        stop = AnimatedButton("Зупинити Flow", "ghost")
        stop.clicked.connect(lambda: self._decide("stop"))
        row.addWidget(stop)
        return row

    # ------------------------------------------------------------------
    # Рішення
    # ------------------------------------------------------------------

    @property
    def pinned_skills(self) -> list[str]:
        return [box.text() for box in self.skill_boxes if box.isChecked()]

    def commit_notes(self) -> None:
        """Перенести написане користувачем у звіт перед закриттям вікна."""
        for card in self.point_cards:
            card.commit()

    def _decide(self, decision: str) -> None:
        self.commit_notes()
        self.decision = decision
        self.model = self.model_combo.currentText()
        self.effort = self.effort_combo.currentText()
        if decision == "stop":
            self.reject()
        else:
            self.accept()

    def reject(self) -> None:
        self.commit_notes()
        if not self.decision:
            self.decision = "stop"
        super().reject()
```

- [ ] **Step 4: Додати стилі картки й таблиці diff у `flowai/ui/theme.py`**

У функцію `build_style`, у кінець рядка стилів, додати:

```python
        f"""
        QFrame#rejectionCard {{
            background: {COLORS["surface_raised"]};
            border: 1px solid {COLORS["border"]};
            border-radius: {RADII["md"]}px;
        }}
        QTableWidget#diffTable {{
            background: {COLORS["surface_sunken"]};
            border: 1px solid {COLORS["border"]};
            border-radius: {RADII["sm"]}px;
        }}
        QTableWidget#diffTable QHeaderView::section {{
            background: {COLORS["surface"]};
            color: {COLORS["text_muted"]};
            border: 0;
            padding: 2px 6px;
        }}
        QTreeWidget#skillsTree {{
            background: {COLORS["surface_sunken"]};
            border: 1px solid {COLORS["border"]};
            border-radius: {RADII["sm"]}px;
        }}
        """
```

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -v
```

Очікується: 20 passed.

- [ ] **Step 6: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/calibration_dialog.py flowai/ui/theme.py tests/test_calibration_ui.py && git commit -m "feat(ui): show why a task was rejected and what to change"
```

---

### Task 14: Застосування правок

**Files:**
- Create: `flowai/apply_edits.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `ProposedEdit`, `CalibrationReport` із Task 7; `SkillEntry`, `backup_skill`, `list_skills` із Task 3; `Workflow` із `flowai/models.py`
- Produces:
  - `class AppliedEdit` — `edit: ProposedEdit`, `ok: bool`, `message: str`
  - `apply_edits(report: CalibrationReport, workflow: Workflow, *, skills_root: Path = SKILLS_ROOT, backups_root: Path = BACKUPS_DIR) -> list[AppliedEdit]`
  - `pin_skills(workflow: Workflow, node_id: str, names: list[str], *, skills_root: Path = SKILLS_ROOT) -> list[str]`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_calibration.py`:

```python
from flowai.apply_edits import apply_edits, pin_skills
from flowai.calibration import ProposedEdit


def test_apply_rewrites_a_task_prompt(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="task_prompt",
            task_id="task-1",
            label="Уточнити",
            before="Зробити карту",
            after="Зробити карту з сіткою",
        )
    ]
    applied = apply_edits(report, workflow, skills_root=tmp_path / "skills")
    assert [item.ok for item in applied] == [True]
    tasks = nodes["manager"].config["tasks"]
    assert tasks[0]["prompt"] == "Зробити карту з сіткою"


def test_apply_replaces_a_fragment_inside_node_instructions(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    nodes["executor"].config["instructions"] = "Роби добре.\nНе поспішай."
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="node_instructions",
            node_id=nodes["executor"].id,
            label="Уточнити",
            before="Роби добре.",
            after="Роби добре й перевіряй сітку.",
        )
    ]
    applied = apply_edits(report, workflow, skills_root=tmp_path / "skills")
    assert applied[0].ok is True
    assert nodes["executor"].config["instructions"] == (
        "Роби добре й перевіряй сітку.\nНе поспішай."
    )


def test_apply_rewrites_a_skill_file_and_backs_it_up(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    directory = skills_root / "birds-map"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: birds-map\ndescription: Карта\n---\n\nСтав об'єкти красиво.\n",
        encoding="utf-8",
    )
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            skill="birds-map",
            path=str(directory / "SKILL.md"),
            label="Правило сітки",
            before="Став об'єкти красиво.",
            after="Став об'єкти у вузли сітки.",
        )
    ]
    applied = apply_edits(
        report,
        workflow,
        skills_root=skills_root,
        backups_root=tmp_path / "backups",
    )
    assert applied[0].ok is True
    assert "у вузли сітки" in (directory / "SKILL.md").read_text(encoding="utf-8")
    backups = list((tmp_path / "backups" / "birds-map").iterdir())
    assert len(backups) == 1
    assert "красиво" in (backups[0] / "SKILL.md").read_text(encoding="utf-8")


def test_apply_reports_a_fragment_that_no_longer_matches(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    directory = skills_root / "birds-map"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("зовсім інший текст", encoding="utf-8")
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            skill="birds-map",
            path=str(directory / "SKILL.md"),
            label="Правило",
            before="цього фрагмента там немає",
            after="нове",
        )
    ]
    applied = apply_edits(
        report, workflow, skills_root=skills_root, backups_root=tmp_path / "b"
    )
    assert applied[0].ok is False
    assert "не знайдено" in applied[0].message


def test_apply_refuses_a_path_outside_the_skills_root(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("таємниця", encoding="utf-8")
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            path=str(outside),
            label="Небезпечна правка",
            before="таємниця",
            after="зламано",
        )
    ]
    applied = apply_edits(
        report, workflow, skills_root=tmp_path / "skills", backups_root=tmp_path / "b"
    )
    assert applied[0].ok is False
    assert outside.read_text(encoding="utf-8") == "таємниця"


def test_apply_refuses_a_script_file(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    directory = skills_root / "birds-map" / "scripts"
    directory.mkdir(parents=True)
    script = directory / "run.py"
    script.write_text("print(1)", encoding="utf-8")
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="skill_file",
            path=str(script),
            label="Правка коду",
            before="print(1)",
            after="print(2)",
        )
    ]
    applied = apply_edits(
        report, workflow, skills_root=skills_root, backups_root=tmp_path / "b"
    )
    assert applied[0].ok is False
    assert script.read_text(encoding="utf-8") == "print(1)"


def test_apply_skips_unchecked_edits(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    report = parse_report({}, **{**CONTEXT, "node_id": nodes["stop"].id})
    report.edits = [
        ProposedEdit(
            target="task_prompt",
            task_id="task-1",
            label="Не застосовувати",
            before="Зробити карту",
            after="Не має статися",
            accepted=False,
        )
    ]
    assert apply_edits(report, workflow, skills_root=tmp_path / "s") == []
    assert nodes["manager"].config["tasks"][0]["prompt"] == "Зробити карту"


def test_pin_skills_adds_them_to_the_executor(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "image-cutout").mkdir(parents=True)
    (skills_root / "image-cutout" / "SKILL.md").write_text(
        "---\nname: image-cutout\ndescription: Фон\n---\n", encoding="utf-8"
    )
    workflow, nodes = build_flow(tmp_path)
    pinned = pin_skills(
        workflow, nodes["executor"].id, ["image-cutout"], skills_root=skills_root
    )
    assert pinned == ["image-cutout"]
    assert nodes["executor"].config["skills"][0]["name"] == "image-cutout"


def test_pin_skills_ignores_unknown_names(tmp_path: Path) -> None:
    workflow, nodes = build_flow(tmp_path)
    assert (
        pin_skills(
            workflow,
            nodes["executor"].id,
            ["не-існує"],
            skills_root=tmp_path / "skills",
        )
        == []
    )
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -k apply -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.apply_edits'`.

- [ ] **Step 3: Написати `flowai/apply_edits.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .calibration import CalibrationReport, ProposedEdit
from .models import Workflow, normalize_managed_tasks
from .skills import BACKUPS_DIR, SKILLS_ROOT, SkillEntry, backup_skill, scan_skills

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AppliedEdit:
    """Результат однієї спроби застосувати правку."""

    edit: ProposedEdit
    ok: bool
    message: str = ""


def _skill_for_path(path: Path, skills_root: Path) -> SkillEntry | None:
    for entry in scan_skills(skills_root):
        try:
            path.relative_to(entry.path)
        except ValueError:
            continue
        return entry
    return None


def _replace_once(text: str, before: str, after: str) -> tuple[str, str]:
    """Замінити рівно один випадок; повернути новий текст і повідомлення."""
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
    if path.suffix.casefold() != ".md":
        return AppliedEdit(edit, False, "Правити можна лише Markdown скіла")
    entry = _skill_for_path(path, skills_root)
    if entry is None:
        return AppliedEdit(
            edit, False, "Файл лежить поза каталогом скілів — правку відхилено"
        )
    if not entry.editable:
        return AppliedEdit(
            edit, False, f"Скіл «{entry.name}» встановив Codex — його не правимо"
        )
    if "scripts" in path.parts or "assets" in path.parts:
        return AppliedEdit(edit, False, "scripts/ і assets/ правити заборонено")
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
                # Промпт задачі часто переписують цілком, а не фрагментом.
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
    """Записати відмічені правки: спершу скіли, потім промпти.

    Кожна правка застосовується окремо. Якщо файл змінився після аналізу
    і фрагмент «було» більше не збігається — правка не проходить, але
    решта застосовується. Мовчазних часткових записів не буває.
    """
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
    """Закріпити названі скіли за нодою; невідомі імена ігноруються."""
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
```

- [ ] **Step 4: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration.py -v
```

Очікується: 31 passed.

- [ ] **Step 5: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/apply_edits.py tests/test_calibration.py && git commit -m "feat(calibration): apply accepted edits to skills, tasks and node prompts"
```

---

### Task 15: Підключення вікна до головного вікна

**Files:**
- Modify: `flowai/ui/main_window.py` (`_show_pending_intervention`, новий `_show_calibration`)
- Test: `tests/test_calibration_ui.py`

**Interfaces:**
- Consumes: `CalibrationDialog` із Task 13; `apply_edits`, `pin_skills` із Task 14; `load_report`, `save_report` із Task 7
- Produces:
  - `MainWindow._show_calibration(session: WorkspaceSession, request: dict[str, Any]) -> None`
  - `MainWindow.calibration_models: list[str]` = `["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]`
  - Відповідь у `session.intervention_responses[node_id]` — `{"action": "retry_task"}` для «Застосувати» / «Regenerate» і `{"action": "continue"}` для «Хай спробує сам»

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_calibration_ui.py`:

```python
from pathlib import Path

from flowai.calibration import save_report
from flowai.models import FlowEdge, FlowNode, Workflow
from flowai.ui.main_window import MainWindow


def build_session_workflow() -> tuple[Workflow, dict[str, FlowNode]]:
    workflow = Workflow(name="Карти")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "task-1", "prompt": "Зробити карту", "attachments": []}
    ]
    executor = FlowNode.create("executor")
    reviewer = FlowNode.create("task_reviewer")
    result = FlowNode.create("result")
    stop = FlowNode.create("calibrator")
    workflow.nodes.extend([manager, executor, reviewer, result, stop])
    workflow.edges.extend(
        [
            FlowEdge.create(manager.id, executor.id, "next"),
            FlowEdge.create(executor.id, reviewer.id),
            FlowEdge.create(reviewer.id, result.id),
            FlowEdge.create(result.id, manager.id, "true"),
            FlowEdge.create(result.id, executor.id, "false"),
            FlowEdge.create(result.id, stop.id, "false"),
        ]
    )
    return workflow, {
        "manager": manager,
        "executor": executor,
        "stop": stop,
    }


def test_apply_writes_the_task_prompt_and_queues_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    workflow, nodes = build_session_workflow()
    session = window.current_workspace
    session.workflow = workflow
    session.run_directory = tmp_path
    window.scene.set_workflow(workflow)
    report = make_report()
    report.node_id = nodes["stop"].id
    report.edits[0].task_id = "task-1"
    save_report(report, tmp_path)
    request = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "report_path": str(tmp_path / "calibration.json"),
    }
    monkeypatch.setattr(
        CalibrationDialog, "exec", lambda self: self._decide("apply") or 1
    )
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    window._show_calibration(session, request)
    assert nodes["manager"].config["tasks"][0]["prompt"] == (
        "Зробити карту з об'єктами у вузлах"
    )
    assert session.intervention_responses[nodes["stop"].id] == {
        "action": "retry_task"
    }
    window.close()


def test_retry_continues_without_touching_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    workflow, nodes = build_session_workflow()
    session = window.current_workspace
    session.workflow = workflow
    session.run_directory = tmp_path
    window.scene.set_workflow(workflow)
    report = make_report()
    report.node_id = nodes["stop"].id
    request = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "report_path": str(tmp_path / "calibration.json"),
    }
    monkeypatch.setattr(
        CalibrationDialog, "exec", lambda self: self._decide("retry") or 1
    )
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    window._show_calibration(session, request)
    assert nodes["manager"].config["tasks"][0]["prompt"] == "Зробити карту"
    assert session.intervention_responses[nodes["stop"].id] == {
        "action": "continue"
    }
    window.close()


def test_user_notes_are_saved_back_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    workflow, nodes = build_session_workflow()
    session = window.current_workspace
    session.workflow = workflow
    session.run_directory = tmp_path
    window.scene.set_workflow(workflow)
    report = make_report()
    report.node_id = nodes["stop"].id
    request = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "report_path": str(tmp_path / "calibration.json"),
    }

    def choose(dialog: CalibrationDialog) -> int:
        dialog.point_cards[0].note.setPlainText("Моє бачення")
        dialog._decide("retry")
        return 1

    monkeypatch.setattr(CalibrationDialog, "exec", choose)
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    window._show_calibration(session, request)
    saved = load_report(tmp_path)
    assert saved is not None
    assert saved.points[0].user_note == "Моє бачення"
    window.close()


def test_pinned_skill_lands_on_the_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "image-cutout").mkdir(parents=True)
    (skills_root / "image-cutout" / "SKILL.md").write_text(
        "---\nname: image-cutout\ndescription: Фон\n---\n", encoding="utf-8"
    )
    window = MainWindow()
    workflow, nodes = build_session_workflow()
    session = window.current_workspace
    session.workflow = workflow
    session.run_directory = tmp_path
    window.scene.set_workflow(workflow)
    report = make_report()
    report.node_id = nodes["stop"].id
    request = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "report_path": str(tmp_path / "calibration.json"),
    }

    def choose(dialog: CalibrationDialog) -> int:
        dialog.skill_boxes[0].setChecked(True)
        dialog._decide("apply")
        return 1

    monkeypatch.setattr(CalibrationDialog, "exec", choose)
    monkeypatch.setattr(MainWindow, "run_workflow", lambda self, resume=False: None)
    monkeypatch.setattr("flowai.ui.main_window.SKILLS_ROOT", skills_root)
    window._show_calibration(session, request)
    assert nodes["executor"].config["skills"][0]["name"] == "image-cutout"
    window.close()


def test_stop_cancels_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    workflow, nodes = build_session_workflow()
    session = window.current_workspace
    session.workflow = workflow
    session.run_directory = tmp_path
    window.scene.set_workflow(workflow)
    report = make_report()
    report.node_id = nodes["stop"].id
    request = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "report_path": str(tmp_path / "calibration.json"),
    }
    monkeypatch.setattr(CalibrationDialog, "exec", lambda self: 0)
    window._show_calibration(session, request)
    assert session.run_state == "cancelled"
    assert session.pending_intervention is None
    window.close()
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -k "apply or retry or pinned or stop or notes" -v
```

Очікується: `AttributeError: 'MainWindow' object has no attribute '_show_calibration'`.

- [ ] **Step 3: Додати обробник у `flowai/ui/main_window.py`**

Додати імпорти:

```python
from ..apply_edits import apply_edits, pin_skills
from ..calibration import CalibrationReport, save_report
from ..skills import SKILLS_ROOT
from .calibration_dialog import CalibrationDialog
```

Додати поле у `MainWindow.__init__`:

```python
        self.calibration_models = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
```

Додати метод поряд із `_show_pending_intervention`:

```python
    def _show_calibration(
        self, session: WorkspaceSession, request: dict[str, Any]
    ) -> None:
        """Показати, чому рев'ювер відхилив роботу, і що з цим робити."""
        workflow = session.workflow
        if workflow is None:
            return
        report = CalibrationReport.from_dict(request.get("report") or {})
        node_id = str(request.get("node_id") or "")
        executor = next(
            (node for node in workflow.nodes_of_kind("executor")), None
        )
        dialog = CalibrationDialog(
            report,
            self,
            models=self.calibration_models,
            default_model=str(
                executor.config.get("model", "gpt-5.6-terra")
                if executor
                else "gpt-5.6-terra"
            ),
            default_effort=str(
                executor.config.get("reasoning_effort", "medium")
                if executor
                else "medium"
            ),
        )
        self.intervention_dialog_open = True
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.intervention_dialog_open = False

        directory = session.run_directory
        if directory is not None:
            save_report(dialog.report, directory)

        if not accepted or dialog.decision == "stop":
            session.run_state = "cancelled"
            session.pending_intervention = None
            if node_id:
                self.scene.set_attention(node_id, False)
            self._append_session_log(session, "■ Flow зупинено користувачем")
            self._save_run_log_for_session(session, "cancelled")
            if directory is not None:
                clear_checkpoint(directory)
            self._refresh_workspace_sidebar()
            return

        if dialog.decision in {"apply", "regenerate"}:
            results = apply_edits(dialog.report, workflow)
            for item in results:
                marker = "✔" if item.ok else "✖"
                self._append_session_log(
                    session, f"{marker} {item.edit.label}: {item.message}"
                )
            if dialog.pinned_skills and executor is not None:
                pinned = pin_skills(
                    workflow,
                    executor.id,
                    dialog.pinned_skills,
                    skills_root=SKILLS_ROOT,
                )
                if pinned:
                    self._append_session_log(
                        session, "Закріплено скіли: " + ", ".join(pinned)
                    )
            session.dirty = True
            self._mark_dirty()

        if dialog.decision == "regenerate":
            self._start_regeneration(session, dialog)
            return

        action = "retry_task" if dialog.decision == "apply" else "continue"
        session.intervention_responses[node_id] = {"action": action}
        session.pending_intervention = None
        session.run_state = "idle"
        if node_id:
            self.scene.set_attention(node_id, False)
        self._refresh_workspace_sidebar()
        self.run_workflow(resume=True)
```

Тимчасова заглушка `_start_regeneration` (буде замінена в Task 18):

```python
    def _start_regeneration(
        self, session: WorkspaceSession, dialog: CalibrationDialog
    ) -> None:
        """Запустити GrillMe на основі звіту калібрації."""
        session.intervention_responses[str(dialog.report.node_id)] = {
            "action": "retry_task"
        }
        session.pending_intervention = None
        session.run_state = "idle"
        self._refresh_workspace_sidebar()
        self.run_workflow(resume=True)
```

- [ ] **Step 4: Направити запит типу `calibration` у новий обробник**

У `_show_pending_intervention`, одразу після `request = session.pending_intervention`, додати:

```python
        if request.get("type") == "calibration":
            self.intervention_dialog_open = True
            try:
                self._show_calibration(session, request)
            finally:
                self.intervention_dialog_open = False
            return
```

і прибрати `calibration` із гілки «Невідомий тип запиту», лишивши перевірку як є.

У `_run_thread_finished` зняти обмеження, яке відкриває вікно лише для не-`result_confirmation`, додавши `calibration` у дозволені:

```python
        if (
            session.id == self.current_workspace_id
            and session.run_state == "needs_attention"
            and session.pending_intervention is not None
            and session.pending_intervention.get("type") != "result_confirmation"
        ):
            QTimer.singleShot(0, self._show_pending_intervention)
```

(умова вже підходить — `calibration` не дорівнює `result_confirmation`.)

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -v
```

Очікується: 25 passed.

- [ ] **Step 6: Прогнати весь набір, лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m pytest -q
```

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/main_window.py tests/test_calibration_ui.py && git commit -m "feat(ui): open the calibration window when the reviewer rejects a task"
```

---
## Фаза 4 — Нотифікація Windows

### Task 16: Тост із кнопками та ярлик у Меню «Пуск»

**Files:**
- Create: `flowai/ui/toast.py`
- Modify: `install.ps1`
- Modify: `flowai/ui/branding.py`
- Test: `tests/test_toast.py`

**Interfaces:**
- Consumes: `APP_USER_MODEL_ID` із `flowai/ui/branding.py`
- Produces:
  - `flowai/ui/branding.py`: `start_menu_shortcut() -> Path` — шлях `%APPDATA%/Microsoft/Windows/Start Menu/Programs/FlowAI.lnk`
  - `flowai/ui/toast.py`:
    - `class ToastAction` — `id: str`, `label: str`
    - `build_toast_xml(title: str, body: str, actions: list[ToastAction], *, tag: str) -> str`
    - `class Toaster(QObject)` — сигнал `activated = Signal(str, str)` (`tag`, `action_id`); методи `show(title, body, *, tag, actions) -> bool`, `available() -> bool`
    - Відкат: якщо WinRT недоступний, `Toaster` віддає `False` і викликач показує трей-балон

- [ ] **Step 1: Написати падаючий тест**

Створити `tests/test_toast.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.branding import APP_USER_MODEL_ID, start_menu_shortcut
from flowai.ui.toast import ToastAction, Toaster, build_toast_xml


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_xml_carries_title_body_and_tag() -> None:
    xml = build_toast_xml(
        "FlowAI", "Задачу відхилено", [], tag="session-1"
    )
    assert "<toast" in xml
    assert "FlowAI" in xml
    assert "Задачу відхилено" in xml
    assert 'launch="session-1|open"' in xml


def test_xml_renders_every_action() -> None:
    xml = build_toast_xml(
        "FlowAI",
        "Задачу відхилено",
        [ToastAction("edits", "Показати правки")],
        tag="session-1",
    )
    assert 'content="Показати правки"' in xml
    assert 'arguments="session-1|edits"' in xml


def test_xml_escapes_markup_in_the_body() -> None:
    xml = build_toast_xml("FlowAI", 'Пункт <b> та "лапки"', [], tag="s")
    assert "<b>" not in xml.split("<text>")[1]
    assert "&lt;b&gt;" in xml


def test_toaster_reports_availability_without_crashing() -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    assert isinstance(toaster.available(), bool)


def test_toaster_show_returns_false_when_winrt_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    monkeypatch.setattr(toaster, "_notifier", None)
    monkeypatch.setattr(toaster, "_available", False)
    assert toaster.show("FlowAI", "тест", tag="s", actions=[]) is False


def test_start_menu_shortcut_points_into_programs() -> None:
    target = start_menu_shortcut()
    assert target.name == "FlowAI.lnk"
    assert "Start Menu" in str(target) or "Меню" in str(target)


def test_activation_signal_parses_the_argument() -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    seen: list[tuple[str, str]] = []
    toaster.activated.connect(lambda tag, action: seen.append((tag, action)))
    toaster._handle_argument("session-7|edits")
    assert seen == [("session-7", "edits")]


def test_activation_without_an_action_defaults_to_open() -> None:
    toaster = Toaster(APP_USER_MODEL_ID)
    seen: list[tuple[str, str]] = []
    toaster.activated.connect(lambda tag, action: seen.append((tag, action)))
    toaster._handle_argument("session-7")
    assert seen == [("session-7", "open")]
```

- [ ] **Step 2: Запустити тест і переконатись, що він падає**

```bash
.venv/Scripts/python.exe -m pytest tests/test_toast.py -v
```

Очікується: `ModuleNotFoundError: No module named 'flowai.ui.toast'`.

- [ ] **Step 3: Додати шлях ярлика у `flowai/ui/branding.py`**

```python
def start_menu_shortcut() -> Path:
    """Ярлик у Меню «Пуск» — Windows вимагає його для справжніх тостів.

    Тост показується від імені AppUserModelID, а той має бути прописаний
    у властивостях ярлика в Programs. Без ярлика система мовчки ковтає
    сповіщення.
    """
    programs = (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
    )
    return programs / "FlowAI.lnk"
```

- [ ] **Step 4: Написати `flowai/ui/toast.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from PySide6.QtCore import QObject, Signal

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ToastAction:
    """Кнопка в тості: id повертається сигналом при натисканні."""

    id: str
    label: str


def build_toast_xml(
    title: str, body: str, actions: list[ToastAction], *, tag: str
) -> str:
    """Скласти XML тоста. Аргумент активації — «tag|action»."""
    buttons = "".join(
        "<action activationType='foreground' "
        f"content={quoteattr(action.label)} "
        f"arguments={quoteattr(f'{tag}|{action.id}')} />"
        for action in actions
    )
    return (
        f"<toast launch={quoteattr(f'{tag}|open')} activationType='foreground'>"
        "<visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text>"
        f"<text>{escape(body)}</text>"
        "</binding></visual>"
        f"<actions>{buttons}</actions>"
        "</toast>"
    )


class Toaster(QObject):
    """Справжній Windows-тост; за відсутності WinRT мовчки віддає False."""

    activated = Signal(str, str)

    def __init__(self, app_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.app_id = app_id
        self._notifier: object | None = None
        self._available = False
        self._documents: object | None = None
        self._toast_type: object | None = None
        self._load()

    def _load(self) -> None:
        try:
            from winrt.windows.data.xml.dom import XmlDocument
            from winrt.windows.ui.notifications import (
                ToastNotification,
                ToastNotificationManager,
            )
        except (ImportError, OSError):
            LOGGER.info("WinRT недоступний — залишаємось на трей-балоні")
            return
        try:
            self._notifier = ToastNotificationManager.create_toast_notifier(
                self.app_id
            )
        except OSError:
            LOGGER.info("Не вдалося створити ToastNotifier для %s", self.app_id)
            return
        self._documents = XmlDocument
        self._toast_type = ToastNotification
        self._available = True

    def available(self) -> bool:
        return self._available

    def show(
        self,
        title: str,
        body: str,
        *,
        tag: str,
        actions: list[ToastAction],
    ) -> bool:
        """Показати тост. False означає «відкотись на трей-балон»."""
        if not self._available or self._notifier is None:
            return False
        xml = build_toast_xml(title, body, actions, tag=tag)
        try:
            document = self._documents()  # type: ignore[misc]
            document.load_xml(xml)
            toast = self._toast_type(document)  # type: ignore[misc]
            toast.add_activated(
                lambda _sender, args: self._handle_argument(
                    str(getattr(args, "arguments", "") or tag)
                )
            )
            self._notifier.show(toast)  # type: ignore[attr-defined]
        except OSError:
            LOGGER.exception("Тост не показався")
            return False
        return True

    def _handle_argument(self, argument: str) -> None:
        tag, _separator, action = argument.partition("|")
        self.activated.emit(tag, action or "open")
```

- [ ] **Step 5: Додати `winrt` у залежності**

У `pyproject.toml`, у `dependencies`, додати:

```toml
    "winrt-Windows.UI.Notifications>=3.0; sys_platform == 'win32'",
    "winrt-Windows.Data.Xml.Dom>=3.0; sys_platform == 'win32'",
```

```bash
.venv/Scripts/python.exe -m pip install "winrt-Windows.UI.Notifications" "winrt-Windows.Data.Xml.Dom"
```

Якщо встановлення не вдається — це не блокер: `Toaster.available()` поверне `False` і FlowAI працюватиме на трей-балоні. Зафіксуйте це в журналі запуску й ідіть далі.

- [ ] **Step 6: Створювати ярлик із AUMID в `install.ps1`**

Замінити блок створення ярлика на:

```powershell
Write-Host "Створення ярлика FlowAI..."
$pythonwPath = Join-Path $venvPath "Scripts\pythonw.exe"
$iconDirectory = Join-Path $env:LOCALAPPDATA "FlowAI\assets"
$iconPath = Join-Path $iconDirectory "FlowAI.ico"
New-Item -ItemType Directory -Force -Path $iconDirectory | Out-Null
& $pythonPath -m flowai.ui.branding $iconPath

$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $programs | Out-Null
$targets = @((Join-Path $flowaiRoot "FlowAI.lnk"), (Join-Path $programs "FlowAI.lnk"))
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $targets) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonwPath
    $shortcut.Arguments = "-m flowai"
    $shortcut.WorkingDirectory = $flowaiRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = "FlowAI"
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Save()
}

# Windows показує тости лише від застосунку, чий AppUserModelID прописано
# у властивостях ярлика в Меню "Пуск". Без цього кроку сповіщення зникають.
$startMenuShortcut = Join-Path $programs "FlowAI.lnk"
& $pythonPath -m flowai.ui.branding --set-app-id $startMenuShortcut "FlowAI.Desktop"
```

- [ ] **Step 7: Реалізувати запис AUMID у `flowai/ui/branding.py`**

Додати функцію та розширити `_main`:

```python
def set_shortcut_app_id(shortcut: Path, app_id: str) -> bool:
    """Прописати System.AppUserModel.ID у властивості ярлика.

    Це той самий ідентифікатор, що ставить configure_windows_app_id;
    Windows зіставляє їх і лише тоді дозволяє застосунку слати тости.
    """
    if sys.platform != "win32":
        return False
    script = (
        "$shell = New-Object -ComObject Shell.Application; "
        f"$folder = $shell.Namespace('{shortcut.parent}'); "
        f"$item = $folder.ParseName('{shortcut.name}'); "
        "$link = $item.GetLink; "
        f"$link.SetProperty('System.AppUserModel.ID', '{app_id}'); "
        "$link.Save()"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return completed.returncode == 0


def _main(arguments: list[str]) -> int:
    if len(arguments) == 4 and arguments[1] == "--set-app-id":
        return 0 if set_shortcut_app_id(Path(arguments[2]), arguments[3]) else 1
    if len(arguments) != 2:
        print(
            "Usage: python -m flowai.ui.branding <target.ico>\n"
            "       python -m flowai.ui.branding --set-app-id <link> <id>",
            file=sys.stderr,
        )
        return 2
    export_windows_icon(Path(arguments[1]))
    return 0
```

Додати імпорт `import subprocess` на початок файлу.

- [ ] **Step 8: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_toast.py -v
```

Очікується: 8 passed.

- [ ] **Step 9: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/toast.py flowai/ui/branding.py install.ps1 pyproject.toml tests/test_toast.py && git commit -m "feat(notify): send a real Windows toast with action buttons"
```

---

### Task 17: Нотифікація про відхилення

**Files:**
- Modify: `flowai/ui/main_window.py` (`_build_notifications`, `_notify_user`, `_notification_clicked`, `_run_thread_finished`)
- Test: `tests/test_calibration_ui.py`

**Interfaces:**
- Consumes: `Toaster`, `ToastAction` із Task 16
- Produces:
  - `MainWindow.toaster: Toaster`
  - `MainWindow._notify_user(..., actions: list[ToastAction] | None = None)` — спершу тост, за невдачі трей-балон
  - `MainWindow._toast_activated(tag: str, action: str) -> None` — `open` відкриває проєкт, `edits` ще й відкриває вкладку правок
  - `MainWindow.calibration_open_tab: int` — індекс вкладки, з якої відкрити вікно

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_calibration_ui.py`:

```python
from flowai.ui.toast import ToastAction


def test_notification_prefers_the_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    window = MainWindow()
    session = window.current_workspace
    sent: list[tuple[str, str, list[ToastAction]]] = []
    monkeypatch.setattr(
        window.toaster,
        "show",
        lambda title, body, *, tag, actions: sent.append((title, body, actions))
        or True,
    )
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    window._notify_user(
        session,
        "FlowAI",
        "Задачу відхилено",
        actions=[ToastAction("edits", "Показати правки")],
    )
    assert sent[0][0] == "FlowAI"
    assert sent[0][2][0].id == "edits"
    window.close()


def test_notification_falls_back_to_the_tray(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow()
    session = window.current_workspace
    balloons: list[str] = []
    monkeypatch.setattr(
        window.toaster, "show", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        window.tray_icon,
        "showMessage",
        lambda title, message, icon, timeout: balloons.append(message),
    )
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    monkeypatch.setattr(
        "flowai.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
        staticmethod(lambda: True),
    )
    window._notify_user(session, "FlowAI", "Задачу відхилено")
    assert balloons == ["Задачу відхилено"]
    window.close()


def test_toast_action_opens_the_edits_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    window = MainWindow()
    session = window.current_workspace
    window._notification_target = (session.id, "node-1")
    monkeypatch.setattr(MainWindow, "select_workspace", lambda self, _id: None)
    window._toast_activated(session.id, "edits")
    assert window.calibration_open_tab == 1
    window._toast_activated(session.id, "open")
    assert window.calibration_open_tab == 0
    window.close()


def test_calibration_notification_names_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    workflow, nodes = build_session_workflow()
    session = window.current_workspace
    session.workflow = workflow
    session.run_directory = tmp_path
    report = make_report()
    report.node_id = nodes["stop"].id
    session.run_state = "needs_attention"
    session.pending_intervention = {
        "type": "calibration",
        "node_id": nodes["stop"].id,
        "report": report.to_dict(),
        "question": "Рев'ювер відхилив «Зробити карту»",
    }
    sent: list[str] = []
    monkeypatch.setattr(
        window, "_notify_user", lambda *args, **kwargs: sent.append(args[2])
    )
    monkeypatch.setattr(window, "_update_workspace_actions", lambda: None)
    window._run_thread_finished(session.id)
    assert any("Зробити карту" in message for message in sent)
    window.close()
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -k "notification or toast" -v
```

Очікується: `AttributeError: 'MainWindow' object has no attribute 'toaster'`.

- [ ] **Step 3: Підключити `Toaster` у `flowai/ui/main_window.py`**

Додати імпорти:

```python
from .toast import ToastAction, Toaster
from ..ui.branding import APP_USER_MODEL_ID
```

У `_build_notifications` додати після створення `self.tray_icon`:

```python
        self.toaster = Toaster(APP_USER_MODEL_ID, self)
        self.toaster.activated.connect(self._toast_activated)
        self.calibration_open_tab = 0
```

Замінити `_notify_user`:

```python
    def _notify_user(
        self,
        session: WorkspaceSession,
        title: str,
        message: str,
        *,
        node_id: str = "",
        warning: bool = False,
        actions: list[ToastAction] | None = None,
    ) -> None:
        """Сповістити, коли FlowAI не в фокусі: спершу тост, потім трей."""
        if self.isActiveWindow():
            return
        self._notification_target = (session.id, node_id)
        if self.toaster.show(
            title, message, tag=session.id, actions=list(actions or [])
        ):
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.tray_icon.showMessage(title, message, icon, 12_000)
```

Додати обробник активації тоста:

```python
    @Slot(str, str)
    def _toast_activated(self, tag: str, action: str) -> None:
        """Клік по тосту: підняти вікно, відкрити проєкт, показати правки."""
        self.calibration_open_tab = 1 if action == "edits" else 0
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.select_workspace(tag)
        QTimer.singleShot(0, lambda: self._show_pending_intervention(True))
```

- [ ] **Step 4: Слати нотифікацію саме про відхилення**

У `_run_thread_finished` замінити блок нотифікації:

```python
        if session.run_state == "needs_attention" and session.pending_intervention:
            request = session.pending_intervention
            if request.get("type") == "calibration":
                report = request.get("report") or {}
                task = str(report.get("task_title") or "завдання")
                self._notify_user(
                    session,
                    "FlowAI: рев'ювер відхилив роботу",
                    f"{session.display_name}: «{task}» повернуто на переробку",
                    node_id=str(request.get("node_id") or ""),
                    warning=True,
                    actions=[ToastAction("edits", "Показати правки")],
                )
            else:
                self._notify_user(
                    session,
                    "FlowAI очікує на підтвердження",
                    f"{session.display_name}: "
                    f"{request.get('question', 'Потрібна відповідь')}",
                    node_id=str(request.get("node_id") or ""),
                    warning=True,
                )
```

- [ ] **Step 5: Відкривати потрібну вкладку у вікні калібрації**

У `_show_calibration`, одразу після створення `dialog`, додати:

```python
        dialog.tabs.setCurrentIndex(self.calibration_open_tab)
        self.calibration_open_tab = 0
```

- [ ] **Step 6: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_calibration_ui.py -v
```

Очікується: 29 passed.

- [ ] **Step 7: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/main_window.py tests/test_calibration_ui.py && git commit -m "feat(notify): tell the user when the reviewer rejects a task"
```

---

## Фаза 5 — Regenerate Prompt

### Task 18: Контекст калібрації в GrillMe

**Files:**
- Modify: `flowai/grill.py`
- Test: `tests/test_grill.py`

**Interfaces:**
- Consumes: `CalibrationReport` із Task 7
- Produces:
  - `flowai.grill.MATERIALS_QUESTION: str` — «Використовувати згенеровані матеріали чи розпочати розробку спочатку?»
  - `flowai.grill.MATERIALS_OPTIONS: list[str]` — два варіанти плюс `OWN_ANSWER` останнім
  - `GrillSession(..., calibration: CalibrationReport | None = None, generated_files: list[str] | None = None)`
  - `GrillSession.next_question()` першим викликом віддає `MATERIALS_QUESTION`, коли `calibration` передано
  - `GrillSession._calibration_text() -> str` — блок про відхилення й нотатки користувача
  - `GrillSession.finish()` включає в промпт вимогу обов'язково переписати `calibration.task_id`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_grill.py`:

```python
from flowai.calibration import CalibrationReport, RejectionPoint
from flowai.grill import (
    MATERIALS_OPTIONS,
    MATERIALS_QUESTION,
    OWN_ANSWER,
    GrillSession,
)


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
    assert MATERIALS_QUESTION in codex.calls[0]["prompt"]


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
    prompt = codex.calls[0]["prompt"]
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
    assert "C:/out/map.png" in codex.calls[0]["prompt"]


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
    assert "task-1" in codex.calls[0]["prompt"]
    assert "обов'язково" in codex.calls[0]["prompt"]


def test_session_without_calibration_behaves_as_before(tmp_path: Path) -> None:
    codex = FakeCodex(
        ['{"done": false, "question": "Перше?", "options": ["так"]}']
    )
    session = GrillSession(make_workflow(), codex, "gpt-5.6-terra", tmp_path)
    question = session.next_question()
    assert question is not None
    assert question.text == "Перше?"
```

Якщо в `tests/test_grill.py` ще немає хелперів `FakeCodex` і `make_workflow`, додати їх на початок файлу:

```python
class FakeCodex:
    """Мінімальний двійник CodexAdapter для GrillSession."""

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
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_grill.py -v
```

Очікується: `ImportError: cannot import name 'MATERIALS_QUESTION'`.

- [ ] **Step 3: Дописати `flowai/grill.py`**

Додати константи після `OWN_ANSWER`:

```python
MATERIALS_QUESTION = (
    "Використовувати згенеровані матеріали чи розпочати розробку спочатку?"
)
MATERIALS_OPTIONS = [
    "Використати згенеровані матеріали й доробити їх",
    "Розпочати розробку спочатку, не спираючись на них",
    OWN_ANSWER,
]
FRESH_START_MARKER = "Розпочати розробку спочатку"
```

Розширити `GrillSession.__init__`:

```python
    def __init__(
        self,
        workflow: Workflow,
        codex: CodexAdapter,
        model: str,
        workspace: Path,
        reasoning_effort: str = "medium",
        calibration: Any | None = None,
        generated_files: list[str] | None = None,
    ) -> None:
        ...
        self.calibration = calibration
        self.generated_files = [str(path) for path in generated_files or []]
        self._asked_materials = False
```

Додати метод, що складає блок калібрації:

```python
    def _calibration_text(self) -> str:
        """Чому рев'ювер відхилив роботу і що про це думає користувач."""
        report = self.calibration
        if report is None:
            return ""
        lines = [
            "# Чому попередня спроба не пройшла перевірку",
            f"Завдання: {report.task_title} (id {report.task_id})",
        ]
        if report.verdict_reason.strip():
            lines.append(f"Вердикт рев'ювера: {report.verdict_reason}")
        if report.root_cause.strip():
            lines.append(f"Причина: {report.root_cause}")
        for index, point in enumerate(report.points, start=1):
            lines.append(f"{index}. {point.title}")
            if point.detail.strip():
                lines.append(f"   {point.detail}")
            for image in point.images:
                lines.append(f"   Ілюстрація: {image.path} — {image.note}")
        notes = report.user_notes_text()
        if notes:
            lines.extend(
                [
                    "",
                    "# Бачення користувача — це рішення, а не побажання",
                    notes,
                ]
            )
        if report.skills_used:
            lines.append(
                "\nАгент працював зі скілами: " + ", ".join(report.skills_used)
            )
        if report.skills_missing:
            lines.append(
                "Рев'ювер вважає, що бракувало скілів: "
                + ", ".join(report.skills_missing)
            )
        if self.generated_files:
            lines.extend(
                [
                    "",
                    "# Файли, які створила попередня спроба",
                    *(f"- {path}" for path in self.generated_files),
                ]
            )
        return "\n".join(lines)
```

Змінити `next_question`, щоб перше питання не йшло в агента:

```python
    def next_question(self) -> GrillQuestion | None:
        if self._done:
            return None
        if self.calibration is not None and not self._asked_materials:
            # Це рішення користувача, а не агента: чи спиратись на те, що
            # вже згенеровано. Питати його в агента безглуздо.
            self._asked_materials = True
            self._last_question = MATERIALS_QUESTION
            return GrillQuestion(
                text=MATERIALS_QUESTION,
                options=list(MATERIALS_OPTIONS),
                rationale=(
                    "Від цієї відповіді залежить увесь наступний промпт"
                ),
            )
        prompt = (
            f"{self._flow_context()}\n\n"
            f"{self._calibration_text()}\n\n"
            f"# Що вже з'ясовано\n{self._history_text()}\n\n"
            "Постав наступне питання або поверни done=true."
        )
        ...
```

Змінити `finish`:

```python
    def finish(self) -> GrillOutcome:
        demand = ""
        if self.calibration is not None:
            demand = (
                f"Промпт завдання {self.calibration.task_id} треба переписати "
                "обов'язково — саме воно не пройшло перевірку. Решту завдань "
                "чіпай лише тоді, коли домовленості справді їх стосуються.\n"
            )
            if any(
                FRESH_START_MARKER in answer for _question, answer in self.history
            ):
                demand += (
                    "Користувач вирішив почати спочатку: у промпті має бути "
                    "явна вказівка не спиратись на вже створені файли, "
                    "з їхнім переліком. Самі файли не чіпаємо.\n"
                )
        prompt = (
            f"{self._flow_context()}\n\n"
            f"{self._calibration_text()}\n\n"
            f"# Домовленості з користувачем\n{self._history_text()}\n\n"
            f"{demand}"
            "Перепиши промпти тих завдань, яких стосуються домовленості. "
            "Не чіпай завдання, яких це не стосується — не включай їх у tasks. "
            "Збережи мову й структуру оригіналу, додай конкретику. "
            "У summary стисло перекажи ухвалені рішення."
        )
        ...
```

Додати `from typing import Any` до імпортів, якщо його там ще немає.

- [ ] **Step 4: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_grill.py -v
```

Очікується: усі проходять, зокрема 8 нових.

- [ ] **Step 5: Перевірити лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/grill.py tests/test_grill.py && git commit -m "feat(grill): start the interview from the rejection report"
```

---

### Task 19: Кнопка Regenerate Prompt веде в GrillMe

**Files:**
- Modify: `flowai/ui/grill_dialog.py`
- Modify: `flowai/ui/main_window.py` (`_start_regeneration`)
- Test: `tests/test_grill_ui.py`

**Interfaces:**
- Consumes: `GrillDialog`, `GrillWorker` із `flowai/ui/grill_dialog.py`; `CalibrationReport`
- Produces:
  - `GrillWorker(..., calibration: CalibrationReport | None = None, generated_files: list[str] | None = None)`
  - `GrillDialog(..., calibration: CalibrationReport | None = None, generated_files: list[str] | None = None)`
  - `MainWindow._start_regeneration(session, dialog)` — відкриває `GrillDialog` із моделлю й складністю з вікна калібрації, застосовує `GrillOutcome` до задач і продовжує Flow

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_grill_ui.py`:

```python
from flowai.calibration import CalibrationReport, RejectionPoint
from flowai.grill import MATERIALS_QUESTION
from flowai.ui.grill_dialog import GrillDialog


def make_calibration() -> CalibrationReport:
    return CalibrationReport(
        node_id="stop-1",
        node_title="Calibration Stop",
        task_id="task-1",
        task_title="Зробити карту",
        workflow_name="Карти",
        attempt=1,
        threshold=1,
        points=[RejectionPoint(title="Сітка з'їхала")],
    )


def test_dialog_passes_calibration_to_the_worker(tmp_path: Path) -> None:
    workflow = make_workflow()
    dialog = GrillDialog(
        workflow,
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
        generated_files=["C:/out/map.png"],
    )
    dialog._start_worker()
    assert dialog._worker is not None
    assert dialog._worker.calibration is not None
    assert dialog._worker.generated_files == ["C:/out/map.png"]
    dialog._stop_thread()
    dialog.close()


def test_dialog_titles_itself_as_a_regeneration(tmp_path: Path) -> None:
    dialog = GrillDialog(
        make_workflow(),
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    assert "Regenerate" in dialog.windowTitle()
    dialog.close()


def test_dialog_keeps_the_plain_title_without_calibration(tmp_path: Path) -> None:
    dialog = GrillDialog(make_workflow(), "gpt-5.6-terra", tmp_path)
    assert dialog.windowTitle() == "GrillMe"
    dialog.close()


def test_materials_question_renders_three_buttons(tmp_path: Path) -> None:
    from flowai.grill import MATERIALS_OPTIONS, GrillQuestion

    dialog = GrillDialog(
        make_workflow(),
        "gpt-5.6-terra",
        tmp_path,
        calibration=make_calibration(),
    )
    dialog.show_question(
        GrillQuestion(text=MATERIALS_QUESTION, options=list(MATERIALS_OPTIONS))
    )
    assert len(dialog.option_buttons) == 3
    assert dialog.question_text.text() == MATERIALS_QUESTION
    dialog.close()
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_grill_ui.py -v
```

Очікується: `TypeError: GrillDialog.__init__() got an unexpected keyword argument 'calibration'`.

- [ ] **Step 3: Прокинути контекст у `flowai/ui/grill_dialog.py`**

У `GrillWorker.__init__` додати параметри `calibration: Any | None = None` і `generated_files: list[str] | None = None`, зберегти їх у полях і передати в `GrillSession` всередині `_ensure_session`:

```python
            self._session = GrillSession(
                self.workflow,
                self._codex,
                self.model,
                self.workspace,
                reasoning_effort=self.reasoning_effort,
                calibration=self.calibration,
                generated_files=self.generated_files,
            )
```

У `GrillDialog.__init__` додати ті самі два параметри, зберегти їх у полях і змінити заголовок:

```python
        self.setWindowTitle(
            "Regenerate Prompt — GrillMe" if calibration is not None else "GrillMe"
        )
```

У `_start_worker` передати їх у `GrillWorker`.

- [ ] **Step 4: Замінити заглушку `_start_regeneration` у `flowai/ui/main_window.py`**

```python
    def _start_regeneration(
        self, session: WorkspaceSession, dialog: CalibrationDialog
    ) -> None:
        """Перезібрати промпт разом з AI, спираючись на звіт калібрації."""
        workflow = session.workflow
        if workflow is None:
            return
        generated = [
            str(path)
            for group in session.generated_file_groups
            for path in list(group.get("intermediate", []))
            + list(group.get("result", []))
        ]
        grill = GrillDialog(
            workflow,
            dialog.model,
            workflow.resolved_workspace(session.project_path),
            self,
            reasoning_effort=dialog.effort,
            calibration=dialog.report,
            generated_files=list(dict.fromkeys(generated)),
        )
        accepted = grill.exec() == QDialog.DialogCode.Accepted
        if accepted and grill.outcome is not None:
            self._apply_grill_outcome(session, grill.outcome)
        node_id = str(dialog.report.node_id)
        session.intervention_responses[node_id] = {"action": "retry_task"}
        session.pending_intervention = None
        session.run_state = "idle"
        if node_id:
            self.scene.set_attention(node_id, False)
        self._refresh_workspace_sidebar()
        self.run_workflow(resume=True)
```

`_apply_grill_outcome` уже існує — `flowai/ui/main_window.py:1491`; він з'явився разом
із кнопкою GrillMe на старті Flow. Нічого не додавайте й не дублюйте: `_start_regeneration`
викликає той самий метод. Імпорти `GrillOutcome` (рядок 70), `normalize_managed_tasks`
(рядок 79) і `GrillDialog` (рядок 88) у файлі теж уже на місці.

- [ ] **Step 5: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_grill_ui.py tests/test_calibration_ui.py -v
```

Очікується: усі проходять.

- [ ] **Step 6: Прогнати весь набір, лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m pytest -q
```

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add flowai/ui/grill_dialog.py flowai/ui/main_window.py tests/test_grill_ui.py && git commit -m "feat(ui): rebuild the prompt with GrillMe from the rejection report"
```

---

### Task 20: Документація ноди та MCP

**Files:**
- Modify: `FLOWAI_NODE_GUIDE.md`
- Modify: `flowai/mcp/schema.py`
- Create: `guides/calibration.md`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `NODE_LABELS`, `NODE_COLORS` із `flowai/models.py`; `list_guides` із `flowai/mcp/guides.py`
- Produces:
  - `describe_kind("calibrator")` повертає підпис, колір, порожній список портів і опис конфігурації
  - `guides/calibration.md` потрапляє в `list_guides()`

- [ ] **Step 1: Написати падаючий тест**

Додати в `tests/test_mcp.py`:

```python
def test_schema_describes_the_calibration_node() -> None:
    from flowai.mcp.schema import describe_kind

    described = describe_kind("calibrator")
    assert described["label"] == "Calibration Stop"
    assert described["color"] == "#E11D48"
    assert described["ports"] == []
    assert "false_threshold" in described["config"]


def test_calibration_guide_is_listed() -> None:
    from flowai.mcp.guides import list_guides

    names = {entry["name"] for entry in list_guides()}
    assert "calibration" in names
```

- [ ] **Step 2: Запустити тести й переконатись, що вони падають**

```bash
.venv/Scripts/python.exe -m pytest tests/test_mcp.py -v
```

Очікується: `KeyError: 'calibrator'` або `AssertionError` на списку довідників.

- [ ] **Step 3: Оновити `flowai/mcp/schema.py`**

У `describe_kind` переконатись, що порти беруться з `Workflow.ports_of` на тимчасовій ноді, і додати опис полів для нового типу:

```python
CONFIG_HINTS: dict[str, dict[str, str]] = {
    "calibrator": {
        "false_threshold": (
            "Після якого за рахунком FALSE зупинити Flow і показати "
            "рекомендації. За замовчуванням 1."
        ),
        "skills": (
            "Скіли, закріплені за нодою: список {name, path}. Codex "
            "завантажує їх до першого кроку агента."
        ),
        "thread_source": (
            "id ноди Task Reviewer, чий Codex-тред продовжує аналіз. "
            "Рушій підставляє його сам."
        ),
    }
}
```

і додати `"config": CONFIG_HINTS.get(kind, {}),` у словник, який повертає `describe_kind`.

- [ ] **Step 4: Написати `guides/calibration.md`**

```markdown
# Калібрація Flow

Нода **Calibration Stop** робить із провалу перевірки одну коротку розмову
замість трьох однакових невдалих спроб.

## Як увімкнути

1. Додайте на полотно ноду Calibration Stop.
2. З'єднайте її з виходом **FALSE** блока Result. Інших з'єднань вона не приймає,
   і власних виходів у неї немає — маршрут на ній закінчується.
3. У полі «Зупиняти після FALSE №» задайте поріг. `1` означає «зупинятись
   на першому ж відхиленні» — це і є швидка калібрація.

Вихід FALSE при цьому лишається підключеним до виконавця: поки поріг не
досягнуто, Flow переробляє задачу сам.

## Що відбувається при зупинці

1. Нода робить другий хід у Codex-треді Task Reviewer — той уже пам'ятає задачу,
   роботу й власний вердикт.
2. Рушій додає в промпт список скілів, які агент справді відкривав, і каталог
   усіх доступних скілів.
3. Агент повертає JSON: пункти відхилення (з картинками й підписами), причину
   й конкретні правки для `SKILL.md`, `references/*.md`, промпта завдання,
   `prompt` або `instructions` блока-виконавця.
4. Звіт лягає в `runs/<час>/calibration.json` разом із чекпоінтом запуску.
5. FlowAI шле Windows-тост. Клік відкриває проєкт, кнопка «Показати правки» —
   одразу другу вкладку.

## Вікно

**Чому відхилено** — пункти по одному, під кожним поле «Ваше бачення
виправлення». Написане там — рішення, а не побажання: воно їде в Regenerate
Prompt як домовленість.

**Пропоновані правки** — список файлів і split-diff: зліва оригінал, справа
зміна. Галочка на кожній правці. Перед записом у скіл робиться копія в
`~/.codex/skills/.flowai/backups/<скіл>/<час>/`; повернути її можна у
Settings → Skills.

## Кнопки

- **Застосувати правки** — записує відмічене, обнуляє лічильник спроб задачі
  й перезапускає саме її. Решта Flow не чіпається.
- **Хай спробує сам** — нічого не пише, Flow продовжує переробку.
- **Regenerate Prompt** — поруч вибір моделі й складності. Закриває вікно
  й відкриває GrillMe, який уже знає весь Flow, причину відхилення й ваші
  нотатки. Перше питання завжди одне: використовувати згенеровані матеріали
  чи почати спочатку. Останній варіант відповіді завжди вільний.
- **Зупинити Flow** — запуск скасовується, чекпоінт прибирається.

## Скіли

`SkillInput` дозволяє закріпити скіл за нодою: агент отримує його одразу,
не витрачаючи кроки на пошук і `Get-Content`. Якщо Рев'ювер каже, що
бракувало скіла, у вкладці правок з'явиться галочка — один клік, і скіл
закріплюється за блоком-виконавцем.
```

- [ ] **Step 5: Дописати розділ у `FLOWAI_NODE_GUIDE.md`**

Додати в кінець файлу:

```markdown
## Calibration Stop

**Колір:** `#E11D48`. **Входи:** лише вихід FALSE блока Result. **Виходи:** немає.

Зупиняє Flow після K-го відхилення задачі й збирає рекомендації, продовжуючи
Codex-тред Task Reviewer. Ключові поля конфігурації:

| Поле | Що робить |
|---|---|
| `false_threshold` | Після якого FALSE зупинятись. За замовчуванням 1 |
| `skills` | Скіли, закріплені за нодою: `[{"name": ..., "path": ...}]` |
| `thread_source` | id ноди, чий тред продовжується. Рушій заповнює сам |

Повний опис — у `guides/calibration.md`.

## Поле «Скіли» в агентських блоках

Будь-який блок-агент може мати закріплені скіли. Вони передаються Codex як
`SkillInput` і завантажуються до першого кроку агента — це економить кроки
й робить поведінку відтворюваною.
```

- [ ] **Step 6: Запустити тести**

```bash
.venv/Scripts/python.exe -m pytest tests/test_mcp.py -v
```

Очікується: усі проходять, зокрема 2 нових.

- [ ] **Step 7: Прогнати весь набір, лінтер і закомітити**

```bash
.venv/Scripts/python.exe -m pytest -q
```

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

```bash
git add FLOWAI_NODE_GUIDE.md guides/calibration.md flowai/mcp/schema.py tests/test_mcp.py && git commit -m "docs: describe the Calibration Stop node and pinned skills"
```

---

## Перевірка після всіх задач

- [ ] **Прогнати весь набір тестів**

```bash
.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Перевірити лінтер**

```bash
.venv/Scripts/python.exe -m ruff check flowai tests
```

- [ ] **Ручна перевірка наскрізного сценарію**

1. Відкрити `!_projects/*.flowai.json` — переконатись, що наявні Flow відкриваються без помилок формату.
2. Додати ноду Calibration Stop, з'єднати з FALSE, зберегти, перевідкрити — нода на місці.
3. Запустити Flow із завідомо провальним завданням. Згорнути FlowAI.
4. Дочекатись тоста → натиснути «Показати правки» → перевірити, що відкрилась друга вкладка.
5. Написати бачення в першому пункті, зняти галочку з однієї правки, натиснути «Застосувати правки» — перевірити журнал і промпт задачі.
6. Закрити FlowAI на паузі, відкрити знову — переконатись, що вікно калібрації відновилось і Flow продовжується.
7. Settings → Skills: відкрити скіл, змінити категорію, повернути резервну копію.
