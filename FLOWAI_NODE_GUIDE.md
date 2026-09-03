# FlowAI: довідник зі складання Flow для AI

Цей файл є короткою технічною специфікацією для AI-агента або розробника, який
створює чи редагує `*.flowai.json`. Докладний опис інтерфейсу міститься у
[`DOCUMENTATION.md`](DOCUMENTATION.md), а готовий базовий приклад — у
[`examples/review_loop.flowai.json`](examples/review_loop.flowai.json).

Звірено з кодом 3 вересня 2026 року: пакет 0.4.0, формат 2, вісім типів нод.

## Головні правила

1. Поточний `format_version` — `2`.
2. Ідентифікатори нод і зв’язків мають бути непорожніми та унікальними.
3. Звичайна нода має вихід `out`; `Result` має `true`, `false` і `exhausted`; `Tasks Manager`
   має `next` і `done`; `Work Reviewer` не має портів.
4. Зворотний зв’язок дозволений лише з `Result`:
   - `Result: false` повертає невдалу роботу на переробку;
   - `Result: true` повертається до `Tasks Manager`, щоб завершити поточне
     завдання й отримати наступне.
   - `Result: exhausted` повертається до `Tasks Manager`, щоб зафіксувати
     невдачу активного завдання та перейти до наступного.
5. Перед кожним `Result` у висхідному ланцюжку має бути `Task Reviewer`, інакше
   `Result` не матиме надійного вердикту.
6. **QA — обов’язковий шлюз, а не паралельна гілка.** Якщо результат ноди
   перевіряє `Task Reviewer` або Visual QA, ця нода не може одночасно вести
   напряму до `Result`, наступного виконавця, генератора чи іншого етапу. Єдиний
   дозволений маршрут: `Виконавець/Generator → Task Reviewer/Visual QA → Result`,
   а наступний етап запускається лише з прийнятої гілки `Result: true`. Зв’язок,
   який дозволяє пройти далі в обхід перевірки, заборонений навіть тоді, коли
   паралельний зв’язок із QA теж існує. Це правило не стосується службового
   `Work Reviewer`, який взагалі не з’єднується з графом.
7. `Tasks Manager` обов’язково повинен мати:
   - хоча б одне завдання з непорожнім промптом;
   - зв’язок із виходу `next`;
   - вхідний зворотний зв’язок із `Result: true`.
8. `Work Reviewer` — бічна службова нода. Її не можна з’єднувати лініями, і у
   Flow може бути лише один такий блок.
9. Якщо агент має виконувати промпт попередньої ноди, встановіть
   `prompt_source: "input"` і передайте значення у змінну `prompt`.
10. Для структурованого рішення `Task Reviewer` використовуйте JSON із полями
   `verdict`, `score`, `reason`, `must_fix` і, якщо потрібен файл,
   `candidate_path`.
11. Не додавайте паралельні гілки без необхідності: одна й та сама нода запускається
    після кожного вхідного пакета, тому різні входи можуть спричинити окремі
    проходи.
12. **Один Flow — одна проєктна тека.** Усі нові файли, підтеки, тимчасові
    матеріали, скрипти, звіти й фінальні артефакти дозволено створювати лише
    всередині теки, де лежить відповідний `*.flowai.json`. Абсолютні шляхи та
    додаткові папки поза нею є джерелами лише для читання. Не створюйте кілька
    `*.flowai.json` поруч в одній спільній теці — кожен Flow має власну теку.

## Архітектура Flow-проєкту

Рекомендована структура однакова для ручного запуску й AI-генерації:

```text
Назва_проєкту/
├── Назва_проєкту.flowai.json
├── artifacts/   # фінальні й проміжні результати агентів
├── tools/       # створені під час роботи скрипти та утиліти
├── reports/     # окремі звіти, якщо вони не належать конкретному запуску
└── runs/        # журнали, checkpoint і Work Reviewer кожного запуску
```

- Проєктна тека є єдиним writable workspace.
- `workspace`, `additional_folders` і однойменні поля нод описують зовнішні
  джерела. Вони не є дозволеними місцями для нових результатів.
- Якщо старий промпт містить зовнішній output path, перенесіть його логічну
  структуру в `artifacts/` і поверніть фактичний шлях усередині проєкту.
