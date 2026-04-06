# core/cheats.py
"""Адміністраторська консоль розробника. 21 команда.
Всі операції з профілем обходять ШІ і напряму маніпулюють Google Sheets.
"""
import asyncio
import re

from database.operations import get_user_data, save_user_data, find_best_match
from database.sheets import db
from database.canon_npc import _score_to_relation_text
from core.mechanics import safe_int
from core.world_constants import is_valid_location, get_region_for_location
from config import TAB_NPC, GODMODE_USERS, PUPPET_USERS, EROTIC_USERS


# ─────────────────────────── Helpers ────────────────────────────

async def _get_profile(chat_id):
    return await get_user_data(chat_id)


async def _save_profile(chat_id, profile):
    char_name = profile.get("Ім'я", "Unknown")
    return await save_user_data(chat_id, profile, char_name)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _find_npc_row(ws, name):
    """Синхронний хелпер. Повертає (row_idx_in_rows, headers, rows).
    row_idx_in_rows — 1-based індекс в масиві rows (0 = заголовок).
    Для видалення: worksheet.delete_rows(row_idx_in_rows + 1) — бо в sheet заголовок = рядок 1.
    """
    rows = ws.get_all_values()
    if not rows:
        return None, [], []
    headers = rows[0]
    try:
        name_col = headers.index("Name")
    except ValueError:
        name_col = 2
    names = [r[name_col] for r in rows[1:] if len(r) > name_col]
    match = find_best_match(name, names)
    if not match:
        return None, headers, rows
    row_idx = next(
        (i + 1 for i, r in enumerate(rows[1:]) if len(r) > name_col and r[name_col] == match),
        None,
    )
    return row_idx, headers, rows


def _parse_time_str(time_str):
    """Парсить 'NNN рік В.Е., M-й місяць, День D (TOD)' у абсолютні хвилини."""
    m_month = re.search(r'(\d+)-й місяць', time_str)
    m_day = re.search(r'День (\d+)', time_str)
    m_tod = re.search(r'\(([^)]+)\)', time_str)
    month = int(m_month.group(1)) if m_month else 1
    day = int(m_day.group(1)) if m_day else 1
    tod_str = m_tod.group(1) if m_tod else "Ранок"
    tod_offsets = {"Ранок": 0, "День": 360, "Вечір": 720, "Ніч": 1080}
    tod_min = tod_offsets.get(tod_str, 0)
    # Рік зберігаємо окремо — не змінюємо
    m_year = re.search(r'(\d+) рік', time_str)
    year = int(m_year.group(1)) if m_year else 298
    absolute = (month - 1) * 30 * 1440 + (day - 1) * 1440 + tod_min
    return year, absolute


def _format_time_str(year, absolute_minutes):
    """Серіалізує абсолютні хвилини назад у рядок ігрового часу."""
    MINUTES_PER_DAY = 1440
    DAYS_PER_MONTH = 30
    tod_map = [(0, "Ранок"), (360, "День"), (720, "Вечір"), (1080, "Ніч")]

    absolute_minutes = max(0, absolute_minutes)
    total_days = absolute_minutes // MINUTES_PER_DAY
    day_minutes = absolute_minutes % MINUTES_PER_DAY

    month = total_days // DAYS_PER_MONTH + 1
    day = total_days % DAYS_PER_MONTH + 1

    tod = "Ранок"
    for threshold, label in reversed(tod_map):
        if day_minutes >= threshold:
            tod = label
            break

    return f"{year} рік В.Е., {month}-й місяць, День {day} ({tod})"


def _find_valid_location_split(args):
    """Пробує всі зліва-направо префікси args, повертає (location_str, remaining_args)
    де location_str — перша валідна локація. Fallback: (args[0], args[1:])."""
    for i in range(len(args), 0, -1):
        candidate = " ".join(args[:i])
        if is_valid_location(candidate):
            return candidate, args[i:]
    return args[0], args[1:]


# ─────────────────────────── Команди ────────────────────────────

async def cmd_heal(message, bot, chat_id, args):
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    profile["Здоров'я"] = 100
    profile["Енергія"] = 100
    await _save_profile(chat_id, profile)
    await message.answer("✅ HP та Енергія відновлені до 100")


