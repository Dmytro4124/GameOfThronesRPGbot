# bot/handlers.py
import asyncio
import logging
import random
import time
import traceback
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.menus import get_dynamic_menu, get_main_menu
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
            profile_region = full_profile.get("Регіон", "")
            profile_class = full_profile.get("class", "")
            profile_heritage = full_profile.get("heritage", "")

            # Фоновий запуск world setup (канонічні NPC + локальні NPC).
            # Стан INITIALIZING залишається до завершення — гравець бачить intro
            # але game loop заблокований (handle_general_messages перевіряє state).
            async def initial_world_setup(loc, p_name):
                try:
                    await background_canon_generation(user_id, excluded_name=p_name)
                    await populate_contextual_npcs(user_id, loc, "Start of the game. Normal daily routine.", excluded_name=p_name)
                finally:
                    # Розблокуємо гравця у будь-якому разі
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

            # --- Intro: cache hit → миттєво; cache miss → fallback + фоновий LLM ---
            cached_intro_text = get_cached_intro(profile_class, profile_heritage, profile_region)

            if cached_intro_text:
                # Cache HIT — надсилаємо одразу
                await send_safe_message(bot, chat_id, cached_intro_text, reply_markup=get_main_menu())
                user_sessions.setdefault(chat_id, {})["action_intents"] = {}
                user_sessions[chat_id]['history'].append({"role": "GM", "content": cached_intro_text})
            else:
                # Cache MISS — показуємо детерміністичний fallback одразу,
                # надсилаємо через bot.send_message щоб отримати Message-об'єкт для edit_text
                fallback_text = build_fallback_intro(full_profile)
                placeholder_msg = await bot.send_message(
                    chat_id,
                    fallback_text + "\n\n_Готую персоналізований вступ..._",
                    reply_markup=get_main_menu(),
                    parse_mode='Markdown',
                )
                user_sessions.setdefault(chat_id, {})["action_intents"] = {}
                user_sessions[chat_id]['history'].append({"role": "GM", "content": fallback_text})

                # Фоновий LLM-intro — оновить повідомлення і закешує результат
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
                            # Кешуємо для майбутніх гравців з тією ж комбінацією
                            await set_cached_intro(profile_class, profile_heritage, profile_region, display_text)
                            # Оновлюємо placeholder повним інтро
                            try:
                                markup = get_dynamic_menu(button_texts) if button_texts else get_main_menu()
                                await placeholder_msg.edit_text(display_text, reply_markup=markup, parse_mode=None)
                                if chat_id in user_sessions:
                                    user_sessions[chat_id]["action_intents"] = intents_map
                                    if user_sessions[chat_id].get('history'):
                                        user_sessions[chat_id]['history'][-1] = {"role": "GM", "content": display_text}
                            except Exception as e_edit:
                                print(f"[bg intro] edit_text failed: {e_edit}")
                    except Exception as e:
                        print(f"[bg intro] Failed (using fallback): {e}")
                        # Прибираємо спінер із fallback-повідомлення
                        try:
                            await placeholder_msg.edit_text(fallback_text, reply_markup=get_main_menu(), parse_mode=None)
                        except Exception:
                            pass

                asyncio.create_task(_bg_intro_task())

            await bot.send_message(chat_id, "⚔️ Ваш шлях починається...")
        else:
            await bot.send_message(chat_id, "❌ Помилка запису в базу даних.")
    else:
        await bot.send_message(chat_id, "❌ Помилка генерації профілю.")


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
    lines.append("💪 *Здібності:*")
    lines.append(
        f"  STR {_score('STR')} ({_mod_str('STR')})    "
        f"DEX {_score('DEX')} ({_mod_str('DEX')})    "
        f"CON {_score('CON')} ({_mod_str('CON')})"
    )
    lines.append(
        f"  INT {_score('INT')} ({_mod_str('INT')})    "
        f"WIS {_score('WIS')} ({_mod_str('WIS')})    "
        f"CHA {_score('CHA')} ({_mod_str('CHA')})"
    )

    # saves
    if saves_proficient:
        lines.append("")
        lines.append(f"🎯 *Saves proficient:* {', '.join(saves_proficient)}")

    # skill profs (only proficient/expertise skills)
    displayed_skills = []
    for sk_name in SKILLS:
        if sk_name in skill_profs or sk_name in skill_expertise:
            mod = skill_modifier(profile, sk_name)
            suffix = " (exp)" if sk_name in skill_expertise else ""
            displayed_skills.append(f"{sk_name} {mod:+d}{suffix}")
    if displayed_skills:
        lines.append("")
        lines.append("🎓 *Skill profs:*")
        lines.append("  " + ", ".join(displayed_skills))

    # features (max 5)
    if features_raw:
        lines.append("")
        lines.append(f"🌟 *Features (L{level}):*")
        shown = 0
        for feat in features_raw:
            if shown >= 5:
                lines.append("  ... та інше")
                break
            if isinstance(feat, dict):
                fname = feat.get("name", "")
                fdesc = feat.get("desc", "")
            else:
                fname = str(feat)
                fdesc = ""
            desc_short = fdesc[:60] + "..." if len(fdesc) > 60 else fdesc
            lines.append(f"  • {fname} — {desc_short}")
            shown += 1

    # equipment
    inv_str = str(inv_raw).strip() if inv_raw else "Нічого"
    if len(inv_str) > 80:
        inv_str = inv_str[:77] + "..."
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
    inv = profile.get("Інвентар", "Пусто")
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

            # Streaming вимикається для еротичного режиму — streamGenerateContent блокує NSFW
            USE_STREAMING = chat_id not in EROTIC_USERS
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
            except Exception as e:
                if consumer_task:
                    consumer_task.cancel()
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