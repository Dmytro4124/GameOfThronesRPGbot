# core/mechanics.py
import random
import json
import re
import asyncio

# Імпортуємо нашого ШІ-клієнта для функцій, які потребують суддівства
from core.ai_client import model_worker, clean_and_parse_json

# === КОНСТАНТИ СИСТЕМИ ===
MINUTES_IN_DAY = 1440
DEFAULT_ENERGY = 100
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
            "restore_small": (10, 15),
            "restore_medium": (16, 30)
        }

        delta = 0
        if energy_impact in ["sleep", "restore_full"]:
            delta = 100 - current_energy
        elif energy_impact in energy_ranges_positive:
            min_cost, max_cost = energy_ranges_positive[energy_impact]
            delta = random.randint(min_cost, max_cost)
        elif energy_impact in energy_ranges_negative:
            min_cost, max_cost = energy_ranges_negative[energy_impact]
            delta = -random.randint(min_cost, max_cost)

        new_energy = max(0, min(100, current_energy + delta))

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
        elif gold_tag == "earn_large": gold_change = random.randint(1000, 2000)

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
        old_location = profile.get("Поточне місцезнаходження", "Невідомо")
        if old_location != new_location:
            profile["Поточне місцезнаходження"] = str(new_location).strip()
            logs.append(f"🗺️ Регіон: {old_location} -> {new_location}")

    new_scene = ai_impacts.get("scene_impact", "none")
    if new_scene and str(new_scene).lower() not in ["none", "немає", "без змін", "", "null"]:
        old_scene = profile.get("Поточна сцена", "Невідомо")
        if old_scene != new_scene:
            profile["Поточна сцена"] = str(new_scene).strip()
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

    prompt = f"""You are the Lore Keeper for "Game of Thrones" (Medieval Fantasy). Check the player’s action for legality.

        PLAYER: {char_name}
        ACTION: "{user_input}"
        INVENTORY: {inventory_list}

        === CRITICAL OVERRIDE — VERBAL BLUFF (CHECK FIRST) ===
        If the action is PURELY verbal (speech, commands, declarations, boasts, lies) — return is_valid: true IMMEDIATELY. Verbal bluffs override ALL other rules.
        ✅ "I tell her I have a dragon egg" — verbal claim, LEGAL.
        ✅ "I shout that I am the true king" — declaration, LEGAL.

        === BLOCK (is_valid: false) if ANY of these ===
        1. OUTCOME CONTROL: Player describes RESULT, not attempt ("I cut off his head" ❌ vs "I swing at his neck" ✅; "I convince him" ❌ vs "I try to persuade" ✅).
        2. NPC PUPPETING: Player writes what NPC does ("Drogo laughs" ❌, "The guard lets me pass" ❌). Player may give orders ("I order Jorah to attack" ✅).
        3. ITEM FRAUD: Player PHYSICALLY produces/uses an item NOT in INVENTORY ("I pull out a Valyrian sword" ❌).
        4. ANACHRONISMS: Modern technology or concepts (F-16, telephone, internet, NATO).
        5. META-GAMING: Controlling the plot as author ("Skip to the end", "A dragon saves me").

        === ALLOW (is_valid: true) ===
        Risky actions, jokes, conversations, anything physically possible in Westeros.

        === refusal_reason STYLE ===
        Ukrainian ONLY. 1-2 sentences. Voice of a sardonic medieval Narrator. No modern or English words.

        OUTPUT STRICTLY VALID JSON. NO MARKDOWN. NO BACKTICKS.
        {{
            "is_valid": true,
            "refusal_reason": ""
        }}
        """
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


