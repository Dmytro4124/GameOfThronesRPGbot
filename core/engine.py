# core/engine.py
import time
import json
import re
from threading import Thread

# Імпорти з нашої нової структури
from core.ai_client import model, model_worker, clean_and_parse_json
from core.mechanics import apply_system_impacts, check_skill_mechanics, process_training_request, safe_int
from core.prompts import GAME_ERA_CONTEXT
from core.world import populate_contextual_npcs
from database.operations import (
    get_user_data, save_user_data, get_relevant_context,
    get_location_npcs, update_npcs_in_db
)

# Збереження стану гравців у пам'яті (раніше було в main.py)
user_sessions = {}


def summarize_turn(gm_response):
    """
    Стискає хід в одне речення для історії.
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
        resp = model_worker.generate_content(prompt)
        return resp.text.strip()
    except:
        return f"Гравець діє."


def process_game_turn(chat_id, user_input):
    """Основний ігровий цикл (рушій)"""
    debug_log = ""
    global_start = time.time()
    debug_log += f"🚀 [START] Хід гравця {chat_id}..."

    user_id = chat_id
    timing_details = []

    # === ЕТАП 1: ЗАВАНТАЖЕННЯ ДАНИХ ===
    t_start = time.time()

    profile, row_id = get_user_data(user_id)
    if not profile: return "❌ Профіль не знайдено."

    session = user_sessions.get(chat_id, {})
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"state": "GAME_ACTIVE", "history": []}
    history = session.get('history', [])

    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-40:]])
    old_location = profile.get("Поточне місцезнаходження", "")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    curr_loc = profile.get("Поточне місцезнаходження", "")

    context_knowledge = get_relevant_context(user_input, curr_loc)
    npc_context_text = get_location_npcs(curr_loc)

    duration = time.time() - t_start
    debug_log += f"\n⏱️ [DATA LOAD] зайняло: {duration:.2f}s"
    timing_details.append(f"📚 Data: {duration:.2f}s")

    # === ЕТАП 2: МЕХАНІКА ТА ПЕРЕВІРКА НАВИЧОК ===
    t_start = time.time()

    training_result = process_training_request(user_input, profile)
    mechanics_note = ""
    extra_updates = {}

    if training_result:
        if "error" in training_result:
            mechanics_note = f"SYSTEM: Player tried to train but FAILED. Reason: {training_result['error']}. Describe their disappointment."
        else:
            mechanics_note = training_result['story_prompt']
            extra_updates = {
                "gold_impact": f"spend_{training_result['cost_gold']}",
                "skill_impact": {training_result['skill']: training_result['stat_gain']},
                "time_passed": "days"
            }
            current_gold = safe_int(profile.get("Особисте Золото", 0))
            profile["Особисте Золото"] = max(0, current_gold - int(training_result['cost_gold']))
    else:
        mechanics_note = check_skill_mechanics(user_input, profile)

    duration = time.time() - t_start
    debug_log += f"\n⏱️ [WORKER AI] зайняло: {duration:.2f}s"
    timing_details.append(f"🤖 Worker: {duration:.2f}s")

    # === ЕТАП 3: ГЕНЕРАЦІЯ СЮЖЕТУ (MAIN AI) ===
    t_start = time.time()

    # --- ПЕРЕВІРКА СВІТОВИХ ПОДІЙ ЗА ДАТОЮ ---
    current_time_str = profile.get("Ігровий час", "")
    day_match = re.search(r'День (\d+)', current_time_str)
    current_day = int(day_match.group(1)) if day_match else 1

    # Словник подій (Ключ = День)
    world_events = {
        5: "Починається сильна хуртовина. Пересування стає майже неможливим.",
        14: "До міста прибуває величезний королівський кортеж.",
        30: "Починається війна. Оголошено загальну мобілізацію."
    }

    event_injection = ""
    if current_day in world_events:
        event_injection = f"\n[SYSTEM INTERRUPT: СЬОГОДНІ ВАЖЛИВА ПОДІЯ: {world_events[current_day]}. Ти ПОВИНЕН органічно вплести цю подію в поточну сцену.]\n"

    clocks_info = json.dumps(profile.get("Годинники", {}), ensure_ascii=False)

    prompt = f"""
        YOU — MERCILESS GAME MASTER (GM) IN THE WORLD OF “GAME OF THRONES.” NOT A NOVELIST. DO NOT WRITE A BOOK. RUN A GAME.
        Your task: Run the game, balancing between the plot, player freedom, and CLEAR MECHANICS, to create a realistic, dangerous, and dark story. The world does not revolve around the player.
        YOU ARE NOT A FRIEND. YOU ARE A SIMULATOR OF A CRUEL REALITY (Game of Thrones).
        Your goal is NOT to tell a heroic story, but to honestly simulate the consequences of stupidity, arrogance, and physics.

        === HERO ===
        {profile_json}
        (GM NOTE: Player currently has {profile.get("Особисте Золото")} gold. Use this to limit their purchases).

        === WORLD CONTEXT & EVENTS ===
        {GAME_ERA_CONTEXT}
        {context_knowledge}
        {event_injection}

        {npc_context_text}

        === ACTIVE CLOCKS (TENSION) ===
        {clocks_info}
        (If a clock reaches its maximum, e.g., 4/4, the threat happens immediately! Do not spawn threats if clock is 0 or 1.)

        === NPC INTERACTION RULES (CRITICAL) ===
        1. **PRIORITY:** If the player says "I look around" or "I talk to the merchant", YOU MUST check the "VISIBLE NPC ROSTER" above.
           - If there is a merchant named 'Lotho' in the list -> Use Lotho.
           - DO NOT invent a new 'Generic Merchant' if a specific one exists.
        2. **SECRETS:** Use the [SECRET] field to determine how the NPC acts, creates tension, or lies.
           - Example: If Secret is "Is a spy", the NPC should ask too many questions, but NOT say "Hello, I am a spy."
        3. **RELATIONS:** Use 'Attitude to Player'. If it says 'Suspicious', the NPC will not be helpful without a bribe or persuasion.

        === TAGS GUIDE (STRICT VALUES REQUIRED) ===
        1. **time_passed** (How much time has passed):
           - “short” (pure dialogue, thoughts, quick look)
           - “medium” (an hour or two - walk, exploration)
           - “long” (half a day - study, work)
           - “sleep” (night - rest)
           - “days” (intense training, travel)
           - "none"

        2. **health_impact** (Health consequences):
           - “none” (no change)
           - “heal_small” / “heal_full”
           --- ONLY if the enemy successfully hit the player in the text ---
           - “dmg_light” (bruise, scratch: -5 HP)
           - “dmg_medium” (sword wound, burn: -15 HP)
           - “dmg_heavy” (critical injury: -30 HP)
           - “dmg_fatal” (death: -100 HP)

        3. **gold_impact** (Economy):
           - “none”
           - “spend_small” (food, small items)
           - “spend_medium” (weapons, clothing)
           - “spend_large” (horses, houses)
           - “earn_small” (found a coin)
           - “earn_medium” (quest reward)
           - “earn_large” (grand treasure)

        4. **inventory** - "inventory_new": ["Item Name"] (If obtained).
           - "inventory_lost": ["Item Name"] (If lost/eaten).

        5. **clocks_impact** (Tension management):
           - Use {{"Name of Threat": 1}} to increase tension if player acts suspiciously or fails a check.
           - Use {{"Name of Threat": "clear"}} to reset it.

        6. **npc_updates**:
           Use this IF and ONLY IF an interacting NPC changes significantly.
           Fields to update:
           - "Relation_Player": "Hostile", "Friendly", "Afraid", "Deadly Enemy".
           - "Goal": If their motivation changes.
           - "Status": "Dead", "Injured", "Fled".
           - "Secrets": If a secret is revealed or created. 

        === THE GOLDEN LAWS OF AGENCY (VIOLATION = FAILURE) ===
        1. **NEVER TOUCH THE PLAYER:** You control NPCs, Weather, and Physics. The Player controls ONLY their Hero.
           - ❌ BAD: "Illyrio screams, and you turn around to leave, feeling angry."
           - ✅ GOOD: "Illyrio screams insults at your back. The door is ahead. What do you do?"

        2. **NO MIND READING:** Never tell the player what they feel, think, or realize.
           - ❌ BAD: "You realized he was lying." / "You felt fear."
           - ✅ GOOD: "His eyes dart nervously." / "His hand trembles on the hilt."

        3. **COMBAT PACING & CONSISTENCY (CRITICAL):**
            - **TARGET LOCK:** Track who is fighting whom.
            - If Player fights a MINION, the BOSS is SAFE watching from the side.
            - **EXCHANGE ONLY:** Describe ONE move. Attack -> Defense -> Result -> Stop.
            - **NO INSTANT KILLS:** A player with low stats cannot "one-shot" a Champion.

        4. **ANTI-RAILROADING:**
           - If the player leaves ("I walk away"), the scene MUST end.

        5. **INTENT vs RESULT:** The player describes their INTENT ("I try to kill him"). YOU determine the RESULT based on logic.

        6. **THE STOP SIGNAL:** You MUST stop writing immediately after the NPC reacts or the event happens.
           - BAD: "The guard attacks, you dodge, and then you kill him."
           - GOOD: "The guard swings his sword at your head! What do you do?"

        7. **NO VENTRILOQUISM:** You are FORBIDDEN from writing dialogue lines for the Hero.
       - ❌ BAD: "You say: 'I will never bow!'"
       - ✅ GOOD: "You stare at him silently. What do you say?"

        === DOCTRINE OF RESISTANCE (CRITICAL RULES) ===
        1. **NO "YES-MAN":** Do not agree with the player just to move the plot.
        2. **NPC POWER:** Illyrio Mopatis, Tywin Lannister, and Iron Bank envoys are SMARTER and MORE POWERFUL than the player.
        3. **PACING & LOGIC (STOP THE LOOP):**
           - **ONE CLIMAX PER SCENE:** If the player wins a major conflict, THE SCENE ENDS. Do NOT spawn a new random enemy immediately.
        4. **PHYSICS & TIME:** - Repairing a ship = Weeks. Traveling = Days.
        5. **CONSEQUENCES:** If the player commits a crime in a Free City, the Magisters WILL send the City Watch.
        6. **PAUSE RULE:** Do NOT describe a long chain of actions for NPCs.

        **IF THE PLAYER FAILS:** Describe the failure painfully. Do not give them what they want. Make them suffer the consequences.

        === PLOT RULES ===
        1. LISTEN TO THE PLAYER: If the player writes “I'm moving on”, YOU MUST change the scene.

        === ATMOSPHERE AND COMPLEXITY (GRIMDARK) ===
        1. **Mortality:** Everyone dies in this world. The player does NOT have “plot armor.”
        2. **Cruelty:** Describe the world realistically. Dirt, blood, betrayal, injustice.
        3. **Consequences:** Every action has a price. Victory is never pure.

        === DIALOGUE RULES (PING-PONG MODE) — CRITICAL! ===
        1. If the player starts a conversation:
           - Write ONLY THE FIRST REACTION or RESPONSE of the NPC.
           - NEVER summarize the conversation.
        2. Speed: One player turn = One NPC line.

        === SYSTEM VERDICT (MANDATORY TO FOLLOW) ===
        {mechanics_note}
        (IF RESULT is FAILURE -> Describe how the hero fails painfully. Add +1 to a tension clock.
         IF RESULT is SUCCESS -> Describe a triumph.)

        === PLOT MODE ===
        1. **Freedom of Action, but Realistic Consequences**
        2. **Story Magnet:** If the player's action is trivial, throw in an event that leads to the main story.
        3. **Continuity of the plot:** The world lives its own life. War is constantly looming.

        === FINAL (CRITICALLY IMPORTANT) ===
        Your response MUST end with a QUESTION that presents the player with a specific choice.

        === HISTORY OF EVENTS (PAST) ===
        {history_text}

        === CURRENT TURN (PRESENT) ===
        PLAYER SAYS/DOES: “{user_input}”

        === YOUR TASK (FUTURE) ===
        Write a creative continuation of the story, responding ONLY TO THE PLAYER'S CURRENT ACTION in Ukrainian.
        1. Describe the consequences of the action, keep it focused on the IMMEDIATE reaction of the world.
        2. End with a QUESTION or a DILEMMA to which the player must respond.
        3. Fill in the JSON TAGS strictly using the values from === TAGS GUIDE ===.

        RESPONSE FORMAT (JSON ONLY):
        {{
            "internal_monologue": "Think here first. Analyze the mechanic verdict, clocks, and events. Example: 'Player failed combat. I will use dmg_medium and add +1 to tension clock.'",
            "story": "Your story text here (Ukrainian)...",
            "updates": {{ 
                 "time_passed": "How much time passed",
                 "health_impact": "players health impact",
                 "gold_impact": "players gold change",
                 "inventory_new": [new inventory item],
                 "inventory_lost": [lost invertory item],
                 "clocks_impact": {{"Clock Name": 1}},
                 "npc_updates": [
                    {{
                        "Name": "Exact NPC Name",
                        "Goal": "New Goal",
                        "Secret": "New Secret",
                        "Relation_Player": "New Attitude",
                        "Status": "Active/Dead/Injured"
                    }}
                 ]
            }}
        }}
        \n\nВАЖЛИВО: Відповідай ТІЛЬКИ JSON.
        """

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        print(f"\n[AI RAW TEXT]:\n{raw_text}\n") # логи, прибрати після розробки
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
            fix_resp = model.generate_content(
                f"Ти GM. Гравець: {user_input}. Опиши наслідки художньо. Закінчи питанням.")
            story = fix_resp.text

        duration = time.time() - t_start
        debug_log += f"\n⏱️ [MAIN AI] зайняло: {duration:.2f}s"
        timing_details.append(f"✍️ Story: {duration:.2f}s")

        # === ЕТАП 4: ЗАСТОСУВАННЯ НАСЛІДКІВ ТА ЗБЕРЕЖЕННЯ ===
        t_start = time.time()

        impacts = ai_data.get("updates", {})
        npc_changes = impacts.get("npc_updates", [])
        if not npc_changes: npc_changes = ai_data.get("npc_updates", [])

        if extra_updates:
            impacts["skill_impact"] = extra_updates.get("skill_impact")
            if "time_passed" in extra_updates: impacts["time_passed"] = "days"

        profile, logs = apply_system_impacts(profile, impacts)

        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"

        new_location = profile.get("Поточне місцезнаходження", "")
        if old_location != new_location and len(new_location) > 3:
            print(f"✈️ TRAVEL: {old_location} -> {new_location}")
            current_char_name = profile.get("Ім'я", "")

            def travel_population_task(new_loc, char_name):
                try:
                    situation = model_worker.generate_content(
                        f'Describe atmosphere in "{new_loc}" (Year 298) in 1 sentence.').text.strip()
                except:
                    situation = "A tense day in Westeros."
                populate_contextual_npcs(new_loc, situation, excluded_name=char_name)

            Thread(target=travel_population_task, args=(new_location, current_char_name)).start()

        # Фонове збереження
        def background_task(chat_id_arg, user_id_arg, profile_arg, char_name_arg, input_arg, story_arg,
                            npc_changes_arg):
            try:
                save_user_data(user_id_arg, profile_arg, char_name_arg)
                if npc_changes_arg: update_npcs_in_db(npc_changes_arg)

                short_hist_turn = summarize_turn(story_arg)
                short_user_inp = summarize_turn(input_arg)

                if chat_id_arg in user_sessions:
                    hist = user_sessions[chat_id_arg].get('history', [])
                    hist.append({"role": "User", "content": short_user_inp})
                    hist.append({"role": "GM", "content": short_hist_turn})

                    # === CONTEXT FLUSHING (Скидання пам'яті) ===
                    # Якщо історія стає занадто довгою (наприклад, > 20 реплік)
                    if len(hist) > 20:
                        # Беремо всі повідомлення як один текст
                        history_to_compress = "\n".join([f"{m['role']}: {m['content']}" for m in hist])

                        # Просимо Worker AI зробити один абзац summary
                        summary_prompt = f"Summarize this RPG history into one short paragraph (Ukrainian). Focus on the current location, main objective, and immediate situation:\n{history_to_compress}"
                        try:
                            summary_resp = model_worker.generate_content(summary_prompt).text.strip()
                            # ОЧИЩАЄМО історію і залишаємо ТІЛЬКИ summary
                            user_sessions[chat_id_arg]['history'] = [
                                {"role": "SYSTEM", "content": f"PREVIOUS EVENTS SUMMARY: {summary_resp}"}]
                            print("🧹 [CONTEXT FLUSH] Пам'ять успішно стиснуто!")
                        except Exception as e:
                            print(f"⚠️ Помилка стиснення: {e}")
                            user_sessions[chat_id_arg]['history'] = hist[-20:]  # Fallback, якщо ШІ впав
                    else:
                        user_sessions[chat_id_arg]['history'] = hist
            except Exception as exept:
                print(f"❌ [BG ERROR] {exept}")

        Thread(target=background_task,
               args=(chat_id, user_id, profile, profile.get("Ім'я"), user_input, story, npc_changes)).start()

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
        import traceback
        traceback.print_exc()
        return f"Щось пішло не так... (Помилка: {str(e)})"