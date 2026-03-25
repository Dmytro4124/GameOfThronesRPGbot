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
    get_location_npcs, update_npcs_in_db,
    update_npc_reputation, append_memory_anchor
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
            new_val = int(m_new.group(1)) if m_new else 100
            delta = int(m_delta.group(1)) if m_delta else 0

            if delta >= 1:
                hints.append("ВІДНОВЛЕННЯ ЕНЕРГІЇ: Персонаж відновився. GM може тонко згадати відчуття свіжості або прилив сил.")
            elif new_val <= 29 and delta < 0:
                hints.append("ВИСНАЖЕННЯ (КРИТИЧНЕ): Персонаж тяжко виснажений. GM ПОВИНЕН описати видиму боротьбу: тремтячі руки, переривчасте дихання, нетверда хода.")
            elif new_val <= 59 and delta <= -10:
                hints.append("ВИСНАЖЕННЯ: Персонаж помітно втомлений. GM ПОВИНЕН вплести втому в наратив (наприклад: 'ноги стають важкими', 'хвиля знесилення накриває').")
            # 60-100: без підказки — персонаж у нормі

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
    prompt = f"""Стисни цей RPG хід в ОДНЕ коротке речення (максимум 15 слів, українською).
    Формат: "[ХТО] [ДІЯ] [РЕЗУЛЬТАТ]". Збережи імена персонажів та результати.
    Текст GM: "{clean_story}"
    Приклади:
    - "Джон спробував вдарити вартового, але той ухилився."
    - "Гравець домовився з торговцем і отримав знижку."
    - "Напад на бандита провалився — гравець поранений."
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
    energy = safe_int(profile.get("Енергія", 100), 100)
    gold = safe_int(profile.get("Особисте Золото", 0), 0)

    def _qualitative(val, thresholds):
        """Перетворює число на якісну мітку."""
        for threshold, label in thresholds:
            if val <= threshold:
                return label
        return thresholds[-1][1]

    hp_label = _qualitative(hp, [(0, "dead"), (29, "critically wounded — on the brink of death"), (49, "seriously wounded"), (69, "wounded"), (89, "lightly bruised"), (100, "healthy")])
    energy_label = _qualitative(energy, [(0, "collapsed from exhaustion"), (20, "barely standing, trembling with fatigue"), (40, "visibly exhausted"), (60, "somewhat tired"), (80, "energetic"), (100, "full of vigour")])
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

    # === ОНОВЛЕНО: Передаємо і локацію, і сцену для жорсткої фільтрації ===
    context_knowledge = await get_relevant_context(user_input, curr_loc)
    npc_context_text, legal_npc_names, npc_reputation_context = get_location_npcs(curr_loc, curr_scene)

    duration = time.time() - t_start
    timing_details.append(f"📚 Data: {duration:.2f}s")

    t_start = time.time()
    logs = []

    # === ЦЕНЗОР: Перевірка дії до будь-якої механіки ===
    is_valid, refusal_reason = await validate_action(user_input, profile)
    if not is_valid:
        return refusal_reason + "\n\n📊 🛑 Дію заблоковано Цензором.", []

    last_turn = history[-1]["content"] if history else ""
    mechanics_verdict, mechanical_updates = await resolve_action_mechanics(
        user_input, profile, npc_reputation_context,
        current_scene=curr_scene, npc_names=legal_npc_names, last_turn_summary=last_turn
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

    prompt = f"""<system>
    Роль: Неупереджений Майстер Гри (GM) для Grimdark RPG (Гра Престолів).
    Мета: Описати безпосередні наслідки дії гравця, СТРОГО дотримуючись механічного вердикту.
    Тон: Реалістичний, жорстокий, атмосферний. Світ не крутиться навколо гравця.
    МОВА: Поле "story" ПОВИННО бути написане ВИКЛЮЧНО українською. Жодних англійських слів у наративі.
    </system>

    <player_identity>
    ГЕРОЙ ЦІЄї ІСТОРІЇ: {profile.get("Ім'я", "Невідомий")} з дому {profile.get("Дім", "Невідомий")}.
    АБСОЛЮТНЕ ПРАВИЛО: NPC звертаються до героя ТІЛЬКИ як "{profile.get("Ім'я", "").split()[-1] if profile.get("Ім'я", "") else "Герой"}" або "лорд/леді {profile.get("Дім", "")}".
    ЗАБОРОНА: НІКОЛИ не називай героя прізвищем іншого дому. Герой НЕ є Ланністером, Старком, Таргарієном, Баратеоном чи іншим домом — ТІЛЬКИ {profile.get("Дім", "")}.
    ПРИКЛАД ПОМИЛКИ: NPC каже "Ланністере" герою з дому {profile.get("Дім", "")} — це ГРУБЕ ПОРУШЕННЯ.
    НАВІТЬ КОЛИ NPC РОЗЛЮЧЕНИЙ — він все одно використовує справжнє прізвище героя: "{profile.get("Ім'я", "").split()[-1] if profile.get("Ім'я", "") else "Герой"}".
    </player_identity>

    <player_state>
    {profile_json}
    ПРИМІТКА GM: Використовуй поле "Wealth" для обмеження покупок гравця. Якщо "penniless" — гравець не може нічого купити.
    </player_state>

    <world_context>
    {GAME_ERA_CONTEXT}
    {context_knowledge}
    {event_injection}
    {burst_injection}
    </world_context>

    <scene_state>
    ПОТОЧНИЙ ЧАС: {current_time_str}
    ПОТОЧНЕ МІСТО: {profile.get("Поточне місцезнаходження", "")}
    ПОТОЧНА СЦЕНА: {profile.get("Поточна сцена", "")}
    ПРИСУТНІ NPC (тільки персонажі, фізично присутні в цій сцені):
    {npc_context_text}
    АТМОСФЕРА СЦЕНИ: {_tension_label}
    ПРАВИЛО НАПРУГИ: Якщо атмосфера "небезпечно" або "на межі вибуху" — NPC мають реагувати з терміновістю, агресією або панікою. НІКОЛИ не повторюй попередні реакції NPC — ескалюй поведінку з кожним ходом.
    </scene_state>

    <mechanical_verdict>
    {mechanics_verdict}
    Інструкція: Ти ЗОБОВ'ЯЗАНИЙ дотримуватися цього вердикту.
    - Якщо FAILURE: Опиши болісний або фрустраційний результат. Додай напруги.
    - Якщо SUCCESS: Опиши тріумф.
    - ЖОДНИХ ЧИСЕЛ (АБСОЛЮТНО): НІКОЛИ не пиши жодної цифри чи числа в тексті story.
      Це стосується і чисел, які гравець згадує у своїй дії.
      ОБОВ'ЯЗКОВА КОНВЕРТАЦІЯ — якщо гравець згадує число, конвертуй:
        "5000 золотих" → "цілий статок", "важкий гаманець золота", "досить щоб купити лордство"
        "100 солдатів" → "ціле військо", "невелика армія"
        "3 дні" → "кілька днів", "кількаденна подорож"
      У наративному світі немає арифметики. Тільки вага, масштаб і відчуття.
    </mechanical_verdict>
    {f'<system_impacts>{chr(10)}{impact_narrative_hints}{chr(10)}ПРАВИЛО: Ти ЗОБОВ\'ЯЗАНИЙ вплести вказані вище підказки в наратив БЕЗ використання чисел. Це обов\'язкові наративні акценти.{chr(10)}</system_impacts>' if impact_narrative_hints else ''}

    <golden_laws_of_agency>
    1. НЕ ЧІПАЙ ГРАВЦЯ: Ти керуєш NPC та фізикою. Гравець керує ТІЛЬКИ своїм Героєм.
    2. БЕЗ ЧИТАННЯ ДУМОК: Ніколи не кажи гравцю що він відчуває чи думає. Показуй, а не розповідай.
    3. БЕЗ ЧЕРЕВОМОВСТВА: Тобі ЗАБОРОНЕНО писати репліки від імені Героя.
    4. НАМІР vs РЕЗУЛЬТАТ: Гравець описує НАМІР. Ти визначаєш РЕЗУЛЬТАТ на основі механічного вердикту.
    5. СИГНАЛ ЗУПИНКИ: Ти ПОВИНЕН зупинити текст одразу після реакції NPC. Залиш хід гравцю.
    6. ВИХІД ЗІ СЦЕНИ (КРИТИЧНО): Якщо дія гравця — ПІТИ, ВИЙТИ або ЗМІНИТИ ЛОКАЦІЮ — поточна сцена НЕГАЙНО ЗАВЕРШУЄТЬСЯ. Тобі СТРОГО ЗАБОРОНЕНО описувати реакції, думки чи дії NPC, яких гравець залишає позаду. Опиши ТІЛЬКИ перехід і нове оточення.
    7. РУХ КОМПАНЬЙОНІВ: Якщо гравець переміщується в нову Сцену/Локацію, ти ПОВИНЕН оновити поля "Scene" та "Location" в npc_updates JSON для союзних NPC, які активно слідують за гравцем. Залишених позаду персонажів НЕ оновлювати.
    </golden_laws_of_agency>

    <history>
    {history_text}
    </history>

    <current_turn>
    ДІЯ ГРАВЦЯ: "{user_input}"
    </current_turn>

    <json_generation_rules>
    0. АНАЛІЗ NPC (ОБОВ'ЯЗКОВО ПЕРШИМ): Заповни "npc_reasoning" ДО написання "story". Перерахуй кожного NPC з АКТИВНОГО РОСТЕРУ, з яким гравець взаємодіяв (прямо чи опосередковано). Для кожного вкажи ЩО ЗМІНИЛОСЬ: ставлення, локація, інвентар, мета, стан. Якщо ЖОДЕН NPC не змінився — явно напиши причину. Тільки після заповнення "npc_reasoning" — переходь до "npc_updates" та "story".
    1. ПОСТІЙНІСТЬ ПРЕДМЕТІВ: Якщо фізичні предмети чи золото обмінюються, ти ПОВИНЕН оновити поле "Inventory" NPC.
    2. ПОРОЖНІ РЯДКИ: Якщо конкретне поле НЕ змінилося, його значення ПОВИННО бути рівно "".
    3. ЗАБОРОНЕНІ СЛОВА: Не пиши "no change", "same", або "без змін". Використовуй "".
    4. ПРАВИЛО ВИКЛИКУ NPC: Використовуй ТІЛЬКИ точні імена з ПРИСУТНІХ NPC.
    5. ЗАПРОПОНОВАНІ ДІЇ: Надай рівно 4 об'єкти у ТОЧНО такому порядку:
       Дія 1 — тип [{_action_slots[0]}]: {{"button": "короткий label до 5 слів", "intent": "розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі як саме і навіщо"}}
       Дія 2 — тип [{_action_slots[1]}]: аналогічно
       Дія 3 — тип [{_action_slots[2]}]: аналогічно
       Дія 4 — тип [{_action_slots[3]}]: аналогічно
       Grimdark тон, українська мова для обох полів. Назву типу НЕ включати в тексти.
    6. ЗАБОРОНА БЕЗІМЕННИХ NPC: НІКОЛИ не вводь нового персонажа як "Лорд [Дім]" або "Леді [Дім]" без першого імені. Завжди давай конкретне ім'я (наприклад, "Сер Ронет Калдер" замість "Лорд Ланністер", "Вартовий Маркос" замість "один з охоронців").
    </json_generation_rules>

    ВІДПОВІДАЙ СТРОГО У ФОРМАТІ JSON:
    {{
        "reasoning": "Твоє коротке внутрішнє міркування про те, як світ/NPC реагують на дію гравця з урахуванням механічного вердикту.",
        "npc_reasoning": "ОБОВ'ЯЗКОВО: перерахуй кожного NPC з активного ростеру, з яким гравець взаємодіяв, і що саме змінилося (ставлення, локація, інвентар, мета, стан). Якщо жоден NPC не змінився — поясни чому.",
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
        ],
        "story": "Твій фінальний наративний текст українською. Заверши запрошенням до наступної дії.",
        "suggested_actions": [{{"button": "Текст кнопки", "intent": "Розгорнутий намір від першої особи"}}, ...]
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

        if "Тут напиши" in story or len(story) < 20:
            char_name = profile.get("Ім'я", "Герой")
            scene_name = profile.get("Поточна сцена", "Невідомо")
            verdict_brief = mechanics_verdict[:300]
            def _sync_gen_fix():
                return model.generate_content(
                    f"Ти GM у грі Game of Thrones (298 рік). Гравець {char_name} у локації {scene_name}. Механічний вердикт: {verdict_brief}. Дія гравця: {user_input}. Опиши наслідки художньо українською. Не використовуй числа. Закінчи питанням.")

            fix_resp = await asyncio.to_thread(_sync_gen_fix)
            story = fix_resp.text

        duration = time.time() - t_start
        timing_details.append(f"✍️ Story: {duration:.2f}s")

        t_start = time.time()
        npc_changes = ai_data.get("npc_updates", [])
        if not npc_changes and npc_context_text:
            npc_rsn = ai_data.get("npc_reasoning", "—")
            print(f"[NPC_WARN] npc_updates=[] при наявних NPC у сцені. npc_reasoning: {npc_rsn[:300]}")

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
        # Модифіковано: Тепер генерація канонічних NPC враховує і зміну сцени (якщо треба)
        if old_location != new_location and len(new_location) > 3:
            current_char_name = profile.get("Ім'я", "")

            async def travel_population_task(new_loc, char_name):
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
                    except:
                        situation = "A tense day in Westeros."
                await populate_contextual_npcs(new_loc, situation, excluded_name=char_name)

            await asyncio.create_task(travel_population_task(new_location, current_char_name))

        async def background_task(chat_id_arg, user_id_arg, profile_arg, char_name_arg, input_arg, story_arg,
                                  npc_changes_arg, legal_names_arg, mech_updates_arg=None):
            try:
                await save_user_data(user_id_arg, profile_arg, char_name_arg)
                if npc_changes_arg:
                    await update_npcs_in_db(npc_changes_arg, legal_names_arg)

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

                short_hist_turn = await summarize_turn(story_arg)
                short_user_inp = await summarize_turn(input_arg)

                if chat_id_arg in user_sessions:
                    hist = user_sessions[chat_id_arg].get('history', [])
                    hist.append({"role": "User", "content": short_user_inp})
                    hist.append({"role": "GM", "content": short_hist_turn})

                    if len(hist) > 20:
                        # Sliding window: стискаємо старі записи, зберігаємо останні 15 verbatim
                        old_part = hist[:-15]
                        recent_part = hist[-15:]
                        history_to_compress = "\n".join([f"{m['role']}: {m['content']}" for m in old_part])
                        summary_prompt = f"""Стисни цю RPG історію в КЛЮЧОВІ ФАКТИ (список, українською, максимум 8 пунктів).
Зосередься на: імена NPC та стосунки, обіцянки/домовленості, поточний квест, важливі отримані/втрачені предмети.
Історія:\n{history_to_compress}"""
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
            except Exception as exept:
                print(f"❌ [BG ERROR] {exept}")

        await asyncio.create_task(
            background_task(chat_id, user_id, profile, profile.get("Ім'я"), user_input, story, npc_changes,
                            legal_npc_names, mechanical_updates))

        duration = time.time() - t_start
        timing_details.append(f"💾 Save: {duration:.2f}s")
        total_time = time.time() - global_start

        debug_msg = "⏱️ *ТЕХНІЧНИЙ ЗВІТ ХОДУ:*\n━━━━━━━━━━━━━━━━\n"
        debug_msg += "\n".join(timing_details)
        debug_msg += "\n━━━━━━━━━━━━━━━━"
        debug_msg += f"\n🏁 *ВСЬОГО:* `{total_time:.2f}s`"
        user_sessions[chat_id]['last_debug_time'] = debug_msg

        story = _sanitize_story(story)
        change_log = "\n\n📊 " + " | ".join(logs) if logs else ""

        return story + change_log, suggested_actions

    except Exception as e:
        import traceback
        traceback.print_exc()
        return "📜 *Ворон згубив вашого листа... Спробуйте ще раз.*", []