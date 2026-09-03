# План прискорення FlowAI та стабілізації якості повторних ітерацій

**Дата:** 2026-08-25  
**Статус:** реалізовано 2026-08-25  
**Проєкт:** `C:\Users\illia\Documents\DDA PF\FlowAI`  
**Цільовий Flow:** `!_projects/ai-flow-20260821-162833/ai-flow-20260821-162833.flowai.json`

## 1. Мета документа

Цей план описує виправлення очевидних системних проблем, через які Flow витрачає десятки хвилин на нескладну ітерацію, повторює вже виконану роботу, не підвищує оцінку QA, показує користувачу неоднозначний стан і може втрачати прогрес Tasks після STOP.

План є самодостатнім: для кожного пункту наведено проблему, її причину, потрібну зміну, алгоритм, тести та критерій готовності. Реалізація не повинна зменшувати якість QA, вводити часовий ліміт для Executor або видаляти наявні результати.

## 2. Підтверджені симптоми

1. У Task 2 оцінки QA змінювалися `82 → 86 → 1`, хоча остання відповідь мала `verdict=true` і порожній `must_fix`. Це не реальне падіння якості, а невалідна відповідь QA, яку рушій прийняв без перевірки.
2. Для E21 QA вже підтвердив правильність cutout, тіні, padding, registration і confinement, але повторна ітерація знову оптимізувала shadow mask замість виправлення clean plate.
3. Скрипт `tools/lock_e21_retry_shadow_mask.py` запускав до 12 000 важких ітерацій без ранньої зупинки та записував результат лише наприкінці.
4. Візуально активним був Executor. Попередня діагностика помилково назвала Optimizer через застарілий checkpoint, який не відображав поточну активну ноду.
5. Один Executor-thread використовувався між різними Tasks. Контекст виріс приблизно до 181 тисячі токенів, тому навіть проста операція читання могла тривати понад хвилину.
6. Prompt Reviewer повторно обробляв уже сформовані технічні завдання й отримував великий опис усього Flow.
7. QA щоразу повторював детерміновані перевірки SHA, manifest, статусів і структури файлів замість використання перевіреного пакета та кешу за hash.
8. Result TRUE, engine checkpoint і предметний `progress.json` існують як кілька джерел стану. Через це наступний Task може бачити застарілий статус і блокувати себе.
9. Старий Flow не має структурованих `issues/defect_id` і потрібного retry guard, тому той самий дефект із трохи іншими словами не розпізнається як повторення.
10. STOP перериває виконання, але активна нода вже може бути вилучена з черги; після очищення checkpoint Tasks не відновлюються коректно.
11. У папці однієї позиції накопичилися десятки кандидатів і retry-файлів без чіткого `current`-покажчика, що збільшує сканування та ризик вибрати застарілий артефакт.
12. `_generated_files` визначаються за шляхами, знайденими у вхідних даних, тому джерела, вкладення й навіть `SKILL.md` можуть помилково відображатися як створені результати.

## 3. Обмеження реалізації

- Не додавати жорсткий часовий ліміт для Executor. Керування має відбуватися через ліміт операцій, convergence і контроль прогресу.
- Не пропускати обов’язкову візуальну перевірку QA.
- Не дозволяти користувацький override технічних blocker-ів.
- Не видаляти та не перезаписувати старі артефакти.
- Не змінювати immutable source-файли завдання.
- Усі нові runtime-файли створювати лише всередині теки відповідного проєкту.
- Старі Flow мають продовжити відкриватися без обов’язкової ручної міграції.
- Поточний проблемний Flow потрібно мігрувати явно, щоб виправлення запрацювали одразу.

## 4. Цільова модель циклу

```text
Task contract
    ↓
Executor виконує лише failed checks
    ↓
Deterministic QA packet
    ↓
Visual QA
    ↓
Result
    ├─ TRUE  → атомарний transition receipt → наступний Task
    └─ FALSE → retry contract
                 ├─ новий дефект → цільовий retry
                 ├─ повтор №2 / regression → Pause · Attention
                 └─ системна помилка → Optimizer proposal, без запису файлів
```

Ключове правило: результат попередньої перевірки не є загальним текстовим коментарем. Він стає машинозчитуваним контрактом, який точно визначає, що дозволено змінювати, що вже пройшло перевірку і які hashes мають залишитися незмінними.

---

# Етап 0. Зафіксувати поточний стан і забезпечити відновлення

## 0.1. Створити незмінний діагностичний snapshot проблемного запуску

**Проблема, яку вирішує:** під час виправлення коду можна втратити точні докази помилок або помилково оцінити нову поведінку за вже зміненими файлами.

**Причина:** поточні журнали, checkpoint та артефакти продовжують змінюватися між запусками, а частина стану існує лише в останньому run log.

**Зміни:**

- Створити всередині теки проблемного проєкту `.flowai/runtime/diagnostics/<run-id>/`.
- Записати туди manifest із шляхами та SHA-256 поточного flow-файлу, run log, checkpoint, task state, `progress.json`, QA-відповідей і поточних E21-артефактів.
- Не копіювати великі файли без потреби: для незмінних артефактів достатньо path, size, mtime і SHA-256.
- Позначити snapshot як read-only логічно через manifest, без зміни прав користувацьких файлів.

**Файли реалізації:** новий модуль на зразок `flowai/diagnostics.py`, інтеграція в recovery/diagnostic command.

**Тести:**

- Snapshot містить усі обов’язкові посилання та hashes.
- Повторне створення не перезаписує попередній snapshot.
- Жоден файл не створюється поза workspace проєкту.

**Критерій готовності:** стан запуску `20260825-104146-478698` можна однозначно відтворити для тестів, не покладаючись на поточний UI.

## 0.2. Додати recovery активного Task зі старого run log

