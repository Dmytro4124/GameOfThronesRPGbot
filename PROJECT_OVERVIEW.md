# Огляд проєкту TelegramGameOfThronesBot

> Read-only аналіз проєкту для передачі контексту іншому ШІ-помічнику для подальшого планування архітектури агентів.

---

## 1. Дерево файлів (до 3 рівнів, ігноруючи `.venv`, `.git`, `__pycache__`, `.idea`, `.pytest_cache`)

```
TelegramGameOfThronesBot/
├── .claude/
│   ├── settings.local.json
│   └── skills/
│       ├── database_and_rag.md
│       └── mechanics_and_prompts.md
├── .env                            (існує, у .gitignore — вміст не читався)
├── .gitignore
├── CLAUDE.md
├── README.md                       (1 рядок: "# GameOfThronesRPGbot")
├── benchmark_models.py             (12 КБ — у .gitignore, бенчмарк моделей)
├── benchmark_turn.py               (6.7 КБ — у .gitignore, бенчмарк ходу)
├── config.py                       (47 рядків)
├── credentials.json                (Google Service Account — у .gitignore)
├── lore_embeddings.npy             (~10 МБ — закешовані вектори лору)
├── lore_hash.txt                   (32 байти — MD5 для інвалідації кешу)
├── main.py                         (94 рядки — точка входу)
├── qa_auto_test.py                 (53.5 КБ — QA-харнес для ходів)
├── qa_profiles.py                  (76.6 КБ — профілі тест-персонажів)
├── run_night_tests.py              (26.6 КБ — у .gitignore, нічне навантаження)
├── requirements.txt
├── test.py                         (5 КБ — НЕ стосується проєкту: HTTP-парсер вакансій Workable)
├── bot/
│   ├── __init__.py                 (пусто)
│   ├── handlers.py                 (501 рядок — aiogram-роутер, всі апдейти)
│   ├── menus.py                    (42 рядки — Reply-клавіатури)
│   └── utils.py                    (62 рядки — markdown-санітайзер, send_safe_message)
├── core/
│   ├── __init__.py                 (пусто)
│   ├── ai_client.py                (287 рядків — Gemini wrapper, JSON-парсер)
│   ├── cheats.py                   (571 рядок — 21 адмін-команда)
│   ├── engine.py                   (825 рядків — головний ігровий цикл)
│   ├── mechanics.py                (781 рядок — кубики, тренування, time/energy)
│   ├── prompts.py                  (1109 рядків — всі системні промпти)
│   ├── world.py                    (349 рядків — генерація стартового світу/NPC)
│   └── world_constants.py          (609 рядків — регіони/локації/сцени)
├── database/
│   ├── __init__.py                 (пусто)
│   ├── canon_npc.py                (1080 рядків — ~100 канонічних NPC хардкоднуто)
│   ├── operations.py               (800 рядків — Google Sheets + RAG)
│   └── sheets.py                   (55 рядків — gspread Singleton)
├── test/
│   ├── __init__.py                 (пусто)
│   ├── test_ai_parsing.py          (38 рядків — clean_and_parse_json)
│   ├── test_engine.py              (56 рядків — process_game_turn з mock-ами)
│   └── test_mechanics.py           (~280 рядків — apply_system_impacts, safe_int)
└── qa_logs/                        (порожня директорія)
```

**Топ файлів за обсягом коду:** `prompts.py` (1109), `canon_npc.py` (1080), `engine.py` (825), `operations.py` (800), `mechanics.py` (781), `world_constants.py` (609), `cheats.py` (571), `handlers.py` (501). Загалом основного коду — **~7 200 рядків** Python.

---

## 2. Стек і залежності

`requirements.txt`:

```
google-genai
gspread~=6.2.1
oauth2client~=4.1.3
python-dotenv~=1.2.1
numpy~=2.4.3
aiogram>=3.4.0
aiohttp>=3.9.0
aiosqlite>=0.20.0
# pyTelegramBotAPI~=4.30.0   (закоментовано)
# flask~=3.1.2               (закоментовано)
# telebot~=0.0.5             (закоментовано)
# google-api-python-client~=2.188.0   (закоментовано)
```

- **Python:** версія прямо не зафіксована (немає `pyproject.toml`/`Pipfile`/`runtime.txt`); `numpy~=2.4.3` фактично потребує Python ≥ 3.10. `.venv` побудована для Windows.
- **Telegram-бібліотека:** `aiogram >= 3.4.0` (фактично використовується v3 — є `Router`, `F`, `Dispatcher`, `DefaultBotProperties`, webhook через `aiohttp`). Старі бібліотеки (`pyTelegramBotAPI`, `telebot`, `flask`) залишені закоментованими — слід міграції.
- **БД:** Google Sheets через `gspread` + Service Account (`credentials.json` або `GOOGLE_CREDENTIALS_JSON`). ORM немає — патерн Singleton + сирі виклики, профіль гравця зберігається як **JSON-рядок у 3-й колонці**. `aiosqlite` присутній у requirements, але **жодного імпорту в коді не знайдено** — мертва залежність.
- **ШІ:** `google-genai` (новий SDK Gemini). Embedding-модель `gemini-embedding-2-preview`, generative — `gemma-4-31b-it` (всі 4 ролі вказують на цю модель — `MODEL_MAIN_NAME`, `MODEL_WORKER_NAME`, `MODEL_GM_LOGIC_NAME`, `MODEL_NARRATOR_NAME`). **Конфлікт із CLAUDE.md**, де описана гібридна 3-агентна система на `gemma-3-4b-it` + `gemma-3-27b-it` — потребує уточнення.
- **Інше:** `numpy` (RAG-вектори/евклідова відстань), `aiohttp` (webhook-сервер), `oauth2client` (вже deprecated, але потрібен `gspread` старим API), `python-dotenv` (env-файл).
- **HTTP-клієнт окремий:** немає (всі мережеві виклики йдуть через SDK Google або aiohttp).
- **Кеш:** немає Redis/Memcached. Локальний `numpy`-кеш векторів (`lore_embeddings.npy` + MD5-хеш в `lore_hash.txt`), in-memory `NPC_CACHE` та `LORE_CACHE` у `database/operations.py`, `user_sessions: dict` у `core/engine.py`.
- **Логування:** стандартний `logging` (basic config у `main.py`) + численні `print()` з emoji-префіксами (`🟢`, `🔴`, `⚠️`, `❌`, `✅`) в усіх модулях. Структурованого логування немає.
- **Тести:** `pytest` (видно з `test/test_*.py` та `.pytest_cache/`), у requirements **НЕ зафіксований** — інстальовується вручну.

---

## 3. Точка входу — `main.py` (повний вміст)

```python
# main.py
import os
import logging
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import TELEGRAM_TOKEN
from database.operations import load_lore_data, refresh_npc_database
from bot.handlers import router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
dp.include_router(router)

WEBHOOK_PATH = f"/{TELEGRAM_TOKEN}"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")


async def health_check(request):
    return web.Response(text="Bot is alive", status=200)


async def run_polling():
    port = int(os.getenv('PORT', 8080))
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Keep-alive сервер запущено на порту {port}")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


async def on_startup(bot: Bot):
    logger.info("⏳ Початок ініціалізації системи...")
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info(f"🔗 Webhook встановлено: {WEBHOOK_URL}{WEBHOOK_PATH}")
    else:
        logger.warning("⚠️ WEBHOOK_URL не знайдено, переходимо на Polling.")
        await bot.delete_webhook(drop_pending_updates=True)

    await refresh_npc_database()
    # КРИТИЧНО: векторизація лору — у фоновій задачі (5 хв), щоб не таймаутити webhook
    await asyncio.create_task(load_lore_data())
    logger.info("✅ Сервер готовий приймати повідомлення!")


async def on_shutdown(bot: Bot):
    logger.info("🛑 Зупинка системи...")
    if WEBHOOK_URL:
        await bot.delete_webhook()
    await bot.session.close()


def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if WEBHOOK_URL:
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_requests_handler.register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        port = int(os.environ.get("PORT", 8080))
        logger.info(f"🚀 Запуск веб-сервера aiohttp на порту {port}...")
        web.run_app(app, host="0.0.0.0", port=port)
    else:
        logger.info("🚀 Запуск у режимі Infinity Polling...")
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
```