- Службові `.py`, маски, crop, тимчасові зображення та діагностика не повинні
  з'являтися поруч із кодом FlowAI або у вихідних теках користувача.

## Мінімальна структура файла

```json
{
  "format_version": 2,
  "name": "Назва Flow",
  "workspace": "C:/optional/read-only/source/path",
  "additional_folders": [],
  "nodes": [],
  "edges": []
}
```

Координати ноди зберігаються у `x` та `y`. Усі специфічні параметри лежать у
`config`. Не видаляйте невідомі поля з наявного файла без причини: нові версії
FlowAI можуть використовувати їх для стану або сумісності.

## Контракт зв’язку

```json
{
  "id": "unique-edge-id",
  "source": "source-node-id",
  "target": "target-node-id",
  "source_port": "out",
  "source_path": "data",
  "target_variable": "input",
  "condition": "",
  "transform": "",
  "label": "",
  "control_points": []
}
```

- `source_port` — `out`, `true`, `false`, `exhausted`, `next` або `done` залежно від ноди.
- `source_path` — що взяти з `NodeResult`: `$`, `text`, `data`,
  `data.improved_prompt`, `data.retry_context` тощо.
- `target_variable` — ім’я у `inputs` наступної ноди, наприклад `prompt`, `work`,
  `review`, `criteria`, `attachments` або `input`.
- `condition` — необов’язкова безпечна умова на кшталт
  `source.status == "success"`.
- `transform` — необов’язковий шаблон із `{{value}}`.
- `control_points` — візуальні точки вигину; на логіку вони не впливають.

Для agent node, яка має працювати за один раз проаналізованою бібліотекою
референсів, використовуйте content-addressed cache:

```json
{
  "reference_cache": {
    "mode": "sha256_once",
    "source_dir": "C:/path/to/UI_refs",
    "manifest_path": "C:/path/to/skill/references/reference-manifest.json",
    "analysis_path": "C:/path/to/skill/references/ui-reference-analysis.md",
    "library_sha256": "expected lowercase SHA-256"
  }
}
```

На першій style-aware ноді runner перевіряє file-level SHA-256 і додає готовий
analysis до інструкцій. Інші ноди з тим самим config використовують receipt у
пам'яті runner: нового AI-аналізу всієї теки немає. Якщо склад або вміст
бібліотеки змінився, нода переходить у `Pause · Attention` з типом
`reference_analysis_attention`, доки manifest та analysis не будуть оновлені.
Source directory завжди лишається read-only; усі outputs створюються у workspace.

Якщо наступній ноді потрібне конкретне поле, передавайте саме це поле. Передача
всього `data` у змінну `input` зручна для `Tasks Manager → Prompt Reviewer`, бо
Prompt Reviewer розпізнає активне завдання у структурованому пакеті.

## Типи нод

### Entry prompt (`entry`)

Призначення: одноразове джерело загального завдання, початкового JSON і спільних
вкладень.

Параметри:

- `text` — основний текст завдання;
- `json` — структуровані початкові дані;
- `attachments` — файли та картинки.

Типовий вихід: `out`. Для агента передавайте `text → prompt`, якщо у нього
`prompt_source: "input"`, або `data → input`, якщо потрібен увесь пакет.

Не ставте `Entry prompt` перед `Tasks Manager`, якщо кожне завдання менеджера вже
містить повний промпт. У такому Flow початковою нодою є сам `Tasks Manager`.

### Tasks Manager (`tasks_manager`)

Призначення: послідовна черга незалежних завдань. Менеджер видає одне завдання,
очікує успішний `Result: true`, ставить галочку й лише тоді переходить до
наступного.

Параметри:

```json
{
  "tasks": [
    {
      "id": "stable-unique-task-id",
      "prompt": "Перше завдання з чітким очікуваним результатом",
      "attachments": ["C:/absolute/path/reference.png"]
    },
    {
      "id": "another-unique-task-id",
      "prompt": "Друге завдання",
      "attachments": []
    }
  ]
}
```

Для плану, який створює попередня нода, використовуйте:

```json
{
  "task_source": "input_once",
  "plan_save_path": "ui_project_spec.json"
}
```