async def process_training_request(user_input, profile, current_scene=None):
    """
    Асинхронний Worker (Тренування): Хардкорна система (+1 бал), Гібрид Енергії та Кулдаунів (Асиміляція).
    """
    current_energy = safe_int(profile.get("Енергія", DEFAULT_ENERGY), DEFAULT_ENERGY)
    if current_energy <= 0:
        return {
            "error": "Ваша енергія на нулі. В очах темніє, ноги не тримають. Тренування абсолютно неможливе — вам терміново потрібен сон, інакше ви втратите свідомість на місці."
        }

    prompt = f"""YOU ARE THE GAME MASTER. The player might be trying to train a skill.

    User Input: "{user_input}"
    Current Scene: {current_scene or "Unknown"}
    Player Skills: Бойові={profile.get("Бойові навички", 10)}, Військові={profile.get("Військові навички", 10)}, Інтрига={profile.get("Інтрига", 10)}, Управління={profile.get("Управління", 10)}
    Player Gold: {profile.get("Особисте Золото", 0)}

    RULES FOR TRAINING:
    1. CONTEXT CHECK: Is the current scene safe to spend days training? (Active combat, stealth mission, or tense dialogue = impossible. Tavern, camp, training yard = possible).
    2. METHOD CHECK: Solo ("I train") vs Mentor ("I pay a master / read a book").

    OUTPUT STRICTLY VALID JSON ONLY. NO MARKDOWN. NO BACKTICKS. NO CODE FENCES.
    {{
        "is_training": true,
        "is_possible": true,
        "skill": "Бойові",
        "method": "solo",
        "reason_if_failed": ""
    }}
    """

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

    skill_target = data.get("skill")
    method = data.get("method", "solo")

    # === ПЕРЕВІРКА КУЛДАУНУ (АСИМІЛЯЦІЇ) ===
    cooldowns = profile.get("training_cooldowns", {})
    current_cooldown_mins = safe_int(cooldowns.get(skill_target, 0), 0)

    if current_cooldown_mins > 0:
        days_left = max(1, current_cooldown_mins // MINUTES_IN_DAY)
        return {
            "error": f"Ваше тіло та розум ще засвоюють попередні уроки з навички '{skill_target}'. Нервовій системі потрібен час на відновлення. Зачекайте ще приблизно {days_left} ігрових днів, перш ніж тренувати це знову."
        }

    current_val = safe_int(profile.get(f"{skill_target} навички", profile.get(skill_target, 10)), 10)
    user_gold = safe_int(profile.get("Особисте Золото", 0), 0)

    # Математика Часу та Золота
    if method == "solo":
        if current_val >= 50:
            return {
                "error": f"Навичка '{skill_target}' вже {current_val}. Межа самостійного навчання досягнута. Шукайте майстра."
            }
        cost_gold = 0
        cost_time_days = 3 + (current_val // 10)
    else:  # mentor
        cost_gold = current_val * 2
        cost_time_days = 2 + (current_val // 15)
        if user_gold < cost_gold:
            return {"error": f"Не вистачає золота. Потрібно {cost_gold} 🪙, а є {user_gold} 🪙."}

    # Гібридна система Енергії та Перетренування
    energy_cost = 40
    stat_gain = 0
    temp_debuff = 0

    # Встановлюємо новий кулдаун: час самого тренування + 7 днів відпочинку (у хвилинах)
    new_cooldown_mins = (cost_time_days + 7) * MINUTES_IN_DAY

    if current_energy >= energy_cost:
        stat_gain = 1
        story_prompt = f"SYSTEM: Player spends {cost_time_days} days and {cost_gold} gold practicing '{skill_target}' ({method}). They gain +1 to the skill but lose 40 Energy. Describe the grueling process."
    else:
        if random.random() < 0.75:  # 75% травма
            temp_debuff = random.randint(15, 20)
            new_cooldown_mins = 14 * MINUTES_IN_DAY  # 14 днів кулдауну через травму
            story_prompt = f"SYSTEM: Player tried to train '{skill_target}' while exhausted. IT WAS A DISASTER. They tore a muscle/had a breakdown. Gain NO stats, get -{temp_debuff} temporary debuff. Describe the painful failure."
        else:  # 25% диво
            stat_gain = 1
            story_prompt = f"SYSTEM: Player fanatically trained '{skill_target}' while completely exhausted. Miraculously gained +1, but collapsed after. Describe this desperate push."

    return {
        "success": True,
        "skill": skill_target,
        "cost_gold": cost_gold,
        "cost_time": cost_time_days,
        "stat_gain": stat_gain,
        "energy_cost": energy_cost,
        "temp_debuff": temp_debuff,
        "set_cooldown": new_cooldown_mins,
        "story_prompt": story_prompt
    }


def get_npc_reputation_modifier(reputation_score):
    """Детермінований модифікатор DC та обставин на основі числової репутації NPC.
    Returns: (circumstance_override, dc_modifier, is_blocked)"""
    score = safe_int(reputation_score, 0)
    if score >= 80:
        return "ADVANTAGE", -20, False
    elif score >= 40:
        return "NORMAL", -10, False
    elif score <= -80:
        return "BLOCK", 0, True
    elif score <= -40:
        return "DISADVANTAGE", 20, False
    return "NORMAL", 0, False


def get_reputation_delta(outcome):
    """Повертає зміну репутації NPC залежно від результату перевірки Інтриги/Управління."""
    deltas = {
        "CRITICAL SUCCESS": 10,
        "SUCCESS": 5,
        "FAILURE": -3,
        "CRITICAL FAILURE": -8,
    }
    return deltas.get(outcome, 0)


async def resolve_action_mechanics(user_input, profile, npc_reputation_context=None,
                                    current_scene=None, npc_names=None, last_turn_summary=None):
    """
    Асинхронний Worker (4b): Оцінює складність дії та рахує всю механіку (Здоров'я, Час, Золото, Енергія, Годинники, Локація).
    ІНТЕГРОВАНО: Система 2d50 (Roll-Over), Перевага/Недолік, Хардкорні Крити та Прокачка.
    Не пише художній текст!
    npc_reputation_context: dict {npc_name: reputation_score} для NPC у поточній сцені.
    current_scene: str — назва поточної мікролокації.
    npc_names: list — імена NPC присутніх у сцені.
    last_turn_summary: str — короткий опис попереднього ходу для контексту.
    """
    def roll_2d50():
        return random.randint(1, 50) + random.randint(1, 50)

    clocks_info = profile.get("Годинники", {})
    temp_debuffs = profile.get("temp_debuffs", {})

    skills = {
        "Бойові":     max(1, safe_int(profile.get("Бойові навички", 10), 10)     - safe_int(temp_debuffs.get("Бойові", 0), 0)),
        "Військові":  max(1, safe_int(profile.get("Військові навички", 10), 10)  - safe_int(temp_debuffs.get("Військові", 0), 0)),
        "Інтрига":    max(1, safe_int(profile.get("Інтрига", 10), 10)            - safe_int(temp_debuffs.get("Інтрига", 0), 0)),
        "Управління": max(1, safe_int(profile.get("Управління", 10), 10)         - safe_int(temp_debuffs.get("Управління", 0), 0)),
    }

    # ЖОРСТКЕ ПЕРЕХОПЛЕННЯ (ВТРАТА СВІДОМОСТІ ДО ШІ)
    current_energy = safe_int(profile.get("Енергія", DEFAULT_ENERGY), DEFAULT_ENERGY)
    sleep_words = ["спл", "сон", "відпоч", "ляга", "sleep", "rest", "засин"]

    if current_energy <= 0 and not any(w in user_input.lower() for w in sleep_words):
        updates = {
            "action_type": "standard",
            "skill_used": "None",
            "outcome": "CRITICAL FAILURE",
            "circumstance": "DISADVANTAGE",
            "minutes_passed": 480,
            "energy_impact": "sleep",
            "health_impact": "none",
            "location_impact": "none",
            "scene_impact": "none",
            "clocks_impact": {"Scene_Tension": 1}
        }
        verdict = "MECHANICAL VERDICT: EXHAUSTION COLLAPSE! GM INFO: ABSOLUTE OVERRIDE. The player's energy is 0. Ignore their requested action. Describe how they suddenly lose consciousness and collapse on the spot from complete exhaustion. Fast forward 8 hours of them being passed out. Describe what happens to them while they are defenseless."
        return verdict, updates

    prompt = f"""<system>
        You are the System Engine for a Grimdark RPG (Game of Thrones).
        Your ONLY job is to calculate the mechanical outcome of the player's action using a ROLL-OVER system (Roll + Skill vs DC).
        </system>

        <data>
        Player Action: "{user_input}"
        Player Skills: {json.dumps(skills, ensure_ascii=False)}
        Active Clocks (Tension): {json.dumps(clocks_info, ensure_ascii=False)}
        NPC Reputation Context: {json.dumps(npc_reputation_context or {}, ensure_ascii=False)}
        Current Scene: {current_scene or "Unknown"}
        NPCs Present: {", ".join(npc_names) if npc_names else "None"}
        Last Turn: {last_turn_summary or "Game start"}
        </data>

        <npc_reputation_guide>
        If the player's action targets a specific NPC, consider their reputation score when setting DC:
        - Score >= 80 (Devoted): Lower DC by 20, consider ADVANTAGE for social skills.
        - Score 40-79 (Friendly): Lower DC by 10.
        - Score -39 to 39 (Neutral): No modifier.
        - Score -79 to -40 (Hostile): Raise DC by 20, consider DISADVANTAGE for social skills.
        - Score <= -80 (Blood Enemy): Social actions should use maximum DC (140+).
        NOTE: This guide applies ONLY to social skills (Інтрига, Управління). Combat/Military skills are unaffected by reputation.
        </npc_reputation_guide>

        <rules>
        1. CLASSIFY REQUIRED SKILL (STRICT):
           - "Бойові": Direct physical combat, sword fighting, wrestling, punching, kicking, shooting.
             KEYWORD OVERRIDE: If the action contains physical violence words (бити, вдарити, рубати, стріляти, штрикнути, кулак, меч, ніж, атакувати, напасти, душити, ламати) — ALWAYS classify as "Бойові", regardless of previous context.
           - "Інтрига": Sneaking, hiding, stealing, lying, bluffing, persuasion (ONLY if NO physical violence keywords are present).
           - "Військові": Tactics, commanding, assessing battlefield, spotting ambushes.
           - "Управління": Using noble status, bribery, trade, official orders.
           - "None": Mundane tasks (walking, looking, casual chat). 🚨 THE "NO ROLL" RULE: If no NPC is actively attacking/stopping the player, use "None".

        2. DETERMINE CIRCUMSTANCE:
           - "ADVANTAGE": Surprise, high ground, great leverage.
           - "NORMAL": Fair conditions, standard risk.
           - "DISADVANTAGE": ONLY when physically injured, outnumbered in active combat, or acting while severely debilitated. Social pressure alone is NOT disadvantage.

        3. ASSESS DIFFICULTY (Target DC):
           - 60 (Very Easy): Beating a weak, unarmed peasant.
           - 80 (Easy): A basic task, fighting a drunk thug.
           - 100 (Normal): Standard challenge, fighting a trained guard.
           - 120 (Hard): Fighting a skilled knight, sneaking into heavily guarded keep.
           - 140 (Extreme): Dragon, multiple knights, deadly trap.
           DC ESCALATION RULE: If the player's action targets royalty, high lords, or extremely powerful NPCs — DC MUST be at least 120. If the action is outrageous (accusing the King of treason, demanding a lordship, blatant lies with no proof) — DC MUST be 140.

        4. INTENT CLASSIFICATION:
           - If player attempts to spend a long period practicing/studying a specific skill -> "training".
           - Otherwise -> "standard".

        5. TAGS GUIDE (STRICT VALUES REQUIRED):
           - minutes_passed: 1-2 (combat/quick action - STRICT OVERRIDE), 15-30 (conversation/lockpicking), 60-120 (travel), 480-600 (sleep).
           - health_impact: "none", "heal_small", "dmg_light" (-5 HP), "dmg_medium" (-15 HP), "dmg_heavy" (-30 HP), "dmg_fatal" (-100 HP).
             WHEN TO APPLY DAMAGE:
             a) Enemy hits the player (SUCCESS of enemy attack or FAILURE of player defense).
             b) Critical failure on any action.
             c) ACTIVE VIOLENCE RULE: If Scene_Tension >= 3/4, the scene involves active combat. NPCs DO NOT stop attacking just because the player chose a non-combat action (talking, running, looking around). Apply appropriate health_impact even if the player's current action is social or passive.
           - gold_impact: EXACT NUMBER as string (e.g., "-5", "10") IF specified. Otherwise: "none", "spend_small", "spend_medium", "spend_large", "earn_small", "earn_medium", "earn_large".
           - energy_impact: "none", "spend_small" (minor stress/walk), "spend_medium" (argument, training), "spend_large" (combat, labor), "restore_small" (food/fire), "restore_medium" (tavern bed), "restore_full" (sleep).
           - clocks_impact: {{"Scene_Tension": 1}} (if suspicious/aggressive/fail), {{"Scene_Tension": "clear"}} (if player leaves the scene/location). Max is 4.
             CLEAR RULE: Output "clear" for Scene_Tension ONLY when the player physically leaves the current scene/location (and you also output a scene_impact or location_impact change).
             The engine will auto-apply a stepped reduction based on current tension — NOT a full reset:
             ST <= 2 → full clear | ST = 3 → drops to 1/4 | ST = 4 → drops to 2/4.
             For escape actions (тікаю, біжу, тікати), output "clear" combined with a scene/location change.
             Accusations, threats, insults, and aggressive actions MUST increment tension (+1). NEVER output "clear" during aggression or without a location change.

        6. MOVEMENT AND LOCATIONS (CRITICAL):
           - If the player explicitly leaves a room, building, or travels locally, you MUST output the new micro-location in "scene_impact" (e.g., "Гавань", "Вулиці", "Таверна").
           - If they travel to an entirely new city or region, update "location_impact" (e.g., "Браавос", "Королівська Гавань") AND update "scene_impact" to a logical starting point there.
           - If they stay in the current scene, output "none" for both.
        </rules>

        <example_output>
        {{
            "reasoning": "Player explicitly said 'I go outside', so scene changes to Вулиці. No combat, no roll needed.",
            "action_type": "standard",
            "skill_used": "None",
            "difficulty": 0,
            "circumstance": "NORMAL",
            "verdict_text": "Player walks outside.",
            "updates": {{
                 "minutes_passed": 15,
                 "location_impact": "none",
                 "scene_impact": "Вулиці",
                 "health_impact": "none",
                 "energy_impact": "spend_small",
                 "gold_impact": "none",
                 "inventory_new": [],
                 "inventory_lost": [],
                 "clocks_impact": {{}}
            }}
        }}
        </example_output>

        OUTPUT IN STRICT JSON FORMAT ONLY:
        {{
            "reasoning": "Your brief mechanical reasoning here",
            "action_type": "standard or training",
            "skill_used": "Бойові or Військові or Інтрига or Управління or None",
            "difficulty": 100,
            "circumstance": "NORMAL",
            "verdict_text": "Short instruction for GM",
            "updates": {{
                 "minutes_passed": 15,
                 "location_impact": "none",
                 "scene_impact": "none",
                 "health_impact": "none",
                 "energy_impact": "spend_small",
                 "gold_impact": "none",
                 "inventory_new": [],
                 "inventory_lost": [],
                 "clocks_impact": {{}}
            }}
        }}"""

    try:
        def _sync_gen_mech():
            return model_worker.generate_content(prompt + "\n\nВАЖЛИВО: Відповідай ТІЛЬКИ JSON.")
        resp = await asyncio.to_thread(_sync_gen_mech)
        data = clean_and_parse_json(resp.text)
    except Exception as e:
        print(f"⚠️ AI Worker Error: {e}")
        data = None

    if not data:
        # Безпечний фолбек у разі падіння API ШІ
        return "MECHANICAL VERDICT: AUTO_SUCCESS", {"minutes_passed": 5, "skill_used": "Немає", "outcome": "SUCCESS", "difficulty": 0}

    skill_used = data.get("skill_used", "None")
    circumstance = data.get("circumstance", "NORMAL")
    updates = data.get("updates", {})
    updates["action_type"] = data.get("action_type", "standard")
    difficulty = safe_int(data.get("difficulty", 100), 100)

    # A09/A10 FIX: Post-hoc DC floor для фізично небезпечних дій
    danger_keywords = ["стриб", "паді", "зістриб", "зіскоч", "стін", "вікн", "дах", "обрив", "прірв", "вогонь", "полум"]
    action_lower = user_input.lower()
    if any(kw in action_lower for kw in danger_keywords) and difficulty < 140:
        difficulty = 140

    # A09: DC ескалація — якщо Scene_Tension >= 3, мінімум DC=120
    scene_tension_raw = clocks_info.get("Scene_Tension", "0/4")
    scene_tension_val = int(str(scene_tension_raw).split("/")[0]) if isinstance(scene_tension_raw, str) else int(scene_tension_raw or 0)
    if scene_tension_val >= 3 and skill_used not in ["None", "Немає", "none", ""] and difficulty < 120:
        difficulty = 120

    # POST-HOC: Scene_Tension clear дозволений ТІЛЬКИ при зміні сцени/локації
    clocks_fix = updates.get("clocks_impact", {})
    scene_changed = updates.get("scene_impact", "none") not in ["none", "None", "", None]
    location_changed = updates.get("location_impact", "none") not in ["none", "None", "", None]
    if isinstance(clocks_fix, dict) and clocks_fix.get("Scene_Tension") == "clear":
        if not scene_changed and not location_changed:
            # Гравець не змінив сцену — заборонити clear, замінити на +1
            clocks_fix["Scene_Tension"] = 1
            updates["clocks_impact"] = clocks_fix
        # else: apply_system_impacts виконає поетапне зниження (не бінарний скид)

    # A07c FIX: Втеча при ST>=3 + зміна локації → форсуємо "clear" для поетапного зниження
    escape_keywords = ["тікаю", "біжу", "втікаю", "тікати", "рятуюсь", "ховаюсь", "тікаємо"]
    if (any(kw in action_lower for kw in escape_keywords)
            and scene_tension_val >= 3
            and (scene_changed or location_changed)
            and isinstance(clocks_fix, dict)
            and clocks_fix.get("Scene_Tension") != "clear"):
        clocks_fix["Scene_Tension"] = "clear"
        updates["clocks_impact"] = clocks_fix

    # A11 FIX: Мінімальний урон для падіння/стрибка з висоти
    fall_keywords = ["стриб", "паді", "зістриб", "зіскоч", "впа"]
    if any(kw in action_lower for kw in fall_keywords):
        current_health_impact = updates.get("health_impact", "none")
        if current_health_impact in ["none", "dmg_light"]:
            updates["health_impact"] = "dmg_medium"

    # A07 FIX: Якщо ST=3 і дія агресивна/провокативна — Worker часто не піднімає до 4. Форсуємо.
    aggression_keywords = [
        "вдар", "атак", "напа", "кричу", "крику", "звинува", "погрож", "вбив", "штовх", "кида",
        "б'ю", "б'є", "бити", "вбию", "вбит", "кидаю", "кидає", "ламаю", "ріжу", "рубаю",
        "стріляю", "душу", "хапаю", "погрожую", "ображаю", "ганьблю", "вимагаю", "звинувачую",
    ]
    if scene_tension_val >= 3 and isinstance(clocks_fix, dict):
        st_delta = clocks_fix.get("Scene_Tension", 0)
        if any(kw in action_lower for kw in aggression_keywords) and (st_delta == 0 or st_delta == "clear"):
            clocks_fix["Scene_Tension"] = 1
            updates["clocks_impact"] = clocks_fix

    # -------------------------------------------------
    # МАТЕМАТИКА 2d50 ТА КРИТІВ ВІДБУВАЄТЬСЯ ТУТ
    # -------------------------------------------------

    if current_energy <= 20:
        circumstance = "DISADVANTAGE"
        data["verdict_text"] = "[СИСТЕМНА ВТОМА: Гравець ледве тримається на ногах]. " + data.get('verdict_text', '')

    if skill_used in ["None", "Немає", "none", ""] or skill_used not in skills:
        updates["skill_used"] = "Немає"
        updates["outcome"] = "SUCCESS"
        updates["dice_roll"] = "-"
        updates["skill_val"] = 0
        updates["total_score"] = 0
        updates["difficulty"] = 0
        return "MECHANICAL VERDICT: AUTO_SUCCESS! (No skill required).", updates

    skill_val = skills[skill_used]

    # --- ДЕТЕРМІНОВАНІ REPUTATION МОДИФІКАТОРИ (для соціальних навичок) ---
    rep_dc_mod = 0
    rep_blocked = False
    rep_target_npc = None
    if skill_used in ["Інтрига", "Управління"] and npc_reputation_context:
        # Шукаємо цільового NPC у тексті дії
        best_rep_score = None
        for npc_name, rep_score in npc_reputation_context.items():
            if npc_name.lower() in user_input.lower():
                best_rep_score = rep_score
                rep_target_npc = npc_name
                break
        # Якщо не знайшли точний збіг, але є лише один NPC — використовуємо його
        if best_rep_score is None and len(npc_reputation_context) == 1:
            rep_target_npc, best_rep_score = next(iter(npc_reputation_context.items()))

        if best_rep_score is not None:
            rep_circ, rep_dc_mod, rep_blocked = get_npc_reputation_modifier(best_rep_score)
            if rep_blocked:
                updates["skill_used"] = skill_used
                updates["outcome"] = "BLOCKED"
                updates["reputation_block"] = True
                updates["reputation_target_npc"] = rep_target_npc
                return f"MECHANICAL VERDICT: BLOCKED! NPC '{rep_target_npc}' (reputation {best_rep_score}) — соціальна дія заблокована кровною ворожнечею. GM INFO: Describe the NPC's absolute refusal, hatred, or hostility. The player CANNOT persuade this NPC through social means — only force, fear, or third-party intervention.", updates
            # Застосовуємо circumstance override від репутації (якщо ще не DISADVANTAGE від енергії)
            if rep_circ == "ADVANTAGE" and circumstance != "DISADVANTAGE":
                circumstance = "ADVANTAGE"
            elif rep_circ == "DISADVANTAGE":
                circumstance = "DISADVANTAGE"

    # Застосовуємо DC-модифікатор від репутації
    difficulty = max(40, difficulty + rep_dc_mod)

    roll_1 = roll_2d50()
    roll_2 = roll_2d50()

    if circumstance == "ADVANTAGE":
        natural_roll = max(roll_1, roll_2)
        roll_str = f"[{roll_1}, {roll_2}] -> {natural_roll}"
    elif circumstance == "DISADVANTAGE":
        natural_roll = min(roll_1, roll_2)
        roll_str = f"[{roll_1}, {roll_2}] -> {natural_roll}"
    else:
        natural_roll = roll_1
        roll_str = f"{natural_roll}"

    total_score = natural_roll + skill_val

    if natural_roll >= 96:
        outcome = "CRITICAL SUCCESS"
    elif natural_roll <= 5:
        outcome = "CRITICAL FAILURE"
    elif total_score >= difficulty:
        outcome = "SUCCESS"
    else:
        outcome = "FAILURE"

    rep_info = f" [REP: {rep_dc_mod:+d} DC]" if rep_dc_mod else ""
    verdict_str = f"MECHANICAL VERDICT: {outcome}! (Skill: {skill_used} [{skill_val}], Roll: {natural_roll}, Total: {total_score} vs DC {difficulty}{rep_info}). GM INFO: {data.get('verdict_text')}"

    updates["skill_used"] = skill_used
    updates["dice_roll"] = roll_str
    updates["skill_val"] = skill_val
    updates["total_score"] = total_score
    updates["outcome"] = outcome
    updates["circumstance"] = circumstance
    updates["difficulty"] = difficulty

    # Reputation delta для соціальних навичок
    if skill_used in ["Інтрига", "Управління"] and rep_target_npc:
        updates["reputation_delta"] = get_reputation_delta(outcome)
        updates["reputation_target_npc"] = rep_target_npc

    # --- ХАРДКОРНА СИСТЕМА ТРАВМ ТА ПРОКАЧКИ ---
    if outcome == "CRITICAL SUCCESS":
        if "skill_impact" not in updates:
            updates["skill_impact"] = {}
        updates["skill_impact"][skill_used] = 1

    elif outcome == "CRITICAL FAILURE":
        temp_penalty = random.randint(15, 20)
        if "temp_debuff" not in updates:
            updates["temp_debuff"] = {}
        updates["temp_debuff"][skill_used] = temp_penalty

        if random.random() < 0.50:
            if "skill_impact" not in updates:
                updates["skill_impact"] = {}
            updates["skill_impact"][skill_used] = -1
            updates["permanent_scar"] = True

    # A07b FIX: Соціальний провал при ST>=2 → ескалація напруги
    social_skills = ["Інтрига", "Управління"]
    if (scene_tension_val >= 2
            and skill_used in social_skills
            and updates.get("outcome") in ["CRITICAL FAILURE", "FAILURE"]):
        clocks_fix_b = updates.get("clocks_impact", {})
        if isinstance(clocks_fix_b, dict) and clocks_fix_b.get("Scene_Tension") in [0, None]:
            clocks_fix_b["Scene_Tension"] = 1
            updates["clocks_impact"] = clocks_fix_b

    return verdict_str, updates