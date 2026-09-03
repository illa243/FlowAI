# FlowAI: надійні переходи між Tasks і прихований Codex console

> **Для agentic workers:** реалізовувати послідовно, починаючи з падаючих
> тестів. Після кожної задачі запускати релевантні тести, а перед завершенням —
> повний `pytest` і `ruff`.

**Мета:** усунути розсинхронізацію між підтвердженим `Result TRUE` та наступним
завданням `Tasks Manager`, а також повністю прибрати появу консольного вікна
`codex.exe` під час запуску або перезапуску агентської ноди у Windows.

**Робоча директорія:**
`C:\Users\illia\Documents\DDA PF\FlowAI`

**Технології:** Python 3.14, PySide6, `openai-codex` SDK 0.147+, Windows
`CREATE_NO_WINDOW`, pytest, ruff.

## Зафіксовані дефекти

### 1. `Result TRUE` не є доступним наступному завданню як системний факт

Фактичний приклад із запуску `20260824-120257-048090`:

1. QA КРОКУ 01 повернув `TRUE`, `score=100`.
2. Користувач підтвердив результат у вікні Result.
3. Внутрішній checkpoint правильно записав перший task у
   `completed_task_ids` і активував КРОК 02.
4. Проєктний `progress.json` залишився зі старими полями
   `selection_status="awaiting_confirmation"`, `confirmed_by_user=false`.
5. Prompt Reviewer КРОКУ 02 зробив старий проєктний файл жорсткою стартовою
   передумовою. Executor відмовився починати КРОК 02, хоча сам Flow уже
   авторитетно підтвердив КРОК 01.
6. QA правильно повернув `FALSE`, бо побачив stale `cut_order.json` і SHA-256
   старого manifest.

Отже, QA працює правильно, але контракт переходу між задачами неповний.

### 2. Під час запуску Codex SDK з'являється консоль

`flowai/codex_adapter.py::_start_client()` створює `openai_codex.Codex`.
SDK усередині `openai_codex.client.CodexClient.start()` викликає
`subprocess.Popen([codex.exe, "app-server", ...])` без `creationflags` і
`startupinfo`. Коли батьківський FlowAI є GUI-процесом, Windows показує окреме
консольне вікно. Воно може з'являтися під час першої агентської ноди та під час
автоматичного `_restart_client()`.

Поточний `login_status()` уже використовує `CREATE_NO_WINDOW`, але основний
SDK transport і прямі виклики SDK у `codex_auth.py` — ні.

## Архітектурні рішення

### Trusted task-transition receipt

Рушій не повинен сам редагувати довільні доменні файли на кшталт
`progress.json`: FlowAI не знає їхню схему. Замість цього він створює власну
надійну квитанцію переходу — `task_transition_receipt` — у checkpoint.

Квитанція створюється лише після фактичного завершення Result:

- для `wait_for_confirmation=true` — після натискання користувачем кнопки
  продовження;
- для автоматичного Result — після остаточного вибору гілки;
- `dismiss`, закриття вікна, PAUSE і незавершений GrillMe не створюють
  квитанцію;
- `FALSE` не створює approval receipt;
- `EXHAUSTED` може створити окремий receipt зі статусом `failed`, але ніколи не
  `approved`.

Мінімальна схема:

```json
{
  "manager_id": "tasks-node-id",
  "task_id": "completed-task-id",
  "result_node_id": "result-node-id",
  "branch": "true",
  "verdict": true,
  "confirmed_by_user": true,
  "confirmed_at": "2026-08-24T18:00:00+03:00",
  "candidate_path": "C:\\...\\01_selection_review_board.png"
}
```

Після переходу `Result TRUE → Tasks Manager` наступна задача отримує квитанцію
як системний контекст. Вона має вищий пріоритет саме як доказ переходу. Якщо
проєктний progress-файл ще містить pre-confirmation стан, агент повинен
атомарно синхронізувати його на початку нового task, а не блокувати сам себе.

Квитанція не означає, що QA наступного кроку треба обходити. Вона підтверджує
лише завершення попереднього task.

### Hidden Codex process launcher

Не редагувати `.venv/Lib/site-packages/openai_codex`: оновлення залежностей
перезапише таку зміну.

Додати локальний фабричний запуск SDK у `flowai/codex_process.py`. На Windows
він має передавати в єдиний `Popen`, який стартує app-server:

```python
creationflags = existing_flags | subprocess.CREATE_NO_WINDOW
startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startupinfo.wShowWindow = subprocess.SW_HIDE
```

Оскільки поточний `CodexConfig` не має `popen_kwargs`, використати вузьку
сумісну обгортку лише на час синхронного конструктора `Codex()`:

1. імпортувати transport-модуль `openai_codex.client`;
2. під lock тимчасово підмінити його атрибут `subprocess` на proxy;
3. proxy делегує всі константи й класи стандартному `subprocess`, але його
   `Popen` додає hidden-window параметри;