На першому проході менеджер знаходить `approved_plan` або `ui_project_spec.tasks`
у входах, нормалізує Tasks і зберігає plan snapshot та SHA-256 у checkpoint.
Після PAUSE, retry або перезапуску вхід більше не перечитується: черга завжди
відновлюється із замороженого snapshot. `plan_save_path` мусить залишатися
всередині проєктної теки.

Виходи:

- синій `next` — активне завдання; спрацьовує, поки у черзі є невиконані задачі;
- зелений `done` — підсумок черги; спрацьовує після завершення всіх задач.

Пакет `next.data` містить:

```json
{
  "branch": "next",
  "prompt": "Текст активного завдання",
  "task": {
    "id": "task-id",
    "index": 0,
    "number": 1,
    "title": "Перша непорожня строка промпту",
    "prompt": "Текст активного завдання",
    "attachments": []
  },
  "attachments": [],
  "previous_task_transition": {
    "status": "approved",
    "manager_id": "tasks-manager-id",
    "task_id": "previous-task-id",
    "result_node_id": "result-id",
    "branch": "true",
    "verdict": true,
    "confirmed_by_user": true,
    "confirmed_at": "2026-08-24T18:00:00+03:00",
    "candidate_path": "C:/project/artifacts/review-board.png"
  },
  "tasks": [],
  "completed_count": 0,
  "task_count": 2
}
```

Вкладення активного завдання автоматично додаються до всіх агентів прямого
ланцюжка до `Result`. Через `Result` вони не переносяться до іншого циклу.

`previous_task_transition` з'являється, починаючи з другого завдання, лише після
фактичного `Result TRUE` попереднього task. Це trusted receipt рушія, який
зберігається в checkpoint під ключем `manager_id:task_id`. Для Result із ручним
підтвердженням квитанція створюється тільки після натискання «Продовжити»;
закриття діалогу, PAUSE, STOP, незавершений GrillMe, FALSE та EXHAUSTED не
створюють approval receipt. Старі checkpoint мігруються ідемпотентно з історії
вже завершених `Result TRUE`.

Prompt Reviewer, Executor і Task Reviewer отримують цю квитанцію в окремому
розділі системних інструкцій `Підтверджений перехід Flow`. Налаштований
`transition_adapter` атомарно синхронізує предметний `progress.json` разом із
receipt; агентам заборонено вручну міняти status/checkpoint. Під час відкриття
Flow reconciliation ідемпотентно повторює adapter для старих receipts. Receipt
не замінює QA активного завдання.

На картці менеджера:

- сіра крапка — завдання ще не почалося;
- синій рухомий індикатор — завдання зараз проходить наступний ланцюжок;
- зелена галочка — `Result: true` повернув успішне завершення.

Вихід `done` можна не підключати: після спорожнення черги Flow завершиться, коли
не залишиться інших запланованих нод. Підключайте його до окремого фінального
ланцюжка, якщо потрібне зведення або звіт.

### Prompt Reviewer (`prompt_reviewer`)

Призначення: переписати сирий промпт у точний, повний і перевірюваний контракт
для виконавця. Бачить опис подальшого ланцюжка.

Рекомендований формат відповіді:

```json
{
  "improved_prompt": "string",
  "notes": ["string"]
}
```

Для `Tasks Manager` рекомендований зв’язок:

```text
Tasks Manager.next: data → Prompt Reviewer: input
```

Prompt Reviewer автоматично бере `task.prompt` або `data.prompt` як
`entry_prompt`. Далі передавайте `data.improved_prompt → Task Executor: prompt`.

### Task Executor (`executor`)

Призначення: виконати задачу й, за наявності зворотної гілки, виправити результат.

Рекомендовані налаштування:

- `prompt_source: "input"`;
- початковий промпт у змінній `prompt`;
- `memory: "thread"`, коли той самий агент має допрацьовувати свою роботу;
- `sandbox: "workspace-write"`, якщо треба створювати або змінювати файли;
- `workspace` або робоча папка Flow мають вказувати на потрібний проєкт.

Для повторної переробки передавайте
`Result.false: data.retry_context → Task Executor: prompt`. У цьому пакеті є
конкретні `must_fix`, причина та шлях до перевіреного кандидата.

