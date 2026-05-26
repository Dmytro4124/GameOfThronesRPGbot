# Telegram Game of Thrones RPG Bot — Developer Guidelines

> Цей файл — джерело правди про проєкт для Claude Code та всіх subagents.
> Він автоматично завантажується у контекст усіх сесій. Тримай його коротким, точним і актуальним.

---

## 1. Що це за проєкт

Telegram-бот текстової RPG у світі Гри Престолів. Користувач керує персонажем у Вестеросі/Ессосі (рік 298 А.З.), вводить дії природною мовою, бот відповідає наративною сценою й оновлює стан персонажа і NPC.

Серце продукту — D&D 5e adaptation для ASoIaF: `1d20 + ability_mod + proficiency_bonus` vs DC, 9 GoT-класів, 6 heritages, FSM-pipeline NORMAL ↔ COMBAT (spotlight 1–2 NPCs/round). Оркестрація: Censor → Worker (NORMAL або COMBAT) → GM_Logic → Narrator. RAG над лор-базою.

## 2. Стек

- **Python 3.10+**
- **Telegram:** aiogram 3.x + aiohttp (webhook через `WEBHOOK_URL`, fallback на polling)
- **LLM:** google-genai SDK
  - Worker, GM_Logic, Narrator: `gemma-4-31b-it`
  - Embeddings: `gemini-embedding-2-preview`
- **БД:** Google Sheets через gspread + Service Account
  - `Users_DB` — профіль користувача як JSON-string у 3-й колонці
  - `NPC_DB` — заповнюється з `database/canon_npc.py` на старті
  - `KnowledgeBase` — лор для RAG
- **RAG:** numpy евклідова відстань, кеш у `lore_embeddings.npy` + MD5 у `lore_hash.txt`
- **Тести:** pytest (юніти у `test/`), окремий E2E QA-харнес (`qa_auto_test.py`)

---

## 3. Робочий протокол (orchestration workflow)

**Це головний розділ файлу.** Він описує, як головна сесія Claude Code обробляє задачі від користувача.

Ключова концепція: **головна сесія = tech-lead.** Її роль — **не писати код**, а розкладати задачу і делегувати спеціалізованим subagents.

### 3.1. Plan mode перш ніж будь-яка дія над кодом

