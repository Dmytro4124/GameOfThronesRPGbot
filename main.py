# main.py
import os
import time
import threading
import telebot
from flask import Flask, request

from config import TELEGRAM_TOKEN
from database.operations import load_lore_data, refresh_npc_database
from bot.handlers import register_handlers

# Ініціалізація бота
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Реєстрація всіх обробників (handlers)
register_handlers(bot)

# Ініціалізація Flask-сервера
app = Flask(__name__)


@app.route('/', methods=['GET'])
def home():
    return "Bot is alive and ready to conquer Westeros!"


# маршрут для Webhook
@app.route('/' + TELEGRAM_TOKEN, methods=['POST'])
def webhook():
    # Отримуємо оновлення від Telegram і передаємо його боту
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Forbidden', 403


def background_loader():
    """Функція для фонового завантаження важких баз та ШІ-моделей"""
    print("⏳ [Background] Початок завантаження лору та бази NPC...")
    refresh_npc_database()
    load_lore_data()
    print("✅ [Background] Всі бази та RAG моделі успішно завантажені!")


if __name__ == "__main__":
    print("🤖 Запуск системи... Valar Morghulis.")

    # 1. Запускаємо важкі процеси в окремому фоновому потоці, щоб не блокувати сервер!
    loader_thread = threading.Thread(target=background_loader)
    loader_thread.start()

    # 2. Отримуємо URL твого додатку на Render
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")

    if WEBHOOK_URL:
        # Режим Webhook для Render
        bot.remove_webhook()
        time.sleep(1)  # Пауза для уникнення лімітів API
        bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
        print(f"🔗 Webhook встановлено на: {WEBHOOK_URL}")

        # Запускаємо сервер (це відбудеться миттєво, і Render буде задоволений)
        port = int(os.environ.get("PORT", 8080))
        print(f"🚀 Запуск веб-сервера на порту {port}...")
        app.run(host='0.0.0.0', port=port)
    else:
        # Режим Polling для локального тестування
        print("⚠️ Змінну WEBHOOK_URL не знайдено. Запуск у режимі Polling...")
        bot.remove_webhook()
        bot.infinity_polling()