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
from config import GODMODE_USERS, PUPPET_USERS, EROTIC_USERS


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
    """Full heal: D&D hp_current = hp_max.  Legacy 'Здоров'я' synced to 100."""
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return

    if "hp_current" in profile:
        # D&D profile: restore to hp_max
        hp_max = safe_int(profile.get("hp_max", 0), 0)
        if hp_max <= 0:
            # hp_max missing or zero — use fallback and warn
            hp_max = 10
            profile["hp_max"] = hp_max
            await message.answer(
                "⚠️ hp_max відсутній або дорівнює 0 — встановлено fallback hp_max=10. "
                "Виконай /start або /debug щоб перевірити профіль."
            )
        profile["hp_current"] = hp_max
        # Sync legacy UI field (CLAUDE.md §5.2)
        profile["Здоров'я"] = 100
        await _save_profile(chat_id, profile)
        await message.answer(f"✅ HP відновлено до {hp_max}/{hp_max} (повне зцілення)")
    else:
        # Legacy profile: restore to 100
        profile["Здоров'я"] = 100
        await _save_profile(chat_id, profile)
        await message.answer("✅ HP відновлено до 100")


async def cmd_sethp(message, bot, chat_id, args):
    """Set HP.  For D&D profiles: writes hp_current clamped to [0, hp_max] and
    syncs the legacy 'Здоров'я' UI field.  Does NOT write to 'Здоров'я' directly.
    For legacy profiles (no hp_current): writes 'Здоров'я' clamped to [0, 100]."""
    if not args:
        await message.answer("Використання: /sethp <число>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        raw_val = int(args[0])
    except ValueError:
        await message.answer("❌ Значення повинно бути цілим числом"); return

    if "hp_current" in profile:
        # D&D profile: clamp to [0, hp_max] (CLAUDE.md §5.2 — HP variable)
        hp_max = safe_int(profile.get("hp_max", 0), 0)
        if hp_max <= 0:
            hp_max = 10
            profile["hp_max"] = hp_max
            await message.answer(
                "⚠️ hp_max відсутній або дорівнює 0 — встановлено fallback hp_max=10."
            )
        val = _clamp(raw_val, 0, hp_max)
        profile["hp_current"] = val
        # Sync legacy UI field (CLAUDE.md §5.2: handlers.py reads "Здоров'я")
        _hp_key = "Здоров'я"
        ui_pct = round(100 * val / hp_max) if hp_max > 0 else 0
        profile[_hp_key] = ui_pct
        await _save_profile(chat_id, profile)
        await message.answer(f"✅ hp_current → {val}/{hp_max} (UI Здоровя → {ui_pct}%)")
    else:
        # Legacy profile: write to 'Здоров'я' (0-100 scale)
        val = _clamp(raw_val, 0, 100)
        profile["Здоров'я"] = val
        await _save_profile(chat_id, profile)
        await message.answer(f"✅ Здоров'я → {val}")


async def cmd_setenergy(message, bot, chat_id, args):
    """Set energy.  Energy scale is 0–1000 (CLAUDE.md §5.2 legacy scale)."""
    if not args:
        await message.answer("Використання: /setenergy <0-1000>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        val = _clamp(int(args[0]), 0, 1000)
    except ValueError:
        await message.answer("❌ Значення повинно бути числом"); return
    profile["Енергія"] = val
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ Енергія → {val}/1000")


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


def _resolve_dnd_skill(raw_name: str) -> tuple[str | None, list[str]]:
    """Case-insensitive lookup of a D&D 5e skill name.

    Returns (canonical_name, top5_suggestions).
    canonical_name is None if no exact match found.
    top5_suggestions is a list of up to 5 closest skill names for error messages.
    """
    from core.dnd_skills import SKILLS
    # Exact case-insensitive match
    raw_lower = raw_name.strip().lower()
    for skill in SKILLS:
        if skill.lower() == raw_lower:
            return skill, []
    # Prefix match
    prefix_matches = [s for s in SKILLS if s.lower().startswith(raw_lower)]
    if len(prefix_matches) == 1:
        return prefix_matches[0], []
    # Fallback: top-5 by difflib similarity
    import difflib
    suggestions = difflib.get_close_matches(raw_name, SKILLS.keys(), n=5, cutoff=0.3)
    if not suggestions:
        suggestions = list(SKILLS.keys())[:5]
    return None, suggestions


async def cmd_addskill(message, bot, chat_id, args):
    """Add delta to a D&D 5e skill proficiency in profile['skill_profs'] context.

    Validates skill name against core.dnd_skills.SKILLS (18 D&D 5e skills).
    Stores result in profile['skills'][<SkillName>] clamped to [0, 100].
    """
    if len(args) < 2:
        await message.answer("Використання: /addskill <D&D Skill Name> <Очки>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        delta = int(args[-1])
    except ValueError:
        await message.answer("❌ Очки повинні бути числом"); return
    raw_skill = " ".join(args[:-1])
    canonical, suggestions = _resolve_dnd_skill(raw_skill)
    if canonical is None:
        suggestion_str = ", ".join(suggestions) if suggestions else "(немає близьких)"
        await message.answer(
            f"❌ Невідома D&D навичка: '{raw_skill}'\n"
            f"Схожі: {suggestion_str}\n"
            f"Повний список: Athletics, Acrobatics, Sleight of Hand, Stealth, "
            f"Arcana, History, Investigation, Nature, Religion, "
            f"Animal Handling, Insight, Medicine, Perception, Survival, "
            f"Deception, Intimidation, Performance, Persuasion"
        )
        return
    skills_dict = profile.setdefault("skills", {})
    current = safe_int(skills_dict.get(canonical, 0), 0)
    new_val = _clamp(current + delta, 0, 100)
    skills_dict[canonical] = new_val
    profile["skills"] = skills_dict
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ {canonical}: {current} → {new_val}")


async def cmd_setskill(message, bot, chat_id, args):
    """Set a D&D 5e skill to an absolute value in profile['skills'][<SkillName>].

    Validates skill name against core.dnd_skills.SKILLS (18 D&D 5e skills).
    Value clamped to [0, 100].
    """
    if len(args) < 2:
        await message.answer("Використання: /setskill <D&D Skill Name> <0-100>"); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return
    try:
        val = _clamp(int(args[-1]), 0, 100)
    except ValueError:
        await message.answer("❌ Очки повинні бути числом від 0 до 100"); return
    raw_skill = " ".join(args[:-1])
    canonical, suggestions = _resolve_dnd_skill(raw_skill)
    if canonical is None:
        suggestion_str = ", ".join(suggestions) if suggestions else "(немає близьких)"
        await message.answer(
            f"❌ Невідома D&D навичка: '{raw_skill}'\n"
            f"Схожі: {suggestion_str}\n"
            f"Повний список: Athletics, Acrobatics, Sleight of Hand, Stealth, "
            f"Arcana, History, Investigation, Nature, Religion, "
            f"Animal Handling, Insight, Medicine, Perception, Survival, "
            f"Deception, Intimidation, Performance, Persuasion"
        )
        return
    skills_dict = profile.setdefault("skills", {})
    skills_dict[canonical] = val
    profile["skills"] = skills_dict
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ {canonical} → {val}")


_VALID_ABILITIES = frozenset({"STR", "DEX", "CON", "INT", "WIS", "CHA"})


async def cmd_setability(message, bot, chat_id, args):
    """Set a D&D ability score.  Usage: /setability <STR|DEX|CON|INT|WIS|CHA> <1-20>

    Validates:
    - ability ∈ {STR, DEX, CON, INT, WIS, CHA} (case-insensitive)
    - value ∈ [1, 20] (CLAUDE.md §5.2 ability score range)
    Writes to profile['ability_scores'][<ABILITY>].
    """
    if len(args) < 2:
        await message.answer(
            "Використання: /setability <STR|DEX|CON|INT|WIS|CHA> <1-20>"
        ); return
    profile, _ = await _get_profile(chat_id)
    if not profile:
        await message.answer("❌ Профіль не знайдено"); return

    ability = args[0].upper().strip()
    if ability not in _VALID_ABILITIES:
        await message.answer(
            f"❌ Невідома характеристика: '{args[0]}'\n"
            f"Допустимі: STR, DEX, CON, INT, WIS, CHA"
        ); return

    try:
        raw_val = int(args[1])
    except ValueError:
        await message.answer("❌ Значення повинно бути числом від 1 до 20"); return

    if raw_val < 1 or raw_val > 20:
        await message.answer(
            f"❌ Значення {raw_val} поза межами [1, 20] (CLAUDE.md §5.2)"
        ); return

    ability_scores = profile.setdefault("ability_scores", {})
    old_val = ability_scores.get(ability, "?")
    ability_scores[ability] = raw_val
    profile["ability_scores"] = ability_scores
    await _save_profile(chat_id, profile)
    await message.answer(f"✅ {ability}: {old_val} → {raw_val}")


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
    admin_user_id = message.from_user.id

    def _sync_tpnpc():
        from database.operations import _npc_tab_name
        tab_name = _npc_tab_name(admin_user_id)
        ws = db.get_sheet(tab_name)
        if not ws:
            return f"❌ Таблицю {tab_name} не знайдено"
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
    admin_user_id = message.from_user.id

    def _sync_setrep():
        from database.operations import _npc_tab_name
        tab_name = _npc_tab_name(admin_user_id)
        ws = db.get_sheet(tab_name)
        if not ws:
            return f"❌ Таблицю {tab_name} не знайдено"
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
    admin_user_id = message.from_user.id

    def _sync_killnpc():
        from database.operations import _npc_tab_name
        tab_name = _npc_tab_name(admin_user_id)
        ws = db.get_sheet(tab_name)
        if not ws:
            return f"❌ Таблицю {tab_name} не знайдено"
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
        await refresh_npc_database(admin_user_id)          # спочатку resync (може повернути стале)
        evict_npc_from_cache(admin_user_id, npc_name_matched)  # потім гарантоване видалення
    await message.answer(result)


async def cmd_delnpc(message, bot, chat_id, args):
    if not args:
        await message.answer("Використання: /delnpc <Ім'я NPC>"); return
    npc_name = " ".join(args)
    admin_user_id = message.from_user.id

    def _sync_delnpc():
        from database.operations import _npc_tab_name
        tab_name = _npc_tab_name(admin_user_id)
        ws = db.get_sheet(tab_name)
        if not ws:
            return f"❌ Таблицю {tab_name} не знайдено"
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
    admin_user_id = message.from_user.id

    def _sync_cleanscene():
        from database.operations import _npc_tab_name
        tab_name = _npc_tab_name(admin_user_id)
        ws = db.get_sheet(tab_name)
        if not ws:
            return f"❌ Таблицю {tab_name} не знайдено"
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


async def cmd_thoughts(message, bot, chat_id, args):
    from core.ai_client import get_thoughts_log
    entries = get_thoughts_log()
    if not entries:
        await message.answer("💭 Роздумів немає — зробіть хід спочатку.")
        return
    lines = []
    for i, e in enumerate(entries, 1):
        short_model = e["model"].replace("gemma-4-31b-it", "G4").replace("gemma-3-27b-it", "G3")
        lines.append(f"[{i}] {short_model}\n{e['thought'].strip()}")
    text = "💭 Роздуми останнього ходу:\n\n" + "\n\n─────\n\n".join(lines)
    # Розбиваємо на частини по 4000 символів якщо потрібно
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        await message.answer(chunk, parse_mode=None)


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
        "/setability": cmd_setability,
        "/tp": cmd_tp,
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
        "/thoughts": cmd_thoughts,
    }
    handler = dispatch.get(cmd)
    if handler:
        try:
            await handler(message, bot, chat_id, args)
        except Exception as e:
            await message.answer(f"❌ Помилка команди {cmd}: {e}")
