# bot/handlers.py
import asyncio
import random
import traceback
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.menus import get_dynamic_menu, get_main_menu
from bot.utils import send_safe_message, send_game_response
from database.operations import (
    get_unique_regions, get_houses_by_region, get_house_stats_data,
    get_user_data, save_user_data, delete_user_data, clear_npc_cache
)
from core.world import (
    get_canon_characters, generate_initial_stats, get_narrative_intro,
    background_canon_generation, populate_contextual_npcs
)
from core.engine import process_game_turn, user_sessions
from config import ADMIN_TELEGRAM_IDS


router = Router()
PROCESSED_MESSAGES = set()
active_processing: set = set()

PLACEHOLDER_PHRASES = [
    "🎲 Майстер підземель кидає кубики...",
    "⏳ Гортаємо літописи Вестеросу...",
    "⚔️ Оцінюємо реакцію персонажів...",
]


async def keep_typing(bot: Bot, chat_id: int):
    """Підтримує статус 'typing' кожні 4 секунди до скасування таски."""
    while True:
        await bot.send_chat_action(chat_id, action="typing")
        await asyncio.sleep(4)


@router.message(Command("start"))
async def start_handler(message: Message):
    chat_id = message.chat.id

    # 1. РОЗУМНИЙ СТАРТ: Перевіряємо, чи є в гравця збереження в базі
    profile, _ = await get_user_data(chat_id)
    if profile:
        char_name = profile.get("Ім'я", "Герой")
        builder = InlineKeyboardBuilder()
        builder.button(text="▶️ Продовжити гру", callback_data="resume_game")
        builder.button(text="💀 Почати заново", callback_data="restart_confirm")
        builder.adjust(1)
        await message.answer(
            f"⚔️ Вітаю знову, **{char_name}**!\nУ вас є збережений прогрес. Бажаєте продовжити?",
            reply_markup=builder.as_markup(),
            parse_mode='Markdown'
        )
        return

    # 2. Якщо збережень немає — стандартна генерація
    await message.answer("🗺️ *Зчитую карту Вестеросу...*", parse_mode='Markdown')
    regions = await get_unique_regions()

    if not regions:
        await message.answer("❌ Не знайдено регіонів у таблиці 'Доми'.")
        return

    builder = InlineKeyboardBuilder()
    for r in regions:
        builder.button(text=r, callback_data=f"reg_{r}")
    builder.adjust(2)

    user_sessions[chat_id] = {"state": "REGION_SELECT", "history": []}
    await message.answer("📍 *Оберіть регіон:*", reply_markup=builder.as_markup(), parse_mode='Markdown')


@router.callback_query(F.data == "resume_game")
async def resume_game_handler(call: CallbackQuery, bot: Bot):
    """Обробка кнопки 'Продовжити гру' після /start"""
    chat_id = call.message.chat.id
    profile, _ = await get_user_data(chat_id)

    if profile:
        user_sessions[chat_id] = {
            "state": "GAME_ACTIVE",
            "character_name": profile.get("Ім'я", "Герой"),
            "history": []
        }
        await call.message.delete()
        await send_safe_message(bot, chat_id, "🔄 *Зв'язок зі світом відновлено. Що робите далі?*",
                                reply_markup=get_main_menu())
    else:
        await call.message.answer("❌ Збереження не знайдено.")


