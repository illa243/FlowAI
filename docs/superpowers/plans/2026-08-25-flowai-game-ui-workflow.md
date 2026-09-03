# FlowAI: Flow для ігрових UI-задач

Дата специфікації: 2026-08-25  
Статус: реалізовано; reference cache і resumable STOP/checkpoint додано 2026-08-25.  
Мобільна ревізія 2026-08-25 (portrait-only, Photoshop-рендер із першого кроку, ui_kit.json, layout manifest, art-placeholder) — реалізовано того ж дня: шаблон і engine оновлені за §10, тести в `tests/test_mobile_revision.py`.

## 1. Мета

Реалізувати в FlowAI спеціалізований, але побудований на наявних типах нод Flow для створення нових ігрових UI-екранів, restyle та точкової модифікації UI. Flow має перетворити одноразовий запит користувача на погоджений список завдань, створити чотири PNG-концепти, дати користувачу вибрати або поєднати їх, побудувати справжній редагований PSD у Photoshop 2022, провести технічний QA та зберегти накопичені знання.

Цільова область — **портретний казуальний мобільний UI**, той самий, що описаний у skill `modern-ui`: ілюстративний 2.5D, а не пласкі web-екрани. Це не одна з можливих платформ, а єдина: бібліотека з 66 референсів, критерії QA і словник станів побудовані саме під неї.

Єдиний рендерер на всіх етапах — **Photoshop 2022 через COM/JSX**. Concept Executor не «малює картинку» абстрактним інструментом: він будує шаровий документ тим самим містком, що описаний у §6, і експортує з нього PNG. Через це PSD Builder не перемальовує затверджений екран, а доводить уже наявний документ до вимог §6, а вся геометрія відома числом і потрапляє в layout manifest.

Усі створені файли, включно зі службовими, мають залишатися всередині теки відповідного проєкту. Перезапуск FlowAI, закриття діалогів або GrillMe не повинні втрачати прогрес, вибір чи введені правки.

### 1.1. Поза межами

Названо явно, щоб QA не вимагав того, чого Flow не обіцяє, а користувач не чекав:

- атлас, спрайт-пакінг, масштаби @2x/@3x і рушійні метадані — це пайплайн проєкту, а не дизайну;
- локалізовані рендери (див. §2.2 — обмеження на запас тексту лишається, самі переклади ні);
- анімація й motion design: PSD не носій руху, тому Flow його не описує й не оцінює;
- планшети та ландшафтна орієнтація;
- генерація ілюстративного арту (персонажі, пропси, мальовані фони) — див. правило placeholder у §2.2.

## 2. Вимоги користувача

