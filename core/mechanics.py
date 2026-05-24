# core/mechanics.py
import random
import json
import re
import asyncio

# Імпортуємо нашого ШІ-клієнта для функцій, які потребують суддівства
from core.ai_client import model_worker, clean_and_parse_json
from core.prompts import (
    build_validate_action_prompt, build_training_request_prompt,
)
import difflib
from core.world_constants import (
    is_valid_location, get_region_for_location, get_locations_for_region,
    TRAVEL_LOCATION, is_valid_scene, get_scenes_for_location,
    VALID_LOCATIONS_ORDERED, LOCATION_TO_REGION, VALID_REGIONS_ORDERED,
)

# === КОНСТАНТИ СИСТЕМИ ===
MINUTES_IN_DAY = 1440
DEFAULT_ENERGY = 1000
DAYS_IN_MONTH = 30
MONTHS_IN_YEAR = 12



def safe_int(value, default=0):
    """Допоміжна функція: перетворює будь-що на ціле число або повертає default"""
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return default


def apply_system_impacts(profile, ai_impacts):
    """
    Приймає абстрактні теги від АІ і перетворює їх на конкретну математику.
    Повертає оновлений профіль і лог змін.
    (Ця функція працює миттєво в пам'яті, залишається синхронною)
    """
    logs = []

    # === 1. ОБРОБКА ЧАСУ ТА КАЛЕНДАРЯ ===
    time_str = profile.get("Ігровий час", "298 рік В.Е., 1-й місяць, День 1, 08:00")
    minutes_passed = ai_impacts.get("minutes_passed", 0)
    energy_impact = ai_impacts.get("energy_impact", "none")

    if minutes_passed > 0 or energy_impact == "sleep":
        date_match = re.search(r'(\d+)\s*рік.*?(\d+)-й\s*місяць.*?День\s*(\d+)', time_str)

        if date_match:
            current_year = int(date_match.group(1))
            current_month = int(date_match.group(2))
            current_day = int(date_match.group(3))
        else:
            current_year, current_month, current_day = 298, 1, 1

        if "Час_хвилини" in profile:
            current_minutes = profile["Час_хвилини"]
        else:
            time_match = re.search(r'(\d{2}):(\d{2})', time_str)
            if time_match:
                current_minutes = int(time_match.group(1)) * 60 + int(time_match.group(2))
            else:
                current_minutes = 8 * 60

        old_time_str = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"

        if energy_impact == "sleep":
            sleep_minutes = (MINUTES_IN_DAY - current_minutes) + (8 * 60)
            minutes_passed = max(minutes_passed, sleep_minutes)

        if minutes_passed > 0:
            cooldowns = profile.get("training_cooldowns", {})
            if cooldowns:
                keys_to_remove = []
                for skill_name, time_left in cooldowns.items():
                    new_time = time_left - minutes_passed
                    if new_time <= 0:
                        keys_to_remove.append(skill_name)
                    else:
                        cooldowns[skill_name] = new_time

                for skill_name in keys_to_remove:
                    del cooldowns[skill_name]

                profile["training_cooldowns"] = cooldowns

        new_total_minutes = current_minutes + minutes_passed
        days_passed = new_total_minutes // MINUTES_IN_DAY
        new_minutes_of_day = new_total_minutes % MINUTES_IN_DAY
        exact_time_str = f"{new_minutes_of_day // 60:02d}:{new_minutes_of_day % 60:02d}"

        total_days = (current_day - 1) + days_passed
        new_day = (total_days % DAYS_IN_MONTH) + 1
        months_passed = total_days // DAYS_IN_MONTH

        total_months = (current_month - 1) + months_passed
        new_month = (total_months % MONTHS_IN_YEAR) + 1
        years_passed = total_months // MONTHS_IN_YEAR

        new_year = current_year + years_passed

        profile["Час_хвилини"] = new_minutes_of_day
        profile["Ігровий час"] = f"{new_year} рік В.Е., {new_month}-й місяць, День {new_day}, {exact_time_str}"

        if years_passed > 0:
            logs.append(f"⏳ З Новим Роком! Тепер {new_year} рік В.Е.")
        elif months_passed > 0:
            logs.append(f"⏳ Почався новий місяць: {new_month}-й.")
        elif days_passed > 0:
            logs.append(f"⏳ Час: {old_time_str} -> {exact_time_str} (Новий день!)")
        else:
            logs.append(f"⏳ Час: {old_time_str} -> {exact_time_str} (+{minutes_passed} хв)")

    # === 1.1. ОБРОБКА ЕНЕРГІЇ ===
    if energy_impact != "none":
        current_energy = safe_int(profile.get("Енергія", DEFAULT_ENERGY), DEFAULT_ENERGY)

        energy_ranges_negative = {
            "spend_small": (1, 3),
            "spend_medium": (4, 8),
            "spend_large": (10, 16)
        }
        energy_ranges_positive = {
            "restore_small": (100, 150),
            "restore_medium": (160, 300)
        }

        delta = 0
        if energy_impact in ["sleep", "restore_full"]:
            delta = DEFAULT_ENERGY - current_energy
        elif energy_impact in energy_ranges_positive:
            min_cost, max_cost = energy_ranges_positive[energy_impact]
            delta = random.randint(min_cost, max_cost)
        elif energy_impact in energy_ranges_negative:
            min_cost, max_cost = energy_ranges_negative[energy_impact]
            delta = -random.randint(min_cost, max_cost)

        new_energy = max(0, min(DEFAULT_ENERGY, current_energy + delta))

        if new_energy != current_energy:
            profile["Енергія"] = new_energy
            sign = "+" if delta > 0 else ""
            logs.append(f"⚡ Енергія: {current_energy} -> {new_energy} ({sign}{delta})")

        if energy_impact in ["sleep", "restore_full"] and profile.get("temp_debuffs"):
            profile["temp_debuffs"] = {}
            logs.append("✨ Після відпочинку тимчасові штрафи знято.")

    # === 2. ОБРОБКА ЗДОРОВ'Я ===
    damage_tag = ai_impacts.get("health_impact", "none")
    hp_change = 0

    if damage_tag == "heal_small": hp_change = random.randint(5, 25)
    elif damage_tag == "heal_full": hp_change = 100
    elif damage_tag == "dmg_light": hp_change = -random.randint(1, 5)
    elif damage_tag == "dmg_medium": hp_change = -random.randint(5, 25)
    elif damage_tag == "dmg_heavy": hp_change = -random.randint(25, 50)
    elif damage_tag == "dmg_fatal": hp_change = -100

    if hp_change != 0:
        old_hp = safe_int(profile.get("Здоров'я", 100), 100)
        new_hp = max(0, min(100, old_hp + hp_change))
        profile["Здоров'я"] = new_hp
        logs.append(f"❤️ Здоров'я: {old_hp} -> {new_hp}")

    # === 3. ОБРОБКА ЗОЛОТА ===
    gold_tag = str(ai_impacts.get("gold_impact", "none")).strip()
    gold_change = 0

    try:
        gold_change = int(gold_tag)
    except ValueError:
        if gold_tag == "spend_small": gold_change = -random.randint(5, 15)
        elif gold_tag == "spend_medium": gold_change = -random.randint(50, 150)
        elif gold_tag == "spend_large": gold_change = -random.randint(300, 800)
        elif gold_tag == "earn_small": gold_change = random.randint(10, 30)
        elif gold_tag == "earn_medium": gold_change = random.randint(100, 300)
        elif gold_tag == "earn_large":
            # earn_large дозволено лише при реальному продажу (є inventory_lost) або квестовій нагороді.
            # Без фізичного продажу — обмежуємо до earn_medium, щоб запобігти Infinite Money Glitch.
            has_sale = bool(ai_impacts.get("inventory_lost"))
            if has_sale:
                gold_change = random.randint(500, 800)
            else:
                gold_change = random.randint(100, 300)
                logs.append("[АУДИТ] earn_large без inventory_lost — обмежено до earn_medium. Перевір логіку транзакції.")

    if gold_change != 0:
        old_gold = safe_int(profile.get("Особисте Золото", 0), 0)
        new_gold = max(0, old_gold + gold_change)
        profile["Особисте Золото"] = new_gold
        logs.append(f"💰 Золото: {gold_change:+}")

    # === 4. ОБРОБКА ІНВЕНТАРЯ ===
    current_inv_str = str(profile.get("Інвентар", ""))
    if current_inv_str in ["-", "Пусто", ""]:
        inventory_list = []
    else:
        inventory_list = [item.strip() for item in current_inv_str.split(',') if item.strip()]

    new_items = ai_impacts.get("inventory_new")
    if new_items:
        if isinstance(new_items, str): new_items = [new_items]
        for item in new_items:
            # Якщо предмет вже є в інвентарі — пропускаємо без логу
            if item.strip().lower() in current_inv_str.lower():
                continue
            # Fallback-захист від галюцинацій: якщо предмет з'являється без золотової транзакції,
            # без бойових подій і без зміни локації/сцени — позначаємо підозрілим для Евалюатора.
            # ВАЖЛИВО: предмет все одно додається (не блокуємо квестові нагороди та подарунки NPC).
            _gold_tag = str(ai_impacts.get("gold_impact", "none"))
            _has_transaction = _gold_tag not in ("none", "0")
            _has_combat = ai_impacts.get("health_impact", "none") != "none"
            _has_scene_change = ai_impacts.get("scene_impact", "none") != "none"
            if not (_has_transaction or _has_combat or _has_scene_change):
                logs.append(f"[БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ] Предмет '{item}' отримано без транзакції, бою або зміни сцени.")
            inventory_list.append(item)
            logs.append(f"➕ Отримано: {item}")

    lost_items = ai_impacts.get("inventory_lost")
    if lost_items:
        if isinstance(lost_items, str): lost_items = [lost_items]
        for item in lost_items:
            for existing_item in inventory_list:
                if item.strip().lower() == existing_item.strip().lower():
                    inventory_list.remove(existing_item)
                    logs.append(f"➖ Втрачено: {existing_item}")
                    break

    profile["Інвентар"] = "Пусто" if not inventory_list else ", ".join(inventory_list)

    # === 5. ОБРОБКА НАВИЧОК ===
    skill_updates = ai_impacts.get("skill_impact", {})
    if skill_updates:
        for skill_name, val in skill_updates.items():
            target_key = None
            for key in profile.keys():
                clean_skill = skill_name.strip().lower()
                clean_key = key.strip().lower()
                if clean_skill == clean_key or f"{clean_skill} навички" == clean_key:
                    target_key = key
                    break

            if target_key:
                old_val = safe_int(profile[target_key], 10)
                new_val = max(0, min(100, old_val + val))
                profile[target_key] = new_val
                sign = "+" if val > 0 else ""
                logs.append(f"{'📈' if val > 0 else '📉'} {skill_name}: {sign}{val} (Стало {new_val})")

    # === 5.5. ОБРОБКА ТИМЧАСОВИХ ШТРАФІВ ===
    temp_debuff_updates = ai_impacts.get("temp_debuff", {})
    if temp_debuff_updates:
        current_debuffs = profile.get("temp_debuffs", {})
        for skill_name, penalty in temp_debuff_updates.items():
            current_debuffs[skill_name] = safe_int(penalty, 0)
            logs.append(f"🩸 Тимчасовий штраф: {skill_name} -{penalty}")
        profile["temp_debuffs"] = current_debuffs

    # === 6. ОБРОБКА ГОДИННИКІВ (CLOCKS) ===
    clocks_updates = ai_impacts.get("clocks_impact", {})
    if clocks_updates:
        current_clocks = profile.get("Годинники", {})
        if isinstance(current_clocks, str):
            current_clocks = {}

        for clock_name, change in clocks_updates.items():
            current_val, max_val = 0, 4
            if clock_name in current_clocks:
                parts = current_clocks[clock_name].split('/')
                current_val, max_val = int(parts[0]), int(parts[1])

            if change == "clear":
                # Поетапний скид залежно від рівня напруги (не бінарний)
                if current_val <= 2:
                    if clock_name in current_clocks:
                        del current_clocks[clock_name]
                    logs.append(f"⏱️ Годинник '{clock_name}' скинуто.")
                elif current_val == 3:
                    current_clocks[clock_name] = "1/4"
                    logs.append(f"⏱️ Втеча: '{clock_name}' знижено 3→1 (гравець вирвався).")
                else:  # current_val == 4
                    current_clocks[clock_name] = "2/4"
                    logs.append(f"⏱️ Втеча з бою: '{clock_name}' знижено 4→2 (погоня можлива).")
            else:
                new_val = min(max_val, current_val + safe_int(change, 0))
                # Burst: годинник щойно досяг максимуму цього ходу
                if current_val < max_val and new_val == max_val:
                    profile["_burst_this_turn"] = clock_name
                # Overflow-лог: годинник вже був на максимумі
                elif current_val == max_val and safe_int(change, 0) > 0:
                    logs.append(f"⏱️ '{clock_name}' вже на максимумі ({max_val}/{max_val}) — напруга продовжує тиснути.")
                current_clocks[clock_name] = f"{new_val}/{max_val}"
                logs.append(f"⏱️ Годинник '{clock_name}': {new_val}/{max_val}")

        profile["Годинники"] = current_clocks

    # === 7. ОБРОБКА ПЕРЕМІЩЕННЯ (ГЛОБАЛЬНА ЛОКАЦІЯ ТА ЛОКАЛЬНА СЦЕНА) ===
    new_location = ai_impacts.get("location_impact", "none")
    if new_location and str(new_location).lower() not in ["none", "немає", "без змін", "", "null"]:
        if not is_valid_location(str(new_location).strip()):
            print(f"🚫 [ЛОКАЦІЯ ЗАБЛОКОВАНА] ШІ повернув неканонічне значення '{new_location}'. "
                  f"Локація збережена: {profile.get('Поточне місцезнаходження', 'Невідомо')}")
        else:
            old_location = profile.get("Поточне місцезнаходження", "Невідомо")
            if old_location != new_location:
                profile["Поточне місцезнаходження"] = str(new_location).strip()
                logs.append(f"🗺️ Локація: {old_location} -> {new_location}")
                # Auto-derive регіон. При TRAVEL_LOCATION — регіон не змінюється.
                if str(new_location).strip() != TRAVEL_LOCATION:
                    new_region = get_region_for_location(str(new_location).strip())
                    if new_region:
                        profile["Регіон"] = new_region
                        logs.append(f"🌍 Регіон: {new_region}")

    new_scene = ai_impacts.get("scene_impact", "none")
    if new_scene and str(new_scene).lower() not in ["none", "немає", "без змін", "", "null"]:
        new_scene = str(new_scene).strip()
        # Валідація: обрізка сцени до 3 слів (як для NPC)
        words = new_scene.split()
        if len(words) > 3:
            new_scene = " ".join(words[:3])
            print(f"✂️ [PLAYER SCENE TRIM] Обрізано до 3 слів: '{new_scene}'")
        # Валідація: canonical scene check + difflib autocorrect
        current_loc = profile.get("Поточне місцезнаходження", "")
        if current_loc and current_loc != TRAVEL_LOCATION:
            if not is_valid_scene(current_loc, new_scene):
                valid_scenes = get_scenes_for_location(current_loc)
                close = difflib.get_close_matches(new_scene, valid_scenes, n=1, cutoff=0.4)
                if close:
                    print(f"🔧 [PLAYER SCENE AUTOCORRECT] '{new_scene}' → '{close[0]}'")
                    new_scene = close[0]
                else:
                    fallback = get_scenes_for_location(current_loc)
                    if fallback:
                        print(f"🔧 [PLAYER SCENE FALLBACK] '{new_scene}' → '{fallback[0]}'")
                        new_scene = fallback[0]
        old_scene = profile.get("Поточна сцена", "Невідомо")
        if old_scene != new_scene:
            profile["Поточна сцена"] = new_scene
            logs.append(f"🚪 Сцена: {old_scene} -> {new_scene}")

    return profile, logs


