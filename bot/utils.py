# bot/utils.py
import asyncio
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder


async def send_safe_message(bot: Bot, chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Асинхронна універсальна функція для відправки повідомлень.
    Автоматично розбиває довгі тексти на частини, не блокуючи Event Loop.
    """
    max_len = 4000
    if len(text) <= max_len:
        try:
            await bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, parse_mode='Markdown')
        except Exception as e:
            print(f"⚠️ Safe Message Error (Markdown): {e}")
            await bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
    else:
        parts = [text[i:i + max_len] for i in range(0, len(text), max_len)]
        for part in parts:
            try:
                await bot.send_message(chat_id, part, parse_mode='Markdown')
            except Exception as e:
                print(f"⚠️ Safe Message Part Error: {e}")
                await bot.send_message(chat_id, part)

            # КРИТИЧНО: Неблокуюча пауза замість time.sleep
            await asyncio.sleep(0.5)


async def send_game_response(bot: Bot, chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Асинхронно відправляє відповідь користувачу з кнопкою для перегляду технічних логів.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Тех. дані", callback_data="debug_stats")

    try:
        await bot.send_message(
            chat_id, text, reply_to_message_id=reply_to_message_id,
            reply_markup=builder.as_markup(), parse_mode='Markdown'
        )
    except Exception as e:
        print(f"⚠️ Markdown Error: {e}. Sending plain text.")
        await bot.send_message(
            chat_id, text, reply_to_message_id=reply_to_message_id,
            reply_markup=builder.as_markup()
        )