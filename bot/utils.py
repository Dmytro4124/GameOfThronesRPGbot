# bot/utils.py
import asyncio
from aiogram import Bot
from bot.menus import get_dynamic_menu


def _sanitize_markdown(text: str) -> str:
    """Видаляє непарні символи форматування, щоб не крашити парсер Telegram."""
    clean_text = text
    if clean_text.count('*') % 2 != 0:
        clean_text = clean_text.replace('*', '')
    if clean_text.count('_') % 2 != 0:
        clean_text = clean_text.replace('_', '')
    return clean_text


async def send_safe_message(bot: Bot, chat_id: int, text: str, reply_to_message_id: int = None,
                            reply_markup=None, edit_message=None, parse_mode: str | None = 'Markdown'):
    """Асинхронна універсальна функція для відправки повідомлень.

    parse_mode: 'Markdown' (default) або None для plain text (напр. інтро без LLM-розмітки).
    Коли parse_mode=None, _sanitize_markdown не застосовується — текст передається як є.
    """
    # Санітайзер потрібен лише якщо Telegram парситиме маркдаун
    safe_text = _sanitize_markdown(text) if parse_mode else text
    max_len = 4000

    # edit_text приймає тільки InlineKeyboardMarkup, але ми використовуємо ReplyKeyboardMarkup.
    # Тому видаляємо placeholder і надсилаємо нове повідомлення через send_message.
    if edit_message:
        try:
            await edit_message.delete()
        except Exception:
            pass
        edit_message = None

    if len(safe_text) <= max_len:
        try:
            await bot.send_message(
                chat_id, safe_text,
                reply_to_message_id=reply_to_message_id,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"⚠️ Safe Message Error (parse_mode={parse_mode}): {e}")
            await bot.send_message(chat_id, safe_text, reply_to_message_id=reply_to_message_id,
                                   reply_markup=reply_markup)
    else:
        parts = [safe_text[i:i + max_len] for i in range(0, len(safe_text), max_len)]
        for i, part in enumerate(parts):
            markup = reply_markup if i == len(parts) - 1 else None
            try:
                await bot.send_message(chat_id, part, parse_mode=parse_mode, reply_markup=markup)
            except Exception as e:
                print(f"⚠️ Safe Message Part Error: {e}")
                await bot.send_message(chat_id, part, reply_markup=markup)

            if i < len(parts) - 1:
                await asyncio.sleep(0.5)


async def send_game_response(bot: Bot, chat_id: int, text: str, suggested_actions: list = None,
                             reply_to_message_id: int = None, edit_message=None):
    """Відправляє хід з динамічними кнопками."""
    markup = get_dynamic_menu(suggested_actions)
    await send_safe_message(bot, chat_id, text, reply_to_message_id=reply_to_message_id,
                            reply_markup=markup, edit_message=edit_message)