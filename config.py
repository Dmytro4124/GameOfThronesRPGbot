# config.py
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

# Налаштування моделей Gemini: Main/Worker(+Censor)/GM_Logic -> Flash-Lite, Narrator -> Gemma 4
# Точний id Flash-Lite ОБОВ'ЯЗКОВО звірити в AI Studio. Пізніше перевикористовується для MODEL_NARRATOR_ALT_NAME.
FLASH_LITE_MODEL_ID = "gemini-3.5-flash-lite"
# ENV-rollback: MODEL_*_NAME=gemma-4-31b-it повертає стару модель без зміни коду
MODEL_MAIN_NAME = os.getenv("MODEL_MAIN_NAME", FLASH_LITE_MODEL_ID)            # утиліта (summarize, NPC gen, intro)
MODEL_WORKER_NAME = os.getenv("MODEL_WORKER_NAME", FLASH_LITE_MODEL_ID)        # Censor + Worker: механіка (кубики, DC, JSON)
MODEL_GM_LOGIC_NAME = os.getenv("MODEL_GM_LOGIC_NAME", FLASH_LITE_MODEL_ID)    # складна NPC логіка, стан світу
MODEL_NARRATOR_NAME = 'gemma-4-31b-it'       # Dense flagship (художній текст), під A/B-тестом

# Narrator A/B (сліпе порівняння Gemma vs Flash-Lite). Вимкнено за замовчуванням.
NARRATOR_AB_ENABLED = os.getenv("NARRATOR_AB_ENABLED", "0") == "1"
MODEL_NARRATOR_ALT_NAME = os.getenv("MODEL_NARRATOR_ALT_NAME", FLASH_LITE_MODEL_ID)
NARRATOR_AB_CHOICE_TTL = 3600  # сек; після цього pending-вибір вважається простроченим
NARRATOR_AB_LOG_PATH = os.getenv("NARRATOR_AB_LOG_PATH", "logs/narrator_ab.jsonl")

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