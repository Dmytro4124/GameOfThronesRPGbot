# core/mechanics.py
import random
import json
import re

# Імпортуємо нашого ШІ-клієнта для функцій, які потребують суддівства
from core.ai_client import model_worker, clean_and_parse_json


def safe_int(value):
    """Допоміжна функція: перетворює будь-що на ціле число або повертає 0"""
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return 0


def apply_system_impacts(profile, ai_impacts):
    """
    Приймає абстрактні теги від АІ і перетворює їх на конкретну математику.
    Повертає оновлений профіль і лог змін.
    """
    logs = []

    # === 1. ОБРОБКА ЧАСУ ТА КАЛЕНДАРЯ ===
    # Константи календаря
    DAYS_IN_MONTH = 30
    MONTHS_IN_YEAR = 12

    time_str = profile.get("Ігровий час", "298 рік В.Е., 1-й місяць, День 1, 08:00")
    minutes_passed = ai_impacts.get("minutes_passed", 0)
    energy_impact = ai_impacts.get("energy_impact", "none")

    if minutes_passed > 0 or energy_impact == "sleep":
        # Витягуємо поточний рік, місяць та день за допомогою регулярного виразу
        # Очікуваний формат: "298 рік В.Е., 1-й місяць, День 1..."
        date_match = re.search(r'(\d+)\s*рік.*?(\d+)-й\s*місяць.*?День\s*(\d+)', time_str)

        if date_match:
            current_year = int(date_match.group(1))
            current_month = int(date_match.group(2))
            current_day = int(date_match.group(3))
        else:
            # Fallback, якщо рядок зламаний
            current_year, current_month, current_day = 298, 1, 1

        # Витягуємо поточні хвилини
        if "Час_хвилини" in profile:
            current_minutes = profile["Час_хвилини"]
        else:
            time_match = re.search(r'(\d{2}):(\d{2})', time_str)
            if time_match:
                current_minutes = int(time_match.group(1)) * 60 + int(time_match.group(2))
            else:
                current_minutes = 8 * 60  # За замовчуванням 08:00

        old_time_str = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"

        # Логіка сну: мотаємо час до 08:00 наступного ранку
        if energy_impact == "sleep":
            # (Хвилин до кінця поточної доби) + (8 годин наступної доби)
            sleep_minutes = (1440 - current_minutes) + (8 * 60)
            minutes_passed = max(minutes_passed, sleep_minutes)  # Беремо більше значення


        # Зменшення кулдаунів тренування
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

                # Видаляємо кулдауни, які завершилися
                for skill_name in keys_to_remove:
                    del cooldowns[skill_name]

                profile["training_cooldowns"] = cooldowns
        # ===================================================

        # --- МАТЕМАТИКА ЧАСУ ---
        new_total_minutes = current_minutes + minutes_passed

        # 1. Рахуємо нові години та хвилини
        days_passed = new_total_minutes // 1440
        new_minutes_of_day = new_total_minutes % 1440

        exact_time_str = f"{new_minutes_of_day // 60:02d}:{new_minutes_of_day % 60:02d}"

        # 2. Рахуємо нові дні, місяці та роки
        # Переводимо в 0-індексовану систему для зручності ділення (День 1 = 0, Місяць 1 = 0)
        total_days = (current_day - 1) + days_passed

        new_day = (total_days % DAYS_IN_MONTH) + 1
        months_passed = total_days // DAYS_IN_MONTH

        total_months = (current_month - 1) + months_passed

        new_month = (total_months % MONTHS_IN_YEAR) + 1
        years_passed = total_months // MONTHS_IN_YEAR

        new_year = current_year + years_passed

        # --- ЗБЕРЕЖЕННЯ ---
        profile["Час_хвилини"] = new_minutes_of_day
        profile["Ігровий час"] = f"{new_year} рік В.Е., {new_month}-й місяць, День {new_day}, {exact_time_str}"

        # --- ЛОГУВАННЯ ---
        if years_passed > 0:
            logs.append(f"⏳ З Новим Роком! Тепер {new_year} рік В.Е.")
        elif months_passed > 0:
            logs.append(f"⏳ Почався новий місяць: {new_month}-й.")
        elif days_passed > 0:
            logs.append(f"⏳ Час: {old_time_str} -> {exact_time_str} (Новий день!)")
        else:
            logs.append(f"⏳ Час: {old_time_str} -> {exact_time_str} (+{minutes_passed} хв)")

    # === 1.1. ОБРОБКА ЕНЕРГІЇ ===
    if energy_impact != "none" or energy_impact == "sleep":
        current_energy = safe_int(profile.get("Енергія", 100))

        # Задаємо діапазони: (мінімум, максимум)
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

        # Обробка повного відновлення (сон або повний відпочинок)
        if energy_impact in ["sleep", "restore_full"]:
            delta = 100 - current_energy
        # Обробка часткового відновлення
        elif energy_impact in energy_ranges_positive:
            min_cost, max_cost = energy_ranges_positive[energy_impact]
            delta = random.randint(min_cost, max_cost)
        # Обробка витрати енергії
        elif energy_impact in energy_ranges_negative:
            min_cost, max_cost = energy_ranges_negative[energy_impact]
            delta = -random.randint(min_cost, max_cost)

        new_energy = max(0, min(100, current_energy + delta))

        if new_energy != current_energy:
            profile["Енергія"] = new_energy
            # Форматуємо знак для красивого логу (+15 або -5)
            sign = "+" if delta > 0 else ""
            logs.append(f"⚡ Енергія: {current_energy} -> {new_energy} ({sign}{delta})")

    # === 2. ОБРОБКА ЗДОРОВ'Я ===
    damage_tag = ai_impacts.get("health_impact", "none")
    hp_change = 0

    if damage_tag == "heal_small":
        hp_change = random.randint(5, 25)
    elif damage_tag == "heal_full":
        hp_change = 100
    elif damage_tag == "dmg_light":
        hp_change = -random.randint(1, 5)
    elif damage_tag == "dmg_medium":
        hp_change = -random.randint(5, 25)
    elif damage_tag == "dmg_heavy":
        hp_change = -random.randint(25, 50)
    elif damage_tag == "dmg_fatal":
        hp_change = -100

    if hp_change != 0:
        old_hp = safe_int(profile.get("Здоров'я", 100))
        new_hp = max(0, min(100, old_hp + hp_change))
        profile["Здоров'я"] = new_hp
        logs.append(f"❤️ Здоров'я: {old_hp} -> {new_hp}")

    # === 3. ОБРОБКА ЗОЛОТА ===
    gold_tag = ai_impacts.get("gold_impact", "none")
    gold_change = 0

    if gold_tag == "spend_small":
        gold_change = -random.randint(5, 15)
    elif gold_tag == "spend_medium":
        gold_change = -random.randint(50, 150)
    elif gold_tag == "spend_large":
        gold_change = -random.randint(300, 800)
    elif gold_tag == "earn_small":
        gold_change = random.randint(10, 30)
    elif gold_tag == "earn_medium":
        gold_change = random.randint(100, 300)
    elif gold_tag == "earn_large":
        gold_change = random.randint(1000, 2000)

    if gold_change != 0:
        old_gold = safe_int(profile.get("Особисте Золото", 0))
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
            inventory_list.append(item)
            logs.append(f"➕ Отримано: {item}")

    lost_items = ai_impacts.get("inventory_lost")
    if lost_items:
        if isinstance(lost_items, str): lost_items = [lost_items]
        for item in lost_items:
            for existing_item in inventory_list:
                if item.lower() in existing_item.lower():
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
                if skill_name in key:
                    target_key = key
                    break

            if target_key:
                old_val = safe_int(profile[target_key])
                new_val = min(100, old_val + val)
                profile[target_key] = new_val
                logs.append(f"📈 {skill_name}: +{val} (Стало {new_val})")

    # === 6. ОБРОБКА ГОДИННИКІВ (CLOCKS) ===
    clocks_updates = ai_impacts.get("clocks_impact", {})
    if clocks_updates:
        current_clocks = profile.get("Годинники", {})
        if isinstance(current_clocks, str):
            current_clocks = {}

        for clock_name, change in clocks_updates.items():
            if change == "clear":
                if clock_name in current_clocks:
                    del current_clocks[clock_name]
                    logs.append(f"⏱️ Годинник '{clock_name}' скинуто.")
            else:
                current_val, max_val = 0, 4  # Годинник на 4 кроки за замовчуванням
                if clock_name in current_clocks:
                    parts = current_clocks[clock_name].split('/')
                    current_val, max_val = int(parts[0]), int(parts[1])

                new_val = min(max_val, current_val + safe_int(change))
                current_clocks[clock_name] = f"{new_val}/{max_val}"
                logs.append(f"⏱️ Годинник '{clock_name}': {new_val}/{max_val}")

        profile["Годинники"] = current_clocks

    return profile, logs