Бот підтримує **обидва режими**: webhook (через aiohttp на `PORT`, шлях `/{TELEGRAM_TOKEN}`) і polling. Вибір — через наявність `WEBHOOK_URL`.

---

## 4. Архітектурні патерни

### Handlers
- **Один файл-роутер:** `bot/handlers.py` (501 рядок), всі апдейти на одному `Router()`. Без розбиття на feature-модулі.
- Команди/коллбеки: `/start`, кнопки `Профіль`, `Інвентар`, `Тех. дані`, `Рестарт`, callback-флоу вибору регіону → дому → персонажа, fallback-обробник `@router.message()` для довільного тексту як ігрового вводу.
- **Адмін-перехоплення:** будь-яка команда з `/`, надіслана з `chat_id ∈ ADMIN_TELEGRAM_IDS`, делегується в `core/cheats.py` (21 чит-команда) **до** перевірки сесії.

### FSM/стани
- **Власний наївний FSM** через словник `user_sessions: dict` у `core/engine.py` (in-memory, гине при рестарті). Стани: `REGION_SELECT`, `WAITING_CUSTOM_NAME`, `INITIALIZING`, `GAME_ACTIVE`. Поля: `state`, `character_name`, `history`, `temp_region`, `house_name`, `action_intents`, `prev_legal_npc_names`, `last_debug_time`, `last_ai_data`.
- **Стандартний aiogram FSM (StorageContext) НЕ використовується.** Бекенд для FSM — Python-словник, без Redis/Memory storage.
- **Авто-recovery після рестарту:** якщо сесії немає, але `get_user_data(chat_id)` повертає профіль — сесія відновлюється у `GAME_ACTIVE` (handlers.py:386-395).

### Зберігання даних користувача
- **Google Sheets `Users_DB`:** `[user_id, char_name, json_str]` — серіалізований профіль у 3-й колонці.
- Є `delete_user_data` (повний скид при `/restart`) і кеш мертвих NPC у пам'яті.

### Конфігурація
- `config.py` — звичайний модуль, читає `os.getenv()` через `python-dotenv` (`load_dotenv()`). **Без pydantic-settings.** Пара констант (`MODEL_*_NAME`, `MODEL_*_TEMP`, назви табів `TAB_*`) хардкодом у файлі.
- Runtime-toggles тримаються в `set` об'єктах у `config.py`: `GODMODE_USERS`, `PUPPET_USERS`, `EROTIC_USERS` — модифікуються чит-командами безпосередньо.
- `ADMIN_TELEGRAM_IDS = [494157543]` — захардкоджено в коді.

### Middleware/фільтри/декоратори
- **Власних middleware немає.** Використовуються лише вбудовані фільтри aiogram (`Command`, `F.text`, `F.data.startswith`).
- **Власні захисти:**
  - `PROCESSED_MESSAGES: set` — дедуплікація `message_id` (handlers.py:26, 408).
  - `active_processing: set` — User Lock проти паралельних ходів одного гравця (handlers.py:27, 423).
  - `keep_typing()` — фоновий таск, що шле "typing" статус кожні 4 сек.
- **Сторонній цензор/валідатор:** `core.mechanics.validate_action()` — LLM-based pre-check дії перед резолвом механіки.

