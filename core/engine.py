# core/engine.py
import time
import json
import re
import asyncio
import logging

logger = logging.getLogger(__name__)

from core.ai_client import model_worker, model_gm_logic, model_narrator, clean_and_parse_json, clear_thoughts, record_thought
from config import MODEL_NARRATOR_NAME
from core.mechanics import apply_system_impacts, process_training_request, safe_int, resolve_action_mechanics, validate_action
from core.prompts import (
    GAME_ERA_CONTEXT, build_summarize_turn_prompt, build_summarize_full_turn_prompt,
    build_narrator_prompt, build_gm_logic_prompt, build_history_summary_prompt,
)
from core.world_constants import VALID_LOCATIONS_ORDERED, VALID_REGIONS_ORDERED, TRAVEL_LOCATION, get_region_for_location, get_locations_for_region, LOCATION_DESCRIPTIONS, format_scenes_for_prompt
from core.world import populate_contextual_npcs
from database.operations import (
    get_user_data, save_user_data, get_relevant_context,
    get_location_npcs, update_npcs_in_db,
    update_npc_reputation, append_memory_anchor, get_dead_npc_names
)

user_sessions = {}
_atmosphere_cache = {}  # P11: кеш атмосфери локацій {location: atmosphere_str}


def _sanitize_story(text: str) -> str:
    """Видаляє технічні рядки кидків, що випадково потрапили в наратив."""
    # "Кидок 47 + Навичка 30 = 77 (Ціль: 100) -> ПРОВАЛ"
    text = re.sub(r'Кидок\s+\d+\s*\+\s*Навичка\s+\d+\s*=\s*\d+\s*\(Ціль:\s*\d+\)\s*->\s*\S+', '', text)
    # Осиротілі числові патерни типу "HP: 80", "DC 60" — строго технічні формати
    text = re.sub(r"\b(HP|DC)\s*[:=]\s*\d+", '', text)
    text = re.sub(r"\bE\s*[:=]\s*\d+", '', text)
    # Технічні терміни годинників/механік
    text = re.sub(r'Scene_Tension', '', text)
    # A15 FIX: Безіменні "Лорд/Леді [Великий Дім]" → нейтральне звертання
    # Regex: ловить "Лорд Ланністер" але НЕ чіпає "Лорд Тайвін Ланністер" (є ім'я перед прізвищем)
    _great_houses_pattern = r'Ланністер|Старк|Таргарієн|Баратеон|Тірелл|Грейджой|Мартелл|Аррен|Таллі|Болтон'
    text = re.sub(
        rf'(?<![А-ЯІЇЄҐа-яіїєґ]\s)[Лл]орд(?:е|у|а|ом|ові)?\s+({_great_houses_pattern})\w*',
        'місцевий лорд',
        text
    )
    text = re.sub(
        rf'(?<![А-ЯІЇЄҐа-яіїєґ]\s)[Лл]еді\s+({_great_houses_pattern})\w*',
        'місцева леді',
        text
    )
    # Подвійні пробіли та порожні рядки
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def _build_impact_hints(impact_logs: list) -> str:
    """Перетворює технічні логи імпактів на наративні підказки для GM (без чисел)."""
    hints = []
    for entry in impact_logs:
        if "⚡ Енергія" in entry:
            m_new = re.search(r'-> (\d+)', entry)
            m_delta = re.search(r'\(([+-]?\d+)\)', entry)
            new_val = int(m_new.group(1)) if m_new else 1000
            delta = int(m_delta.group(1)) if m_delta else 0

            if delta >= 1:
                hints.append("ВІДНОВЛЕННЯ ЕНЕРГІЇ: Персонаж відновився. GM може тонко згадати відчуття свіжості або прилив сил.")
            elif new_val <= 290 and delta < 0:
                hints.append("ВИСНАЖЕННЯ (КРИТИЧНЕ): Персонаж тяжко виснажений. GM ПОВИНЕН описати видиму боротьбу: тремтячі руки, переривчасте дихання, нетверда хода.")
            elif new_val <= 590 and delta <= -10:
                hints.append("ВИСНАЖЕННЯ: Персонаж помітно втомлений. GM ПОВИНЕН вплести втому в наратив (наприклад: 'ноги стають важкими', 'хвиля знесилення накриває').")
            # 590-1000: без підказки — персонаж у нормі

        elif "❤️ Здоров'я" in entry:
            m_vals = re.search(r'(\d+) -> (\d+)', entry)
            if not m_vals:
                continue
            old_val = int(m_vals.group(1))
            new_val = int(m_vals.group(2))
            delta = new_val - old_val

            if delta >= 1:
                hints.append("ЗЦІЛЕННЯ: Персонаж підлікувався. GM може згадати полегшення або фізичне відновлення.")
            elif delta < 0:
                if new_val <= 39:
                    hints.append("ЗДОРОВ'Я КРИТИЧНЕ: Персонаж тяжко поранений. GM ПОВИНЕН передати терміновість і смертельну небезпеку (наприклад: 'кров ллється потоком', 'кожен подих — як останній', 'смерть витає поруч').")
                elif new_val <= 69:
                    hints.append("СЕРЙОЗНА РАНА: Персонаж значно поранений. GM ПОВИНЕН чітко описати рану (наприклад: 'кров тече', 'гострий біль пронизує', 'хитається на ногах').")
                else:
                    hints.append("ЛЕГКА РАНА: Персонаж отримав поверхневий удар. GM МОЖЕ коротко згадати (наприклад: 'болісне зморщення', 'подряпина', 'жалючий біль від удару').")

    return "\n".join(hints)


