# core/engine.py
import time
import json
import re
import asyncio

# Імпорти з нашої нової структури
from core.ai_client import model, model_worker, clean_and_parse_json
from core.mechanics import apply_system_impacts, process_training_request, safe_int, \
    resolve_action_mechanics
from core.prompts import GAME_ERA_CONTEXT
from core.world import populate_contextual_npcs
from database.operations import (
    get_user_data, save_user_data, get_relevant_context,
    get_location_npcs, update_npcs_in_db
)

# Збереження стану гравців у пам'яті (раніше було в main.py)
user_sessions = {}


async def summarize_turn(gm_response):
    """
    Асинхронно стискає хід в одне речення для історії.
    Використовує меншу модель для швидкості.
    """
    clean_story = gm_response.split("📊")[0][:800]
    prompt = f"""
    Summarize this RPG turn into ONE short sentence (Ukrainian).
    Keep names and outcomes.
    GM: "{clean_story}"
    Output example: "Джон спробував вдарити вартового, але той ухилився."
    """
    try:
        def _sync_gen():
            return model_worker.generate_content(prompt)

        resp = await asyncio.to_thread(_sync_gen)
        return resp.text.strip()
    except:
        return f"Гравець діє."


async def process_game_turn(chat_id, user_input):
    """Основний ігровий цикл (рушій) - АСИНХРОННИЙ"""
    debug_log = ""
    global_start = time.time()
    debug_log += f"🚀 [START] Хід гравця {chat_id}..."

    user_id = chat_id
    timing_details = []

    # === ЕТАП 1: ЗАВАНТАЖЕННЯ ДАНИХ ===
    t_start = time.time()

    profile, row_id = await get_user_data(user_id)
    if not profile: return "❌ Профіль не знайдено."

    session = user_sessions.get(chat_id, {})
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"state": "GAME_ACTIVE", "history": []}
    history = session.get('history', [])

    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-40:]])
    old_location = profile.get("Поточне місцезнаходження", "")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    curr_loc = profile.get("Поточне місцезнаходження", "")

    # Асинхронний виклик (бо робить HTTP запит до Gemini)
    context_knowledge = await get_relevant_context(user_input, curr_loc)

    # Синхронний виклик (працює лише з кешем у пам'яті)
    npc_context_text, legal_npc_names = get_location_npcs(curr_loc)

    duration = time.time() - t_start
    debug_log += f"\n⏱️ [DATA LOAD] зайняло: {duration:.2f}s"
    timing_details.append(f"📚 Data: {duration:.2f}s")

    # === ЕТАП 2: МЕХАНІКА ТА ПЕРЕВІРКА НАВИЧОК ===
    t_start = time.time()
    logs = []

    # 1. ЗАВЖДИ спочатку викликаємо Розумного Суддю (він визначить намір)
    # resolve_action_mechanics має бути асинхронним, якщо він робить запити до AI
    mechanics_verdict, mechanical_updates = await resolve_action_mechanics(user_input, profile)

    # 2. СЕМАНТИЧНИЙ РЕДИРЕКТ: Чи виявив суддя спробу довгого тренування?
    if mechanical_updates.get("action_type") == "training":

        training_result = await process_training_request(user_input, profile)

        if training_result:
            if "error" in training_result:
                mechanics_verdict = f"SYSTEM: Player tried to train but FAILED. Reason: {training_result['error']}. Describe their disappointment."
                logs.append(f"❌ {training_result['error']}")
            else:
                mechanics_verdict = training_result['story_prompt']
                skill = training_result['skill']
                gain = training_result['stat_gain']
                days_spent = training_result['cost_time']
                cost_gold = int(training_result['cost_gold'])
                energy_cost = training_result.get('energy_cost', 40)

                logs.append(
                    f"🏋️ Тренування: {skill} +{gain} (Витрачено: {days_spent} дн., {cost_gold} 🪙, {energy_cost} ⚡)")

                if training_result.get("temp_debuff", 0) > 0:
                    logs.append(f"🩸 Травма від перевтоми! Штраф -{training_result['temp_debuff']} до '{skill}'.")

                mechanical_updates["minutes_passed"] = days_spent * 1440

                current_energy = safe_int(profile.get("Енергія", 100))
                profile["Енергія"] = max(0, current_energy - energy_cost)

                if "skill_impact" not in mechanical_updates:
                    mechanical_updates["skill_impact"] = {}
                mechanical_updates["skill_impact"][skill] = gain

                current_gold = safe_int(profile.get("Особисте Золото", 0))
                if cost_gold > 0:
                    profile["Особисте Золото"] = max(0, current_gold - cost_gold)
                    logs.append(f"💰 Золото: -{cost_gold} (Оплата вчителю)")

                if "set_cooldown" in training_result:
                    if "training_cooldowns" not in profile:
                        profile["training_cooldowns"] = {}
                    profile["training_cooldowns"][skill] = training_result["set_cooldown"]

        mechanical_updates["skill_used"] = "None"

    # 3. ОДРАЗУ застосовуємо всі наслідки до профілю (час, здоров'я, інвентар)
    profile, impact_logs = apply_system_impacts(profile, mechanical_updates)
    logs.extend(impact_logs)

    duration = time.time() - t_start
    debug_log += f"\n⏱️ [WORKER AI / MECHANICS] зайняло: {duration:.2f}s"
    timing_details.append(f"🤖 Mechanics: {duration:.2f}s")

    # === ЕТАП 3: ГЕНЕРАЦІЯ СЮЖЕТУ (MAIN AI) ===
    t_start = time.time()

    current_time_str = profile.get("Ігровий час", "")
    day_match = re.search(r'День (\d+)', current_time_str)
    current_day = int(day_match.group(1)) if day_match else 1

    world_events = {
        5: "Починається сильна хуртовина. Пересування стає майже неможливим.",
        14: "До міста прибуває величезний королівський кортеж.",
        30: "Починається війна. Оголошено загальну мобілізацію."
    }

    event_injection = ""
    if current_day in world_events:
        event_injection = f"\n[SYSTEM INTERRUPT: СЬОГОДНІ ВАЖЛИВА ПОДІЯ: {world_events[current_day]}. Ти ПОВИНЕН органічно вплести цю подію в поточну сцену.]\n"

    clocks_info = json.dumps(profile.get("Годинники", {}), ensure_ascii=False)

    prompt = f"""<system>
    Role: Impartial Game Master for a Grimdark RPG (Game of Thrones).
    Objective: Narrate the immediate consequences of the player's action based STRICTLY on the mechanical verdict.
    Tone: Realistic, cruel, atmospheric. The world does not revolve around the player.
    </system>

    <player_state>
    {profile_json}
    GM NOTE: Player currently has {profile.get("Особисте Золото")} gold. Use this to limit their purchases.
    </player_state>

    <world_context>
    {GAME_ERA_CONTEXT}
    {context_knowledge}
    {event_injection}
    </world_context>

    <scene_state>
    CURRENT TIME: {current_time_str}
    CURRENT LOCATION: {curr_loc}
    VISIBLE NPC ROSTER (Current characters in the scene):
    {npc_context_text}
    </scene_state>

    <mechanical_verdict>
    {mechanics_verdict}
    Instruction: You MUST adhere to this verdict. 
    - If FAILURE: Describe a painful or frustrating outcome. Add tension.
    - If SUCCESS: Describe a triumph.
    - NO NUMBERS: NEVER mention specific numeric values for gold, health, skills, or energy in the story text.
    </mechanical_verdict>

    <golden_laws_of_agency>
    1. NEVER TOUCH THE PLAYER: You control NPCs and Physics. The Player controls ONLY their Hero.
    2. NO MIND READING: Never tell the player what they feel or think. Show, don't tell.
    3. NO VENTRILOQUISM: You are FORBIDDEN from writing dialogue lines for the Hero.
    4. INTENT vs RESULT: Player describes INTENT. You determine RESULT based on the mechanical verdict.
    5. THE STOP SIGNAL: You MUST stop writing immediately after the NPC reacts. Leave the ball in the player's court.
    6. ANTI-RAILROADING: If the player leaves the scene, the scene MUST end.
    </golden_laws_of_agency>

    <combat_and_pacing>
    1. TARGET LOCK: Track who is fighting whom. If Player fights a minion, the boss is safe.
    2. EXCHANGE ONLY: Describe ONE move (Attack -> Defense -> Result -> Stop).
    3. NO INSTANT KILLS: A player with low stats cannot "one-shot" a Champion.
    </combat_and_pacing>

    <doctrine_of_resistance_and_npc_behavior>
    1. NO "YES-MAN": The world resists the player. Do not agree just to move the plot.
    2. NPC POWER & RETALIATION: Powerful NPCs will strike back violently or politically if insulted or threatened. They do not just smile.
    3. TRAUMA SIMULATION: Victims of abuse DO NOT instantly forgive. Sudden kindness causes panic/suspicion, not trust.
    4. SECRETS: Use the [SECRET] field to guide NPC behavior, but DO NOT say the secret out loud.
    5. ALLOW DOWNTIME: It is perfectly fine for a scene to be quiet and atmospheric.
    </doctrine_of_resistance_and_npc_behavior>

    <history>
    {history_text}
    </history>

    <current_turn>
    PLAYER ACTION: "{user_input}"
    </current_turn>

    <json_generation_rules>
    1. OBJECT PERMANENCE: If physical items or gold are exchanged, you MUST update the NPC's "Inventory" field.
    2. EMPTY STRINGS: If a specific field has NOT changed, you MUST set its value to exactly "". 
    3. FORBIDDEN WORDS: Do not write "no change", "same", or "без змін". Use "".
    4. NPC SUMMONING RULE: You MUST use exact names from the VISIBLE NPC ROSTER. HOWEVER, if the story logically requires a famous canonical Game of Thrones character to appear (e.g., Lord Stark, Oberyn), you MAY introduce them by outputting their exact canonical Full Name in the "Name" field. NEVER invent fake names.
    </json_generation_rules>

    <example_output>
    {{
        "step1_tot_brainstorming": "Branch 1 (Merciless): Guard attacks immediately. Branch 2 (Realistic): Guard takes the bribe but demands more. Branch 3 (Passive): Guard just opens the gate.",
        "step2_adversarial_validation": "Lore Keeper: Branch 1 is too extreme for a 5-gold bribe. Sadist GM: Branch 3 is too boring and friendly. Branch 2 is perfect—it drains the player's resources and maintains tension.",
        "story": "Вартовий хапає монети, швидко пробуючи одну на зуб. Він ховає їх у кишеню і ледь помітно киває, відчиняючи хвіртку лише настільки, щоб ви могли протиснутися. «Тільки без дурниць», — бурмоче він. Що ви робите далі?",
        "npc_updates": [
            {{
                "Name": "Вартовий Пітер",
                "Description": "",
                "Character": "",
                "Goal": "",
                "Relation_Player": "",
                "Memory_Anchor": "Взяв хабар у гравця, але все ще не довіряє йому.",
                "Relation_NPCs": "",
                "Secrets": "",
                "Inventory": "5 золотих монет від гравця",
                "Status": "Active"
            }}
        ]
    }}
    </example_output>

    OUTPUT IN STRICT JSON FORMAT ONLY:
    {{
        "step1_tot_brainstorming": "Generate 3 extremely short distinct approaches (Branches) on how the world/NPC should react to the player.",
        "step2_adversarial_validation": "Have 'The Sadist GM' and 'The Lore Keeper' ruthlessly critique the branches to select the most grimdark, realistic, and rule-compliant path.",
        "story": "Your finalized narrative text in Ukrainian based on the winning branch. End with a prompt for the next action.",
        "npc_updates": [
            {{
                "Name": "Exact Name from Roster",
                "Description": "New visual state/injury OR \\"\\"",
                "Character": "New personality OR \\"\\"",
                "Goal": "New goal OR \\"\\"",
                "Relation_Player": "New attitude OR \\"\\"",
                "Memory_Anchor": "Reason for attitude change OR \\"\\"",
                "Relation_NPCs": "New attitude to others OR \\"\\"",
                "Secrets": "New secret OR \\"\\"",
                "Inventory": "Physical items/gold held OR \\"\\"",
                "Status": "Active or Dead or Injured"
            }}
        ]
    }}"""

    try:
        def _sync_gen_main():
            return model.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen_main)
        raw_text = response.text.strip()
        print(f"\n[AI RAW TEXT]:\n{raw_text}\n")
        ai_data = clean_and_parse_json(raw_text)

        if not ai_data and '"story":' in raw_text:
            try:
                story_match = re.search(r'"story":\s*"(.*?)(?<!\\)"', raw_text, re.DOTALL)
                extracted_story = story_match.group(1).replace('\\"', '"').replace('\\n',
                                                                                   '\n') if story_match else raw_text
                ai_data = {"story": extracted_story, "updates": {}}
            except:
                ai_data = {"story": raw_text, "updates": {}}

        if not ai_data: ai_data = {"story": raw_text, "updates": {}}

        story = ai_data.get("story", "...")
        if "Тут напиши" in story or len(story) < 20:
            def _sync_gen_fix():
                return model.generate_content(
                    f"Ти GM. Гравець: {user_input}. Опиши наслідки художньо. Закінчи питанням.")

            fix_resp = await asyncio.to_thread(_sync_gen_fix)
            story = fix_resp.text

        duration = time.time() - t_start
        debug_log += f"\n⏱️ [MAIN AI] зайняло: {duration:.2f}s"
        timing_details.append(f"✍️ Story: {duration:.2f}s")

        # === ЕТАП 4: ЗАСТОСУВАННЯ НАСЛІДКІВ ТА ЗБЕРЕЖЕННЯ ===
        t_start = time.time()

        npc_changes = ai_data.get("npc_updates", [])

        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"

        if "skill_used" in mechanical_updates and mechanical_updates["skill_used"] != "None":
            skill = mechanical_updates["skill_used"]
            roll_str = mechanical_updates.get("dice_roll", "0")
            skill_val = mechanical_updates.get("skill_val", 0)
            total_score = mechanical_updates.get("total_score", 0)
            outcome = mechanical_updates.get("outcome", "UNKNOWN")
            circumstance = mechanical_updates.get("circumstance", "NORMAL")

            circ_ua = " (Перевага)" if circumstance == "ADVANTAGE" else " (Недолік)" if circumstance == "DISADVANTAGE" else ""

            if outcome == "CRITICAL SUCCESS":
                icon, outcome_ua = "🔥", "КРИТИЧНИЙ УСПІХ"
            elif outcome == "CRITICAL FAILURE":
                icon, outcome_ua = "💀", "КРИТИЧНИЙ ПРОВАЛ"
            elif outcome == "SUCCESS":
                icon, outcome_ua = "✅", "УСПІХ"
            else:
                icon, outcome_ua = "❌", "ПРОВАЛ"

            target_dc = mechanical_updates.get("difficulty", 100)
            dice_log = f"{icon} {skill}{circ_ua}: Кидок {roll_str} + Навичка {skill_val} = {total_score} (Ціль: {target_dc}) -> {outcome_ua}"
            logs.insert(0, dice_log)

            if outcome == "CRITICAL SUCCESS":
                logs.insert(1, f"✨ Осяяння! Навичка '{skill}' назавжди зросла на +1.")
            elif outcome == "CRITICAL FAILURE":
                temp_debuff = mechanical_updates.get("temp_debuff", {}).get(skill, 0)
                logs.insert(1, f"🩸 Тяжкий наслідок! Отримано тимчасовий штраф -{temp_debuff} до навички '{skill}'.")
                if mechanical_updates.get("permanent_scar"):
                    logs.insert(2, f"☠️ Фатальна помилка! Навичка '{skill}' назавжди знижена на -1.")

        new_location = profile.get("Поточне місцезнаходження", "")
        if old_location != new_location and len(new_location) > 3:
            print(f"✈️ TRAVEL: {old_location} -> {new_location}")
            current_char_name = profile.get("Ім'я", "")

            # Асинхронна функція для заселення нової локації
            async def travel_population_task(new_loc, char_name):
                try:
                    def _sync_gen_travel():
                        return model_worker.generate_content(
                            f'Describe atmosphere in "{new_loc}" (Year 298) in 1 sentence.')

                    situation_resp = await asyncio.to_thread(_sync_gen_travel)
                    situation = situation_resp.text.strip()
                except:
                    situation = "A tense day in Westeros."

                # Припускаємо, що populate_contextual_npcs ми теж зробимо асинхронним
                await populate_contextual_npcs(new_loc, situation, excluded_name=char_name)

            asyncio.create_task(travel_population_task(new_location, current_char_name))

        # Асинхронне фонове збереження
        async def background_task(chat_id_arg, user_id_arg, profile_arg, char_name_arg, input_arg, story_arg,
                                  npc_changes_arg, legal_names_arg):
            try:
                await save_user_data(user_id_arg, profile_arg, char_name_arg)
                if npc_changes_arg:
                    await update_npcs_in_db(npc_changes_arg, legal_names_arg)

                # Обидва summarize_turn тепер awaitable
                short_hist_turn = await summarize_turn(story_arg)
                short_user_inp = await summarize_turn(input_arg)

                if chat_id_arg in user_sessions:
                    hist = user_sessions[chat_id_arg].get('history', [])
                    hist.append({"role": "User", "content": short_user_inp})
                    hist.append({"role": "GM", "content": short_hist_turn})

                    if len(hist) > 20:
                        history_to_compress = "\n".join([f"{m['role']}: {m['content']}" for m in hist])
                        summary_prompt = f"Summarize this RPG history into one short paragraph (Ukrainian). Focus on the current location, main objective, and immediate situation:\n{history_to_compress}"
                        try:
                            def _sync_gen_sum():
                                return model_worker.generate_content(summary_prompt)

                            summary_resp = await asyncio.to_thread(_sync_gen_sum)
                            user_sessions[chat_id_arg]['history'] = [
                                {"role": "SYSTEM", "content": f"PREVIOUS EVENTS SUMMARY: {summary_resp.text.strip()}"}]
                            print("🧹 [CONTEXT FLUSH] Пам'ять успішно стиснуто!")
                        except Exception as ex:
                            print(f"⚠️ Помилка стиснення: {ex}")
                            user_sessions[chat_id_arg]['history'] = hist[-20:]
                    else:
                        user_sessions[chat_id_arg]['history'] = hist
            except Exception as exept:
                print(f"❌ [BG ERROR] {exept}")

        # Запускаємо фонову задачу замість створення окремого системного потоку
        asyncio.create_task(
            background_task(chat_id, user_id, profile, profile.get("Ім'я"), user_input, story, npc_changes,
                            legal_npc_names))

        duration = time.time() - t_start
        timing_details.append(f"💾 Save: {duration:.2f}s")
        total_time = time.time() - global_start

        debug_msg = "⏱️ *ТЕХНІЧНИЙ ЗВІТ ХОДУ:*\n━━━━━━━━━━━━━━━━\n"
        debug_msg += "\n".join(timing_details)
        debug_msg += "\n━━━━━━━━━━━━━━━━"
        debug_msg += f"\n🏁 *ВСЬОГО:* `{total_time:.2f}s`"
        user_sessions[chat_id]['last_debug_time'] = debug_msg

        change_log = "\n\n📊 " + " | ".join(logs) if logs else ""
        return story + change_log

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "Quota" in error_msg:
            return "⏳ *Старі Боги потребують часу на роздуми...*\n(Перевищено ліміт запитів до серверів. Будь ласка, зачекайте 20-30 секунд і повторіть дію)."
        else:
            import traceback
            traceback.print_exc()
            return f"📜 *Ворон згубив вашого листа...* Щось пішло не так. (Помилка: {error_msg})"