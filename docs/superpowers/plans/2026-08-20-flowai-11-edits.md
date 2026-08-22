# FlowAI: 11 правок і сучасний інтерфейс · План імплементації

> **Для агентів-виконавців:** ОБОВ'ЯЗКОВИЙ САБ-СКІЛ: використайте `superpowers:subagent-driven-development` (рекомендовано) або `superpowers:executing-plans`, щоб виконувати план задача за задачею. Кроки позначені чекбоксами (`- [ ]`).

**Мета:** розширити FlowAI 0.4 до версії, де програма стартує без вікна консолі, запуск переживає сон ПК, кожне завдання має власний бюджет спроб, чат показує живі дії агента з інтерактивними файлами, інтерфейс має єдину сучасну дизайн-систему з темними шапками вікон і плавними анімаціями, а Flow можна скласти або уточнити за допомогою AI через власний MCP-сервер.

**Архітектура:** рушій (`flowai/engine.py`) лишається єдиним джерелом правди про стан запуску — усі нові лічильники живуть у `RunCheckpoint`, щоб переживати паузу й втручання. UI ніколи не рахує стан сам, а лише відмальовує події з `on_event`. Новий функціонал виноситься в окремі модулі (`flowai/run_stats.py`, `flowai/grill.py`, `flowai/mcp/`, `flowai/ui/log_panel.py`, `flowai/ui/paths.py`), щоб не роздувати `main_window.py`, який уже 2946 рядків.

**Стек:** Python ≥3.10, PySide6 6.9–6.x, `openai-codex` 0.147, `mcp` (додається), pytest 9.

## Глобальні обмеження

- **`FLOW_FORMAT_VERSION` лишається `2`.** `Workflow.from_dict` кидає `UnsupportedFlowFormat` для будь-якої версії, меншої за поточну, тож підняття версії зламає наявні `!_projects/*.flowai.json`. Усі нові поля конфігів додаються з дефолтами у `_default_config`, і старі файли мають відкриватися без змін.
- **Мова інтерфейсу — українська.** Усі нові підписи, кнопки, тексти помилок і повідомлень журналу — українською, у тому ж тоні, що й наявні.
- **Ніяких нових мережевих залежностей.** Codex працює через збережений вхід у ChatGPT; OpenAI API-ключ не використовується ніде.
- **Тести не мають ходити в мережу.** Рушійні тести працюють у режимі `FLOWAI_FAKE_CODEX=1`; UI-тести — з `QT_QPA_PLATFORM=offscreen`.
- **Кожна задача завершується запуском тестів і комітом.**
- Команда тестів: `.venv\Scripts\python -m pytest tests/ -q` з кореня `C:\Users\illia\Documents\DDA PF\FlowAI`.
- Лінтер: `.venv\Scripts\python -m ruff check flowai tests` — має бути чисто перед комітом.

## Рішення, ухвалені на grill-сесії

| № | Правка | Рішення |
|---|---|---|
| 0 | Консоль при запуску | `pythonw.exe` замість `python.exe`, `[project.gui-scripts]` замість `[project.scripts]`, ярлик `FlowAI.lnk`, видалений мертвий код із `CREATE_NEW_CONSOLE` |
| 1 | Ліміт спроб на завдання | Нове поле `task_attempt_limit` у Result (дефолт 2), лічильник на `active_task_id`, третій жовтий порт `EXHAUSTED`, авто-продовження без діалогу, провалене завдання фінальне й видиме у підсумку |
| 2 | Живі дії + файли в чаті | `QTextBrowser` + закріплений живий рядок із плавною пульсацією; джерело дій — `TurnHandle.stream()`; проміжні файли зі стриму **та** власного `QFileSystemWatcher`; у журнал іде компактний рядок на завершений крок |
| 3 | Сон ПК | Не переривати агента ні при сні, ні при локі; `TurnStatus.interrupted` більше ніколи не вважається успіхом |
| 4 | GrillMe перед стартом | Один сеанс на весь Flow → переписані промпти завдань із диффом; зміни лягають у Flow як звичайне редагування (undo, dirty); без автоматичного ліміту питань |
| 5 | Темне вікно Files | Прибрати смугастість, білий текст, заголовок кольором ноди |
| 6 | Сумарний час завдань | Час завдання = від активації до completed/failed; сума показується у футері блока |
| 7 | Stats | Час + токени + % контексту, перемикач «Цей запуск / Усі запуски» |
| 8 | MCP | Окремий файловий stdio-сервер `python -m flowai.mcp`, спільний для внутрішнього агента та зовнішніх клієнтів |
| 9 | Новий Flow через AI | Агент читає робочу папку read-only, попередні Flow і `guides/*.md`; вибір моделі; GrillMe увімкнено за замовчуванням |
| 10 | Результати при поверненні | Окреме вікно «Результати» по прапорцю `unread_result` |
| UI | Шапка вікон | Темна системна шапка через DWM для всіх вікон; безрамкові вікна не робимо |
| UI | Глибина правок | Єдина дизайн-система: токени в `design.py`, тема генерується з них, локальні `setStyleSheet` прибрані |
| UI | Анімації | Функціональні плюс канвас: плавна пульсація замість блимання 550 мс, 60 fps лише для активних нод, біжучий пунктир по активному ребру, анімовані кнопки й поява вікон |
| UI | Шрифти | Вбудовані Inter (інтерфейс) і JetBrains Mono (промпти, JSON, шляхи), єдина шкала, одне джерело розміру |
| UI | Палітра | Та сама темно-синя база з фіолетовим акцентом, але з ієрархією поверхонь, радіусами 8/12/16, тінями й фокус-кільцем |

## Структура файлів

**Нові:**
- `flowai/run_stats.py` — чиста агрегація подій запуску в статистику (без Qt).
- `flowai/run_history.py` — читання `runs/*/flowai-run.json`.
- `flowai/grill.py` — сеанс GrillMe: питання, відповіді, збирання підсумку (без Qt).
- `flowai/mcp/__init__.py`, `flowai/mcp/__main__.py`, `flowai/mcp/server.py`, `flowai/mcp/drafts.py`, `flowai/mcp/guides.py` — MCP-сервер.
- `flowai/ui/paths.py` — відкриття файлу, показ у Провіднику, копіювання шляху/картинки, збірка контекстного меню.
- `flowai/ui/log_panel.py` — панель журналу: `QTextBrowser` + живий рядок.
- `flowai/ui/file_watch.py` — спостерігач за робочими папками під час запуску.
- `flowai/ui/stats_dialog.py` — вікно Stats.
- `flowai/ui/results_dialog.py` — вікно «Результати».
- `flowai/ui/grill_dialog.py` — вікно питань GrillMe і фінальне вікно з диффом.
- `flowai/ui/run_start_dialog.py` — вибір «Запустити / GrillMe» після Run.
- `flowai/ui/flow_composer_dialog.py` — складання Flow через AI.
- `flowai/ui/design.py` — токени дизайну: палітра, типографіка, радіуси, відступи, тривалості.
- `flowai/ui/platform.py` — темна системна шапка вікон через DWM.
- `flowai/ui/typography.py` — завантаження вбудованих шрифтів.
- `flowai/ui/icons.py` — рендер SVG-іконок у колір теми.
- `flowai/ui/controls.py` — `AnimatedButton` трьох рівнів із плавним hover.
- `flowai/ui/motion.py` — пульсація, поява вікон, `AnimatedDialog`.
- `flowai/ui/assets/fonts/`, `flowai/ui/assets/icons/` — вбудовані шрифти та іконки.
- `guides/` — папка md-довідників, які подає MCP.

**Змінюються:**
- `flowai/models.py` — третій порт Result, `task_attempt_limit`, статус `failed` у завданнях.
- `flowai/engine.py` — per-task лічильник, час завдань, токени, стрим-колбек, `{{grill_summary}}`.
- `flowai/codex_adapter.py` — стрим замість `turn.run()`, обробка `interrupted`, збір `usage`.
- `flowai/ui/canvas.py` — жовтий порт, червоний хрестик, час завдань.
- `flowai/ui/inspector.py` — поле ліміту спроб.
- `flowai/ui/main_window.py` — кнопка Stats, підключення нових вікон і панелі журналу, обробка сну.
- `flowai/ui/theme.py` — повністю переписується: QSS генерується з токенів `design.py`.
- `flowai/app.py` — шрифти, темні шапки, збірка стилю.
- `start-flowai.cmd`, `install.ps1` — запуск без консолі та ярлик.
- `pyproject.toml` — залежність `mcp`, `gui-scripts`, package-data для шрифтів та іконок.
- `README.md`, `DOCUMENTATION.md`, `FLOWAI_NODE_GUIDE.md` — опис нових можливостей.

---

# ФАЗА 0 — болючі дрібниці, які заважають щодня (правки 0 і 3)

### Задача 0: Прибрати консольне вікно при запуску

**Файли:**
- Змінити: `start-flowai.cmd`
- Змінити: `pyproject.toml:17-18` (`[project.scripts]`)
- Змінити: `install.ps1` (створення ярлика без консолі)
- Змінити: `flowai/codex_adapter.py:303-313` (видалити мертвий `start_chatgpt_login`)
- Тест: `tests/test_launcher.py`

**Інтерфейси:**
- Виробляє: `FlowAI.lnk` у корені проєкту, який запускає `pythonw.exe -m flowai` без вікна консолі.
- Споживає: нічого з інших задач; цю задачу можна робити першою і незалежно.

**Три різні причини, чому зараз відкривається cmd:**

1. `start-flowai.cmd` виконує `"%FLOWAI_PYTHON%" -m flowai`, де `FLOWAI_PYTHON` — це `python.exe`. Це **консольний** застосунок, тож вікно cmd висить увесь сеанс роботи програми, а не блимає на секунду.
2. `pyproject.toml` оголошує точку входу в секції `[project.scripts]`, і setuptools генерує `flowai.exe` як консольний лаунчер. Той самий ефект, якщо запускати через `.venv\Scripts\flowai.exe`.
3. `flowai/codex_adapter.py:303` містить `start_chatgpt_login()`, який відкриває `cmd.exe /k codex login` із прапорцем `CREATE_NEW_CONSOLE`. Це **мертвий код**: вхід у ChatGPT уже йде через SDK у `flowai/ui/login_dialog.py:44` (`codex.login_chatgpt()`), і жоден виклик `start_chatgpt_login` у проєкті не лишився.

**Чому не втрачаємо діагностику.** Нинішній `if errorlevel 1 pause` у `.cmd` — єдине, що показувало помилку старту. Замість нього працює наявна інфраструктура: `configure_logging()` пише файли журналів, `install_exception_hooks()` ловить необроблені винятки, `UiHangWatchdog` фіксує зависання, а шляхи до всіх цих файлів показує **Довідка → Про програму**. Тобто після переходу на `pythonw` помилки не зникають, вони просто перестають вимагати відкритої консолі.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_launcher.py`:

```python
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_launcher_uses_pythonw() -> None:
    """Консольний python.exe тримає вікно cmd відкритим на весь сеанс."""
    text = (PROJECT_ROOT / "start-flowai.cmd").read_text(encoding="utf-8")
    assert "pythonw.exe" in text
    assert "python.exe\" -m flowai" not in text
    assert "%FLOWAI_PYTHON%\" -m flowai" not in text


def test_entry_point_is_gui_script() -> None:
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.gui-scripts]" in text
    assert "[project.scripts]" not in text


def test_no_new_console_is_spawned() -> None:
    text = (PROJECT_ROOT / "flowai" / "codex_adapter.py").read_text(encoding="utf-8")
    assert "CREATE_NEW_CONSOLE" not in text
    assert "cmd.exe" not in text
```

- [ ] **Крок 2: Запустити тести і переконатися, що вони падають**

Виконати: `.venv\Scripts\python -m pytest tests/test_launcher.py -q`
Очікується: FAIL — усі три тести, бо зараз використовується `python.exe`, секція `[project.scripts]` і `CREATE_NEW_CONSOLE`.

- [ ] **Крок 3: Перевести `.cmd` на `pythonw` і від'єднати процес**

Замінити вміст `start-flowai.cmd` на:

```bat
@echo off
setlocal
set "FLOWAI_ROOT=%~dp0"
set "FLOWAI_PYTHONW=%FLOWAI_ROOT%.venv\Scripts\pythonw.exe"

if not exist "%FLOWAI_PYTHONW%" (
  echo FlowAI ще не встановлено. Запускаю install.ps1...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%FLOWAI_ROOT%install.ps1"
  if errorlevel 1 pause & exit /b 1
)

cd /d "%FLOWAI_ROOT%"
rem start від'єднує процес, тому вікно cmd закривається одразу,
rem а pythonw.exe не створює консолі взагалі.
start "" "%FLOWAI_PYTHONW%" -m flowai
exit /b 0
```

- [ ] **Крок 4: Зробити точку входу GUI-скриптом**

У `pyproject.toml` замінити:

```toml
[project.scripts]
flowai = "flowai.app:main"
```

на:

```toml
[project.gui-scripts]
flowai = "flowai.app:main"
```

Перевстановити пакет, щоб перегенерувався лаунчер:

Виконати: `.venv\Scripts\python -m pip install -e .`
Очікується: `Successfully installed flowai-desktop`

- [ ] **Крок 5: Видалити мертвий код із консоллю**

У `flowai/codex_adapter.py` повністю видалити функцію `start_chatgpt_login` (рядки 303–313). Якщо після цього імпорт `sys` більше ніде у файлі не використовується — прибрати і його; `subprocess` лишається потрібним для `login_status`.

Переконатися, що видалення нічого не зламало:

Виконати: `.venv\Scripts\python -m pytest tests/test_auth_ui.py -q`
Очікується: PASS.

- [ ] **Крок 6: Створювати ярлик без консолі під час встановлення**

Дописати в кінець `install.ps1`:

```powershell
Write-Host "Створення ярлика FlowAI..."
$shortcutPath = Join-Path $flowaiRoot "FlowAI.lnk"
$pythonwPath = Join-Path $venvPath "Scripts\pythonw.exe"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonwPath
$shortcut.Arguments = "-m flowai"
$shortcut.WorkingDirectory = $flowaiRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "FlowAI"
$shortcut.Save()

Write-Host ""
Write-Host "FlowAI встановлено. Запустіть FlowAI.lnk або start-flowai.cmd"
```

Додати `FlowAI.lnk` у `.gitignore` — ярлик створюється локально під конкретні шляхи й не має потрапляти в репозиторій.

- [ ] **Крок 7: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 8: Ручна перевірка**

Подвійним кліком запустити `FlowAI.lnk` — вікно консолі не має з'явитися взагалі. Потім запустити `start-flowai.cmd` — вікно cmd має зникнути одразу після старту програми, а не висіти поруч із нею. Перевірити, що програма нормально працює й закривається.

- [ ] **Крок 9: Оновити документацію**

У `README.md` у розділі встановлення пункт 3 замінити на:

```markdown
3. Запустіть **FlowAI.lnk** (створюється під час встановлення) або
   `start-flowai.cmd`. Обидва шляхи запускають програму без вікна консолі.
   Якщо програма не стартує, шляхи до журналів і звіту про збій показує
   **Довідка → Про програму**.
```

- [ ] **Крок 10: Коміт**

```bash
git add start-flowai.cmd pyproject.toml install.ps1 flowai/codex_adapter.py .gitignore README.md tests/test_launcher.py
git commit -m "fix: запуск без вікна консолі через pythonw і gui-script"
```

---

### Задача 1: Перерваний хід агента більше не вважається успішним

**Файли:**
- Змінити: `flowai/codex_adapter.py` (додати виняток і розбір статусу ходу)
- Змінити: `flowai/engine.py:206-224` (`pause`), `flowai/engine.py:550-600` (`_execute_with_retries`)
- Змінити: `flowai/ui/main_window.py:2858-2865` (обробка `WM_WTSSESSION_CHANGE`)
- Тест: `tests/test_core.py`

**Інтерфейси:**
- Виробляє: `codex_adapter.TurnInterrupted(RuntimeError)`; `codex_adapter.agent_run_from_turn(result: Any, thread_id: str) -> AgentRun` — піднімає `TurnInterrupted`, якщо `result.status` дорівнює `interrupted`, інакше повертає `AgentRun`.
- Споживає: наявний `AgentRun`, наявний `normalize_items`.

**Чому це баг.** Windows шле `PBT_APMSUSPEND` → `MainWindow._set_system_pause_reason("sleep", True)` → `WorkflowRunner.pause()` → `CodexAdapter.cancel_active()` → `turn.interrupt()`. SDK повертає `TurnResult` зі `status == TurnStatus.interrupted` **без винятку** (`_raise_for_failed_turn` реагує лише на `failed`). `run_agent` бере `result.final_response or ""` — і нода завершується як `success` із порожнім текстом. Гілка `interrupted_by_system` у `_execute_with_retries` не спрацьовує, бо винятку немає.

- [ ] **Крок 1: Написати падаючий тест**

Додати в кінець `tests/test_core.py`:

```python
def test_interrupted_turn_raises_instead_of_returning_empty_text() -> None:
    """Перерваний хід не має тихо ставати успішним результатом ноди."""

    class FakeStatus:
        value = "interrupted"

    class FakeResult:
        status = FakeStatus()
        final_response = ""
        items: list[Any] = []

    with pytest.raises(codex_adapter.TurnInterrupted):
        codex_adapter.agent_run_from_turn(FakeResult(), thread_id="thread-1")


def test_completed_turn_returns_agent_run() -> None:
    class FakeStatus:
        value = "completed"

    class FakeResult:
        status = FakeStatus()
        final_response = "готово"
        items: list[Any] = []

    run = codex_adapter.agent_run_from_turn(FakeResult(), thread_id="thread-1")
    assert run.text == "готово"
    assert run.thread_id == "thread-1"
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_interrupted_turn_raises_instead_of_returning_empty_text -q`
Очікується: FAIL — `AttributeError: module 'flowai.codex_adapter' has no attribute 'TurnInterrupted'`

- [ ] **Крок 3: Додати виняток і функцію розбору в `flowai/codex_adapter.py`**

Після класу `CodexUnavailable` додати:

```python
class TurnInterrupted(RuntimeError):
    """Хід агента обірвано ззовні — результат неповний і не є успіхом."""
```

Після `normalize_items` додати:

```python
def agent_run_from_turn(result: Any, thread_id: str) -> AgentRun:
    """Звести TurnResult до AgentRun, не дозволяючи перерваному ходу пройти далі."""
    status = getattr(result, "status", None)
    status_value = str(getattr(status, "value", status) or "")
    if status_value == "interrupted":
        raise TurnInterrupted(
            "Хід агента перервано до завершення — результат неповний"
        )
    return AgentRun(
        text=str(getattr(result, "final_response", "") or ""),
        items=normalize_items(getattr(result, "items", None)),
        thread_id=str(thread_id or ""),
    )
```

- [ ] **Крок 4: Використати нову функцію у `run_agent`**

У `flowai/codex_adapter.py` замінити фінальний блок `run_agent`:

```python
        return AgentRun(
            text=str(result.final_response or ""),
            items=normalize_items(getattr(result, "items", None)),
            thread_id=str(getattr(thread, "id", "") or ""),
        )
```

на:

```python
        return agent_run_from_turn(result, str(getattr(thread, "id", "") or ""))
```

- [ ] **Крок 5: Запустити тести і переконатися, що вони проходять**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py -q`
Очікується: PASS, усі тести зелені.

- [ ] **Крок 6: Прибрати переривання агента при паузі**

У `flowai/engine.py` метод `pause` наразі вбиває активний хід. Замінити його тіло на:

```python
    def pause(self, reason: str = "Систему призупинено") -> None:
        """Пауза між нодами. Активний хід агента навмисно НЕ переривається:
        процес Codex засинає разом із ПК і після пробудження продовжує хід."""
        if self._stop.is_set() or not self._resume_event.is_set():
            return
        with self._control_lock:
            self._pause_generation += 1
            self._resume_event.clear()
        self._emit("run_paused", message=reason)
```

Метод `cancel` лишається без змін — там переривання доречне.

- [ ] **Крок 7: Ловити `TurnInterrupted` як системне переривання**

У `flowai/engine.py` імпорт зверху змінити на:

```python
from .codex_adapter import CodexAdapter, TurnInterrupted
```

У `_execute_with_retries` перед загальним `except Exception as exc:` додати окремий обробник:

```python
            except TurnInterrupted:
                if self._stop.is_set():
                    raise RunCancelled("Flow зупинено")
                self._wait_until_resumed()
                self._emit(
                    "node_retry",
                    node=node,
                    message="Хід агента обірвався — повторюємо в тому ж треді",
                )
                continue
```

- [ ] **Крок 8: Блокування екрана більше не паузить Flow**

У `flowai/ui/main_window.py` у `nativeEvent` видалити гілку `WM_WTSSESSION_CHANGE`, лишивши тільки живлення:

```python
    def nativeEvent(self, event_type: Any, message: Any) -> tuple[bool, int]:
        if sys.platform == "win32":
            try:
                native = ctypes.wintypes.MSG.from_address(int(message))
                if native.message == 0x0218:  # WM_POWERBROADCAST
                    if native.wParam == 0x0004:  # PBT_APMSUSPEND
                        self._set_system_pause_reason("sleep", True)
                    elif native.wParam in {0x0007, 0x0012}:  # resume variants
                        self._set_system_pause_reason("sleep", False)
            except (TypeError, ValueError, OSError):
                LOGGER.debug("Could not decode native Windows event", exc_info=True)
        return super().nativeEvent(event_type, message)
```

Реєстрацію `WTSRegisterSessionNotification` у `showEvent` та її скасування у `closeEvent` видалити разом із полем `_wts_notifications_registered` — вони більше ні на що не впливають. Текст паузи у `_set_system_pause_reason` змінити на `"ПК переходить у сон — Flow продовжиться після пробудження"`, а текст відновлення — на `"ПК прокинувся — виконання Flow триває"`.

- [ ] **Крок 9: Тест на те, що пауза не чіпає активний хід**

Додати в `tests/test_core.py`:

```python
def test_pause_does_not_interrupt_active_turn(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow)
    interrupts: list[str] = []

    class FakeCodex:
        def cancel_active(self) -> bool:
            interrupts.append("called")
            return True

    runner._active_codex = FakeCodex()
    runner.pause("тест")
    assert interrupts == []
    assert not runner._resume_event.is_set()
    runner.resume("тест")
    assert runner._resume_event.is_set()
```

- [ ] **Крок 10: Прогнати всі тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 11: Оновити документацію**

У `README.md` у розділі про виконання Flow додати абзац:

```markdown
Якщо ПК засинає під час роботи агента, FlowAI більше не перериває його хід:
процес Codex засинає разом із системою і після пробудження продовжує з того
самого місця. Пауза діє лише як бар'єр між блоками — нова нода не стартує,
поки ПК не прокинеться. Блокування екрана на виконання не впливає взагалі.
```

- [ ] **Крок 12: Коміт**

```bash
git add flowai/codex_adapter.py flowai/engine.py flowai/ui/main_window.py tests/test_core.py README.md
git commit -m "fix: не переривати хід агента при сні ПК і не ковтати interrupted"
```

---

# ФАЗА 1 — швидкі відчутні правки (5, 6, 7, 10)

### Задача 2: Правка 5 — темне вікно Files

**Файли:**
- Змінити: `flowai/ui/main_window.py:509-638` (`GeneratedFilesDialog`)
- Змінити: `flowai/ui/theme.py`
- Тест: `tests/test_workspaces_ui.py`

**Інтерфейси:**
- Споживає: `WorkspaceSession.generated_file_groups`.
- Виробляє: нічого нового для інших задач; стиль `QTreeWidget#generatedFilesTree` перевикористають вікна Stats і «Результати».

**Причина смугастості:** `self.tree.setAlternatingRowColors(True)` у поєднанні з тим, що для дерева не заданий фон у QSS — Qt малює системний світлий колір через рядок.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_generated_files_dialog_is_dark_and_not_striped() -> None:
    application()
    session = WorkspaceSession(display_name="Тест")
    session.generated_file_groups = [
        {
            "node_id": "abc123",
            "node_title": "Task Executor",
            "iteration": 1,
            "color": "#7C3AED",
            "intermediate": [],
            "result": [],
        }
    ]
    dialog = GeneratedFilesDialog(session)
    assert dialog.tree.alternatingRowColors() is False
    heading = dialog.tree.topLevelItem(0)
    assert heading.foreground(0).color().name() == "#7c3aed"
    child = heading.child(0)
    assert child.foreground(0).color().name() == "#e5e7eb"
    dialog.deleteLater()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py::test_generated_files_dialog_is_dark_and_not_striped -q`
Очікується: FAIL — `assert True is False`.

- [ ] **Крок 3: Прибрати смугастість і перефарбувати вміст**

У `GeneratedFilesDialog.__init__` замінити `self.tree.setAlternatingRowColors(True)` на `self.tree.setAlternatingRowColors(False)`.

Розділити фарбування на два методи. Замінити `_color_item` на:

```python
    @staticmethod
    def _color_item(item: QTreeWidgetItem, color: QColor) -> None:
        """Заголовок групи — кольором ноди."""
        item.setForeground(0, color)
        item.setForeground(1, color)

    @staticmethod
    def _plain_item(item: QTreeWidgetItem) -> None:
        """Рядки вмісту — світлий текст на темному тлі."""
        item.setForeground(0, QColor("#E5E7EB"))
        item.setForeground(1, QColor("#94A3B8"))