async def summarize_turn(gm_response):
    clean_story = gm_response.split("📊")[0][:800]
    prompt = build_summarize_turn_prompt(clean_story)
    try:
        def _sync_gen():
            return model_worker.generate_content(prompt)

        resp = await asyncio.to_thread(_sync_gen)
        return resp.text.strip()
    except:
        return f"Гравець діє."


async def summarize_full_turn(user_input, gm_response):
    """Об'єднана сумаризація: дія гравця + результат GM → одне речення."""
    prompt = build_summarize_full_turn_prompt(user_input, gm_response)
    try:
        def _sync_gen():
            return model_worker.generate_content(prompt)

        resp = await asyncio.to_thread(_sync_gen)
        return resp.text.strip()
    except Exception:
        return f"{user_input[:100]} → ..."


def _build_narrator_prompt(user_input, director_notes, npc_context_text,
                           player_name, player_house, current_scene,
                           current_location, impact_narrative_hints,
                           puppet_mode=False, recent_history_text=None,
                           erotic_mode=False, active_roster=None, dead_npcs=None,
                           departing_roster_text="", arriving_roster_text=""):
    return build_narrator_prompt(
        user_input=user_input,
        director_notes=director_notes,
        npc_context_text=npc_context_text,
        player_name=player_name,
        player_house=player_house,
        current_scene=current_scene,
        current_location=current_location,
        impact_narrative_hints=impact_narrative_hints,
        puppet_mode=puppet_mode,
        recent_history_text=recent_history_text,
        erotic_mode=erotic_mode,
        active_roster=active_roster,
        dead_npcs=dead_npcs,
        departing_roster_text=departing_roster_text,
        arriving_roster_text=arriving_roster_text,
    )