### Task Reviewer (`task_reviewer`)

Призначення: незалежно перевірити роботу виконавця за критеріями завдання.

Рекомендований формат відповіді:

```json
{
  "verdict": true,
  "score": 100,
  "candidate_path": "C:/absolute/path/result.png",
  "reason": "Чому робота прийнята або відхилена",
  "must_fix": [],
  "issues": [],
  "checks": []
}
```

Звичайні входи:

- результат виконавця `→ work`;
- початкове або покращене завдання `→ criteria`;
- за потреби структурований пакет `→ input`.

`sandbox: "read-only"` означає, що рев’ювер не змінює файли, але його текстовий
JSON-вердикт усе одно передається наступній ноді через Flow. Read-only обмежує
файлові операції агента, а не обмін даними між блоками.

Нові ноди мають `strict_review_contract: true`, `pass_threshold: 80` та
`qa_correction_attempts: 1`. `verdict` має бути boolean, `score` — цілим 0–100.
TRUE дозволено лише за оцінки не нижче порога, без `must_fix`, failed checks
і blocking issues. FALSE потребує issue або `system_error`; старі
`reason`/`must_fix` нормалізуються для сумісності.

Issue є блокувальним за severity `blocking|blocker|critical|error` **або** за
category `missing_requirement|technical_blocker|visual_mismatch`. Ці категорії
блокують приймання навіть із `severity: "warning"` чи `"info"`. Не змінюйте
категорію реального дефекту лише заради TRUE. Необов'язкову пораду можна
описати в `reason`, якщо вона не є порушенням вимог.

Після вичерпання виправлень JSON Runner створює `invalid_qa_contract`,
зберігаючи поточну QA-ноду й її входи в checkpoint. UI показує помилки та
фрагмент відповіді. **Повторити QA** надсилає `retry_task` із поясненням,
а не приймає некоректний verdict. X/Esc зберігає паузу; **Зупинити Flow**
залишає прогрес для продовження.

### Result (`result`)

Призначення: прочитати вердикт `Task Reviewer`, вибрати `true` або `false`,
зберегти прийнятий результат і керувати лімітами циклу.

Параметри:

- `template` — текст фінального результату;
- `save_path` — необов’язковий файл для гілки `true`;
- `true_limit` — кількість дозволених проходів `true`;
- `false_limit` — кількість дозволених проходів `false`;
- `task_attempt_limit` — спроби одного завдання до виходу `EXHAUSTED`;
- `wait_for_confirmation` — пауза перед переходом для ручного огляду файлів.
- `confirmation_mode` — `standard`, `plan_approval`, `variant_selection` або
  `asset_approval`;
- `confirmation_ports` — порти, на яких потрібне ручне підтвердження;
- `final_task_result` — лише такий Result створює trusted receipt і завершує
  активний task;
- `retry_guard_enabled` / `retry_guard_threshold` — PAUSE при повторному
  stable `defect_id` або регресії score;
- `retry_contract_enabled` — зберігати failed checks, protected PASS, editable
  files/regions та immutable SHA-256 для цільового retry;
- `transition_adapter` — declarative `json_merge` для атомарного предметного
  state patch після TRUE. Точні маркери `{state.<field>}` беруть типізоване
  значення зі стану до merge, `{receipt.<field>}` — з trusted receipt;
  `default_append_unique` і `task_append_unique` розділяють стандартні та
  task-specific доповнення списків;
- `learning_enabled` — додавати QA/user review у project-local learning.

`plan_approval` показує редагований JSON-план. `variant_selection` показує
checkbox для V01–V04 і повертає `selected_variant_ids`, `selection_mode`, `note`
та `approved_artifact_hash`. `asset_approval` дозволяє override лише для
`visual_preference`; `technical_blocker`, `visual_mismatch` і
`missing_requirement` треба виправити.

Для UI Flow ставте проміжним Result `final_task_result: false`, а Result після
PSD QA — `true`. Тоді вибір концепту або Synthesis PNG не завершить task завчасно.

При з’єднанні `Result.true → Tasks Manager` ефективний ліміт `true` автоматично
стає не меншим за кількість завдань. Це дає менеджеру завершити всю чергу навіть
тоді, коли у Parameters залишено значення `1`.