4. у `finally` завжди повернути оригінальний атрибут;
5. stdout, stderr, stdin, text mode, cwd та env SDK залишити без змін.

Не підміняти глобальний `subprocess.Popen` у стандартному модулі. Це могло б
вплинути на паралельні процеси FlowAI.

Якщо майбутній SDK офіційно додасть `popen_kwargs` або `creationflags`, перейти
на публічний API й залишити proxy лише як version-gated fallback.

## Файли

| Файл | Зміна |
|---|---|
| `flowai/engine.py` | модель receipt, запис після Result, відновлення старих checkpoint, передача контексту наступному task |
| `flowai/codex_process.py` | новий централізований фабричний запуск SDK без console window |
| `flowai/codex_adapter.py` | використовувати фабрику для `_start_client()` і `_restart_client()` |
| `flowai/codex_auth.py` | використовувати ту саму фабрику для account/model/logout викликів |
| `flowai/models.py` | уточнити стандартні інструкції Prompt Reviewer про trusted transition |
| `FLOWAI_NODE_GUIDE.md` | документувати receipt і порядок синхронізації проєктних checkpoint-файлів |
| `tests/test_core.py` | регресійні тести переходу між Tasks |
| `tests/test_codex_process.py` | тести hidden Windows launch і fallback |
| `tests/test_process_guard.py` | перевірка сумісності hidden process із Job Object та STOP |

---

## Task 1 — Падаючі тести для підтвердженого переходу

**Files:**

- Modify: `tests/test_core.py`

- [ ] Створити Flow `Tasks → Executor → QA → Result`, де Result має
  `wait_for_confirmation=true` і TRUE повертається назад у Tasks.
- [ ] На першому запуску перевірити, що до підтвердження receipt відсутній.
- [ ] Відновити запуск із response `{"action": "continue"}`.
- [ ] Перевірити, що перший task є completed, другий active, а checkpoint має
  approval receipt для першого task.
- [ ] Перевірити поля `branch`, `verdict`, `confirmed_by_user`, `candidate_path`
  і ISO timestamp.
- [ ] Перевірити, що `continue_with_feedback` на FALSE не створює approval
  receipt.
- [ ] Перевірити, що `dismiss`, STOP і незавершений GrillMe не створюють receipt.
- [ ] Перевірити, що EXHAUSTED не може бути інтерпретований як approved.

Очікування: нові тести падають, бо `RunCheckpoint` поки не зберігає trusted
transition.

## Task 2 — Зберігання та backward-compatible відновлення receipt

**Files:**

- Modify: `flowai/engine.py`
- Test: `tests/test_core.py`

- [ ] Додати до `RunCheckpoint` поле
  `task_transition_receipts: dict[str, dict[str, Any]]`.
- [ ] Додати поле в `to_dict()` і толерантний `from_dict()` без зміни формату
  `.flowai.json`.
- [ ] Ключ формувати як `manager_id:task_id`, щоб кілька Tasks Manager не
  перетирали одне одного.
- [ ] У `_execute_result()` записувати receipt лише після остаточного branch і
  лише коли action справді дозволив продовження.
- [ ] Зберігати receipt до dispatch, щоб аварія після Result не втратила
  підтвердження.
- [ ] Для старих checkpoint реалізувати recovery із Result history: якщо запис
  містить `branch=true`, `task_id`, а task уже є у `completed_task_ids`, створити
  міграційний receipt. Для Result із `wait_for_confirmation=true` такий
  завершений запис вважати підтвердженим користувачем.
- [ ] Recovery має бути idempotent і не змінювати актуальні receipts.

## Task 3 — Передача receipt наступній задачі як системного контексту

**Files:**

- Modify: `flowai/engine.py`
- Modify: `flowai/models.py`
- Test: `tests/test_core.py`

- [ ] Додати останній receipt попереднього task у data-вихід Tasks Manager як
  `previous_task_transition`.
- [ ] Автоматично додавати окремий розділ `# Підтверджений перехід Flow` до
  developer instructions Prompt Reviewer, Executor і Task Reviewer.
- [ ] Явно зазначити: це авторитетний доказ попереднього Result TRUE; stale
  доменний progress-файл треба синхронізувати як першу атомарну дію поточного
  task, якщо сам task вимагає такої синхронізації.
- [ ] Не дозволяти Prompt Reviewer перетворювати pre-confirmation marker на
  умову, яку неможливо виконати. Він має зберігати формулювання активного task
  «на початку познач попередній крок approved».
- [ ] Не передавати receipts від іншого Tasks Manager або старішого task.
- [ ] Додати receipt до protocol/work-review для діагностики.

## Task 4 — Регресійний тест фактичної помилки Step 01 → Step 02

**Files:**

