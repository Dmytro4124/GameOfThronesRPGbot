# bot/handlers.py
import asyncio
import copy
import io
import json
import logging
import random
import time
import traceback
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.menus import get_dynamic_menu, get_main_menu, build_ability_preview_keyboard, build_point_buy_keyboard, build_asi_picker_keyboard
from bot.utils import send_safe_message, send_game_response
from database.operations import (
    get_unique_regions, get_houses_by_region, get_house_stats_data,
    get_user_data, save_user_data, delete_user_data,
    clear_npc_cache, delete_user_npc_sheet,
)
from core.world import (
    get_canon_characters, generate_initial_stats, get_narrative_intro,
    background_canon_generation, populate_contextual_npcs, build_fallback_intro
)
from core.intro_cache import get_cached_intro, set_cached_intro
from core.world_constants import format_player_map
from core.engine import process_game_turn, user_sessions
from config import ADMIN_TELEGRAM_IDS, EROTIC_USERS, BOT_VERSION, MODEL_MAIN_NAME
from bot.help_text import HELP_TEXT, ADMIN_HELP_TEXT


logger = logging.getLogger(__name__)

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


@router.message(Command("version"))
async def cmd_version(message: Message, bot: Bot):
    """Показує версію бота та поточну LLM-модель. Доступна всім."""
    text = (
        f"🤖 *Версія бота:* `{BOT_VERSION}`\n"
        f"🧠 *Модель LLM:* `{MODEL_MAIN_NAME}`"
    )
    await send_safe_message(bot, message.chat.id, text)


@router.message(Command("help"))
@router.message(F.text == "📖 Інструкція")
async def cmd_help(message: Message, bot: Bot):
    await send_safe_message(bot, message.chat.id, HELP_TEXT)