**Проблема, яку вирішує:** після STOP користувач бачить, що раніше виконані Tasks або активний Task втрачено, хоча файли залишилися на диску.

**Причина:** checkpoint міг бути очищений, а активна нода вже вилучена з черги перед збереженням.

**Зміни:**

- Додати команду/дію `Recover progress from last run`.
- Відновлювати завершені Tasks із підтверджених Result receipts.
- Відновлювати останній незавершений Task у стані `pending` або `paused`, а не позначати його виконаним.
- Не запускати відновлений Task автоматично; спочатку показувати користувачу summary.
- Зберігати recovery checkpoint у `.flowai/runtime/checkpoints/`.

**Алгоритм:**

1. Знайти останній run log поточного flow/project pair.
2. Програти події до останньої валідної завершеної ноди.
3. Перевірити Result receipts та hashes артефактів.
4. Побудувати task state.
5. Поставити перервану ноду назад у чергу з її останніми валідними inputs.

**Тести:** відновлення після STOP на Executor, QA, Result і між Tasks; пошкоджений run log не має перезаписувати чинний checkpoint.

**Критерій готовності:** проблемний проєкт відновлює Tasks 1–2 як завершені, а Task 3/E21 — як незавершений і готовий до продовження.

---

# Етап 1. Зробити QA-оцінку достовірною

## 1.1. Ввести строгий контракт відповіді Task Reviewer

**Проблема, яку вирішує:** QA може повернути `verdict=true` та `score=1`, а Flow приймає це як успіх. Через це користувач бачить абсурдне падіння оцінки й не розуміє, чи якість зросла.

**Причина:** поточна схема допускає довільне число `score`, не описує шкалу 0–100, а рушій перевіряє фактично лише boolean `verdict`.

**Зміни:**

- Описати `score` як integer `0..100`.
- Додати `pass_threshold`, стандартно 80, із можливістю конфігурації ноди.
- `verdict=true` дозволений лише коли:
  - `score >= pass_threshold`;
  - немає blocking issues;
  - `must_fix` порожній;
  - усі обов’язкові checks мають PASS.
- `verdict=false` повинен мати хоча б один issue або явну `system_error`.
- Невідповідну відповідь один раз відправляти тій самій QA-моделі на schema correction без повторного аналізу файлів.
- Якщо correction знову невалідний — не продовжувати Flow, перейти в `Pause · Attention` із причиною `invalid_qa_contract`.

**Цільовий формат:**

```json
{
  "verdict": false,
  "score": 72,
  "pass_threshold": 80,
  "issues": [],
  "must_fix": [],
  "checks": [],
  "evidence_files": [],
  "system_error": null
}
```

**Файли реалізації:** `flowai/models.py`, Task Reviewer execution/validation у `flowai/engine.py`, UI Result/Stats.

**Тести:**

- TRUE + score 1 відхиляється.
- TRUE + blocking issue відхиляється.
- FALSE без issues відхиляється.
- Score 80 із усіма PASS приймається.
- Другий невалідний результат переводить Flow у Attention, не в TRUE.

**Критерій готовності:** рушій фізично не може записати успішний QA verdict з оцінкою нижче порога.

## 1.2. Розділити оцінки за Task та спробами

**Проблема, яку вирішує:** оцінка іншого Task або старої спроби виглядає як поточна оцінка всього Flow; під час роботи Executor користувач очікує, що число вже мало змінитися.

**Причина:** UI показує останнє відоме число без чіткої прив’язки до task_id, attempt_id та моменту QA.

**Зміни:**

- Зберігати `task_id`, `attempt_id`, `qa_run_id`, `evaluated_artifact_hash` біля кожної оцінки.
- У header та Stats показувати: `Остання оцінка QA для Task E21: 45`.
- Поки Executor змінив артефакт, але QA ще його не перевірив, показувати `Очікує повторної перевірки`, а не старе число як актуальне.
- Історію будувати окремо для кожного Task: `45 → pending → 68`.
- Заборонити порівнювати score різних Tasks як ітерації одного результату.

**Тести:** Task 1 score 100 не потрапляє в графік Task 2; новий hash автоматично робить старий score stale.

**Критерій готовності:** користувач завжди бачить, який саме Task, attempt і hash отримав показану оцінку.

## 1.3. Додати структуровані QA issues зі стабільним `defect_id`

**Проблема, яку вирішує:** однаковий дефект, сформульований іншими словами, сприймається як новий, тому Flow може робити третю, четверту й наступні автоматичні спроби.

**Причина:** старий Flow повертає переважно `reason/must_fix`, а fallback ID обчислюється з повного тексту.

**Зміни:** кожний issue має містити:

```json
{
  "defect_id": "clean_plate.patch_seam.E21.lower_right",
  "category": "clean_plate",
  "severity": "blocker",
  "rule_id": "seam_visibility",
  "description": "Visible patch boundary",
  "target_files": [".../ai_clean_plate_registered.png"],
  "target_regions": [{"x": 420, "y": 310, "w": 180, "h": 140}],
  "fix_action": "Reconstruct paving continuation without touching accepted cutout/shadow",
  "evidence_files": [".../qa/crop-seam.png"]
}
```

- `defect_id` будується з нормалізованих category, rule_id, logical element/region, а не з prose.
- Опис може змінюватися без зміни identity дефекту.
- Для старих reviewer schemas рушій додає compatibility normalization.

**Тести:** два формулювання одного seam дають той самий ID; seam і ghost fragment мають різні ID.

**Критерій готовності:** повтор дефекту на другій послідовній перевірці гарантовано розпізнається незалежно від формулювання QA.

---

# Етап 2. Виправляти лише те, що не пройшло перевірку

## 2.1. Створити `retry_contract.json`

**Проблема, яку вирішує:** Executor витрачає час на тінь, яка вже пройшла QA, тоді як реальний дефект clean plate залишається без змін.

