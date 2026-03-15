# bot/handlers.py
import asyncio
import traceback
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.menus import get_dynamic_menu, get_main_menu
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

router = Router()
PROCESSED_MESSAGES = set()


@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("🗺️ *Зчитую карту Вестеросу...*", parse_mode='Markdown')
    regions = await get_unique_regions()

    if not regions:
        await message.answer("❌ Не знайдено регіонів у таблиці 'Доми'.")
        return

    builder = InlineKeyboardBuilder()
    for r in regions:
        builder.button(text=r, callback_data=f"reg_{r}")
    builder.adjust(2)

    user_sessions[message.chat.id] = {"state": "REGION_SELECT", "history": []}
    await message.answer("📍 *Оберіть регіон:*", reply_markup=builder.as_markup(), parse_mode='Markdown')


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

    await call.message.edit_text(text=f"🗺 Ви обрали регіон: *{region_name}*.\nОберіть Дім:", parse_mode="Markdown",
                                 reply_markup=builder.as_markup())


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

    await call.message.edit_text(text=f"🏰 Дім: *{house_name}*.\nКим ви хочете бути?", parse_mode="Markdown",
                                 reply_markup=builder.as_markup())


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
        await bot.send_message(chat_id, "❌ Помилка: контекст Дому втрачено. Почніть з /start")
        return

    house_data = await get_house_stats_data(house_name)
    full_profile = await generate_initial_stats(char_name, house_name, house_data)

    if full_profile:
        if await save_user_data(user_id, full_profile, char_name):

            if chat_id not in user_sessions:
                user_sessions[chat_id] = {}
            user_sessions[chat_id]['state'] = "GAME_ACTIVE"
            user_sessions[chat_id]['character_name'] = char_name
            user_sessions[chat_id]['history'] = []

            start_loc = full_profile.get("Поточне місцезнаходження", "Вестерос")
            hero_name = full_profile.get("Ім'я", char_name)

            async def initial_world_setup(loc, p_name, time_ctx):
                await background_canon_generation(context_text=time_ctx, excluded_name=p_name)
                await populate_contextual_npcs(loc, "Start of the game. Normal daily routine.", excluded_name=p_name)

            asyncio.create_task(initial_world_setup(start_loc, char_name, GAME_ERA_CONTEXT))

            stats_msg = f"✅ *Персонажа створено!*\n👤 **{hero_name}**\n📍 Локація: _{start_loc}_"
            await bot.send_message(chat_id, stats_msg, parse_mode='Markdown')

            await bot.send_chat_action(chat_id, action='typing')
            intro_story = await get_narrative_intro(full_profile)

            # Викликаємо головне меню як стартове
            await send_safe_message(bot, chat_id, intro_story, reply_markup=get_main_menu())
            await bot.send_message(chat_id, "⚔️ Ваш шлях починається...")

            user_sessions[chat_id]['history'].append({"role": "GM", "content": intro_story})
        else:
            await bot.send_message(chat_id, "❌ Помилка запису в базу даних.")
    else:
        await bot.send_message(chat_id, "❌ Помилка генерації профілю.")


@router.message(F.text == "📜 Профіль")
async def show_profile_handler(message: Message):
    chat_id = message.chat.id
    if chat_id not in user_sessions or user_sessions[chat_id].get('state') != "GAME_ACTIVE":
        await message.answer("Спершу почніть гру через /start")
        return

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
    log_text = user_sessions.get(chat_id, {}).get('last_debug_time', "❌ Ще не було зроблено жодного ходу.")
    await message.answer(log_text, parse_mode='Markdown')


@router.message(F.text == "🔄 Рестарт")
async def restart_request_handler(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так", callback_data="restart_confirm")
    builder.button(text="❌ Ні", callback_data="restart_cancel")
    builder.adjust(2)
    await message.answer("⚠️ Видалити персонажа?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("restart_"))
async def callback_restart_handler(call: CallbackQuery):
    chat_id = call.message.chat.id
    await call.message.edit_reply_markup(reply_markup=None)
    if call.data == "restart_confirm":
        await call.message.answer("💀 Старий світ зникає у темряві...")
        user_sessions[chat_id] = {}
        await start_handler(call.message)


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

    # Захист від порожніх повідомлень (фото, гіфки, стікери)
    if not user_text:
        return

    session = user_sessions.get(chat_id, {})
    state = session.get('state')

    if state == "WAITING_CUSTOM_NAME":
        await start_game_with_character(bot, chat_id, user_text)
        return

    if state == "GAME_ACTIVE":
        if msg_id in PROCESSED_MESSAGES: return
        PROCESSED_MESSAGES.add(msg_id)
        if len(PROCESSED_MESSAGES) > 100: PROCESSED_MESSAGES.pop()

        # Вирізаємо емодзі-префікс, якщо гравець обрав згенеровану дію з Reply-меню
        if user_text.startswith("👉 "):
            user_text = user_text[2:].strip()

        await bot.send_chat_action(chat_id, action='typing')

        try:
            # Отримуємо текст І список дій від рушія
            response_text, suggested_actions = await process_game_turn(chat_id, user_text)

            # Передаємо запропоновані дії у функцію відправки
            await send_game_response(bot, chat_id, response_text, suggested_actions, reply_to_message_id=msg_id)


        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(error_trace)
            error_msg = f"⚠️ ТЕХНІЧНА ПОМИЛКА:\n\n{error_trace[-1500:]}"
            await message.answer(error_msg)