# config.py
import os
from dotenv import load_dotenv

# Завантажуємо змінні середовища
load_dotenv()

# Токени та ключі
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY_TEST = os.getenv("GEMINI_API_KEY_TEST")

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

# Налаштування моделей Gemini
MODEL_MAIN_NAME = 'gemma-3-27b-it'
MODEL_WORKER_NAME = 'gemma-3-4b-it'
MODEL_GM_LOGIC_NAME = 'gemma-3-27b-it'      # Логічний рушій (JSON стану світу)
MODEL_NARRATOR_NAME = 'gemma-3-27b-it'     # Письменник (художній текст)

# Налаштування температури моделей
MODEL_MAIN_TEMP = 0.7   # Для генерації сюжету та креативних описів
MODEL_WORKER_TEMP = 0.1 # Для точного суддівства та парсингу JSON
MODEL_GM_LOGIC_TEMP = 0.1  # Детерміністична генерація JSON
MODEL_NARRATOR_TEMP = 0.7  # Креативний літературний вихід

# Інші константи
CREDENTIALS_FILE = 'credentials.json'

# Адміністраторська консоль
ADMIN_TELEGRAM_IDS: list = [494157543]  # замінити на реальний Telegram ID адміна
GODMODE_USERS: set = set()              # runtime-toggle: автокрит у кубиках
PUPPET_USERS: set = set()               # runtime-toggle: режим ляльковода (всі NPC лояльні)