**Причина:** текстові рекомендації QA змішують контекст, passed checks і must-fix; агент сам вирішує, яку частину оптимізувати.

**Зміни:** після кожного FALSE рушій створює контракт у теці поточної спроби:

```json
{
  "task_id": "E21",
  "source_qa_run_id": "...",
  "failed_checks": ["clean_plate.patch_seam", "clean_plate.paving_continuity"],
  "protected_passed_checks": ["cutout", "shadow", "padding", "registration", "confinement"],
  "editable_files": ["ai_clean_plate_registered.png"],
  "editable_regions": [],
  "immutable_files": ["extracted_rgba.png", "shadow_rgba.png"],
  "immutable_hashes": {},
  "required_outputs": [],
  "acceptance_checks": []
}
```

- Executor отримує контракт як основне завдання retry.
- Файли protected PASS включаються з hash; зміна hash є regression до запуску QA.
- Якщо fix справді потребує зміни protected компонента, QA або користувач повинен явно зняти захист і пояснити залежність.

**Файли реалізації:** нова модель `RetryContract`, генерація після Task Reviewer, передавання Executor.

**Тести:** E21 retry дозволяє зміну clean plate і блокує зміну cutout/shadow; accepted hashes залишаються незмінними.

**Критерій готовності:** повторна ітерація E21 не запускає shadow-mask fitting і змінює лише файли/регіони, пов’язані з невдалим clean plate.

## 2.2. Додати objective guard перед запуском інструментів

**Проблема, яку вирішує:** навіть за правильного промпту агент може написати дорогий скрипт, оптимізаційна ціль якого не збігається з QA-дефектом.

**Причина:** зараз немає машинної перевірки зв’язку між командою/вихідним файлом і `failed_checks`.

**Зміни:**

- Перед виконанням локального скрипта Executor реєструє короткий `operation_intent`: target check, input files, output files, metric, max operations.
- Рушій відхиляє intent, якщо output входить до immutable/protected files або target check уже PASS.
- Для довгих оптимізаційних скриптів обов’язкові progress protocol та best-candidate checkpoint.
- Відхилений intent не рахується повною спробою; агент отримує точне пояснення порушення контракту.

**Тести:** intent для shadow при failed clean_plate відхиляється; intent для clean plate patch приймається.

**Критерій готовності:** дорога операція не може стартувати, якщо вона не спрямована на активний failed check.

## 2.3. Виявляти regression до повторного QA

**Проблема, яку вирішує:** Executor може погіршити вже прийняту частину, після чого QA витрачає повний цикл лише для виявлення очевидної регресії.

**Причина:** hashes passed-артефактів не перевіряються між спробами.

**Зміни:** після Executor порівнювати `immutable_hashes` і детерміновані checks із retry contract. При зміні:

- не запускати дорогий Visual QA;
- зберегти файли спроби;
- позначити `regression_detected`;
- на першому випадку повернути Executor точний diff;
- на повторному — `Pause · Attention`.

**Тести:** зміна одного байта accepted shadow виявляється; зміна дозволеного clean plate не блокується.

**Критерій готовності:** QA не витрачає час на повторну повну перевірку, якщо protected artifact уже порушений.

---

# Етап 3. Прибрати неконтрольовані тривалі обчислення без wall-clock timeout

## 3.1. Запровадити operation budget і convergence policy

**Проблема, яку вирішує:** нескладна задача працює 30–40 хвилин через 12 000 важких ітерацій без ранньої зупинки.

**Причина:** цикл має фіксовану велику кількість ітерацій, не зупиняється при відсутності покращення і не враховує, чи достатній результат уже отримано.

**Зміни:**

- Не вводити обмеження у хвилинах.
- Для ітеративної операції вимагати:
  - `max_iterations` або `max_evaluations`;
  - `target_metric`;
  - `acceptable_threshold`;
  - `no_improvement_patience`;
  - `min_delta`;
  - `checkpoint_every`;
  - `cancel_token`.
- При досягненні threshold завершуватися одразу.
- При відсутності meaningful improvement зупиняти поточний алгоритм і повертати best candidate.
- Якщо best candidate не проходить acceptance check, переходити в Attention із варіантами зміни методу, а не автоматично запускати той самий brute force.

**Тести:** synthetic optimizer з plateau припиняється після patience; target metric завершує цикл раніше max iterations; cancel зберігає best candidate.

**Критерій готовності:** жодна локальна оптимізація не може виконати тисячі однакових безрезультатних ітерацій без checkpoint та повідомлення про прогрес.

## 3.2. Записувати best candidate під час роботи

**Проблема, яку вирішує:** STOP після 30 хвилин втрачає найкращий результат у пам’яті, бо файл записується лише після завершення циклу.

**Причина:** output commit відкладено до останнього рядка скрипта.

**Зміни:**

- Кожні N ітерацій атомарно оновлювати `best/current.json` і новий versioned candidate.
- Записувати metric, iteration, hashes inputs, algorithm version.
- Не перезаписувати accepted output.
- При cancel завершувати дочірній процес і формувати `interrupted_operation.json` із останнім best candidate.

**Тести:** cancel у середині циклу залишає валідний PNG і manifest; частково записаний файл не стає current.

**Критерій готовності:** після STOP користувач не втрачає останній валідний кандидат і може продовжити з нього.

## 3.3. Показувати реальний прогрес активної операції

**Проблема, яку вирішує:** UI показує лише `Executor працює`, тому користувач не знає, чи є прогрес, який скрипт запущено і чому він триває довго.

**Причина:** дочірній процес не надсилає структурованих progress events.

**Зміни:** показувати node, task, operation, iteration/evaluations, best metric, last improvement, elapsed time та кнопку STOP. Якщо `no improvement` перевищив policy — показати `Pause · Attention`.