- Початковий промпт вводиться один раз і використовується для побудови завдань.
- UI Planner створює Tasks відносно промпту, вкладених референсів, локального профілю та глобального skill `modern-ui` (канонічне hyphen-case ім'я для запланованого `modern_ui`).
- До запуску Tasks користувач бачить план, припущення та може відредагувати або повернути його Planner.
- Погоджений план заморожується у checkpoint і не перебудовується на retry або після перезапуску.
- Одне завдання Tasks Manager відповідає одному логічному UI-екрану з усіма застосовними станами.
- Для нового UI-сетапу першим завданням створюється UI kit. Для restyle UI kit виводиться з наданих референсів. У обох випадках цей task віддає `ui_kit.json` із токенами стилю; кожен наступний screen task несе `ui_kit_ref` і не має права від них відходити.
- Concept Executor створює рівно чотири варіанти V01–V04. Кожен варіант — це два PNG: `V0N.png` — екран на повний canvas в основному стані, і `V0N_board.png` — борд із прев'ю решти застосовних станів. Затверджується, хешується й потрапляє в `approved_artifact_hash` саме `V0N.png`; борд існує для вибору варіанта та для QA станів.
- Поруч із кожним варіантом Executor кладе `layout_manifest.json` із точною геометрією: bbox кожного інтерактивного елемента, його роль, anchor, набір станів, окремі `tap_box` і `text_box`, а для масштабованих панелей — 9-slice margins. Photoshop знає ці числа точно, тож манифест не оцінка, а витяг.
- Ілюстративні ділянки (персонажі, пропси, мальовані фони, фактури) створюються як явно позначені art-placeholder шари в правильному боксі плюс запис у `art_requests.json` із брифом на кожну. Якщо в `references/art/` уже лежить готовий асет — вставляється він. QA не має права валити варіант за «немає намальованого персонажа»: тільки за відсутній, неправильно розміщений або неописаний placeholder.
- Користувач може вибрати один, декілька або жодного варіанта.
- Якщо вибрано декілька варіантів, користувач описує, що взяти з кожного; Executor створює один Synthesis-варіант за тим самим контрактом, що й концепти (екран, борд, layout manifest, шаровий документ), який окремо проходить QA і підтвердження. Стадія PSD Builder — доведення документа до вимог §6 — не починається до затвердження Synthesis PNG.
- Якщо жоден варіант не підходить, користувач задає правки й запускається новий PNG-раунд.
- Після затвердження одного PNG створюється один справжній редагований PSD на екран через Photoshop 2022. Builder не перемальовує екран з нуля: він відкриває той самий шаровий документ, з якого експортовано затверджений `V0N.png`, і доводить його до вимог §6.
- PSD спочатку перевіряє QA, після чого результат обов'язково підтверджує користувач.
- Builder може домальовувати приховані ділянки, наприклад фон під винесеною на окремий шар кнопкою. Видимий композит при ввімкнених шарах не повинен відрізнятися від затвердженого `V0N.png`. Борд у цьому порівнянні не бере участі — інакше вимога була б невиконанною за побудовою.
- Часового ліміту Executor немає. Flow переходить у `Pause · Attention`, коли той самий blocking-дефект приходить від QA удруге поспіль або коли повертається дефект, якого минулого разу вже не було.
- STOP доступний під час роботи, Pause та Attention. Перше натискання перериває
  активний turn, повертає ноду разом з inputs у чергу і зберігає
  `stopped_resumable` checkpoint; versioned best локальної операції лишається на
  диску. Повторне натискання примусово закриває транспорт, але також не видаляє
  checkpoint або Tasks progress. Видалення прогресу — окрема підтверджувана дія.
- QA і користувацькі рев'ю автоматично оновлюють локальні знання проєкту.
- Глобальний `modern-ui` змінюється лише після окремого підтвердження користувача; зміна бібліотеки референсів також не переписує аналіз автоматично.

### 2.1. Затверджена бібліотека UI-референсів

Фактичне джерело на диску: `C:\Users\illia\Desktop\UI_refs`. Шлях із початкового повідомлення `C:\Users\illia\Desktop\UI\_refs` не існував, тому Flow використовує знайдену сусідню теку `UI_refs` і не створює другу копію бібліотеки.

У бібліотеці один раз проаналізовано 66 зображень у шести сім'ях: Clash Royale, Flambe, Merge Restyle, Pop Flow Odyssey, Royal Match і Tasty Travel. Результат збережено в:

```text
C:\Users\illia\.codex\skills\modern-ui\
  SKILL.md
  references\ui-reference-analysis.md
  references\reference-manifest.json
  references\contact-sheets\
  scripts\build_reference_cache.py
```

SHA-256 поточної бібліотеки: `efb174469a315311007dda9aea6d8ee12c7bd8566ca0debbd1bf11178e10b6b4`.

Стиль нових макетів має рівнятися на записаний аналіз. Planner вибирає одну основну reference family і щонайбільше дві допоміжні риси **один раз на весь проєкт** і записує їх у `ui_project_spec.art_direction`; усі tasks їх успадковують. Вибір per-task дав би магазин у стилі Clash Royale і level complete у стилі Tasty Travel — набір гарних картинок замість UI однієї гри. Executor та QA отримують у контексті `reference_analysis_receipt` зі шляхом до готового аналізу, кількістю файлів і хешем бібліотеки, а skill `modern-ui` зобов'язує прочитати цей файл перед будь-яким рішенням про стиль. Сам текст аналізу в промпт не вклеюється, щоб не роздувати контекст щоходу. Вони не запускають повторний повний аналіз 66 картинок і не змішують усі сім'ї в один випадковий стиль. Окремі оригінали можна відкрити лише точково, коли поточному task потрібне конкретне візуальне порівняння.

Кожна style-aware agent node має `reference_cache`:

```json
{
  "mode": "sha256_once",
  "source_dir": "C:\\Users\\illia\\Desktop\\UI_refs",
  "manifest_path": "C:\\Users\\illia\\.codex\\skills\\modern-ui\\references\\reference-manifest.json",
  "analysis_path": "C:\\Users\\illia\\.codex\\skills\\modern-ui\\references\\ui-reference-analysis.md",
  "library_sha256": "efb174469a315311007dda9aea6d8ee12c7bd8566ca0debbd1bf11178e10b6b4"
}
```

FlowAI перевіряє файл-рівень SHA-256 один раз на runner і повторно використовує receipt для всіх нод із тим самим кешем. Це технічна перевірка актуальності, а не повторний AI-аналіз. Якщо додано, видалено або змінено хоч один референс, Flow не працює зі старим аналізом: він переходить у `Pause · Attention` з типом `reference_analysis_attention` і вимагає контрольованого одноразового оновлення manifest та analysis.

Тека `UI_refs` і глобальний skill є джерелами тільки для читання. Усі результати конкретного запуску, локальні знання, тимчасові матеріали та артефакти й надалі створюються виключно всередині project workspace.

### 2.2. Мобільні обмеження

Це та частина, яку неможливо додати після затвердження PSD: якщо концепт намальовано без запасу, ніякий downstream-крок його не врятує. Усі числа — дефолти, записані тут; Planner може перевизначити їх із brief, але мусить винести зміну в `assumptions`.

**Canvas.** Дизайн-роздільність `1080×1920`. Це найвужчий випадок (9:16), і саме тому вона базова: вищі пристрої додають вертикального місця, а не забирають його. Макет мусить пережити діапазон `9:16 … 9:21`, тому кожен елемент має записаний `anchor` — до верху, низу, центру чи країв, — а не «лежить там, де його поклали».

**Safe area.** Резервні смуги від країв дизайн-канви: `top 132`, `bottom 100`, `left 0`, `right 0` px. Верхня — під виріз/Dynamic Island, нижня — під home indicator. Критичні контроли (close, back, головна CTA, лічильники валюти) в ці смуги не заходять. Декор — може.

**Тач-таргети.** Мінімум `144×144` px. Число не магічне: 1080 px дизайн-ширини на 360 dp логічної ширини дає рівно 3 px на dp, а `48 dp × 3 = 144`. Абсолютна нижня межа — 132 px (44 pt). Мінімальний проміжок між сусідніми тач-таргетами — 24 px. Перевіряється числом за `tap_box` із layout manifest, а не оком.

**Стани.** Фіксований словник, узгоджений зі skill `modern-ui`: `default`, `pressed`, `disabled`, `selected`, `locked`, `claimed`, `available`, `insufficient_currency`, `loading`, `empty`, `error`. Курсорні стани (`hover`, `focus`, `mouse-over`) заборонені — на тачі їх не існує. Обов'язковість не «на розсуд», а виводиться з ролі елемента в layout manifest:

| Роль елемента | Обов'язкові стани |
|---|---|
| будь-яка кнопка | `default`, `pressed`, `disabled` |
| контрол купівлі / витрати валюти | те саме плюс `insufficient_currency` |
| колекційний або прогресивний елемент | те саме плюс `locked`, `claimed` |
| перемикач, таб, вибір картки | те саме плюс `selected` |
| список або контейнер із даними | `default`, `loading`, `empty` |

Завдяки цьому Plan QA має що блокувати: «немає застосовних states» перестає бути судженням і стає похідною від ролей.

**Запас тексту.** Кожен текстовий контрол проєктується з запасом ≥ 30% довжини рядка — типова різниця UA/DE/RU відносно EN. Текст у PSD існує тільки як живий текстовий шар і ніколи не запікається в растр кнопки чи панелі. `text_box` у манифесті записується окремо від `tap_box`; напис, що торкається країв свого боксу, — блокуючий дефект.

**9-slice.** Кожна панель або кнопка, яку в грі розтягуватимуть, малюється з однорідним центром, а її margins потрапляють у layout manifest. Це рішення про те, *як малювати*, тому воно належить концепт-стадії, а не експорту.

## 3. Архітектура Flow

```text
Entry Prompt
  ↓
UI Planner
  ↓
Plan QA
  ↓
Plan Approval
  ├─ правки → UI Planner
  └─ підтверджено → Dynamic Tasks Manager
                         ↓
                  Concept Executor
                         ↓
                     Concept QA
                         ↓
                  Variant Selection
                  ├─ жодного → Concept Executor
                  ├─ один → PSD Builder
                  └─ декілька → Synthesis Executor
                                      ↓
                                  Synthesis QA
                                      ↓
                              Synthesis Approval
                              ├─ правки → Synthesis Executor
                              └─ прийнято → PSD Builder
                                                ↓
                                             PSD QA
                                                ↓
                                        Asset Approval
                                        ├─ правки → PSD Builder
                                        └─ прийнято → Tasks Manager

UI Knowledge Curator — Work Reviewer, який виконується один раз наприкінці запуску і підсумовує роботу. Поточне навчання після кожного QA чи рішення користувача пишуть самі Result-ноди з `learning_enabled`, одразу в момент рішення, а не Curator.
```

Використовуються наявні типи нод:

- `Prompt Reviewer` як UI Planner;
- `Tasks Manager` як Dynamic Tasks Manager;
- `Executor` як Concept Executor, Synthesis Executor і PSD Builder;
- `Task Reviewer` як Plan QA, Concept QA, Synthesis QA і PSD QA;
- `Result` у режимах `plan_approval`, `variant_selection` та `asset_approval`;
- `Work Reviewer` як UI Knowledge Curator.

## 4. Контракти даних

### 4.1. UI project spec

UI Planner повертає JSON:

```json
{
  "verdict": true,
  "ui_project_spec": {
    "operation": "create|restyle|modify",
    "platform": "portrait_mobile",
    "engine_profile": "baseline|unity|unreal|custom",
    "canvas": {
      "width": 1080,
      "height": 1920,
      "aspect_range": ["9:16", "9:21"],
      "safe_area": {"top": 132, "bottom": 100, "left": 0, "right": 0}
    },
    "art_direction": {
      "primary_reference_family": "clash-royale|flambe|merge-restyle|pop-flow-odyssey|royal-match|tasty-travel",
      "supporting_traits": ["string"],
      "summary": "string"
    },
    "ui_kit": {"path": "tasks/<ui-kit-task>/ui_kit.json", "sha256": "HEX"},
    "constraints": ["string"],
    "references": ["relative/project/path"],
    "assumptions": ["string"],
    "tasks": [
      {
        "id": "stable-id",
        "title": "Screen title",
        "prompt": "Self-contained executor prompt",
        "screen": "screen-id",
        "states": ["default", "pressed", "disabled"],
        "anchor_plan": "string",
        "ui_kit_ref": {"path": "tasks/<ui-kit-task>/ui_kit.json", "sha256": "HEX"},
        "acceptance_criteria": ["string"],
        "attachments": ["relative/project/path"],
        "export_profile": "baseline|sliced"
      }
    ]
  }
}
```

`platform` має єдине значення: Flow робить портретний мобільний UI (§1). Plan QA блокує spec, у якому воно інше або відсутнє.

`art_direction` вибирається один раз на проєкт, `ui_kit` заповнюється після завершення першого task (UI kit) і після цього не змінюється. У кожного screen task `ui_kit_ref` дорівнює цьому ж значенню — надлишковість тут навмисна: task має бути самодостатнім, бо Executor отримує його окремо від spec.

`states` — підмножина словника з §2.2; обов'язковий мінімум виводиться з ролей елементів, а не з бажання Planner. `export_profile`: `baseline` — PNG станів у дизайн-роздільності, `sliced` — те саме плюс 9-slice margins для масштабованих елементів.

Tasks Manager приймає `task_source: input_once`. Під час першого виконання він нормалізує `ui_project_spec.tasks`, записує `ui_plan_snapshot`, `ui_plan_hash` і заморожений список Tasks у checkpoint. Наступні проходи читають лише snapshot.

### 4.2. Variant manifest

```json
{
  "task_id": "string",
  "round_id": "round-001",
  "variants": [
    {
      "variant_id": "V01",
      "path": "tasks/<task>/concepts/round-001/V01.png",
      "sha256": "HEX",
      "board_path": "tasks/<task>/concepts/round-001/V01_board.png",
      "board_sha256": "HEX",
      "layout_manifest_path": "tasks/<task>/concepts/round-001/V01_layout.json",
      "psd_path": "tasks/<task>/concepts/round-001/V01.psd",
      "direction": "short description",
      "qa_status": "pending|passed|failed",
      "supersedes": null
    }
  ]
}
```

`path` — екран на повний canvas в основному стані. Це канонічний артефакт: його бачить `variant_selection`, його hash іде в `approved_artifact_hash`, з ним звіряється composite PSD.

`board_path` — review board: той самий екран плюс прев'ю решти застосовних станів. Він потрібен, щоб користувач вибирав варіант, уже побачивши стани, і щоб Concept QA міг їх оцінити. Але він ніколи не є еталоном для composite.

`psd_path` — шаровий документ, з якого експортовано обидва PNG. Саме його продовжує PSD Builder після затвердження варіанта, тому збіг composite із затвердженим PNG забезпечується побудовою, а не старанням.

Усі чотири варіанти мають однакову функцію, контент, canvas, набір станів і однакові art-placeholder бокси, але відрізняються композицією, ієрархією, shape language, матеріалами та декором. Саме ці п'ять осей Photoshop і малює сам — тому placeholder на місці ілюстрації не знецінює порівняння варіантів.

Concept QA повертає загальний `verdict` і `variant_reviews`. Якщо провалився лише V03, наступний контракт містить `retry_variant_ids: ["V03"]` і frozen hash решти. Executor не має права змінювати прийняті варіанти.

### 4.2.1. Layout manifest

Поруч із кожним `V0N.png` лежить `V0N_layout.json` — витяг точної геометрії з Photoshop-документа. Він існує, щоб мобільні правила з §2.2 перевірялися числом, а не оком:

```json
{
  "variant_id": "V01",
  "canvas": {"width": 1080, "height": 1920},
  "safe_area": {"top": 132, "bottom": 100, "left": 0, "right": 0},
  "elements": [
    {
      "id": "btn-play",
      "role": "button|purchase|collectible|toggle|list|label|decor|art_placeholder",
      "tap_box": {"x": 0, "y": 0, "w": 0, "h": 0},
      "text_box": {"x": 0, "y": 0, "w": 0, "h": 0},
      "anchor": "top|bottom|center|left|right|top-left|top-right|bottom-left|bottom-right",
      "states": ["default", "pressed", "disabled"],
      "nine_slice": {"left": 0, "top": 0, "right": 0, "bottom": 0},
      "critical": true
    }
  ],
  "art_requests_path": "tasks/<task>/concepts/round-001/art_requests.json"
}
```

- `tap_box` обов'язковий для кожної інтерактивної ролі; `text_box` — для кожного елемента з текстом; `nine_slice` — для кожного масштабованого; `critical: true` позначає контроли, яким заборонено заходити в safe area.
- QA звіряє манифест механічно: `tap_box ≥ 144×144` (абсолютний мінімум 132), проміжок між сусідніми `tap_box` ≥ 24 px, критичні елементи всередині safe area, у кожного елемента є `anchor`, набір `states` відповідає ролі за таблицею §2.2, текст не торкається країв `text_box`.
- Манифест звіряється і з самим PNG: елемент, якого не видно на рендері, або видимий контрол без запису в манифесті — блокуючий дефект.
- `art_requests.json` — брифи на ілюстративні placeholder: id, бокс, опис бажаного арту, посилання на готовий асет із `references/art/`, якщо він уже є.

### 4.3. Result response

Усі спеціалізовані Result повертають:

```json
{
  "action": "approve_plan|select_variants|continue|continue_with_feedback",
  "selected_variant_ids": ["V01"],
  "selection_mode": "none|single|multiple",
  "note": "string",
  "approved_artifact_hash": "HEX",
  "approved_plan": {}
}
```

`plan_approval` показує редагований JSON-план. `variant_selection` показує checkbox, прев'ю та описи V01–V04. `asset_approval` показує PSD QA, rendered previews, PSD/exports/manifest і допускає override лише для `visual_preference`.

### 4.4. QA schema

```json
{
  "verdict": false,
  "score": 0,
  "reason": "string",
  "issues": [
    {
      "defect_id": "stable-id",
      "category": "visual_preference|visual_mismatch|technical_blocker|missing_requirement",
      "severity": "info|warning|blocking",
      "description": "string",
      "target_files": ["relative/path"],
      "must_fix": "concrete action"
    }
  ],
  "must_fix": ["string"],
  "evidence_files": ["relative/path"]
}
```

Одного score недостатньо для проходження. `verdict=false` є обов'язковим при будь-якому blocking issue. `visual_preference` може бути прийнятий користувачем; `visual_mismatch`, `technical_blocker` і `missing_requirement` — ні.

Порушення мобільних правил із §2.2 — замалий тач-таргет, критичний контрол у safe area, відсутній anchor, текст без запасу, курсорний стан, відхилення від токенів `ui_kit.json` — класифікуються як `technical_blocker`, а не `visual_preference`. Кнопка 118 px не «справа смаку»: у неї не влучить палець. Окрема категорія для цього не потрібна — достатньо того, що `technical_blocker` не підлягає override.

Якщо `severity` не вказано, `visual_preference` отримує `warning`, а решта категорій — `blocking`. Інакше пропущене поле мовчки закривало б override, який цей контракт обіцяє. Явно вказаний `blocking` лишається блокуючим і для `visual_preference`: це свідоме рішення QA, а не пропуск.

## 5. Result, checkpoint і retry guard

- `Result.confirmation_mode`: `standard|plan_approval|variant_selection|asset_approval`.
- `Result.confirmation_ports`: список портів, для яких показується діалог, типово `true,false`; UI-шаблон використовує `true`, щоб негативний технічний QA автоматично повертав роботу Executor.
- Чернетки Result зберігають note, вибрані variant IDs, редагований план і стан GrillMe.
- Trusted receipt для Tasks Manager створюється лише Result із `final_task_result: true`. Проміжні Plan/Variant/Synthesis Result не завершують task.
- Retry guard зберігає для result/task останні defect IDs, усі бачені раніше defect IDs і score. `retry_attention` intervention замість чергової автоматичної спроби створює або друга поспіль поява того самого blocking defect (поріг `retry_guard_threshold`, типово 2), або повернення дефекту, якого минулого разу вже не було. Саме лише падіння score регресією не вважається: бали просідають і від нових, ще не бачених зауважень.
- Attention не видаляє queue, pending inputs, outputs, history, файли чи чернетки.
- Resumable STOP перериває активний turn, повертає ноду та inputs у queue і
  зберігає стан `stopped_resumable`, тому продовжити можна одразу кнопкою Run
  або після повного перезапуску FlowAI. Навіть примусове переривання транспорту
  не дорівнює Discard; очищення checkpoint виконує лише окрема команда.
- Зупинений запуск не підсумовується: Work Reviewer робив би висновки про роботу, яку не довели до кінця.
- Запит STOP знімає бар'єр паузи, інакше переривання на Pause не могло б дійти
  до runner. Наступна Pause після STOP ігнорується.

## 6. Photoshop і PSD

Photoshop 2022 — єдиний рендерер Flow, від першого концепту до фінального PSD (§1). Photoshop adapter:

- перевіряє Windows і наявність Photoshop 2022/COM; ця перевірка виконується перед першим Concept Executor, а не перед PSD Builder — без Photoshop Flow не має чим малювати вже перший раунд;
- створює runtime JSX лише у `<project>/.flowai/runtime/`;
- запускає JSX через Photoshop COM без консолі shell;
- видаляє попередній validation report перед запуском, щоб звіт доводив саме цей прогін;
- повторно відкриває PSD та збирає validation report;
- разом із кожним рендером екрана витягає layout manifest (§4.2.1) із фактичних меж шарів — манифест ніколи не пишеться «з пам'яті» агента;
- не створює placeholder PSD (art-placeholder шари всередині справжнього документа — інша річ і дозволені);
- при відсутності Photoshop, шрифту або необхідного ресурсу повертає blocking attention.

PSD Builder стартує не з чистого документа, а з `psd_path` затвердженого варіанта — того самого файлу, з якого експортовано затверджений PNG.

PSD-вимоги:

- один PSD на екран;
- групи Background, Frame, Header, Content, Controls, Icons, Text і States;
- editable text, buttons, panels, icons та state groups;
- текст існує тільки як живі текстові шари і ніколи не запечений у растр контролу (§2.2, запас тексту);
- складні ілюстрації/фактури як окремі растрові Smart Objects; art-placeholder шари підписані `ART_` і відповідають записам `art_requests.json`;
- масштабовані панелі та кнопки мають однорідний центр, придатний для 9-slice, а margins записані в layout manifest;
- Layer Comps для станів;
- відновлений фон під відокремленими елементами;
- composite render відповідає затвердженому `V0N.png` (не борду);
- відсутні broken links;
- створені state PNG exports, оновлений layout manifest і manifest.

## 7. Локальне навчання

```text
learnings/
  ui_learnings.jsonl
  ui_project_profile.md
  skill-proposals/
```

`ui_learnings.jsonl` — append-only журнал структурованих QA та користувацьких review events. `ui_project_profile.md` — актуальна локальна пам'ять. Обидва файли оновлює Result-нода з `learning_enabled` одразу після кожного QA чи рішення користувача. Після прийнятого PSD Curator може створити diff-пропозицію в `skill-proposals/`; глобальний `modern-ui` не змінюється без явного підтвердження користувача.

Пріоритет інструкцій:

```text
поточні правки користувача
→ затверджений task/spec
→ локальний ui_project_profile
→ глобальний modern-ui
→ рекомендації QA
```

## 8. Структура файлів проєкту

```text
project/
  ui_project_spec.json
  references/
    art/                      ← готові ілюстративні асети, якщо є
  tasks/<ui-kit-task>/
    ui_kit.json               ← токени стилю: палітра, радіуси, рамки, шрифтові ролі, матеріали
  tasks/<task-id>/
    concepts/round-001/
      V01.png                 ← екран, основний стан; канонічний артефакт
      V01_board.png           ← борд станів; тільки для вибору та QA
      V01_layout.json         ← layout manifest (§4.2.1)
      V01.psd                 ← шаровий документ; його продовжує PSD Builder
      V02.png … V04.psd
      art_requests.json
      manifest.json
    synthesis/
    psd/
    exports/
    qa/
  learnings/
    ui_learnings.jsonl
    ui_project_profile.md
    skill-proposals/
  .flowai/runtime/
```

Будь-який output path нормалізується відносно project workspace. Абсолютний шлях або `..`, що виходить за workspace, відхиляється. Старі раунди не перезаписуються.

## 9. Критерії приймання

- Неповний Entry Prompt створює редагований plan з явними assumptions; дефолти §2.2 підставляються самі, а їх перевизначення видно в assumptions.
- Spec із `platform`, відмінним від `portrait_mobile`, або з курсорними станами блокується Plan QA.
- Підтверджений plan відновлюється після перезапуску і не запускає Planner повторно.
- Tasks Manager послідовно обробляє UI kit і всі screen tasks; `ui_kit.json` створюється першим task, і кожен наступний несе його hash у `ui_kit_ref`.
- `primary_reference_family` одна на весь проєкт; Concept QA блокує відхилення екрана від токенів `ui_kit.json`.
- Planner, Executor і visual QA використовують записаний `modern-ui` analysis для бібліотеки з 66 референсів, не запускаючи повторного corpus-wide аналізу.
- Незмінна бібліотека дає cache hit за SHA-256; доданий, видалений або змінений файл дає `reference_analysis_attention` до роботи зі стилем.
- Concept Executor створює рівно V01–V04: на кожен варіант екран `V0N.png`, борд `V0N_board.png`, `V0N_layout.json`, `V0N.psd` — і manifest із hash.
- Порушення §2.2 (тач-таргет < 144 px, критичний контрол у safe area, відсутній anchor, текст без запасу) ловиться числом за layout manifest і класифікується як `technical_blocker` без права override.
- Ілюстративні ділянки — підписані art-placeholder із брифом у `art_requests.json`; QA не валить варіант за відсутність намальованого арту, тільки за відсутній чи неописаний placeholder.
- Провал V03 не змінює hash V01/V02/V04.
- Працюють single-select, multi-select із Synthesis та reject-all із правками.
- Закриття Result або GrillMe не втрачає checkbox, plan edit чи note.
- GrillMe feedback передається правильному Executor у гілку FALSE.
- PSD Builder продовжує `V0N.psd` затвердженого варіанта, а не малює екран заново; PSD реально відкривається Photoshop і має необхідні groups, editable layers та Layer Comps.
- Прихований фон під відокремленим елементом відновлений, а composite збігається із затвердженим `V0N.png` (борд у порівнянні не бере участі).
- Увесь текст у PSD — живі текстові шари; масштабовані елементи мають однорідний центр і записані 9-slice margins.
- Технічний blocker не можна override; visual preference можна.
- Друга поява того самого defect ID або повернення вже виправленого дефекту переводять Flow у `Pause · Attention` зі збереженим прогресом; падіння score саме собою — ні.
- STOP активний у running, paused і attention state, причому на паузі він справді зупиняє Flow, а не чекає на Resume.
- Після resumable STOP запуск продовжується як із того самого сеансу, так і після виходу з програми та повторного запуску FlowAI.
- Усі артефакти та runtime-файли залишаються в project workspace.
- Локальні learning-файли оновлюються автоматично; global skill — лише після підтвердження.
- UI Flow template проходить validation; лінії не перетинаються або мають мінімальну кількість перетинів завдяки доріжкам і control points.
- Наявні статичні Tasks Manager та standard Result лишаються сумісними без міграції старих Flow.

## 10. Дельта реалізації мобільної ревізії

Виконано 2026-08-25. Валідація тут живе в промптах: код FlowAI не перевіряє `states` і `canvas` ([ui_workflow.py:252](file:///C:/Users/illia/Documents/DDA%20PF/FlowAI/flowai/ui_workflow.py)), тож текст нод — і є контракт. Перевірка Photoshop перед концепт-нодами — окремий прапор `photoshop_preflight` (лише preflight), бо `photoshop_required` тягне за собою ще й вимогу кінцевого `.psd` через `candidate_path`, якої концепт-нода з variant manifest виконати не може.

`examples/game_ui_workflow.flowai.json`:

- **ui-entry** — текст запрошення каже, що платформа фіксована (портретний мобайл), і просить лише розмір/екрани/стиль/референси.
- **ui-planner** — інструкції: family один раз на проєкт; дефолти canvas/safe area з §2.2 з винесенням перевизначень у assumptions; словник станів і таблиця ролей; перший task — UI kit із `ui_kit.json`; `ui_kit_ref` і `anchor_plan` у кожному screen task; заборона курсорних станів.
- **plan-qa** — blocking: `platform != portrait_mobile`, курсорні стани, відсутні canvas-дефолти, відсутній UI-kit task, family per-task замість per-project.
- **concept-executor** — рендер через Photoshop COM (§6); на варіант: `V0N.png`, `V0N_board.png`, `V0N_layout.json`, `V0N.psd`, `art_requests.json`; `output_schema` доповнюється `board_path`, `board_sha256`, `layout_manifest_path`, `psd_path`.
- **concept-qa** — числові перевірки §4.2.1 за layout manifest; звірка з токенами `ui_kit.json`; порушення §2.2 = `technical_blocker`.
- **synthesis-executor / synthesis-qa** — той самий контракт варіанта, що й у концептів.
- **psd-builder** — продовжує `V0N.psd`, не малює заново; живий текст; однорідний центр і margins для масштабованих елементів.
- **psd-qa** — composite звіряється з `V0N.png`; перевірка живого тексту, `ART_`-шарів проти `art_requests.json`, оновленого layout manifest.

Код FlowAI:

- перевірка Photoshop 2022/COM перед першим Concept Executor, а не перед PSD Builder (§6);
- `find_variants` у [ui_workflow.py](file:///C:/Users/illia/Documents/DDA%20PF/FlowAI/flowai/ui_workflow.py) копіює довільні ключі варіанта, тож нові поля проходять без правок; переконатися тестом;
- опційне зміцнення: фільтр словника станів у `normalize`-шляху `ui_project_spec.tasks` — не обов'язковий для запуску, але робить контракт незалежним від дисципліни промптів.
