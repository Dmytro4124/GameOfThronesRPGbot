# bot/utils.py
import time
from telebot import types

def send_safe_message(bot, chat_id, text, reply_to_message_id=None):
    """
    Універсальна функція для відправки повідомлень.
    Автоматично розбиває довгі тексти на частини.
    """
    max_len = 4000
    if len(text) <= max_len:
        try:
            bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, parse_mode='Markdown')
        except:
            bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
    else:
        parts = [text[i:i + max_len] for i in range(0, len(text), max_len)]
        for part in parts:
            try:
                bot.send_message(chat_id, part, parse_mode='Markdown')
            except:
                bot.send_message(chat_id, part)
            time.sleep(0.5)

def send_game_response(bot, chat_id, text, reply_to_message_id=None):
    """
    Відправляє відповідь користувачу з кнопкою для перегляду технічних логів.
    """
    markup = types.InlineKeyboardMarkup()
    debug_btn = types.InlineKeyboardButton("⚙️ Тех. дані", callback_data="debug_stats")
    markup.add(debug_btn)

    try:
        bot.send_message(
            chat_id, text, reply_to_message_id=reply_to_message_id,
            reply_markup=markup, parse_mode='Markdown'
        )
    except Exception as e:
        print(f"⚠️ Markdown Error: {e}. Sending plain text.")
        bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, reply_markup=markup)