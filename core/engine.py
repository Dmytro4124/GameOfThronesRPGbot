# core/engine.py
import time
import json
import re
from threading import Thread

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
    # === ЕТАП 2: МЕХАНІКА ТА ПЕРЕВІРКА НАВИЧОК ===
    t_start = time.time()

    # 1. Перевіряємо жорстко задані механіки (Тренування)
    training_result = process_training_request(user_input, profile)
    mechanics_verdict = ""
    mechanical_updates = {}
    logs = []

    if training_result:
        if "error" in training_result:
            # Тренування провалено (наприклад, мало золота)
            mechanics_verdict = f"SYSTEM: Player tried to train but FAILED. Reason: {training_result['error']}. Describe their disappointment."
        else:
            # Тренування успішне
            mechanics_verdict = training_result['story_prompt']

            # Переводимо дні у хвилини (1 день = 1440 хвилин)
            days_spent = training_result['cost_time']
            cost_gold = int(training_result['cost_gold'])

            # Формуємо словник оновлень за новими правилами Worker'а
            mechanical_updates = {
                "minutes_passed": days_spent * 1440,
                "energy_impact": "spend_large",  # Тренування виснажує
                "skill_impact": {training_result['skill']: training_result['stat_gain']}
            }

            # Віднімаємо золото вручну (оскільки apply_system_impacts для gold_impact використовує рендомні діапазони)
            current_gold = safe_int(profile.get("Особисте Золото", 0))
            profile["Особисте Золото"] = max(0, current_gold - cost_gold)
            logs.append(f"💰 Золото: -{cost_gold} (Оплата тренування)")

    else:
        # 2. Якщо це не тренування — віддаємо Worker AI
        mechanics_verdict, mechanical_updates = resolve_action_mechanics(user_input, profile)

    # 3. ОДРАЗУ застосовуємо всі наслідки до профілю (час, здоров'я, навички, годинники, інвентар)
    profile, impact_logs = apply_system_impacts(profile, mechanical_updates)
    logs.extend(impact_logs)

    duration = time.time() - t_start
    debug_log += f"\n⏱️ [WORKER AI / MECHANICS] зайняло: {duration:.2f}s"
    timing_details.append(f"🤖 Mechanics: {duration:.2f}s")

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
        
        === IN-GAME TIME & ENVIRONMENT ===
        CURRENT TIME: {current_time_str}
        CURRENT LOCATION: {curr_loc}
        (GM INSTRUCTION: The scene takes place STRICTLY in {curr_loc}. Do not change the location or teleport the player unless they explicitly state they are traveling elsewhere. Respect the time of day: night means darkness and sleeping NPCs.)
        
        === SYSTEM VERDICT (MANDATORY TO FOLLOW) ===
        {mechanics_verdict}
        (IF RESULT is FAILURE -> Describe how the hero fails painfully. Add +1 to a tension clock.
         IF RESULT is SUCCESS -> Describe a triumph.)
        
        === NPC INTERACTION RULES (CRITICAL) ===
        1. **PRIORITY:** If the player says "I look around" or "I talk to the merchant", YOU MUST check the "VISIBLE NPC ROSTER" above.
           - If there is a merchant named 'Lotho' in the list -> Use Lotho.
           - DO NOT invent a new 'Generic Merchant' if a specific one exists.
        2. **SECRETS:** Use the [SECRET] field to determine how the NPC acts, creates tension, or lies.
           - Example: If Secret is "Is a spy", the NPC should ask too many questions, but NOT say "Hello, I am a spy."
        3. **RELATIONS:** Use 'Attitude to Player'. If it says 'Suspicious', the NPC will not be helpful without a bribe or persuasion.
        4. **STRICT ROSTER RULE (NO HALLUCINATIONS):** It is STRICTLY FORBIDDEN to invent characters on the fly. You must take them exclusively from those already generated in the world database. In the npc_updates block, the Name field must contain an exact, unmodified string from the database (character for character). It is forbidden to translate, abbreviate, or combine names. If a character is not in the current context of the database, do not mention it in updates.
        === NPC INTERACTION RULES (CRITICAL) ===
        1. **PRIORITY:** If the player says "I look around"...
        2. **SECRETS:** Use the [SECRET] field...
        3. **RELATIONS:** Use 'Attitude to Player'...
        4. **STRICT ROSTER RULE...
        5. **FULL NPC CONTEXT (CRITICAL):** Synthesize ALL provided data from the roster for every NPC. You MUST use these exact tags to guide their behavior:
           - **Visual:** Use for their physical description, current injuries, or clothing.
           - **Personality:** Defines their tone, vocabulary, and decision-making style.
           - **Goal:** Drives their immediate actions in this turn.
           - **Attitude to Player:** Dictates their base reaction to the hero.
           - **Memory Anchor (WHY they feel this way):** Use as the context/reason for their attitude towards the player.
           - **Attitude to other NPC:** Defines alliances, rivalries, and dialogue with others in the room.
           - **[SECRET/GM ONLY]:** Guides their hidden motives. NEVER say this out loud, show it through subtle actions.

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
        
        8. NO NUMBER DROPPING: NEVER mention specific numeric values for gold, health, skills, or energy in the story text. (e.g., Say "You found some coins", NOT "You found 50 gold"). The System Engine handles exact numbers.
       

        === DOCTRINE OF RESISTANCE (CRITICAL RULES) ===
        1. **NO "YES-MAN":** Do not agree with the player just to move the plot, the world resists the player. But do not artificially complicate simple tasks (like buying bread).
        2. **NPC POWER & RETALIATION (CRITICAL):** Powerful NPCs (like Illyrio Mopatis, Tywin Lannister, Khal Drogo) are NOT passive props. 
           - If the player insults them, breaks deals, acts arrogantly, or attacks their servants, they MUST retaliate immediately and harshly. 
           - They will use guards, cut off funds, use political leverage, or make direct threats. 
           - They DO NOT "just smile", "clench their jaw", or passively accept disrespect. They strike back.
        3. **ALLOW DOWNTIME:** The player MUST have time to rest, train, drink in a tavern, or think. Do NOT constantly throw new events, ambushes, or dramatic twists at them. It is perfectly fine for a scene to be quiet and atmospheric.
        4. **STOP THE ESCALATION:** If a fight, argument, or scene naturally resolves, LET IT END. Do not immediately spawn a new crisis, a new enemy, or a sudden plot twist. Return the world to a calm "resting state".
        5. **PLAYER DRIVEN:** Let the player drive the story forward. Only inject sudden external events if a scheduled 'WORLD EVENT' triggers.
        6. **CONSEQUENCES:** If the player commits a crime in a Free City, the Magisters WILL send the City Watch.
        7. **PAUSE RULE:** Do NOT describe a long chain of actions for NPCs.
        8. **TRAUMA SIMULATION & EMOTIONAL INERTIA (CRITICAL):** Victims of long-term psychological or physical abuse (e.g., Daenerys Targaryen) DO NOT instantly forgive or change their baseline attitude.
           - Sudden kindness from an abuser causes PANIC, SUSPICION, or DISSOCIATION, not trust. The victim expects a trap or the "dragon to wake".
           - IT IS STRICTLY FORBIDDEN to describe an abuse victim as "intrigued", "curious", "blushing", or "warming up" to their abuser after a single kind action.
           - 'Relation_Player' shifts take IN-GAME WEEKS OR MONTH. You MUST maintain their 'Relation_Player' as Fearful/Suspicious regardless of the player's current sweet words.
        9.**IF THE PLAYER FAILS:** Describe the failure painfully. Do not give them what they want. Make them suffer the consequences.

        === ATMOSPHERE AND COMPLEXITY (GRIMDARK) ===
        1. **Mortality:** Everyone dies in this world. The player does NOT have “plot armor.”
        2. **Cruelty:** Describe the world realistically. Dirt, blood, betrayal, injustice.
        3. **Consequences:** Every action has a price. Victory is never pure.
        4. **Quiet Grimdark:** Grimdark does not mean constant combat. Sitting in a dark, leaking tavern eating stale bread while the rain pours is perfect Grimdark.

        === DIALOGUE RULES (PING-PONG MODE) — CRITICAL! ===
        1. If the player starts a conversation:
           - Write ONLY THE FIRST REACTION or RESPONSE of the NPC.
           - NEVER summarize the conversation.
        2. Speed: One player turn = One NPC line.
        
        === CLOCKS RULES ===
            - **Escalation:** If `Scene_Tension` reaches 3, you MUST immediately alter the scene's state (e.g., the NPC attacks, issues a hard ultimatum, or leaves). Stop looping the conversation.
            - **Reset tension:** If `Scene_Tension` resets or equals 0 scene must be quiet and calm. No threats or battles.   

        === PLOT MODE ===
        1. **Freedom of Action, but Realistic Consequences**
        2. **Story Magnet:** If the player's action is trivial, throw in an event that leads to the main story.
        3. **Continuity of the plot:** The world lives its own life. War is constantly looming.

        === HISTORY OF EVENTS (PAST) ===
        {history_text}

        === CURRENT TURN (PRESENT) ===
        PLAYER SAYS/DOES: “{user_input}”

        === YOUR TASK (FUTURE) ===
        Write a creative continuation of the story, responding ONLY TO THE PLAYER'S CURRENT ACTION in Ukrainian.
        1. Describe the consequences of the action, keeping it focused on the IMMEDIATE reaction of the world.
        2. Adhere STRICTLY to the System Verdict.
        3. End your text organically. You can ask "Що ви робите далі?" (What do you do next?) or simply describe the environment waiting for the player's reaction. DO NOT force a high-stakes "dilemma" at the end of every turn.

        === JSON GENERATION RULES (CRITICAL) ===
        If a specific field (like Character, Goal, Secrets) has NOT changed, you MUST set its value to an empty string "". 
        IT IS STRICTLY FORBIDDEN to write words like "no change", "same", or "без змін". Only use "".

        RESPONSE FORMAT (JSON ONLY):
        {{
            "internal_monologue": "Think here first. Analyze the NPC's Personality, Goal, and [SECRET] before writing their reaction.",
            "story": "Your story text here (Ukrainian)...",
            "npc_updates": [
                {{
                    "Name": "Exact NPC Name",
                    "Description": "New description OR \"\" if no change.",
                    "Character": "New personality OR \"\" if no change.",
                    "Goal": "New goal OR \"\" if no change.",
                    "Relation_Player": "New attitude OR \"\" if no change.",
                    "Memory_Anchor": "Short explanation of WHY their attitude changed OR \"\" if no change.",
                    "Relation_NPCs": "New attitude to others OR \"\" if no change.",
                    "Secrets": "New secret OR \"\" if no change.",
                    "Status": "Active/Dead/Injured"
                }}
            ]
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

        # Дістаємо тільки оновлення NPC (бо іншого Main-модель більше не генерує)
        npc_changes = ai_data.get("npc_updates", [])

        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"

        if "skill_used" in mechanical_updates and mechanical_updates["skill_used"] != "None":
            skill = mechanical_updates["skill_used"]
            total = mechanical_updates.get("total_score", 0)
            diff = mechanical_updates.get("difficulty", 0)
            outcome = mechanical_updates.get("outcome", "UNKNOWN")

            icon = "✅" if outcome == "SUCCESS" else "❌"
            dice_log = f"{icon} Перевірка: {skill} | Результат: {total} (Складність: {diff})"

            # Вставляємо кидок кубика на початок списку логів
            logs.insert(0, dice_log)

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
                        except Exception as ex:
                            print(f"⚠️ Помилка стиснення: {ex}")
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
        error_msg = str(e)
        if "429" in error_msg or "Quota" in error_msg:
            return "⏳ *Старі Боги потребують часу на роздуми...*\n(Перевищено ліміт запитів до серверів. Будь ласка, зачекайте 20-30 секунд і повторіть дію)."
        else:
            import traceback
            traceback.print_exc()
            return f"📜 *Ворон згубив вашого листа...* Щось пішло не так. (Помилка: {error_msg})"