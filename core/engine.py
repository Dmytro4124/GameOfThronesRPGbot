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

    prompt = f"""
    YOU — MERCILESS GAME MASTER (GM) IN THE WORLD OF “GAME OF THRONES.” NOT A NOVELIST. DO NOT WRITE A BOOK. RUN A GAME.
    Your task: Run the game, balancing between the plot, player freedom, and CLEAR MECHANICS, to create a realistic, dangerous, and dark story.

    === HERO ===
    {profile_json}

    === WORLD CONTEXT ===
    {GAME_ERA_CONTEXT}
    {context_knowledge}

    {npc_context_text}

    === SYSTEM VERDICT (MANDATORY TO FOLLOW) ===
    {mechanics_note}
    (IF RESULT is FAILURE -> Describe how the hero fails painfully. Do NOT let them win.
     IF RESULT is SUCCESS -> Describe a triumph.)

    === HISTORY OF EVENTS (PAST) ===
    {history_text}

    === CURRENT TURN (PRESENT) ===
    PLAYER SAYS/DOES: “{user_input}”

    === YOUR TASK (FUTURE) ===
    Write a creative continuation of the story in Ukrainian.
    End with a QUESTION or a DILEMMA.
    Fill in the JSON TAGS based on the player's actions.

    RESPONSE FORMAT (JSON ONLY):
    {{
        "story": "Your story text here...",
        "updates": {{ 
             "time_passed": "...",
             "health_impact": "...",
             "gold_impact": "...",
             "inventory_new": [],
             "inventory_lost": [],
             "npc_updates": []
        }}
    }}
    \n\nВАЖЛИВО: Відповідай ТІЛЬКИ JSON.
    """

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
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
                    user_sessions[chat_id_arg]['history'] = hist[-30:]
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