```

У `_add_section` заголовок секції лишити кольоровим (`self._color_item(section, color)`), а для `placeholder` та кожного `item` викликати `self._plain_item(...)` замість `self._color_item(...)`.

- [ ] **Крок 4: Додати стиль дерева у `flowai/ui/theme.py`**

Перед закриттям рядка `APP_STYLE` додати:

```css
QTreeWidget, QTreeView {
    background: #0B1220;
    alternate-background-color: #0B1220;
    border: 1px solid #263247;
    border-radius: 5px;
    color: #E5E7EB;
}
QTreeWidget::item { padding: 3px; }
QTreeWidget::item:selected, QTreeView::item:selected { background: #4C3AC7; }
QHeaderView::section {
    background: #172033;
    color: #F9FAFB;
    border: none;
    border-right: 1px solid #263247;
    padding: 5px;
    font-weight: 600;
}
```

- [ ] **Крок 5: Запустити тест і переконатися, що він проходить**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py -q`
Очікується: PASS.

- [ ] **Крок 6: Коміт**

```bash
git add flowai/ui/main_window.py flowai/ui/theme.py tests/test_workspaces_ui.py
git commit -m "fix: темне вікно Files без смугастих рядків"
```

---

### Задача 3: Правка 6 — час кожного завдання і сумарний час у блоці Tasks

**Файли:**
- Змінити: `flowai/engine.py:636-726` (`_execute_tasks_manager`)
- Змінити: `flowai/ui/main_window.py:1602-1613` (обробка `task_states` у `_handle_run_event`)
- Змінити: `flowai/ui/canvas.py:502-553` (`_paint_tasks`), `766-790` (`set_task_states`)
- Тест: `tests/test_core.py`, `tests/test_workspaces_ui.py`

**Інтерфейси:**
- Виробляє: у події `tasks_progress` кожен елемент `task_states` отримує поле `seconds: float`; сама подія — поле `total_seconds: float`. `NodeItem.task_states` тепер містить ті самі ключі.
- Споживає: `RunCheckpoint.task_progress[node_id]` — до нього додається ключ `"times"`.

**Семантика:** час завдання — від моменту, коли Tasks Manager його активував, до моменту, коли воно стало `completed` або `failed`. Тобто він включає всі проходи Executor→Reviewer→Result для цього завдання. Сумарний час — сума часів завдань, тому очікування вашої відповіді в діалогах у нього не входить.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_tasks_manager_measures_time_per_task(tmp_path: Path) -> None:
    workflow = Workflow(name="Черга", workspace=str(tmp_path))
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Перше", "attachments": []},
        {"id": "t2", "prompt": "Друге", "attachments": []},
    ]
    workflow.nodes = [manager]
    runner = WorkflowRunner(workflow)

    first = runner._execute_tasks_manager(manager)
    assert first.data["task"]["id"] == "t1"
    assert first.data["tasks"][0]["seconds"] == 0.0

    time.sleep(0.05)
    second = runner._execute_tasks_manager(manager)
    states = {item["id"]: item for item in second.data["tasks"]}
    assert states["t1"]["status"] == "completed"
    assert states["t1"]["seconds"] >= 0.05
    assert states["t2"]["status"] == "running"
    assert second.data["total_seconds"] >= 0.05
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_tasks_manager_measures_time_per_task -q`
Очікується: FAIL — `KeyError: 'seconds'`.

- [ ] **Крок 3: Вести облік часу в `_execute_tasks_manager`**

У `flowai/engine.py` на початку `_execute_tasks_manager` після отримання `progress` додати нормалізацію словника часів:

```python
        times: dict[str, dict[str, float]] = progress.setdefault("times", {})
        now = time.time()

        def _close_task(task_id: str) -> None:
            record = times.get(task_id)
            if not record or record.get("finished"):
                return
            record["finished"] = now
            record["seconds"] = max(0.0, now - float(record.get("started", now)))
```

Одразу після рядка, який зараховує активне завдання у `completed`, додати `_close_task(active_id)`. Після обчислення нового `active_id` додати:

```python
        if active_id and active_id not in times:
            times[active_id] = {"started": now, "finished": 0.0, "seconds": 0.0}
```

У циклі побудови `states` кожен елемент доповнити часом — для активного завдання час рахується «наживо»:

```python
            record = times.get(task_id, {})
            if status == "running" and record:
                seconds = max(0.0, now - float(record.get("started", now)))
            else:
                seconds = float(record.get("seconds", 0.0))
            states.append(
                {
                    "id": task_id,
                    "title": managed_task_title(task, index),
                    "status": status,
                    "seconds": round(seconds, 3),
                }
            )
```

Перед `self._emit("tasks_progress", ...)` порахувати підсумок і додати його в подію та в обидві гілки `data`:

```python
        total_seconds = round(sum(item["seconds"] for item in states), 3)
```

У виклик `self._emit("tasks_progress", ...)` додати аргумент `total_seconds=total_seconds`, а в обидва словники `data` — ключ `"total_seconds": total_seconds`.

- [ ] **Крок 4: Запустити тест і переконатися, що він проходить**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_tasks_manager_measures_time_per_task -q`
Очікується: PASS.

- [ ] **Крок 5: Прокинути час у сесію UI**

У `flowai/ui/main_window.py` у `_handle_run_event` у блоці нормалізації `task_states` додати поле часу:

```python
            clean_states = [
                {
                    "id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "status": str(item.get("status", "pending")),
                    "seconds": float(item.get("seconds", 0.0) or 0.0),
                }
                for item in task_states
                if isinstance(item, dict)
            ]
```

У гілці `elif event_type == "tasks_progress":` дописати сумарний час у рядок журналу:

```python
        elif event_type == "tasks_progress":
            total = float(event.get("total_seconds", 0.0) or 0.0)
            self._append_session_log(
                session,
                f"{prefix}: {message} "
                f"({event.get('completed_count', 0)}/{event.get('task_count', 0)}"
                f" · сумарно {total:.1f} с)",
                color=color,
            )
```

- [ ] **Крок 6: Показати час у блоці на канвасі**

У `flowai/ui/canvas.py` у `_configured_task_states` додати `"seconds": 0.0` у словник, який будується з конфігу, а в `set_task_states` — зберігати поле `seconds` при злитті станів.

У `_paint_tasks` після відмальовки заголовка завдання додати час праворуч:

```python
            seconds = float(task.get("seconds", 0.0) or 0.0)
            if seconds > 0:
                painter.setPen(QColor("#94A3B8"))
                painter.drawText(
                    QRectF(self.node_width - 76, y, 62, 18),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    self._format_seconds(seconds),
                )
```

а сам заголовок обрізати вужче: у виклику `metrics.elidedText(...)` замінити ширину `round(self.node_width - 48)` на `round(self.node_width - 116)`.

У футері замінити виведення `self._time_lines()[-1]` на сумарний час завдань:

```python
        total = sum(float(task.get("seconds", 0.0) or 0.0) for task in self.task_states)
        painter.setPen(QColor("#CBD5E1"))
        painter.drawText(
            QRectF(self.node_width - 116, footer_y, 102, 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"Σ {self._format_seconds(total)}" if total > 0 else "Σ —",
        )
```

- [ ] **Крок 7: Тест канваса**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_tasks_node_keeps_seconds_in_states() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Черга")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [{"id": "t1", "prompt": "Перше", "attachments": []}]
    workflow.nodes = [manager]
    scene.set_workflow(workflow)
    scene.set_task_states(
        manager.id,
        [{"id": "t1", "title": "Перше", "status": "completed", "seconds": 12.5}],
    )
    item = scene.node_items[manager.id]
    assert item.task_states[0]["seconds"] == 12.5
```

- [ ] **Крок 8: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 9: Коміт**

```bash
git add flowai/engine.py flowai/ui/main_window.py flowai/ui/canvas.py tests/
git commit -m "feat: облік часу кожного завдання і сумарного часу в блоці Tasks"
```

---

### Задача 4: Правка 7 — збір токенів і контексту в рушії

**Файли:**
- Змінити: `flowai/codex_adapter.py` (`AgentRun`, `agent_run_from_turn`, `_fake_run`)
- Змінити: `flowai/engine.py` (`_execute_agent` — покласти `usage` у `result.data`)
- Тест: `tests/test_core.py`

**Інтерфейси:**
- Виробляє: `AgentRun.usage: dict[str, int]` із ключами `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`; `AgentRun.context_window: int` (0, якщо невідомо). У `NodeResult.data` з'являється ключ `"usage"` із тими самими полями плюс `"context_window"`.
- Споживає: `TurnResult.usage` (`ThreadTokenUsage`), який SDK уже повертає з `turn.run()` — стрим для цього не потрібен.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_agent_run_collects_token_usage() -> None:
    class FakeStatus:
        value = "completed"

    class FakeBreakdown:
        input_tokens = 100
        cached_input_tokens = 20
        output_tokens = 30
        reasoning_output_tokens = 10
        total_tokens = 160

    class FakeUsage:
        last = FakeBreakdown()
        model_context_window = 400000

    class FakeResult:
        status = FakeStatus()
        final_response = "готово"
        items: list[Any] = []
        usage = FakeUsage()

    run = codex_adapter.agent_run_from_turn(FakeResult(), thread_id="t")
    assert run.usage["total_tokens"] == 160
    assert run.usage["reasoning_output_tokens"] == 10
    assert run.context_window == 400000
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_agent_run_collects_token_usage -q`
Очікується: FAIL — `AttributeError: 'AgentRun' object has no attribute 'usage'`.

- [ ] **Крок 3: Розширити `AgentRun` і розбір ходу**

У `flowai/codex_adapter.py` додати поля в датаклас:

```python
@dataclass(slots=True)
class AgentRun:
    """Результат одного ходу агента разом із його реальними кроками."""

    text: str
    items: list[dict[str, Any]] = field(default_factory=list)
    thread_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    context_window: int = 0
```

Додати функцію розбору перед `agent_run_from_turn`:

```python
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def usage_from_turn(result: Any) -> tuple[dict[str, int], int]:
    """Витягти токени останнього ходу та розмір контекстного вікна."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return {}, 0
    breakdown = getattr(usage, "last", None)
    values: dict[str, int] = {}
    for name in USAGE_FIELDS:
        raw = getattr(breakdown, name, None)
        try:
            values[name] = int(raw)
        except (TypeError, ValueError):
            values[name] = 0
    try:
        window = int(getattr(usage, "model_context_window", 0) or 0)
    except (TypeError, ValueError):
        window = 0
    return values, window
```

У `agent_run_from_turn` після перевірки статусу:

```python
    values, window = usage_from_turn(result)
    return AgentRun(
        text=str(getattr(result, "final_response", "") or ""),
        items=normalize_items(getattr(result, "items", None)),
        thread_id=str(thread_id or ""),
        usage=values,
        context_window=window,
    )
```

У `_fake_run` додати правдоподібні значення, щоб UI-тести мали з чим працювати:

```python
        return AgentRun(
            text=text,
            items=[{"kind": "fake", "summary": "Тестовий крок", "detail": {}}],
            thread_id=thread_id,
            usage={
                "input_tokens": len(prompt),
                "cached_input_tokens": 0,
                "output_tokens": len(text),
                "reasoning_output_tokens": 0,
                "total_tokens": len(prompt) + len(text),
            },
            context_window=400000,
        )
```

- [ ] **Крок 4: Покласти токени в результат ноди**

У `flowai/engine.py` у `_execute_agent` знайти місце, де формується `NodeResult` для агентської ноди, і додати в `data` перед поверненням:

```python
        if run.usage:
            data["usage"] = {**run.usage, "context_window": run.context_window}
```

(тут `data` — словник, який уже збирається для результату ноди; якщо в поточній реалізації результат агента будується з `text`/`data` в кількох гілках, додати цей блок у кожній гілці перед `return`.)

- [ ] **Крок 5: Тест наскрізного проходження токенів**

Додати в `tests/test_core.py`:

```python
def test_node_result_carries_usage(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    runner = WorkflowRunner(pipeline.workflow)
    checkpoint = runner.run()
    executor_output = checkpoint.outputs[pipeline.executor.id]
    assert executor_output["data"]["usage"]["total_tokens"] > 0
    assert executor_output["data"]["usage"]["context_window"] == 400000
```

- [ ] **Крок 6: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py -q`
Очікується: PASS.

- [ ] **Крок 7: Коміт**

```bash
git add flowai/codex_adapter.py flowai/engine.py tests/test_core.py
git commit -m "feat: збір токенів і розміру контексту з кожного ходу агента"
```

---

### Задача 5: Правка 7 — агрегація статистики (чистий модуль)

**Файли:**
- Створити: `flowai/run_stats.py`
- Створити: `flowai/run_history.py`
- Тест: `tests/test_run_stats.py`

**Інтерфейси:**
- Виробляє:
  - `NodeStat` — датаклас із полями `node_id: str`, `title: str`, `kind: str`, `color: str`, `runs: int`, `attempts: list[float]`, `total_seconds: float`, `average_seconds: float`, `total_tokens: int`, `reasoning_tokens: int`, `context_percent: float`, `failures: int`.
  - `RunStats` — датаклас із полями `nodes: list[NodeStat]`, `total_seconds: float`, `tasks_total_seconds: float`, `run_count: int`.
  - `collect_stats(events: list[dict], colors: dict[str, str]) -> RunStats` — агрегує події одного запуску.
  - `merge_stats(items: list[RunStats]) -> RunStats` — зводить кілька запусків в один звіт.
  - `flowai/run_history.py::load_runs(directory: Path) -> list[list[dict]]` — читає `*/flowai-run.json` і повертає списки подій, найновіші першими.
- Споживає: події `node_finished`, `node_failed`, `work_review_finished`, `tasks_progress` — усі вже емітяться рушієм.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_run_stats.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from flowai.run_history import load_runs
from flowai.run_stats import collect_stats, merge_stats


def _finished(node_id: str, title: str, seconds: float, tokens: int) -> dict:
    return {
        "type": "node_finished",
        "node_id": node_id,
        "node_title": title,
        "result": {
            "duration_seconds": seconds,
            "status": "success",
            "data": {
                "usage": {
                    "total_tokens": tokens,
                    "reasoning_output_tokens": 5,
                    "context_window": 1000,
                }
            },
        },
    }


def test_collect_stats_aggregates_attempts() -> None:
    events = [
        _finished("n1", "Task Executor", 2.0, 100),
        _finished("n1", "Task Executor", 3.0, 300),
        _finished("n2", "Task Reviewer", 1.0, 50),
    ]
    stats = collect_stats(events, {"n1": "#7C3AED", "n2": "#D97706"})
    executor = next(item for item in stats.nodes if item.node_id == "n1")
    assert executor.runs == 2
    assert executor.attempts == [2.0, 3.0]
    assert executor.total_seconds == 5.0
    assert executor.average_seconds == 2.5
    assert executor.total_tokens == 400
    assert executor.context_percent == 30.0
    assert stats.total_seconds == 6.0


def test_merge_stats_sums_runs() -> None:
    first = collect_stats([_finished("n1", "Виконавець", 2.0, 100)], {})
    second = collect_stats([_finished("n1", "Виконавець", 4.0, 100)], {})
    merged = merge_stats([first, second])
    node = merged.nodes[0]
    assert node.runs == 2
    assert node.total_seconds == 6.0
    assert merged.run_count == 2


def test_load_runs_reads_newest_first(tmp_path: Path) -> None:
    for name, workflow in (("20260101-000000", "A"), ("20260102-000000", "B")):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "flowai-run.json").write_text(
            json.dumps({"workflow": workflow, "status": "success", "events": []}),
            encoding="utf-8",
        )
    runs = load_runs(tmp_path)
    assert len(runs) == 2
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_run_stats.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.run_stats'`.

- [ ] **Крок 3: Створити `flowai/run_stats.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FINISH_EVENTS = frozenset({"node_finished", "work_review_finished"})
FAIL_EVENTS = frozenset({"node_failed", "work_review_failed"})


@dataclass(slots=True)
class NodeStat:
    node_id: str
    title: str = ""
    kind: str = ""
    color: str = "#CBD5E1"
    runs: int = 0
    attempts: list[float] = field(default_factory=list)
    total_seconds: float = 0.0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    context_window: int = 0
    peak_tokens: int = 0
    failures: int = 0

    @property
    def average_seconds(self) -> float:
        if not self.attempts:
            return 0.0
        return round(self.total_seconds / len(self.attempts), 3)

    @property
    def context_percent(self) -> float:
        if not self.context_window:
            return 0.0
        return round(self.peak_tokens / self.context_window * 100, 1)


@dataclass(slots=True)
class RunStats:
    nodes: list[NodeStat] = field(default_factory=list)
    total_seconds: float = 0.0
    tasks_total_seconds: float = 0.0
    run_count: int = 1


def _stat_for(bucket: dict[str, NodeStat], node_id: str) -> NodeStat:
    if node_id not in bucket:
        bucket[node_id] = NodeStat(node_id=node_id)
    return bucket[node_id]


def collect_stats(
    events: list[dict[str, Any]], colors: dict[str, str] | None = None
) -> RunStats:
    """Звести події одного запуску в статистику по блоках."""
    palette = colors or {}
    bucket: dict[str, NodeStat] = {}
    tasks_total = 0.0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        node_id = str(event.get("node_id") or "")
        if event_type == "tasks_progress":
            try:
                tasks_total = max(tasks_total, float(event.get("total_seconds", 0.0)))
            except (TypeError, ValueError):
                pass
            continue
        if not node_id or event_type not in FINISH_EVENTS | FAIL_EVENTS:
            continue
        stat = _stat_for(bucket, node_id)
        stat.title = str(event.get("node_title") or stat.title or node_id[:6])
        stat.color = palette.get(node_id, stat.color)
        result = event.get("result")
        result = result if isinstance(result, dict) else {}
        try:
            seconds = float(result.get("duration_seconds", 0.0) or 0.0)
        except (TypeError, ValueError):
            seconds = 0.0
        stat.runs += 1
        stat.attempts.append(round(seconds, 3))
        stat.total_seconds = round(stat.total_seconds + seconds, 3)
        if event_type in FAIL_EVENTS:
            stat.failures += 1
        data = result.get("data")
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            stat.total_tokens += int(usage.get("total_tokens", 0) or 0)
            stat.reasoning_tokens += int(usage.get("reasoning_output_tokens", 0) or 0)
            stat.peak_tokens = max(
                stat.peak_tokens, int(usage.get("total_tokens", 0) or 0)
            )
            stat.context_window = max(
                stat.context_window, int(usage.get("context_window", 0) or 0)
            )
    nodes = sorted(bucket.values(), key=lambda item: item.total_seconds, reverse=True)
    return RunStats(
        nodes=nodes,
        total_seconds=round(sum(item.total_seconds for item in nodes), 3),
        tasks_total_seconds=round(tasks_total, 3),
    )


def merge_stats(items: list[RunStats]) -> RunStats:
    """Скласти кілька запусків в один звіт."""
    bucket: dict[str, NodeStat] = {}
    for stats in items:
        for node in stats.nodes:
            target = _stat_for(bucket, node.node_id)
            target.title = node.title or target.title
            target.color = node.color if node.color != "#CBD5E1" else target.color
            target.runs += node.runs
            target.attempts.extend(node.attempts)
            target.total_seconds = round(target.total_seconds + node.total_seconds, 3)
            target.total_tokens += node.total_tokens
            target.reasoning_tokens += node.reasoning_tokens
            target.failures += node.failures
            target.peak_tokens = max(target.peak_tokens, node.peak_tokens)
            target.context_window = max(target.context_window, node.context_window)
    nodes = sorted(bucket.values(), key=lambda item: item.total_seconds, reverse=True)
    return RunStats(
        nodes=nodes,
        total_seconds=round(sum(item.total_seconds for item in nodes), 3),
        tasks_total_seconds=round(
            sum(item.tasks_total_seconds for item in items), 3
        ),
        run_count=len(items),
    )
```

- [ ] **Крок 4: Створити `flowai/run_history.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
RUN_FILE = "flowai-run.json"
MAX_RUNS = 50


def load_runs(directory: Path, limit: int = MAX_RUNS) -> list[list[dict[str, Any]]]:
    """Прочитати збережені запуски, найновіші першими."""
    if not directory.is_dir():
        return []
    files = sorted(
        directory.glob(f"*/{RUN_FILE}"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    runs: list[list[dict[str, Any]]] = []
    for path in files[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            LOGGER.warning("Не вдалося прочитати журнал запуску %s", path)
            continue
        events = payload.get("events")
        if isinstance(events, list):
            runs.append([item for item in events if isinstance(item, dict)])
    return runs
```

- [ ] **Крок 5: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_run_stats.py -q`
Очікується: PASS, три тести.

- [ ] **Крок 6: Коміт**

```bash
git add flowai/run_stats.py flowai/run_history.py tests/test_run_stats.py
git commit -m "feat: модуль агрегації статистики запусків"
```

---

### Задача 6: Правка 7 — вікно Stats і кнопка на панелі

**Файли:**
- Створити: `flowai/ui/stats_dialog.py`
- Змінити: `flowai/ui/main_window.py:925-983` (`_build_toolbar`), `flowai/ui/main_window.py:2511-2528` (`_update_workspace_actions`)
- Змінити: `flowai/ui/theme.py` (стиль кнопки `statsButton`)
- Тест: `tests/test_workspaces_ui.py`

**Інтерфейси:**
- Споживає: `collect_stats`, `merge_stats`, `load_runs`, `WorkspaceSession.run_events`, `NODE_COLORS`.
- Виробляє: `StatsDialog(session: WorkspaceSession, parent: QWidget | None)` із публічними атрибутами `tree: QTreeWidget`, `scope: QComboBox`, методом `refresh() -> None`. `MainWindow.show_run_stats() -> None` і `MainWindow.stats_action: QAction`.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_stats_dialog_lists_nodes_with_time_and_tokens() -> None:
    application()
    session = WorkspaceSession(display_name="Тест")
    session.run_events = [
        {
            "type": "node_finished",
            "node_id": "n1",
            "node_title": "Task Executor",
            "result": {
                "duration_seconds": 4.0,
                "data": {"usage": {"total_tokens": 200, "context_window": 1000}},
            },
        }
    ]
    dialog = StatsDialog(session)
    heading = dialog.tree.topLevelItem(0)
    assert heading.text(0).startswith("Task Executor")
    assert heading.text(1) == "1"
    assert "4.0" in heading.text(2)
    assert "200" in heading.text(4)
    dialog.deleteLater()
```

Додати `StatsDialog` до імпорту з `flowai.ui.stats_dialog` угорі тесту.

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py::test_stats_dialog_lists_nodes_with_time_and_tokens -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.stats_dialog'`.

- [ ] **Крок 3: Створити `flowai/ui/stats_dialog.py`**