**Тести:** UI отримує monotonic progress events; stale checkpoint не замінює live active-node state.

**Критерій готовності:** за 10–20 секунд користувач бачить, що саме робить Executor і чи покращується результат.

---

# Етап 4. Усунути дублювання й розсинхронізацію прогресу Tasks

## 4.1. Зробити Result receipt єдиним авторитетним переходом

**Проблема, яку вирішує:** Result уже дав TRUE, але наступний Executor читає старий `progress.json` і вважає попередній крок незавершеним.

**Причина:** engine checkpoint, Result і доменні metadata оновлюються незалежно та неатомарно.

**Зміни:** після успішного Result створювати `TaskTransitionReceipt`:

```json
{
  "receipt_id": "...",
  "flow_id": "...",
  "project_id": "...",
  "task_id": "step-02",
  "result_node_id": "...",
  "verdict": true,
  "approved_artifact_hash": "...",
  "accepted_at": "...",
  "next_task_id": "step-03",
  "state_patch_hash": "..."
}
```

- Receipt записується атомарно у `.flowai/runtime/receipts/`.
- Queue advance, task status і checkpoint commit виконуються однією транзакцією.
- Для предметного `progress.json` використовується детермінований project transition adapter, а не агент.
- Якщо adapter не застосувався, Task не переходить далі та показує технічну помилку; TRUE не губиться.

**Тести:** crash на кожному кроці транзакції; повторне застосування receipt є idempotent; next Task бачить завершений previous Task.

**Критерій готовності:** після TRUE жодна нода не може побачити попередній Task як незавершений.

## 4.2. Заборонити агентам самостійно змінювати статус проходження

**Проблема, яку вирішує:** QA або Executor редагує `progress.json`, хоча це не його робота, і створює приховану розбіжність зі станом рушія.

**Причина:** предметні промпти компенсували відсутність engine-owned transition logic.

**Зміни:**

- QA лише повертає verdict/evidence.
- Executor лише створює task artifacts.
- Result підтверджує перехід.
- Engine/adapter оновлює status.
- Вилучити з нодових інструкцій вимоги вручну міняти `order_status`, `progress` або `cut_order`.
- Додати policy, що metadata status files недоступні для запису agent nodes.

**Тести:** QA/Executor attempt змінити state file блокується; adapter може змінити його після receipt.

**Критерій готовності:** існує один чіткий власник кожного класу даних, а read-only QA не виконує bookkeeping.

## 4.3. Додати reconciliation під час відкриття Flow

**Проблема, яку вирішує:** після аварійного закриття checkpoint і domain manifest можуть містити різні останні стани.

**Причина:** попередні версії не мали атомарної транзакції.

**Зміни:** на startup порівнювати receipts, checkpoint та domain state. Валідний receipt має пріоритет. Автоматично застосовувати лише безпечний idempotent patch; неоднозначність переводити в Attention із preview змін.

**Тести:** старий status + новий receipt; receipt без artifact; artifact hash mismatch.

**Критерій готовності:** перезапуск або закриття між Result і наступним Task не втрачає підтверджений прогрес.

---

# Етап 5. Реально гарантувати `read-only`

## 5.1. Закріпити `deny_all` для read-only агентів

**Проблема, яку вирішує:** нода з доступом `read-only` історично могла отримати auto-approval та створити/змінити файл.

**Причина:** sandbox і approval mode є різними механізмами; одного тексту `Файли не змінюй` недостатньо.

**Зміни:** зберегти й покрити regression-тестами поточне мапування `read-only → ApprovalMode.deny_all`. Writable режими повинні мати явне окреме мапування.

**Тести:** direct shell write, patch, redirect, запуск скрипта, який пише файл, — відхиляються для read-only.

**Критерій готовності:** жоден стандартний шлях SDK не дозволяє read-only ноді підтвердити запис.

## 5.2. Додати post-run mutation audit

**Проблема, яку вирішує:** запис може відбутися через зовнішній інструмент, MCP або неочікуваний побічний ефект, який sandbox не перехопив.

**Причина:** контроль лише на рівні SDK не охоплює всі capability providers.

**Зміни:**

- Перед read-only нодою зняти lightweight snapshot workspace: path, size, mtime, hash для змінених/підозрілих файлів.
- Після ноди зробити diff.
- Не рахувати системні read-only access timestamps.
- Якщо є mutation, позначити run як policy violation, зберегти diff і перейти в Attention.
- Не видаляти зміни автоматично; показати користувачу файли та запропонувати відновлення з versioned backup.

**Тести:** прихований запис через helper/MCP виявляється; звичайне читання не створює false positive.

**Критерій готовності:** навіть обхід основного sandbox стає видимим і не може тихо вплинути на Flow.

## 5.3. Розділити Optimizer proposal і застосування змін

**Проблема, яку вирішує:** Optimizer може виглядати як нода, яка сама редагує робочі артефакти або нодові інструкції.

**Причина:** structured recommendation і фактична filesystem mutation не розділені в UX та контракті.

**Зміни:** Optimizer повертає лише typed diff/proposal. FlowAI застосовує дозволені config-зміни лише після натискання `Застосувати правки`. Task artifacts Optimizer не редагує ніколи.

**Тести:** proposal не змінює flow JSON; apply створює backup і audit entry; reject не має side effects.

**Критерій готовності:** користувач чітко бачить різницю між порадою Optimizer і реально застосованою зміною.

---

# Етап 6. Обмежити контекст кожним Task

## 6.1. Додати memory mode `task_thread`

**Проблема, яку вирішує:** Executor накопичує контекст попередніх Tasks до 150–180 тисяч токенів, через що прості дії стають повільними й модель плутає старі вимоги з поточними.

**Причина:** `memory: thread` прив’язаний лише до node_id і повторно використовується для всього Flow.