- Modify: `tests/test_core.py`

- [ ] Створити тимчасовий `progress.json` зі
  `selection_status="awaiting_confirmation"`.
- [ ] Провести Step 01 через QA TRUE і ручне підтвердження.
- [ ] Перевірити, що Step 02 отримує receipt навіть якщо файл ще stale.
- [ ] Імітований Executor має атомарно оновити progress до `approved` і
  продовжити побудову Step 02, а не повернути self-blocking FALSE.
- [ ] Перевірити, що без receipt такий самий stale progress коректно блокує
  роботу.
- [ ] Перевірити відновлення поточного старого checkpoint після перезапуску
  FlowAI.

## Task 5 — Падаючі тести hidden Codex launch

**Files:**

- Create: `tests/test_codex_process.py`

- [ ] Перевірити, що на Windows wrapper додає `CREATE_NO_WINDOW` до вже наявних
  creation flags, а не перезаписує їх.
- [ ] Перевірити `STARTF_USESHOWWINDOW` і `SW_HIDE`.
- [ ] Перевірити збереження `stdin/stdout/stderr`, `cwd`, `env`, `text`,
  `encoding` та інших kwargs SDK.
- [ ] Перевірити, що на Linux/macOS kwargs не змінюються.
- [ ] Перевірити, що transport-модуль SDK відновлюється у `finally`, навіть
  якщо конструктор Codex кидає виняток.
- [ ] Перевірити lock двома конкурентними фабричними викликами.
- [ ] Перевірити version-gated fallback, якщо transport module або його
  `subprocess` недоступні.

## Task 6 — Централізований запуск Codex без консолі

**Files:**

- Create: `flowai/codex_process.py`
- Modify: `flowai/codex_adapter.py`
- Modify: `flowai/codex_auth.py`
- Test: `tests/test_codex_process.py`

- [ ] Реалізувати `create_codex(config=None)` або контекстну фабрику з proxy
  transport subprocess.
- [ ] У `CodexAdapter._start_client()` використовувати тільки цю фабрику.
- [ ] Переконатися, що `_restart_client()` проходить тим самим шляхом і не може
  повторно показати console window.
- [ ] Перевести `available_models()`, `read_codex_user()` і
  `logout_codex_user()` на фабрику.
- [ ] Не змінювати `login_status()`, окрім переходу на спільний helper flags за
  бажанням: він уже використовує `CREATE_NO_WINDOW`.
- [ ] Зберегти наявний `guard_subprocess_tree()` після створення SDK client.
- [ ] Перевірити, що STOP усе ще interrupt-ить turn і закриває повне дерево
  `codex.exe/node_repl.exe`.

## Task 7 — Інтеграційна перевірка Windows і документація

**Files:**

- Modify: `tests/test_process_guard.py`
- Modify: `FLOWAI_NODE_GUIDE.md`

- [ ] Windows-only test: запустити консольний child через той самий hidden
  launcher і перевірити, що для його PID немає видимого top-level console HWND.
- [ ] Запустити агентську ноду, дочекатися app-server і перевірити stdout JSON-RPC
  та отримання відповіді.
- [ ] Примусово перезапустити transport і перевірити відсутність другого
  консольного flash.
- [ ] Натиснути PAUSE під час turn: процес лишається живим, консоль не з'являється.
- [ ] Натиснути STOP під час turn: повне process tree завершується без зависання.
- [ ] Перезапустити FlowAI на paused Result і переконатися, що transition receipt
  відновився з checkpoint.
- [ ] Доповнити Node Guide: внутрішній receipt є джерелом істини для переходу,
  але не замінює QA активного кроку.

## Команди перевірки

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_core.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_codex_process.py tests\test_process_guard.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check flowai tests
```

## Критерії приймання

- Після ручного підтвердження Result TRUE наступний task завжди бачить trusted
  receipt попереднього task.
- Старий `progress.json` не створює self-block, якщо активний task прямо вимагає
  синхронізувати його після підтвердженого переходу.
- FALSE, dismiss, PAUSE, незавершений GrillMe та STOP не створюють фальшивого
  approval.
- Старі checkpoint відновлюють receipt без ручного редагування JSON.
- Під час першого запуску agent node, transport restart, account read і model
  list користувач не бачить вікна `codex.exe`, `cmd.exe` або PowerShell.
- JSON-RPC stdio, логування stderr, authentication, PAUSE, STOP і Job Object
  cleanup працюють як до зміни.
- Жодних правок у `.venv/site-packages`.
- Повні pytest і ruff проходять.

## Не входить у цей план

- Автоматичне редагування рушієм довільних `progress.json` користувацьких
  проєктів.
- Приховування термінала, який користувач сам явно відкрив.
- Обхід QA або автоматичне прийняття наступного task.
- Зміна семантики кнопок PAUSE/STOP/GrillMe.