```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import NODE_COLORS
from ..run_history import load_runs
from ..run_stats import NodeStat, RunStats, collect_stats, merge_stats
from ..workspaces import WorkspaceSession

HEADERS = [
    "Блок",
    "Запусків",
    "Сумарно",
    "У середньому",
    "Токени",
    "% контексту",
]


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} с"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes):02d}:{remainder:04.1f}"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}:{minutes:02d}:{int(remainder):02d}"


class StatsDialog(QDialog):
    """Скільки разів працював кожен блок і скільки це коштувало часу й токенів."""

    def __init__(
        self, session: WorkspaceSession, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"Stats — {session.display_name}")
        self.setMinimumSize(880, 540)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItem("Цей запуск", "current")
        self.scope.addItem("Усі запуски цього Flow", "history")
        self.scope.currentIndexChanged.connect(self.refresh)
        controls.addWidget(QLabel("Обсяг:"))
        controls.addWidget(self.scope)
        controls.addStretch()
        layout.addLayout(controls)

        self.tree = QTreeWidget()
        self.tree.setObjectName("generatedFilesTree")
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setColumnWidth(0, 300)
        self.tree.setAlternatingRowColors(False)
        layout.addWidget(self.tree, 1)

        self.summary = QLabel("")
        self.summary.setObjectName("mutedLabel")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _colors(self) -> dict[str, str]:
        workflow = self.session.workflow
        if workflow is None:
            return {}
        return {
            node.id: NODE_COLORS.get(node.kind, "#CBD5E1") for node in workflow.nodes
        }

    def _stats(self) -> RunStats:
        colors = self._colors()
        if self.scope.currentData() == "history":
            directory = self.session.run_directory
            root = Path(directory).parent if directory else None
            runs = load_runs(root) if root else []
            if not runs:
                return collect_stats(self.session.run_events, colors)
            return merge_stats([collect_stats(events, colors) for events in runs])
        return collect_stats(self.session.run_events, colors)

    def refresh(self) -> None:
        stats = self._stats()
        self.tree.clear()
        if not stats.nodes:
            empty = QTreeWidgetItem(["Даних про запуски ще немає", "", "", "", "", ""])
            empty.setForeground(0, QColor("#94A3B8"))
            self.tree.addTopLevelItem(empty)
            self.summary.setText("")
            return
        for node in stats.nodes:
            self.tree.addTopLevelItem(self._node_item(node))
        parts = [f"Сумарний час блоків: {format_seconds(stats.total_seconds)}"]
        if stats.tasks_total_seconds:
            parts.append(f"час завдань: {format_seconds(stats.tasks_total_seconds)}")
        if stats.run_count > 1:
            parts.append(f"запусків у вибірці: {stats.run_count}")
        self.summary.setText(" · ".join(parts))

    def _node_item(self, node: NodeStat) -> QTreeWidgetItem:
        heading = QTreeWidgetItem(
            [
                node.title,
                str(node.runs),
                format_seconds(node.total_seconds),
                format_seconds(node.average_seconds),
                f"{node.total_tokens:,}".replace(",", " "),
                f"{node.context_percent:.1f}%" if node.context_window else "—",
            ]
        )
        color = QColor(node.color)
        font = heading.font(0)
        font.setBold(True)
        heading.setFont(0, font)
        heading.setForeground(0, color)
        for column in range(1, len(HEADERS)):
            heading.setForeground(column, QColor("#E5E7EB"))
        if node.failures:
            heading.setToolTip(0, f"Помилок: {node.failures}")
        for index, seconds in enumerate(node.attempts, start=1):
            attempt = QTreeWidgetItem(
                [f"Спроба {index}", "", format_seconds(seconds), "", "", ""]
            )
            attempt.setForeground(0, QColor("#E5E7EB"))
            attempt.setForeground(2, QColor("#94A3B8"))
            heading.addChild(attempt)
        heading.setExpanded(True)
        return heading
```

- [ ] **Крок 4: Додати кнопку Stats на панель**

У `flowai/ui/main_window.py` імпортувати вікно: `from .stats_dialog import StatsDialog`.

У `_build_toolbar` після блока з `files_action` додати:

```python
        self.stats_action = QAction("Stats", self)
        self.stats_action.setEnabled(False)
        self.stats_action.triggered.connect(self.show_run_stats)
        toolbar.addAction(self.stats_action)
        self.stats_button = toolbar.widgetForAction(self.stats_action)
        if self.stats_button is not None:
            self.stats_button.setObjectName("statsButton")
```

Додати метод поруч із `show_generated_files`:

```python
    def show_run_stats(self) -> None:
        session = self.current_workspace
        if session is None:
            return
        dialog = StatsDialog(session, self)
        dialog.exec()
```

У `_update_workspace_actions` увімкнення `files_action` супроводити тим самим для `stats_action` — обидві кнопки активні, коли в сесії є події запуску.

- [ ] **Крок 5: Стиль кнопки**

У `flowai/ui/theme.py` після блока `QToolButton#filesButton` додати:

```css
QToolButton#statsButton {
    background: #312E81;
    border-color: #6366F1;
    color: #E0E7FF;
    font-weight: 600;
}
QToolButton#statsButton:hover { background: #4338CA; color: #FFFFFF; }
QToolButton#statsButton:disabled {
    background: #374151;
    border-color: #4B5563;
    color: #9CA3AF;
}
```

- [ ] **Крок 6: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.

- [ ] **Крок 7: Документація**

У `README.md` після абзацу про кнопку **Files** додати:

```markdown
Кнопка **Stats** поруч показує, скільки разів працював кожен блок, скільки
зайняла кожна спроба, сумарний і середній час, витрачені токени та наскільки
блок заповнив контекстне вікно моделі. Перемикач угорі показує або поточний
запуск, або зведення по всіх збережених запусках цього Flow.
```

- [ ] **Крок 8: Коміт**

```bash
git add flowai/ui/stats_dialog.py flowai/ui/main_window.py flowai/ui/theme.py tests/test_workspaces_ui.py README.md
git commit -m "feat: вікно Stats із часом, токенами та історією запусків"
```

---

### Задача 7: Правка 10 — вікно «Результати» при поверненні до завершеного Flow

**Файли:**
- Створити: `flowai/ui/results_dialog.py`
- Змінити: `flowai/ui/main_window.py:1922-1987` (`select_workspace`)
- Тест: `tests/test_workspaces_ui.py`

**Інтерфейси:**
- Споживає: `WorkspaceSession.unread_result` (уже виставляється в `_run_completed`, коли Flow завершився не у вибраному середовищі), `WorkspaceSession.generated_file_groups`, `WorkspaceSession.checkpoint`, `WorkspaceSession.task_states`.
- Виробляє: `ResultsDialog(session, parent)` із атрибутом `files: QTreeWidget` і методом `result_paths() -> list[str]`; `MainWindow.show_run_results() -> None`.

**Логіка показу:** у `select_workspace` прапорець `session.unread_result` зараз просто скидається в `False`. Треба запам'ятати його значення до скидання і, якщо він був `True` і `run_state` у `{"completed", "completed_with_failures", "failed"}`, показати вікно через `QTimer.singleShot(0, ...)` — інакше воно з'явиться раніше, ніж канвас перемалюється.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_results_dialog_shows_final_files_only() -> None:
    application()
    session = WorkspaceSession(display_name="Тест")
    session.generated_file_groups = [
        {
            "node_id": "n1",
            "node_title": "Task Executor",
            "iteration": 1,
            "color": "#7C3AED",
            "intermediate": ["C:/tmp/step.png"],
            "result": ["C:/tmp/final.md"],
        }
    ]
    dialog = ResultsDialog(session)
    assert dialog.result_paths() == ["C:/tmp/final.md"]
    dialog.deleteLater()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py::test_results_dialog_shows_final_files_only -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.results_dialog'`.

- [ ] **Крок 3: Створити `flowai/ui/results_dialog.py`**

```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..workspaces import WorkspaceSession

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
THUMBNAIL_HEIGHT = 46


class ResultsDialog(QDialog):
    """Підсумок завершеного Flow: що вийшло і де це лежить."""

    def __init__(
        self, session: WorkspaceSession, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(f"Результати — {session.display_name}")
        self.setMinimumSize(760, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(self._headline())

        self.files = QTreeWidget()
        self.files.setObjectName("generatedFilesTree")
        self.files.setColumnCount(2)
        self.files.setHeaderLabels(["Файл", "Повний шлях"])
        self.files.setColumnWidth(0, 300)
        self.files.setAlternatingRowColors(False)
        self.files.setIconSize(self.files.iconSize().expandedTo(
            self.files.iconSize().scaled(THUMBNAIL_HEIGHT, THUMBNAIL_HEIGHT,
                                         Qt.AspectRatioMode.KeepAspectRatio)
        ))
        layout.addWidget(self.files, 1)

        self.all_files_button = QPushButton("Усі файли запуску")
        self.stats_button = QPushButton("Статистика")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(
            self.all_files_button, QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.addButton(self.stats_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _headline(self) -> QLabel:
        tasks = [
            state
            for states in self.session.task_states.values()
            for state in states
        ]
        done = sum(1 for item in tasks if item.get("status") == "completed")
        failed = sum(1 for item in tasks if item.get("status") == "failed")
        seconds = sum(float(item.get("seconds", 0.0) or 0.0) for item in tasks)
        parts = [f"Flow «{self.session.display_name}» завершено"]
        if tasks:
            parts.append(f"виконано {done}/{len(tasks)}")
            if failed:
                parts.append(f"провалено {failed}")
            parts.append(f"час завдань {seconds:.1f} с")
        label = QLabel(" · ".join(parts))
        label.setObjectName("sectionTitle")
        label.setWordWrap(True)
        return label

    def result_paths(self) -> list[str]:
        paths: list[str] = []
        for group in self.session.generated_file_groups:
            for raw in group.get("result", []):
                text = str(raw)
                if text and text not in paths:
                    paths.append(text)
        return paths

    def _fill(self) -> None:
        self.files.clear()
        paths = self.result_paths()
        if not paths:
            empty = QTreeWidgetItem(["Фінальних файлів немає", ""])
            empty.setForeground(0, QColor("#94A3B8"))
            self.files.addTopLevelItem(empty)
            return
        for raw in paths:
            path = Path(raw)
            item = QTreeWidgetItem([path.name or raw, raw])
            item.setData(0, Qt.ItemDataRole.UserRole, raw)
            item.setForeground(0, QColor("#E5E7EB"))
            item.setForeground(1, QColor("#94A3B8"))
            if path.suffix.casefold() in IMAGE_SUFFIXES and path.is_file():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    item.setIcon(
                        0,
                        pixmap.scaledToHeight(
                            THUMBNAIL_HEIGHT, Qt.TransformationMode.SmoothTransformation
                        ),
                    )
            self.files.addTopLevelItem(item)
```

- [ ] **Крок 4: Підключити вікно до перемикання середовищ**

У `flowai/ui/main_window.py` імпортувати `from .results_dialog import ResultsDialog`.

У `select_workspace` рядок `session.unread_result = False` замінити на:

```python
        had_unread_result = session.unread_result
        session.unread_result = False
```

а в кінці методу, поруч із наявним показом `_show_pending_intervention`, додати:

```python
        if had_unread_result and session.run_state in {
            "completed",
            "completed_with_failures",
            "failed",
        }:
            QTimer.singleShot(0, self.show_run_results)
```

Додати метод:

```python
    def show_run_results(self) -> None:
        session = self.current_workspace
        if session is None:
            return
        dialog = ResultsDialog(session, self)
        dialog.all_files_button.clicked.connect(dialog.accept)
        dialog.all_files_button.clicked.connect(self.show_generated_files)
        dialog.stats_button.clicked.connect(dialog.accept)
        dialog.stats_button.clicked.connect(self.show_run_stats)
        dialog.exec()
```

- [ ] **Крок 5: Тест на автопоказ**

Додати в `tests/test_workspaces_ui.py` тест, який перевіряє, що прапорець зчитується до скидання:

```python
def test_switching_to_finished_workspace_requests_results(monkeypatch) -> None:
    application()
    window = MainWindow()
    session = window.current_workspace
    assert session is not None
    session.run_state = "completed"
    session.unread_result = True
    shown: list[str] = []
    monkeypatch.setattr(
        MainWindow, "show_run_results", lambda self: shown.append("shown")
    )
    window.select_workspace(session.id)
    QApplication.processEvents()
    assert shown == ["shown"]
    window.close()
```

- [ ] **Крок 6: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 7: Коміт**

```bash
git add flowai/ui/results_dialog.py flowai/ui/main_window.py tests/test_workspaces_ui.py
git commit -m "feat: вікно Результати при поверненні до завершеного Flow"
```

---

# ФАЗА 2 — Правка 1: власний бюджет спроб на кожне завдання

### Задача 8: Модель — третій порт Result, ліміт спроб і статус «провалено»

**Файли:**
- Змінити: `flowai/models.py:47-49` (`RESULT_PORTS`), `flowai/models.py:167-173` (конфіг `result`), `flowai/models.py` (`validate`, `result_port_limit`)
- Змінити: `flowai/ui/canvas.py:47-53` (`PORT_COLORS`)
- Тест: `tests/test_core.py`

**Інтерфейси:**
- Виробляє: `RESULT_PORTS = ("true", "false", "exhausted")`; конфіг `result` отримує `task_attempt_limit: int = 2`; `Workflow.exhausted_target(node_id: str) -> FlowNode | None` — повертає Tasks Manager, під'єднаний до жовтого порту, або `None`.
- Споживає: наявний `normalize_managed_tasks`.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_result_has_exhausted_port_and_attempt_limit() -> None:
    result = FlowNode.create("result")
    assert result.config["task_attempt_limit"] == 2
    workflow = Workflow(nodes=[result])
    assert workflow.ports_of(result.id) == ("true", "false", "exhausted")


def test_exhausted_edge_must_target_tasks_manager() -> None:
    workflow = Workflow()
    result = FlowNode.create("result")
    executor = FlowNode.create("executor")
    workflow.nodes = [result, executor]
    workflow.edges = [FlowEdge.create(result.id, executor.id, "exhausted")]
    errors = workflow.validate()
    assert any("EXHAUSTED" in error for error in errors)


def test_exhausted_target_returns_manager() -> None:
    workflow = Workflow()
    result = FlowNode.create("result")
    manager = FlowNode.create("tasks_manager")
    workflow.nodes = [result, manager]
    workflow.edges = [FlowEdge.create(result.id, manager.id, "exhausted")]
    assert workflow.exhausted_target(result.id) is manager
```

- [ ] **Крок 2: Запустити тести і переконатися, що вони падають**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py -k exhausted -q`
Очікується: FAIL — `KeyError: 'task_attempt_limit'`.

- [ ] **Крок 3: Розширити модель**

У `flowai/models.py`:

```python
RESULT_PORTS = ("true", "false", "exhausted")
```

У `_default_config` блок `"result"`:

```python
        "result": {
            "template": "{{work}}",
            "save_path": "",
            "true_limit": 1,
            "false_limit": 3,
            "task_attempt_limit": 2,
            "wait_for_confirmation": False,
        },
```

У `result_port_limit` на початку додати:

```python
        if node.kind == "result" and port == "exhausted":
            # Жовтий вихід не має ліміту: він і є реакцією на вичерпаний ліміт.
            return 10**6
```

Додати метод у `Workflow`:

```python
    def exhausted_target(self, node_id: str) -> FlowNode | None:
        """Tasks Manager, у який веде жовтий вихід Result, якщо він з'єднаний."""
        for edge in self.outgoing(node_id, "exhausted"):
            target = self.find(edge.target)
            if target is not None and target.kind == "tasks_manager":
                return target
        return None
```

У `validate` після циклу перевірки ребер додати:

```python
        for edge in self.edges:
            if edge.source_port != "exhausted":
                continue
            target = self.find(edge.target)
            if target is None or target.kind != "tasks_manager":
                errors.append(
                    "Вихід EXHAUSTED можна з'єднати лише з блоком Tasks Manager"
                )
```

- [ ] **Крок 4: Жовтий колір порту**

У `flowai/ui/canvas.py`:

```python
PORT_COLORS = {
    DEFAULT_PORT: "#A78BFA",
    "true": "#22C55E",
    "false": "#EF4444",
    "exhausted": "#EAB308",
    "next": "#3B82F6",
    "done": "#22C55E",
}
```

- [ ] **Крок 5: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py -q`
Очікується: PASS.

- [ ] **Крок 6: Коміт**

```bash
git add flowai/models.py flowai/ui/canvas.py tests/test_core.py
git commit -m "feat: третій вихід Result EXHAUSTED і ліміт спроб на завдання"
```

---

### Задача 9: Рушій — лічильник спроб на завдання і маршрут у жовтий порт

**Файли:**
- Змінити: `flowai/engine.py:636-726` (`_execute_tasks_manager`), `flowai/engine.py:726-860` (`_execute_result`)
- Змінити: `flowai/engine.py:93-150` (`RunCheckpoint` — новий словник)
- Тест: `tests/test_core.py`

**Інтерфейси:**
- Виробляє: `RunCheckpoint.task_attempts: dict[str, int]` із ключем `f"{result_node_id}:{task_id}"`; у `task_progress[manager_id]` з'являється список `"failed_task_ids"`; у `NodeResult.data` ноди Result — ключі `"task_outcome": "failed"` і `"failed_task_id"`.
- Споживає: `Workflow.exhausted_target`, `RunCheckpoint.task_progress`.

**Ключова тонкість:** `_execute_tasks_manager` зараз безумовно зараховує активне завдання у `completed_task_ids` при кожному вході. Провалене завдання не має туди потрапити, інакше воно намалюється галочкою.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_task_exhausts_own_attempt_budget(tmp_path: Path) -> None:
    """Друга поразка на тому самому завданні йде в EXHAUSTED, а не в діалог."""
    pipeline = Pipeline(tmp_path, with_tasks_manager=True)
    pipeline.result.config["task_attempt_limit"] = 2
    pipeline.result.config["false_limit"] = 99
    pipeline.workflow.edges.append(
        FlowEdge.create(pipeline.result.id, pipeline.manager.id, "exhausted")
    )

    def always_reject(call: dict[str, Any]) -> str:
        if "reviewer-model" == call["model"]:
            return json.dumps(
                {"verdict": False, "score": 1, "reason": "ні", "must_fix": ["фікс"]}
            )
        return "робота"

    codex_adapter.FAKE_RESPONDER = always_reject
    runner = WorkflowRunner(pipeline.workflow)
    checkpoint = runner.run()

    progress = checkpoint.task_progress[pipeline.manager.id]
    assert progress["failed_task_ids"], "Завдання мало бути позначене провальним"
    assert checkpoint.task_attempts[
        f"{pipeline.result.id}:{progress['failed_task_ids'][0]}"
    ] == 2
```

Розширити клас `Pipeline` у тому ж файлі прапорцем `with_tasks_manager`, який додає ноду `tasks_manager` з двома завданнями, ребро `manager --next--> executor`, ребро `result --true--> manager` і робить менеджер коренем маршруту.

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_task_exhausts_own_attempt_budget -q`
Очікується: FAIL — `AttributeError: 'RunCheckpoint' object has no attribute 'task_attempts'`.

- [ ] **Крок 3: Додати лічильник у чекпоінт**

У `flowai/engine.py` у датакласі `RunCheckpoint` додати поле, а також його серіалізацію:

```python
    task_attempts: dict[str, int] = field(default_factory=dict)
```

у `to_dict`: `"task_attempts": dict(self.task_attempts),`
у `from_dict`: `task_attempts=dict(raw.get("task_attempts") or {}),`

- [ ] **Крок 4: Рахувати спроби в `_execute_result`**

У `flowai/engine.py` у `_execute_result` після рядка `port = "true" if verdict else "false"` додати визначення активного завдання:

```python
        with self._control_lock:
            manager = self.workflow.exhausted_target(node.id)
        active_task_id = ""
        if manager is not None:
            progress = self.checkpoint.task_progress.get(manager.id, {})
            active_task_id = str(progress.get("active_task_id", ""))
```

Після блоку, який обробляє відповіді користувача (`add_attempts` / `force_branch`), і **перед** перевіркою глобального ліміту вставити перевірку бюджету завдання:

```python
        failed_task_id = ""
        if port == "false" and manager is not None and active_task_id and not forced:
            attempt_key = f"{node.id}:{active_task_id}"
            used_attempts = self.checkpoint.task_attempts.get(attempt_key, 0) + 1
            self.checkpoint.task_attempts[attempt_key] = used_attempts
            with self._control_lock:
                attempt_limit = max(1, int(node.config.get("task_attempt_limit", 2)))
            if used_attempts >= attempt_limit:
                port = "exhausted"
                failed_task_id = active_task_id
                progress = self.checkpoint.task_progress.setdefault(
                    manager.id,
                    {"active_task_id": "", "completed_task_ids": []},
                )
                failed_ids = progress.setdefault("failed_task_ids", [])
                if active_task_id not in failed_ids:
                    failed_ids.append(active_task_id)
                self._emit(
                    "task_exhausted",
                    node=node,
                    message=(
                        f"Завдання вичерпало {attempt_limit} спроби — "
                        "переходимо до наступного"
                    ),
                    task_id=active_task_id,
                    attempts=used_attempts,
                )
```

Перевірку глобального ліміту (`if not forced and used + 1 > limit:`) обгорнути так, щоб вона не спрацьовувала для жовтого порту:

```python
        key = f"{node.id}:{port}"
        used = self.checkpoint.port_counts.get(key, 0)
        with self._control_lock:
            configured_limit = self.workflow.result_port_limit(node, port)
        limit = configured_limit + self.checkpoint.limit_grants.get(key, 0)
        if port != "exhausted" and not forced and used + 1 > limit:
            raise InterventionRequired(...)   # блок лишається без змін
```

У фінальний словник `data` додати:

```python
        if failed_task_id:
            data["task_outcome"] = "failed"
            data["failed_task_id"] = failed_task_id
```

а гілку тексту доповнити:

```python
        if port == "true":
            ...
        elif port == "exhausted":
            text = reason or "Завдання вичерпало ліміт спроб"
        else:
            text = reason or "Результат відправлено на переробку"
```

- [ ] **Крок 5: Не зараховувати провалені завдання як виконані**

У `flowai/engine.py` у `_execute_tasks_manager` замінити початок методу:

```python
        progress = self.checkpoint.task_progress.setdefault(
            node.id,
            {"active_task_id": "", "completed_task_ids": [], "failed_task_ids": []},
        )
        failed = [
            str(task_id)
            for task_id in progress.get("failed_task_ids", [])
            if str(task_id) in valid_ids
        ]
        completed = [
            str(task_id)
            for task_id in progress.get("completed_task_ids", [])
            if str(task_id) in valid_ids and str(task_id) not in failed
        ]
        active_id = str(progress.get("active_task_id", ""))
        if active_id in valid_ids and active_id not in completed and active_id not in failed:
            completed.append(active_id)
```

Вибір наступного завдання має пропускати і виконані, і провалені:

```python
        finished = set(completed) | set(failed)
        active_task: dict[str, Any] | None = next(
            (task for task in tasks if str(task["id"]) not in finished),
            None,
        )
```

Далі `progress["failed_task_ids"] = failed` поруч із наявними записами прогресу.

У побудові `states` статус визначати так:

```python
            status = (
                "failed"
                if task_id in failed
                else "completed"
                if task_id in completed
                else "running"
                if task_id == active_id
                else "pending"
            )
```

У подію `tasks_progress` і в обидва `data` додати `failed_count=len(failed)`.

- [ ] **Крок 6: Запустити тест і переконатися, що він проходить**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_task_exhausts_own_attempt_budget -q`
Очікується: PASS.

- [ ] **Крок 7: Тест на fallback без жовтого ребра**

Додати в `tests/test_core.py`:

```python
def test_without_exhausted_edge_old_dialog_still_fires(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path, with_tasks_manager=True)
    pipeline.result.config["task_attempt_limit"] = 1
    pipeline.result.config["false_limit"] = 1

    codex_adapter.FAKE_RESPONDER = lambda call: (
        json.dumps({"verdict": False, "score": 1, "reason": "ні", "must_fix": []})
        if call["model"] == "reviewer-model"
        else "робота"
    )
    runner = WorkflowRunner(pipeline.workflow)
    checkpoint = runner.run()
    waiting = [
        item
        for item in checkpoint.outputs.values()
        if item.get("status") == "waiting"
    ]
    assert waiting, "Без жовтого ребра має спрацювати старий діалог ліміту"
```

- [ ] **Крок 8: Прогнати всі тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 9: Коміт**

```bash
git add flowai/engine.py tests/test_core.py
git commit -m "feat: per-task бюджет спроб і маршрут у порт EXHAUSTED"
```

---

### Задача 10: Канвас та Інспектор — хрестик, жовтий порт, поле ліміту

**Файли:**
- Змінити: `flowai/ui/canvas.py:363-399` (`_layout_ports`, `refresh_port_labels`), `502-553` (`_paint_tasks`)
- Змінити: `flowai/ui/inspector.py:66-71`, `505-515`, `547-549`, `576-578`, `702-705`, `786-789`, `840-845`
- Змінити: `flowai/ui/main_window.py` (`_handle_run_event` — подія `task_exhausted`)
- Тест: `tests/test_workspaces_ui.py`