def validate_action(user_input, profile):
    """
    Окрема функція-фільтр (Цензор).
    Перевіряє, чи не намагається гравець зламати гру чітами, анахронізмами чи діями за когось іншого.
    """
    char_name = profile.get("Ім'я", "Герой")

    prompt = f"""
        You are the Lore Keeper for the game “Game of Thrones” (Medieval Fantasy).

        Your task: Check the player's action for “legality.”

        PLAYER: {char_name}
        ACTION: “{user_input}”

        === THE GOLDEN RULE: INTENT ONLY ===
        The player is allowed to describe ONLY what they TRY to do, player controls ONLY their own body and voice.
        The player is FORBIDDEN from describing the RESULT of their action.
        The result is determined by the Game Master, not the player.

        === EXAMPLES OF VIOLATIONS (RETURN is_valid: false) ===
        1. **Deciding the hit:**
           - ❌ "I cut off his head." (Player decided the hit landed and killed).
           - ❌ "I shoot him in the eye." (Too specific result).
           - ✅ CORRECT: "I swing my sword at his neck." / "I aim for his eye."

        2. **Deciding the social outcome:**
           - ❌ "I convince him to give me the keys." (Player decided success).
           - ❌ "I scare him and he runs away." (Player controlled the NPC's reaction).
           - ✅ CORRECT: "I demand the keys persuasively." / "I scream to scare him."

        3. **Deciding the world state:**
           - ❌ "I find a secret door." (Player decided there is a door).
           - ✅ CORRECT: "I search the room for secrets."

        4. It is FORBIDDEN to write actions for NPCs (other characters).
            - ❌ NOT ALLOWED: “Jorah Mormont draws his sword and kills the Night King.” (The player controls Jorah).
            - ✅ ALLOWED: “I shout to Jorah, ‘Kill him!’” (The player controls their voice).
            - ✅ ACCEPTABLE: “I order Jorah to attack.” (The player gives the order, and the GM decides whether the NPC will carry it out or not).

        5. It is FORBIDDEN to describe the CONSEQUENCES of your actions as fact.
            - ❌ NOT ALLOWED: “I hit the guard, and his head flies off his shoulders.” (The player decided the outcome).
            - ✅ ALLOWED: “I hit the guard in the neck with my sword with all my strength.” (The player describes the attempt, the outcome is up to the GM).

        6. **NPC as Subject:** The sentence describes what an NPC does.
           - ❌ "Drogo laughs." (Player decided Drogo laughs).
           - ❌ "The guard lets me pass." (Player decided the guard's action).
           - ❌ "Everyone cheers for me."

        7. **Forced Compliance:** The player writes the result of their command.
           - ❌ "I tell him to leave and he does." ("...and he does" is the violation).
           - ✅ CORRECT: "I tell him to leave." (Stop there).


        === PROHIBITION CRITERIA (RETURN is_valid: false) ===
        1. **Anachronisms:** Mention of modern technologies (F-16, telephone, automatic weapon, internet, NATO, Biden).
        2. **ANTI-GOD-MODING (REALITY CHECK):** If the player attempts to invent reality (e.g., "I find a chest of infinite gold", "I suddenly have a Valyrian sword", "I instantly kill the boss"), you MUST automatically classify the OUTCOME as "FAILURE". 
            - Set "gold_impact" to "none".
            - Set "inventory_new" to [].
            - Add to "clocks_impact": {{"Scene_Tension": 1}}.
            - In "verdict_text", explain that the universe rejects their delusion (e.g., "Player tried to hallucinate gold, but found only dirt.").
        3. **Meta-gaming:** Attempts to control the plot as an author (“I want a dragon to fly in and save everyone,” “Skip to the end of the game”).
        4. **Absurdity:** Actions that are physically impossible for a human (unless it is magic available in the lore, e.g., wargs).

        === PERMISSION CRITERIA (RETURN is_valid: true) ===
        1. Allow risky actions (“I attack the king” is stupid, but legal. The head GM will kill him).
        2. Allow jokes and conversations.
        3. Allow any actions that are possible in the physical world of Westeros.

        === YOUR VERDICT ===
        If the player describes the RESULT, reject the action.

        === FORBID TO PLAY AFTER DEATH ===
        If {profile.get("Здоров'я", 100) < 0} Game is ended and player can not continue.

        RESPONSE (JSON):
        {{
            “is_valid”: true or false,
            “refusal_reason”: “A short ironic comment in Ukrainian explaining why it is impossible (only if false). For example: ‘The gods don't know what an F-16 is.’”
        }}
        """
    try:
        response = model_worker.generate_content(prompt)
        result = clean_and_parse_json(response.text)

        if not result:
            return True, ""

        return result.get("is_valid", True), result.get("refusal_reason", "Це неможливо.")
    except Exception as e:
        print(f"⚠️ Помилка валідатора: {e}")
        return True, ""


