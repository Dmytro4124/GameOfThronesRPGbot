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
                            reply_markup=None, edit_message=None):
    """Асинхронна універсальна функція для відправки повідомлень."""
    safe_text = _sanitize_markdown(text)
    max_len = 4000

    if len(safe_text) <= max_len:
        if edit_message:
            try:
                await edit_message.edit_text(safe_text, parse_mode='Markdown', reply_markup=reply_markup)
            except Exception as e:
                print(f"⚠️ Edit Message Error (Markdown): {e}")
                await edit_message.edit_text(safe_text, reply_markup=reply_markup)
        else:
            try:
                await bot.send_message(
                    chat_id, safe_text,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            except Exception as e:
                print(f"⚠️ Safe Message Error (Markdown): {e}")
                await bot.send_message(chat_id, safe_text, reply_to_message_id=reply_to_message_id,
                                       reply_markup=reply_markup)
    else:
        parts = [safe_text[i:i + max_len] for i in range(0, len(safe_text), max_len)]
        for i, part in enumerate(parts):
            markup = reply_markup if i == len(parts) - 1 else None
            if i == 0 and edit_message:
                try:
                    await edit_message.edit_text(part, parse_mode='Markdown', reply_markup=markup)
                except Exception as e:
                    print(f"⚠️ Edit Message Part Error: {e}")
                    await edit_message.edit_text(part, reply_markup=markup)
            else:
                try:
                    await bot.send_message(chat_id, part, parse_mode='Markdown', reply_markup=markup)
                except Exception as e:
                    print(f"⚠️ Safe Message Part Error: {e}")
                    await bot.send_message(chat_id, part, reply_markup=markup)

            await asyncio.sleep(0.5)


async def send_game_response(bot: Bot, chat_id: int, text: str, suggested_actions: list = None,
                             reply_to_message_id: int = None, edit_message=None):
    """Відправляє хід з динамічними кнопками."""
    markup = get_dynamic_menu(suggested_actions)
    await send_safe_message(bot, chat_id, text, reply_to_message_id=reply_to_message_id,
                            reply_markup=markup, edit_message=edit_message)