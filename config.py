# config.py
import logging
import os
from dotenv import load_dotenv

# Завантажуємо змінні середовища
load_dotenv()

# Токени та ключі
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY_TEST = os.getenv("GEMINI_API_KEY_TEST")

# Версія бота (оновлюється вручну при релізах)
BOT_VERSION = "0.1.0"

# Пул тестових ключів для паралельного QA (GEMINI_API_KEY_TEST_1, _2, _3, ...)
# Fallback: якщо пул порожній, використовуємо основний тестовий ключ
GEMINI_API_KEYS_TEST = [
    v for k, v in sorted(os.environ.items())
    if k.startswith("GEMINI_API_KEY_TEST") and v
] or ([GEMINI_API_KEY_TEST] if GEMINI_API_KEY_TEST else [])
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Назви аркушів в Google Sheets
TAB_HOUSES = 'Доми'
TAB_CHARACTER = 'CharacterSheet'
TAB_KNOWLEDGE = 'KnowledgeBase'
TAB_USERS = 'Users_DB'
TAB_NPC = 'NPC_DB'

# Налаштування моделей Gemini: Main/Worker(+Censor)/GM_Logic/Narrator -> Flash-Lite
# Точний id Flash-Lite ОБОВ'ЯЗКОВО звірити в AI Studio. Пізніше перевикористовується для MODEL_NARRATOR_ALT_NAME.
FLASH_LITE_MODEL_ID = "gemini-3.5-flash-lite"
# ENV-rollback: MODEL_*_NAME=gemma-4-31b-it повертає стару модель без зміни коду
MODEL_MAIN_NAME = os.getenv("MODEL_MAIN_NAME", FLASH_LITE_MODEL_ID)            # утиліта (summarize, NPC gen, intro)
MODEL_WORKER_NAME = os.getenv("MODEL_WORKER_NAME", FLASH_LITE_MODEL_ID)        # Censor + Worker: механіка (кубики, DC, JSON)
MODEL_GM_LOGIC_NAME = os.getenv("MODEL_GM_LOGIC_NAME", FLASH_LITE_MODEL_ID)    # складна NPC логіка, стан світу
MODEL_NARRATOR_NAME = os.getenv("MODEL_NARRATOR_NAME", FLASH_LITE_MODEL_ID)    # художній текст; rollback: MODEL_NARRATOR_NAME=gemma-4-31b-it

# Narrator A/B (сліпе порівняння Gemma vs Flash-Lite). Вимкнено за замовчуванням.
# Повторний A/B з Gemma = MODEL_NARRATOR_NAME=gemma-4-31b-it + NARRATOR_AB_ENABLED=1.
# Якщо обидві моделі збігаються -- A/B автоматично вимкнено (порівнювати нічого).
MODEL_NARRATOR_ALT_NAME = os.getenv("MODEL_NARRATOR_ALT_NAME", FLASH_LITE_MODEL_ID)
_AB_FLAG = os.getenv("NARRATOR_AB_ENABLED", "0") == "1"
NARRATOR_AB_ENABLED = _AB_FLAG and MODEL_NARRATOR_NAME != MODEL_NARRATOR_ALT_NAME
if _AB_FLAG and not NARRATOR_AB_ENABLED:
    logging.getLogger(__name__).warning(
        "Narrator A/B disabled: NARRATOR_AB_ENABLED=1 but both narrator models are identical (%s). "
        "Set MODEL_NARRATOR_NAME=gemma-4-31b-it to re-run the A/B.",
        MODEL_NARRATOR_NAME,
    )
NARRATOR_AB_CHOICE_TTL = 3600  # сек; після цього pending-вибір вважається простроченим
NARRATOR_AB_LOG_PATH = os.getenv("NARRATOR_AB_LOG_PATH", "logs/narrator_ab.jsonl")
NARRATOR_AB_SINK = os.getenv("NARRATOR_AB_SINK", "both").strip().lower()  # file|sheets|both
if NARRATOR_AB_SINK not in ("file", "sheets", "both"):
    NARRATOR_AB_SINK = "both"

# Налаштування температури моделей
MODEL_MAIN_TEMP = 0.7   # Для генерації сюжету та креативних описів
MODEL_WORKER_TEMP = 0.1 # Для точного суддівства та парсингу JSON
MODEL_GM_LOGIC_TEMP = 0.1  # Детерміністична генерація JSON
MODEL_NARRATOR_TEMP = 0.7  # Креативний літературний вихід

# Інші константи
CREDENTIALS_FILE = 'credentials.json'

# Адміністраторська консоль
ADMIN_TELEGRAM_IDS: list = [494157543, 778186089, 444884375]  # замінити на реальний Telegram ID адміна
GODMODE_USERS: set = set()              # runtime-toggle: автокрит у кубиках
PUPPET_USERS: set = set()               # runtime-toggle: режим ляльковода (всі NPC лояльні)
EROTIC_USERS: set = set()              # runtime-toggle: еротичний режим (явні сексуальні описи)