### Загальний потік ходу (`process_game_turn` у `core/engine.py`)
1. Profile load + якісна перебудова статів (HP/енергія/золото → текстові мітки).
2. RAG: `get_relevant_context()` (Gemini embedding + numpy евклід).
3. NPC-фільтр 3 рівнів (Регіон → Локація → Сцена) + dead/absent diff.
4. Censor (`validate_action`) + механіка (`resolve_action_mechanics` — кубики 2d50, DC, виявлення training).
5. `apply_system_impacts` (час, енергія, золото, скіли).
6. **GM_Logic виклик** → JSON `{director_notes, npc_updates, suggested_actions, companion_npcs}`.
7. **Narrator виклик** (з опційним streaming через `asyncio.Queue` для прогресивного оновлення Telegram-повідомлення; вимикається в еротичному режимі).
8. Background save: профіль, NPC-зміни, репутація, memory anchor, sliding-window сумаризація історії (стиснення коли > 20 ходів).

---

## 5. Контентний шар (специфіка Гри Престолів)

Контент про ГП розкладений на **5 джерел**:

| Шар | Файл / місце | Формат | Обсяг |
|---|---|---|---|
| Канонічні NPC (хардкод) | `database/canon_npc.py` | Список Python-словників | **100 NPC** (`"Name":` matches) |
| Регіони/локації/сцени | `core/world_constants.py` | `LOCATION_TO_REGION: dict`, `LOCATION_DESCRIPTIONS: dict`, `LOCATION_SCENES: dict[str, dict[str, list[str]]]` | ~21 регіон, ~80 локацій, ∞ сцен |
| Лор (RAG) | Google Sheets `KnowledgeBase` (TAB_KNOWLEDGE) | Рядки `{Ключові слова, Інформація}` + закешовані ембедінги в `lore_embeddings.npy` | **~800 записів** (за розміром .npy ≈ 10 МБ / 3072 dims × 4 байти) — потребує уточнення точного числа |
| Live NPC | Google Sheets `NPC_DB` (TAB_NPC) | Рядки з полями `Name, Location, Scene, Description, Character, Goal, Secrets, Relation_Player, Reputation_Score, Memory_Anchor, Status, Is_Canon, Inventory, Region` | Динамічна (заповнюється з canon_npc.py + LLM) |
| Доми | Google Sheets `Доми` (TAB_HOUSES) | Рядки `{Регіон, Рід, ...}` | За кількістю Великих/малих домів — потребує уточнення |
| Era-context (хардкод) | `core/prompts.py` `GAME_ERA_CONTEXT` | Багаторядковий Python-string | ~30 рядків лор-довідки про 298 рік В.Е. |

**Приклад одного канонічного NPC** (`database/canon_npc.py:137-145`):

```python
{
    "Location": "Вінтерфел", "Scene": "Великий Зал", "Name": "Еддард Старк",
    "Description": "Чоловік із суворим довгим обличчям, темно-сірими очима...",
    "Character": "Чесний, справедливий, позбавлений політичної хитрості, відданий честі.",
    "Goal": "Керувати Північчю та гідно прийняти короля Роберта.",
    "Secrets": "Джон Сноу — син Ліанни Старк і Рейгара Таргарієна.",
    "Relation_Player": "Нейтральна", "Memory_Anchor": "-",
    "Relation_NPCs": "Любить Кейтлін, відданий Роберту Баратеону.",
    "Status": "Active", "Is_Canon": "TRUE", "Inventory": "Пусто"
}
```

**RAG-механізм** (`database/operations.py:208-285`): на старті бот завантажує `KnowledgeBase`, обчислює MD5 вмісту, порівнює з `lore_hash.txt`. Якщо хеш збігається — підвантажує `lore_embeddings.npy`. Інакше — батчами по 90 робить `embed_content` (з 60-сек паузами для квот) і перезаписує кеш. Пошук — евклідова відстань, відсікання `< 1.2`, top-3.