**Інтерфейси:**
- Споживає: `PORT_COLORS["exhausted"]`, статус завдання `"failed"`, конфіг `task_attempt_limit`.
- Виробляє: `Inspector.task_attempt_limit: NoWheelSpinBox`.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_workspaces_ui.py`:

```python
def test_result_node_has_three_ports_and_attempt_field() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Тест")
    result = FlowNode.create("result")
    workflow.nodes = [result]
    scene.set_workflow(workflow)
    item = scene.node_items[result.id]
    assert set(item.output_ports) == {"true", "false", "exhausted"}

    inspector = Inspector()
    inspector.set_workflow(workflow)
    inspector.show_node(result)
    assert inspector.task_attempt_limit.value() == 2
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py::test_result_node_has_three_ports_and_attempt_field -q`
Очікується: FAIL — порти не збігаються.

- [ ] **Крок 3: Розкласти три порти**

У `flowai/ui/canvas.py` у `_layout_ports` гілку `result` замінити на:

```python
        elif self.model.kind == "result":
            for index, name in enumerate(("true", "false", "exhausted"), start=1):
                port = self.output_ports.get(name)
                if port is not None:
                    port.setPos(self.node_width, self.node_height * index / 4)
```

У `refresh_port_labels` цикл `for name in ("true", "false"):` лишити як є — жовтий порт лічильника не показує; замість цього після циклу додати:

```python
        exhausted = self.output_ports.get("exhausted")
        if exhausted is not None:
            limit = int(self.model.config.get("task_attempt_limit", 2))
            exhausted.set_label(f"EXHAUSTED {limit}")
```

У `_minimum_height` для ноди `result` підняти мінімум, щоб три порти не злипались: повертати `max(150.0, ...)` → для `result` використати `max(170.0, ...)`.

- [ ] **Крок 4: Червоний хрестик для провалених завдань**

У `flowai/ui/canvas.py` у `_paint_tasks` у ланцюжку статусів додати гілку перед `else`:

```python
            elif status == "failed":
                painter.setPen(QPen(QColor("#EF4444"), 2.4))
                painter.drawLine(QPointF(14, y + 4), QPointF(24, y + 13))
                painter.drawLine(QPointF(24, y + 4), QPointF(14, y + 13))
```

Колір тексту заголовка для провалених теж зробити червонуватим:

```python
            if status == "running":
                painter.setPen(QColor("#E5E7EB"))
            elif status == "failed":
                painter.setPen(QColor("#FCA5A5"))
            else:
                painter.setPen(QColor("#CBD5E1"))
```

У футері показати провалені:

```python
        failed = sum(1 for task in self.task_states if task.get("status") == "failed")
        summary = f"Виконано {completed}/{len(self.task_states)}"
        if failed:
            summary += f" · провалено {failed}"
```

- [ ] **Крок 5: Поле ліміту в Інспекторі**

У `flowai/ui/inspector.py`:
- у словнику дозволених полів для `"result"` (рядки 66–71) додати `"task_attempt_limit"`;
- після `self.false_limit` створити віджет:

```python
        self.task_attempt_limit = NoWheelSpinBox()
        self.task_attempt_limit.setRange(1, 99)
        self.task_attempt_limit.setToolTip(
            "Скільки разів одне завдання Tasks Manager може піти на переробку, "
            "перш ніж спрацює жовтий вихід EXHAUSTED"
        )
        self.node_form.addRow("Ліміт спроб на завдання", self.task_attempt_limit)
```

- додати віджет у мапу полів (`"task_attempt_limit": self.task_attempt_limit`);
- підключити сигнал: `self.task_attempt_limit.valueChanged.connect(self._save_node)`;
- увімкнути разом з іншими полями Result: `self.task_attempt_limit.setEnabled(True)`;
- завантаження: `self.task_attempt_limit.setValue(int(node.config.get("task_attempt_limit", 2)))`;
- збереження: `node.config["task_attempt_limit"] = self.task_attempt_limit.value()`.

- [ ] **Крок 6: Показати подію в журналі**

У `flowai/ui/main_window.py` у `_handle_run_event` додати гілку перед `elif message:`:

```python
        elif event_type == "task_exhausted":
            self._append_session_log(session, f"{prefix}: ✖ {message}", color=color)
```

- [ ] **Крок 7: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 8: Коміт**

```bash
git add flowai/ui/canvas.py flowai/ui/inspector.py flowai/ui/main_window.py tests/test_workspaces_ui.py
git commit -m "feat: жовтий порт EXHAUSTED, червоний хрестик і поле ліміту спроб"
```

---

### Задача 11: Підсумок запуску з провалами

**Файли:**
- Змінити: `flowai/workspaces.py` (`status_text`)
- Змінити: `flowai/ui/main_window.py:1675-1723` (`_run_completed`)
- Змінити: `flowai/work_review.py` (запис провалених завдань у протокол)
- Тест: `tests/test_workspaces_ui.py`, `tests/test_core.py`

**Інтерфейси:**
- Виробляє: новий `run_state` — `"completed_with_failures"`; `status_text` для нього — `"Виконано з провалами"`.
- Споживає: `session.task_states` (де вже є статуси `failed`).

- [ ] **Крок 1: Написати падаючий тест**

```python
def test_run_with_failed_tasks_reports_partial_success() -> None:
    application()
    window = MainWindow()
    session = window.current_workspace
    assert session is not None
    session.task_states = {
        "n1": [
            {"id": "t1", "title": "Перше", "status": "completed", "seconds": 1.0},
            {"id": "t2", "title": "Друге", "status": "failed", "seconds": 2.0},
        ]
    }
    window._run_completed(session.id, None)
    assert session.run_state == "completed_with_failures"
    assert session.status_text == "Виконано з провалами"
    window.close()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py::test_run_with_failed_tasks_reports_partial_success -q`
Очікується: FAIL — `assert 'completed' == 'completed_with_failures'`.

- [ ] **Крок 3: Додати статус**

У `flowai/workspaces.py` у `status_text` перед перевіркою `completed` додати:

```python
        if self.run_state == "completed_with_failures":
            return "Виконано з провалами"
```

У `flowai/ui/main_window.py` у `_run_completed` у гілці успіху:

```python
        else:
            failed_tasks = [
                state
                for states in session.task_states.values()
                for state in states
                if state.get("status") == "failed"
            ]
            if failed_tasks:
                session.run_state = "completed_with_failures"
                status = "completed_with_failures"
                message = f"■ Виконано, провалено завдань: {len(failed_tasks)}"
            else:
                session.run_state = "completed"
                status, message = "success", "■ Виконання завершено"
            session.unread_result = session.id != self.current_workspace_id
```

Умову сповіщення розширити: `if status in {"success", "failed", "completed_with_failures"}:`.

- [ ] **Крок 4: Записати провали в протокол Work Reviewer**

У `flowai/work_review.py` у методі, що закриває протокол (`finish`), додати рядок із підсумком завдань. Метод отримує статус запуску; додати необов'язковий аргумент:

```python
    def finish(self, status: str, failed_tasks: list[str] | None = None) -> None:
```

і дописати в кінець протоколу, коли список непорожній:

```markdown
## Провалені завдання

- <заголовок завдання> — вичерпано ліміт спроб
```

У `flowai/engine.py` у виклику `self.protocol.finish(status)` передати список заголовків провалених завдань, зібраний із `self.checkpoint.task_progress`.

- [ ] **Крок 5: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 6: Документація**

У `README.md` і `FLOWAI_NODE_GUIDE.md` описати новий вихід:

```markdown
Блок **Result** має третій вихід **EXHAUSTED** (жовтий). Він спрацьовує, коли
одне завдання Tasks Manager вичерпало власний ліміт спроб (поле «Ліміт спроб на
завдання», за замовчуванням 2). Завдання отримує червоний хрестик, Flow без
жодного діалогу переходить до наступного завдання, а в кінці запуск позначається
як «Виконано з провалами». Якщо жовтий вихід нікуди не з'єднаний, поводження
лишається старим: Flow зупиняється й питає, чи додати спроби.
```

- [ ] **Крок 7: Коміт**

```bash
git add flowai/workspaces.py flowai/ui/main_window.py flowai/work_review.py flowai/engine.py README.md FLOWAI_NODE_GUIDE.md tests/
git commit -m "feat: статус Виконано з провалами і провалені завдання в протоколі"
```

---

# ФАЗА 3 — Сучасний інтерфейс

**Чому саме тут.** Ця фаза вставлена перед чатом і AI-вікнами навмисно: у фазах 4–5 з'являється сім нових вікон, і будувати їх треба вже на новій дизайн-системі, інакше доведеться перефарбовувати двічі. Вікна з фази 1 (Stats, Результати, Files) перефарбуються самі, бо стилізуються через тему за objectName.

**Що саме зараз виглядає старомодно (за результатами огляду коду):**
- Усі 8 вікон отримують світлий системний заголовок — програма темна, шапка біла.
- Два джерела розміру шрифту: `app.setFont(QFont("Segoe UI", 10))` у `flowai/app.py:31` і `font-size: 13px` у `flowai/ui/theme.py`.
- Локальні `setStyleSheet` повз тему: `main_window.py:1018`, `main_window.py:1033`, `workspace_sidebar.py:118`, `workspace_sidebar.py:157`, `workspace_sidebar.py:193`, `attachments.py:34`, `inspector.py:936-939`.
- Плоска палітра без ієрархії поверхонь; радіуси 3/4/5/6/7/18 без системи; межі високого контрасту (`#344259` на `#1F2937`).
- Анімації: `_blink_timer` на 550 мс дає різке ввімк/вимк блимання; `_running_timer` перемальовує на 10 fps, через що пульсація смикається. Плавних переходів немає ніде — у Qt QSS немає `transition`.

**Рішення grill-сесії:** темна системна шапка через DWM (без безрамкових вікон); єдина дизайн-система з токенами; вбудовані Inter + JetBrains Mono; та сама темно-синя база, але з глибиною (поверхні, радіуси 8/12/16, тіні, фокус-кільце); анімації — функціональні плюс канвас.

**Припущення, яке я приймаю без окремого питання:** іконки — вбудований мінімальний набір SVG у стилі Lucide (ліцензія MIT) у `flowai/ui/assets/icons/`, який рендериться через `QtSvg` і підфарбовується кольором із токенів. Це логічно випливає з рішення вбудувати шрифти й потрібно для кнопок «трьох рівнів».

---

### Задача 12: Темна шапка вікон і вбудовані шрифти

**Файли:**
- Створити: `flowai/ui/platform.py`
- Створити: `flowai/ui/assets/fonts/` — `Inter-Regular.ttf`, `Inter-Medium.ttf`, `Inter-SemiBold.ttf`, `Inter-Bold.ttf`, `JetBrainsMono-Regular.ttf`
- Створити: `flowai/ui/typography.py`
- Змінити: `flowai/app.py:22-34`
- Змінити: `pyproject.toml` (package-data)
- Тест: `tests/test_theme_ui.py`

**Інтерфейси:**
- Виробляє:
  - `flowai/ui/platform.py::apply_dark_titlebar(widget: QWidget) -> bool` — вмикає темну шапку конкретному вікну, повертає `True`, якщо вдалося.
  - `flowai/ui/platform.py::DarkTitleBarFilter(QObject)` — глобальний фільтр подій, який чіпляє темну шапку кожному новому вікну верхнього рівня; `install_dark_titlebar(app: QApplication) -> DarkTitleBarFilter`.
  - `flowai/ui/typography.py::load_fonts() -> tuple[str, str]` — повертає `(ui_family, mono_family)`, з відкатом на `("Segoe UI", "Consolas")`, якщо ttf не знайдено.
- Споживає: нічого з попередніх задач.

- [ ] **Крок 1: Покласти шрифти в репозиторій**

Завантажити Inter (github.com/rsms/inter, OFL) і JetBrains Mono (github.com/JetBrains/JetBrainsMono, OFL), покласти статичні `.ttf` у `flowai/ui/assets/fonts/` під іменами зі списку файлів вище. Поруч створити `flowai/ui/assets/fonts/LICENSE.txt` із текстом обох ліцензій OFL.

- [ ] **Крок 2: Написати падаючий тест**

Створити `tests/test_theme_ui.py`:

```python
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from flowai.ui.platform import apply_dark_titlebar
from flowai.ui.typography import load_fonts


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_fonts_load_and_report_families() -> None:
    application()
    ui_family, mono_family = load_fonts()
    assert ui_family in {"Inter", "Segoe UI"}
    assert mono_family in {"JetBrains Mono", "Consolas"}


def test_apply_dark_titlebar_never_raises() -> None:
    application()
    widget = QWidget()
    widget.show()
    assert apply_dark_titlebar(widget) in {True, False}
    widget.close()
```

- [ ] **Крок 3: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_theme_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.platform'`.

- [ ] **Крок 4: Створити `flowai/ui/platform.py`**

```python
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

LOGGER = logging.getLogger(__name__)

# Windows 10 1903+ використовує атрибут 20; ранні збірки 1809–1903 — 19.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19


def apply_dark_titlebar(widget: QWidget) -> bool:
    """Зробити системну шапку вікна темною. Поза Windows — тихо нічого не робить."""
    if sys.platform != "win32":
        return False
    try:
        handle = wintypes.HWND(int(widget.winId()))
    except (RuntimeError, TypeError, ValueError):
        return False
    value = ctypes.c_int(1)
    applied = False
    for attribute in (
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY,
    ):
        try:
            status = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle,
                ctypes.c_int(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except (AttributeError, OSError):
            LOGGER.debug("DwmSetWindowAttribute недоступний", exc_info=True)
            return False
        if status == 0:
            applied = True
            break
    return applied


class DarkTitleBarFilter(QObject):
    """Чіпляє темну шапку кожному вікну верхнього рівня при першому показі."""

    def __init__(self) -> None:
        super().__init__()
        self._done: set[int] = set()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            if watched.isWindow():
                key = id(watched)
                if key not in self._done:
                    self._done.add(key)
                    apply_dark_titlebar(watched)
        return super().eventFilter(watched, event)


def install_dark_titlebar(app: QApplication) -> DarkTitleBarFilter:
    dark_filter = DarkTitleBarFilter()
    app.installEventFilter(dark_filter)
    return dark_filter
```

**Зауваження для виконавця:** якщо на конкретній збірці Windows шапка не перемальовується одразу після `DwmSetWindowAttribute`, це відома поведінка DWM. Правильний обхід — викликати `apply_dark_titlebar` у `showEvent` **до першої відмальовки**, що фільтр і робить, ловлячи `QEvent.Show`. Не використовуйте `hide()/show()` для форсування перемальовки — це дає видиме мерехтіння.

- [ ] **Крок 5: Створити `flowai/ui/typography.py`**

```python
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QFontDatabase

LOGGER = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
UI_FALLBACK = "Segoe UI"
MONO_FALLBACK = "Consolas"


def _load_family(file_names: list[str]) -> str:
    families: list[str] = []
    for name in file_names:
        path = FONTS_DIR / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            LOGGER.warning("Не вдалося завантажити шрифт %s", path)
            continue
        families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families[0] if families else ""


def load_fonts() -> tuple[str, str]:
    """Завантажити вбудовані шрифти; повернути родини для інтерфейсу й коду."""
    ui_family = _load_family(
        [
            "Inter-Regular.ttf",
            "Inter-Medium.ttf",
            "Inter-SemiBold.ttf",
            "Inter-Bold.ttf",
        ]
    )
    mono_family = _load_family(["JetBrainsMono-Regular.ttf"])
    return ui_family or UI_FALLBACK, mono_family or MONO_FALLBACK
```

- [ ] **Крок 6: Підключити у `flowai/app.py`**

Замінити блок налаштування застосунку:

```python
    app = QApplication(sys.argv)
    app.setApplicationName("FlowAI")
    app.setOrganizationName("FlowAI")
    app.setStyle("Fusion")
    ui_family, mono_family = load_fonts()
    base_font = QFont(ui_family, 10)
    base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(base_font)
    app.setStyleSheet(build_style(ui_family, mono_family))
    install_dark_titlebar(app)
```

з імпортами:

```python
from .ui.platform import install_dark_titlebar
from .ui.theme import build_style
from .ui.typography import load_fonts
```

(`build_style` створюється в наступній задачі; поки що додати в `theme.py` тимчасову обгортку `def build_style(ui_family: str, mono_family: str) -> str: return APP_STYLE`, щоб фаза лишалася працездатною покроково.)

- [ ] **Крок 7: Додати ресурси в пакет**

У `pyproject.toml` після секції `[tool.setuptools.packages.find]` додати:

```toml
[tool.setuptools.package-data]
"flowai.ui" = ["assets/fonts/*.ttf", "assets/fonts/LICENSE.txt", "assets/icons/*.svg"]
```

- [ ] **Крок 8: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_theme_ui.py -q`
Очікується: PASS, два тести.

- [ ] **Крок 9: Ручна перевірка**

Запустити `start-flowai.cmd`, відкрити **Файл → Новий**, потім **Settings** і **Files**. Переконатися, що заголовок головного вікна й усіх діалогів темний, а не білий.

- [ ] **Крок 10: Коміт**

```bash
git add flowai/ui/platform.py flowai/ui/typography.py flowai/ui/assets flowai/app.py flowai/ui/theme.py pyproject.toml tests/test_theme_ui.py
git commit -m "feat: темна системна шапка вікон і вбудовані шрифти Inter/JetBrains Mono"
```

---

### Задача 13: Дизайн-токени й переписана тема

**Файли:**
- Створити: `flowai/ui/design.py`
- Переписати: `flowai/ui/theme.py`
- Змінити: `flowai/ui/main_window.py:1018`, `1033`; `flowai/ui/workspace_sidebar.py:118`, `157`, `193`; `flowai/ui/attachments.py:34`; `flowai/ui/inspector.py:936-939`
- Тест: `tests/test_theme_ui.py`

**Інтерфейси:**
- Виробляє:
  - `flowai/ui/design.py::COLORS: dict[str, str]` — ключі: `bg`, `surface`, `surface_raised`, `surface_sunken`, `border`, `border_strong`, `text`, `text_muted`, `text_dim`, `accent`, `accent_hover`, `accent_text`, `success`, `danger`, `warning`, `focus`.
  - `RADII: dict[str, int]` — `sm=8`, `md=12`, `lg=16`, `pill=999`.
  - `SPACE: dict[str, int]` — `xs=4`, `sm=8`, `md=12`, `lg=16`, `xl=24`.
  - `TYPE: dict[str, tuple[int, int]]` — назва → `(розмір_px, вага)`: `caption=(11,500)`, `body=(13,400)`, `label=(13,600)`, `title=(15,600)`, `heading=(20,700)`.
  - `DURATION: dict[str, int]` — `fast=120`, `base=180`, `slow=260`.
  - `flowai/ui/theme.py::build_style(ui_family: str, mono_family: str) -> str`.
- Споживає: `load_fonts` із задачі 12.

**Принцип, який робить вигляд сучасним:** ієрархія поверхонь замість плоского тла. `bg` — найтемніше полотно вікна; `surface` — панелі й діалоги; `surface_raised` — картки, спливні меню, поля вводу у фокусі; `surface_sunken` — поля вводу й журнал. Межі низькоконтрастні (різниця зі своєю поверхнею ≤ 12% яскравості), а виділення дає не зміна межі, а окреме фокус-кільце.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_theme_ui.py`:

```python
from flowai.ui.design import COLORS, DURATION, RADII, TYPE
from flowai.ui.theme import build_style


def test_design_tokens_cover_required_keys() -> None:
    for key in ("bg", "surface", "surface_raised", "border", "text", "accent", "focus"):
        assert key in COLORS and COLORS[key].startswith("#")
    assert RADII["sm"] == 8 and RADII["md"] == 12 and RADII["lg"] == 16
    assert DURATION["fast"] < DURATION["base"] < DURATION["slow"]
    assert TYPE["body"][0] == 13


def test_build_style_uses_given_families_and_tokens() -> None:
    style = build_style("Inter", "JetBrains Mono")
    assert "Inter" in style
    assert "JetBrains Mono" in style
    assert COLORS["accent"] in style
    assert "border-radius: 12px" in style
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_theme_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.design'`.

- [ ] **Крок 3: Створити `flowai/ui/design.py`**

```python
from __future__ import annotations

# Палітра лишається впізнаваною (темно-синя база, фіолетовий акцент),
# але отримує ієрархію поверхонь — саме її брак читається як «плоско й старо».
COLORS: dict[str, str] = {
    "bg": "#0B1017",
    "surface": "#121A26",
    "surface_raised": "#18222F",
    "surface_sunken": "#080D14",
    "border": "#1F2B3A",
    "border_strong": "#2C3B4E",
    "text": "#EDF1F7",
    "text_muted": "#9AA9BD",
    "text_dim": "#64748B",
    "accent": "#6D4AFF",
    "accent_hover": "#8067FF",
    "accent_text": "#FFFFFF",
    "success": "#22C55E",
    "danger": "#EF4444",
    "warning": "#EAB308",
    "focus": "#8B7BFF",
}

RADII: dict[str, int] = {"sm": 8, "md": 12, "lg": 16, "pill": 999}

SPACE: dict[str, int] = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

# назва → (розмір у px, вага)
TYPE: dict[str, tuple[int, int]] = {
    "caption": (11, 500),
    "body": (13, 400),
    "label": (13, 600),
    "title": (15, 600),
    "heading": (20, 700),
}

DURATION: dict[str, int] = {"fast": 120, "base": 180, "slow": 260}

CONTROL_HEIGHT = 34
SHADOW_BLUR = 28
SHADOW_ALPHA = 110
```

- [ ] **Крок 4: Переписати `flowai/ui/theme.py`**

Замінити вміст файлу на функцію, яка збирає QSS із токенів. Повний QSS має покривати ті самі селектори, що й нинішній `APP_STYLE`, плюс `QTreeWidget`/`QHeaderView` із задачі 2. Скелет:

```python
from __future__ import annotations

from .design import COLORS, CONTROL_HEIGHT, RADII, SPACE, TYPE


def build_style(ui_family: str = "Segoe UI", mono_family: str = "Consolas") -> str:
    c = COLORS
    return f"""
QMainWindow, QDialog, QWidget {{
    background: {c["bg"]};
    color: {c["text"]};
    font-family: "{ui_family}";
    font-size: {TYPE["body"][0]}px;
}}
QToolBar {{
    background: {c["surface"]};
    border: none;
    border-bottom: 1px solid {c["border"]};
    spacing: {SPACE["sm"]}px;
    padding: {SPACE["sm"]}px {SPACE["md"]}px;
}}
QLabel#sectionTitle {{
    color: {c["text"]};
    font-size: {TYPE["heading"][0]}px;
    font-weight: {TYPE["heading"][1]};
    padding: {SPACE["xs"]}px 0;
}}
QLabel#mutedLabel {{ color: {c["text_muted"]}; font-size: {TYPE["caption"][0]}px; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {{
    background: {c["surface_sunken"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px;
    color: {c["text"]};
    padding: {SPACE["sm"]}px;
    selection-background-color: {c["accent"]};
    min-height: {CONTROL_HEIGHT - 16}px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {c["focus"]};
    background: {c["surface_raised"]};
}}
QPlainTextEdit#promptEditor, QTextBrowser#logView, QPlainTextEdit#schemaEditor {{
    font-family: "{mono_family}";
    font-size: {TYPE["body"][0]}px;
}}
QListWidget, QTreeWidget, QTreeView {{
    background: {c["surface_sunken"]};
    alternate-background-color: {c["surface_sunken"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["md"]}px;
    color: {c["text"]};
}}
QListWidget::item {{ padding: {SPACE["sm"]}px; border-radius: {RADII["sm"]}px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {c["accent"]};
    color: {c["accent_text"]};
}}
QHeaderView::section {{
    background: {c["surface"]};
    color: {c["text"]};
    border: none;
    border-right: 1px solid {c["border"]};
    padding: {SPACE["sm"]}px;
    font-weight: {TYPE["label"][1]};
}}
QDockWidget {{ color: {c["text"]}; font-weight: {TYPE["label"][1]}; }}
QDockWidget::title {{
    background: {c["surface"]};
    padding: {SPACE["xs"]}px;
    border-bottom: 1px solid {c["border"]};
}}
QDockWidget::close-button {{
    background: {c["danger"]};
    border: none;
    border-radius: {RADII["sm"] // 2}px;
}}
QWidget#dockWidthHandle {{ background: {c["border_strong"]}; }}
QWidget#dockWidthHandle:hover {{ background: {c["focus"]}; }}
QMenu {{
    background: {c["surface_raised"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["md"]}px;
    padding: {SPACE["xs"]}px;
}}
QMenu::item {{ padding: {SPACE["sm"]}px {SPACE["md"]}px; border-radius: {RADII["sm"]}px; }}
QMenu::item:selected {{ background: {c["accent"]}; color: {c["accent_text"]}; }}
QToolTip {{
    background: {c["surface_raised"]};
    color: {c["text"]};
    border: 1px solid {c["border_strong"]};
    border-radius: {RADII["sm"]}px;
    padding: {SPACE["xs"]}px {SPACE["sm"]}px;
}}
QProgressBar {{
    background: {c["surface_sunken"]};
    border: none;
    border-radius: {RADII["sm"] // 2}px;
    max-height: 4px;
}}
QProgressBar::chunk {{ background: {c["accent"]}; border-radius: {RADII["sm"] // 2}px; }}
QStatusBar {{
    background: {c["surface"]};
    color: {c["text_muted"]};
    border-top: 1px solid {c["border"]};
}}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c["border_strong"]};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {c["text_dim"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QSplitter::handle {{ background: {c["border"]}; }}
"""


# Сумісність зі старим ім'ям, доки всі імпорти не переїдуть на build_style.
APP_STYLE = build_style()
```