def process_training_request(user_input, profile):
    """
    Worker (Тренування): Хардкорна система (+1 бал), Гібрид Енергії та Кулдаунів (Асиміляція).
    """
    import json
    import random
    from core.ai_client import model_worker, clean_and_parse_json

    def safe_int(val, default=10):
        try:
            return int(val)
        except:
            return default

    prompt = f"""
    YOU ARE THE GAME MASTER. The player might be trying to train a skill.

    User Input: "{user_input}"
    Current Stats: {json.dumps(profile, ensure_ascii=False)}

    RULES FOR TRAINING:
    1. CONTEXT CHECK: Is it a safe time to pass days? (Active combat, stealth, or dialogue = impossible).
    2. METHOD CHECK: Solo ("I train") vs Mentor ("I pay a master / read a book").

    OUTPUT STRICTLY JSON:
    {{
        "is_training": true or false,
        "is_possible": true or false,
        "skill": "Бойові" or "Військові" or "Інтрига" or "Управління",
        "method": "solo" or "mentor",
        "reason_if_failed": "Why it's impossible."
    }}
    """

    try:
        resp = model_worker.generate_content(prompt)
        data = clean_and_parse_json(resp.text)

        if not data or not data.get("is_training"): return None
        if not data.get("is_possible"):
            return {
                "error": f"Зараз неможливо розпочати тренування: {data.get('reason_if_failed', 'Небезпечна ситуація.')}"}

        skill_target = data.get("skill")
        method = data.get("method", "solo")

        # === ПЕРЕВІРКА КУЛДАУНУ (АСИМІЛЯЦІЇ) ===
        cooldowns = profile.get("training_cooldowns", {})
        current_cooldown_mins = safe_int(cooldowns.get(skill_target, 0), 0)

        if current_cooldown_mins > 0:
            days_left = max(1, current_cooldown_mins // 1440)
            return {
                "error": f"Ваше тіло та розум ще засвоюють попередні уроки з навички '{skill_target}'. Нервовій системі потрібен час на відновлення. Зачекайте ще приблизно {days_left} ігрових днів, перш ніж тренувати це знову."}

        current_val = safe_int(profile.get(f"{skill_target} навички", profile.get(skill_target, 10)))
        user_gold = safe_int(profile.get("Особисте Золото", 0))
        current_energy = safe_int(profile.get("Енергія", 100))

        # Математика Часу та Золота
        if method == "solo":
            if current_val >= 50:
                return {
                    "error": f"Навичка '{skill_target}' вже {current_val}. Межа самостійного навчання досягнута. Шукайте майстра."}
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
        new_cooldown_mins = (cost_time_days + 7) * 1440

        if current_energy >= energy_cost:
            stat_gain = 1
            story_prompt = f"SYSTEM: Player spends {cost_time_days} days and {cost_gold} gold practicing '{skill_target}' ({method}). They gain +1 to the skill but lose 40 Energy. Describe the grueling process."
        else:
            if random.random() < 0.75:  # 75% травма
                temp_debuff = random.randint(15, 20)
                new_cooldown_mins = 14 * 1440  # 14 днів кулдауну через травму
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
            "set_cooldown": new_cooldown_mins,  # Передаємо кулдаун у хвилинах
            "story_prompt": story_prompt
        }

    except Exception as e:
        print(f"⚠️ Training Error: {e}")
        return None


