# bot/handlers.py
from telebot import types
from threading import Thread

# Імпорти з нашої архітектури
from bot.menus import get_main_menu
from bot.utils import send_safe_message, send_game_response
from database.operations import (
    get_unique_regions, get_houses_by_region, get_house_stats_data,
    get_user_data, save_user_data
)
from core.world import (
    get_canon_characters, generate_initial_stats, get_narrative_intro,
    background_canon_generation, populate_contextual_npcs
)
from core.engine import process_game_turn, user_sessions
from core.prompts import GAME_ERA_CONTEXT

# Захист від подвійних повідомлень
PROCESSED_MESSAGES = set()


def register_handlers(bot):
    @bot.message_handler(commands=['start'])
    def start_handler(message):
        bot.send_message(message.chat.id, "🗺️ *Зчитую карту Вестеросу...*", parse_mode='Markdown')
        regions = get_unique_regions()

        if not regions:
            bot.send_message(message.chat.id, "❌ Не знайдено регіонів у таблиці 'Доми'.")
            return

        markup = types.InlineKeyboardMarkup(row_width=2)
        btns = [types.InlineKeyboardButton(r, callback_data=f"reg_{r}") for r in regions]
        markup.add(*btns)

        user_sessions[message.chat.id] = {"state": "REGION_SELECT", "history": []}
        bot.send_message(message.chat.id, "📍 *Оберіть регіон:*", reply_markup=markup, parse_mode='Markdown')

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reg_"))
    def handle_region_selection(call):
        chat_id = call.message.chat.id
        region_name = call.data.split("_")[1]

        if chat_id not in user_sessions: user_sessions[chat_id] = {}
        user_sessions[chat_id]['temp_region'] = region_name

        houses = get_houses_by_region(region_name)
        if not houses:
            bot.answer_callback_query(call.id, f"У регіоні {region_name} немає доступних домів!", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(h.strip()[:20], callback_data=f"house_{h.strip()[:20]}") for h in houses]
        markup.add(*buttons)
        markup.add(types.InlineKeyboardButton("⬅️ Назад до регіонів", callback_data="back_to_regions"))

        bot.edit_message_text(
            chat_id=chat_id, message_id=call.message.message_id,
            text=f"🗺 Ви обрали регіон: *{region_name}*.\nОберіть Дім:",
            parse_mode="Markdown", reply_markup=markup
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("house_"))
    def handle_house_selection(call):
        chat_id = call.message.chat.id
        house_name = call.data.split("_")[1]

        if chat_id not in user_sessions: user_sessions[chat_id] = {}
        user_sessions[chat_id]['house_name'] = house_name

        characters = get_canon_characters(house_name)
        markup = types.InlineKeyboardMarkup(row_width=1)

        for char in characters:
            markup.add(types.InlineKeyboardButton(char, callback_data=f"char_{char[:20]}"))

        markup.add(types.InlineKeyboardButton("✨ Створити свого...", callback_data="char_custom"))
        markup.add(types.InlineKeyboardButton("⬅️ Назад до списку Домів", callback_data="back_to_houses"))

        bot.edit_message_text(
            chat_id=chat_id, message_id=call.message.message_id,
            text=f"🏰 Дім: *{house_name}*.\nКим ви хочете бути?",
            parse_mode="Markdown", reply_markup=markup
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("char_"))
    def handle_character_selection(call):
        chat_id = call.message.chat.id
        selection = call.data.split("_")[1]

        bot.delete_message(chat_id, call.message.message_id)

        if selection == "custom":
            msg = bot.send_message(chat_id, "✍️ Як зватимуть вашого героя?")
            bot.register_next_step_handler(msg, lambda m: start_game_with_character(m.chat.id, m.text))
        else:
            char_name = selection
            bot.send_message(chat_id, f"⚔️ Ви обрали долю: *{char_name}*...", parse_mode='Markdown')
            start_game_with_character(chat_id, char_name)

    def start_game_with_character(chat_id, char_name):
        user_id = chat_id
        session = user_sessions[chat_id]
        house_name = session['house_name']
        house_data = get_house_stats_data(house_name)

        full_profile = generate_initial_stats(char_name, house_name, house_data)

        if full_profile:
            if save_user_data(user_id, full_profile, char_name):
                session['state'] = "GAME_ACTIVE"
                session['character_name'] = char_name
                session['history'] = []

                start_loc = full_profile.get('Поточне місцезнаходження', 'Westeros')

                def initial_world_setup(loc, p_name, time_ctx):
                    background_canon_generation(context_text=time_ctx, excluded_name=p_name)
                    populate_contextual_npcs(loc, "Start of the game. Normal daily routine.", excluded_name=p_name)
                    print("🏁 [WORLD READY] Світ повністю налаштований!")

                Thread(target=initial_world_setup, args=(start_loc, char_name, GAME_ERA_CONTEXT)).start()

                stats_msg = f"✅ *Персонажа створено!*\n👤 **{full_profile.get('Ім' + chr(39) + 'я')}**\n📍 Локація: _{start_loc}_"
                bot.send_message(chat_id, stats_msg, parse_mode='Markdown')

                bot.send_chat_action(chat_id, 'typing')
                intro_story = get_narrative_intro(full_profile)
                send_safe_message(bot, chat_id, intro_story)
                bot.send_message(chat_id, "⚔️ Ваш шлях починається...", reply_markup=get_main_menu())

                session['history'].append({"role": "GM", "content": intro_story})
            else:
                bot.send_message(chat_id, "❌ Помилка запису в базу даних.")
        else:
            bot.send_message(chat_id, "❌ Помилка генерації профілю.")

    @bot.message_handler(func=lambda m: m.text == "📜 Профіль")
    def show_profile_handler(message):
        chat_id = message.chat.id
        if chat_id not in user_sessions or user_sessions[chat_id].get('state') != "GAME_ACTIVE":
            bot.send_message(chat_id, "Спершу почніть гру через /start")
            return

        profile, _ = get_user_data(chat_id)
        if profile:
            text = (
                f"👤 *{profile.get('Ім' + chr(39) + 'я', 'Невідомий')}*\n"
                f"🏠 Дім: {profile.get('Дім')}\n📍 {profile.get('Поточне місцезнаходження')}\n"
                f"📅 {profile.get('Ігровий час', 'Невідомий час')}\n━━━━━━━━━━━━━━━━━━\n"
                f"❤️ Здоров'я: {profile.get('Здоров' + chr(39) + 'я')} | ⚡ Енергія: {profile.get('Енергія')}\n"
                f"⚔️ Бойові: {profile.get('Бойові навички')} | 🛡️ Військові: {profile.get('Військові навички')}\n"
                f"🍷 Інтрига: {profile.get('Інтрига')} | ⚖️ Управління: {profile.get('Управління')}\n"
                f"━━━━━━━━━━━━━━━━━━\n🗣 Репутація: {profile.get('Репутація (Рідний регіон)')}\n"
                f"💀 Вороги: {profile.get('Вороги')}"
            )
            bot.send_message(chat_id, text, parse_mode='Markdown')
        else:
            bot.send_message(chat_id, "❌ Не вдалося зчитати профіль.")

    @bot.message_handler(func=lambda m: m.text == "🎒 Інвентар")
    def show_inventory_handler(message):
        chat_id = message.chat.id
        profile, _ = get_user_data(chat_id)
        if profile:
            text = (
                f"🎒 *Ваш мішок:*\n\n💰 *Золото:* {profile.get('Особисте Золото', 0)} драконів\n"
                f"⚔️ *Зброя:* {profile.get('Зброя', '-')}\n🛡 *Броня:* {profile.get('Броня', '-')}\n"
                f"━━━━━━━━━━━━━━━━━━\n📦 *Речі:* {profile.get('Інвентар', 'Пусто')}"
            )
            bot.send_message(chat_id, text, parse_mode='Markdown')

    @bot.message_handler(func=lambda m: m.text == "🎲 Кинути кубик")
    def roll_dice_handler(message):
        chat_id = message.chat.id
        log_text = user_sessions.get(chat_id, {}).get('last_debug_time', "❌ Ще не було зроблено жодного ходу.")
        bot.send_message(chat_id, log_text, parse_mode='Markdown')

    @bot.message_handler(func=lambda m: m.text == "🆘 Допомога")
    def help_handler(message):
        bot.send_message(message.chat.id,
                         "📜 *Правила Гри:*\n1. Пишіть дії від першої особи.\n2. Використовуйте /start для перезапуску.",
                         parse_mode='Markdown')

    @bot.message_handler(func=lambda m: m.text == "🔄 Рестарт")
    def restart_request_handler(message):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Так", callback_data="restart_confirm"),
                   types.InlineKeyboardButton("❌ Ні", callback_data="restart_cancel"))
        bot.send_message(message.chat.id, "⚠️ Видалити персонажа?", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("restart_"))
    def callback_restart_handler(call):
        chat_id = call.message.chat.id
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        if call.data == "restart_confirm":
            bot.send_message(chat_id, "💀 Старий світ зникає у темряві...")
            user_sessions[chat_id] = {}
            start_handler(call.message)

    @bot.callback_query_handler(func=lambda call: call.data == "back_to_regions")
    def back_to_regions_handler(call):
        regions = get_unique_regions()
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(*[types.InlineKeyboardButton(r, callback_data=f"reg_{r}") for r in regions])
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text="📍 *Оберіть регіон:*", reply_markup=markup, parse_mode='Markdown')

    @bot.callback_query_handler(func=lambda call: call.data == "back_to_houses")
    def back_to_houses_handler(call):
        region_name = user_sessions.get(call.message.chat.id, {}).get('temp_region', 'Північ')
        houses = get_houses_by_region(region_name)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(*[types.InlineKeyboardButton(h, callback_data=f"house_{h}") for h in houses])
        markup.add(types.InlineKeyboardButton("⬅️ Назад до регіонів", callback_data="back_to_regions"))
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text=f"🗺 Регіон: *{region_name}*.\nОберіть Дім:", reply_markup=markup,
                              parse_mode='Markdown')

    @bot.callback_query_handler(func=lambda call: call.data == "debug_stats")
    def show_debug_stats(call):
        debug_info = user_sessions.get(call.message.chat.id, {}).get('last_debug_time', "Дані відсутні.")
        bot.answer_callback_query(call.id, text="Завантажую логи...", show_alert=False)
        bot.send_message(call.message.chat.id, debug_info, parse_mode='Markdown')

    @bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get('state') == "GAME_ACTIVE")
    def handle_game_turn(message):
        chat_id = message.chat.id
        msg_id = message.message_id

        if msg_id in PROCESSED_MESSAGES: return
        PROCESSED_MESSAGES.add(msg_id)
        if len(PROCESSED_MESSAGES) > 100: PROCESSED_MESSAGES.pop()

        user_text = message.text
        if user_text in ["📜 Профіль", "🎒 Інвентар", "🎲 Кинути кубик", "🆘 Допомога", "🔄 Рестарт"]: return

        bot.send_chat_action(chat_id, 'typing')

        try:
            response = process_game_turn(chat_id, user_text)
            send_game_response(bot, chat_id, response, reply_to_message_id=msg_id)
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Сталася помилка. Спробуйте ще раз.")