@router.message(Command("adminhelp"))
async def cmd_adminhelp(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_TELEGRAM_IDS:
        await send_safe_message(bot, message.chat.id, "🔒 Команда недоступна.")
        return
    await send_safe_message(bot, message.chat.id, ADMIN_HELP_TEXT)


@router.message(Command("speed"))
async def cmd_speed(message: Message):
    chat_id = message.chat.id
    log_text = user_sessions.get(chat_id, {}).get(
        'last_debug_time',
        "❌ Логи зберігаються лише для поточної активної сесії (до перезапуску сервера). Зробіть хід."
    )
    await message.answer(log_text, parse_mode=None)


@router.message(Command("map"))
@router.message(F.text == "🗺 Карта")
async def cmd_map(message: Message, bot: Bot):
    """Показує карту сцен поточної локації гравця."""
    chat_id = message.chat.id
    profile, _ = await get_user_data(chat_id)
    if not profile:
        await send_safe_message(bot, chat_id, "Спершу почніть гру через /start")
        return
    location = profile.get("Поточне місцезнаходження", "")
    scene = profile.get("Поточна сцена", "")
    text = format_player_map(location, scene)
    await send_safe_message(bot, chat_id, text)


@router.callback_query(F.data == "resume_game")
async def resume_game_handler(call: CallbackQuery, bot: Bot):
    """Обробка кнопки 'Продовжити гру' після /start"""
    chat_id = call.message.chat.id
    profile, _ = await get_user_data(chat_id)

    if profile:
        user_sessions[chat_id] = {
            "state": "GAME_ACTIVE",
            "character_name": profile.get("Ім'я", "Герой"),
            "history": [],
            "npc_cache": {},
            "dead_npc_names": set(),
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
    """Generate base profile (apply_heritage=False) and show ability-preview message.

    The player then chooses to Accept the auto-rolled stats or Redistribute via
    point-buy.  Profile is NOT saved here — saving happens in the Accept/Confirm
    callback handlers after heritage bonuses are applied.
    """
    user_id = chat_id
    session = user_sessions.get(chat_id, {})
    house_name = session.get('house_name')

    if not house_name:
        await bot.send_message(chat_id, "❌ Помилка: контекст Дому втрачено. Натисніть /start")
        return

    house_data = await get_house_stats_data(house_name)
    # apply_heritage=False: base scores 8-15 returned; heritage applied later
    base_profile = await generate_initial_stats(char_name, house_name, house_data, apply_heritage=False)

    if not base_profile:
        await bot.send_message(chat_id, "❌ Помилка генерації профілю.")
        return

    # Store both copies in session; auto_roll_profile is the Cancel target.
    # Use copy.deepcopy so that point-buy mutations to temp_profile['ability_scores']
    # do not corrupt auto_roll_profile (both start as the same base dict).
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {}
    user_sessions[chat_id]['temp_profile'] = base_profile
    user_sessions[chat_id]['auto_roll_profile'] = copy.deepcopy(base_profile)
    user_sessions[chat_id]['state'] = "WAITING_ABILITY_PREVIEW"
    user_sessions[chat_id]['character_name'] = char_name
    user_sessions[chat_id]['history'] = []

    # Build the preview message
    preview_text = _build_ability_preview_text(base_profile)
    await bot.send_message(
        chat_id,
        preview_text,
        parse_mode='Markdown',
        reply_markup=build_ability_preview_keyboard(),
    )


def _build_ability_preview_text(profile: dict) -> str:
    """Format a concise ability-score preview with heritage bonus hint."""
    from core.dnd_heritages import HERITAGES
    from core.dnd_core import ability_modifier

    heritage_name = profile.get("heritage", "")
    char_name = profile.get("Ім'я", "Герой")
    char_class = profile.get("class", "")
    scores = profile.get("ability_scores", {})

    # Heritage bonus description
    heritage_bonus_hint = ""
    if heritage_name in HERITAGES:
        hdef = HERITAGES[heritage_name]
        bonuses = hdef.ability_bonuses
        parts = []
        for key, val in bonuses.items():
            if key.startswith("any+"):
                parts.append(f"+{val} до будь-якої здібності (на вибір)")
            else:
                parts.append(f"+{val} {key}")
        heritage_bonus_hint = ", ".join(parts) if parts else "без бонусів"
    else:
        heritage_bonus_hint = "невідоме походження"

    _AB_LABELS = {
        "STR": "Сила",
        "DEX": "Спритність",
        "CON": "Витривалість",
        "INT": "Інтелект",
        "WIS": "Мудрість",
        "CHA": "Харизма",
    }

    lines = [
        f"⚔️ *{char_name}* — {char_class}",
        "",
        "📊 *Авто-розкид характеристик:*",
    ]
    for ab, label in _AB_LABELS.items():
        score = scores.get(ab, 8)
        mod = ability_modifier(score)
        lines.append(f"  {label} ({ab}): *{score}* ({mod:+d})")

    lines += [
        "",
        f"🏛 *Походження:* {heritage_name}",
        f"   Буде додано: _{heritage_bonus_hint}_",
        "",
        "Прийняти авто-розкид або перерозподілити 27 очок вручну?",
    ]
    return "\n".join(lines)


async def _finalise_character_and_start(bot: Bot, chat_id: int, full_profile: dict) -> None:
    """Save profile and launch world-setup + intro.

    Extracted from the original start_game_with_character so both the
    ability_accept and pb_confirm callbacks share the same path.

    Precondition: full_profile already has heritage bonuses applied and
    recompute_ac() and recompute_hp() already called by the caller.
    """
    user_id = chat_id
    char_name = full_profile.get("Ім'я", "")

    if not await save_user_data(user_id, full_profile, char_name):
        await bot.send_message(chat_id, "❌ Помилка запису в базу даних.")
        return

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {}
    user_sessions[chat_id]['state'] = "INITIALIZING"
    user_sessions[chat_id]['character_name'] = char_name
    # history already set by start_game_with_character; keep it
    user_sessions[chat_id].setdefault('history', [])
    # Clean up point-buy temp keys
    user_sessions[chat_id].pop('temp_profile', None)
    user_sessions[chat_id].pop('auto_roll_profile', None)

    start_loc = full_profile.get("Поточне місцезнаходження", "Вестерос")
    hero_name = full_profile.get("Ім'я", char_name)
    profile_region = full_profile.get("Регіон", "")
    profile_class = full_profile.get("class", "")
    profile_heritage = full_profile.get("heritage", "")

    # Background world setup (canon NPC + contextual NPC)
    async def initial_world_setup(loc, p_name):
        try:
            await background_canon_generation(user_id, excluded_name=p_name)
            await populate_contextual_npcs(user_id, loc, "Start of the game. Normal daily routine.", excluded_name=p_name)
        finally:
            if chat_id in user_sessions:
                user_sessions[chat_id]['state'] = "GAME_ACTIVE"

    task = asyncio.create_task(initial_world_setup(start_loc, char_name))

    def _on_world_setup_done(t):
        if t.exception():
            print(f"[WORLD SETUP FAILED] {t.exception()}")
            if chat_id in user_sessions:
                user_sessions[chat_id]['state'] = "GAME_ACTIVE"

    task.add_done_callback(_on_world_setup_done)

    stats_msg = f"✅ *Персонажа створено!*\n👤 **{hero_name}**\n📍 Локація: _{start_loc}_"
    await bot.send_message(chat_id, stats_msg, parse_mode='Markdown')

    # Intro: cache hit → instant; cache miss → fallback + background LLM
    cached_intro_text = get_cached_intro(profile_class, profile_heritage, profile_region)

    if cached_intro_text:
        await send_safe_message(bot, chat_id, cached_intro_text, reply_markup=get_main_menu(), parse_mode=None)
        user_sessions.setdefault(chat_id, {})["action_intents"] = {}
        user_sessions[chat_id]['history'].append({"role": "GM", "content": cached_intro_text})
    else:
        fallback_text = build_fallback_intro(full_profile)
        placeholder_msg = await bot.send_message(
            chat_id,
            fallback_text + "\n\n_Готую персоналізований вступ..._",
            reply_markup=get_main_menu(),
            parse_mode='Markdown',
        )
        user_sessions.setdefault(chat_id, {})["action_intents"] = {}
        user_sessions[chat_id]['history'].append({"role": "GM", "content": fallback_text})

        async def _bg_intro_task():
            try:
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

                display_text = narrative
                if action_prompt:
                    display_text += f"\n\n_{action_prompt}_"

                if display_text:
                    await set_cached_intro(profile_class, profile_heritage, profile_region, display_text)
                    markup = get_dynamic_menu(button_texts) if button_texts else get_main_menu()
                    try:
                        await placeholder_msg.delete()
                    except Exception:
                        pass
                    try:
                        await bot.send_message(chat_id, display_text, reply_markup=markup, parse_mode=None)
                        if chat_id in user_sessions:
                            user_sessions[chat_id]["action_intents"] = intents_map
                            if user_sessions[chat_id].get('history'):
                                user_sessions[chat_id]['history'][-1] = {"role": "GM", "content": display_text}
                    except Exception as e_send:
                        print(f"[bg intro] send_message failed: {e_send}")
            except Exception as e:
                print(f"[bg intro] Failed (using fallback): {e}")
                try:
                    await placeholder_msg.delete()
                except Exception:
                    pass
                try:
                    await bot.send_message(chat_id, fallback_text, reply_markup=get_main_menu(), parse_mode=None)
                except Exception:
                    pass

        asyncio.create_task(_bg_intro_task())

    await bot.send_message(chat_id, "⚔️ Ваш шлях починається...")


# ---------------------------------------------------------------------------
# Point-buy / ability-preview callback handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "noop")
async def callback_noop(call: CallbackQuery):
    """Non-interactive label button — silently acknowledge."""
    await call.answer()


@router.callback_query(F.data == "ability_accept")
async def callback_ability_accept(call: CallbackQuery, bot: Bot):
    """Player accepts the auto-rolled stats.  Apply heritage bonuses and save."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_PREVIEW":
        await call.answer("Ця кнопка більше не активна.")
        return

    try:
        from core.dnd_heritages import apply_heritage_bonuses
        from core.dnd_engine import recompute_ac, recompute_hp

        profile = session.get('temp_profile')
        if not profile:
            await call.answer("❌ Профіль не знайдено. Почніть /start заново.")
            return

        heritage_name = profile.get("heritage", "")
        # Apply heritage bonuses to the base profile
        profile = apply_heritage_bonuses(profile, heritage_name, ability_choices=None)
        recompute_ac(profile)
        recompute_hp(profile)

        # Remove the preview inline keyboard
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await call.answer("Характеристики прийнято!")
        await _finalise_character_and_start(bot, chat_id, profile)
    finally:
        # Ensure temp keys are cleaned even on exception (also cleaned inside
        # _finalise_character_and_start on success, but we guard against crash).
        if chat_id in user_sessions:
            user_sessions[chat_id].pop('temp_profile', None)
            user_sessions[chat_id].pop('auto_roll_profile', None)
            # If the state transition to INITIALIZING/GAME_ACTIVE never happened
            # (e.g. Sheets save failed), release the stuck preview state so the
            # player is not locked out until /start.
            stuck_state = user_sessions[chat_id].get('state')
            if stuck_state in ("WAITING_ABILITY_PREVIEW", "WAITING_ABILITY_REDISTRIBUTION"):
                user_sessions[chat_id].pop('state', None)
                await bot.send_message(
                    chat_id,
                    "⚠️ Виникла помилка під час збереження персонажа. Спробуйте /start ще раз.",
                )


@router.callback_query(F.data == "ability_redistribute")
async def callback_ability_redistribute(call: CallbackQuery):
    """Player chose to redistribute via point-buy.  Reset to all-8 and show editor."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_PREVIEW":
        await call.answer("Ця кнопка більше не активна.")
        return

    try:
        from core.character_creation import point_buy_initial, point_buy_remaining, POINT_BUY_BUDGET

        profile = session.get('temp_profile')
        if not profile:
            await call.answer("❌ Профіль не знайдено. Почніть /start заново.")
            return

        # Reset ability scores to point-buy floor
        profile['ability_scores'] = point_buy_initial()
        user_sessions[chat_id]['temp_profile'] = profile
        user_sessions[chat_id]['state'] = "WAITING_ABILITY_REDISTRIBUTION"

        scores = profile['ability_scores']
        remaining = point_buy_remaining(scores)
        kb = build_point_buy_keyboard(scores, remaining)

        heritage_name = profile.get("heritage", "")
        editor_header = (
            f"✏️ *Розподіл очок*\n"
            f"Походження: {heritage_name} (бонуси буде додано після підтвердження)\n\n"
            f"Розподіліть {POINT_BUY_BUDGET} очок між характеристиками:"
        )

        await call.message.edit_text(editor_header, parse_mode='Markdown', reply_markup=kb)
        await call.answer()
    except Exception as e:
        logger.error(f"[PB REDISTRIBUTE] {type(e).__name__}: {e}")
        await call.answer("Помилка. Спробуйте ще раз.")


@router.callback_query(F.data.startswith("pb_inc_"))
async def callback_pb_inc(call: CallbackQuery):
    """Increment an ability score by 1 (point-buy)."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_REDISTRIBUTION":
        await call.answer()
        return

    ability = call.data[len("pb_inc_"):]  # e.g. "STR"

    try:
        from core.character_creation import point_buy_can_increment, point_buy_remaining

        profile = session.get('temp_profile')
        if not profile:
            await call.answer()
            return

        scores = profile['ability_scores']
        if not point_buy_can_increment(scores, ability):
            await call.answer()
            return

        scores[ability] += 1
        profile['ability_scores'] = scores
        user_sessions[chat_id]['temp_profile'] = profile

        remaining = point_buy_remaining(scores)
        kb = build_point_buy_keyboard(scores, remaining)
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await call.answer()
    except Exception as e:
        logger.error(f"[PB INC] {ability}: {type(e).__name__}: {e}")
        await call.answer()


@router.callback_query(F.data.startswith("pb_dec_"))
async def callback_pb_dec(call: CallbackQuery):
    """Decrement an ability score by 1 (point-buy)."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_REDISTRIBUTION":
        await call.answer()
        return

    ability = call.data[len("pb_dec_"):]  # e.g. "STR"

    try:
        from core.character_creation import point_buy_can_decrement, point_buy_remaining

        profile = session.get('temp_profile')
        if not profile:
            await call.answer()
            return

        scores = profile['ability_scores']
        if not point_buy_can_decrement(scores, ability):
            await call.answer()
            return

        scores[ability] -= 1
        profile['ability_scores'] = scores
        user_sessions[chat_id]['temp_profile'] = profile

        remaining = point_buy_remaining(scores)
        kb = build_point_buy_keyboard(scores, remaining)
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await call.answer()
    except Exception as e:
        logger.error(f"[PB DEC] {ability}: {type(e).__name__}: {e}")
        await call.answer()


@router.callback_query(F.data == "pb_confirm")
async def callback_pb_confirm(call: CallbackQuery, bot: Bot):
    """Confirm point-buy allocation: apply heritage, recompute AC, save, start game."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_REDISTRIBUTION":
        await call.answer("Ця кнопка більше не активна.")
        return

    try:
        from core.character_creation import point_buy_remaining
        from core.dnd_heritages import apply_heritage_bonuses
        from core.dnd_engine import recompute_ac, recompute_hp

        profile = session.get('temp_profile')
        if not profile:
            await call.answer("❌ Профіль не знайдено. Почніть /start заново.")
            return

        scores = profile['ability_scores']
        remaining = point_buy_remaining(scores)

        # Defensive check: keyboard already gates this, but verify in handler too
        if remaining != 0:
            await call.answer(f"Спочатку розподіліть усі очки. Залишок: {remaining}")
            return

        heritage_name = profile.get("heritage", "")
        profile = apply_heritage_bonuses(profile, heritage_name, ability_choices=None)
        recompute_ac(profile)
        recompute_hp(profile)

        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await call.answer("Характеристики збережено!")
        await _finalise_character_and_start(bot, chat_id, profile)
    finally:
        if chat_id in user_sessions:
            user_sessions[chat_id].pop('temp_profile', None)
            user_sessions[chat_id].pop('auto_roll_profile', None)
            # If the state transition to INITIALIZING/GAME_ACTIVE never happened
            # (e.g. Sheets save failed), release the stuck redistribution state so
            # the player is not locked out until /start.
            stuck_state = user_sessions[chat_id].get('state')
            if stuck_state in ("WAITING_ABILITY_PREVIEW", "WAITING_ABILITY_REDISTRIBUTION"):
                user_sessions[chat_id].pop('state', None)
                await bot.send_message(
                    chat_id,
                    "⚠️ Виникла помилка під час збереження персонажа. Спробуйте /start ще раз.",
                )


@router.callback_query(F.data == "pb_reset")
async def callback_pb_reset(call: CallbackQuery):
    """Reset point-buy scores back to all-8."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_REDISTRIBUTION":
        await call.answer()
        return

    try:
        from core.character_creation import point_buy_initial, point_buy_remaining

        profile = session.get('temp_profile')
        if not profile:
            await call.answer()
            return

        profile['ability_scores'] = point_buy_initial()
        user_sessions[chat_id]['temp_profile'] = profile

        scores = profile['ability_scores']
        remaining = point_buy_remaining(scores)
        kb = build_point_buy_keyboard(scores, remaining)
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await call.answer("Скинуто до початкових значень.")
    except Exception as e:
        logger.error(f"[PB RESET] {type(e).__name__}: {e}")
        await call.answer()


@router.callback_query(F.data == "pb_cancel")
async def callback_pb_cancel(call: CallbackQuery):
    """Cancel point-buy and return to the auto-roll preview."""
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id, {})

    if session.get('state') != "WAITING_ABILITY_REDISTRIBUTION":
        await call.answer()
        return

    try:
        auto_roll = session.get('auto_roll_profile')
        if not auto_roll:
            await call.answer()
            return

        # Restore temp_profile to the original auto-rolled base scores.
        # Deep-copy so that a subsequent "Перерозподілити" cannot mutate auto_roll_profile.
        user_sessions[chat_id]['temp_profile'] = copy.deepcopy(auto_roll)
        user_sessions[chat_id]['state'] = "WAITING_ABILITY_PREVIEW"

        preview_text = _build_ability_preview_text(auto_roll)
        await call.message.edit_text(
            preview_text,
            parse_mode='Markdown',
            reply_markup=build_ability_preview_keyboard(),
        )
        await call.answer()
    except Exception as e:
        logger.error(f"[PB CANCEL] {type(e).__name__}: {e}")
        await call.answer()


# ---------------------------------------------------------------------------
# ASI picker — helper + callback handlers (B2–B5)
# ---------------------------------------------------------------------------

_EMPTY_ASI_USED: dict = {"STR": 0, "DEX": 0, "CON": 0, "INT": 0, "WIS": 0, "CHA": 0}


async def _check_and_trigger_asi(bot: Bot, chat_id: int) -> None:
    """Post-turn hook: if profile has asi_pending=True, set WAITING_ASI_CHOICE state
    and send the picker keyboard.  Called from handle_general_messages after
    process_game_turn completes successfully (B2).

    Fast path (99% of turns): engine.py caches asi_pending in user_sessions after
    apply_dnd_impacts.  If False/missing → return immediately without a Sheets read.
    Slow path (level-up turn): cache is True → fetch profile once from Sheets.
    """
    from core.dnd_classes import GOT_CLASSES

    # Fast-path: skip Sheets read when engine reported no pending ASI this turn.
    if not user_sessions.get(chat_id, {}).get('asi_pending'):
        return

    profile, _ = await get_user_data(chat_id)
    if not profile or not profile.get("asi_pending"):
        return

    # Build default suggestion from class primary_abilities
    class_name = profile.get("class", "")
    asi_used = dict(_EMPTY_ASI_USED)  # start zeroed
    try:
        if class_name and class_name in GOT_CLASSES:
            primary = GOT_CLASSES[class_name].primary_abilities
            ability_scores = profile.get("ability_scores", {})
            if len(primary) >= 2:
                # +1/+1 split — skip if primary[0] is already at cap
                ab0, ab1 = primary[0], primary[1]
                if ability_scores.get(ab0, 10) < 20:
                    asi_used[ab0] = 1
                    if ability_scores.get(ab1, 10) < 20:
                        asi_used[ab1] = 1
                    else:
                        # ab1 at cap, put both in ab0 if room
                        if ability_scores.get(ab0, 10) + 2 <= 20:
                            asi_used[ab0] = 2
                        else:
                            asi_used = dict(_EMPTY_ASI_USED)  # leave blank
                else:
                    # ab0 at cap, try ab1
                    if ability_scores.get(ab1, 10) + 2 <= 20:
                        asi_used[ab1] = 2
                    else:
                        asi_used = dict(_EMPTY_ASI_USED)
            elif len(primary) == 1:
                ab0 = primary[0]
                if ability_scores.get(ab0, 10) + 2 <= 20:
                    asi_used[ab0] = 2
    except Exception as _e:
        logger.warning("[ASI TRIGGER] Failed to build default suggestion: %s", _e)
        asi_used = dict(_EMPTY_ASI_USED)

    new_level = profile.get("level", 1)

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {}
    user_sessions[chat_id]['state'] = "WAITING_ASI_CHOICE"
    user_sessions[chat_id]['asi_used'] = asi_used

    current_scores = profile.get("ability_scores", {})
    # Cache current_scores so the WAITING_ASI_CHOICE hard-gate (B3) can
    # re-render the picker without an extra Sheets read (WARN #3).
    user_sessions[chat_id]['asi_current_scores'] = dict(current_scores)
    kb = build_asi_picker_keyboard(current_scores, asi_used)
    await bot.send_message(
        chat_id,
        (
            f"Рівень {new_level}! Оберіть розподіл ASI "
            f"(+2 здібностей всього: +2 в одну АБО +1+1 в дві різні)."
        ),
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("asi_inc_"))
async def callback_asi_inc(call: CallbackQuery):
    """Increment a pending ASI delta for one ability."""
    chat_id = call.message.chat.id
    ability = call.data[len("asi_inc_"):]  # e.g. "STR"

    try:
        session = user_sessions.get(chat_id, {})
        if session.get('state') != "WAITING_ASI_CHOICE":
            await call.answer()
            return

        profile, _ = await get_user_data(chat_id)
        if not profile:
            await call.answer()
            return

        asi_used: dict = session.get('asi_used', dict(_EMPTY_ASI_USED))
        current_scores = profile.get("ability_scores", {})
        sum_used = sum(asi_used.values())
        current_delta = asi_used.get(ability, 0)
        current_score = current_scores.get(ability, 10)

        # Validate: sum headroom AND cap guard
        if sum_used >= 2 or (current_score + current_delta) >= 20:
            await call.answer()
            return

        # Validate: at most 2 distinct abilities may receive points
        # (distributing to a 3rd ability is forbidden even if sum < 2)
        abilities_with_points = {ab for ab, v in asi_used.items() if v > 0}
        if ability not in abilities_with_points and len(abilities_with_points) >= 2:
            await call.answer()
            return

        asi_used[ability] = current_delta + 1
        user_sessions[chat_id]['asi_used'] = asi_used

        kb = build_asi_picker_keyboard(current_scores, asi_used)
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await call.answer()
    except Exception as e:
        logger.error(f"[ASI INC] {ability}: {type(e).__name__}: {e}")
        await call.answer()


@router.callback_query(F.data.startswith("asi_dec_"))
async def callback_asi_dec(call: CallbackQuery):
    """Decrement a pending ASI delta for one ability."""
    chat_id = call.message.chat.id
    ability = call.data[len("asi_dec_"):]

    try:
        session = user_sessions.get(chat_id, {})
        if session.get('state') != "WAITING_ASI_CHOICE":
            await call.answer()
            return

        profile, _ = await get_user_data(chat_id)
        if not profile:
            await call.answer()
            return

        asi_used: dict = session.get('asi_used', dict(_EMPTY_ASI_USED))
        current_scores = profile.get("ability_scores", {})

        if asi_used.get(ability, 0) <= 0:
            await call.answer()
            return

        asi_used[ability] -= 1
        user_sessions[chat_id]['asi_used'] = asi_used

        kb = build_asi_picker_keyboard(current_scores, asi_used)
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await call.answer()
    except Exception as e:
        logger.error(f"[ASI DEC] {ability}: {type(e).__name__}: {e}")
        await call.answer()


@router.callback_query(F.data == "asi_confirm")
async def callback_asi_confirm(call: CallbackQuery, bot: Bot):
    """Validate and apply the chosen ASI, save profile, resume normal play."""
    chat_id = call.message.chat.id

    try:
        session = user_sessions.get(chat_id, {})
        if session.get('state') != "WAITING_ASI_CHOICE":
            await call.answer("Ця кнопка більше не активна.")
            return

        asi_used: dict = session.get('asi_used', dict(_EMPTY_ASI_USED))
        sum_used = sum(asi_used.values())
        abilities_used = [ab for ab, v in asi_used.items() if v > 0]

        # Validate: exactly 2 points, at most 2 abilities
        if sum_used != 2 or len(abilities_used) > 2:
            await call.answer("Розподіліть рівно 2 очки між не більш ніж двома здібностями.")
            return

        profile, _ = await get_user_data(chat_id)
        if not profile:
            await call.answer("Профіль не знайдено. Спробуйте ще раз.")
            return

        choices = {ab: v for ab, v in asi_used.items() if v > 0}

        from core.dnd_progression import apply_asi
        from core.dnd_engine import recompute_ac

        try:
            apply_asi(profile, choices)
        except ValueError as ve:
            await call.answer(f"Помилка: {ve}")
            return

        recompute_ac(profile)

        char_name = profile.get("Ім'я", "")
        if not await save_user_data(chat_id, profile, char_name):
            await call.answer("Помилка збереження. Спробуйте ще раз.")
            return

        # Clear ASI state (including WARN #2/#3 session cache keys)
        user_sessions[chat_id].pop('asi_used', None)
        user_sessions[chat_id].pop('asi_current_scores', None)
        user_sessions[chat_id]['asi_pending'] = False
        user_sessions[chat_id]['state'] = "GAME_ACTIVE"

        # Remove the picker keyboard
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        # Build confirmation strings
        _LABELS = {
            "STR": "Сила", "DEX": "Спритність", "CON": "Витривалість",
            "INT": "Інтелект", "WIS": "Мудрість", "CHA": "Харизма",
        }
        choices_str = ", ".join(
            f"{_LABELS.get(ab, ab)} +{v}" for ab, v in choices.items()
        )
        final_scores = profile.get("ability_scores", {})
        scores_str = "  ".join(
            f"{ab} {final_scores.get(ab, '?')}"
            for ab in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        )
        await bot.send_message(
            chat_id,
            f"ASI застосовано! {choices_str}.\nТепер ваші характеристики: {scores_str}",
        )
        await call.answer("ASI збережено!")
    except Exception as e:
        logger.error(f"[ASI CONFIRM] {type(e).__name__}: {e}", exc_info=True)
        await call.answer("Виникла помилка. Спробуйте ще раз.")
        # State intentionally left as WAITING_ASI_CHOICE on failure so the player
        # can retry without losing their picker.  Success path already set GAME_ACTIVE.


@router.callback_query(F.data == "asi_reset")
async def callback_asi_reset(call: CallbackQuery):
    """Zero out the ASI allocation and re-render the picker."""
    chat_id = call.message.chat.id

    try:
        session = user_sessions.get(chat_id, {})
        if session.get('state') != "WAITING_ASI_CHOICE":
            await call.answer()
            return

        profile, _ = await get_user_data(chat_id)
        if not profile:
            await call.answer()
            return

        user_sessions[chat_id]['asi_used'] = dict(_EMPTY_ASI_USED)
        current_scores = profile.get("ability_scores", {})
        kb = build_asi_picker_keyboard(current_scores, dict(_EMPTY_ASI_USED))
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await call.answer("Скинуто.")
    except Exception as e:
        logger.error(f"[ASI RESET] {type(e).__name__}: {e}")
        await call.answer()


_SKILL_EMOJI: dict[str, str] = {
    "Athletics": "🏋️",
    "Acrobatics": "🤸",
    "Sleight of Hand": "🃏",
    "Stealth": "🥷",
    "Arcana": "🔮",
    "History": "📜",
    "Investigation": "🔍",
    "Nature": "🌿",
    "Religion": "⛪",
    "Animal Handling": "🐴",
    "Insight": "💭",
    "Medicine": "💊",
    "Perception": "👁",
    "Survival": "🏕️",
    "Deception": "🎭",
    "Intimidation": "😠",
    "Performance": "🎤",
    "Persuasion": "🗣️",
}

SKILL_DISPLAY_NAMES: dict[str, str] = {
    "Athletics": "Атлетика",
    "Acrobatics": "Акробатика",
    "Sleight of Hand": "Спритність рук",
    "Stealth": "Стелс",
    "Arcana": "Магія",
    "History": "Історія",
    "Investigation": "Розслідування",
    "Nature": "Природа",
    "Religion": "Релігія",
    "Animal Handling": "Поводження з тваринами",
    "Insight": "Інсайт",
    "Medicine": "Медицина",
    "Perception": "Сприйняття",
    "Survival": "Виживання",
    "Deception": "Обман",
    "Intimidation": "Залякування",
    "Performance": "Виступ",
    "Persuasion": "Переконання",
}


def _build_dnd_profile_text(profile: dict, chat_id: int) -> str:
    """Build D&D 5e profile display text from a full D&D profile dict."""
    from core.dnd_core import ability_modifier, proficiency_bonus as pb_func
    from core.dnd_skills import SKILLS, skill_modifier
    from core.dnd_progression import XP_TABLE

    char_name = profile.get("Ім'я", "Невідомий")
    house = profile.get("Дім", "Невідомий")
    title = profile.get("Титул", "")
    alignment = profile.get("Світогляд", "")
    loc = profile.get("Поточне місцезнаходження", "Невідомо")
    region = profile.get("Регіон", "")
    scene = profile.get("Поточна сцена", "")
    time_val = profile.get("Ігровий час", "")
    gold = profile.get("Особисте Золото", 0)
    weapon = profile.get("Зброя", "-")
    armor = profile.get("Броня", "-")
    inv_raw = profile.get("Інвентар", "")

    char_class = profile.get("class", "")
    heritage = profile.get("heritage", "")
    level = profile.get("level", 1)
    xp = profile.get("xp", 0)
    hp_current = profile.get("hp_current", profile.get("Здоров'я", 100))
    hp_max = profile.get("hp_max", 100)
    ac = profile.get("ac", 10)
    prof_bonus = profile.get("proficiency_bonus", pb_func(level))
    ability_scores = profile.get("ability_scores", {})
    saves_proficient = profile.get("saves_proficient", [])
    skill_profs = profile.get("skill_profs", [])
    skill_expertise = profile.get("skill_expertise", [])
    features_raw = profile.get("features", [])
    conditions_raw = profile.get("conditions", [])
    game_mode = profile.get("mode", "NORMAL")

    # next level XP
    if level >= 20:
        next_xp_str = "MAX"
    else:
        next_xp_str = str(XP_TABLE.get(level + 1, "?"))

    def _score(ab: str) -> int:
        return ability_scores.get(ab, 10)

    def _mod_str(ab: str) -> str:
        m = ability_modifier(_score(ab))
        return f"{m:+d}"

    # header
    header_parts = []
    if title:
        header_parts.append(title)
    if alignment:
        header_parts.append(alignment)
    header_line = " • ".join(header_parts) if header_parts else ""

    lines = []
    lines.append(f"🎭 *{char_name}* | *{house}*")
    if header_line:
        lines.append(f"📜 {header_line}")
    lines.append("")
    lines.append(f"🛡 *Клас:* {char_class} (L{level})")
    lines.append(f"🩸 *Походження:* {heritage}")
    lines.append(f"⚡ *XP:* {xp} / {next_xp_str}")
    lines.append("")
    lines.append(f"❤️ *HP:* {hp_current}/{hp_max}  |  🛡 *AC:* {ac}")
    lines.append(f"⚔️ *Proficiency:* +{prof_bonus}  |  💰 *Gold:* {gold} 🪙")
    lines.append("")
    # D) Tooltip-explainer перед abilities
    lines.append(
        "💪 сила  🏃 спритність  ❤️ витривалість  "
        "🧠 знання  👁 інтуїція  💬 переконання"
    )
    lines.append("")
    # B) Abilities з emoji
    lines.append("*Здібності:*")
    lines.append(
        f"  💪 STR {_score('STR')} ({_mod_str('STR')})   "
        f"🏃 DEX {_score('DEX')} ({_mod_str('DEX')})   "
        f"❤️ CON {_score('CON')} ({_mod_str('CON')})"
    )
    lines.append(
        f"  🧠 INT {_score('INT')} ({_mod_str('INT')})   "
        f"👁 WIS {_score('WIS')} ({_mod_str('WIS')})   "
        f"💬 CHA {_score('CHA')} ({_mod_str('CHA')})"
    )

    # saves
    if saves_proficient:
        lines.append("")
        lines.append(f"🎯 *Saves proficient:* {', '.join(saves_proficient)}")

    # C) Skill profs with emoji (only proficient/expertise skills)
    displayed_skills = []
    for sk_name in SKILLS:
        if sk_name in skill_profs or sk_name in skill_expertise:
            mod = skill_modifier(profile, sk_name)
            suffix = " (exp)" if sk_name in skill_expertise else ""
            icon = _SKILL_EMOJI.get(sk_name, "•")
            display_name = SKILL_DISPLAY_NAMES.get(sk_name, sk_name)
            displayed_skills.append(f"{icon} {display_name} {mod:+d}{suffix}")
    if displayed_skills:
        lines.append("")
        lines.append("🎓 *Skill profs:*")
        for sk_entry in displayed_skills:
            lines.append(f"  {sk_entry}")

    # features — повний опис; send_safe_message split-ить при потребі
    if features_raw:
        lines.append("")
        lines.append(f"🌟 *Здібності (L{level}):*")
        for feat in features_raw:
            if isinstance(feat, dict):
                fname = feat.get("name", "")
                fdesc = feat.get("desc", "")
            else:
                fname = str(feat)
                fdesc = ""
            lines.append(f"  • *{fname}* — {fdesc}")

    # A) Equipment — inventory without truncation; send_safe_message handles split
    from core.inventory import parse_inventory, format_inventory
    inv_str = format_inventory(parse_inventory(inv_raw)) if inv_raw else "Нічого"
    lines.append("")
    lines.append("⚔️ *Спорядження:*")
    lines.append(f"  Зброя: {weapon}")
    lines.append(f"  Броня: {armor}")
    lines.append(f"  Інвентар: {inv_str}")

    # location / time
    loc_str = f"{loc} ({region})" if region else loc
    lines.append("")
    lines.append(f"📍 *Локація:* {loc_str}")
    if scene:
        lines.append(f"🎬 *Сцена:* {scene}")
    if time_val:
        lines.append(f"⏱ *Ігровий час:* {time_val}")

    # conditions block
    if conditions_raw:
        lines.append("")
        lines.append("⚠️ *Стани:*")
        for cond in conditions_raw:
            if isinstance(cond, dict):
                cname = cond.get("name", str(cond))
                dur = cond.get("duration", "")
                src = cond.get("source", "")
                cond_line = f"  • {cname}"
                if dur:
                    cond_line += f" ({dur})"
                if src:
                    cond_line += f" — {src}"
            else:
                cond_line = f"  • {cond}"
            lines.append(cond_line)

    # combat block
    if game_mode == "COMBAT":
        combat_roster_lines = ""
        round_label = "Раунд ?"
        try:
            from core.combat_state import get_combat_state
            cs = get_combat_state(chat_id)
            if cs:
                initiative_display = []
                for ref in cs.initiative_order:
                    if ref.kind == "npc":
                        npc = cs.npcs.get(ref.name, {})
                        npc_hp = npc.get("hp_current", "?")
                        npc_hp_max = npc.get("hp_max", "?")
                        status = npc.get("Status", "Active")
                        status_icon = "💀" if status == "Dead" else ("🏃" if status == "Fled" else "⚔️")
                        initiative_display.append(
                            f"  {status_icon} {ref.name}: {npc_hp}/{npc_hp_max} HP (init {ref.init_roll})"
                        )
                    else:
                        initiative_display.append(
                            f"  👤 {ref.name}: {hp_current}/{hp_max} HP, AC {ac} (init {ref.init_roll})"
                        )
                combat_roster_lines = "\n".join(initiative_display)
                round_label = f"Раунд {cs.round}"
            else:
                combat_roster_lines = "  (стан у пам'яті не знайдено)"
        except Exception:
            combat_roster_lines = ""
        lines.append("")
        lines.append("⚔️ *АКТИВНИЙ БІЙ* ⚔️")
        lines.append(round_label)
        lines.append("Ініціатива:")
        if combat_roster_lines:
            lines.append(combat_roster_lines)

    # reputation
    rep = profile.get("Репутація (Рідний регіон)", None)
    if rep is not None:
        lines.append("")
        lines.append(f"📊 *Репутація (регіон):* {rep}")

    return "\n".join(lines)


def _build_legacy_profile_text(profile: dict) -> str:
    """Fallback display for pre-D&D profiles (no ability_scores key)."""
    char_name = profile.get("Ім'я", "Невідомий")
    house = profile.get("Дім", "Невідомий")
    loc = profile.get("Поточне місцезнаходження", "Невідомо")
    time_val = profile.get("Ігровий час", "Невідомий час")
    gold = profile.get("Особисте Золото", 0)
    weapon = profile.get("Зброя", "-")
    armor = profile.get("Броня", "-")
    from core.inventory import parse_inventory, format_inventory
    inv = format_inventory(parse_inventory(profile.get("Інвентар", [])))
    rep = profile.get("Репутація (Рідний регіон)", 0)
    health_val = profile.get("Здоров'я", 100)
    energy_val = profile.get("Енергія", 1000)
    combat = profile.get("Бойові навички", 0)
    military = profile.get("Військові навички", 0)
    intrigue = profile.get("Інтрига", 0)
    manage = profile.get("Управління", 0)
    return (
        f"👤 *{char_name}*\n"
        f"🏠 Дім: {house}\n📍 {loc}\n"
        f"📅 {time_val}\n━━━━━━━━━━━━━━━━━━\n"
        f"❤️ Здоров'я: {health_val} | ⚡ Енергія: {energy_val}\n"
        f"⚔️ Бойові: {combat} | 🛡️ Військові: {military}\n"
        f"🍷 Інтрига: {intrigue} | ⚖️ Управління: {manage}\n"
        f"━━━━━━━━━━━━━━━━━━\n🗣 Репутація: {rep}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Золото:* {gold} драконів\n"
        f"⚔️ *Зброя:* {weapon}\n"
        f"🛡 *Броня:* {armor}\n"
        f"📦 *Речі:* {inv}"
    )


@router.message(Command("profile"))
@router.message(F.text == "📜 Профіль")
async def show_profile_handler(message: Message, bot: Bot):
    chat_id = message.chat.id

    profile, _ = await get_user_data(chat_id)
    if not profile:
        await send_safe_message(bot, chat_id, "Спершу почніть гру через /start")
        return

    # D&D profile detection: ability_scores key is present in Phase 9+ profiles
    if profile.get("ability_scores"):
        text = _build_dnd_profile_text(profile, chat_id)
    else:
        # Legacy pre-D&D profile — graceful fallback with migration hint
        text = _build_legacy_profile_text(profile)

    await send_safe_message(bot, chat_id, text)


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

    # 3. Видаляємо per-player NPC аркуш та очищуємо кеш
    await delete_user_npc_sheet(user_id)
    clear_npc_cache(user_id)

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


def _section_header(num: int, title: str) -> str:
    """Box-style section header for debug trace file."""
    bar = "═" * 68
    return f"\n{bar}\n║ STAGE {num}: {title}\n{bar}"


def _subsection(label: str) -> str:
    """Sub-section divider for INPUT / THOUGHTS / OUTPUT blocks."""
    dashes = "─" * max(0, 64 - len(label))
    return f"\n┌─ {label} {dashes}"


async def _send_debug_trace_file(bot: Bot, chat_id: int, trace: dict) -> None:
    """Send full pipeline trace as a .txt file attachment (admin debug mode only)."""
    ts_iso = datetime.fromtimestamp(trace.get("timestamp", 0)).isoformat()
    lines = []

    # ── Title block ──────────────────────────────────────────────────────────
    outer = "╔" + "═" * 68 + "╗"
    inner_title = "║" + "              DEBUG TRACE — Game Turn Pipeline".center(68) + "║"
    inner_meta = "║" + f"  Chat ID: {trace.get('chat_id')}  |  {ts_iso}".ljust(68) + "║"
    bottom = "╚" + "═" * 68 + "╝"
    lines.extend([outer, inner_title, inner_meta, bottom])

    # ── Stage 1: Player action ────────────────────────────────────────────────
    lines.append(_section_header(1, "PLAYER ACTION (input)"))
    lines.append(trace.get("user_input") or "<empty>")

    # ── Stage 2: Censor ───────────────────────────────────────────────────────
    censor = trace.get("censor") or {}
    lines.append(_section_header(2, "CENSOR"))

    lines.append(_subsection("INPUT (prompt to model)"))
    lines.append(censor.get("prompt") or "<not captured>")

    lines.append(_subsection("REASONING (model thoughts)"))
    thoughts_c = censor.get("thoughts") or []
    if thoughts_c:
        for t in thoughts_c:
            lines.append(f"  - {t}")
    else:
        lines.append("  (no thoughts captured — Censor uses model_worker, see Worker section)")

    lines.append(_subsection("OUTPUT (parsed JSON)"))
    lines.append(json.dumps(censor.get("parsed") or {}, ensure_ascii=False, indent=2))

    # ── Stage 3: Worker ───────────────────────────────────────────────────────
    worker = trace.get("worker") or {}
    lines.append(_section_header(3, "WORKER (NORMAL pipeline)"))

    lines.append(_subsection("INPUT (prompt to model)"))
    lines.append(worker.get("prompt") or "<not captured>")

    lines.append(_subsection("REASONING (model thoughts)"))
    thoughts_w = worker.get("thoughts") or []
    if thoughts_w:
        for t in thoughts_w:
            lines.append(f"  - {t}")
    else:
        lines.append("  (none)")

    lines.append(_subsection("OUTPUT (parsed JSON)"))
    parsed_w = worker.get("parsed")
    lines.append(json.dumps(parsed_w, ensure_ascii=False, indent=2) if parsed_w else "<None — Worker skipped or failed>")

    lines.append(_subsection("RAW LLM RESPONSE"))
    lines.append(worker.get("raw") or "<not captured>")

    # ── Stage 4: GM_Logic ─────────────────────────────────────────────────────
    gm = trace.get("gm_logic") or {}
    lines.append(_section_header(4, "GM_LOGIC"))

    lines.append(_subsection("INPUT (prompt to model)"))
    lines.append(gm.get("prompt") or "<not captured>")

    lines.append(_subsection("REASONING (model thoughts)"))
    thoughts_gm = gm.get("thoughts") or []
    if thoughts_gm:
        for t in thoughts_gm:
            lines.append(f"  - {t}")
    else:
        lines.append("  (none)")

    lines.append(_subsection("OUTPUT (parsed JSON)"))
    parsed_gm = gm.get("parsed")
    lines.append(json.dumps(parsed_gm, ensure_ascii=False, indent=2) if parsed_gm else "<None — GM_Logic skipped or failed>")

    lines.append(_subsection("RAW LLM RESPONSE"))
    lines.append(gm.get("raw") or "<not captured>")

    # ── Stage 5: Narrator ─────────────────────────────────────────────────────
    nar = trace.get("narrator") or {}
    lines.append(_section_header(5, "NARRATOR"))

    lines.append(_subsection("INPUT (prompt to model)"))
    lines.append(nar.get("prompt") or "<not captured>")

    lines.append(_subsection("REASONING (model thoughts)"))
    thoughts_n = nar.get("thoughts") or []
    if thoughts_n:
        for t in thoughts_n:
            lines.append(f"  - {t}")
    else:
        lines.append("  (none)")

    lines.append(_subsection("FINAL TEXT"))
    lines.append(nar.get("final_text") or "<empty>")

    # ── Stage 6: Mechanical impacts ───────────────────────────────────────────
    lines.append(_section_header(6, "MECHANICAL IMPACTS"))
    for log in trace.get("logs") or []:
        lines.append(f"  - {log}")

    # ── Footer ────────────────────────────────────────────────────────────────
    bar = "═" * 68
    lines.append(f"\n{bar}")
    lines.append("END OF TRACE")
    lines.append(bar)

    # str() guard: any line that is None would crash "\n".join — coerce defensively.
    content = "\n".join(str(line) for line in lines).encode("utf-8")
    ts = datetime.fromtimestamp(trace.get("timestamp", 0)).strftime("%Y%m%d_%H%M%S")
    filename = f"debug_{chat_id}_{ts}.txt"

    file = BufferedInputFile(content, filename=filename)
    await bot.send_document(
        chat_id=chat_id,
        document=file,
        caption=f"Debug trace ({len(lines)} lines)",
    )


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

    # ── B3: Hard-gate — ASI must be resolved before any new turn ─────────────
    if state == "WAITING_ASI_CHOICE":
        asi_used = session.get('asi_used', {"STR": 0, "DEX": 0, "CON": 0, "INT": 0, "WIS": 0, "CHA": 0})
        # Use the scores snapshot cached when _check_and_trigger_asi first fired
        # (WARN #3 fix): avoids a redundant Sheets read just to re-render the picker.
        current_scores = session.get('asi_current_scores', {})
        if not current_scores:
            # Fallback: session was cleared or bot restarted — fetch once from Sheets.
            profile, _ = await get_user_data(chat_id)
            current_scores = profile.get("ability_scores", {}) if profile else {}
        kb = build_asi_picker_keyboard(current_scores, asi_used)
        await message.answer(
            "Спершу оберіть розподіл ASI через кнопки вище.",
            reply_markup=kb,
        )
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
            # КРОК 3: Тематична заглушка (буде оновлюватись прогресивно)
            temp_msg = await message.reply(random.choice(PLACEHOLDER_PHRASES))

            # Progress callback — оновлює placeholder зі статусом поточного кроку
            async def _progress(status_text: str):
                try:
                    await temp_msg.edit_text(status_text, parse_mode=None)
                except Exception:
                    pass

            # КРОК 2: Фоновий typing на весь час обробки
            typing_task = asyncio.create_task(keep_typing(bot, chat_id))

            # Streaming тимчасово ВИМКНЕНО глобально через нестабільність gemma-4-31b-it
            # (часті MALFORMED_RESPONSE, 500 INTERNAL посеред stream-ів).
            # Раніше: USE_STREAMING = chat_id not in EROTIC_USERS
            USE_STREAMING = False
            narrator_queue = asyncio.Queue() if USE_STREAMING else None

            # Streaming consumer — оновлює повідомлення кожні 1.5с новим текстом
            async def _stream_consumer():
                accumulated = ""
                last_edit_time = 0
                print("🟢 [CONSUMER] Stream consumer started")
                while True:
                    try:
                        chunk = await asyncio.wait_for(narrator_queue.get(), timeout=300)
                    except asyncio.TimeoutError:
                        print("🔴 [CONSUMER] Timeout waiting for chunk")
                        break
                    if chunk is None:  # сигнал завершення
                        print(f"🟢 [CONSUMER] Done. Total accumulated: {len(accumulated)} chars")
                        break
                    accumulated += chunk
                    print(f"🟢 [CONSUMER] Got chunk, accumulated: {len(accumulated)} chars")
                    now = time.time()
                    if now - last_edit_time >= 1.5 and len(accumulated) > 10:
                        try:
                            if len(accumulated) > 4000:
                                display = "✍️ ...\n\n" + accumulated[-3900:]
                            else:
                                display = accumulated + " ✍️"
                            await temp_msg.edit_text(display, parse_mode=None)
                            last_edit_time = now
                            print(f"🟢 [CONSUMER] Message edited, {len(display)} chars")
                        except Exception as e:
                            logger.warning(f"[CONSUMER_EDIT] edit_text failed: {type(e).__name__}: {e}")

            consumer_task = asyncio.create_task(_stream_consumer()) if USE_STREAMING else None
            response_text: str | None = None
            suggested_actions: list = []
            try:
                response_text, suggested_actions = await process_game_turn(
                    chat_id, user_text,
                    progress_callback=_progress,
                    narrator_queue=narrator_queue,
                )
                if consumer_task:
                    await consumer_task

                if display_intent:
                    response_text = f"🗣️ _{display_intent}_\n\n{response_text}"
                await send_game_response(bot, chat_id, response_text, suggested_actions,
                                         edit_message=temp_msg)

                # Debug trace dispatch — decoupled from in-memory DEBUG_USERS.
                # Engine saves last_debug_trace whenever _debug_active (in-memory set OR profile flag).
                # We just check the session; if trace exists, the turn was debug-active.
                session = user_sessions.get(chat_id, {})
                trace = session.pop("last_debug_trace", None)  # consume one-time
                if trace:
                    logger.info(f"[DEBUG_MODE] Checkpoint: trace found for chat_id={chat_id}, sending file")
                    try:
                        await _send_debug_trace_file(bot, chat_id, trace)
                        logger.info(f"[DEBUG_MODE] File sent OK for chat_id={chat_id}")
                    except Exception as _trace_err:
                        logger.error(f"[DEBUG_TRACE] Failed to send: {type(_trace_err).__name__}: {_trace_err}", exc_info=True)

                # ── ASI pending check (B2) ────────────────────────────────────
                # After the turn is fully rendered, check if a level-up left an
                # ASI pending.  If so, gate the player and show the picker.
                await _check_and_trigger_asi(bot, chat_id)

            except Exception as e:
                if consumer_task:
                    consumer_task.cancel()
                error_trace = traceback.format_exc()
                resp_len = len(response_text) if response_text is not None else "N/A"
                resp_preview = response_text[:200] if response_text is not None else "N/A"
                actions_len = len(suggested_actions)
                logger.error(
                    f"[HANDLER_FATAL] {type(e).__name__}: {str(e)[:300]}\n"
                    f"  response_text len={resp_len}, preview={repr(resp_preview)}\n"
                    f"  suggested_actions len={actions_len}\n"
                    f"{error_trace}"
                )
                try:
                    await temp_msg.edit_text("⚠️ Технічна помилка. Спробуйте ще раз.", parse_mode=None)
                except Exception:
                    try:
                        await bot.send_message(chat_id, "⚠️ Технічна помилка. Спробуйте ще раз.")
                    except Exception as final_err:
                        logger.error(f"[HANDLER_LAST_RESORT_FAIL] {type(final_err).__name__}: {str(final_err)[:200]}")
            finally:
                typing_task.cancel()

        finally:
            active_processing.discard(chat_id)