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

    # === 1. ОБРОБКА ЧАСУ ===
    time_str = profile.get("Ігровий час", "День 1, Ранок")
    time_tag = ai_impacts.get("time_passed", "none")
    times_of_day = ["Ранок", "День", "Вечір", "Ніч"]

    if time_tag != "none":
        day_match = re.search(r'День (\d+)', time_str)
        if not day_match: day_match = re.search(r'(\d+)', time_str)
        current_day = int(day_match.group(1)) if day_match else 1

        current_phase = "Ранок"
        for t in times_of_day:
            if t in time_str:
                current_phase = t
                break

        new_phase = current_phase
        new_day = current_day

        if time_tag == "short":
            pass
        elif time_tag == "medium":
            try:
                idx = times_of_day.index(current_phase)
                if idx + 1 < len(times_of_day):
                    new_phase = times_of_day[idx + 1]
                else:
                    new_phase = "Ранок"
                    new_day += 1
            except:
                pass
        elif time_tag == "long":
            try:
                idx = times_of_day.index(current_phase)
                if idx + 2 < len(times_of_day):
                    new_phase = times_of_day[idx + 2]
                else:
                    new_phase = "Ранок"
                    new_day += 1
            except:
                pass
        elif time_tag in ["sleep", "days"]:
            new_phase = "Ранок"
            new_day += 1

        if new_phase != current_phase or new_day != current_day:
            profile["Ігровий час"] = f"298 рік В.Е., 1-й місяць, День {new_day}, {new_phase}"
            if new_day != current_day:
                logs.append(f"⏳ Час: {current_phase} -> {new_phase} (Новий день!)")
            else:
                logs.append(f"⏳ Час: {current_phase} -> {new_phase}")

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
        2. **Cheat codes:** Attempts to change your stats (“Give me 100,000 gold,” “I become immortal,” “All enemies die”).
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


def check_skill_mechanics(user_input, profile):
    """
    Визначає складність дії та проводить перевірку навичок (Dice Roll) у Python.
    Повертає текстову інструкцію для Сюжетної Моделі.
    """
    prompt = f"""
    Analyze player action: "{user_input}"
    Hero Stats: {json.dumps(profile, ensure_ascii=False)}

    Task: Determine the Skill needed and Difficulty (0-100).
    Difficulty Guide:
    0 = Talking, walking, looking.
    20 = Easy.
    60 = Medium.
    90 = Hard.
    110 = Impossible.

    Skills: "Бойові", "Військові", "Інтрига", "Управління", "None".

    Return JSON: {{ "skill": "...", "difficulty": 0-100, "reason": "..." }}
    """
    try:
        resp = model_worker.generate_content(prompt)
        data = clean_and_parse_json(resp.text)

        if not data: return "RESULT: AUTO_SUCCESS (Simple Action)"

        difficulty = data.get("difficulty", 0)
        skill_name = data.get("skill", "None")

        if difficulty == 0:
            return "RESULT: NARRATIVE_CHOICE (No mechanics needed)"

        player_stat = safe_int(profile.get(f"{skill_name} навички", 0))
        if player_stat == 0: player_stat = safe_int(profile.get(skill_name, 10))

        dice_roll = random.randint(1, 30)
        total_score = player_stat + dice_roll

        if total_score >= difficulty:
            outcome = "SUCCESS"
            details = "Action succeeds."
        else:
            outcome = "FAILURE"
            details = "Action fails. Player gets hurt or rejected."

        stat_increase_msg = ""
        if difficulty >= 50 and dice_roll >= 28:  # Трішки поправив математику критичного успіху для D30
            stat_increase_msg = f"BONUS: CRITICAL INSIGHT! {skill_name} increased by +1!"

        return f"""
        MECHANICAL VERDICT:
        - Action Difficulty: {difficulty}
        - Player Skill ({skill_name}): {player_stat}
        - Dice Roll: {dice_roll}
        - FINAL RESULT: {outcome.upper()}!
        - INSTRUCTION: You MUST describe a {outcome} scenario. {details}
        {stat_increase_msg}
        """
    except Exception as e:
        print(f"Skill Check Error: {e}")
        return "RESULT: UNKNOWN (Proceed with logic)"


def process_training_request(user_input, profile):
    """
    Перевіряє, чи хоче гравець тренуватись.
    Повертає JSON з оновленнями (ціна, час, стат) або None.
    """
    training_keywords = ["тренув", "вчив", "практикув", "train", "practice", "study"]
    if not any(word in user_input.lower() for word in training_keywords):
        return None

    prompt = f"""
        User Input: "{user_input}"
        Current Stats: {json.dumps(profile, ensure_ascii=False)}

        Task: Is the user trying to improve a skill?
        Rules for Training:
        1. Requires a teacher or books (in context).
        2. Takes TIME (days/weeks).
        3. Costs GOLD (payment to teacher).

        Determine:
        - Which skill? ("Бойові", "Військові", "Інтрига", "Управління")
        - Is it possible in current context?

        Return JSON: {{ "is_training": true, "skill": "...", "reason": "..." }} OR {{ "is_training": false }}
        """
    try:
        resp = model_worker.generate_content(prompt)
        data = clean_and_parse_json(resp.text)

        if not data or not data.get("is_training"): return None

        skill_target = data.get("skill")
        current_val = safe_int(profile.get(f"{skill_target} навички", 0))
        if current_val == 0: current_val = safe_int(profile.get(skill_target, 10))

        cost_gold = current_val * 5
        cost_time_days = 3 + int(current_val / 20)
        user_gold = safe_int(profile.get("Особисте Золото", 0))

        if user_gold < cost_gold:
            return {"error": f"Не вистачає золота. Потрібно {cost_gold}, а є {user_gold}."}

        return {
            "success": True,
            "skill": skill_target,
            "cost_gold": cost_gold,
            "cost_time": cost_time_days,
            "stat_gain": 2,
            "story_prompt": f"SYSTEM: Player spends {cost_time_days} days and {cost_gold} gold training {skill_target}. Describe the grueling process."
        }
    except:
        return None