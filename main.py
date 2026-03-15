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
# Підключаємо наші оновлені асинхронні модулі
from database.operations import load_lore_data, refresh_npc_database
from bot.handlers import router

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ініціалізація бота та диспетчера
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

# Підключаємо роутер з усіма ігровими командами
dp.include_router(router)

# Налаштування Webhook
WEBHOOK_PATH = f"/{TELEGRAM_TOKEN}"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Формат: https://your-render-app.onrender.com


async def on_startup(bot: Bot):
    """Виконується ПЕРЕД тим, як бот почне приймати повідомлення"""
    logger.info("⏳ Початок ініціалізації системи...")

    # Завантажуємо бази даних (тепер це не блокує цикл!)
    await refresh_npc_database()
    await load_lore_data()

    logger.info("✅ Всі бази завантажені та векторизовані!")

    if WEBHOOK_URL:
        # Режим продакшену
        await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info(f"🔗 Webhook встановлено: {WEBHOOK_URL}{WEBHOOK_PATH}")
    else:
        # Режим локальної розробки (Polling)
        logger.warning("⚠️ WEBHOOK_URL не знайдено, переходимо на Polling. Видаляю старі вебхуки...")
        # КРИТИЧНЕ ВИПРАВЛЕННЯ ТВОЄЇ ПОМИЛКИ:
        await bot.delete_webhook(drop_pending_updates=True)


async def on_shutdown(bot: Bot):
    """Коректне завершення з'єднань при вимкненні"""
    logger.info("🛑 Зупинка системи...")
    if WEBHOOK_URL:
        await bot.delete_webhook()
    await bot.session.close()


def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if WEBHOOK_URL:
        # Режим Webhook для продакшену (наприклад, Render)
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        )
        webhook_requests_handler.register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        port = int(os.environ.get("PORT", 8080))
        logger.info(f"🚀 Запуск веб-сервера aiohttp на порту {port}...")
        web.run_app(app, host="0.0.0.0", port=port)
    else:
        # Режим Polling для локальної розробки
        logger.info("🚀 Запуск у режимі Infinity Polling...")
        asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()