# config.py
import os
from dotenv import load_dotenv

# Завантажуємо змінні середовища
load_dotenv()

# Токени та ключі
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY_TEST = os.getenv("GEMINI_API_KEY_TEST")
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

# Налаштування температури моделей
MODEL_MAIN_TEMP = 0.7   # Для генерації сюжету та креативних описів
MODEL_WORKER_TEMP = 0.1 # Для точного суддівства та парсингу JSON

# Інші константи
CREDENTIALS_FILE = 'credentials.json'   