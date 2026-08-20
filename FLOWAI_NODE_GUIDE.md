# FlowAI: довідник зі складання Flow для AI

Цей файл є короткою технічною специфікацією для AI-агента або розробника, який
створює чи редагує `*.flowai.json`. Докладний опис інтерфейсу міститься у
[`DOCUMENTATION.md`](DOCUMENTATION.md), а готовий базовий приклад — у
[`examples/review_loop.flowai.json`](examples/review_loop.flowai.json).

## Головні правила

1. Поточний `format_version` — `2`.
2. Ідентифікатори нод і зв’язків мають бути непорожніми та унікальними.
3. Звичайна нода має вихід `out`; `Result` має `true` і `false`; `Tasks Manager`
   має `next` і `done`; `Work Reviewer` не має портів.
4. Зворотний зв’язок дозволений лише з `Result`:
   - `Result: false` повертає невдалу роботу на переробку;
   - `Result: true` повертається до `Tasks Manager`, щоб завершити поточне
     завдання й отримати наступне.
5. Перед кожним `Result` у висхідному ланцюжку має бути `Task Reviewer`, інакше
   `Result` не матиме надійного вердикту.
6. `Tasks Manager` обов’язково повинен мати:
   - хоча б одне завдання з непорожнім промптом;
   - зв’язок із виходу `next`;
   - вхідний зворотний зв’язок із `Result: true`.
7. `Work Reviewer` — бічна службова нода. Її не можна з’єднувати лініями, і у
   Flow може бути лише один такий блок.
8. Якщо агент має виконувати промпт попередньої ноди, встановіть
   `prompt_source: "input"` і передайте значення у змінну `prompt`.
9. Для структурованого рішення `Task Reviewer` використовуйте JSON із полями
   `verdict`, `score`, `reason`, `must_fix` і, якщо потрібен файл,
   `candidate_path`.
10. Не додавайте паралельні гілки без необхідності: одна й та сама нода запускається
    після кожного вхідного пакета, тому різні входи можуть спричинити окремі
    проходи.

## Мінімальна структура файла

```json
{
  "format_version": 2,
  "name": "Назва Flow",
  "workspace": "C:/absolute/project/path",
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

- `source_port` — `out`, `true`, `false`, `next` або `done` залежно від ноди.
- `source_path` — що взяти з `NodeResult`: `$`, `text`, `data`,
  `data.improved_prompt`, `data.retry_context` тощо.
- `target_variable` — ім’я у `inputs` наступної ноди, наприклад `prompt`, `work`,
  `review`, `criteria`, `attachments` або `input`.
- `condition` — необов’язкова безпечна умова на кшталт
  `source.status == "success"`.
- `transform` — необов’язковий шаблон із `{{value}}`.
- `control_points` — візуальні точки вигину; на логіку вони не впливають.

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
  "tasks": [],
  "completed_count": 0,
  "task_count": 2
}
```

Вкладення активного завдання автоматично додаються до всіх агентів прямого
ланцюжка до `Result`. Через `Result` вони не переносяться до іншого циклу.

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
  "must_fix": []
}
```

Звичайні входи:

- результат виконавця `→ work`;
- початкове або покращене завдання `→ criteria`;
- за потреби структурований пакет `→ input`.

`sandbox: "read-only"` означає, що рев’ювер не змінює файли, але його текстовий
JSON-вердикт усе одно передається наступній ноді через Flow. Read-only обмежує
файлові операції агента, а не обмін даними між блоками.

### Result (`result`)

Призначення: прочитати вердикт `Task Reviewer`, вибрати `true` або `false`,
зберегти прийнятий результат і керувати лімітами циклу.

Параметри:

- `template` — текст фінального результату;
- `save_path` — необов’язковий файл для гілки `true`;
- `true_limit` — кількість дозволених проходів `true`;
- `false_limit` — кількість дозволених проходів `false`;
- `wait_for_confirmation` — пауза перед переходом для ручного огляду файлів.

При з’єднанні `Result.true → Tasks Manager` ефективний ліміт `true` автоматично
стає не меншим за кількість завдань. Це дає менеджеру завершити всю чергу навіть
тоді, коли у Parameters залишено значення `1`.

Типові маршрути:

- `false → Task Executor` для виправлення;
- `true → Tasks Manager` для наступного завдання;
- `true → фінальна нода` у звичайному одноразовому Flow.

### Work Reviewer (`work_reviewer`)

Призначення: записати Markdown-протокол проходів і після завершення Flow оцінити
якість роботи інших агентів.

Нода не має портів. Виберіть `monitor_all: true` або задайте `monitored_nodes`.
Звіт пишеться у `report_path` або у папку поточного запуску.

## Спільні параметри агентів

Ці поля застосовуються до `Prompt Reviewer`, `Task Executor`, `Task Reviewer` і
`Work Reviewer`:

- `model` — модель Codex;
- `reasoning_effort` — `none`, `low`, `medium`, `high`, `xhigh` або `max`;
- `instructions` — постійні інструкції, що додаються до кожного запиту ноди;
- `instruction_files` — MD-файли постійних інструкцій;
- `prompt` — шаблон запиту;
- `prompt_source` — `template` або `input`;
- `sandbox` — `read-only`, `workspace-write` або `full-access`;
- `workspace` — окрема робоча папка ноди;
- `additional_folders` — додаткові доступні папки;
- `output_format` — `text` або `json`;
- `output_schema` — схема очікуваної JSON-відповіді;
- `attachments` — постійні вкладення ноди;
- `timeout_seconds` — граничний час одного запиту;
- `retries` — повтори лише після технічної помилки, не після негативного рев’ю;
- `memory` — `thread` для продовження треду або `fresh` для нового треду.

Найменші необхідні права:

- `read-only` — аналіз і QA без зміни файлів;
- `workspace-write` — генерація та редагування у робочих папках;
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
Tasks Manager.next.data.prompt → Task Reviewer: criteria
Task Reviewer.data → Result: review
Result.false.data.retry_context → Task Executor: prompt
Result.true.data → Tasks Manager: input
```

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
- `false_limit` достатній для циклу виправлень, але не безмежний.
- Є лише один `Work Reviewer`, і він не має зв’язків.
- `Workflow.validate()` не повертає помилок.

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
6. Після редагування завантажте JSON через `Workflow.load()` і виконайте
   `validate()`. За наявності коду також запустіть тести проєкту.