**Зміни:**

- Новий ключ thread: `{flow_run_id}:{node_id}:{task_id}`.
- Retry того самого Task використовує той самий thread.
- Наступний Task отримує новий thread і компактний handoff: task contract, accepted receipts, relevant project profile.
- Thread IDs та compact handoff зберігаються в checkpoint.
- Старий `thread` режим лишається сумісним.

**Тести:** два Tasks не ділять history; retry зберігає task context; restore після restart відкриває правильний task thread.

**Критерій готовності:** контекст Executor не зростає лінійно з кількістю завершених Tasks.

## 6.2. Додати керовану compaction за структурованими даними

**Проблема, яку вирішує:** навіть один складний Task може накопичити великий контекст із повторними логами та описами файлів.

**Причина:** у thread повторно передаються повні QA reports, manifests та списки файлів.

**Зміни:**

- Ввести конфігурований soft threshold контексту, наприклад 80k tokens.
- На threshold створювати structured summary, а не обрізати випадкові повідомлення.
- Завжди зберігати поточні user corrections, task contract, retry contract, receipts і file hashes.
- Великі логи передавати шляхом + релевантним excerpt, не повним текстом.

**Тести:** після compaction пріоритет користувацьких правок не втрачається; old irrelevant Task data відсутні.

**Критерій готовності:** типова retry-ітерація отримує компактний контекст і не перечитує всю історію Flow.

---

# Етап 7. Не повторювати зайвий Prompt Reviewer

## 7.1. Кешувати підготовлений prompt за task contract hash

**Проблема, яку вирішує:** Prompt Reviewer витрачає 1–2,5 хвилини перед кожною спробою, хоча завдання вже точне й не змінилося.

**Причина:** Reviewer запускається за структурою графа, а не за фактом зміни task requirements.

**Зміни:**

- Обчислювати hash нормалізованого task contract + user corrections + relevant profile version.
- Зберігати improved prompt у `.flowai/runtime/prompt-cache/`.
- На retry використовувати cache, додаючи лише retry contract.
- Для `contract_ready=true` пропускати Reviewer повністю.
- Інвалідувати кеш лише після зміни вимог, references, profile або користувацьких правок.

**Тести:** незмінний retry не запускає модель Reviewer; зміна user note інвалідує кеш.

**Критерій готовності:** системний retry не витрачає повторно хвилини на перефразування того самого prompt.

## 7.2. Прибрати повний опис Flow із prompt-review context

**Проблема, яку вирішує:** Reviewer отримує десятки тисяч зайвих токенів і може змішувати правила нод із суттю Task.

**Причина:** контекст формується як verbose dump усього ланцюжка.

**Зміни:** передавати capabilities summary, task contract, relevant skill refs і output schema. Повний flow JSON доступний лише як файл для діагностики, а не як обов’язковий prompt.

**Тести:** reviewed prompt містить усі task requirements, але не дублює інструкції інших нод.

**Критерій готовності:** вхід Reviewer суттєво менший без втрати обов’язкових вимог.

---

# Етап 8. Розділити детермінований і візуальний QA

## 8.1. Створити deterministic QA packet

**Проблема, яку вирішує:** QA-модель витрачає хвилини й десятки тисяч токенів на SHA, manifest, dimensions, file existence та статуси, які швидше й надійніше перевіряються кодом.

**Причина:** монолітний QA сам пише/запускає допоміжні скрипти та повторно сканує всю папку.

**Зміни:** перед Visual QA рушій формує `qa_packet.json` із:

- file existence, size, dimensions, mode/profile;
- hashes і provenance;
- manifest consistency;
- required filenames/states;
- protected artifact comparisons;
- validator outputs;
- current-only evidence paths;
- targeted crops для failed regions.

QA-модель аналізує візуальні та семантичні проблеми, але не повторює машинні checks.

**Тести:** packet deterministically відтворюється; missing file стає blocker до model call; read-only QA не створює helper scripts.

**Критерій готовності:** метадані перевіряються локально за секунди, а модель витрачає час лише на те, що потребує зору й судження.

## 8.2. Кешувати PASS checks за hashes inputs і версією validator

**Проблема, яку вирішує:** незмінна тінь, cutout чи manifest перевіряються з нуля на кожному retry.

**Причина:** QA не має адресного кешу результатів.

**Зміни:** cache key = check_id + input hashes + validator version + relevant contract hash. Візуальні checks можна повторно використати лише коли всі pixels/evidence та acceptance rule незмінні. Зміна будь-якого input інвалідує check.

**Тести:** незмінний shadow reuse PASS; змінений hash змушує повторну перевірку; оновлення validator invalidates cache.

**Критерій готовності:** retry clean plate не повторює технічні та візуальні checks незмінних accepted компонентів.

## 8.3. Залишити повну фінальну перевірку перед trusted receipt

**Проблема, яку вирішує:** агресивне кешування могло б пропустити інтеграційну помилку фінального композита.

**Причина:** окремі компоненти можуть бути правильними, але їх композиція — ні.

**Зміни:** перед фінальним TRUE обов’язково перевірити поточний composite/after-map, required overview і відповідність acceptance contract. Кеш компонентів не замінює фінальну інтеграційну перевірку.

**Тести:** accepted cutout + accepted clean plate, але неправильна композиція, дає FALSE.

**Критерій готовності:** прискорення QA не знижує контроль фінального візуального результату.

---

# Етап 9. Зупиняти повторювані дефекти на другій появі

## 9.1. Увімкнути retry guard у поточному Flow і додати safe schema upgrade

**Проблема, яку вирішує:** користувач уже четвертий раз бачить той самий дефект, хоча очікує Attention після двох повторів.

**Причина:** у фактичному старому flow-файлі немає потрібних retry guard fields і structured issues.