Plan mode — обов'язковий стартовий крок для:
- **Будь-якої зміни коду** (нова функція, баг-фікс, рефакторинг, оновлення тестів)
- **Будь-якої діагностики** (з'ясувати чому щось падає, чому поведінка не та, що очікувалась, чому JSON ламається)
- **Будь-якої дослідницької задачі**, де результат — звіт або рекомендація

Винятки — лише тривіальні правки за прямим вказівником користувача (див. 3.4).

Plan mode завершується тим, що план показано користувачу і він його схвалив. Жодних правок або викликів агентів до того.

### 3.2. Делегуй, а не пиши сам

Головна сесія сама код **не пише** окрім винятків (див. 3.4). Замість цього вона викликає subagents через Task tool.

### 3.3. Обов'язкова послідовність

Для кожної задачі на зміну коду:

1. **Domain workers** (паралельно, де задачі незалежні; послідовно, де є залежності):
   - `prompt-engineer` — для змін у `core/prompts.py`
   - `mechanics-dev` — для змін у `core/mechanics.py`, `core/engine.py`, `core/world.py`
   - `data-rag-agent` — для змін у `database/operations.py`, `database/canon_npc.py`, `database/sheets.py`
   - `python-dev` — для змін у `bot/handlers.py`, `bot/utils.py`, `bot/menus.py`, `main.py`, `config.py`
2. **`qa-agent`** — обов'язковий крок. Пише або оновлює pytest, запускає, повертає результат.
3. **`code-reviewer`** — обов'язковий крок. Read-only ревізія, повертає список зауважень.

Задача **не вважається завершеною**, доки `qa-agent` не повернув PASS і `code-reviewer` не повернув звіт без блокуючих зауважень.

### 3.4. Винятки (можна без делегації)

Делегація — оверкіл для:
- Перейменування змінних (rename refactoring)
- Виправлення опечаток у коментарях / docstrings / повідомленнях
- Форматування (whitespace, lint-правки)
- Однорядкові правки за прямим вказівником користувача

У цих випадках головна сесія може правити сама, але все одно показує діф перед збереженням.

### 3.5. Fallback для нечітких задач

Якщо домен задачі очевидно належить одному з спеціалізованих агентів (`prompt-engineer`, `mechanics-dev`, `data-rag-agent`) — делегуй йому. Якщо задача — це glue-код між модулями (новий handler, конфіг, CLI-скрипт, інтеграція aiogram, виправлення імпортів) — делегуй `python-dev`. Якщо домен неочевидний (наприклад, баг "повзе" через два модулі різних доменів) — план у plan mode уточнює маршрут і послідовність делегацій перш ніж виконанням.

### 3.6. Формат звіту worker-агента

Кожен subagent повертає звіт у фіксованому форматі:

```
## Зроблено
- (конкретний список змін)

## НЕ зроблено
- (що було в задачі, але я не зробив, і чому)

## Припущення
- (що я припустив, що НЕ було явно сказано в задачі)

## Ризики
- (що може зламатись, на що звернути увагу при ревізії)

## Файли змінено
- path/file.py — суть змін
```

Розділ **Припущення** — критичний. Це головний інструмент користувача для виявлення місць, де агент "зрізав кут" або не зрозумів задачу.

### 3.7. Фінальний підсумок користувачу

Головна сесія агрегує звіти всіх агентів у єдине повідомлення:

```
## Підсумок
(одне-два речення про результат)

## Що зроблено
(склад зроблених змін)

## Тести
(результат qa-agent: PASS/FAIL, скільки тестів, що покрито)

## Зауваження ревьюера
(склад зауважень code-reviewer, з помiткою які блокуючі)

## Припущення, які зробили агенти
(зведення з усіх worker-звітів)

## Я рекомендую
(merge / fix and re-review / rollback)
```

Без цього підсумку задача не вважається завершеною.

### 3.8. Діагностичні задачі — делегувати, не робити сам

Якщо задача — це **з'ясувати причину поведінки** ("чому ці тести падають", "чому бот зависає", "чому JSON ламається"), а не "змінити X на Y" — головна сесія **не діагностує сама**, а делегує спеціалізованому агенту відповідного домену:

- Падіння тестів механіки / неочікувана поведінка движка → `mechanics-dev`
- Помилка парсингу LLM-відповіді / промпт повертає не те → `prompt-engineer`
- Збій у gspread / RAG / читанні з Sheets → `data-rag-agent`
- Помилка в handler-і Telegram / glue-логіці → `python-dev`
- Домен неочевидний → план у plan mode уточнює, далі делегація

**Чому це важливо.** Діагноз — це повноцінна одиниця роботи зі своїм звітом у форматі §3.6. Розділи **Припущення** і доменно-специфічні чек-листи (`Перевірені інваріанти` для `mechanics-dev`, `Async-аудит` для `data-rag-agent` тощо) залишають слід у системі: завтра ми знатимемо, що саме спеціаліст перевірив. Усний "діагноз у три рядки" від tech-lead такого сліду не залишає.

Дозволено: головна сесія може запустити одну швидку команду (`pytest -v`, `git diff`) на самому початку, щоб **зрозуміти, до якого домену належить задача**, і потім делегувати. Це орієнтація, не діагностика.

Заборонено: головна сесія читає 5+ файлів коду, формулює гіпотези про причину поведінки і виносить вердикт. Це робота спеціаліста.

Якщо діагноз показав, що задача потребує зміни коду — після нього йдуть звичайні кроки 3.3 (worker → qa-agent → code-reviewer).

---

## 4. Карта проєкту

```
TelegramGameOfThronesBot/
├── main.py                  # точка входу: webhook або polling
├── config.py                # ENV, моделі, режими (godmode/puppet/erotic)
├── scripts/
│   └── dnd_migrate.py       # CLI tool для Phase 9 migration (НЕ runtime)
├── bot/
│   ├── handlers.py          # aiogram-роутер (всі апдейти, монолітом)
│   ├── menus.py             # reply-клавіатури
│   └── utils.py             # markdown-санітайзер, send_safe_message
├── core/
│   ├── ai_client.py         # Gemini wrapper, JSON-парсер
│   ├── engine.py            # головний цикл process_game_turn
│   ├── mechanics.py         # apply_system_impacts (час/золото/локації/clocks), validate_action (Censor), process_training_request (D&D XP)
│   ├── prompts.py           # ВСІ системні промпти 4 ролей
│   ├── world.py             # генерація стартового світу
│   ├── world_constants.py   # 21 регіон / 80 локацій / сцени
│   ├── cheats.py            # адмін-команди
│   ├── dnd_core.py          # d20 math, CheckResult, skill_check/ability_check/saving_throw
│   ├── dnd_skills.py        # 18 D&D 5e skills mapping (ability → skill)
│   ├── dnd_classes.py       # 9 GoT-класів з L1–20 features
│   ├── dnd_heritages.py     # 6 heritages з traits
│   ├── dnd_conditions.py    # 14 PHB conditions + bleeding
│   ├── dnd_combat.py        # CombatState FSM, attack/damage resolution
│   ├── combat_state.py      # in-memory registry з asyncio.Lock per chat_id
│   ├── dnd_progression.py   # XP_TABLE, level_up, ASI
│   ├── dnd_rest.py          # short/long rest
│   ├── dnd_engine.py        # resolve_normal_action + apply_dnd_impacts (NORMAL pipeline)
│   ├── dnd_combat_engine.py # COMBAT round execution (spotlight pattern)
│   └── dnd_migration.py     # backup + LLM-regen NPC + wipe utilities
├── database/
│   ├── canon_npc.py         # ~100 канонічних NPC (хардкод)
│   ├── operations.py        # gspread + RAG
│   └── sheets.py            # gspread Singleton
└── test/
    ├── test_ai_parsing.py
    ├── test_engine.py
    └── test_mechanics.py
```

Окремо: `qa_auto_test.py`, `qa_profiles.py`, `run_night_tests.py` — це E2E QA-харнес, **не unit-тести**. Запускати окремо для регресійного контролю pipeline.

---

## 5. Ключові інваріанти (НЕ ПОРУШУВАТИ)

Ці правила діють у всьому коді. Будь-який subagent, що їх ламає, має бути виправлений ревьюером.

### 5.1. Асинхронність

- **Усі виклики gspread** (Google Sheets) — синхронні. **Завжди** загортати в `asyncio.to_thread(...)`.
- **Усі виклики Gemini SDK** — синхронні. **Завжди** загортати в `asyncio.to_thread(...)`.
- Виняток — embedding-батчі з власними паузами для квот (`load_lore_data`).

### 5.2. Шкали і константи

- **Кубики: 1d20 + ability_mod + proficiency_bonus** vs DC. Попередня система 2d50 видалена. Advantage/Disadvantage = roll 2d20, take high/low.
- **DC — STRICT enum:** `{2, 5, 10, 12, 15, 17, 20, 22}`. Будь-яке інше число — **баг**. `clamp_dc()` у `core/dnd_core.py` обрізає до найближчого валідного значення. DC 2 = ultra-trivial auto-success (sensory, prosaic, social signals); `AUTO_SUCCESS_MAX_DC = 2` — DC ≤ 2 пропускає кидок. (Макс знижено з `{...,25,28,30}` під час тестування — див. §7 технічний борг.)
- **Ability scores:** 6 характеристик (STR/DEX/CON/INT/WIS/CHA), діапазон 1–20. Modifier = `floor((score - 10) / 2)`.
- **Proficiency bonus:** L1–4 = +2, L5–8 = +3, L9–12 = +4, L13–16 = +5, L17–20 = +6.
- **Skills:** 18 D&D 5e скілів, прив'язані до abilities (Athletics→STR, Insight→WIS, Deception→CHA тощо). Повний список — `core/dnd_skills.py:SKILLS`.
- **HP:** `hp_current` / `hp_max` — variable, залежить від класу (`hit_die`) + рівня + CON mod. **НЕ** фіксований 0–100.
  - Legacy UI sync: `profile["Здоров'я"] = round(100 * hp_current / hp_max)` — синхронізується в `apply_dnd_impacts` для зворотної сумісності з handlers.py.
- **Енергія персонажа: 0–1000** (пороги 200/400/600/800/1000). *Legacy* шкала, зберігається для backward compat і UI. Міграція до exhaustion levels 1–6 — deferred (Phase 9+). Старт нового персонажа = 1000.
- **9 GoT-класів:** Knight, Hedge Knight, Maester, Septon, Sellsword, Spy/Whisperer, Courtier, Bastard, Wildling Raider.
- **XP per-turn:** 0 / 25 / 50 / 100 / 200. Таблиця рівнів (D&D 5e PHB p.15): L1=0, L2=300, L3=900, L4=2700, L5=6500 … L20=355000. Повна таблиця у `core/dnd_progression.py:XP_TABLE`.
- **Sliding-window історії:** стиснення сумаризацією при більше ніж 20 ходів.

### 5.3. JSON-контракти pipeline

Це найкритичніший інваріант продукту. Ламати його — означає поламати весь хід гри.

Pipeline складається з 4 ролей. Перші три повертають JSON, **четверта (Narrator) — чистий художній текст**, не JSON.

**FSM Mode:** `profile["mode"]` = `"NORMAL"` або `"COMBAT"`. Engine диспетчеризує відповідний pipeline.

**Censor** (`build_validate_action_prompt`) — обов'язкові ключі:
- `is_valid` (bool), `refusal_reason` (string)

**Worker NORMAL** (`build_normal_resolve_prompt`) — обов'язкові ключі:
- `skill_check_reasoning`, `difficulty_reasoning`, `gold_reasoning` — внутрішнє міркування (потрібні для діагностики поведінки моделі)
- `ability_used` (`"STR"` | `"DEX"` | `"CON"` | `"INT"` | `"WIS"` | `"CHA"` | `"None"`)
- `skill_used` (один з 18 D&D skills або `"None"`)
- `difficulty` — STRICT enum `{5, 10, 12, 15, 17, 20, 22}` (div. 5.2)
- `advantage_reason`, `disadvantage_reason` (рядки; порожні = no modifier)
- `combat_imminent` (bool) — якщо true, engine ініціює COMBAT_MODE наступним ходом
- `verdict_text`, `xp_award` ∈ {0|25|50|100|200}, `reputation_delta` (-3..+3), `reputation_target_npc`
- `updates` — вкладений об'єкт: `minutes_passed`, `location_impact`, `scene_impact`, `hp_damage_dice` (напр. `"1d6"`), `hp_heal_dice`, `gold_impact`, `inventory_new`, `inventory_lost`, `clocks_impact`, `condition_apply[]`, `condition_remove[]`

**Worker NORMAL OPTIONAL keys** (для D&D save/rest mechanics, додано 2026-05):
- `save_used` (`"STR"|"DEX"|"CON"|"INT"|"WIS"|"CHA"|"None"`, default `"None"`) — якщо set, engine викликає `saving_throw()` замість skill_check. Див. GATE S у Worker prompt.
- `save_dc` (integer ∈ LEGAL_DCS, default 5) — DC для save. Engine робить `clamp_dc()`.
- `rest_type` (`"long"|"short"|"none"`, default `"none"`) — якщо set, engine викликає `long_rest()` / `short_rest()`. Див. GATE R. **REST має пріоритет над SAVE** при одночасному наявності обох.

**Worker NORMAL INPUT context** (передається в `<player_state>`):
- `equipped_weapon` (dict `{name, damage_dice, damage_type, properties}`) — structured опис зброї. Опціонально (старі профілі без поля → fallback на текст). LLM використовує properties (`"finesse"`, `"thrown"`, `"ranged"`) для вибору ability_used (DEX vs STR). Див. GATE W.

**Worker COMBAT** (`build_combat_round_prompt`) — обов'язкові ключі:
- `intent` (`"attack"` | `"cast"` | `"move"` | `"dodge"` | `"flee"` | `"item"` | `"help"` | `"grapple"` | `"shove"`)
- `target_npc`, `weapon`, `spell_or_ability`, `tactic` (`"reckless"` | `"normal"` | `"cautious"`), `move_to`, `verdict_text`, `reasoning`

**GM_Logic** (`build_gm_logic_prompt`) — обов'язкові ключі:
- `reasoning`, `npc_reasoning` — внутрішнє міркування
- `director_notes` — масив 3–7 фактичних речень для Narrator (без літературних прикрас)
- `companion_npcs` — масив імен NPC, що йдуть з гравцем. **Критично:** це білий список проти телепортацій. Без імені тут NPC фізично не зможе перейти з гравцем.
- `npc_updates` — масив об'єктів NPC. Фіксований склад: `Name`, `Location`, `Scene`, `Memory_Anchor`, `Relation_NPCs`, `Inventory`, `Status` (`Active` | `Dead` | `Fled` | `Unconscious`), `hp_current`, `conditions[]`.
- `mode_transition` (`null` | `"TO_COMBAT"` | `"TO_NORMAL"`)
- `suggested_actions` — **рівно 4** об'єкти `{button, intent}`, не більше і не менше.

**Narrator** (`build_narrator_prompt(combat_log=...)`) — повертає **чистий художній текст**, без JSON, без маркдауну.
- Якщо `combat_log` передано → COMBAT style: 4–6 коротких речень з action verbs.
- Інакше → NORMAL style: 150–250 слів атмосферного тексту.

**Заморожені поля NPC** (за замовчуванням НЕ включати в `npc_updates`): `Description`, `Character`, `Goal`, `Secrets`. Виняток — епічна незворотна подія (каліцтво, публічно розкрита таємниця). Поле `Attitude to Player` — **read-only**; ніколи не включати в `npc_updates`.

**Шкала Relation_Player:** 15-тирна шкала зберігається як lore-текст на NPC без системного механічного впливу (faction reputation system замінює механіку). Значення: `Смертельна ненависть` ... `Абсолютна довіра`.

**Правило мутації pipeline:** будь-яка зміна ключа вимагає **синхронного оновлення трьох місць**:
1. Відповідного промпту в `core/prompts.py`
2. Парсера в `core/ai_client.py:clean_and_parse_json` і його споживача в `core/engine.py`
3. `qa_auto_test.py` — якщо тест чекає на цей ключ

**Допоміжні JSON-промпти** (поза основним ходом):
- Training intent (`build_training_request_prompt`): `is_training`, `is_possible`, `skill`, `method`, `reason_if_failed`
- Initial stats (`build_initial_stats_prompt`): D&D 5e character creation — `suggested_class`, `suggested_heritage`, `ability_scores` (6 полів), `hp_max`, `thought_process` (внутрішнє CoT, не ключ pipeline)
- Game intro (`build_game_intro_prompt`): `narrative_text`, `action_prompt`, `suggested_actions`
- NPC populate (`build_populate_npcs_prompt`): масив NPC з повним D&D statblock
- NPC combat action (`build_npc_combat_action_prompt`): batched 1–2 NPC; returns `{"actions": [{npc_name, action, target, weapon, reason}]}`
- NPC regen (`build_npc_regen_prompt`): Phase 6 migration — повертає D&D statblock для канонічного NPC

### 5.4. Конкурентність (слабке місце)

In-memory структури без локів — навмисний компроміс, але код, що їх торкається, повинен мати `try/finally`:
- `user_sessions: dict` (engine.py)
- `PROCESSED_MESSAGES: set` (handlers.py)
- `active_processing: set` (handlers.py) — це user lock проти паралельних ходів
- `_thoughts_log: list` (модуль-глобальна)
- `combat_state._combat_states: dict[chat_id, CombatState]` (`core/combat_state.py`) — стан активного бою
- `combat_state._state_locks: dict[chat_id, asyncio.Lock]` (`core/combat_state.py`) — атомарність COMBAT pipeline

**COMBAT lock pattern** (обов'язково при integration з `combat_state`):
```python
lock = get_or_create_lock(chat_id)
async with lock:
    # combat operations
    ...
cleanup_lock(chat_id)  # при exit з COMBAT mode
```

Якщо процес впаде під час ходу і `active_processing` не звільниться — користувач не зможе ходити до рестарту бота. Будь-який новий код, що захоплює ці структури, повинен мати `try/finally` на верхньому рівні.

### 5.5. RAG-кеш

- `lore_embeddings.npy` валідний тільки якщо MD5 у `lore_hash.txt` збігається з MD5 поточного `KnowledgeBase`.
- Перевикладання кешу — батчами по 90 з 60-секундними паузами (квоти Gemini).
- Поріг релевантності — евклідова відстань < 1.2, top-3.

---

## 6. Команди розробки

```bash
# Запуск у polling-режимі (локально)
python main.py

# Запуск у webhook-режимі
WEBHOOK_URL=https://your.domain PORT=8080 python main.py

# Юніт-тести
pytest test/ -v

# E2E QA-харнес
python qa_auto_test.py
```

Адмін-ID для cheat-команд хардкоднуто у `config.py:ADMIN_TELEGRAM_IDS`.

ENV-змінні: `TELEGRAM_TOKEN`, `GEMINI_API_KEY` (+ `GEMINI_API_KEY_TEST_*` для ротації квот), `SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_JSON`, `WEBHOOK_URL`, `PORT`.

---

## 7. Свідомий технічний борг

Не виправляти "по дорозі" без явної задачі. Кожне з цього — окремий task:

- `bot/handlers.py` — монолітний. Розбиття на feature-модулі — окремий рефакторинг.
- Дублювання canon NPC між `database/canon_npc.py` (Python-список) і Sheets `NPC_DB` (runtime source of truth) — ризик розсинхрону. NPC потребують regen на D&D statblocks.
- Низьке покриття unit-тестами. Кожна нова фіча мусить додавати тест.
- Промпти inline у `prompts.py` як f-strings — складно діффити та рефакторити.
- Migration script `scripts/dnd_migrate.py` — CLI tool для Phase 9; `Users_DB` вимагає wipe перед production launch (старі профілі несумісні з D&D pipeline).
- `test_engine.py::test_process_game_turn_success` — замінено на `pytest.skip` (застарів після переходу на async + D&D pipeline). Перезапис — окрема задача qa-agent.
- Death saving throws (3 fails = death) — deferred. На MVP HP=0 = `Status: Dead`.
- Subclass archetypes (D&D L3 multi-class branches) — deferred.
- Feats — deferred (наразі лише ASI при level-up).
- Multiclassing — deferred.
- Exhaustion levels 1–6 — deferred (зараз energy 0–1000 legacy).
- DC enum знижено до max 22 під час тестового релізу (тестувальник скаржився на недосяжні DC для L1). Розширення до повного 5e enum `{5,10,12,15,17,20,22,25,28,30}` — окрема задача після калібрування з більш сильними персонажами (L3+).
- Class features tracker (1/day usage для active features типу "Срібний язик") — deferred. Зараз `build_normal_resolve_prompt` передає features у Worker LLM як context (Layer 1), але Python не лічить використання — система довіряє player narrative. Layer 2 — окрема задача коли з'являться зловживання.

---

## 8. Skills

У `.claude/skills/` лежать спеціалізовані інструкції:
- `database_and_rag.md` — правила gspread / RAG / NPC anti-hallucination
- `mechanics_and_prompts.md` — правила 2d50/DC/енергії та обов'язкові ключі JSON-промптів

**Увага:** `mechanics_and_prompts.md` описує **застарілу** систему 2d50/DC{50..140}. Вміст суперечить поточному CLAUDE.md §5.2–5.3. Синхронізація обох skills-файлів під D&D 5e — окрема задача. До синхронізації — пріоритет має CLAUDE.md.