def resolve_action_mechanics(user_input, profile):
    """
    Worker (4b): Оцінює складність дії та рахує всю механіку (Здоров'я, Час, Золото, Енергія, Годинники).
    ІНТЕГРОВАНО: Система 2d50 (Roll-Over), Перевага/Недолік, Хардкорні Крити та Прокачка.
    Не пише художній текст!
    """
    import random
    from core.ai_client import model_worker, clean_and_parse_json
    import json

    def safe_int(val, default=10):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def roll_2d50():
        return random.randint(1, 50) + random.randint(1, 50)

    skills = {
        "Бойові": safe_int(profile.get("Бойові навички", 10)),
        "Військові": safe_int(profile.get("Військові навички", 10)),
        "Інтрига": safe_int(profile.get("Інтрига", 10)),
        "Управління": safe_int(profile.get("Управління", 10))
    }

    clocks_info = profile.get("Годинники", {})

    prompt = f"""
    YOU ARE THE SYSTEM ENGINE FOR A GRIMDARK RPG. 
    Your ONLY job is to calculate the mechanical outcome of the player's action using a ROLL-OVER system (Roll + Skill vs DC 100).

    === DATA ===
    Player Action: "{user_input}"
    Player Skills: {json.dumps(skills, ensure_ascii=False)}
    Active Clocks (Tension): {json.dumps(clocks_info, ensure_ascii=False)}

    === ACTIVE CLOCKS RULES ===
        If a clock reaches its maximum, e.g., 4/4, the threat happens immediately! Do not spawn threats if clock is 0 or 1.

    === RULES & MATH (CRITICAL) ===
    1. CLASSIFY REQUIRED SKILL (STRICT RULES):
       - "Інтрига": Sneaking, hiding, stealing, lying, bluffing, persuasion, gathering rumors.
       - "Військові": Tactics, commanding, assessing the battlefield, spotting ambushes.
       - "Бойові": Direct physical combat, sword fighting, wrestling, attacking.
       - "Управління": Using noble status, bribery, trade, giving official orders.
       - "Немає": Basic dialogue, walking peacefully, looking around.

    2. DETERMINE CIRCUMSTANCE:
       - "ADVANTAGE": Player has the upper hand, surprise, high ground, or great leverage.
       - "NORMAL": Fair conditions, standard risk.
       - "DISADVANTAGE": Player is injured, outnumbered, acting under extreme pressure, or doing something very difficult.

    3. INTENT CLASSIFICATION: 
       Is the player attempting to spend a long period of time to practice, study, or hone a specific skill? 
       If YES -> set "action_type": "training".
       If NO (it's a standard immediate action like combat, dialogue, or exploration) -> set "action_type": "standard".
    
    === TAGS GUIDE (STRICT VALUES REQUIRED) ===
        1. **minutes_passed** (Integer): Estimate the realistic duration of the player's current action in in-game minutes.
            - 1-2 (combat turn, quick action), 
            - 15-30 (conversation, lockpicking), 
            - 60-120 (travel, exploring), 
            - 480-600 (sleeping, waiting until morning).
            - STRICT OVERRIDE: If the action involves ANY physical combat, dodging, running, or quick physical struggle, it MUST be 1 or 2.

        2. **health_impact** (Health consequences):
           - "none" (no change)
           - "heal_small" / "heal_full"
           --- ONLY if the enemy successfully hit the player in the text, OR if there is a CRITICAL FAILURE in combat ---
           - "dmg_light" (bruise, scratch: -5 HP)
           - "dmg_medium" (sword wound, burn: -15 HP)
           - "dmg_heavy" (critical injury: -30 HP)
           - "dmg_fatal" (death: -100 HP)

        3. **gold_impact** (Economy):
           - "none"
           - "spend_small" (food, small items)
           - "spend_medium" (weapons, clothing)
           - "spend_large" (horses, houses)
           - "earn_small" (found a coin)
           - "earn_medium" (quest reward)
           - "earn_large" (grand treasure)

        4. **inventory** - "inventory_new": ["Item Name"] (If obtained).
           - "inventory_lost": ["Item Name"] (If lost/eaten).

        5. **clocks_impact** (Tension & Event Management):
            - **Local Tension (Scenes/Dialogues):** IT IS STRICTLY FORBIDDEN to invent new names for tension clocks. Use ONLY the fixed key {{"Scene_Tension": 1}} to increase tension (max 3) if the player acts suspiciously, aggressively, or fails a skill check.
            - **Reset:** Use {{"Scene_Tension": "clear"}} to reset the tension when the conflict is resolved.

        6. **"energy_impact"** (String): 
            Evaluate the stamina cost of the action. Must be ONE of the following: 
            - "none" (talking), 
            - "spend_small" (minor stress, short walk), 
            - "spend_medium" (argument, training, long walk), 
            - "spend_large" (combat, heavy labor), 
            - "restore_full" (sleep),
            - "restore_small" (relaxing by the campfire, hearty food or lying in the bed),
            - "restore_medium" (rest in tavern or brothel, having bath).
    
    OUTPUT EXACTLY IN THIS JSON FORMAT:
    {{
        "action_type": "standard",
        "skill_used": "Name of skill or None",
        "circumstance": "ADVANTAGE" or "NORMAL" or "DISADVANTAGE",
        "verdict_text": "Short instruction for GM (e.g. 'Player successfully dodged' or 'Player failed and took damage. Scene tension +1.')",
        "updates": {{
             "minutes_passed": 15,
             "health_impact": "none",
             "energy_impact": "spend_small",
             "gold_impact": "none",
             "inventory_new": [new inventory],
             "inventory_lost": [lost inventory],
             "clocks_impact": {{"Scene_Tension": "..."}}
        }}
    }}
    """

    try:
        resp = model_worker.generate_content(prompt + "\n\nВАЖЛИВО: Відповідай ТІЛЬКИ JSON.")
        data = clean_and_parse_json(resp.text)
        if not data: return "MECHANICAL VERDICT: AUTO_SUCCESS", {}

        skill_used = data.get("skill_used", "None")
        circumstance = data.get("circumstance", "NORMAL")
        updates = data.get("updates", {})
        updates["action_type"] = data.get("action_type", "standard")

        # -------------------------------------------------
        # МАТЕМАТИКА 2d50 ТА КРИТІВ ВІДБУВАЄТЬСЯ ТУТ (В ПАЙТОНІ)
        # -------------------------------------------------

        # Якщо дія не потребує навички - автоуспіх
        if skill_used == "None" or skill_used not in skills:
            # Зберігаємо "костиль", щоб у логах не було порожнечі, якщо ШІ повернув None
            updates["skill_used"] = "None"
            updates["outcome"] = "SUCCESS"
            return "MECHANICAL VERDICT: AUTO_SUCCESS! (No skill required).", updates

        skill_val = skills[skill_used]

        # Механіка Переваги / Недоліку (Більше = Краще)
        roll_1 = roll_2d50()
        roll_2 = roll_2d50()

        if circumstance == "ADVANTAGE":
            natural_roll = max(roll_1, roll_2)  # Беремо БІЛЬШИЙ
            roll_str = f"[{roll_1}, {roll_2}] -> {natural_roll}"
        elif circumstance == "DISADVANTAGE":
            natural_roll = min(roll_1, roll_2)  # Беремо МЕНШИЙ
            roll_str = f"[{roll_1}, {roll_2}] -> {natural_roll}"
        else:
            natural_roll = roll_1
            roll_str = f"{natural_roll}"

        total_score = natural_roll + skill_val

        # Визначення результату (Більше = Краще)
        if natural_roll >= 96:
            outcome = "CRITICAL SUCCESS"
        elif natural_roll <= 5:
            outcome = "CRITICAL FAILURE"
        elif total_score >= 100:
            outcome = "SUCCESS"
        else:
            outcome = "FAILURE"

        verdict_str = f"MECHANICAL VERDICT: {outcome}! (Skill: {skill_used} [{skill_val}], Roll: {natural_roll}, Total: {total_score} vs 100). GM INFO: {data.get('verdict_text')}"

        # Прокидаємо дані кубика для відображення в інтерфейсі (в engine.py)
        updates["skill_used"] = skill_used
        updates["dice_roll"] = roll_str
        updates["skill_val"] = skill_val
        updates["total_score"] = total_score
        updates["outcome"] = outcome
        updates["circumstance"] = circumstance

        # --- ХАРДКОРНА СИСТЕМА ТРАВМ ТА ПРОКАЧКИ ---
        if outcome == "CRITICAL SUCCESS":
            if "skill_impact" not in updates:
                updates["skill_impact"] = {}
            updates["skill_impact"][skill_used] = 1  # +1 назавжди

        elif outcome == "CRITICAL FAILURE":
            # 1. Тимчасовий дебаф (штраф 15-20)
            temp_penalty = random.randint(15, 20)
            if "temp_debuff" not in updates:
                updates["temp_debuff"] = {}
            updates["temp_debuff"][skill_used] = temp_penalty

            # 2. Шрам: 50% шанс втратити -1 назавжди
            if random.random() < 0.50:
                if "skill_impact" not in updates:
                    updates["skill_impact"] = {}
                updates["skill_impact"][skill_used] = -1
                updates["permanent_scar"] = True
        # -------------------------------------------------

        return verdict_str, updates

    except Exception as e:
        print(f"⚠️ Worker Error: {e}")
        return "MECHANICAL VERDICT: AUTO_SUCCESS", {"minutes_passed": 5}