**Зміни:**

- Мігрувати `ai-flow-20260821-162833.flowai.json`:
  - `retry_guard_enabled: true`;
  - `same_defect_threshold: 2`;
  - `regression_threshold: 1` або 2 відповідно до severity policy;
  - structured reviewer output schema;
  - confirmation/attention routing.
- Під час завантаження старого Flow додавати нові поля in-memory з backward-compatible defaults.
- Не перезаписувати користувацькі інструкції під час автоматичного upgrade.

**Тести:** old flow loads; current flow explicitly has guard; same normalized defect twice produces Attention before third automatic retry.

**Критерій готовності:** третя автоматична спроба того самого дефекту без участі користувача неможлива.

## 9.2. Виправити поріг запуску Optimizer

**Проблема, яку вирішує:** Optimizer запускається після першого звичайного FALSE, додає вузькі prompt patches і збільшує контекст замість того, щоб дати Executor виконати конкретний must-fix.

**Причина:** у поточній конфігурації `false_threshold: 1`.

**Зміни:**

- Перший FALSE із чітким artifact defect → прямий targeted retry.
- Другий той самий defect або regression → Attention.
- Optimizer запускається для системної причини: неправильний prompt strategy, schema, routing чи engine contract.
- Optimizer класифікує root cause як `artifact | agent_strategy | engine_state | tool_failure`.
- Engine/state bugs не маскуються новими постійними інструкціями Executor.

**Тести:** перший seam не запускає Optimizer; повтор seam викликає Attention; invalid routing створює optimizer proposal.

**Критерій готовності:** Optimizer не втручається в рутинне виправлення зображення і не вирощує prompt без системної причини.

---

# Етап 10. Упорядкувати артефакти й вибір current candidate

## 10.1. Перейти на versioned attempts

**Проблема, яку вирішує:** десятки raw/retry/candidate файлів лежать разом, QA довго сканує папку й може вибрати неактуальний файл.

**Причина:** відсутні межі attempt та єдиний current pointer.

**Цільова структура:**

```text
position_01_E21/
  accepted/
  attempts/
    attempt-001/
      artifacts/
      qa/
      retry_contract.json
      manifest.json
    attempt-002/
  current.json
  task_manifest.json
```

**Зміни:** кожна спроба має immutable manifest. `current.json` містить attempt_id і hashes канонічних файлів. Accepted files не перезаписуються. Старі спроби архівуються логічно, але не видаляються.

**Тести:** QA читає лише current + protected accepted; stale filename не може стати current без manifest update.

**Критерій готовності:** для будь-якого Task однозначно відомо, які файли є поточною спробою і які вже прийняті.

## 10.2. Мігрувати наявну E21-папку без вгадування

**Проблема, яку вирішує:** автоматичне сортування старих 119 файлів лише за назвою може неправильно призначити accepted/current outputs.

**Причина:** старі naming conventions непослідовні.

**Зміни:** будувати migration manifest із run events, mtimes, recorded paths і hashes. При неоднозначності залишити файл у `legacy/` і попросити підтвердження, не робити його current автоматично.

**Тести:** dry-run migration; повторний запуск idempotent; жоден старий файл не видаляється.

**Критерій готовності:** E21 має чіткий current attempt, а історичні кандидати залишаються доступними.

---

# Етап 11. Правильно визначати створені файли

## 11.1. Замінити `_existing_input_files` на file-change ledger

**Проблема, яку вирішує:** UI та наступні ноди отримують source, attachments і skills як нібито `_generated_files`, через що аналізують зайве та можуть вибрати неправильний результат.

**Причина:** рушій рекурсивно знаходить будь-який існуючий path у вхідних даних і зараховує його до generated output.

**Зміни:**

- Перед writable нодою створювати workspace snapshot/ledger.
- Після ноди класифікувати:
  - `attached_files`;
  - `read_files`;
  - `generated_files`;
  - `modified_files`;
  - `deleted_files`.
- Generated/modified можуть бути лише в project workspace та повинні мати provenance node_id/attempt_id.
- Read-only нода не має generated/modified files.
- Явні agent artifact events мають пріоритет над пошуком рядків у відповіді.

**Тести:** `SKILL.md` і source PNG не стають generated; новий output PNG стає generated; modified manifest класифікується окремо.

**Критерій готовності:** Result і QA бачать лише фактично створені або змінені поточною нодою артефакти.

---

# Етап 12. Зробити STOP відновлюваним, а видалення прогресу — окремою дією

## 12.1. Реалізувати resumable STOP

**Проблема, яку вирішує:** користувач натискає STOP, щоб припинити довгу операцію, але ризикує втратити Tasks progress і не може продовжити після перезапуску.

**Причина:** cancel і discard run змішані; активна нода не завжди повертається до queue перед checkpoint.

**Зміни:**

- STOP завжди активний.
- STOP:
  1. скасовує активну команду;
  2. чекає bounded graceful shutdown лише службового процесу;
  3. зберігає best candidate;
  4. повертає активну ноду та inputs до queue;
  5. зберігає checkpoint зі станом `stopped_resumable`.
- Окрема дія `Discard run progress` із підтвердженням очищує checkpoint, але не project artifacts.
- Закриття програми під час активного Flow використовує той самий resumable stop protocol.

**Тести:** STOP на Python child process, model call, Result Attention; restart відновлює активну ноду; Discard не видаляє artifacts.

**Критерій готовності:** після STOP і повторного відкриття Flow користувач продовжує з того самого Task, а не починає Tasks спочатку.

## 12.2. Зберігати checkpoint на кожній межі стану

**Проблема, яку вирішує:** UI та діагностика можуть показувати стару активну ноду, наприклад Optimizer замість Executor.

**Причина:** checkpoint оновлюється не після кожної зміни active node/queue/activity.