---

## 6. Команди та функціональність бота

### Користувацькі (Telegram, з `bot/handlers.py`)

| Тригер | Функція | Що робить |
|---|---|---|
| `/start` | `start_handler` | Перевіряє наявність профілю → пропонує "Продовжити" або "Почати заново". Якщо нема — показує вибір регіону. |
| Callback `resume_game` | `resume_game_handler` | Відновлює сесію `GAME_ACTIVE` за існуючим профілем. |
| Callback `reg_*` | `handle_region_selection` | Зберігає тимчасовий регіон, показує доми. |
| Callback `house_*` | `handle_house_selection` | Зберігає дім, показує канонічних персонажів + опцію "Створити свого". |
| Callback `char_*` | `handle_character_selection` | Старт кастомного імені або генерація профілю + інтро. |
| Callback `back_to_regions` / `back_to_houses` | відповідні хендлери | Навігація назад у меню вибору. |
| Callback `restart_confirm` / `restart_cancel` | `callback_restart_*` | Видалення профілю та запуск нового світу. |
| Reply-кнопка `📜 Профіль` | `show_profile_handler` | Відображає основні стати з Google Sheets + інвентар (золото, зброя, броня, речі). |
| Reply-кнопка `🗺 Карта` / `/map` | `cmd_map` | Карта сцен поточної локації гравця. |
| Reply-кнопка `⚙️ Тех. дані` | `show_debug_stats` | Тайминги останнього ходу. |
| Reply-кнопка `🔄 Рестарт` | `restart_request_handler` | Підтвердження видалення персонажа. |
| Будь-який текст | `handle_general_messages` | Головний хід гри: censor → mechanics → GM_Logic → Narrator → save. |

### Адмінські cheat-команди (`core/cheats.py`, доступні лише `ADMIN_TELEGRAM_IDS`)

`/heal`, `/sethp`, `/setenergy`, `/setgold`, `/settitle`, `/sethouse`, `/additem`, `/delitem`, `/clearinv`, `/addskill`, `/setskill`, `/tp`, `/addtime`, `/tpnpc`, `/setrep`, `/killnpc`, `/delnpc`, `/cleanscene`, `/godmode` (auto-крит у кубиках), `/debug`, `/puppet` (всі NPC лояльні), `/erotic` (NSFW режим — вимикає safety_settings + streaming), `/thoughts` (показує `_thoughts_log` від include_thoughts).

---

## 7. Тести

- **Папка:** `test/` (одна, не `tests/`).
- **Фреймворк:** `pytest` + `unittest.mock`.
- **Файли:** `test_ai_parsing.py` (5 тестів `clean_and_parse_json`), `test_engine.py` (1 тест `process_game_turn` з повним моком), `test_mechanics.py` (`safe_int`, `apply_system_impacts`, енергія).
- **Покриття:** ~3 тестових файли проти **17 основних** Python-файлів — покриття дуже низьке. Жоден тест не покриває `prompts.py`, `world.py`, `cheats.py`, `handlers.py`, `operations.py`, `world_constants.py`.
- **QA-харнес окремо:** `qa_auto_test.py` (53 КБ) + `qa_profiles.py` (76 КБ) + `run_night_tests.py` (26 КБ) — це **не unit-тести**, а інтеграційний end-to-end "нічний прогон" з профілями типу `standard`, `movement_tester`, `npc_resurrection`, `combat_stress_tester`, `chaos_engineer`, `diplomat_romantic`, `shadow_mage` (з `settings.local.json`). Логи мали б іти в `qa_logs/`, але директорія порожня.

---

## 8. Інфраструктура

