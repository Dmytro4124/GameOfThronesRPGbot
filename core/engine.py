# core/engine.py
import time
import json
import re
import asyncio

from core.ai_client import model, model_worker, clean_and_parse_json
from core.mechanics import apply_system_impacts, process_training_request, safe_int, resolve_action_mechanics, validate_action
from core.prompts import GAME_ERA_CONTEXT
from core.world import populate_contextual_npcs
from database.operations import (
    get_user_data, save_user_data, get_relevant_context,
    get_location_npcs, update_npcs_in_db
)

user_sessions = {}


def _build_impact_hints(impact_logs: list) -> str:
    """Converts technical impact log strings into GM narrative cues (no numbers)."""
    hints = []
    for entry in impact_logs:
        if "⚡ Енергія" in entry:
            m_new = re.search(r'-> (\d+)', entry)
            m_delta = re.search(r'\(([+-]?\d+)\)', entry)
            new_val = int(m_new.group(1)) if m_new else 100
            delta = int(m_delta.group(1)) if m_delta else 0

            if delta >= 1:
                hints.append("ENERGY RESTORE: The character recovered. GM may subtly note a sense of refreshment or renewed vigour.")
            elif new_val <= 29 and delta < 0:
                hints.append("ENERGY DRAIN (SERIOUS): The character is severely exhausted. GM MUST describe visible struggle: trembling hands, ragged breath, faltering steps.")
            elif new_val <= 59 and delta <= -10:
                hints.append("ENERGY DRAIN: The character is noticeably tired. GM MUST weave weariness into the narrative (e.g., 'your legs grow heavy', 'a wave of exhaustion washes over you').")
            # 60-100: no hint — character is fine

        elif "❤️ Здоров'я" in entry:
            m_vals = re.search(r'(\d+) -> (\d+)', entry)
            if not m_vals:
                continue
            old_val = int(m_vals.group(1))
            new_val = int(m_vals.group(2))
            delta = new_val - old_val

            if delta >= 1:
                hints.append("HEALTH RESTORE: The character healed. GM may note a sense of relief or physical recovery.")
            elif delta < 0:
                if new_val <= 39:
                    hints.append("HEALTH CRITICAL: The character is gravely wounded. GM MUST convey urgency and mortal danger (e.g., 'blood pours freely', 'each breath is a struggle', 'death hovers close').")
                elif new_val <= 69:
                    hints.append("HEALTH WOUND (SERIOUS): The character is significantly hurt. GM MUST describe the wound clearly (e.g., 'blood flows', 'a sharp pain flares', 'you stagger').")
                else:
                    hints.append("HEALTH WOUND (MINOR): The character took a glancing blow. GM SHOULD note it briefly (e.g., 'a wince', 'a graze', 'the sting of a blow').")

    return "\n".join(hints)