@router.callback_query(F.data.startswith("reg_"))
async def handle_region_selection(call: CallbackQuery):
    chat_id = call.message.chat.id
    region_name = call.data.split("_")[1]

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {}
    user_sessions[chat_id]['temp_region'] = region_name

    houses = await get_houses_by_region(region_name)
    builder = InlineKeyboardBuilder()
    for h in houses:
        house_clean = h.strip()[:20]
        builder.button(text=house_clean, callback_data=f"house_{house_clean}")
    builder.button(text="⬅️ Назад до регіонів", callback_data="back_to_regions")
    builder.adjust(2)

    await call.message.edit_text(
        text=f"🗺 Ви обрали регіон: *{region_name}*.\nОберіть Дім:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("house_"))
async def handle_house_selection(call: CallbackQuery):
    chat_id = call.message.chat.id
    house_name = call.data.split("_")[1]

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {}
    user_sessions[chat_id]['house_name'] = house_name

    characters = await get_canon_characters(house_name)
    builder = InlineKeyboardBuilder()
    for char in characters:
        builder.button(text=char, callback_data=f"char_{char[:20]}")
    builder.button(text="✨ Створити свого...", callback_data="char_custom")
    builder.button(text="⬅️ Назад до списку Домів", callback_data="back_to_houses")
    builder.adjust(1)

    await call.message.edit_text(
        text=f"🏰 Дім: *{house_name}*.\nКим ви хочете бути?",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("char_"))
async def handle_character_selection(call: CallbackQuery, bot: Bot):
    chat_id = call.message.chat.id
    selection = call.data.split("_")[1]
    await call.message.delete()

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {}

    if selection == "custom":
        await call.message.answer("✍️ Як зватимуть вашого героя?")
        user_sessions[chat_id]['state'] = "WAITING_CUSTOM_NAME"
    else:
        await call.message.answer(f"⚔️ Ви обрали долю: *{selection}*...", parse_mode='Markdown')
        await start_game_with_character(bot, chat_id, selection)


async def start_game_with_character(bot: Bot, chat_id: int, char_name: str):
    user_id = chat_id
    session = user_sessions.get(chat_id, {})
    house_name = session.get('house_name')

    if not house_name:
        await bot.send_message(chat_id, "❌ Помилка: контекст Дому втрачено. Натисніть /start")
        return

    house_data = await get_house_stats_data(house_name)
    full_profile = await generate_initial_stats(char_name, house_name, house_data)

    if full_profile:
        if await save_user_data(user_id, full_profile, char_name):

            if chat_id not in user_sessions:
                user_sessions[chat_id] = {}
            user_sessions[chat_id]['state'] = "INITIALIZING"  # Блокуємо ігрові дії до готовності світу
            user_sessions[chat_id]['character_name'] = char_name
            user_sessions[chat_id]['history'] = []

            start_loc = full_profile.get("Поточне місцезнаходження", "Вестерос")
            hero_name = full_profile.get("Ім'я", char_name)

            async def initial_world_setup(loc, p_name):
                await background_canon_generation(excluded_name=p_name)
                await populate_contextual_npcs(loc, "Start of the game. Normal daily routine.", excluded_name=p_name)
                # Розблокуємо ПІСЛЯ повної готовності світу
                if chat_id in user_sessions:
                    user_sessions[chat_id]['state'] = "GAME_ACTIVE"

            task = asyncio.create_task(initial_world_setup(start_loc, char_name))

            def _on_world_setup_done(t):
                if t.exception():
                    print(f"❌ [WORLD SETUP FAILED] {t.exception()}")
                    if chat_id in user_sessions:
                        user_sessions[chat_id]['state'] = "GAME_ACTIVE"

            task.add_done_callback(_on_world_setup_done)

            stats_msg = f"✅ *Персонажа створено!*\n👤 **{hero_name}**\n📍 Локація: _{start_loc}_"
            await bot.send_message(chat_id, stats_msg, parse_mode='Markdown')

            await bot.send_chat_action(chat_id, action='typing')
            intro_data = await get_narrative_intro(full_profile)

            narrative = intro_data.get("narrative_text", "")
            action_prompt = intro_data.get("action_prompt", "")
            raw_suggested = intro_data.get("suggested_actions", [])

            button_texts = []
            intents_map = {}
            for item in raw_suggested:
                if isinstance(item, dict):
                    btn = str(item.get("button", "...")).strip()[:40]
                    intent = str(item.get("intent", btn)).strip()
                else:
                    btn = str(item).strip()[:40]
                    intent = btn
                button_texts.append(btn)
                intents_map[btn] = intent

            user_sessions.setdefault(chat_id, {})["action_intents"] = intents_map

            display_text = narrative
            if action_prompt:
                display_text += f"\n\n_{action_prompt}_"

            markup = get_dynamic_menu(button_texts) if button_texts else get_main_menu()
            await send_safe_message(bot, chat_id, display_text, reply_markup=markup)
            await bot.send_message(chat_id, "⚔️ Ваш шлях починається...")

            user_sessions[chat_id]['history'].append({"role": "GM", "content": display_text})
        else:
            await bot.send_message(chat_id, "❌ Помилка запису в базу даних.")
    else:
        await bot.send_message(chat_id, "❌ Помилка генерації профілю.")


@router.message(F.text == "📜 Профіль")
async def show_profile_handler(message: Message):
    chat_id = message.chat.id

    # Видалено жорстку перевірку user_sessions, тепер профіль можна подивитися навіть якщо сесія скинулась
    profile, _ = await get_user_data(chat_id)
    if profile:
        char_name = profile.get("Ім'я", "Невідомий")
        health_val = profile.get("Здоров'я", 100)
        energy_val = profile.get("Енергія", 100)
        combat = profile.get("Бойові навички", 0)
        military = profile.get("Військові навички", 0)
        intrigue = profile.get("Інтрига", 0)
        manage = profile.get("Управління", 0)
        rep = profile.get("Репутація (Рідний регіон)", 0)
        enemies = profile.get("Вороги", "Немає")
        house = profile.get("Дім", "Невідомий")
        loc = profile.get("Поточне місцезнаходження", "Невідомо")
        time_val = profile.get("Ігровий час", "Невідомий час")

        text = (
            f"👤 *{char_name}*\n"
            f"🏠 Дім: {house}\n📍 {loc}\n"
            f"📅 {time_val}\n━━━━━━━━━━━━━━━━━━\n"
            f"❤️ Здоров'я: {health_val} | ⚡ Енергія: {energy_val}\n"
            f"⚔️ Бойові: {combat} | 🛡️ Військові: {military}\n"
            f"🍷 Інтрига: {intrigue} | ⚖️ Управління: {manage}\n"
            f"━━━━━━━━━━━━━━━━━━\n🗣 Репутація: {rep}\n"
            f"💀 Вороги: {enemies}"
        )
        await message.answer(text, parse_mode='Markdown')
    else:
        await message.answer("Спершу почніть гру через /start")


@router.message(F.text == "🎒 Інвентар")
async def show_inventory_handler(message: Message):
    chat_id = message.chat.id
    profile, _ = await get_user_data(chat_id)
    if profile:
        gold = profile.get("Особисте Золото", 0)
        weapon = profile.get("Зброя", "-")
        armor = profile.get("Броня", "-")
        inv = profile.get("Інвентар", "Пусто")

        text = (
            f"🎒 *Ваш мішок:*\n\n💰 *Золото:* {gold} драконів\n"
            f"⚔️ *Зброя:* {weapon}\n🛡 *Броня:* {armor}\n"
            f"━━━━━━━━━━━━━━━━━━\n📦 *Речі:* {inv}"
        )
        await message.answer(text, parse_mode='Markdown')


@router.message(F.text == "⚙️ Тех. дані")
async def show_debug_stats(message: Message):
    chat_id = message.chat.id
    log_text = user_sessions.get(chat_id, {}).get('last_debug_time',
                                                  "❌ Логи зберігаються лише для поточної активної сесії (до перезапуску сервера). Зробіть хід.")
    await message.answer(log_text, parse_mode='Markdown')


@router.message(F.text == "🔄 Рестарт")
async def restart_request_handler(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так", callback_data="restart_confirm")
    builder.button(text="❌ Ні", callback_data="restart_cancel")
    builder.adjust(2)
    await message.answer("⚠️ Видалити персонажа назавжди?", reply_markup=builder.as_markup())


@router.callback_query(F.data == "restart_cancel")
async def callback_restart_cancel_handler(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Рестарт скасовано. Гра продовжується.")


@router.callback_query(F.data == "restart_confirm")
async def callback_restart_confirm_handler(call: CallbackQuery):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("💀 Старий світ зникає у темряві...")

    # 1. Блокуємо ігрові дії під час ініціалізації
    user_sessions[chat_id] = {"state": "INITIALIZING", "history": []}

    # 2. Видаляємо старий профіль з Google Sheets
    await delete_user_data(user_id)

    # 3. Очищуємо NPC-кеш (NPC_DB буде перезаписана при створенні персонажа)
    clear_npc_cache()

    # 4. Показуємо вибір регіону
    regions = await get_unique_regions()
    builder = InlineKeyboardBuilder()
    for r in regions: builder.button(text=r, callback_data=f"reg_{r}")
    builder.adjust(2)

    user_sessions[chat_id]["state"] = "REGION_SELECT"
    await call.message.answer("📍 *Оберіть регіон:*", reply_markup=builder.as_markup(), parse_mode='Markdown')


@router.callback_query(F.data == "back_to_regions")
async def back_to_regions_handler(call: CallbackQuery):
    regions = await get_unique_regions()
    builder = InlineKeyboardBuilder()
    for r in regions: builder.button(text=r, callback_data=f"reg_{r}")
    builder.adjust(2)
    await call.message.edit_text(text="📍 *Оберіть регіон:*", reply_markup=builder.as_markup(), parse_mode='Markdown')


@router.callback_query(F.data == "back_to_houses")
async def back_to_houses_handler(call: CallbackQuery):
    region_name = user_sessions.get(call.message.chat.id, {}).get('temp_region', 'Північ')
    houses = await get_houses_by_region(region_name)
    builder = InlineKeyboardBuilder()
    for h in houses: builder.button(text=h, callback_data=f"house_{h}")
    builder.button(text="⬅️ Назад до регіонів", callback_data="back_to_regions")
    builder.adjust(2)
    await call.message.edit_text(text=f"🗺 Регіон: *{region_name}*.\nОберіть Дім:", reply_markup=builder.as_markup(),
                                 parse_mode='Markdown')


@router.message()
async def handle_general_messages(message: Message, bot: Bot):
    chat_id = message.chat.id
    msg_id = message.message_id
    user_text = message.text

    if not user_text:
        return

    # --- ADMIN CHEAT INTERCEPTOR (до User Lock і session recovery) ---
    if user_text.startswith('/') and chat_id in ADMIN_TELEGRAM_IDS:
        from core.cheats import handle_cheat_command
        await handle_cheat_command(message, bot)
        return
    # -----------------------------------------------------------------

    session = user_sessions.get(chat_id)

    # ❗ СИСТЕМА АВТО-ВІДНОВЛЕННЯ ❗
    # Якщо бот перезапустився на сервері, оперативна пам'ять порожня.
    if not session:
        profile, _ = await get_user_data(chat_id)
        if profile:
            # Гравець існує в БД! Відновлюємо сесію і дозволяємо хід
            user_sessions[chat_id] = {
                "state": "GAME_ACTIVE",
                "character_name": profile.get("Ім'я", "Герой"),
                "history": []
            }
            session = user_sessions[chat_id]
        else:
            # Це новий користувач, який не натиснув /start
            await message.answer("❄️ Зима близько. Натисніть /start, щоб почати гру.")
            return

    state = session.get('state')

    if state == "WAITING_CUSTOM_NAME":
        await start_game_with_character(bot, chat_id, user_text)
        return

    if state == "GAME_ACTIVE":
        if msg_id in PROCESSED_MESSAGES: return
        PROCESSED_MESSAGES.add(msg_id)
        if len(PROCESSED_MESSAGES) > 100: PROCESSED_MESSAGES.pop()

        display_intent = None
        if user_text.startswith("👉 "):
            stripped = user_text[2:].strip()
            session = user_sessions.get(chat_id, {})
            intents_map = session.get("action_intents", {})
            resolved_intent = intents_map.get(stripped, stripped)
            if resolved_intent != stripped:
                display_intent = resolved_intent
            user_text = resolved_intent

        # КРОК 1: User Lock — блокуємо паралельні ходи одного гравця
        if chat_id in active_processing:
            await message.answer("⏳ Зачекайте, ваш попередній хід ще обробляється Майстром...")
            return
        active_processing.add(chat_id)

        try:
            # КРОК 3: Тематична заглушка (буде замінена на фінальну відповідь)
            temp_msg = await message.reply(random.choice(PLACEHOLDER_PHRASES))

            # КРОК 2: Фоновий typing на весь час обробки
            typing_task = asyncio.create_task(keep_typing(bot, chat_id))
            try:
                response_text, suggested_actions = await process_game_turn(chat_id, user_text)
                if display_intent:
                    response_text = f"🗣️ _{display_intent}_\n\n{response_text}"
                await send_game_response(bot, chat_id, response_text, suggested_actions,
                                         edit_message=temp_msg)
            except Exception as e:
                error_trace = traceback.format_exc()
                print(error_trace)
                try:
                    await temp_msg.edit_text("⚠️ Технічна помилка. Спробуйте ще раз.", parse_mode=None)
                except Exception:
                    await bot.send_message(chat_id, "⚠️ Технічна помилка. Спробуйте ще раз.")
            finally:
                typing_task.cancel()

        finally:
            active_processing.discard(chat_id)