- **Dockerfile:** **відсутній**.
- **docker-compose:** **відсутній**.
- **CI (.github/workflows):** **відсутній**.
- **Procfile / heroku.yml / fly.toml / render.yaml:** **відсутні**.
- **Деплой-скрипти:** не знайдено.
- **`main.py`** реагує на `WEBHOOK_URL` і `PORT` з ENV — типовий патерн для Heroku/Render/Railway/Fly.io, але конкретний таргет деплою у репо ніяк не задокументований. Потребує уточнення.
- **Environment:** `.env` (TELEGRAM_TOKEN, GEMINI_API_KEY, GEMINI_API_KEY_TEST, GEMINI_API_KEY_TEST_1..N, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON, WEBHOOK_URL, PORT).

---

## 9. `CLAUDE.md` і `.claude/`

### `CLAUDE.md` — **існує** (5.5 КБ)
Перші абзаци:
> Telegram Game of Thrones RPG Bot - Core Developer Guidelines
>
> ## 1. Архітектура та Стек
> - Мова: Python 3
> - Асинхронність: asyncio (КРИТИЧНО: всі виклики до Gemini API та Google Sheets є синхронними під капотом і ПОВИННІ загортатися в asyncio.to_thread()).
> - База даних: Google Sheets (бібліотека gspread).
> - ШІ (Гібридний роутинг на Gemma): gemma-3-4b-it (Worker, GM_Logic), gemma-3-27b-it (виключно Narrator), gemini-embedding-2-preview.

Далі — карта проекту, опис 3-агентної системи, жорсткі правила кодування, базові механіки (2d50, скілпоїнти ≥96 / ≤5, енергія 0-100), маршрутизація на skills.

⚠️ **Розбіжність з кодом:** `CLAUDE.md` зазначає Gemma 3, тоді як `config.py` встановлює всі ролі на `gemma-4-31b-it`. Або CLAUDE.md застарів, або `config.py` ще в процесі переходу — потребує уточнення.

⚠️ Друга розбіжність: `CLAUDE.md` каже "Енергія 0-100", але код у `engine.py:191` працює зі шкалою 0-1000 (`DEFAULT_ENERGY = 1000`, пороги 200/400/600/800/1000). Скіл `mechanics_and_prompts.md` теж пише 0-100. Документація відстала від коду.

### `.claude/`
- `settings.local.json` — список allowed Bash/Read/WebSearch паттернів для Claude Code (стандартний дозвільний список, нічого специфічного для архітектури).
- `skills/database_and_rag.md` (2.3 КБ) — правила роботи з gspread/RAG/NPC anti-hallucination.
- `skills/mechanics_and_prompts.md` (2.7 КБ) — правила 2d50/DC/енергії та обов'язкові ключі JSON-промптів (`step1_tot_brainstorming`, `step2_adversarial_validation`, `verdict_text`, `updates`, `director_notes`, `npc_updates`, `suggested_actions`).

---

## 10. Технічний борг і дивні місця

1. **`test.py` у корені — стороннє сміття.** Це HTTP-парсер вакансій з Workable (мовами EN/DK/UA/RU/PT/DE/NL/IT/SE/PL/CZ/HU/RO/FR/ES) — абсолютно не стосується RPG-бота. Очевидно, потрапив сюди випадково. Можна видалити.

2. **`README.md` пустий** — лише один рядок `# GameOfThronesRPGbot`.

3. **Розбіжність CLAUDE.md ↔ code:**
   - Моделі: 3-агентна гібридна (Gemma 3 4B + 27B) у CLAUDE.md vs все Gemma 4 31B в `config.py`.
   - Енергія 0-100 vs 0-1000.
   - У `requirements.txt` закоментовано pyTelegramBotAPI/flask/telebot — сліди старішої архітектури, не зачищені.

4. **`aiosqlite` у requirements, але не використовується ніде.**

5. **Закоментовані fallback-блоки.** Наприклад, `test_engine.py` мокає `core.engine.Thread`, якого в новому `engine.py` вже немає (там `asyncio.create_task`) — тест застарів і, ймовірно, упав би при запуску. Потребує уточнення (тести не запускалися в межах цього аналізу).

