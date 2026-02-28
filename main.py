# main.py
import telebot
from config import TELEGRAM_TOKEN
from database.operations import load_lore_data, refresh_npc_database
from bot.handlers import register_handlers
from server import keep_alive

# Ініціалізація бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Реєстрація всіх обробників (handlers)
register_handlers(bot)

if __name__ == "__main__":
    print("🤖 Запуск системи... Valar Morghulis.")

    # 1. Завантажуємо дані з Google Sheets у кеш
    load_lore_data()
    refresh_npc_database()

    # 2. Запускаємо веб-сервер для утримання бота онлайн
    keep_alive()

    # 3. Стартуємо бота
    print("✅ Бот успішно запущено!")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"❌ Помилка polling: {e}")