**Зміни:** checkpoint після node start, node finish, queue mutation, Result draft, GrillMe result, Pause, Attention і STOP. Додати `checkpoint_version`, `saved_at`, `active_node_id`, `active_operation_id`, `event_cursor`.

**Тести:** crash після node start; UI відрізняє live state від stale checkpoint; diagnostics вибирає подієвий timeline.

**Критерій готовності:** активна нода в UI, run log і checkpoint збігається або UI явно позначає checkpoint як застарілий.

---

# Етап 13. Покращити UX діагностики без приховування проблем

## 13.1. Показувати `чому зараз працює` і `що має змінитися`

**Проблема, яку вирішує:** користувач бачить довгу активність Executor, але не знає, що QA відхилив, які checks уже PASS і яка ціль поточної спроби.

**Причина:** retry contract і operation intent не виводяться в UI.

**Зміни:** у панелі активного Task показувати:

- QA score/status для поточного artifact hash;
- failed checks;
- protected PASS checks;
- файли, які зараз дозволено змінювати;
- активну операцію і progress;
- останнє meaningful improvement;
- причину Pause/Attention.

**Тести:** E21 UI показує `Fix clean plate seam/paving continuity`, а не загальне `Executor працює`.

**Критерій готовності:** користувач може без читання консолі зрозуміти, чи Flow робить правильну роботу.

## 13.2. Додати пояснення переходу оцінки

**Проблема, яку вирішує:** число змінилося, але незрозуміло — через які checks, інший Task чи помилку QA.

**Причина:** score показується без diff.

**Зміни:** після QA показувати `score delta explanation`: fixed issues, new issues, regressions, unchanged blockers. Якщо це перша оцінка нового Task — писати `нова шкала для іншого Task`, без delta до попереднього Task.

**Тести:** Task switch не показує `-55`; retry того самого artifact показує конкретний issue diff.

**Критерій готовності:** кожне підвищення або зниження оцінки має машинно підтверджене пояснення.

---

# Етап 14. Міграція проблемного Flow

## 14.1. Оновити Task Reviewer ноди

**Проблема, яку вирішує:** загальні зміни рушія не допоможуть уже створеному Flow, якщо він продовжить вимагати старий schema `{verdict, score, candidate_path, reason, must_fix}`.

**Зміни:** додати structured issues/checks/evidence, pass threshold, retry contract generation і stable defect policy до фактичних QA nodes. Зберегти зрозумілі користувацькі інструкції та task-specific acceptance rules.

**Критерій готовності:** поточний Flow повертає валідний новий QA contract без ручного редагування користувачем.

## 14.2. Оновити Executor та Result routing

**Проблема, яку вирішує:** Executor може не отримати targeted retry, а Result TRUE — не створити trusted transition receipt.

**Зміни:** Executor читає `retry_contract`; Result створює receipt; FALSE повертається прямо до Executor для першого цільового retry; повтор defect і regression йдуть в Attention.

**Критерій готовності:** E21 проходить повний маршрут без ручної правки `progress.json` і без повторного аналізу shadow.

## 14.3. Оновити Optimizer policy

**Проблема, яку вирішує:** вузькі task-specific постійні інструкції накопичуються й не усувають системну причину в наступному Task.

**Зміни:** Optimizer отримує compact current-task evidence, root-cause categories та повертає proposal. Не передавати йому весь `work-review.md` на сотні кілобайт, якщо релевантна лише остання спроба.

**Критерій готовності:** Optimizer пропонує engine/config fix для state bug, а не наказує кожному майбутньому Executor вручну ремонтувати status.

---

# Етап 15. Порядок реалізації

1. **Baseline і recovery** — пункти 0.1–0.2.  
   **Проблема, яку вирішує:** захищає поточний прогрес та дає regression fixture до змін.
2. **QA contract і stable defects** — 1.1–1.3.  
   **Проблема, яку вирішує:** прибирає неправдиві оцінки та нескінченні повтори одного дефекту.
3. **Retry contract і protected PASS** — 2.1–2.3.  
   **Проблема, яку вирішує:** спрямовує роботу саме на реальний blocker.
4. **Engine-owned transitions і read-only enforcement** — 4.1–5.3.  
   **Проблема, яку вирішує:** усуває stale progress та роботу нод поза своєю роллю.
5. **Cancelable convergence loop** — 3.1–3.3.  
   **Проблема, яку вирішує:** забирає безконтрольні 30–40-хвилинні brute-force цикли без введення time limit.
6. **Task-scoped memory і prompt cache** — 6.1–7.2.  
   **Проблема, яку вирішує:** скорочує model latency і контекстне змішування Tasks.
7. **Deterministic QA та cache** — 8.1–8.3.  
   **Проблема, яку вирішує:** не витрачає дорогий QA на повторювані машинні перевірки.
8. **Attempts, file ledger і resumable STOP** — 10.1–12.2.  
   **Проблема, яку вирішує:** упорядковує файли, показує правильні результати й зберігає прогрес.
9. **UX і міграція конкретного Flow** — 13.1–14.3.  
   **Проблема, яку вирішує:** робить виправлення видимими користувачу та активує їх у реальному Flow.

Кожний етап має завершуватися окремими targeted tests. Не слід чекати завершення всіх етапів, щоб увімкнути критичні correctness fixes.

## 16. Набір обов’язкових regression-тестів