async def validate_action(user_input, profile):
    """
    Асинхронна функція-фільтр (Цензор).
    Перевіряє, чи не намагається гравець зламати гру чітами, анахронізмами чи діями за когось іншого.
    """
    # Жорстка перевірка смерті ДО виклику ШІ
    if safe_int(profile.get("Здоров'я", 100), 100) <= 0:
        return False, "Ви мертві. Ваша історія завершена, і ви не можете діяти."

    char_name = profile.get("Ім'я", "Герой")
    inventory_list = profile.get("Інвентар", "порожній") or "порожній"

    prompt = build_validate_action_prompt(char_name, user_input, inventory_list)
    try:
        def _sync_gen_val():
            return model_worker.generate_content(prompt)
        response = await asyncio.to_thread(_sync_gen_val)
        result = clean_and_parse_json(response.text)

        if not result:
            return True, ""

        return result.get("is_valid", True), result.get("refusal_reason", "Це неможливо.")
    except Exception as e:
        print(f"⚠️ Помилка валідатора: {e}")
        return True, ""


# === D&D TRAINING CONSTANTS ===
# Solo: free, slow (2 days), heavy energy drain, partial XP on exhaustion failure.
SOLO_COSTS = {
    "cost_gold": 0,
    "cost_time_days": 2,
    "energy_cost": 40,
    "xp_gained_base": 1500,
    "hp_damage_dice": "none",  # solo practice — no physical trauma
}
# Mentor: paid, fast (1 day), moderate energy, double XP.
MENTOR_COSTS = {
    "cost_gold": 50,
    "cost_time_days": 1,
    "energy_cost": 25,
    "xp_gained_base": 3000,
    "hp_damage_dice": "none",
}
# Failure path (low-energy solo): partial XP + 1d4 HP trauma from overexertion.
_FAILURE_XP = 500
_FAILURE_HP_DMG = "1d4"