async def summarize_turn(gm_response):
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
    debug_log = ""
    global_start = time.time()
    debug_log += f"🚀 [START] Хід гравця {chat_id}..."

    user_id = chat_id
    timing_details = []

    t_start = time.time()

    profile, row_id = await get_user_data(user_id)
    if not profile: return "❌ Профіль не знайдено.", []

    session = user_sessions.get(chat_id, {})
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"state": "GAME_ACTIVE", "history": []}
    history = session.get('history', [])

    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-40:]])

    # === ОНОВЛЕНО: Читаємо локацію та сцену ===
    old_location = profile.get("Поточне місцезнаходження", "Невідомо")
    old_scene = profile.get("Поточна сцена", "Невідомо")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)

    curr_loc = profile.get("Поточне місцезнаходження", "Невідомо")
    curr_scene = profile.get("Поточна сцена", "Невідомо")

    # === ОНОВЛЕНО: Передаємо і локацію, і сцену для жорсткої фільтрації ===
    context_knowledge = await get_relevant_context(user_input, curr_loc)
    npc_context_text, legal_npc_names = get_location_npcs(curr_loc, curr_scene)

    duration = time.time() - t_start
    timing_details.append(f"📚 Data: {duration:.2f}s")

    t_start = time.time()
    logs = []

    # === ЦЕНЗОР: Перевірка дії до будь-якої механіки ===
    is_valid, refusal_reason = await validate_action(user_input, profile)
    if not is_valid:
        return refusal_reason + "\n\n📊 🛑 Дію заблоковано Цензором.", []

    mechanics_verdict, mechanical_updates = await resolve_action_mechanics(user_input, profile)

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
                    debuff_val = training_result["temp_debuff"]
                    logs.append(f"🩸 Травма від перевтоми! Штраф -{debuff_val} до '{skill}'.")
                    current_debuffs = profile.get("temp_debuffs", {})
                    current_debuffs[skill] = debuff_val
                    profile["temp_debuffs"] = current_debuffs

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

    # === [SECURITY AUDIT] САНІТИЗАЦІЯ LLM INJECTION ===
    # Гарантуємо, що ШІ не згалюцинував зміну навичок в обхід правил
    is_valid_training = mechanical_updates.get("action_type") == "training"
    is_critical_success = mechanical_updates.get("outcome") == "CRITICAL SUCCESS"
    is_critical_failure = mechanical_updates.get("outcome") == "CRITICAL FAILURE"

    if not (is_valid_training or is_critical_success or is_critical_failure):
        if "skill_impact" in mechanical_updates:
            print(f"⚠️ [SECURITY] Видалено нелегальну галюцинацію навичок від ШІ: {mechanical_updates['skill_impact']}")
            mechanical_updates.pop("skill_impact", None)

    profile, impact_logs = apply_system_impacts(profile, mechanical_updates)
    logs.extend(impact_logs)
    impact_narrative_hints = _build_impact_hints(impact_logs)

    duration = time.time() - t_start
    timing_details.append(f"🤖 Mechanics: {duration:.2f}s")

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
    CURRENT CITY: {profile.get("Поточне місцезнаходження", "")}
    CURRENT SCENE: {profile.get("Поточна сцена", "")}
    VISIBLE NPC ROSTER (Only characters physically present in this exact scene):
    {npc_context_text}
    </scene_state>

    <mechanical_verdict>
    {mechanics_verdict}
    Instruction: You MUST adhere to this verdict.
    - If FAILURE: Describe a painful or frustrating outcome. Add tension.
    - If SUCCESS: Describe a triumph.
    - NO NUMBERS (ABSOLUTE): NEVER write any digit or numeral in the story text.
      This includes numbers spoken by the player in their action.
      MANDATORY CONVERSION — if the player mentions a number, convert it:
        "5000 gold" → "a fortune", "a heavy pouch of gold", "enough to buy a lordship"
        "100 soldiers" → "a host of men", "a small army"
        "3 days" → "a few days", "several days' ride"
      The narrative world has no arithmetic. Only weight, scale, and sensation.
    </mechanical_verdict>
    {f'<system_impacts>{chr(10)}{impact_narrative_hints}{chr(10)}RULE: You MUST weave the above hints into the story narrative WITHOUT using any numbers. These are mandatory narrative beats.{chr(10)}</system_impacts>' if impact_narrative_hints else ''}

    <golden_laws_of_agency>
    1. NEVER TOUCH THE PLAYER: You control NPCs and Physics. The Player controls ONLY their Hero.
    2. NO MIND READING: Never tell the player what they feel or think. Show, don't tell.
    3. NO VENTRILOQUISM: You are FORBIDDEN from writing dialogue lines for the Hero.
    4. INTENT vs RESULT: Player describes INTENT. You determine RESULT based on the mechanical verdict.
    5. THE STOP SIGNAL: You MUST stop writing immediately after the NPC reacts. Leave the ball in the player's court.
    6. SCENE EXIT OVERRIDE (CRITICAL): If the player's action is to LEAVE, WALK AWAY, or CHANGE LOCATION, the current scene IMMEDIATELY ENDS. You are STRICTLY FORBIDDEN from describing the reactions, thoughts, or actions of the NPCs the player is leaving behind. Describe ONLY the transition and the new environment.
    7. COMPANION MOVEMENT: If the player moves to a new Scene/Location, you MUST update the "Scene" and "Location" fields in the npc_updates JSON for any allied NPCs who are actively following the player. Characters left behind MUST NOT be updated.
    </golden_laws_of_agency>

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
    4. NPC SUMMONING RULE: You MUST use exact names from the VISIBLE NPC ROSTER. 
    5. SUGGESTED ACTIONS: You MUST provide exactly 4 distinct, logical actions the player could take next based on this specific situation. Keep them under 5 words in Ukrainian. Make them fit the Grimdark tone.
    </json_generation_rules>

    OUTPUT IN STRICT JSON FORMAT ONLY:
    {{
        "step1_tot_brainstorming": "Generate 3 extremely short distinct approaches (Branches) on how the world/NPC should react to the player.",
        "step2_adversarial_validation": "Have 'The Sadist GM' and 'The Lore Keeper' ruthlessly critique the branches to select the most grimdark, realistic, and rule-compliant path.",
        "story": "Your finalized narrative text in Ukrainian based on the winning branch. End with a prompt for the next action.",
        "suggested_actions": ["Action 1", "Action 2", "Action 3", "Action 4"],
        "npc_updates": [
            {{
                "Name": "Exact Name from Roster",
                "Location": "",
                "Scene": "",
                "Description": "",
                "Character": "",
                "Goal": "",
                "Relation_Player": "",
                "Memory_Anchor": "",
                "Relation_NPCs": "",
                "Secrets": "",
                "Inventory": "",
                "Status": "Active"
            }}
        ]
    }}"""

    try:
        def _sync_gen_main():
            return model.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen_main)
        raw_text = response.text.strip()
        ai_data = clean_and_parse_json(raw_text)

        if not ai_data and '"story":' in raw_text:
            try:
                story_match = re.search(r'"story"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_text, re.DOTALL)
                extracted_story = story_match.group(1).replace('\\"', '"').replace('\\n',
                                                                                   '\n') if story_match else raw_text
                ai_data = {"story": extracted_story, "updates": {}, "suggested_actions": []}
            except:
                ai_data = {"story": raw_text, "updates": {}, "suggested_actions": []}

        if not ai_data: ai_data = {"story": raw_text, "updates": {}, "suggested_actions": []}

        story = ai_data.get("story", "...")
        suggested_actions = ai_data.get("suggested_actions", [])

        if "Тут напиши" in story or len(story) < 20:
            def _sync_gen_fix():
                return model.generate_content(
                    f"Ти GM. Гравець: {user_input}. Опиши наслідки художньо. Закінчи питанням.")

            fix_resp = await asyncio.to_thread(_sync_gen_fix)
            story = fix_resp.text

        duration = time.time() - t_start
        timing_details.append(f"✍️ Story: {duration:.2f}s")

        t_start = time.time()
        npc_changes = ai_data.get("npc_updates", [])

        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"
            suggested_actions = ["🔄 Почати заново"]

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
                logs.insert(1, f"✨ Осяяння! Навичка '{skill}' назавжди зросла на +1.")
            elif outcome == "CRITICAL FAILURE":
                icon, outcome_ua = "💀", "КРИТИЧНИЙ ПРОВАЛ"
                temp_debuff = mechanical_updates.get("temp_debuff", {}).get(skill, 0)
                logs.insert(1, f"🩸 Тяжкий наслідок! Отримано тимчасовий штраф -{temp_debuff} до навички '{skill}'.")
                if mechanical_updates.get("permanent_scar"):
                    logs.insert(2, f"☠️ Фатальна помилка! Навичка '{skill}' назавжди знижена на -1.")
            elif outcome == "SUCCESS":
                icon, outcome_ua = "✅", "УСПІХ"
            else:
                icon, outcome_ua = "❌", "ПРОВАЛ"

            target_dc = mechanical_updates.get("difficulty", 100)
            dice_log = f"{icon} {skill}{circ_ua}: Кидок {roll_str} + Навичка {skill_val} = {total_score} (Ціль: {target_dc}) -> {outcome_ua}"
            logs.insert(0, dice_log)

        new_location = profile.get("Поточне місцезнаходження", "")
        # Модифіковано: Тепер генерація канонічних NPC враховує і зміну сцени (якщо треба)
        if old_location != new_location and len(new_location) > 3:
            current_char_name = profile.get("Ім'я", "")

            async def travel_population_task(new_loc, char_name):
                try:
                    def _sync_gen_travel():
                        return model_worker.generate_content(
                            f'Describe atmosphere in "{new_loc}" (Year 298) in 1 sentence.')

                    situation_resp = await asyncio.to_thread(_sync_gen_travel)
                    situation = situation_resp.text.strip()
                except:
                    situation = "A tense day in Westeros."
                await populate_contextual_npcs(new_loc, situation, excluded_name=char_name)

            await asyncio.create_task(travel_population_task(new_location, current_char_name))

        async def background_task(chat_id_arg, user_id_arg, profile_arg, char_name_arg, input_arg, story_arg,
                                  npc_changes_arg, legal_names_arg):
            try:
                await save_user_data(user_id_arg, profile_arg, char_name_arg)
                if npc_changes_arg:
                    await update_npcs_in_db(npc_changes_arg, legal_names_arg)

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
                        except Exception as ex:
                            print(f"⚠️ Помилка стиснення: {ex}")
                            user_sessions[chat_id_arg]['history'] = hist[-20:]
                    else:
                        user_sessions[chat_id_arg]['history'] = hist
            except Exception as exept:
                print(f"❌ [BG ERROR] {exept}")

        await asyncio.create_task(
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

        return story + change_log, suggested_actions

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"📜 *Ворон згубив вашого листа...* (Помилка: {str(e)})", []