Стилі кнопок у QSS **не описуються** — кнопки малює власний клас із задачі 14.

- [ ] **Крок 5: Прибрати локальні `setStyleSheet`**

Видалити виклики й перенести їхній сенс у тему через `objectName`:

| Файл і рядок | Що робити |
|---|---|
| `main_window.py:1018` (`node_list`) | видалити виклик, дати `self.node_list.setObjectName("paletteList")` і описати селектор у темі |
| `main_window.py:1033` (кнопки палітри) | видалити; кнопки стають `AnimatedButton` із задачі 14 |
| `workspace_sidebar.py:118` (`WorkspaceCard`) | видалити, дати `setObjectName("workspaceCard")` і описати в темі |
| `workspace_sidebar.py:157` (`status_icon`) | замінити на динамічну властивість `setProperty("state", "running"/"idle"/...)` і селектори `QLabel#workspaceStatus[state="running"]` |
| `workspace_sidebar.py:193` (`list_widget`) | видалити, покривається селектором `QListWidget` |
| `attachments.py:34` | замінити на `setObjectName("imagePreview")` і селектор у темі |
| `inspector.py:936-939` (підсвітка помилки) | замінити на `editor.setProperty("invalid", True/False)` + `style().unpolish/polish` і селектор `QPlainTextEdit[invalid="true"]` |

- [ ] **Крок 6: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS. Якщо якийсь UI-тест перевіряв старі кольори — оновити очікування на токени з `design.py`, а не навпаки.

- [ ] **Крок 7: Ручна перевірка**

Запустити програму й пройтись по всіх вікнах: жодне не має світлого тла, поля вводу «втоплені», панелі «підняті», радіуси однакові.

- [ ] **Крок 8: Коміт**

```bash
git add flowai/ui/design.py flowai/ui/theme.py flowai/ui/main_window.py flowai/ui/workspace_sidebar.py flowai/ui/attachments.py flowai/ui/inspector.py tests/test_theme_ui.py
git commit -m "feat: дизайн-токени, переписана тема, прибрані локальні стилі"
```

---

### Задача 14: Кнопки трьох рівнів з плавним hover та іконками

**Файли:**
- Створити: `flowai/ui/controls.py`
- Створити: `flowai/ui/assets/icons/` — `play.svg`, `square.svg`, `folder.svg`, `chart.svg`, `settings.svg`, `plus.svg`, `trash.svg`, `sparkles.svg`, `check.svg`, `x.svg`, `refresh.svg`, `external-link.svg`
- Створити: `flowai/ui/icons.py`
- Змінити: `flowai/ui/main_window.py:925-983` (`_build_toolbar`), `1002-1061` (`_build_palette`)
- Тест: `tests/test_theme_ui.py`

**Інтерфейси:**
- Виробляє:
  - `flowai/ui/icons.py::icon(name: str, color: str | None = None, size: int = 18) -> QIcon` — рендерить SVG у `QIcon`, підставляючи колір замість `currentColor`.
  - `flowai/ui/controls.py::AnimatedButton(QToolButton)` — конструктор `AnimatedButton(text: str = "", variant: str = "secondary", icon_name: str = "", parent: QWidget | None = None)`; варіанти: `"primary"`, `"secondary"`, `"ghost"`, `"success"`, `"danger"`. Має Qt-властивість `hover_progress: float` (0…1) і `press_progress: float` (0…1), обидві анімовані `QPropertyAnimation` за `DURATION["fast"]`.
- Споживає: `COLORS`, `RADII`, `TYPE`, `DURATION`, `CONTROL_HEIGHT` із `design.py`.

**Чому власний клас, а не QSS.** У Qt Style Sheets немає `transition`, тож `:hover` дає миттєвий стрибок кольору — саме це читається як «дешеві кнопки». Власний `paintEvent` із анімованим прогресом дає плавний перехід і однакову геометрію в усіх вікнах.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_theme_ui.py`:

```python
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QEnterEvent

from flowai.ui.controls import AnimatedButton
from flowai.ui.icons import icon


def test_icon_renders_non_null() -> None:
    application()
    result = icon("play", "#FFFFFF", 18)
    assert not result.isNull()


def test_animated_button_animates_hover() -> None:
    application()
    button = AnimatedButton("Run", variant="primary", icon_name="play")
    assert button.hover_progress == 0.0
    button.enterEvent(
        QEnterEvent(QPoint(1, 1), QPoint(1, 1), QPoint(1, 1))
    )
    assert button._hover_animation.endValue() == 1.0
    assert button.minimumHeight() == 34
    button.deleteLater()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_theme_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.icons'`.

- [ ] **Крок 3: Покласти іконки**

Створити файли в `flowai/ui/assets/icons/` за формою Lucide (MIT). Приклад `play.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round"
     stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>
```

Решта — так само з `stroke="currentColor"`, щоб підфарбовування працювало підстановкою рядка. Додати `flowai/ui/assets/icons/LICENSE.txt` із текстом MIT-ліцензії Lucide.

- [ ] **Крок 4: Створити `flowai/ui/icons.py`**

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .design import COLORS

ICONS_DIR = Path(__file__).parent / "assets" / "icons"


@lru_cache(maxsize=256)
def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """SVG-іконка, підфарбована кольором теми."""
    path = ICONS_DIR / f"{name}.svg"
    if not path.is_file():
        return QIcon()
    markup = path.read_text(encoding="utf-8").replace(
        "currentColor", color or COLORS["text"]
    )
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
```

- [ ] **Крок 5: Створити `flowai/ui/controls.py`**

```python
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QToolButton, QWidget

from .design import COLORS, CONTROL_HEIGHT, DURATION, RADII, SPACE, TYPE
from .icons import icon as load_icon

VARIANTS: dict[str, dict[str, str]] = {
    "primary": {
        "bg": COLORS["accent"],
        "hover": COLORS["accent_hover"],
        "text": COLORS["accent_text"],
        "border": COLORS["accent_hover"],
    },
    "secondary": {
        "bg": COLORS["surface_raised"],
        "hover": COLORS["border_strong"],
        "text": COLORS["text"],
        "border": COLORS["border"],
    },
    "ghost": {
        "bg": "transparent",
        "hover": COLORS["surface_raised"],
        "text": COLORS["text_muted"],
        "border": "transparent",
    },
    "success": {
        "bg": COLORS["success"],
        "hover": "#16A34A",
        "text": "#04140A",
        "border": COLORS["success"],
    },
    "danger": {
        "bg": COLORS["danger"],
        "hover": "#DC2626",
        "text": "#FFFFFF",
        "border": COLORS["danger"],
    },
}


def _blend(start: str, end: str, amount: float) -> QColor:
    first, second = QColor(start), QColor(end)
    if not first.isValid():
        return second
    ratio = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * ratio),
        round(first.green() + (second.green() - first.green()) * ratio),
        round(first.blue() + (second.blue() - first.blue()) * ratio),
        round(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )


class AnimatedButton(QToolButton):
    """Кнопка з плавним hover/press — того, чого QSS у Qt дати не може."""

    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        icon_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.variant = variant if variant in VARIANTS else "secondary"
        self._hover = 0.0
        self._press = 0.0
        self._icon_name = icon_name
        self.setText(text)
        self.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if text
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.setMinimumHeight(CONTROL_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)
        self._apply_icon()

        self._hover_animation = QPropertyAnimation(self, b"hover_progress", self)
        self._hover_animation.setDuration(DURATION["fast"])
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_animation = QPropertyAnimation(self, b"press_progress", self)
        self._press_animation.setDuration(DURATION["fast"])
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _apply_icon(self) -> None:
        if self._icon_name:
            self.setIcon(load_icon(self._icon_name, VARIANTS[self.variant]["text"]))

    def get_hover_progress(self) -> float:
        return self._hover

    def set_hover_progress(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hover_progress = Property(float, get_hover_progress, set_hover_progress)

    def get_press_progress(self) -> float:
        return self._press

    def set_press_progress(self, value: float) -> None:
        self._press = float(value)
        self.update()

    press_progress = Property(float, get_press_progress, set_press_progress)

    def _animate(self, animation: QPropertyAnimation, target: float) -> None:
        animation.stop()
        animation.setStartValue(
            self._hover if animation is self._hover_animation else self._press
        )
        animation.setEndValue(target)
        animation.start()

    def enterEvent(self, event: object) -> None:
        self._animate(self._hover_animation, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._animate(self._hover_animation, 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event: object) -> None:
        self._animate(self._press_animation, 1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: object) -> None:
        self._animate(self._press_animation, 0.0)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: object) -> None:
        palette = VARIANTS[self.variant]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, RADII["sm"], RADII["sm"])

        if self.isEnabled():
            background = _blend(palette["bg"], palette["hover"], self._hover)
            background = _blend(
                background.name(), COLORS["surface_sunken"], self._press * 0.35
            )
            text_color = QColor(palette["text"])
            border_color = QColor(palette["border"])
        else:
            background = QColor(COLORS["surface"])
            text_color = QColor(COLORS["text_dim"])
            border_color = QColor(COLORS["border"])

        painter.fillPath(path, background)
        painter.setPen(border_color)
        painter.drawPath(path)

        content = self.rect().adjusted(SPACE["md"], 0, -SPACE["md"], 0)
        if not self.icon().isNull():
            size = self.iconSize()
            icon_rect = content.adjusted(0, 0, 0, 0)
            icon_rect.setWidth(size.width())
            icon_rect.moveTop(content.center().y() - size.height() // 2 + 1)
            self.icon().paint(painter, icon_rect)
            content.setLeft(icon_rect.right() + SPACE["sm"])

        if self.text():
            font = painter.font()
            font.setPixelSize(TYPE["label"][0])
            font.setWeight(TYPE["label"][1])
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(
                content,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                if not self.icon().isNull()
                else Qt.AlignmentFlag.AlignCenter,
                self.text(),
            )
        painter.end()
```

- [ ] **Крок 6: Перевести панель інструментів на нові кнопки**

У `_build_toolbar` замінити `toolbar.addAction(...)` + `widgetForAction` на власні віджети, зберігши ті самі `QAction` (щоб гарячі клавіші та наявні тести на `run_action` працювали):

```python
        self.run_button = AnimatedButton("Run", variant="success", icon_name="play")
        self.run_button.setDefaultAction(self.run_action)
        self.stop_button = AnimatedButton("Stop", variant="danger", icon_name="square")
        self.stop_button.setDefaultAction(self.stop_action)
        self.files_button = AnimatedButton(
            "Files", variant="secondary", icon_name="folder"
        )
        self.files_button.setDefaultAction(self.files_action)
        self.stats_button = AnimatedButton(
            "Stats", variant="secondary", icon_name="chart"
        )
        self.stats_button.setDefaultAction(self.stats_action)
        for button in (
            self.run_button,
            self.stop_button,
            self.files_button,
            self.stats_button,
        ):
            toolbar.addWidget(button)
```

Кнопку Settings перевести на `AnimatedButton("", variant="ghost", icon_name="settings")` замість намальованої `settings_gear_icon()`; саму функцію `settings_gear_icon` видалити.

У `_build_palette` кнопки додавання нод замінити на `AnimatedButton(NODE_LABELS[kind], variant="secondary", icon_name="plus")`, прибравши локальний `setStyleSheet`.

- [ ] **Крок 7: Оновити тести, які лізли в `widgetForAction`**

Пройтись по `tests/test_workspaces_ui.py` і замінити звернення виду `window.main_toolbar.widgetForAction(window.run_action)` на пряме `window.run_button`.

- [ ] **Крок 8: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 9: Коміт**

```bash
git add flowai/ui/controls.py flowai/ui/icons.py flowai/ui/assets/icons flowai/ui/main_window.py tests/
git commit -m "feat: анімовані кнопки трьох рівнів з іконками"
```

---

### Задача 15: Анімації вікон і плавна пульсація замість блимання

**Файли:**
- Створити: `flowai/ui/motion.py`
- Змінити: `flowai/ui/canvas.py:1049-1055` (таймери), `1340-1390` (`_toggle_blink`, `_sync_running_timer`)
- Змінити: `flowai/ui/main_window.py`, `flowai/ui/stats_dialog.py`, `flowai/ui/results_dialog.py` (поява вікон)
- Тест: `tests/test_theme_ui.py`

**Інтерфейси:**
- Виробляє:
  - `flowai/ui/motion.py::fade_in(widget: QWidget, duration: int | None = None) -> QPropertyAnimation` — поява вікна: прозорість 0→1 плюс підйом на 8 px.
  - `flowai/ui/motion.py::AnimatedDialog(QDialog)` — базовий клас діалогу, який анімує появу у `showEvent` і має темну шапку.
  - `flowai/ui/motion.py::pulse(value: float) -> float` — спільна функція плавної хвилі 0…1.
- Споживає: `DURATION` із `design.py`.

**Що саме прибираємо:** `FlowScene._blink_timer` з інтервалом 550 мс перемикає булеве `_blink` — це різке блимання. Замінюємо на неперервну фазу, яку вже вміє рахувати `NodeItem._running_pulse`, і піднімаємо частоту перемальовки з 10 до 60 fps, але **тільки поки на канвасі є активні ноди** — інакше програма гріє процесор на порожньому Flow.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_theme_ui.py`:

```python
from flowai.ui.motion import pulse


def test_pulse_is_continuous_and_bounded() -> None:
    values = [pulse(step / 10) for step in range(30)]
    assert all(0.0 <= value <= 1.0 for value in values)
    # Плавність: сусідні значення не стрибають більш ніж на 0.35
    assert all(
        abs(second - first) < 0.35 for first, second in zip(values, values[1:])
    )
```

Додати в `tests/test_workspaces_ui.py`:

```python
def test_canvas_uses_high_fps_only_while_running() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Тест")
    node = FlowNode.create("executor")
    workflow.nodes = [node]
    scene.set_workflow(workflow)
    assert not scene._running_timer.isActive()
    scene.set_node_status(node.id, "running")
    assert scene._running_timer.isActive()
    assert scene._running_timer.interval() <= 17
    scene.set_node_status(node.id, "success")
    assert not scene._running_timer.isActive()
```

- [ ] **Крок 2: Запустити тести і переконатися, що вони падають**

Виконати: `.venv\Scripts\python -m pytest tests/test_theme_ui.py tests/test_workspaces_ui.py -k "pulse or high_fps" -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.motion'`.

- [ ] **Крок 3: Створити `flowai/ui/motion.py`**

```python
from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import QDialog, QGraphicsOpacityEffect, QWidget

from .design import DURATION

PULSE_PERIOD = 1.6
RISE_PIXELS = 8


def pulse(moment: float | None = None) -> float:
    """Плавна хвиля 0…1 з періодом 1,6 с — спільна для нод і живого рядка."""
    value = time.monotonic() if moment is None else moment
    return (math.sin(value * math.tau / PULSE_PERIOD) + 1.0) / 2.0


def fade_in(widget: QWidget, duration: int | None = None) -> QParallelAnimationGroup:
    """Поява вікна: прозорість 0→1 і підйом на 8 px."""
    span = duration or DURATION["base"]
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)

    opacity = QPropertyAnimation(effect, b"opacity", widget)
    opacity.setDuration(span)
    opacity.setStartValue(0.0)
    opacity.setEndValue(1.0)
    opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

    end = widget.pos()
    start = QPoint(end.x(), end.y() + RISE_PIXELS)
    move = QPropertyAnimation(widget, b"pos", widget)
    move.setDuration(span)
    move.setStartValue(start)
    move.setEndValue(end)
    move.setEasingCurve(QEasingCurve.Type.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(opacity)
    group.addAnimation(move)
    group.finished.connect(lambda: widget.setGraphicsEffect(None))
    group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
    return group


class AnimatedDialog(QDialog):
    """Діалог, який з'являється плавно, а не вистрибує."""

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        if not getattr(self, "_appeared", False):
            self._appeared = True
            fade_in(self)
```

- [ ] **Крок 4: Замінити блимання на пульсацію**

У `flowai/ui/canvas.py`:
- видалити `self._blink_timer` разом із методом `_toggle_blink` і всіма його викликами;
- інтервал `self._running_timer` змінити на `16` (60 fps);
- у `_sync_running_timer` умову запуску розширити так, щоб таймер працював, коли є хоч одна нода зі статусом `running`, нода з активним завданням або нода в стані `attention`;
- у `NodeItem._running_pulse` викинути власну реалізацію і викликати `from .motion import pulse` — щоб пульс ноди, живого рядка й ребра був синхронним;
- місця, які раніше читали `_blink`, перевести на `pulse()`: замість двох станів кольору використати `_blend_color(base, highlight, pulse())`.

- [ ] **Крок 5: Перевести діалоги на `AnimatedDialog`**

Змінити базовий клас на `AnimatedDialog` у: `GeneratedFilesDialog`, `ResultLimitDialog`, `ResultConfirmationDialog`, `WorkflowSettingsDialog` (`main_window.py`), `StatsDialog`, `ResultsDialog`, `FullScreenTextEditorDialog` (`inspector.py`), `ImagePreviewDialog` (`attachments.py`), `ChatGPTLoginDialog` (`login_dialog.py`).

- [ ] **Крок 6: Прогнати тести**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.

- [ ] **Крок 7: Ручна перевірка навантаження**

Запустити програму на порожньому Flow, відкрити Диспетчер задач: споживання CPU процесом має бути близьким до нуля в спокої (таймер 60 fps не має працювати без активних нод). Запустити Flow — під час роботи ноди пульсація має бути плавною, без сходинок.

- [ ] **Крок 8: Коміт**

```bash
git add flowai/ui/motion.py flowai/ui/canvas.py flowai/ui/main_window.py flowai/ui/inspector.py flowai/ui/attachments.py flowai/ui/login_dialog.py flowai/ui/stats_dialog.py flowai/ui/results_dialog.py tests/
git commit -m "feat: плавна пульсація замість блимання і анімована поява вікон"
```

---

### Задача 16: Канвас під нову палітру і біжучий пунктир по активному ребру

**Файли:**
- Змінити: `flowai/ui/canvas.py:403-501` (`NodeItem.paint`), `862-940` (`EdgeItem`)
- Тест: `tests/test_workspaces_ui.py`

**Інтерфейси:**
- Виробляє: `EdgeItem.set_active(active: bool) -> None` — вмикає анімований пунктир; `FlowScene.set_active_edge(edge_id: str) -> None`.
- Споживає: `pulse` із `motion.py`, токени з `design.py`.

- [ ] **Крок 1: Написати падаючий тест**

```python
def test_active_edge_animates_dashes() -> None:
    application()
    scene = FlowScene()
    workflow = Workflow(name="Тест")
    first = FlowNode.create("executor")
    second = FlowNode.create("task_reviewer")
    edge = FlowEdge.create(first.id, second.id)
    workflow.nodes = [first, second]
    workflow.edges = [edge]
    scene.set_workflow(workflow)
    item = scene.edge_items[edge.id]
    assert item.is_active is False
    scene.set_active_edge(edge.id)
    assert item.is_active is True
    assert scene._running_timer.isActive()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_workspaces_ui.py::test_active_edge_animates_dashes -q`
Очікується: FAIL — `AttributeError: 'EdgeItem' object has no attribute 'is_active'`.

- [ ] **Крок 3: Анімований пунктир на ребрі**

У `flowai/ui/canvas.py` у `EdgeItem.__init__` додати `self.is_active = False`, метод:

```python
    def set_active(self, active: bool) -> None:
        if self.is_active != active:
            self.is_active = active
            self.update()
```

У `EdgeItem.paint` перед відмальовкою лінії:

```python
        if self.is_active:
            pen = QPen(self._color().lighter(130), 2.6)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([6, 5])
            # Зсув візерунка робить пунктир «біжучим» уздовж маршруту.
            pen.setDashOffset((time.monotonic() * 14) % 11)
            painter.setPen(pen)
```

У `FlowScene` додати:

```python
    def set_active_edge(self, edge_id: str) -> None:
        for identifier, item in self.edge_items.items():
            item.set_active(identifier == edge_id)
        self._sync_running_timer()
```

і врахувати активні ребра в умові `_sync_running_timer`, щоб таймер 60 fps не вимикався, поки біжить пунктир. У `_update_running_nodes` додати виклик `item.update()` для активних ребер.

- [ ] **Крок 4: Підсвітити маршрут під час виконання**

У `flowai/ui/main_window.py` у `_handle_run_event` при `node_started` знайти ребро, яким прийшли дані, і зробити його активним: серед `workflow.incoming(node_id)` взяти те, чиє джерело завершилось останнім (за `session.node_statuses`), і викликати `self.scene.set_active_edge(edge.id)`. При `run_finished`, `run_failed`, `run_cancelled` викликати `self.scene.set_active_edge("")`.

- [ ] **Крок 5: Ноди під нову палітру**

У `NodeItem.paint` замінити захардкожені кольори тла й межі на токени: тло — `COLORS["surface_raised"]`, межа — `COLORS["border"]`, у стані `running` — `_blend_color(COLORS["border"], NODE_COLORS[kind], pulse())`, радіус скруглення підняти з поточного до `RADII["md"]`, під ноду додати м'яку тінь через `QGraphicsDropShadowEffect` із `SHADOW_BLUR` і `SHADOW_ALPHA`. Кольори самих типів нод (`NODE_COLORS`) не чіпати — вони лишаються впізнаваними.

- [ ] **Крок 6: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 7: Документація**

У `README.md` додати короткий розділ:

```markdown
## Вигляд

FlowAI використовує єдину темну дизайн-систему: вбудовані шрифти Inter та
JetBrains Mono, темну системну шапку вікон, три рівні кнопок і плавні
переходи. Під час виконання активна нода м'яко пульсує, а маршрут, яким
зараз ідуть дані, підсвічується біжучим пунктиром.
```

- [ ] **Крок 8: Коміт**

```bash
git add flowai/ui/canvas.py flowai/ui/main_window.py README.md tests/
git commit -m "feat: канвас під нову палітру і біжучий пунктир активного маршруту"
```

---

# ФАЗА 4 — Правка 2: живі дії агента та інтерактивні файли в чаті

### Задача 17: Стрим ходу агента замість очікування результату

**Файли:**
- Змінити: `flowai/codex_adapter.py` (`run_agent`)
- Змінити: `flowai/engine.py` (`_execute_agent` — прокинути колбек)
- Тест: `tests/test_core.py`

**Інтерфейси:**
- Виробляє: `CodexAdapter.run_agent(..., on_activity: Callable[[dict[str, Any]], None] | None = None)`. Колбек отримує словники виду `{"kind": str, "summary": str, "paths": list[str], "phase": "started"|"completed"}`. Рушій перетворює їх на подію `agent_activity` із полями `node_id`, `node_title`, `kind`, `message`, `paths`.
- Споживає: `TurnHandle.stream()` із SDK; `agent_run_from_turn` і `usage_from_turn` із задач 1 і 4.

**Чому це можливо.** `TurnHandle.run()` у SDK — це просто `_collect_turn_result(self.stream(), ...)`. Ми робимо той самий збір, але дорогою віддаємо кожен `ItemCompletedNotification` назовні. Нічого приватного не використовуємо: `stream()` — публічний метод.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_stream_activity_reaches_engine(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    events: list[dict[str, Any]] = []
    runner = WorkflowRunner(
        pipeline.workflow, on_event=lambda event: events.append(event)
    )
    runner.run()
    activity = [item for item in events if item["type"] == "agent_activity"]
    assert activity, "Рушій має емітити хід агента"
    assert activity[0]["node_id"]
    assert activity[0]["message"]
```

Щоб тест не залежав від мережі, у `codex_adapter._fake_run` додати виклик колбека:

```python
        if on_activity is not None:
            on_activity(
                {
                    "kind": "fake",
                    "summary": "Тестовий крок",
                    "paths": [],
                    "phase": "completed",
                }
            )
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_stream_activity_reaches_engine -q`
Очікується: FAIL — `TypeError: _fake_run() got an unexpected keyword argument 'on_activity'`.

- [ ] **Крок 3: Перевести `run_agent` на стрим**

У `flowai/codex_adapter.py` додати розбір айтемів на шляхи:

```python
def paths_from_item(data: dict[str, Any]) -> list[str]:
    """Витягти шляхи файлів, яких торкнувся агент, із одного айтема."""
    found: list[str] = []
    changes = data.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            for key in ("path", "file_path", "filePath"):
                value = change.get(key)
                if isinstance(value, str) and value.strip():
                    found.append(value.strip())
    for key in ("path", "file_path", "filePath"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return list(dict.fromkeys(found))
```

У сигнатуру `run_agent` додати параметр `on_activity: Callable[[dict[str, Any]], None] | None = None` і замінити блок запуску ходу:

```python
        start_turn = getattr(thread, "turn", None)
        if callable(start_turn):
            turn = start_turn(run_input)
            with self._active_turn_lock:
                self._active_turn = turn
            try:
                result = self._consume_turn(turn, on_activity)
            finally:
                with self._active_turn_lock:
                    if self._active_turn is turn:
                        self._active_turn = None
        else:
            result = thread.run(run_input)
        return agent_run_from_turn(result, str(getattr(thread, "id", "") or ""))
```

і додати метод, який повторює збір SDK, віддаючи кроки назовні:

```python
    def _consume_turn(
        self, turn: Any, on_activity: Callable[[dict[str, Any]], None] | None
    ) -> Any:
        """Прочитати потік ходу, віддаючи кожен крок назовні наживо."""
        from openai_codex._run import TurnResult  # локально: SDK може бути відсутнім

        stream = turn.stream()
        items: list[Any] = []
        usage = None
        completed = None
        try:
            for event in stream:
                payload = event.payload
                kind = type(payload).__name__
                if kind == "ItemStartedNotification" and on_activity is not None:
                    normalized = normalize_items([payload.item])
                    if normalized:
                        entry = normalized[0]
                        on_activity(
                            {
                                "kind": entry["kind"],
                                "summary": entry["summary"],
                                "paths": paths_from_item(entry["detail"]),
                                "phase": "started",
                            }
                        )
                elif kind == "ItemCompletedNotification":
                    items.append(payload.item)
                    normalized = normalize_items([payload.item])
                    if normalized and on_activity is not None:
                        entry = normalized[0]
                        on_activity(
                            {
                                "kind": entry["kind"],
                                "summary": entry["summary"],
                                "paths": paths_from_item(entry["detail"]),
                                "phase": "completed",
                            }
                        )
                elif kind == "ThreadTokenUsageUpdatedNotification":
                    usage = payload.token_usage
                elif kind == "TurnCompletedNotification":
                    completed = payload
        finally:
            stream.close()
        if completed is None:
            raise CodexUnavailable("Хід завершився без події turn/completed")
        turn_data = completed.turn
        return TurnResult(
            id=turn_data.id,
            status=turn_data.status,
            error=turn_data.error,
            started_at=turn_data.started_at,
            completed_at=turn_data.completed_at,
            duration_ms=turn_data.duration_ms,
            final_response=_final_response(items),
            items=items,
            usage=usage,
        )
```

де `_final_response` — локальна копія логіки SDK: останній `AgentMessageThreadItem` із фазою `final_answer`, інакше останній без фази.

**Важливо:** статус `failed` тепер не піднімає винятку сам (це робив `_raise_for_failed_turn` усередині `run()`), тож у `agent_run_from_turn` додати перевірку й для нього:

```python
    if status_value == "failed":
        error = getattr(result, "error", None)
        message = str(getattr(error, "message", "") or "Хід агента завершився помилкою")
        raise CodexUnavailable(message)
```

- [ ] **Крок 4: Прокинути колбек у рушій**

У `flowai/engine.py` у `_execute_agent` перед викликом `codex.run_agent(...)` створити колбек і передати його:

```python
        def report(activity: dict[str, Any]) -> None:
            summary = str(activity.get("summary", "")).strip()
            if not summary:
                return
            self._emit(
                "agent_activity",
                node=node,
                message=summary,
                kind=str(activity.get("kind", "")),
                phase=str(activity.get("phase", "")),
                paths=[str(item) for item in activity.get("paths", [])],
            )

        run = codex.run_agent(..., on_activity=report)
```

- [ ] **Крок 5: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py -q`
Очікується: PASS.

- [ ] **Крок 6: Ручна перевірка на живому Codex**

Запустити реальний Flow і переконатися, що в журналі з'являються рядки дій **під час** роботи ноди, а не після її завершення.

- [ ] **Крок 7: Коміт**

```bash
git add flowai/codex_adapter.py flowai/engine.py tests/test_core.py
git commit -m "feat: живий стрим кроків агента з Codex SDK"
```

---

### Задача 18: Інтерактивні шляхи — відкриття, Провідник, копіювання

**Файли:**
- Створити: `flowai/ui/paths.py`
- Змінити: `flowai/ui/main_window.py` (`GeneratedFilesDialog` — контекстне меню), `flowai/ui/results_dialog.py`
- Тест: `tests/test_paths_ui.py`

**Інтерфейси:**
- Виробляє:
  - `open_file(path: str) -> bool` — відкрити файл програмою за замовчуванням.
  - `reveal_in_explorer(path: str) -> bool` — відкрити Провідник із **виділеним** файлом.
  - `copy_path(path: str) -> None` — покласти шлях у буфер обміну.
  - `copy_image(path: str) -> bool` — покласти саму картинку у буфер обміну.
  - `path_menu(path: str, parent: QWidget | None = None) -> QMenu` — готове контекстне меню у стилі Windows.
- Споживає: іконки `folder`, `external-link` із задачі 14.

**Тонкість Windows:** `explorer /select,"шлях"` повертає код виходу 1 навіть за успіху, тож на результат `subprocess` покладатися не можна. Прапорець `CREATE_NO_WINDOW` обов'язковий, інакше блимає консоль.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_paths_ui.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from flowai.ui.paths import copy_path, path_menu


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_copy_path_puts_text_in_clipboard(tmp_path: Path) -> None:
    application()
    target = tmp_path / "file.txt"
    target.write_text("дані", encoding="utf-8")
    copy_path(str(target))
    assert QGuiApplication.clipboard().text() == str(target)


def test_path_menu_has_expected_actions(tmp_path: Path) -> None:
    application()
    image = tmp_path / "picture.png"
    image.write_bytes(b"")
    menu = path_menu(str(image))
    titles = [action.text() for action in menu.actions() if action.text()]
    assert "Відкрити" in titles
    assert "Показати в Провіднику" in titles
    assert "Копіювати шлях" in titles
    assert "Копіювати картинку" in titles
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_paths_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.paths'`.

- [ ] **Крок 3: Створити `flowai/ui/paths.py`**

```python
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QImage
from PySide6.QtWidgets import QMenu, QWidget

from .icons import icon

LOGGER = logging.getLogger(__name__)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})


def is_image(path: str) -> bool:
    return Path(path).suffix.casefold() in IMAGE_SUFFIXES


def open_file(path: str) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def reveal_in_explorer(path: str) -> bool:
    """Відкрити папку файлу з виділеним файлом."""
    target = Path(path)
    if not target.exists():
        return False
    if sys.platform == "win32":
        try:
            # explorer повертає 1 навіть за успіху — код виходу не перевіряємо.
            subprocess.Popen(
                ["explorer", f"/select,{target}"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            LOGGER.warning("Не вдалося відкрити Провідник для %s", target)
            return False
        return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))


def copy_path(path: str) -> None:
    QGuiApplication.clipboard().setText(str(path))


def copy_image(path: str) -> bool:
    image = QImage(str(path))
    if image.isNull():
        return False
    QGuiApplication.clipboard().setImage(image)
    return True


def path_menu(path: str, parent: QWidget | None = None) -> QMenu:
    """Контекстне меню для будь-якого шляху у стилі Windows."""
    menu = QMenu(parent)
    exists = Path(path).exists()

    open_action = QAction(icon("external-link"), "Відкрити", menu)
    open_action.setEnabled(exists)
    open_action.triggered.connect(lambda: open_file(path))
    menu.addAction(open_action)

    reveal_action = QAction(icon("folder"), "Показати в Провіднику", menu)
    reveal_action.setEnabled(exists)
    reveal_action.triggered.connect(lambda: reveal_in_explorer(path))
    menu.addAction(reveal_action)

    menu.addSeparator()

    copy_action = QAction("Копіювати шлях", menu)
    copy_action.triggered.connect(lambda: copy_path(path))
    menu.addAction(copy_action)

    if is_image(path):
        image_action = QAction("Копіювати картинку", menu)
        image_action.setEnabled(exists)
        image_action.triggered.connect(lambda: copy_image(path))
        menu.addAction(image_action)
    return menu
```

- [ ] **Крок 4: Підключити меню у вікнах Files і Результати**

У `GeneratedFilesDialog.__init__` і `ResultsDialog.__init__` додати:

```python
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
```

і метод в обох класах:

```python
    def _context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        raw_path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not raw_path:
            return
        path_menu(raw_path, self).exec(self.tree.viewport().mapToGlobal(position))
```

(у `ResultsDialog` дерево називається `self.files` — підставити відповідне ім'я).

- [ ] **Крок 5: Прогнати тести**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.

- [ ] **Крок 6: Коміт**

```bash
git add flowai/ui/paths.py flowai/ui/main_window.py flowai/ui/results_dialog.py tests/test_paths_ui.py
git commit -m "feat: контекстне меню для шляхів з показом у Провіднику"
```

---

### Задача 19: Панель журналу — чат із живим рядком, посиланнями й картинками

**Файли:**
- Створити: `flowai/ui/log_panel.py`
- Змінити: `flowai/ui/main_window.py:855-865` (створення `log_view`), `1076-1099` (`_build_log`), `2559-2641` (рендер журналу)
- Тест: `tests/test_log_panel_ui.py`

**Інтерфейси:**
- Виробляє: `LogPanel(QWidget)` із методами:
  - `append_entry(entry: dict[str, Any]) -> None` — `entry` має ключі `timestamp`, `text`, `color`, `file_paths`, необов'язково `image_paths`.
  - `render_entries(entries: list[dict[str, Any]]) -> None`
  - `clear() -> None`
  - `set_activity(text: str, color: str) -> None` — оновити живий рядок; порожній текст ховає його.
  - публічний атрибут `view: QTextBrowser`.
- Споживає: `path_menu`, `open_file` із задачі 18; `pulse`, `DURATION` із задач 15 і 13.

**Ключові рішення реалізації:**
- Шляхи вставляються як анкори `<a href="flowai-file:///абсолютний/шлях">`; `setOpenLinks(False)` + `anchorClicked` дає ЛКМ-відкриття, а `anchorAt(pos)` у `contextMenuEvent` — ПКМ-меню.
- Картинки вставляються через `document().addResource(QTextDocument.ImageResource, ...)` із масштабуванням до висоти 120 px, і кожна обгортається в той самий анкор — щоб ПКМ на картинці давав те саме меню.
- Живий рядок — окремий `QLabel` під журналом із `QGraphicsOpacityEffect`, прозорість якого веде `QPropertyAnimation` у циклі 0.55↔1.0 за `DURATION["slow"]`, `QEasingCurve.InOutSine`. Це і є «легенько мигати та плавно», і воно не змушує перемальовувати документ.
- Оновлення живого рядка **обмежене 10 разами на секунду**: подія `agent_activity` кладеться в поле, а `QTimer` на 100 мс переносить його в підпис. Без цього швидкий стрим дельт заб'є цикл подій UI.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_log_panel_ui.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.log_panel import LogPanel


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_paths_become_clickable_anchors(tmp_path: Path) -> None:
    application()
    target = tmp_path / "звіт.md"
    target.write_text("дані", encoding="utf-8")
    panel = LogPanel()
    panel.append_entry(
        {
            "timestamp": "12:00:00",
            "text": f"Готово: {target}",
            "color": "#7C3AED",
            "file_paths": [str(target)],
        }
    )
    html = panel.view.toHtml()
    assert "flowai-file:" in html
    panel.deleteLater()


def test_activity_line_shows_and_hides() -> None:
    application()
    panel = LogPanel()
    assert panel.activity_label.isVisible() is False
    panel.set_activity("Виконує: python gen.py", "#7C3AED")
    panel.flush_activity()
    assert panel.activity_label.text().endswith("python gen.py")
    panel.set_activity("", "")
    panel.flush_activity()
    assert panel.activity_label.isVisible() is False
    panel.deleteLater()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_log_panel_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.log_panel'`.

- [ ] **Крок 3: Створити `flowai/ui/log_panel.py`**

```python
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    QUrl,
    Qt,
)
from PySide6.QtGui import QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .design import COLORS, DURATION, SPACE
from .paths import IMAGE_SUFFIXES, open_file, path_menu

FILE_SCHEME = "flowai-file"
THUMBNAIL_HEIGHT = 120
ACTIVITY_INTERVAL_MS = 100


class LogView(QTextBrowser):
    """Журнал, у якому будь-який шлях — це посилання з контекстним меню."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("logView")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.anchorClicked.connect(self._anchor_clicked)

    @staticmethod
    def _path_from(url: QUrl) -> str:
        if url.scheme() != FILE_SCHEME:
            return ""
        return url.path().lstrip("/")

    def _anchor_clicked(self, url: QUrl) -> None:
        path = self._path_from(url)
        if path:
            open_file(path)

    def contextMenuEvent(self, event: Any) -> None:
        anchor = self.anchorAt(event.pos())
        if anchor:
            path = self._path_from(QUrl(anchor))
            if path:
                path_menu(path, self).exec(event.globalPos())
                return
        super().contextMenuEvent(event)


class LogPanel(QWidget):
    """Журнал виконання плюс закріплений рядок «що робиться прямо зараз»."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        self.view = LogView(self)
        layout.addWidget(self.view, 1)

        self.activity_label = QLabel("", self)
        self.activity_label.setObjectName("activityLine")
        self.activity_label.setWordWrap(False)
        self.activity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.activity_label.hide()
        layout.addWidget(self.activity_label)

        self._effect = QGraphicsOpacityEffect(self.activity_label)
        self.activity_label.setGraphicsEffect(self._effect)
        self._breath = QPropertyAnimation(self._effect, b"opacity", self)
        self._breath.setDuration(DURATION["slow"] * 4)
        self._breath.setStartValue(0.55)
        self._breath.setKeyValueAt(0.5, 1.0)
        self._breath.setEndValue(0.55)
        self._breath.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._breath.setLoopCount(-1)

        self._pending_activity: tuple[str, str] | None = None
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(ACTIVITY_INTERVAL_MS)
        self._activity_timer.timeout.connect(self.flush_activity)
        self._activity_timer.start()

    # ------------------------------------------------------------------
    # Живий рядок
    # ------------------------------------------------------------------

    def set_activity(self, text: str, color: str) -> None:
        """Запам'ятати дію; на екран вона потрапить не частіше 10 разів на секунду."""
        self._pending_activity = (text, color)

    def flush_activity(self) -> None:
        if self._pending_activity is None:
            return
        text, color = self._pending_activity
        self._pending_activity = None
        if not text:
            self._breath.stop()
            self._effect.setOpacity(1.0)
            self.activity_label.hide()
            return
        self.activity_label.setText(f"⟳  {text}")
        self.activity_label.setStyleSheet(
            f"color: {color or COLORS['text_muted']};"
        )
        if not self.activity_label.isVisible():
            self.activity_label.show()
        if self._breath.state() != QPropertyAnimation.State.Running:
            self._breath.start()

    # ------------------------------------------------------------------
    # Журнал
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self.view.clear()

    def render_entries(self, entries: list[dict[str, Any]]) -> None:
        self.view.clear()
        for entry in entries:
            self.append_entry(entry)

    def append_entry(self, entry: dict[str, Any]) -> None:
        color = str(entry.get("color") or COLORS["text_muted"])
        timestamp = html.escape(str(entry.get("timestamp", "")))
        text = str(entry.get("text", ""))
        file_paths = [str(item) for item in entry.get("file_paths", []) if str(item)]
        body = self._linkify(html.escape(text), file_paths)
        block = (
            f'<div style="color:{color}; margin:2px 0;">'
            f'<span style="color:{COLORS["text_dim"]};">[{timestamp}]</span> '
            f"{body}</div>"
        )
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(block)
        for raw_path in file_paths:
            self._insert_thumbnail(raw_path)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _linkify(escaped_text: str, file_paths: list[str]) -> str:
        """Перетворити згадані шляхи на посилання, найдовші — першими."""
        for raw_path in sorted(file_paths, key=len, reverse=True):
            needle = html.escape(raw_path)
            if needle not in escaped_text:
                continue
            href = QUrl.fromLocalFile(raw_path).toString().replace("file://", "")
            anchor = (
                f'<a href="{FILE_SCHEME}://{href}" '
                f'style="color:{COLORS["focus"]}; text-decoration:underline;">'
                f"{html.escape(Path(raw_path).name)}</a>"
            )
            escaped_text = escaped_text.replace(needle, anchor)
        return escaped_text.replace("\n", "<br/>")

    def _insert_thumbnail(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path.suffix.casefold() not in IMAGE_SUFFIXES or not path.is_file():
            return
        image = QImage(str(path))
        if image.isNull():
            return
        if image.height() > THUMBNAIL_HEIGHT:
            image = image.scaledToHeight(
                THUMBNAIL_HEIGHT, Qt.TransformationMode.SmoothTransformation
            )
        url = QUrl(f"{FILE_SCHEME}://{raw_path}")
        self.view.document().addResource(
            QTextDocument.ResourceType.ImageResource, url, image
        )
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(
            f'<div style="margin:4px 0 8px 0;">'
            f'<a href="{FILE_SCHEME}://{raw_path}">'
            f'<img src="{FILE_SCHEME}://{raw_path}"/></a></div>'
        )
```

- [ ] **Крок 4: Стиль живого рядка**

У `build_style` (файл `flowai/ui/theme.py`) додати в кінець f-рядка блок — імена в дужках мають збігатися з локальними змінними функції (`c`, `RADII`, `SPACE`, `TYPE`, `mono_family`):

```python
QLabel#activityLine {{
    background: {c["surface_raised"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px;
    padding: {SPACE["sm"]}px {SPACE["md"]}px;
    font-family: "{mono_family}";
    font-size: {TYPE["caption"][0]}px;
}}
```

- [ ] **Крок 5: Замінити `log_view` на `LogPanel` у головному вікні**

У `flowai/ui/main_window.py`:
- рядки створення `self.log_view = QPlainTextEdit()` замінити на `self.log_panel = LogPanel()`;
- у `_build_log` додавати в лейаут `self.log_panel`;
- `_clear_current_log` → `self.log_panel.clear()`;
- `_render_session_log` → `self.log_panel.render_entries(session.log_entries)`;
- `_insert_log_entry` видалити, замінивши виклики на `self.log_panel.append_entry(entry)`;
- `_capture_current_workspace`, який робив `session.log_text = self.log_view.toPlainText()`, більше не потрібен — `log_text` уже накопичується в `_append_session_log`, тож цей рядок видалити.

- [ ] **Крок 6: Показувати живі дії**

У `_handle_run_event` додати гілку:

```python
        elif event_type == "agent_activity":
            paths = [str(item) for item in event.get("paths", []) if str(item)]
            if session.id == self.current_workspace_id:
                self.log_panel.set_activity(message, color)
            if str(event.get("phase")) == "completed":
                self._append_session_log(
                    session, f"{prefix}: {message}", color=color, file_paths=paths
                )
```

а в `node_finished`, `node_failed`, `node_cancelled` і `run_*` подіях гасити рядок:

```python
            if session.id == self.current_workspace_id:
                self.log_panel.set_activity("", "")
```

- [ ] **Крок 7: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS. Тести, які перевіряли `window.log_view`, перевести на `window.log_panel.view`.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 8: Коміт**

```bash
git add flowai/ui/log_panel.py flowai/ui/main_window.py flowai/ui/theme.py tests/
git commit -m "feat: чат із живим рядком дій, посиланнями на файли та картинками"
```

---

### Задача 20: Проміжні файли з файлової системи

**Файли:**
- Створити: `flowai/ui/file_watch.py`
- Змінити: `flowai/ui/main_window.py` (запуск і зупинка спостерігача, запис у групи файлів)
- Тест: `tests/test_file_watch_ui.py`

**Інтерфейси:**
- Виробляє: `RunFileWatcher(QObject)` із сигналом `file_ready = Signal(str)`, методами `start(roots: list[Path]) -> None`, `stop() -> None`. Ігнорує `.git`, `__pycache__`, `.venv`, `runs`, `node_modules`, `.ruff_cache`, `.pytest_cache` і файли, більші за 64 МБ.
- Споживає: нічого з попередніх задач.

**Чому потрібен окремий спостерігач.** `FileChange`-айтеми зі стриму показують лише те, що агент пропатчив сам. Якщо агент запустив скрипт, який згенерував PNG, у стрімі цього не буде. `fs/watch` у SDK не експонований — є лише модель нотифікації без методу, тож єдиний шлях — власний `QFileSystemWatcher`.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_file_watch_ui.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.file_watch import RunFileWatcher, is_interesting


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ignores_service_folders(tmp_path: Path) -> None:
    assert is_interesting(tmp_path / "картинка.png") is True
    assert is_interesting(tmp_path / ".git" / "index") is False
    assert is_interesting(tmp_path / "__pycache__" / "a.pyc") is False
    assert is_interesting(tmp_path / "runs" / "flowai-run.json") is False


def test_watcher_reports_new_file(tmp_path: Path) -> None:
    application()
    watcher = RunFileWatcher()
    seen: list[str] = []
    watcher.file_ready.connect(seen.append)
    watcher.start([tmp_path])
    (tmp_path / "новий.png").write_bytes(b"x")
    watcher.rescan()
    assert any("новий.png" in item for item in seen)
    watcher.stop()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_file_watch_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.file_watch'`.

- [ ] **Крок 3: Створити `flowai/ui/file_watch.py`**

```python
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

LOGGER = logging.getLogger(__name__)

IGNORED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "runs",
    }
)
MAX_SIZE_BYTES = 64 * 1024 * 1024
MAX_WATCHED_DIRECTORIES = 400
RESCAN_INTERVAL_MS = 900


def is_interesting(path: Path) -> bool:
    """Чи варто показувати цей файл користувачу як проміжний результат."""
    if any(part in IGNORED_PARTS for part in path.parts):
        return False
    if path.name.startswith("~") or path.suffix in {".tmp", ".part", ".lock"}:
        return False
    try:
        if path.is_file() and path.stat().st_size > MAX_SIZE_BYTES:
            return False
    except OSError:
        return False
    return True


class RunFileWatcher(QObject):
    """Стежить за робочими папками, доки виконується Flow."""

    file_ready = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _path: self._schedule())
        self._known: set[str] = set()
        self._roots: list[Path] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RESCAN_INTERVAL_MS)
        self._timer.timeout.connect(self.rescan)

    def start(self, roots: list[Path]) -> None:
        self.stop()
        self._roots = [Path(root) for root in roots if Path(root).is_dir()]
        directories: list[str] = []
        for root in self._roots:
            directories.append(str(root))
            for child in root.rglob("*"):
                if len(directories) >= MAX_WATCHED_DIRECTORIES:
                    break
                if child.is_dir() and is_interesting(child):
                    directories.append(str(child))
        if directories:
            self._watcher.addPaths(directories)
        self._known = {str(path) for path in self._collect()}

    def stop(self) -> None:
        self._timer.stop()
        for group in (self._watcher.files(), self._watcher.directories()):
            if group:
                self._watcher.removePaths(group)
        self._known.clear()
        self._roots = []

    def _schedule(self) -> None:
        self._timer.start()

    def _collect(self) -> list[Path]:
        found: list[Path] = []
        for root in self._roots:
            try:
                for path in root.rglob("*"):
                    if path.is_file() and is_interesting(path):
                        found.append(path)
            except OSError:
                LOGGER.debug("Не вдалося обійти %s", root, exc_info=True)
        return found

    def rescan(self) -> None:
        for path in self._collect():
            text = str(path)
            if text in self._known:
                continue
            self._known.add(text)
            self.file_ready.emit(text)
```

- [ ] **Крок 4: Підключити спостерігач до запуску**

У `flowai/ui/main_window.py`:
- у `run_workflow` після старту потоку створити спостерігач для сесії:

```python
        watcher = RunFileWatcher(self)
        watcher.file_ready.connect(
            lambda path, session_id=session.id: self._intermediate_file(session_id, path)
        )
        workflow = session.workflow
        roots = [workflow.resolved_workspace(session.project_path)]
        roots.extend(workflow.resolved_additional_folders(session.project_path))
        watcher.start(roots)
        session.file_watcher = watcher
```

(додати поле `file_watcher: Any = None` у `WorkspaceSession`);

- у `_run_thread_finished` зупинити: `if session.file_watcher is not None: session.file_watcher.stop(); session.file_watcher = None`;
- додати метод:

```python
    def _intermediate_file(self, session_id: str, path: str) -> None:
        session = self._workspace(session_id)
        if session is None or not session.generated_file_groups:
            return
        group = session.generated_file_groups[-1]
        intermediate = group.setdefault("intermediate", [])
        if path in intermediate:
            return
        intermediate.append(path)
        self._append_session_log(
            session,
            f"Проміжний файл: {path}",
            color=str(group.get("color") or ""),
            file_paths=[path],
        )
```

- [ ] **Крок 5: Прогнати тести**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.

- [ ] **Крок 6: Документація**

У `README.md` у розділі про журнал додати:

```markdown
Під час роботи агента в журналі видно, що саме він робить прямо зараз —
рядок унизу панелі плавно пульсує й оновлюється в реальному часі. Проміжні
файли, зокрема згенеровані картинки, показуються прямо в журналі: ЛКМ
відкриває файл, ПКМ дає меню з переходом у Провідник із виділеним файлом,
копіюванням шляху й копіюванням самої картинки.
```

- [ ] **Крок 7: Коміт**

```bash
git add flowai/ui/file_watch.py flowai/ui/main_window.py flowai/workspaces.py README.md tests/test_file_watch_ui.py
git commit -m "feat: проміжні файли з робочих папок у чаті"
```

---

# ФАЗА 5 — Правки 8, 9, 4: MCP і AI-складання Flow

### Задача 21: MCP-сервер — каркас і довідник нод

**Файли:**
- Створити: `flowai/mcp/__init__.py`, `flowai/mcp/__main__.py`, `flowai/mcp/server.py`, `flowai/mcp/schema.py`
- Змінити: `pyproject.toml` (залежність `mcp`)
- Тест: `tests/test_mcp.py`

**Інтерфейси:**
- Виробляє:
  - `flowai/mcp/schema.py::node_kinds() -> list[dict[str, Any]]` — для кожного типу ноди: `kind`, `label`, `color`, `ports`, `is_agent`, `config_fields` (ім'я → тип і дефолт), `description`.
  - `flowai/mcp/schema.py::describe_kind(kind: str) -> dict[str, Any]`.
  - `flowai/mcp/server.py::build_server() -> FastMCP` — сервер з усіма інструментами.
  - Запуск: `python -m flowai.mcp` (stdio).
- Споживає: `flowai.models` — `NODE_LABELS`, `NODE_COLORS`, `AGENT_KINDS`, `_default_config`, `Workflow.ports_of`.

- [ ] **Крок 1: Додати залежність**

У `pyproject.toml` у `dependencies` додати `"mcp>=1.2,<2"`. Виконати `.venv\Scripts\python -m pip install -e .`.

- [ ] **Крок 2: Написати падаючий тест**

Створити `tests/test_mcp.py`:

```python
from __future__ import annotations

from flowai.mcp.schema import describe_kind, node_kinds


def test_node_kinds_cover_all_blocks() -> None:
    kinds = {item["kind"] for item in node_kinds()}
    assert kinds == {
        "entry",
        "tasks_manager",
        "prompt_reviewer",
        "executor",
        "task_reviewer",
        "result",
        "work_reviewer",
    }


def test_result_description_includes_exhausted_port() -> None:
    described = describe_kind("result")
    assert described["ports"] == ["true", "false", "exhausted"]
    assert "task_attempt_limit" in described["config_fields"]
```

- [ ] **Крок 3: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.mcp'`.

- [ ] **Крок 4: Створити `flowai/mcp/schema.py`**

```python
from __future__ import annotations

from typing import Any

from ..models import (
    AGENT_KINDS,
    NODE_COLORS,
    NODE_LABELS,
    FlowNode,
    Workflow,
)

DESCRIPTIONS: dict[str, str] = {
    "entry": "Вхідний промпт користувача та вкладення. Кореневий блок маршруту.",
    "tasks_manager": (
        "Черга послідовних завдань. Вихід NEXT віддає активне завдання, "
        "вихід DONE спрацьовує, коли завдань не лишилось. Обов'язково потребує "
        "повернення виходу TRUE блока Result назад у себе."
    ),
    "prompt_reviewer": "Агент, який уточнює промпт перед виконанням.",
    "executor": "Агент, який виконує задачу і створює файли.",
    "task_reviewer": (
        "Агент-контролер. Має повертати JSON із полем verdict — саме на нього "
        "спирається розгалуження блока Result."
    ),
    "result": (
        "Розгалуження. TRUE — робота прийнята, FALSE — на переробку, "
        "EXHAUSTED (жовтий) — активне завдання вичерпало власний ліміт спроб "
        "і має бути позначене провальним."
    ),
    "work_reviewer": "Аналітик протоколу роботи. Не має портів і не бере участі в маршруті.",
}


def _config_fields(kind: str) -> dict[str, Any]:
    node = FlowNode.create(kind)
    return {
        name: {"type": type(value).__name__, "default": value}
        for name, value in node.config.items()
    }


def describe_kind(kind: str) -> dict[str, Any]:
    if kind not in NODE_LABELS:
        raise ValueError(f"Невідомий тип ноди: {kind}")
    node = FlowNode.create(kind)
    workflow = Workflow(nodes=[node])
    return {
        "kind": kind,
        "label": NODE_LABELS[kind],
        "color": NODE_COLORS[kind],
        "is_agent": kind in AGENT_KINDS,
        "ports": list(workflow.ports_of(node.id)),
        "description": DESCRIPTIONS[kind],
        "config_fields": _config_fields(kind),
    }


def node_kinds() -> list[dict[str, Any]]:
    return [describe_kind(kind) for kind in NODE_LABELS]
```

- [ ] **Крок 5: Створити каркас сервера**

`flowai/mcp/__init__.py`:

```python
from .server import build_server

__all__ = ["build_server"]
```

`flowai/mcp/__main__.py`:

```python
from .server import build_server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
```

`flowai/mcp/server.py` (у цій задачі — лише довідкові інструменти; складання Flow додається наступною задачею):

```python
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .schema import describe_kind, node_kinds


def build_server() -> FastMCP:
    server = FastMCP("flowai")

    @server.tool()
    def list_node_kinds() -> list[dict[str, Any]]:
        """Усі типи блоків FlowAI з портами, полями конфігу та призначенням."""
        return node_kinds()

    @server.tool()
    def describe_node_kind(kind: str) -> dict[str, Any]:
        """Детальний опис одного типу блока."""
        return describe_kind(kind)

    return server
```

- [ ] **Крок 6: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py -q`
Очікується: PASS.

- [ ] **Крок 7: Перевірити запуск сервера**

Виконати: `.venv\Scripts\python -c "from flowai.mcp import build_server; print(build_server().name)"`
Очікується: `flowai`

- [ ] **Крок 8: Коміт**

```bash
git add flowai/mcp pyproject.toml tests/test_mcp.py
git commit -m "feat: каркас MCP-сервера FlowAI з довідником типів нод"
```

---

### Задача 22: MCP-інструменти складання і збереження Flow

**Файли:**
- Створити: `flowai/mcp/drafts.py`
- Змінити: `flowai/mcp/server.py`
- Тест: `tests/test_mcp.py`

**Інтерфейси:**
- Виробляє: `DraftStore` із методами `create(name: str) -> str`, `get(draft_id: str) -> Workflow`, `drop(draft_id: str) -> None`, `auto_layout(draft_id: str) -> None`. Інструменти сервера: `create_flow`, `add_node`, `set_node_config`, `set_tasks`, `connect_nodes`, `auto_layout`, `validate_flow`, `save_flow`, `list_flows`, `read_flow`.
- Споживає: `flowai.models.Workflow/FlowNode/FlowEdge`, `flowai.persistence.save_workflow/load_workflow`, `describe_kind` із задачі 21.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_mcp.py`:

```python
from pathlib import Path

from flowai.mcp.drafts import DraftStore
from flowai.persistence import load_workflow


def test_draft_store_builds_valid_flow(tmp_path: Path) -> None:
    store = DraftStore()
    draft_id = store.create("Тестовий Flow")
    manager = store.add_node(draft_id, "tasks_manager")
    executor = store.add_node(draft_id, "executor")
    reviewer = store.add_node(draft_id, "task_reviewer")
    result = store.add_node(draft_id, "result")
    store.set_tasks(draft_id, manager, ["Перше завдання", "Друге завдання"])
    store.connect(draft_id, manager, executor, "next")
    store.connect(draft_id, executor, reviewer)
    store.connect(draft_id, reviewer, result)
    store.connect(draft_id, result, manager, "true")
    store.connect(draft_id, result, manager, "exhausted")
    store.auto_layout(draft_id)

    assert store.validate(draft_id) == []
    target = tmp_path / "новий.flowai.json"
    store.save(draft_id, str(target))
    saved = load_workflow(target)
    assert len(saved.nodes) == 4
    assert saved.nodes[0].x != saved.nodes[1].x
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py::test_draft_store_builds_valid_flow -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.mcp.drafts'`.

- [ ] **Крок 3: Створити `flowai/mcp/drafts.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import DEFAULT_PORT, FlowEdge, FlowNode, Workflow, new_managed_task
from ..persistence import load_workflow, save_workflow

COLUMN_WIDTH = 320.0
ROW_HEIGHT = 210.0


class DraftStore:
    """Чернетки Flow, які агент збирає покроково перед збереженням."""

    def __init__(self) -> None:
        self._drafts: dict[str, Workflow] = {}

    def create(self, name: str, workspace: str = "") -> str:
        draft_id = uuid4().hex
        self._drafts[draft_id] = Workflow(name=name or "Новий Flow", workspace=workspace)
        return draft_id

    def get(self, draft_id: str) -> Workflow:
        if draft_id not in self._drafts:
            raise ValueError(f"Чернетка {draft_id} не знайдена")
        return self._drafts[draft_id]

    def drop(self, draft_id: str) -> None:
        self._drafts.pop(draft_id, None)

    def add_node(self, draft_id: str, kind: str, title: str = "") -> str:
        workflow = self.get(draft_id)
        node = FlowNode.create(kind)
        if title:
            node.title = title
        workflow.nodes.append(node)
        return node.id

    def set_config(self, draft_id: str, node_id: str, values: dict[str, Any]) -> None:
        workflow = self.get(draft_id)
        node = workflow.node(node_id)
        allowed = set(node.config)
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(
                f"У блока «{node.title}» немає полів: {', '.join(sorted(unknown))}"
            )
        node.config.update(values)

    def set_tasks(self, draft_id: str, node_id: str, prompts: list[str]) -> None:
        workflow = self.get(draft_id)
        node = workflow.node(node_id)
        if node.kind != "tasks_manager":
            raise ValueError("Завдання можна задати лише блоку Tasks Manager")
        node.config["tasks"] = [new_managed_task(prompt) for prompt in prompts]

    def connect(
        self,
        draft_id: str,
        source: str,
        target: str,
        source_port: str = DEFAULT_PORT,
        target_variable: str = "input",
    ) -> str:
        workflow = self.get(draft_id)
        edge = FlowEdge.create(source, target, source_port)
        edge.target_variable = target_variable
        workflow.edges.append(edge)
        return edge.id

    def auto_layout(self, draft_id: str) -> None:
        """Розкласти ноди колонками за топологічним порядком."""
        workflow = self.get(draft_id)
        try:
            order = workflow.topological_order()
        except ValueError:
            order = [node.id for node in workflow.nodes]
        depth: dict[str, int] = {}
        for node_id in order:
            incoming = [
                edge.source
                for edge in workflow.incoming(node_id)
                if (source := workflow.find(edge.source)) is not None
                and source.kind != "result"
            ]
            depth[node_id] = (
                0 if not incoming else max(depth.get(item, 0) for item in incoming) + 1
            )
        rows: dict[int, int] = {}
        for node in workflow.nodes:
            column = depth.get(node.id, 0)
            row = rows.get(column, 0)
            rows[column] = row + 1
            node.x = 80.0 + column * COLUMN_WIDTH
            node.y = 80.0 + row * ROW_HEIGHT

    def validate(self, draft_id: str) -> list[str]:
        return self.get(draft_id).validate()

    def save(self, draft_id: str, path: str) -> str:
        workflow = self.get(draft_id)
        errors = workflow.validate()
        if errors:
            raise ValueError("Flow не валідний:\n" + "\n".join(errors))
        target = Path(path).expanduser()
        if target.suffix != ".json":
            target = target.with_suffix(".flowai.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        save_workflow(workflow, target)
        return str(target)

    @staticmethod
    def read(path: str) -> dict[str, Any]:
        return load_workflow(Path(path)).to_dict()
```

- [ ] **Крок 4: Додати інструменти в сервер**

У `flowai/mcp/server.py` створити `store = DraftStore()` усередині `build_server` і зареєструвати інструменти — по одному на кожен метод сховища, плюс:

```python
    @server.tool()
    def list_flows(directory: str) -> list[dict[str, str]]:
        """Готові Flow у папці — як еталони стилю."""
        root = Path(directory).expanduser()
        found = []
        for path in sorted(root.glob("*.flowai.json")):
            try:
                workflow = load_workflow(path)
            except Exception:  # noqa: BLE001 - зіпсований файл не має валити список
                continue
            found.append(
                {
                    "path": str(path),
                    "name": workflow.name,
                    "nodes": str(len(workflow.nodes)),
                }
            )
        return found
```

Кожен інструмент має докстрінг українською — саме він потрапляє агенту як опис.

- [ ] **Крок 5: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py -q`
Очікується: PASS.

- [ ] **Крок 6: Коміт**

```bash
git add flowai/mcp tests/test_mcp.py
git commit -m "feat: MCP-інструменти складання, валідації та збереження Flow"
```

---

### Задача 23: Довідники `guides/` з автоіндексом

**Файли:**
- Створити: `flowai/mcp/guides.py`
- Створити: `guides/README.md` (пояснення, що це за папка)
- Змінити: `flowai/mcp/server.py`
- Тест: `tests/test_mcp.py`

**Інтерфейси:**
- Виробляє: `guides_root() -> Path`; `list_guides() -> list[dict[str, str]]` (`name`, `title`, `summary`, `path`); `read_guide(name: str, section: str = "") -> str`. `FLOWAI_NODE_GUIDE.md` із кореня проєкту завжди включається до списку під іменем `node-guide`.
- Споживає: нічого з попередніх задач.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_mcp.py`:

```python
from flowai.mcp.guides import list_guides, read_guide


def test_node_guide_is_always_available() -> None:
    names = {item["name"] for item in list_guides()}
    assert "node-guide" in names
    text = read_guide("node-guide")
    assert "Tasks Manager" in text


def test_read_guide_can_return_single_section() -> None:
    text = read_guide("node-guide", section="Result")
    assert text.strip().startswith("#")
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py -k guide -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.mcp.guides'`.

- [ ] **Крок 3: Створити `flowai/mcp/guides.py`**

```python
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


def list_guides() -> list[dict[str, str]]:
    """Усі доступні md-довідники: як працюють ноди й що радять використовувати."""
    entries: list[dict[str, str]] = []
    for name, path in _files().items():
        text = path.read_text(encoding="utf-8", errors="replace")
        title, summary = _title_and_summary(text, name)
        entries.append(
            {"name": name, "title": title, "summary": summary, "path": str(path)}
        )
    return entries


def read_guide(name: str, section: str = "") -> str:
    """Текст довідника цілком або лише один його розділ."""
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
    level = len(text[match.start() : match.end()].split()[0])
    tail = text[match.start() :]
    following = re.compile(rf"^#{{1,{level}}}\s", re.MULTILINE)
    next_match = following.search(tail, pos=len(match.group(0)))
    return tail[: next_match.start()] if next_match else tail
```

- [ ] **Крок 4: Створити `guides/README.md`**

```markdown
# Довідники для AI

Усе, що лежить у цій папці як `*.md`, автоматично стає доступним агенту, який
складає Flow, через MCP-інструменти `list_guides` і `read_guide`. Код при цьому
не змінюється — достатньо покласти файл сюди.

Формат: перший заголовок `#` стає назвою довідника, перший абзац — коротким
описом у списку. Розділи `##` можна читати окремо через `read_guide(name, section)`.

Приклади того, що варто сюди класти: рекомендації, коли який блок доречний;
шаблони формулювання завдань; типові помилки під час складання Flow.
```

- [ ] **Крок 5: Зареєструвати інструменти**

У `flowai/mcp/server.py` додати два інструменти, які делегують у `guides.list_guides` і `guides.read_guide` із докстрінгами українською.

- [ ] **Крок 6: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py -q`
Очікується: PASS.

- [ ] **Крок 7: Коміт**

```bash
git add flowai/mcp/guides.py flowai/mcp/server.py guides tests/test_mcp.py
git commit -m "feat: автоіндекс md-довідників у MCP"
```

---

### Задача 24: Підключення MCP до треда Codex (спайк + інтеграція)

**Файли:**
- Змінити: `flowai/codex_adapter.py` (`run_agent` — параметр `mcp_servers`)
- Створити: `flowai/mcp/client_config.py`
- Тест: `tests/test_mcp.py`

**Інтерфейси:**
- Виробляє: `flowai/mcp/client_config.py::flowai_server_config() -> dict[str, Any]` — блок конфігурації для Codex; `CodexAdapter.run_agent(..., mcp_servers: dict[str, Any] | None = None)`.
- Споживає: наявний механізм `config` у `thread_start`, який уже використовується для `model_reasoning_effort` і `sandbox_workspace_write`.

**Спайк — виконати першим кроком.** `Codex.thread_start(config=...)` приймає вільний словник, який app-server накладає поверх `config.toml`. Codex CLI підтримує секцію `mcp_servers.<ім'я>.command/args/env`, тож теоретично цього достатньо. Перевірити емпірично; якщо не спрацює — відкат описано нижче.

- [ ] **Крок 1: Спайк — перевірити, чи Codex підхоплює MCP через `config`**

Створити тимчасовий файл `scratch_mcp_spike.py` (у корені, у git не комітити):

```python
import openai_codex

with openai_codex.Codex() as codex:
    thread = codex.thread_start(
        model="gpt-5.6-terra",
        sandbox=openai_codex.Sandbox.read_only,
        config={
            "mcp_servers": {
                "flowai": {
                    "command": "python",
                    "args": ["-m", "flowai.mcp"],
                }
            }
        },
    )
    turn = thread.turn("Перелічи інструменти MCP-сервера flowai, які тобі доступні.")
    for event in turn.stream():
        print(type(event.payload).__name__)
    print("---")
```

Виконати: `.venv\Scripts\python scratch_mcp_spike.py`
Очікується: у потоці є `McpServerStatusUpdatedNotification`, а фінальна відповідь перелічує `list_node_kinds`, `create_flow` тощо.

**Якщо спайк провалився** — записати це в задачу і піти запасним шляхом: агент не викликає MCP, а отримує довідник нод і приклади прямо в промпті (`list_node_kinds()` серіалізується в JSON і вставляється в інструкції), пише `.flowai.json` звичайними файловими інструментами, а FlowAI після завершення сам викликає `Workflow.validate()` і показує помилки. Функціонально правка 9 працює в обох випадках; різниця лише в кількості помилок структури.

- [ ] **Крок 2: Написати тест конфігурації**

Додати в `tests/test_mcp.py`:

```python
import sys

from flowai.mcp.client_config import flowai_server_config


def test_server_config_points_at_current_interpreter() -> None:
    config = flowai_server_config()
    server = config["mcp_servers"]["flowai"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "flowai.mcp"]
```

- [ ] **Крок 3: Створити `flowai/mcp/client_config.py`**

```python
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def flowai_server_config() -> dict[str, Any]:
    """Конфіг для Codex, який реєструє MCP-сервер FlowAI на час треда."""
    return {
        "mcp_servers": {
            "flowai": {
                "command": sys.executable,
                "args": ["-m", "flowai.mcp"],
                "env": {"PYTHONPATH": str(PROJECT_ROOT)},
            }
        }
    }
```

- [ ] **Крок 4: Прокинути в адаптер**

У `run_agent` додати параметр `mcp_servers: dict[str, Any] | None = None` і в місці збирання `config`:

```python
            if mcp_servers:
                config.update(mcp_servers)
```

- [ ] **Крок 5: Прогнати тести й прибрати спайк**

Виконати: `.venv\Scripts\python -m pytest tests/test_mcp.py -q`
Очікується: PASS.
Видалити `scratch_mcp_spike.py`.

- [ ] **Крок 6: Документація**

У `README.md` додати розділ:

```markdown
## MCP-сервер

FlowAI має власний MCP-сервер: `python -m flowai.mcp`. Він віддає довідник типів
блоків, дозволяє покроково зібрати Flow, перевірити його тією самою валідацією,
що й програма, автоматично розкласти ноди й зберегти `.flowai.json`. Плюс подає
всі md-довідники з папки `guides/`.

Підключити його до зовнішнього агента (Claude Code, Codex CLI) можна як
звичайний stdio-сервер із командою `python -m flowai.mcp`.
```

- [ ] **Крок 7: Коміт**

```bash
git add flowai/mcp/client_config.py flowai/codex_adapter.py README.md tests/test_mcp.py
git commit -m "feat: реєстрація MCP-сервера FlowAI у треді Codex"
```

---

### Задача 25: Рушій GrillMe

**Файли:**
- Створити: `flowai/grill.py`
- Тест: `tests/test_grill.py`

**Інтерфейси:**
- Виробляє:
  - `GrillQuestion` — датаклас: `text: str`, `options: list[str]`, `rationale: str`.
  - `GrillOutcome` — датаклас: `summary: str`, `rewritten_tasks: dict[str, str]` (id завдання → новий промпт), `rewritten_entry: str`.
  - `GrillSession` — конструктор `GrillSession(workflow: Workflow, codex: CodexAdapter, model: str, workspace: Path)`; методи `next_question() -> GrillQuestion | None` (повертає `None`, коли агент сказав `done`), `answer(text: str) -> None`, `finish() -> GrillOutcome`, властивість `history: list[tuple[str, str]]`.
- Споживає: `CodexAdapter.run_agent`, `extract_json` із `flowai/templating.py`.

**Рішення grill-сесії:** ліміту питань немає — агент грилить, доки сам не поставить `done: true`. Кнопка «Досить — збирай промпт» у вікні викликає `finish()` достроково; це ручний вихід користувача, а не автоматична стеля.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_grill.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowai import codex_adapter
from flowai.codex_adapter import CodexAdapter
from flowai.grill import GrillSession
from flowai.models import FlowNode, Workflow


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWAI_FAKE_CODEX", "1")
    codex_adapter.FAKE_CALLS.clear()


def _workflow() -> tuple[Workflow, FlowNode]:
    workflow = Workflow(name="Тест")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [
        {"id": "t1", "prompt": "Зроби аналіз ринку", "attachments": []}
    ]
    workflow.nodes = [manager]
    return workflow, manager


def test_session_asks_then_finishes(tmp_path: Path) -> None:
    replies = [
        json.dumps(
            {
                "done": False,
                "question": "Який ринок аналізуємо?",
                "options": ["Мобільні ігри", "Веб"],
                "rationale": "Без ринку задача нечітка",
            }
        ),
        json.dumps({"done": True, "question": "", "options": [], "rationale": ""}),
        json.dumps(
            {
                "summary": "Ринок: мобільні ігри",
                "tasks": {"t1": "Зроби аналіз ринку мобільних ігор"},
                "entry": "",
            }
        ),
    ]
    codex_adapter.FAKE_RESPONDER = lambda call: replies.pop(0)

    workflow, manager = _workflow()
    with CodexAdapter() as codex:
        session = GrillSession(workflow, codex, "gpt-5.6-terra", tmp_path)
        question = session.next_question()
        assert question is not None
        assert question.options[-1] == "Своя відповідь"
        session.answer("Мобільні ігри")
        assert session.next_question() is None
        outcome = session.finish()
    assert outcome.rewritten_tasks["t1"].endswith("мобільних ігор")
    assert "мобільні ігри" in outcome.summary.lower()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_grill.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.grill'`.

- [ ] **Крок 3: Створити `flowai/grill.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codex_adapter import CodexAdapter
from .models import Workflow, normalize_managed_tasks
from .templating import extract_json

OWN_ANSWER = "Своя відповідь"

INSTRUCTIONS = (
    "Ти проводиш співбесіду з користувачем перед запуском ланцюга агентів. "
    "Твоя мета — вибити з нього все, чого бракує, щоб завдання стали "
    "однозначними й перевірюваними. Став РІВНО ОДНЕ питання за раз. "
    "Кожне питання супроводжуй 2-4 конкретними варіантами відповіді — "
    "не абстрактними, а такими, які справді змінюють результат. "
    "Не питай те, на що вже є відповідь у завданнях або в історії розмови. "
    "Коли інформації достатньо, поверни done=true."
)

QUESTION_SCHEMA = {
    "done": False,
    "question": "string",
    "options": ["string"],
    "rationale": "string",
}

SUMMARY_SCHEMA = {
    "summary": "string",
    "tasks": {"id завдання": "новий промпт"},
    "entry": "string",
}


@dataclass(slots=True)
class GrillQuestion:
    text: str
    options: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class GrillOutcome:
    summary: str = ""
    rewritten_tasks: dict[str, str] = field(default_factory=dict)
    rewritten_entry: str = ""


class GrillSession:
    """Сеанс уточнення промптів Flow перед запуском."""

    def __init__(
        self,
        workflow: Workflow,
        codex: CodexAdapter,
        model: str,
        workspace: Path,
    ) -> None:
        self.workflow = workflow
        self.codex = codex
        self.model = model
        self.workspace = workspace
        self.history: list[tuple[str, str]] = []
        self._thread_id = ""
        self._done = False

    # ------------------------------------------------------------------
    # Контекст
    # ------------------------------------------------------------------

    def _flow_context(self) -> str:
        lines: list[str] = [f"# Flow «{self.workflow.name}»"]
        for node in self.workflow.nodes:
            lines.append(f"\n## Блок {node.title} ({node.kind}, id {node.id})")
            if node.kind == "tasks_manager":
                for index, task in enumerate(
                    normalize_managed_tasks(node.config.get("tasks")), start=1
                ):
                    lines.append(f"\n### Завдання {index} (id {task['id']})")
                    lines.append(str(task.get("prompt", "")))
                continue
            if node.kind == "entry":
                lines.append(str(node.config.get("text", "")))
                continue
            instructions = str(node.config.get("instructions", "")).strip()
            if instructions:
                lines.append(f"Інструкції: {instructions}")
        return "\n".join(lines)

    def _history_text(self) -> str:
        if not self.history:
            return "Питань ще не було."
        return "\n".join(
            f"- Питання: {question}\n  Відповідь: {answer}"
            for question, answer in self.history
        )

    def _ask(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        run = self.codex.run_agent(
            prompt=prompt,
            developer_instructions=INSTRUCTIONS
            + "\n\nВідповідай лише JSON за схемою:\n"
            + json.dumps(schema, ensure_ascii=False, indent=2),
            model=self.model,
            sandbox="read-only",
            workspace=self.workspace,
            resume_thread_id=self._thread_id,
        )
        self._thread_id = run.thread_id or self._thread_id
        parsed = extract_json(run.text)
        if not isinstance(parsed, dict):
            raise ValueError("Агент GrillMe повернув не JSON")
        return parsed

    # ------------------------------------------------------------------
    # Цикл
    # ------------------------------------------------------------------

    def next_question(self) -> GrillQuestion | None:
        if self._done:
            return None
        prompt = (
            f"{self._flow_context()}\n\n"
            f"# Що вже з'ясовано\n{self._history_text()}\n\n"
            "Постав наступне питання або поверни done=true."
        )
        parsed = self._ask(prompt, QUESTION_SCHEMA)
        if bool(parsed.get("done")) or not str(parsed.get("question", "")).strip():
            self._done = True
            return None
        options = [
            str(item).strip()
            for item in parsed.get("options", [])
            if str(item).strip()
        ]
        options.append(OWN_ANSWER)
        return GrillQuestion(
            text=str(parsed["question"]).strip(),
            options=options,
            rationale=str(parsed.get("rationale", "")).strip(),
        )

    def answer(self, text: str) -> None:
        question = self.history[-1][0] if self.history else ""
        self.history.append((question, text))

    def record(self, question: str, answer: str) -> None:
        self.history.append((question, answer))

    def finish(self) -> GrillOutcome:
        prompt = (
            f"{self._flow_context()}\n\n"
            f"# Домовленості з користувачем\n{self._history_text()}\n\n"
            "Перепиши промпти тих завдань, яких стосуються домовленості. "
            "Не чіпай завдання, яких це не стосується — не включай їх у tasks. "
            "Збережи мову й структуру оригіналу, додай конкретику. "
            "У summary стисло перекажи ухвалені рішення."
        )
        parsed = self._ask(prompt, SUMMARY_SCHEMA)
        tasks = parsed.get("tasks")
        rewritten = (
            {str(key): str(value) for key, value in tasks.items()}
            if isinstance(tasks, dict)
            else {}
        )
        return GrillOutcome(
            summary=str(parsed.get("summary", "")).strip(),
            rewritten_tasks=rewritten,
            rewritten_entry=str(parsed.get("entry", "")).strip(),
        )
```

**Зауваження:** метод `answer` бере питання з історії, тож вікно має спочатку викликати `record(question_text, "")`… Щоб уникнути плутанини, у вікні використовуйте **лише** `record(question.text, answer_text)`, а `answer` не використовуйте — його лишено для сумісності з тестом і для сценарію, коли питання вже записане.

- [ ] **Крок 4: Запустити тест**

Виконати: `.venv\Scripts\python -m pytest tests/test_grill.py -q`
Очікується: PASS.

- [ ] **Крок 5: Коміт**

```bash
git add flowai/grill.py tests/test_grill.py
git commit -m "feat: рушій GrillMe для уточнення промптів перед запуском"
```

---

### Задача 26: Вікно GrillMe і фінальний дифф

**Файли:**
- Створити: `flowai/ui/grill_dialog.py`
- Тест: `tests/test_grill_ui.py`

**Інтерфейси:**
- Виробляє:
  - `GrillWorker(QObject)` — виконує `GrillSession` у окремому потоці; сигнали `question_ready(object)`, `outcome_ready(object)`, `failed(str)`; слоти `request_question()`, `submit_answer(str)`, `request_finish()`.
  - `GrillDialog(AnimatedDialog)` — конструктор `GrillDialog(workflow, model, workspace, parent=None)`; результат `outcome: GrillOutcome | None`, `decision: str` — `"run"`, `"edit"` або `""` (скасовано).
- Споживає: `GrillSession`, `AnimatedButton`, `AnimatedDialog`, токени дизайну.

**Компоновка вікна питань:** зверху — лічильник «Питання N» і, якщо є, `rationale` дрібним текстом; далі — сам текст питання великим шрифтом; далі — **вертикальний стовпчик** кнопок-варіантів на всю ширину; останній варіант «Своя відповідь» розкриває поле вводу з кнопкою «Відповісти». Унизу — «Досить — збирай промпт» і «Скасувати».

**Фінальне вікно:** заголовок «Все готово», текст `summary`, список змінених завдань із диффом «було → стало» (два `QPlainTextEdit` поруч, лише для читання), кнопки **«Запустити»** (variant primary) і **«Edit»**.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_grill_ui.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.grill import GrillOutcome, GrillQuestion
from flowai.models import FlowNode, Workflow
from flowai.ui.grill_dialog import GrillDialog


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _workflow() -> Workflow:
    workflow = Workflow(name="Тест")
    manager = FlowNode.create("tasks_manager")
    manager.config["tasks"] = [{"id": "t1", "prompt": "Стара задача", "attachments": []}]
    workflow.nodes = [manager]
    return workflow


def test_question_options_are_vertical_buttons(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(_workflow(), "gpt-5.6-terra", tmp_path)
    dialog.show_question(
        GrillQuestion(
            text="Який ринок?",
            options=["Мобільні ігри", "Веб", "Своя відповідь"],
            rationale="Потрібна конкретика",
        )
    )
    assert [button.text() for button in dialog.option_buttons] == [
        "Мобільні ігри",
        "Веб",
        "Своя відповідь",
    ]
    dialog.deleteLater()


def test_ready_page_shows_diff(tmp_path: Path) -> None:
    application()
    dialog = GrillDialog(_workflow(), "gpt-5.6-terra", tmp_path)
    dialog.show_outcome(
        GrillOutcome(
            summary="Ринок: мобільні ігри",
            rewritten_tasks={"t1": "Нова задача"},
        )
    )
    assert "Стара задача" in dialog.diff_text()
    assert "Нова задача" in dialog.diff_text()
    dialog.deleteLater()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_grill_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.grill_dialog'`.

- [ ] **Крок 3: Створити `flowai/ui/grill_dialog.py`**

Реалізувати за описом інтерфейсів вище. Обов'язкові публічні елементи, на які спираються тести й головне вікно: `option_buttons: list[AnimatedButton]`, `show_question(question: GrillQuestion) -> None`, `show_outcome(outcome: GrillOutcome) -> None`, `diff_text() -> str`, `outcome`, `decision`. Вікно тримає `QStackedWidget` із трьома сторінками: очікування («Агент формулює питання…» з тим самим пульсуючим рядком, що й у журналі), питання, готово.

Кожне натискання варіанта викликає `session.record(question.text, вибраний_текст)` і запитує наступне питання у воркері. Коли `next_question()` повертає `None` або користувач натиснув «Досить», воркер викликає `finish()` і вікно показує сторінку «Все готово».

- [ ] **Крок 4: Запустити тести**

Виконати: `.venv\Scripts\python -m pytest tests/test_grill_ui.py -q`
Очікується: PASS.

- [ ] **Крок 5: Коміт**

```bash
git add flowai/ui/grill_dialog.py tests/test_grill_ui.py
git commit -m "feat: вікно GrillMe з вертикальними варіантами та фінальним диффом"
```

---

### Задача 27: Кнопка Run → вибір «Запустити / GrillMe» і передача домовленостей далі

**Файли:**
- Створити: `flowai/ui/run_start_dialog.py`
- Змінити: `flowai/ui/main_window.py:1311-1398` (`run_workflow`)
- Змінити: `flowai/engine.py` (змінна шаблону `{{grill_summary}}`)
- Змінити: `flowai/models.py` (промпт Prompt Reviewer за замовчуванням)
- Тест: `tests/test_workspaces_ui.py`, `tests/test_core.py`

**Інтерфейси:**
- Виробляє: `RunStartDialog(AnimatedDialog)` із результатом `choice: str` — `"run"` або `"grill"`; галочка «Не питати більше» пише `QSettings("FlowAI", "FlowAI").setValue("run/skip_start_dialog", True)`. `Workflow.grill_summary: str` — поле, яке зберігається у файл Flow і потрапляє в контекст шаблонів як `{{grill_summary}}`.
- Споживає: `GrillDialog`, `GrillOutcome`.

- [ ] **Крок 1: Написати падаючий тест**

Додати в `tests/test_core.py`:

```python
def test_grill_summary_reaches_prompt_reviewer(tmp_path: Path) -> None:
    pipeline = Pipeline(tmp_path)
    pipeline.workflow.grill_summary = "Ринок: мобільні ігри"
    pipeline.improver.config["prompt"] = "{{entry_prompt}}\n{{grill_summary}}"
    runner = WorkflowRunner(pipeline.workflow)
    runner.run()
    improver_call = next(
        call for call in codex_adapter.FAKE_CALLS if call["model"] == "improver-model"
    )
    assert "Ринок: мобільні ігри" in improver_call["prompt"]
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_core.py::test_grill_summary_reaches_prompt_reviewer -q`
Очікується: FAIL — `AttributeError: 'Workflow' object has no attribute 'grill_summary'`.

- [ ] **Крок 3: Додати поле у модель**

У `flowai/models.py` у датакласі `Workflow` додати `grill_summary: str = ""`, включити його в `to_dict` і `from_dict`.

У промпт `prompt_reviewer` за замовчуванням додати блок:

```python
            prompt=(
                "# Промпт користувача\n{{entry_prompt}}\n\n"
                "# Домовленості з користувачем (GrillMe)\n{{grill_summary}}\n"
                "Це рішення користувача. Не переглядай і не викидай їх — "
                "лише зроби промпт чіткішим навколо них.\n\n"
                "# Ланцюг блоків, які працюватимуть далі\n{{flow_chain}}\n\n"
                "Поверни покращений промпт і перелік того, що ти змінив."
            ),
```

- [ ] **Крок 4: Прокинути змінну в контекст**

У `flowai/engine.py` у місці, де формується `context` для рендера шаблонів ноди, додати ключ:

```python
            "grill_summary": self.workflow.grill_summary
            or "Окремих домовленостей не було.",
```

- [ ] **Крок 5: Створити `flowai/ui/run_start_dialog.py`**

Вікно з двома великими кнопками: **«Запустити зараз»** (variant `success`, іконка `play`) і **«GrillMe — уточнити перед запуском»** (variant `primary`, іконка `sparkles`), під ними — галочка «Більше не питати, завжди запускати одразу» і пояснювальний текст про те, що GrillMe ставить питання й переписує промпти завдань.

- [ ] **Крок 6: Вплести у `run_workflow`**

На початку `run_workflow` (лише коли `resume is False`):

```python
        settings = QSettings("FlowAI", "FlowAI")
        if not bool(settings.value("run/skip_start_dialog", False, type=bool)):
            starter = RunStartDialog(self)
            if starter.exec() != QDialog.DialogCode.Accepted:
                return
            if starter.choice == "grill" and not self._run_grill(session):
                return
```

і метод, який реалізує цикл «Запустити / Edit»:

```python
    def _run_grill(self, session: WorkspaceSession) -> bool:
        """Провести сеанс GrillMe. True — можна запускати Flow."""
        workflow = session.workflow
        if workflow is None:
            return False
        while True:
            dialog = GrillDialog(
                workflow,
                self._grill_model(workflow),
                workflow.resolved_workspace(session.project_path),
                self,
            )
            dialog.exec()
            if dialog.decision == "run" and dialog.outcome is not None:
                self._apply_grill_outcome(session, dialog.outcome)
                return True
            if dialog.decision == "edit" and dialog.outcome is not None:
                self._apply_grill_outcome(session, dialog.outcome)
                self.inspector.focus_first_task()
                if (
                    QMessageBox.question(
                        self,
                        "Продовжити уточнення?",
                        "Промпти оновлено. Запустити GrillMe ще раз "
                        "із вашими правками?",
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return False
                continue
            return False
```

`_apply_grill_outcome` записує нові промпти завдань і `entry.text` у ноди, ставить `workflow.grill_summary`, викликає `self._mark_dirty()` і `self._commit_current_history()` — саме це робить зміну звичайним редагуванням із підтримкою `Ctrl+Z`.

`_grill_model` бере модель ноди Prompt Reviewer, якщо вона є, інакше `"gpt-5.6-terra"`.

- [ ] **Крок 7: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 8: Документація**

У `README.md` розділ «Швидкий старт», пункт про Run, доповнити:

```markdown
Після натискання **Run** FlowAI питає, запускати одразу чи спершу пройти
**GrillMe**. У режимі GrillMe агент ставить питання по одному, з готовими
варіантами відповіді та можливістю написати свою. Коли питання вичерпано,
він показує, які завдання й як саме перепише, — ви або запускаєте Flow, або
тиснете **Edit**, правите промпти вручну й проходите GrillMe ще раз уже з
доповненнями. Зміни лягають у Flow як звичайне редагування: працює `Ctrl+Z`,
а зберігаються вони лише коли ви натиснете «Зберегти».
```

- [ ] **Крок 9: Коміт**

```bash
git add flowai/ui/run_start_dialog.py flowai/ui/main_window.py flowai/engine.py flowai/models.py README.md tests/
git commit -m "feat: вибір GrillMe перед запуском і передача домовленостей у Flow"
```

---

### Задача 28: Новий Flow через AI

**Файли:**
- Створити: `flowai/ui/flow_composer_dialog.py`
- Змінити: `flowai/ui/main_window.py:1191-1203` (`new_workflow`)
- Змінити: `flowai/codex_auth.py` (список моделей)
- Тест: `tests/test_flow_composer_ui.py`

**Інтерфейси:**
- Виробляє:
  - `flowai/codex_auth.py::available_models() -> list[str]` — читає `Codex.models()`, з відкатом на `["gpt-5.6-terra", "gpt-5.6-terra-max", "gpt-5.6-flex"]`.
  - `FlowComposerDialog(AnimatedDialog)` — поля `prompt: QPlainTextEdit`, `model: QComboBox`, `grill: QCheckBox` (увімкнена за замовчуванням), `workspace: QLineEdit`, панель `log: LogPanel`; результат `saved_path: str`.
- Споживає: `flowai_server_config` (задача 24), `GrillDialog` (задача 26), `LogPanel` (задача 19), `DraftStore` через MCP.

**Сценарій:** «Новий» показує вибір із трьох варіантів — «Порожній», «Готова схема» (нинішній `starter_workflow`), «Скласти з AI». Останній відкриває це вікно. Користувач пише запит, обирає модель, лишає ввімкненою галочку GrillMe і тисне «Скласти». Якщо GrillMe ввімкнено — спершу проходить сеанс питань по самому запиту, і вже уточнений запит іде агенту. Агент працює з `sandbox="read-only"` на вказаній робочій папці, має MCP-сервер FlowAI і бачить: довідник нод, `guides/*.md`, попередні Flow через `list_flows`. Хід роботи видно в `LogPanel` того ж вигляду, що й основний журнал. Готовий файл відкривається як нове робоче середовище.

- [ ] **Крок 1: Написати падаючий тест**

Створити `tests/test_flow_composer_ui.py`:

```python
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from flowai.ui.flow_composer_dialog import FlowComposerDialog


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_composer_defaults_to_grill_enabled() -> None:
    application()
    dialog = FlowComposerDialog()
    assert dialog.grill.isChecked() is True
    assert dialog.model.count() > 0
    dialog.deleteLater()
```

- [ ] **Крок 2: Запустити тест і переконатися, що він падає**

Виконати: `.venv\Scripts\python -m pytest tests/test_flow_composer_ui.py -q`
Очікується: FAIL — `ModuleNotFoundError: No module named 'flowai.ui.flow_composer_dialog'`.

- [ ] **Крок 3: Додати список моделей**

У `flowai/codex_auth.py`:

```python
FALLBACK_MODELS = ("gpt-5.6-terra", "gpt-5.6-terra-max", "gpt-5.6-flex")


def available_models() -> list[str]:
    """Моделі, доступні акаунту; за недоступності — відомий мінімум."""
    try:
        import openai_codex

        with openai_codex.Codex() as codex:
            response = codex.models()
            names = [
                str(getattr(item, "id", "") or getattr(item, "name", ""))
                for item in getattr(response, "models", [])
            ]
            cleaned = [name for name in names if name]
            if cleaned:
                return cleaned
    except Exception:  # noqa: BLE001 - список моделей не критичний
        LOGGER.info("Не вдалося отримати список моделей", exc_info=True)
    return list(FALLBACK_MODELS)
```

- [ ] **Крок 4: Створити `flowai/ui/flow_composer_dialog.py`**

Реалізувати за описом інтерфейсів. Інструкції агенту-складачу (передаються як `developer_instructions`):

```python
COMPOSER_INSTRUCTIONS = (
    "Ти складаєш Flow для FlowAI через MCP-сервер «flowai». "
    "Порядок роботи обов'язковий: "
    "1) виклич list_guides і прочитай усі довідники — вони описують, як "
    "працюють блоки і які зв'язки коректні; "
    "2) виклич list_node_kinds, щоб знати точні поля конфігів; "
    "3) виклич list_flows на папці !_projects і прочитай один-два готові "
    "Flow як еталон стилю формулювань; "
    "4) прочитай робочу папку користувача, щоб завдання посилались на "
    "реальні шляхи й реальні файли; "
    "5) збери Flow інструментами create_flow / add_node / set_node_config / "
    "set_tasks / connect_nodes, виклич auto_layout, потім validate_flow і "
    "виправ усі помилки; "
    "6) лише після чистої валідації виклич save_flow. "
    "Завдання формулюй довгими й конкретними, як в еталонних Flow: що зробити, "
    "з якими файлами, який результат вважається прийнятним."
)
```

Виконання винести в `QThread`-воркер із колбеком `on_activity`, підключеним до `LogPanel.set_activity`, щоб вікно не блокувалось і було видно, що агент робить.

- [ ] **Крок 5: Вплести у `new_workflow`**

`new_workflow` спершу показує вибір із трьох варіантів; за вибору «Скласти з AI» відкриває `FlowComposerDialog`, і після успіху викликає наявний шлях відкриття файлу для `dialog.saved_path`, створюючи нове робоче середовище.

- [ ] **Крок 6: Прогнати тести й лінтер**

Виконати: `.venv\Scripts\python -m pytest tests/ -q`
Очікується: PASS.
Виконати: `.venv\Scripts\python -m ruff check flowai tests`
Очікується: `All checks passed!`

- [ ] **Крок 7: Ручна перевірка наскрізного сценарію**

Запустити програму → **Новий** → **Скласти з AI** → ввести запит на кшталт «Flow, який робить ревʼю моїх рівнів у D:/Work/PixelFlow і пише звіт» → лишити GrillMe ввімкненим → відповісти на питання → дочекатись складання → переконатися, що новий Flow відкрився, проходить валідацію і має осмислені завдання.

- [ ] **Крок 8: Документація**

У `README.md` додати:

```markdown
## Складання Flow за допомогою AI

**Файл → Новий → Скласти з AI** відкриває вікно, де ви описуєте, що потрібно,
обираєте модель GPT і за замовчуванням лишаєте ввімкненим **GrillMe**. Агент
спершу уточнює запит питаннями, потім через власний MCP-сервер FlowAI читає
довідники з `guides/`, дивиться на ваші готові проєкти як на еталон стилю,
читає вказану робочу папку — і збирає Flow, який одразу проходить валідацію.
```

- [ ] **Крок 9: Коміт**

```bash
git add flowai/ui/flow_composer_dialog.py flowai/ui/main_window.py flowai/codex_auth.py README.md tests/test_flow_composer_ui.py
git commit -m "feat: складання нового Flow за допомогою AI через MCP"
```

---

## Самоперевірка плану

**Покриття правок:**

| Правка | Задачі |
|---|---|
| 0 — не виводити консоль при запуску | 0 |
| 1 — ліміт спроб на завдання, жовтий порт, хрестик | 8, 9, 10, 11 |
| 2 — живі дії, проміжні файли, інтерактивні шляхи | 17, 18, 19, 20 |
| 3 — сон ПК | 1 |
| 4 — GrillMe при старті | 25, 26, 27 |
| 5 — темне вікно Files | 2 |
| 6 — сумарний час завдань | 3 |
| 7 — вікно Stats | 4, 5, 6 |
| 8 — MCP | 21, 22, 23, 24 |
| 9 — новий Flow через AI | 28 |
| 10 — вікно Результати | 7 |
| Інтерфейс — шапка, текст, кнопки, анімації | 12, 13, 14, 15, 16 |

**Ризики, які варто тримати на видноті:**

1. **Спайк MCP (задача 24, крок 1) — єдине місце з невизначеністю.** Якщо `thread_start(config={"mcp_servers": ...})` не реєструє сервер, задача 28 працює запасним шляхом (довідник у промпті + валідація постфактум). Це не блокує решту плану.
2. **Стрим (задача 17) обходить `TurnHandle.run()`,** тому перевірка `failed`-статусу, яку робив SDK, перенесена в `agent_run_from_turn`. Якщо цього не зробити, помилка ходу тихо стане порожнім результатом — той самий клас багу, що й у правці 3.
3. **`QFileSystemWatcher` має ліміт дескрипторів.** Тому в задачі 20 стоїть стеля `MAX_WATCHED_DIRECTORIES = 400` і повний перескан раз на 900 мс замість стеження за кожним файлом.
4. **60 fps на канвасі (задача 15)** вмикається лише за наявності активних нод або активного ребра. Крок 7 задачі 15 — обов'язкова перевірка, що в спокої CPU не гріється.
5. **Задача 14 змінює спосіб створення кнопок панелі,** тож тести, які лізли в `toolbar.widgetForAction`, треба оновити — це прописано кроком 7.

**Що навмисно НЕ входить у план:**
- Підняття `FLOW_FORMAT_VERSION` — заборонено глобальним обмеженням.
- Перебудова компоновки головного вікна — на grill-сесії обрано варіант без неї.
- Скляні ефекти Mica/Acrylic — не працюють на Windows 10.
- Ліміт кількості питань GrillMe — ви явно обрали «без ліміту».