async def cmd_sethp(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /sethp <1-100>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        val = _clamp(int(args[0]), 1, 100)
    except ValueError:
        await message.answer("❌ Значення повинно бути числом"); return
    profile["Здоров'я"] = val
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ HP → {val}")


async def cmd_setenergy(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /setenergy <1-100>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        val = _clamp(int(args[0]), 1, 100)
    except ValueError:
        await message.answer("❌ Значення повинно бути числом"); return
    profile["Енергія"] = val
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Енергія → {val}")


async def cmd_setgold(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /setgold <сума>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        val = max(0, int(args[0]))
    except ValueError:
        await message.answer("❌ Значення повинно бути числом"); return
    profile["Особисте Золото"] = val
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Золото → {val}")


async def cmd_settitle(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /settitle <Титул>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    profile["Титул"] = " ".join(args)
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Титул → {profile['Титул']}")


async def cmd_sethouse(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /sethouse <Дім>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    profile["Дім"] = " ".join(args)
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Дім → {profile['Дім']}")


async def cmd_additem(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /additem <Назва предмету>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    item = " ".join(args)
    inv = profile.get("Інвентар", "").strip()
    profile["Інвентар"] = f"{inv}, {item}" if inv else item
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Додано: {item}")


async def cmd_delitem(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /delitem <Назва предмету>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    target = " ".join(args)
    inv = profile.get("Інвентар", "")
    items = [i.strip() for i in inv.split(",") if i.strip()]
    match = find_best_match(target, items)
    if not match:
        await message.answer(f"❌ Предмет '{target}' не знайдено в інвентарі"); return
    items.remove(match)
    profile["Інвентар"] = ", ".join(items)
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Видалено: {match}")


async def cmd_clearinv(message, bot, chat_id, args):
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    profile["Інвентар"] = ""
    await _save_profile(chat_id, profile)
    await message.answer("✅ Інвентар очищено")


async def cmd_addskill(message, bot, chat_id, args):
    if len(args) < 2:
        await message.answer("Використання: /addskill <Назва навички> <Очки>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        delta = int(args[-1])
    except ValueError:
        await message.answer("❌ Очки повинні бути числом"); return
    skill_key = " ".join(args[:-1])
    current = safe_int(profile.get(skill_key, 0), 0)
    profile[skill_key] = _clamp(current + delta, 0, 100)
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ {skill_key}: {current} → {profile[skill_key]}")


async def cmd_setskill(message, bot, chat_id, args):
    if len(args) < 2:
        await message.answer("Використання: /setskill <Назва навички> <Очки>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        val = _clamp(int(args[-1]), 0, 100)
    except ValueError:
        await message.answer("❌ Очки повинні бути числом"); return
    skill_key = " ".join(args[:-1])
    profile[skill_key] = val
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ {skill_key} → {val}")


async def cmd_tp(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /tp <Локація> [Сцена]"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    location, scene_args = _find_valid_location_split(args)
    if not is_valid_location(location):
        await message.answer(f"❌ '{location}' — невалідна локація. Перевір назву."); return
    profile["Поточне місцезнаходження"] = location
    profile["Регіон"] = get_region_for_location(location) or profile.get("Регіон", "")
    if scene_args:
        profile["Поточна сцена"] = " ".join(scene_args)
    await _save_profile(chat_id, profile)
    scene_info = f" / {profile['Поточна сцена']}" if scene_args else ""
    await message.answer(f"✅ Телепортовано → {location}{scene_info}")


async def cmd_addtime(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /addtime <Хвилини>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        delta = int(args[0])
    except ValueError:
        await message.answer("❌ Значення повинно бути числом"); return
    time_str = profile.get("Ігровий час", "298 рік В.Е., 1-й місяць, День 1 (Ранок)")
    year, absolute = _parse_time_str(time_str)
    new_time_str = _format_time_str(year, absolute + delta)
    profile["Ігровий час"] = new_time_str
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Час: {time_str}\n→ {new_time_str}")


async def cmd_tpnpc(message, bot, chat_id, args):
    if len(args) < 2:
        await message.answer("Використання: /tpnpc <Ім'я NPC> <Локація> [Сцена]"); return

    # Парсимо: шукаємо позицію де починається валідна локація (зліва перебираємо суфікси)
    location = None
    npc_end_idx = None
    scene_args = []
    for i in range(1, len(args)):
        for j in range(i, len(args) + 1):
            candidate = " ".join(args[i:j])
            if is_valid_location(candidate):
                location = candidate
                npc_end_idx = i
                scene_args = args[j:]
                break
        if location:
            break

    if not location:
        await message.answer("❌ Валідну локацію не знайдено. Перевір назву."); return

    npc_name = " ".join(args[:npc_end_idx])
    scene_str = " ".join(scene_args) if scene_args else ""

    def _sync_tpnpc():
        ws = db.get_sheet(TAB_NPC)
        if not ws:
            return "❌ Таблицю NPC_DB не знайдено"
        row_idx, headers, rows = _find_npc_row(ws, npc_name)
        if row_idx is None:
            return f"❌ NPC '{npc_name}' не знайдено"
        try:
            loc_col = headers.index("Location") + 1
            scene_col = headers.index("Scene") + 1
        except ValueError:
            return "❌ Колонки Location/Scene не знайдено"
        sheet_row = row_idx + 1  # +1 бо заголовок = рядок 1 у sheet
        ws.update_cell(sheet_row, loc_col, location)
        if scene_str:
            ws.update_cell(sheet_row, scene_col, scene_str)
        matched_name = rows[row_idx][headers.index("Name")]
        return f"✅ NPC '{matched_name}' переміщено → {location}" + (f" / {scene_str}" if scene_str else "")

    result = await asyncio.to_thread(_sync_tpnpc)
    await message.answer(result)


async def cmd_setrep(message, bot, chat_id, args):
    if len(args) < 2:
        await message.answer("Використання: /setrep <Ім'я NPC> <Оцінка -100..100>"); return
    try:
        score = _clamp(int(args[-1]), -100, 100)
    except ValueError:
        await message.answer("❌ Оцінка повинна бути числом від -100 до 100"); return
    npc_name = " ".join(args[:-1])

    def _sync_setrep():
        ws = db.get_sheet(TAB_NPC)
        if not ws:
            return "❌ Таблицю NPC_DB не знайдено"
        row_idx, headers, rows = _find_npc_row(ws, npc_name)
        if row_idx is None:
            return f"❌ NPC '{npc_name}' не знайдено"
        try:
            rep_col = headers.index("Reputation_Score") + 1
            rel_col = headers.index("Relation_Player") + 1
        except ValueError:
            return "❌ Колонки Reputation_Score/Relation_Player не знайдено"
        sheet_row = row_idx + 1
        ws.update_cell(sheet_row, rep_col, score)
        ws.update_cell(sheet_row, rel_col, _score_to_relation_text(score))
        matched_name = rows[row_idx][headers.index("Name")]
        return f"✅ '{matched_name}' репутація → {score} ({_score_to_relation_text(score)})"

    result = await asyncio.to_thread(_sync_setrep)
    await message.answer(result)


async def cmd_killnpc(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /killnpc <Ім'я NPC>"); return
    npc_name = " ".join(args)

    def _sync_killnpc():
        ws = db.get_sheet(TAB_NPC)
        if not ws:
            return "❌ Таблицю NPC_DB не знайдено"
        row_idx, headers, rows = _find_npc_row(ws, npc_name)
        if row_idx is None:
            return f"❌ NPC '{npc_name}' не знайдено"
        try:
            status_col = headers.index("Status") + 1
        except ValueError:
            return "❌ Колонку Status не знайдено"
        ws.update_cell(row_idx + 1, status_col, "Dead")
        matched_name = rows[row_idx][headers.index("Name")]
        return f"✅ NPC '{matched_name}' позначено як Dead"

    result = await asyncio.to_thread(_sync_killnpc)
    if result.startswith("✅"):
        from database.operations import evict_npc_from_cache, refresh_npc_database
        npc_name_matched = result.split("'")[1]
        await refresh_npc_database()          # спочатку resync (може повернути стале)
        evict_npc_from_cache(npc_name_matched)  # потім гарантоване видалення — завжди останнє
    await message.answer(result)


async def cmd_delnpc(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /delnpc <Ім'я NPC>"); return
    npc_name = " ".join(args)

    def _sync_delnpc():
        ws = db.get_sheet(TAB_NPC)
        if not ws:
            return "❌ Таблицю NPC_DB не знайдено"
        row_idx, headers, rows = _find_npc_row(ws, npc_name)
        if row_idx is None:
            return f"❌ NPC '{npc_name}' не знайдено"
        matched_name = rows[row_idx][headers.index("Name")]
        ws.delete_rows(row_idx + 1)  # +1: заголовок займає рядок 1 у sheet
        return f"✅ NPC '{matched_name}' видалено з бази"

    result = await asyncio.to_thread(_sync_delnpc)
    await message.answer(result)


async def cmd_cleanscene(message, bot, chat_id, args):
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    curr_scene = profile.get("Поточна сцена", "")
    if not curr_scene:
        await message.answer("❌ Поточна сцена не визначена в профілі"); return

    def _sync_cleanscene():
        ws = db.get_sheet(TAB_NPC)
        if not ws:
            return "❌ Таблицю NPC_DB не знайдено"
        rows = ws.get_all_values()
        if not rows:
            return "❌ Таблиця порожня"
        headers = rows[0]
        try:
            scene_col_idx = headers.index("Scene")
            scene_col = scene_col_idx + 1
        except ValueError:
            return "❌ Колонку Scene не знайдено"

        cleared = 0
        for i, row in enumerate(rows[1:], start=2):  # start=2: рядок 1 — заголовок у sheet
            if len(row) > scene_col_idx and row[scene_col_idx] == curr_scene:
                ws.update_cell(i, scene_col, "")
                cleared += 1
        return f"✅ Очищено сцену '{curr_scene}': {cleared} NPC переміщено"

    result = await asyncio.to_thread(_sync_cleanscene)
    await message.answer(result)


async def cmd_godmode(message, bot, chat_id, args):
    if chat_id in GODMODE_USERS:
        GODMODE_USERS.discard(chat_id)
        await message.answer("🔴 Godmode ВИМКНЕНО")
    else:
        GODMODE_USERS.add(chat_id)
        await message.answer("🟢 Godmode УВІМКНЕНО — всі кидки = критичний успіх (100)")


async def cmd_debug(message, bot, chat_id, args):
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    godmode_flag = "ON" if chat_id in GODMODE_USERS else "OFF"
    puppet_flag = "ON" if chat_id in PUPPET_USERS else "OFF"
    erotic_flag = "ON" if chat_id in EROTIC_USERS else "OFF"
    name_key = "Ім'я"
    hp_key = "Здоров'я"
    p_name = profile.get(name_key, '?')
    p_house = profile.get('Дім', '?')
    p_title = profile.get('Титул', '?')
    p_hp = profile.get(hp_key, '?')
    p_energy = profile.get('Енергія', '?')
    p_gold = profile.get('Особисте Золото', '?')
    p_loc = profile.get('Поточне місцезнаходження', '?')
    p_scene = profile.get('Поточна сцена', '?')
    p_region = profile.get('Регіон', '?')
    p_time = profile.get('Ігровий час', '?')
    p_inv = profile.get('Інвентар', '-')
    text = (
        f"[DEBUG PROFILE]\n"
        f"Ім'я: {p_name}\n"
        f"Дім: {p_house} | Титул: {p_title}\n"
        f"HP: {p_hp} | Енергія: {p_energy}\n"
        f"Золото: {p_gold}\n"
        f"Локація: {p_loc}\n"
        f"Сцена: {p_scene}\n"
        f"Регіон: {p_region}\n"
        f"Час: {p_time}\n"
        f"Інвентар: {p_inv}\n"
        f"Godmode: {godmode_flag} | Puppet: {puppet_flag} | Erotic: {erotic_flag}"
    )
    await message.answer(text)


async def cmd_puppet(message, bot, chat_id, args):
    if chat_id in PUPPET_USERS:
        PUPPET_USERS.discard(chat_id)
        await message.answer("🔴 Режим Ляльковода ВИМКНЕНО")
    else:
        PUPPET_USERS.add(chat_id)
        await message.answer("🟢 Режим Ляльковода УВІМКНЕНО — всі NPC беззаперечно лояльні")


async def cmd_erotic(message, bot, chat_id, args):
    if chat_id in EROTIC_USERS:
        EROTIC_USERS.discard(chat_id)
        await message.answer("🔴 Еротичний режим ВИМКНЕНО")
    else:
        EROTIC_USERS.add(chat_id)
        await message.answer("🔞 Еротичний режим УВІМКНЕНО — явні описи інтимних сцен")


# ─────────────────────────── Диспетчер ───────────────────────────

async def handle_cheat_command(message, bot):
    chat_id = message.chat.id
    parts = message.text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]

    dispatch = {
        "/heal": cmd_heal,
        "/sethp": cmd_sethp,
        "/setenergy": cmd_setenergy,
        "/setgold": cmd_setgold,
        "/settitle": cmd_settitle,
        "/sethouse": cmd_sethouse,
        "/additem": cmd_additem,
        "/delitem": cmd_delitem,
        "/clearinv": cmd_clearinv,
        "/addskill": cmd_addskill,
        "/setskill": cmd_setskill,
        "/tТакоp": cmd_tp,
        "/addtime": cmd_addtime,
        "/tpnpc": cmd_tpnpc,
        "/setrep": cmd_setrep,
        "/killnpc": cmd_killnpc,
        "/delnpc": cmd_delnpc,
        "/cleanscene": cmd_cleanscene,
        "/godmode": cmd_godmode,
        "/debug": cmd_debug,
        "/puppet": cmd_puppet,
        "/erotic": cmd_erotic,
    }
    handler = dispatch.get(cmd)
    if handler:
        try:
            await handler(message, bot, chat_id, args)
        except Exception as e:
            await message.answer(f"❌ Помилка команди {cmd}: {e}")