Типові маршрути:

- `false → Task Executor` для виправлення;
- `true → Tasks Manager` для наступного завдання;
- `exhausted → Tasks Manager` після вичерпання бюджету активного завдання;
- `true → фінальна нода` у звичайному одноразовому Flow.

Жовтий вихід `EXHAUSTED` спрацьовує, коли активне завдання вичерпало
`task_attempt_limit` (типово 2). Завдання отримує статус `failed`, а менеджер
автоматично переходить до наступного. Без ребра `EXHAUSTED` лишається діалог
додавання спроб.

### Calibration Stop (`calibrator`)

**Колір:** `#E11D48`. **Входи:** лише вихід FALSE блока Result.
**Вихід:** `out`, який з'єднується з Executor і передає `data.retry_context`
у його змінну `prompt`. Пряме ребро Result.FALSE → Executor у такому маршруті
не допускається.

Після K-го відхилення аналізує невдалий прохід перед повторним виконавцем.
З `memory: fresh` аналіз працює в незалежному Codex-треді.

| Поле | Що робить |
|---|---|
| `false_threshold` | Після якого FALSE зупинятись. За замовчуванням 2 |
| `auto_skip` | Повністю пропускає модель, звіт та intervention; за замовчуванням `false` |
| `reviewed_nodes` | ID нод, для яких створюються окремі `node_reviews` і секції правок |
| `skills` | Скіли, закріплені за нодою: `[{'name': ..., 'path': ...}]` |
| `thread_source` | id ноди, чий тред продовжується; рушій заповнює сам |

Повний опис — у [guides/calibration.md](guides/calibration.md).

### Work Reviewer (`work_reviewer`)

Призначення: записати Markdown-протокол проходів і після завершення Flow оцінити
якість роботи інших агентів.

Нода не має портів. Виберіть `monitor_all: true` або задайте `monitored_nodes`.
Звіт пишеться у `report_path` або у папку поточного запуску.

У шаблоні `examples/game_ui_workflow.flowai.json` Work Reviewer виконує роль
UI Knowledge Curator. Структуровані Result одразу оновлюють
`learnings/ui_learnings.jsonl` та `learnings/ui_project_profile.md`, а Curator
після запуску аналізує протокол. Він може створити diff у
`learnings/skill-proposals/`, але рушій ніколи не змінює global `modern-ui`
автоматично.

### Photoshop і чотири UI-концепти

Executor із `variant_contract_enabled: true` повинен повернути рівно V01–V04.
Рушій перевіряє існування PNG, фактичні SHA-256, новий `round_id` і незмінність
`frozen_variants`. `enforce_project_outputs: true` відхиляє задекларовані файли
поза workspace.

Для PSD Builder використовуйте `photoshop_required: true`. FlowAI перевіряє
наявність Photoshop, запускає COM/JSX без консолі, зберігає JSX та validation
report у `.flowai/runtime/photoshop/` і повторно відкриває справжній PSD. Помилка
Photoshop переводить Flow у `Pause · Attention`, не створюючи placeholder-файл.

## Спільні параметри агентів

Спільні агентські поля доступні `Prompt Reviewer`, `Task Executor`,
`Task Reviewer`, `Calibration Stop` і `Work Reviewer`; спеціалізовані блоки
можуть додавати власні інструкції й особливості виконання:

- `model` — модель Codex;
- `reasoning_effort` — `none`, `low`, `medium`, `high`, `xhigh` або `max`;
- `instructions` — постійні інструкції, що додаються до кожного запиту ноди;
- `instruction_files` — MD-файли постійних інструкцій;
- `prompt` — шаблон запиту;
- `prompt_source` — `template` або `input`;
- `sandbox` — `read-only`, `workspace-write` або `full-access`;
- `workspace` — додаткове джерело ноди; для збереженого Flow тека запису
  залишається папкою його `.flowai.json`;
- `additional_folders` — додаткові доступні папки;
- `output_format` — `text` або `json`;
- `output_schema` — схема очікуваної JSON-відповіді;
- `attachments` — постійні вкладення ноди;
- `retries` — повтори лише після технічної помилки, не після негативного рев’ю;
- `memory` — `thread`, `fresh` або `task_thread`; останній ізолює контекст за
  `task_id`, але зберігає потрібну історію retry поточного Task;