6. **`PROCESSED_MESSAGES.pop()` без аргументу** (handlers.py:410) — `set.pop()` видаляє довільний елемент. Виглядає як LRU-обмеження, але насправді це невпорядкований pop. Працює як "обмеження розміру 100", але навмисно це чи баг — неясно.

7. **`active_processing: set` як lock** — на in-memory словнику + чекаються сирі mutation. Якщо процес впаде під час ходу, lock не звільниться, і користувач не зможе ходити до рестарту бота. Жодних `try/finally` навколо нього на самому верхньому рівні немає (тільки навколо внутрішнього блоку).

8. **Конкурентність:** `_thoughts_log: list` — модуль-глобальна змінна без lock-у. `clear_thoughts()` викликається на старті кожного ходу, але якщо ходи паралельні (різні чати) — лог змішається.

9. **Один `Router()`** для всього + один файл `handlers.py` на 501 рядок — рости буде боляче.

10. **Дублювання даних NPC:** канонічні NPC дублюються в `database/canon_npc.py` (хардкод) і у Google Sheets `NPC_DB` (заповнюється з нього через `background_canon_generation`). Sheet — це source of truth для runtime, але стартовий завантажувач — Python-список. Ризик розсинхрону.

11. **Великі промпти inline у `prompts.py`:** 1109 рядків, переважно багаторядкові f-strings із вкладеним JSON-описом. Складно діффити та рефакторити.

12. **Жодного `TODO`/`FIXME`-коментаря** у коді не знайдено (єдиний матч на "TODO" — у docstring `_fix_invalid_escapes` як приклад валідного escape). Тобто борг не задокументований у коді — він "у головах".

13. **Бенчмарки/нічні тести у gitignore:** `benchmark_models.py`, `benchmark_turn.py`, `run_night_tests.py` навмисно не комітяться, але присутні локально (26 КБ + 12 КБ + 7 КБ). Якщо це частина флоу — варто документувати в README.

14. **`credentials.json` присутній у репо як файл, але відмічений у .gitignore.** Тобто у git-історії його не повинно бути, але для аудитора варто перевірити, чи він не потрапив у попередні комміти. Потребує уточнення.

---

## Що потребує уточнення (відкриті питання)

- Точна кількість записів у `KnowledgeBase` (Google Sheets) — за розміром `.npy` оцінено ~800.
- Поточний таргет деплою (Render? Heroku? Fly.io?) — у коді немає підказки.
- Чи код реально використовує Gemma 4 31B чи `MODEL_*_NAME` константи переписуються в runtime — варто перевірити логи.
- Чи `test_engine.py` досі проходить — мок на `core.engine.Thread` виглядає застарілим.
- Чи `credentials.json` колись потрапляв у git-історію.

---

## Підсумок для агентного планування

Це **Telegram-бот RPG у світі Гри Престолів** з нетиповою архітектурою:
- aiogram 3 + aiohttp webhook/polling.
- **Power-house — це promt-engineering на Gemma**: 3 агенти (Worker → GM_Logic → Narrator) у послідовному pipeline, плюс Censor + RAG.
- Стан зберігається в Google Sheets як serialized JSON; кеші — in-memory; вектори — локальний numpy.
- Контент про ГП розпорошений: hardcoded canon NPCs (~100), 21 регіон/80 локацій (constants), ~800 lore-записів у KnowledgeBase, 30+ рядків era-context у prompts.
- Низьке покриття unit-тестами, натомість великий QA-харнес для E2E.
- Дві ключові точки розширення під агентську архітектуру: (1) `core/engine.py:process_game_turn` (центральний оркестратор), (2) `core/prompts.py` (всі ролі агентів).
- Болючі точки для майбутніх агентів: відсутність структурованого логування, відсутність Redis/persistent state, конкурентність на in-memory словниках, синхронні `gspread` виклики (загортаються в `asyncio.to_thread`).