# Valid D&D skills for training (must match core/dnd_skills.py SKILLS dict).
_VALID_DND_TRAINING_SKILLS = frozenset({
    "Athletics", "Acrobatics", "Sleight of Hand", "Stealth",
    "Arcana", "History", "Investigation", "Nature", "Religion",
    "Animal Handling", "Insight", "Medicine", "Perception", "Survival",
    "Deception", "Intimidation", "Performance", "Persuasion",
})


async def process_training_request(
    user_input: str,
    profile: dict,
    current_scene: str | None = None,
) -> dict | None:
    """Detects training intent and awards XP toward specific D&D skill mastery.

    Returns:
        None                  — not a training action (is_training=False from LLM)
        {"error": str}        — training declined (scene unsafe, insufficient gold, bad skill)
        {                     — training succeeded
            "story_prompt": str,
            "skill": str,           one of 18 D&D skills
            "xp_gained": int,
            "method": "solo" | "mentor",
            "cost_gold": int,
            "cost_time_days": int,
            "energy_cost": int,
            "hp_damage_dice": str,  "1d4" or "none"
        }
    """
    from core.dnd_skills import is_valid_skill
    from core.dnd_progression import award_xp

    # --- Build skill modifier context for the prompt ---
    skill_modifiers: dict = {}
    try:
        from core.dnd_skills import skill_modifier, SKILLS
        for sk in SKILLS:
            skill_modifiers[sk] = skill_modifier(profile, sk)
    except Exception:
        skill_modifiers = {}

    user_gold = safe_int(profile.get("Особисте Золото", profile.get("gold", 0)), 0)

    prompt = build_training_request_prompt(
        user_input=user_input,
        current_scene=current_scene,
        skill_modifiers=skill_modifiers,
        gold=user_gold,
    )

    resp = None
    try:
        def _sync_gen_train():
            return model_worker.generate_content(prompt)
        resp = await asyncio.to_thread(_sync_gen_train)
        data = clean_and_parse_json(resp.text)
    except Exception as e:
        print(f"⚠️ Training Error: {e}")
        return None

    if not data:
        if resp is not None:
            print(f"⚠️ [TRAINING] JSON parse returned None. Raw (200 chars): {resp.text[:200]!r}")
        return None
    if not data.get("is_training"):
        print(f"⚠️ [TRAINING] is_training=False. Parsed: {data}")
        return None
    if not data.get("is_possible"):
        return {
            "error": f"Зараз неможливо розпочати тренування: {data.get('reason_if_failed', 'Небезпечна ситуація.')}"
        }

    skill = str(data.get("skill", "")).strip()
    method = str(data.get("method", "solo")).strip()

    # --- Validate skill ---
    if not skill or not is_valid_skill(skill):
        return {
            "error": f"Невідомий скіл для тренування: '{skill}'. Оберіть один з 18 D&D навичок."
        }

    # --- Resolve costs by method ---
    if method == "mentor":
        costs = dict(MENTOR_COSTS)
    else:
        method = "solo"
        costs = dict(SOLO_COSTS)

    cost_gold = costs["cost_gold"]
    cost_time_days = costs["cost_time_days"]
    energy_cost = costs["energy_cost"]
    xp_gained = costs["xp_gained_base"]
    hp_damage_dice = costs["hp_damage_dice"]

    # --- Gold check (mentor only) ---
    if method == "mentor" and user_gold < cost_gold:
        return {
            "error": f"Не вистачає золота для наставника. Потрібно {cost_gold} 🪙, а є {user_gold} 🪙."
        }

    # --- Energy path: insufficient energy → failure path ---
    current_energy = safe_int(profile.get("Енергія", DEFAULT_ENERGY), DEFAULT_ENERGY)
    failure_path = (current_energy < energy_cost)

    if failure_path:
        xp_gained = _FAILURE_XP
        hp_damage_dice = _FAILURE_HP_DMG
        story_prompt = (
            f"SYSTEM: Player tried to train '{skill}' ({method}) while dangerously exhausted. "
            f"They pushed through cramps and dizziness but achieved only partial results. "
            f"Award {xp_gained} XP (partial gain). They take 1d4 HP damage from overexertion. "
            f"Describe the painful, messy training session — sloppy form, torn muscle, bruised pride."
        )
    elif method == "mentor":
        story_prompt = (
            f"SYSTEM: Player spends {cost_time_days} day(s) training '{skill}' under a mentor "
            f"(cost: {cost_gold} gold, {energy_cost} energy). "
            f"They gain {xp_gained} XP. Describe guided, efficient practice — correction of bad habits, "
            f"expert drills, measurable improvement."
        )
    else:
        story_prompt = (
            f"SYSTEM: Player spends {cost_time_days} day(s) training '{skill}' solo "
            f"(cost: {energy_cost} energy). "
            f"They gain {xp_gained} XP. Describe self-directed, gruelling practice — repetition, "
            f"improvised exercises, slow but steady improvement."
        )

    # --- Award XP now (inside process_training_request, not engine) ---
    xp_result = award_xp(profile, xp_gained, reason=f"training: {skill}")
    print(
        f"[TRAINING] skill={skill} method={method} xp={xp_gained} "
        f"level {xp_result.level_before}→{xp_result.level_after} "
        f"failure_path={failure_path}"
    )

    return {
        "story_prompt": story_prompt,
        "skill": skill,
        "xp_gained": xp_gained,
        "method": method,
        "cost_gold": cost_gold,
        "cost_time_days": cost_time_days,
        "energy_cost": energy_cost,
        "hp_damage_dice": hp_damage_dice,
        "leveled_up": xp_result.leveled_up,
        "level_before": xp_result.level_before,
        "level_after": xp_result.level_after,
    }

