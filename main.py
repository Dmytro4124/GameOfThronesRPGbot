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


async def on_startup(bot: Bot):
    logger.info("⏳ Початок ініціалізації системи...")

    # 1. ОДРАЗУ підключаємо Webhook, щоб Telegram знав, що ми живі
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info(f"🔗 Webhook встановлено: {WEBHOOK_URL}{WEBHOOK_PATH}")
    else:
        logger.warning("⚠️ WEBHOOK_URL не знайдено, переходимо на Polling.")
        await bot.delete_webhook(drop_pending_updates=True)

    # 2. Швидке завантаження бази NPC (займає 1-2 секунди, не блокує сервер)
    await refresh_npc_database()

    # 3. КРИТИЧНО: Відправляємо векторизацію лору у ФОНОВУ задачу!
    # Інакше aiohttp сервер зависне на 5 хвилин і Telegram відкине вебхук.
    await asyncio.create_task(load_lore_data())

    logger.info("✅ Сервер готовий приймати повідомлення! (Векторизація лору триває у фоні)")


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
        logger.info("🚀 Запуск у режимі Infinity Polling...")
        asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()