- `context_soft_limit` — після перевищення контексту наступний retry отримує
  чистий task thread;
- `prompt_cache_enabled` / `qa_cache_enabled` — content-addressed cache за
  prompt, schema та SHA-256 фактичних inputs;
- `operation_policy` — max iterations, patience, min delta і checkpoint cadence;
- `operation_intent_required` — перед ітеративним Python-скриптом перевірити
  target check, outputs, metric і budget проти retry contract.

`timeout_seconds` вилучено з конфігурації: він не обмежує час SDK-запиту.
Не додавайте його в нові Flow. Повтори після технічної помилки, виправлення
JSON QA та цикл FALSE мають різні лічильники й призначення.

`instruction_files` читаються відносно теки Flow або за абсолютним шляхом.
Вони повинні існувати та мати формат `.md`/`.markdown`. Не дублюйте один
`SKILL.md` одночасно в skills і MD-інструкціях. Підключення інструкцій не
встановлює потрібні skill інструменти; для досліджень дивіться
[Deep research](guides/deep-research.md).

На Windows усі внутрішні запуски Codex app-server — перший старт, транспортний
restart, login, logout, читання акаунта та списку моделей — проходять через
централізований launcher із `CREATE_NO_WINDOW`, `STARTF_USESHOWWINDOW` і
`SW_HIDE`. Це прибирає спалах консолі `codex.exe`, не змінюючи stdio JSON-RPC,
PAUSE, STOP або очищення дерева процесів через Job Object.

Найменші необхідні права:

- `read-only` — аналіз і QA без зміни файлів;
- `workspace-write` — генерація та редагування у теці Flow-проєкту;
- `full-access` — лише коли агенту справді потрібні дії поза дозволеними папками.

## Рекомендовані схеми

### Один результат із циклом виправлень

```text
Entry prompt → Prompt Reviewer → Task Executor → Task Reviewer → Result
                                        ▲                          │
                                        └──────── FALSE ───────────┘
```

Ключові мапінги:

```text
Entry.text → Prompt Reviewer: entry_prompt
Prompt Reviewer.data.improved_prompt → Task Executor: prompt
Task Executor.data → Task Reviewer: work
Task Reviewer.data → Result: review
Result.data.retry_context → Task Executor: prompt
```

### Черга Tasks Manager

```text
Tasks Manager.NEXT → Prompt Reviewer → Task Executor → Task Reviewer → Result
       ▲                                                          │
       └────────────────────── TRUE ───────────────────────────────┘

Tasks Manager.DONE → необов’язкове зведення або завершення
Result.FALSE       → Task Executor для виправлення поточного завдання
```

Рекомендовані мапінги:

```text
Tasks Manager.next.data → Prompt Reviewer: input
Prompt Reviewer.data.improved_prompt → Task Executor: prompt
Task Executor.data → Task Reviewer: work
Task Reviewer.data → Result: review
Result.false.data.retry_context → Task Executor: prompt
Result.true.data → Tasks Manager: input
```

Для критеріїв установіть `Task Reviewer.criteria_node` у ID Prompt Reviewer
або Tasks Manager. Не додавайте окреме ребро менеджера до QA лише для критеріїв:
воно може поставити QA в чергу до готовності роботи виконавця.

Повернення `Result.true` до менеджера є сигналом завершення активного завдання.
Не повертайте до менеджера `false`: воно має залишити активним те саме завдання й
піти на переробку.

### Генерація файла з візуальним QA

```text
Entry/Tasks Manager → Generator → Visual QA (Task Reviewer) → Result
                           ▲                              │
                           └────────── FALSE ─────────────┘
```

Генератору потрібен `workspace-write`; Visual QA достатньо `read-only`. Передайте
рев’юверу абсолютний `candidate_path` і референси як вкладення. На `false`
поверніть структурований `retry_context`, а не лише фразу «перегенеруй».

Цей ланцюжок є послідовним шлюзом приймання. Не створюйте паралельне ребро
`Generator → Result` або `Generator → наступна нода`: воно запустить продовження
до отримання QA-вердикту. Наступна робоча нода повинна отримувати прийнятий
результат тільки через `Result.true`; неприйнятий результат повертається на
переробку тільки через `Result.false`.