async def process_game_turn(chat_id, user_input, progress_callback=None, narrator_queue=None):
    """
    Головний ігровий цикл.
    progress_callback: async callable(str) — оновлює статус для гравця.
    narrator_queue: asyncio.Queue — якщо передано, стрімить narrator-текст чанками замість блокуючого виклику.
    """
    clear_thoughts()
    debug_log = ""
    global_start = time.time()
    debug_log += f"🚀 [START] Хід гравця {chat_id}..."

    user_id = chat_id
    timing_details = []

    t_start = time.time()

    profile, row_id = await get_user_data(user_id)
    if not profile: return "❌ Профіль не знайдено.", []

    session = user_sessions.get(chat_id, {})
    if session.get("state") == "INITIALIZING":
        return "⏳ *Світ відроджується... Зачекайте кілька секунд.*", []
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"state": "GAME_ACTIVE", "history": []}
    history = session.get('history', [])

    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-40:]])

    # === ОНОВЛЕНО: Читаємо локацію та сцену ===
    old_location = profile.get("Поточне місцезнаходження", "Невідомо")
    old_scene = profile.get("Поточна сцена", "Невідомо")

    # === P1 FIX: Фільтрований стан для GM (без числових статів) ===
    hp = safe_int(profile.get("Здоров'я", 100), 100)
    energy = safe_int(profile.get("Енергія", 1000), 1000)
    gold = safe_int(profile.get("Особисте Золото", 0), 0)

    def _qualitative(val, thresholds):
        """Перетворює число на якісну мітку."""
        for threshold, label in thresholds:
            if val <= threshold:
                return label
        return thresholds[-1][1]

    hp_label = _qualitative(hp, [(0, "dead"), (29, "critically wounded — on the brink of death"), (49, "seriously wounded"), (69, "wounded"), (89, "lightly bruised"), (100, "healthy")])
    energy_label = _qualitative(energy, [(0, "collapsed from exhaustion"), (200, "barely standing, trembling with fatigue"), (400, "visibly exhausted"), (600, "somewhat tired"), (800, "energetic"), (1000, "full of vigour")])
    gold_label = _qualitative(gold, [(0, "penniless"), (20, "a few coins"), (100, "modest purse"), (500, "comfortable wealth"), (2000, "wealthy"), (100000, "rich as a lord")])

    player_state_for_gm = {
        "Name": profile.get("Ім'я", ""),
        "House": profile.get("Дім", ""),
        "Title": profile.get("Титул", ""),
        "Physical condition": hp_label,
        "Fatigue level": energy_label,
        "Wealth": gold_label,
        "Weapon": profile.get("Зброя", "None"),
        "Armor": profile.get("Броня", "None"),
        "Transport": profile.get("Транспорт", "None"),
        "Inventory": profile.get("Інвентар", "Пусто"),
        "Worldview": profile.get("Світогляд", ""),
        "Traits": profile.get("Риси", ""),
        "Flaws": profile.get("Вади", ""),
        "Enemies": profile.get("Вороги", ""),
        "Friends": profile.get("Друзі", ""),
    }
    profile_json = json.dumps(player_state_for_gm, ensure_ascii=False, indent=2)

    curr_loc = profile.get("Поточне місцезнаходження", "Невідомо")
    curr_scene = profile.get("Поточна сцена", "Невідомо")
    curr_region = profile.get("Регіон", get_region_for_location(curr_loc) or "Невідомо")

    # === ОНОВЛЕНО: 3-рівнева фільтрація NPC (Регіон → Локація → Сцена) ===
    context_knowledge = await get_relevant_context(user_input, curr_loc)
    npc_context_text, legal_npc_names, npc_reputation_context = get_location_npcs(
        curr_loc, curr_scene, current_region=curr_region
    )

    # === B+C: відстежуємо зникнення NPC між ходами (розділяємо мертвих та просто відсутніх) ===
    _dead_names_cache = get_dead_npc_names()
    prev_legal_npc_names = user_sessions.get(chat_id, {}).get("prev_legal_npc_names", [])
    absent_npcs_raw = [n for n in prev_legal_npc_names if n not in set(legal_npc_names)]
    dead_npcs = [n for n in absent_npcs_raw if n in _dead_names_cache]
    absent_npcs = [n for n in absent_npcs_raw if n not in _dead_names_cache]
    # Мертві ніколи не повернуться — не зберігаємо їх у prev_legal_npc_names
    user_sessions.setdefault(chat_id, {})["prev_legal_npc_names"] = [n for n in legal_npc_names if n not in _dead_names_cache]
    if dead_npcs or absent_npcs:
        print(f"☠️ [B+C] Мертві: {dead_npcs} | Зниклі живі: {absent_npcs} | Активний: {legal_npc_names}")
    else:
        print(f"👁️ [B+C] Ростер стабільний: {legal_npc_names}")

    duration = time.time() - t_start
    timing_details.append(f"📚 Data: {duration:.2f}s")

    t_start = time.time()
    logs = []

    # === ЦЕНЗОР: Перевірка дії до будь-якої механіки ===
    if progress_callback:
        await progress_callback("🎲 Оцінюємо дію...")
    is_valid, refusal_reason = await validate_action(user_input, profile)
    if not is_valid:
        return refusal_reason + "\n\n📊 🛑 Дію заблоковано Цензором.", []

    if progress_callback:
        await progress_callback("⚔️ Кидаємо кубики...")
    last_turn = history[-1]["content"] if history else ""
    mechanics_verdict, mechanical_updates = await resolve_action_mechanics(
        user_input, profile, npc_reputation_context,
        current_scene=curr_scene, npc_names=legal_npc_names, last_turn_summary=last_turn,
        user_id=chat_id, current_location=curr_loc
    )

    # === БЛОКУВАННЯ РЕПУТАЦІЄЮ: NPC відмовляє через кровну ворожнечу ===
    if mechanical_updates.get("reputation_block"):
        npc_name = mechanical_updates.get("reputation_target_npc", "NPC")
        logs.append(f"🚫 Репутація: {npc_name} — соціальна дія заблокована (кровна ворожнеча)")
        change_log = "\n\n📊 " + " | ".join(logs) if logs else ""
        # Передаємо GM для генерації художньої відмови
        mechanics_verdict_for_block = mechanics_verdict

    if mechanical_updates.get("action_type") == "training":
        training_result = await process_training_request(user_input, profile, current_scene=curr_scene)

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
                current_energy = safe_int(profile.get("Енергія", 1000))
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

    # === REFRESH: Перечитуємо loc/scene/region якщо apply_system_impacts їх змінила ===
    # Зберігаємо "ростер відправлення" ПЕРЕД rebuild (для dual roster промптів)
    departing_npc_context = npc_context_text
    departing_npc_names = list(legal_npc_names)
    arriving_npc_context = ""
    arriving_npc_names = []
    location_or_scene_changed = False

    _post_loc   = profile.get("Поточне місцезнаходження", curr_loc)
    _post_scene = profile.get("Поточна сцена", curr_scene)
    _post_region = profile.get("Регіон", get_region_for_location(_post_loc) or curr_region)
    if _post_scene != curr_scene or _post_loc != curr_loc:
        location_or_scene_changed = True
        curr_loc    = _post_loc
        curr_scene  = _post_scene
        curr_region = _post_region
        npc_context_text, legal_npc_names, npc_reputation_context = get_location_npcs(
            curr_loc, curr_scene, current_region=curr_region
        )
        # "Ростер прибуття" = NPC нової локації/сцени
        arriving_npc_context = npc_context_text
        arriving_npc_names = list(legal_npc_names)
        # Перераховуємо dead/absent з оновленим Ростером
        _dnames_post = get_dead_npc_names()
        _absent_raw_post = [n for n in prev_legal_npc_names if n not in set(legal_npc_names)]
        dead_npcs   = [n for n in _absent_raw_post if n in _dnames_post]
        absent_npcs = [n for n in _absent_raw_post if n not in _dnames_post]
        user_sessions.setdefault(chat_id, {})["prev_legal_npc_names"] = [
            n for n in legal_npc_names if n not in _dnames_post
        ]
        print(f"🔄 [ROSTER REBUILD] {old_scene}/{old_location} → {curr_scene}/{curr_loc} | Новий Ростер: {legal_npc_names}")
        print(f"🔄 [DUAL ROSTER] Departing: {len(departing_npc_names)} NPC {departing_npc_names} | Arriving: {len(arriving_npc_names)} NPC {arriving_npc_names}")
    else:
        # Нема переміщення — departing не потрібний
        departing_npc_context = ""
        departing_npc_names = []

    # === BURST CHECK: годинник щойно досяг максимуму ===
    burst_clock = profile.pop("_burst_this_turn", None)
    is_exhaustion_collapse = (
        mechanical_updates.get("energy_impact") == "sleep"
        and mechanical_updates.get("outcome") == "CRITICAL FAILURE"
        and mechanical_updates.get("minutes_passed") == 480
    )
    burst_injection = ""
    if burst_clock == "Scene_Tension" and not is_exhaustion_collapse:
        burst_injection = (
            "\n[SYSTEM INTERRUPT — VIOLENCE ERUPTS: "
            "Scene_Tension досягла максимуму. АБСОЛЮТНИЙ OVERRIDE. "
            "NPC негайно починає атаку, спалахує бійка або прибуває варта — "
            "щось насильницьке та неминуче відбувається ПРЯМО ЗАРАЗ. "
            "Дія гравця перервана цією подією. Гравець НЕ може уникнути її через розмову цього ходу.]\n"
        )
        logs.append("💥 Напруга вибухнула! Сцена переходить до відкритого насильства.")

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

    # Якісна мітка напруги сцени (без технічного терміну)
    _tension_raw = profile.get("Годинники", {}).get("Scene_Tension", "0/4")
    _tension_val = str(_tension_raw).split("/")[0] if isinstance(_tension_raw, str) else str(_tension_raw)
    _tension_labels = {"0": "спокійно", "1": "легка напруга", "2": "напружено", "3": "небезпечно — на межі конфлікту", "4": "на межі вибуху — насильство неминуче"}
    _tension_label = _tension_labels.get(_tension_val, "невідомо")
    _action_slots_map = {
        "0": ["ДОСЛІДИТИ", "ПОГОВОРИТИ", "ПІДГОТУВАТИСЬ", "РУШИТИ ДАЛІ"],
        "1": ["РОЗПИТАТИ", "ПОГРОЖУВАТИ", "СЛІДКУВАТИ", "ЗАЧЕКАТИ"],
        "2": ["ТИСНУТИ", "МАНІПУЛЮВАТИ", "ОТОЧИТИ", "ВІДСТУПИТИ"],
        "3": ["АТАКУВАТИ", "ТОРГУВАТИСЬ", "ВТЕКТИ", "ПЕРЕХИТРИТИ"],
        "4": ["АТАКУВАТИ", "ЗАХИСТИТИСЬ", "ВТЕКТИ", "ДИКИЙ GAMBLE"],
    }
    _action_slots = _action_slots_map.get(_tension_val, _action_slots_map["2"])
    _valid_locs_str = ", ".join(f'"{loc}"' for loc in VALID_LOCATIONS_ORDERED)
    _valid_regions_str = ", ".join(f'"{r}"' for r in VALID_REGIONS_ORDERED)
    _region_locs = get_locations_for_region(curr_region)
    _region_locs_str = ", ".join(f'"{loc}"' for loc in _region_locs) if _region_locs else _valid_locs_str
    _curr_loc_desc = LOCATION_DESCRIPTIONS.get(curr_loc, "")
    _loc_hint = f"\n    ОПИС ПОТОЧНОЇ ЛОКАЦІЇ: {_curr_loc_desc}" if _curr_loc_desc else ""

    # ============ GM_LOGIC PROMPT (JSON стану світу, без story) ============
    from config import PUPPET_USERS, EROTIC_USERS
    _puppet_mode = chat_id in PUPPET_USERS
    _erotic_mode = chat_id in EROTIC_USERS
    _scenes_block_str = format_scenes_for_prompt(curr_loc)
    gm_logic_prompt = build_gm_logic_prompt(
        hero_name=profile.get("Ім'я", "Невідомий"),
        hero_house=profile.get("Дім", "Невідомий"),
        profile_json=profile_json,
        context_knowledge=context_knowledge,
        event_injection=event_injection,
        burst_injection=burst_injection,
        current_time_str=current_time_str,
        curr_region=curr_region,
        curr_loc=curr_loc,
        is_traveling=(curr_loc == TRAVEL_LOCATION),
        loc_hint=_loc_hint,
        curr_scene=curr_scene,
        valid_locs_str=_valid_locs_str,
        valid_regions_str=_valid_regions_str,
        region_locs_str=_region_locs_str,
        npc_context_text=npc_context_text,
        tension_label=_tension_label,
        mechanics_verdict=mechanics_verdict,
        impact_narrative_hints=impact_narrative_hints,
        history_text=history_text,
        user_input=user_input,
        action_slots=_action_slots,
        puppet_mode=_puppet_mode,
        absent_npcs=absent_npcs,
        dead_npcs=dead_npcs,
        scenes_block_str=_scenes_block_str,
        departing_roster_text=departing_npc_context if location_or_scene_changed else "",
        arriving_roster_text=arriving_npc_context if location_or_scene_changed else "",
    )


    try:
        # ============ КРОК 1: GM_Logic (легка модель → JSON) ============
        if progress_callback:
            await progress_callback("🌍 Оновлюємо стан світу...")
        t_gm_logic = time.time()

        def _sync_gen_gm_logic():
            return model_gm_logic.generate_content(gm_logic_prompt)

        gm_logic_response = await asyncio.to_thread(_sync_gen_gm_logic)
        gm_logic_raw = gm_logic_response.text.strip()
        ai_data = clean_and_parse_json(gm_logic_raw)

        # GM_Logic fallback якщо JSON не парситься
        if not ai_data:
            ai_data = {"director_notes": ["Дія мала неоднозначний результат."],
                       "npc_updates": [], "suggested_actions": []}

        duration_gm_logic = time.time() - t_gm_logic
        timing_details.append(f"🧠 GM_Logic: {duration_gm_logic:.2f}s")

        # Зберігаємо ai_data в сесію для QA-валідації
        user_sessions[chat_id]['last_ai_data'] = ai_data

        # Витягуємо suggested_actions з GM_Logic
        raw_actions = ai_data.get("suggested_actions", [])
        button_texts = []
        intents_map = {}
        for item in raw_actions:
            if isinstance(item, dict):
                btn = str(item.get("button", "...")).strip()[:40]
                intent = str(item.get("intent", btn)).strip()
            else:
                btn = str(item).strip()[:40]
                intent = btn
            button_texts.append(btn)
            intents_map[btn] = intent
        if len(button_texts) < 4:
            button_texts = (button_texts + ["..."] * 4)[:4]
        user_sessions.setdefault(chat_id, {})["action_intents"] = intents_map
        suggested_actions = button_texts

        # ============ КРОК 2: Narrator (важка модель → художній текст) ============
        if progress_callback:
            # Показуємо результати кубиків одразу, поки Narrator думає
            preview = "✍️ Пишемо історію...\n\n"
            if logs:
                preview += "📊 " + " | ".join(logs)
            await progress_callback(preview)
        t_narrator = time.time()
        director_notes = ai_data.get("director_notes", [])
        if not director_notes:
            director_notes = ["Щось сталося."]

        # Fix 1: Санітайзер director_notes — видаляє згадки мертвих NPC до Narrator
        if dead_npcs:
            _dead_lower = {n.lower(): n for n in dead_npcs}
            _correction_added = set()
            _clean_notes = []
            for note in director_notes:
                note_lower = note.lower()
                _found_dead = next((orig for low, orig in _dead_lower.items() if low in note_lower), None)
                if _found_dead:
                    if _found_dead not in _correction_added:
                        _clean_notes.append(f"КОРЕКЦІЯ: {_found_dead} мертвий — відсутній у сцені. Не згадуй його дій.")
                        _correction_added.add(_found_dead)
                        print(f"🧹 [NOTES SANITIZED] Видалено згадку мертвого '{_found_dead}' з director_notes.")
                else:
                    _clean_notes.append(note)
            director_notes = _clean_notes

        _recent_hist_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in history[-10:]
        ) if history else ""

        # Fix 3: Анотація history — попереджає Narrator про мертвих NPC у свіжій пам'яті
        if dead_npcs:
            _dead_list_str = ", ".join(dead_npcs)
            _recent_hist_text = (_recent_hist_text or "") + (
                f"\n\n[СИСТЕМНА НОТАТКА ДЛЯ КОНТЕКСТУ: Такі NPC МЕРТВІ і НЕ існують у поточній реальності: "
                f"{_dead_list_str}. Все що ти читаєш про них в попередніх ходах — минуле.]"
            )
        narrator_prompt = _build_narrator_prompt(
            user_input=user_input,
            director_notes=director_notes,
            npc_context_text=npc_context_text,
            player_name=profile.get("Ім'я", "Невідомий"),
            player_house=profile.get("Дім", "Невідомий"),
            current_scene=curr_scene,
            current_location=curr_loc,
            impact_narrative_hints=impact_narrative_hints,
            puppet_mode=_puppet_mode,
            recent_history_text=_recent_hist_text,
            erotic_mode=_erotic_mode,
            active_roster=legal_npc_names,
            dead_npcs=dead_npcs,
            departing_roster_text=departing_npc_context if location_or_scene_changed else "",
            arriving_roster_text=arriving_npc_context if location_or_scene_changed else "",
        )

        if narrator_queue is not None:
            # === STREAMING MODE: стрімимо narrator-текст чанками через queue ===
            _loop = asyncio.get_event_loop()

            def _sync_stream_narrator():
                chunks = []
                thought_parts = []
                print("🔵 [STREAM] Narrator streaming started...")
                try:
                    for chunk in model_narrator.generate_content_stream(narrator_prompt):
                        try:
                            for part in chunk.candidates[0].content.parts:
                                if getattr(part, "thought", False):
                                    thought_parts.append(part.text or "")
                                elif part.text:
                                    chunks.append(part.text)
                                    _loop.call_soon_threadsafe(narrator_queue.put_nowait, part.text)
                                    print(f"🔵 [STREAM] Chunk #{len(chunks)}: {len(part.text)} chars")
                        except (AttributeError, IndexError):
                            text = chunk.text if chunk.text else ""
                            if text:
                                chunks.append(text)
                                _loop.call_soon_threadsafe(narrator_queue.put_nowait, text)
                                print(f"🔵 [STREAM] Chunk #{len(chunks)}: {len(text)} chars")
                        try:
                            fr = chunk.candidates[0].finish_reason if chunk.candidates else None
                            if fr and str(fr) not in ("FinishReason.STOP", "STOP", "1", "None"):
                                print(f"🔵 [STREAM] finish_reason={fr}")
                        except Exception:
                            pass
                except Exception as e:
                    print(f"🔴 [STREAM] Error during streaming: {e}")
                    return ""  # порожній → retry спрацює
                record_thought(MODEL_NARRATOR_NAME, "\n".join(thought_parts))
                print(f"🔵 [STREAM] Done. Total chunks: {len(chunks)}")
                return "".join(chunks)

            story = await asyncio.to_thread(_sync_stream_narrator)
            story = story.strip() if story else ""
            # None надсилається ПІСЛЯ перевірки на обрізання — див. нижче
        else:
            # === BLOCKING MODE: стандартний виклик ===
            def _sync_gen_narrator():
                return model_narrator.generate_content(narrator_prompt)

            narrator_response = await asyncio.to_thread(_sync_gen_narrator)
            story = narrator_response.text.strip() if narrator_response and narrator_response.text else ""

        # Retry якщо Narrator видав сміття, обрізаний текст або None
        _story_truncated = story and len(story) > 20 and not story.rstrip().endswith(('.', '!', '?', '…', '"', '*'))
        if _story_truncated:
            print(f"⚠️ [NARRATOR] Текст обрізаний (не закінчується розділовим знаком): ...{story[-50:]!r}")
        if not story or "Тут напиши" in story or len(story) < 20 or _story_truncated:
            if narrator_queue is not None:
                # Streaming retry: consumer ще живий (None не надсилали), тому пушимо нові чанки
                def _sync_stream_retry():
                    chunks = []
                    thought_parts = []
                    try:
                        for chunk in model_narrator.generate_content_stream(narrator_prompt):
                            try:
                                for part in chunk.candidates[0].content.parts:
                                    if getattr(part, "thought", False):
                                        thought_parts.append(part.text or "")
                                    elif part.text:
                                        chunks.append(part.text)
                                        _loop.call_soon_threadsafe(narrator_queue.put_nowait, part.text)
                            except (AttributeError, IndexError):
                                text = chunk.text if chunk.text else ""
                                if text:
                                    chunks.append(text)
                                    _loop.call_soon_threadsafe(narrator_queue.put_nowait, text)
                    except Exception as e:
                        print(f"🔴 [STREAM RETRY] Error: {e}")
                    record_thought(MODEL_NARRATOR_NAME, "\n".join(thought_parts))
                    return "".join(chunks)

                story = await asyncio.to_thread(_sync_stream_retry)
                story = story.strip() if story else "..."
                narrator_queue.put_nowait(None)  # сигнал завершення після retry
            else:
                def _sync_gen_narrator_retry():
                    return model_narrator.generate_content(narrator_prompt)

                retry_resp = await asyncio.to_thread(_sync_gen_narrator_retry)
                story = retry_resp.text.strip() if retry_resp and retry_resp.text else "..."
        elif narrator_queue is not None:
            # Перший стрім вдався — надсилаємо сигнал завершення
            narrator_queue.put_nowait(None)

        duration_narrator = time.time() - t_narrator
        timing_details.append(f"✍️ Narrator: {duration_narrator:.2f}s")

        t_start = time.time()
        npc_changes = ai_data.get("npc_updates", [])
        companion_npcs = ai_data.get("companion_npcs", [])
        if companion_npcs:
            print(f"🤝 [COMPANIONS] NPC що рухаються з гравцем: {companion_npcs}")
        if not npc_changes and npc_context_text:
            print(f"[NPC_WARN] npc_updates=[] при наявних NPC у сцені.")

        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"
            user_sessions.setdefault(chat_id, {})["action_intents"] = {}
            suggested_actions = ["🔄 Почати заново"]

        if "skill_used" in mechanical_updates and mechanical_updates["skill_used"] not in ["None", "Немає"]:
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

            # Reputation delta лог
            rep_delta = mechanical_updates.get("reputation_delta", 0)
            rep_target = mechanical_updates.get("reputation_target_npc")
            if rep_delta and rep_target:
                rep_icon = "📈" if rep_delta > 0 else "📉"
                logs.append(f"{rep_icon} Репутація з {rep_target}: {rep_delta:+d}")

        new_location = profile.get("Поточне місцезнаходження", "")
        # Генерація NPC тільки при зміні реальної локації (не при travel-стані)
        if old_location != new_location and len(new_location) > 3 and new_location != TRAVEL_LOCATION:
            current_char_name = profile.get("Ім'я", "")

            async def travel_population_task(new_loc, char_name):
                try:
                    if new_loc in _atmosphere_cache:
                        situation = _atmosphere_cache[new_loc]
                    else:
                        try:
                            def _sync_gen_travel():
                                return model_worker.generate_content(
                                    f'Describe atmosphere in "{new_loc}" (Year 298) in 1 sentence.')

                            situation_resp = await asyncio.to_thread(_sync_gen_travel)
                            situation = situation_resp.text.strip()
                            _atmosphere_cache[new_loc] = situation
                        except Exception:
                            situation = "A tense day in Westeros."
                    await populate_contextual_npcs(new_loc, situation, excluded_name=char_name,
                                                   excluded_dead_names=get_dead_npc_names())
                except Exception as e:
                    logger.error(f"[BG] travel_population_task failed: {e}", exc_info=True)

            asyncio.create_task(travel_population_task(new_location, current_char_name))

        async def background_task(chat_id_arg, user_id_arg, profile_arg, char_name_arg, input_arg, story_arg,
                                  npc_changes_arg, legal_names_arg, mech_updates_arg=None,
                                  new_location_arg=None, player_location_changed_arg=False,
                                  player_new_scene_arg=None, player_scene_changed_arg=False,
                                  companion_npcs_arg=None):
            try:
                await save_user_data(user_id_arg, profile_arg, char_name_arg)
                if npc_changes_arg:
                    await update_npcs_in_db(
                        npc_changes_arg, legal_names_arg,
                        player_new_location=new_location_arg,
                        player_location_changed=player_location_changed_arg,
                        player_new_scene=player_new_scene_arg,
                        player_scene_changed=player_scene_changed_arg,
                        companion_npcs=companion_npcs_arg,
                    )

                # Оновлення репутації NPC після соціальних перевірок
                if mech_updates_arg:
                    rep_delta = mech_updates_arg.get("reputation_delta", 0)
                    rep_target = mech_updates_arg.get("reputation_target_npc")
                    if rep_delta and rep_target:
                        await update_npc_reputation(rep_target, rep_delta)
                        outcome = mech_updates_arg.get("outcome", "")
                        skill = mech_updates_arg.get("skill_used", "")
                        game_minutes = safe_int(profile_arg.get("Час_хвилини", 0), 0)
                        game_day = game_minutes // 1440
                        await append_memory_anchor(
                            rep_target,
                            f"{skill} {outcome} (дія гравця)",
                            game_day=game_day,
                            rep_change=rep_delta
                        )

                # Один AI-виклик замість двох: об'єднана сумаризація input + story
                clean_story = story_arg.split("📊")[0][:800]
                turn_summary = await summarize_full_turn(input_arg, clean_story)

                if chat_id_arg in user_sessions:
                    hist = user_sessions[chat_id_arg].get('history', [])
                    hist.append({"role": "Turn", "content": turn_summary})

                    if len(hist) > 20:
                        # Sliding window: стискаємо старі записи, зберігаємо останні 15 verbatim
                        old_part = hist[:-15]
                        recent_part = hist[-15:]
                        history_to_compress = "\n".join([f"{m['role']}: {m['content']}" for m in old_part])
                        summary_prompt = build_history_summary_prompt(history_to_compress)
                        try:
                            def _sync_gen_sum():
                                return model_worker.generate_content(summary_prompt)

                            summary_resp = await asyncio.to_thread(_sync_gen_sum)
                            user_sessions[chat_id_arg]['history'] = [
                                {"role": "SYSTEM", "content": f"PREVIOUS EVENTS SUMMARY:\n{summary_resp.text.strip()}"}
                            ] + recent_part
                        except Exception as ex:
                            print(f"⚠️ Помилка стиснення: {ex}")
                            user_sessions[chat_id_arg]['history'] = hist[-20:]
                    else:
                        user_sessions[chat_id_arg]['history'] = hist
            except Exception as e:
                logger.error(f"[BG] background_task failed: {e}", exc_info=True)

        _player_loc_changed  = (old_location != new_location)
        _player_scene_changed = (old_scene != curr_scene)
        asyncio.create_task(
            background_task(
                chat_id, user_id, profile, profile.get("Ім'я"), user_input, story, npc_changes,
                legal_npc_names, mechanical_updates,
                new_location_arg=new_location,
                player_location_changed_arg=_player_loc_changed,
                player_new_scene_arg=curr_scene,
                player_scene_changed_arg=_player_scene_changed,
                companion_npcs_arg=companion_npcs,
            ))

        duration = time.time() - t_start
        timing_details.append(f"💾 Save: {duration:.2f}s")
        total_time = time.time() - global_start

        debug_msg = "⏱️ ТЕХНІЧНИЙ ЗВІТ ХОДУ:\n━━━━━━━━━━━━━━━━\n"
        debug_msg += "\n".join(timing_details)
        debug_msg += "\n━━━━━━━━━━━━━━━━"
        debug_msg += f"\n🏁 ВСЬОГО: {total_time:.2f}s"
        user_sessions[chat_id]['last_debug_time'] = debug_msg

        story = _sanitize_story(story)
        change_log = "\n\n📊 " + " | ".join(logs) if logs else ""

        return story + change_log, suggested_actions

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Якщо streaming вже запущений — надсилаємо сигнал завершення, щоб consumer не висів 120с
        if narrator_queue is not None:
            try:
                narrator_queue.put_nowait(None)
            except Exception:
                pass
        return "📜 *Ворон згубив вашого листа... Спробуйте ще раз.*", []