1. `qa_true_score_1_is_rejected` — вирішує проблему невалідної оцінки.
2. `qa_score_is_scoped_to_task_and_hash` — вирішує змішування оцінок Tasks.
3. `same_defect_different_wording_pauses_after_second` — вирішує нескінченні повтори.
4. `e21_retry_changes_clean_plate_only` — вирішує роботу над уже PASS shadow.
5. `protected_hash_regression_blocks_visual_qa` — вирішує повторну дорогу перевірку очевидної регресії.
6. `optimizer_loop_early_stops_and_saves_best` — вирішує 12 000 безрезультатних ітерацій.
7. `result_true_atomically_advances_project_state` — вирішує stale `progress.json`.
8. `read_only_node_cannot_write_via_sdk_script_or_mcp` — вирішує порушення ролі QA/Optimizer.
9. `task_thread_does_not_leak_previous_task_context` — вирішує context explosion.
10. `prompt_reviewer_cache_reused_on_retry` — вирішує повторне перефразування prompt.
11. `unchanged_qa_checks_are_cached_by_hash` — вирішує повторні metadata checks.
12. `source_and_skill_paths_are_not_generated_files` — вирішує неправильний список результатів.
13. `stop_requeues_active_node_and_restores_tasks` — вирішує втрату прогресу.
14. `checkpoint_active_node_matches_event_timeline` — вирішує помилкове визначення Optimizer замість Executor.
15. `legacy_flow_loads_with_safe_defaults` — захищає backward compatibility.
16. `current_problem_flow_migrates_with_retry_guard` — гарантує, що зміни працюють не лише для нових Flow.

## 17. Загальні критерії приймання

- QA не може успішно завершити Task з `score < pass_threshold` або blocking issues.
- UI ніколи не показує score іншого Task як падіння поточного.
- Після зміни artifact hash стара оцінка позначена як stale/pending.
- Retry E21 працює над clean plate; hashes already-passed cutout і shadow не змінюються.
- Повтор одного `defect_id` вдруге переводить Flow у `Pause · Attention`; третьої автоматичної спроби немає.
- Executor не має wall-clock timeout, але кожна ітеративна операція має budget, convergence, progress і checkpoint best candidate.
- Result TRUE атомарно оновлює task state; наступний Executor не блокується на застарілому `progress.json`.
- QA та інші read-only ноди не можуть редагувати файли через SDK, shell, helper або MCP; несподівана mutation виявляється audit-ом.
- Executor memory ізольована за Task; retry зберігає потрібний контекст без історії інших Tasks.
- Prompt Reviewer не викликається повторно для незмінного task contract.
- QA повторно використовує незмінні PASS checks за hash, але виконує фінальну інтеграційну візуальну перевірку.
- Result показує лише фактично generated/modified artifacts, а не sources, references чи skills.
- STOP доступний завжди, перериває активний процес, зберігає best candidate та resumable checkpoint.
- Після перезапуску завершені Tasks і поточний Task відновлюються.
- Активна нода в UI відповідає live event timeline; stale checkpoint явно позначений.
- Усі runtime, manifests, cache, attempts, receipts і diagnostics лежать усередині project workspace.
- Старі Flow відкриваються без руйнування конфігурації; проблемний Flow мігрований і проходить regression suite.

## 18. Очікуваний ефект

Після реалізації швидкість зросте не через штучне скорочення часу або погіршення моделі, а через усунення зайвої роботи:

- Executor не оптимізує вже прийняті компоненти;
- безрезультатний локальний алгоритм завершується за convergence policy;
- Prompt Reviewer не повторює незмінне завдання;
- QA не перераховує незмінні checks;
- кожний Task має чистий контекст;
- прогрес не губиться між STOP, закриттям і повторним відкриттям;
- користувач бачить достовірну оцінку тільки після фактичного QA.

Головний показник успіху — не довільне скорочення кількості хвилин, а відсутність повторної роботи без зміни failed check та стабільне зростання якості саме того артефакту, який QA позначив як проблемний.

## 19. Звіт про реалізацію

План реалізовано в рушії, UI та цільовому Flow. Ключові точки:

- строгий QA contract, task/hash-scoped score, stable `defect_id`, retry contract,
  protected hashes і regression guard реалізовані у `flowai/quality_control.py`
  та `flowai/engine.py`;
- iterative Python-команда в цільовому retry вимагає валідний
  `operation_intent`; прогрес `iteration/max/best` надходить у live UI;
- E21-скрипт більше не має 12 000 безумовних ітерацій: є operation budget,
  early-stop, patience, проміжний atomic best і versioned `current.json`;
- Result receipt атомарно застосовує declarative state patch. Reconciliation
  повторно програє receipt ідемпотентно; QA/Optimizer не володіють progress;
- read-only використовує `deny_all` і post-run mutation audit, який ігнорує
  лише власні runtime/checkpoint-файли рушія;
- додано task-thread, content-addressed prompt/QA cache, deterministic QA packet,
  file-change ledger, versioned attempts, resumable STOP і окремий Discard;
- checkpoint пишеться атомарно унікальним temp-файлом на кожній межі стану;
  додано UI-дію відновлення з event log та diagnostic snapshots;
- старі Flow отримують compatibility schema без примусового strict mode;
  цільовий Flow мігрований явно.

Реальний запуск `20260825-104146-478698` відновлено: перші два Tasks завершені,
активний Task 3/E21 і Executor повернуті в чергу. Збережено:

```text
!_projects/ai-flow-20260821-162833/
  runs/20260825-104146-478698/flowai-checkpoint.json
  .flowai/runtime/checkpoints/recovered-20260825-104146-478698.json
  .flowai/runtime/diagnostics/20260825-153631-692413/manifest.json
  .flowai/runtime/attempts/c7954580af88446a914de90dc8e7c15b/legacy-index.json
  artifacts/sequential_extraction_all/position_01_E21/attempts/legacy-index.json
```

Legacy index охоплює 119 файлів E21 (6 current, 5 protected snapshots,
63 legacy candidates); жоден старий файл не переміщено й не видалено.

Regression suite містить окремі тести з §16, включно з recovery тієї самої
Executor-ноди після її попередніх успішних проходів. На момент завершення всі
тести та Ruff проходять.