### Аудит виконання

Додайте один `Work Reviewer`, увімкніть спостереження за всіма блоками й не
з’єднуйте його з графом. Він запуститься як службовий аналізатор після основного
маршруту.

## Контрольний список перед збереженням

- Усі `id` унікальні, усі `edge.source` та `edge.target` існують.
- Порти відповідають типам вихідних нод.
- У кожного `Result` є висхідний `Task Reviewer`.
- У кожного Tasks Manager є `next` і повернення `Result.true`.
- Кожне завдання Tasks Manager має промпт і стабільний унікальний `id`.
- `prompt_source: "input"` має вхідну змінну `prompt`.
- Генератори файлів мають `workspace-write` і правильну робочу папку.
- QA-ноди отримують критерії, результат і потрібні референси.
- Жодне ребро не дозволяє перейти від перевірюваної ноди до наступного етапу в
  обхід `Task Reviewer/Visual QA → Result.true`.
- `false_limit` достатній для циклу виправлень, але не безмежний.
- Є лише один `Work Reviewer`, і він не має зв’язків.
- `Workflow.validate()` не повертає помилок.

## Геометрія зв'язків і перетини ліній

Після побудови або редагування графа розташуйте блоки так, щоб лінії зв'язків
не перетиналися. Це обов'язкова частина готового Flow, а не косметична правка.

1. Спочатку розкладіть основний маршрут зліва направо за топологічним порядком.
   Паралельні гілки ставте в окремих рядках, а блоки одного етапу переставляйте
   по вертикалі, доки прямі зв'язки між сусідніми колонками не перетинаються.
2. Зворотні цикли `Result.false` і `Result.true` прокладайте окремими зовнішніми
   коридорами над або під графом. Не ведіть їх крізь блоки чи крізь середину
   основного маршруту.
3. Після `auto_layout` прочитайте координати нод і всіх ребер. Якщо автоматична
   розкладка залишила перетини, скоригуйте координати через `set_node_position`
   та задайте ребрам `control_points` через `set_edge_control_points`.
4. Не накладайте різні ребра одне на одне на довгій ділянці. Для паралельних
   маршрутів використовуйте окремі коридори з помітним відступом.
5. Якщо повністю планарна схема неможлива, мінімізуйте загальну кількість
   перетинів: згрупуйте їх у одному локальному місці й не допускайте, щоб ті
   самі лінії перетиналися багато разів у різних частинах полотна.
6. Перед збереженням ще раз перевірте, що лінії не проходять крізь блоки,
   підписи не накладаються на вузли, а `control_points` утворюють короткий і
   зрозумілий маршрут без зайвих вигинів.

## Поради AI, який редагує готовий Flow

1. Спочатку прочитайте весь JSON і визначте роль кожної ноди за `kind`, а не лише
   за довільною `title`.
2. Збережіть існуючі `id`, координати й `control_points`, якщо не перебудовуєте
   граф навмисно.
3. Міняйте інструкції конкретної ноди відповідно до її ролі; не дублюйте весь
   промпт в усіх агентах.
4. Передавайте файли через `attachments`, а їхні шляхи й висновки — через дані
   зв’язків. Не покладайтеся лише на згадку файла у тексті.
5. Для кожної задачі сформулюйте перевірюваний результат: шлях файла, формат,
   критерії прийняття й те, що змінювати заборонено.
6. Після редагування завантажте JSON через
   `flowai.persistence.load_workflow(Path(...))` і виконайте
   `workflow.validate()`. Порожній список означає успішну перевірку графа;
   доступність інструментів та якість результату перевіряються окремо.
## Поле «Скіли» в агентських блоках

Будь-який блок-агент може мати закріплені скіли. Вони передаються Codex як
`SkillInput` і завантажуються до першого кроку агента.

Локальний список FlowAI сканує `%USERPROFILE%/.codex/skills` та `.system`.
Plugin-skills з інших кешів можуть бути відсутні у цьому списку. Наявний
`SKILL.md` можна підключити через `instruction_files`; він не додає відсутні
інструменти чи права доступу автоматично.
