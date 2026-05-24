# core/world.py
import difflib
import json
import asyncio
from database.sheets import db
from database.operations import refresh_npc_database, find_best_match, ensure_user_npc_sheet, _npc_tab_name
from core.ai_client import model, model_gm_logic, ask_gemini, clean_and_parse_json
from core.prompts import (
    GAME_ERA_CONTEXT, build_famous_characters_prompt,
    build_initial_stats_prompt, build_game_intro_prompt, build_populate_npcs_prompt,
)
from core.world_constants import get_region_for_location, get_locations_for_region, is_valid_location, VALID_LOCATIONS_ORDERED, format_scenes_for_prompt, LOCATION_SCENES
from database.canon_npc import get_canon_npcs_copy


async def get_canon_characters(house_name):
    """Асинхронно повертає список відомих канонічних персонажів для конкретного дому."""
    PRESET_CHARACTERS = {
        "Старк": ["Еддард Старк", "Кейтлін Старк", "Робб Старк", "Джон Сноу", "Арія Старк"],
        "Ланністер": ["Тайвін Ланністер", "Джейме Ланністер", "Серсея Ланністер", "Тіріон Ланністер"],
        "Баратеон": ["Роберт Баратеон", "Станніс Баратеон", "Ренлі Баратеон", "Джоффрі Баратеон"],
        "Таргарієн": ["Данерис Таргарієн", "Візеріс Таргарієн"],
        "Грейджой": ["Бейлон Грейджой", "Теон Грейджой", "Аша Грейджой", "Еурон Грейджой"],
        "Тірелл": ["Мейс Тірелл", "Оленна Тірелл", "Маргері Тірелл", "Лорас Тірелл"],
        "Мартелл": ["Доран Мартелл", "Оберін Мартелл", "Аріанна Мартелл"],
        "Таллі": ["Хостер Таллі", "Брінден Чорна Риба", "Едмур Таллі"],
        "Аррен": ["Ліза Аррен", "Роберт Аррен"]
    }

    if house_name in PRESET_CHARACTERS:
        print(f"⚡ Використовую кеш для дому {house_name}")
        return PRESET_CHARACTERS[house_name]

    print(f"🔍 Запитую AI про персонажів дому {house_name}...")
    prompt = build_famous_characters_prompt(house_name)

    result = await ask_gemini(prompt)
    return result if result else []


async def generate_initial_stats_legacy(char_name, house_name, house_data):
    """Legacy 2d50-profile generator (збережено для rollback). Не використовується в Phase 5+."""
    origin_region = house_data.get('Регіон', 'Вестерос')
    print(f"🎲 [LEGACY] Генерую статистику для {char_name}...")

    valid_locations_str = ", ".join(f'"{loc}"' for loc in VALID_LOCATIONS_ORDERED)
    _scenes_block = format_scenes_for_prompt(origin_region)

    from core.prompts import build_initial_stats_prompt as _legacy_prompt
    prompt = _legacy_prompt(char_name, house_name, origin_region, valid_locations_str, scenes_block_str=_scenes_block)

    try:
        def _sync_gen_profile():
            return model_gm_logic.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen_profile)
        profile = clean_and_parse_json(response.text)
    except Exception as e:
        print(f"❌ [LEGACY] Помилка генерації профілю: {e}")
        profile = None
    if profile:
        profile["Ім'я"] = char_name
        profile["Дім"] = house_name
        loc = profile.get("Поточне місцезнаходження", "")
        if not is_valid_location(loc):
            fallback_loc = VALID_LOCATIONS_ORDERED[0]
            profile["Поточне місцезнаходження"] = fallback_loc
            loc = fallback_loc
        profile["Регіон"] = get_region_for_location(loc) or origin_region
        curr_scene = profile.get("Поточна сцена", "")
        if not curr_scene or curr_scene.strip().lower() in ("невідомо", "global", "unknown", ""):
            loc_scenes = LOCATION_SCENES.get(loc, {})
            hub_scenes = loc_scenes.get("hub", [])
            if hub_scenes:
                profile["Поточна сцена"] = hub_scenes[0]
            else:
                profile["Поточна сцена"] = loc
        return profile
    return None


async def generate_initial_stats(char_name, house_name, house_data):
    """Асинхронно генерує стартовий D&D профіль героя (Phase 5+).

    Порядок:
    1. LLM (build_initial_stats_prompt D&D) → suggested_class, suggested_heritage, ability_scores, etc.
    2. Python детерміністично обчислює: level, xp, proficiency_bonus, hp_max, hp_current,
       ac, skill_profs, saves_proficient, equipment, gold, features, conditions.
    3. Застосовує heritage bonuses через dnd_heritages.apply_heritage_bonuses.
    4. Зберігає legacy-сумісні ключі ("Здоров'я", "Енергія") поряд з D&D полями.
    """
    from core.dnd_classes import get_class, get_starting_hp, build_class_starting_kit, get_class_features_at_level
    from core.dnd_heritages import apply_heritage_bonuses, get_heritage
    from core.dnd_core import ability_modifier, proficiency_bonus

    origin_region = house_data.get('Регіон', 'Вестерос')
    print(f"🎲 [D&D] Генерую статистику для {char_name}...")

    valid_locations_str = ", ".join(f'"{loc}"' for loc in VALID_LOCATIONS_ORDERED)
    _scenes_block = format_scenes_for_prompt(origin_region)

    prompt = build_initial_stats_prompt(char_name, house_name, origin_region, valid_locations_str, scenes_block_str=_scenes_block)

    # Gemma 4 31B — одноразова генерація, якість важливіша за швидкість
    try:
        def _sync_gen_profile():
            return model_gm_logic.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen_profile)
        llm_data = clean_and_parse_json(response.text)
    except Exception as e:
        print(f"❌ [D&D] Помилка генерації профілю (Gemma 4): {e}")
        llm_data = None

    if not llm_data:
        print("⚠️ [D&D] LLM повернув None — fallback до legacy generator")
        return await generate_initial_stats_legacy(char_name, house_name, house_data)

    # --- Validate location and scene (same as legacy) ---
    llm_data["Ім'я"] = char_name
    llm_data["Дім"] = house_name
    loc = llm_data.get("Поточне місцезнаходження", "")
    if not is_valid_location(loc):
        fallback_loc = VALID_LOCATIONS_ORDERED[0]
        print(f"⚠️ [D&D] Невалідна локація '{loc}' → замінено на '{fallback_loc}'")
        llm_data["Поточне місцезнаходження"] = fallback_loc
        loc = fallback_loc
    llm_data["Регіон"] = get_region_for_location(loc) or origin_region
    curr_scene = llm_data.get("Поточна сцена", "")
    if not curr_scene or curr_scene.strip().lower() in ("невідомо", "global", "unknown", ""):
        loc_scenes = LOCATION_SCENES.get(loc, {})
        hub_scenes = loc_scenes.get("hub", [])
        if hub_scenes:
            llm_data["Поточна сцена"] = hub_scenes[0]
            print(f"⚠️ [D&D SCENE FALLBACK] LLM did not provide scene for '{loc}'; using hub[0]='{hub_scenes[0]}'.")
        else:
            llm_data["Поточна сцена"] = loc

    # --- Resolve class and heritage ---
    suggested_class: str = str(llm_data.get("suggested_class", "Hedge Knight")).strip()
    suggested_heritage: str = str(llm_data.get("suggested_heritage", "Westerosi (Andal)")).strip()

    # Graceful fallback for unknown class / heritage
    from core.dnd_classes import GOT_CLASSES
    from core.dnd_heritages import HERITAGES
    if suggested_class not in GOT_CLASSES:
        print(f"⚠️ [D&D] Unknown class '{suggested_class}' → fallback to 'Hedge Knight'")
        suggested_class = "Hedge Knight"
    if suggested_heritage not in HERITAGES:
        print(f"⚠️ [D&D] Unknown heritage '{suggested_heritage}' → fallback to 'Westerosi (Andal)'")
        suggested_heritage = "Westerosi (Andal)"

    cls_def = get_class(suggested_class)

    # --- Ability scores from LLM (should be 8-15 each; heritage bonuses applied by Python) ---
    raw_scores: dict = llm_data.get("ability_scores", {})
    _default_scores = {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}
    ability_scores: dict[str, int] = {}
    for ab in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        raw_val = raw_scores.get(ab, _default_scores[ab])
        # Clamp: LLM must stay 8-15; clamped here for safety before heritage
        ability_scores[ab] = max(8, min(15, int(float(str(raw_val))) if str(raw_val).replace('.', '').isdigit() else 10))

    llm_data["ability_scores"] = ability_scores

    # Apply heritage bonuses (mutates ability_scores safely, caps at 20)
    llm_data = apply_heritage_bonuses(
        llm_data,
        suggested_heritage,
        ability_choices=None,  # Westerosi Andal: fallback to STR, DEX
    )
    final_scores: dict[str, int] = llm_data["ability_scores"]

    # --- Level 1 D&D fields (deterministic) ---
    level = 1
    xp = 0
    pb = proficiency_bonus(level)  # = 2 at L1
    con_mod = ability_modifier(final_scores.get("CON", 10))
    hp_max = get_starting_hp(suggested_class, con_mod)
    hp_current = hp_max

    # AC: 10 + DEX mod (unarmored), or armor-based if equipment has armor
    kit = build_class_starting_kit(suggested_class)
    armor_str: str = kit.get("armor") or ""
    dex_mod = ability_modifier(final_scores.get("DEX", 10))
    if armor_str:
        # Simple armor AC parse: "Mail (AC 16)" → extract number
        import re
        ac_match = re.search(r'AC\s*(\d+)', armor_str)
        if ac_match:
            base_ac = int(ac_match.group(1))
            # Studded Leather / Leather: AC base + DEX mod (up to +2 for medium)
            if "DEX mod" in armor_str:
                max_dex_bonus = 2 if ("medium" in armor_str.lower() or "hide" in armor_str.lower() or "studded" in armor_str.lower()) else 99
                ac = base_ac + min(dex_mod, max_dex_bonus)
            else:
                ac = base_ac
        else:
            ac = 10 + dex_mod
    else:
        ac = 10 + dex_mod

    # Shield bonus if class has shield in starting kit
    if kit.get("shield"):
        ac += 2

    # Skill proficiencies: choose first N from class skill_pool
    skill_choices_count = cls_def.skill_choices
    skill_profs: list[str] = list(cls_def.skill_pool[:skill_choices_count])

    # Save proficiencies from class
    saves_proficient: list[str] = list(cls_def.saves_proficient)

    # L1 features
    features: list[dict] = [
        {"name": f.name, "desc": f.desc, "source": f.source}
        for f in get_class_features_at_level(suggested_class, 1)
    ]

    # Equipment string for legacy "Інвентар" field
    items_list: list[str] = list(kit.get("items", []))
    if kit.get("weapon_main"):
        items_list.insert(0, kit["weapon_main"])
    if kit.get("weapon_off"):
        items_list.insert(1, kit["weapon_off"])
    inventory_str = ", ".join(items_list) if items_list else "Пусто"

    gold_from_kit = int(kit.get("gold", 0))

    # Heritage traits (languages etc already applied by apply_heritage_bonuses)
    heritage_obj = get_heritage(suggested_heritage)
    heritage_trait_names = ", ".join(t.name for t in heritage_obj.traits)

    # --- Build final profile ---
    profile: dict = {
        # D&D core fields
        "level": level,
        "xp": xp,
        "class": suggested_class,
        "heritage": suggested_heritage,
        "background": str(llm_data.get("background", "Soldier")).strip(),
        "ability_scores": final_scores,
        "proficiency_bonus": pb,
        "hp_max": hp_max,
        "hp_current": hp_current,
        "hit_dice_total": 1,
        "ac": ac,
        "skill_profs": skill_profs,
        "skill_expertise": [],
        "saves_proficient": saves_proficient,
        "features": features,
        "conditions": [],
        "mode": "NORMAL",
        "combat_state_ref": None,
        "asi_pending": False,
        "languages": llm_data.get("languages", ["Common Tongue"]),

        # Narrative / character fields
        "Ім'я": char_name,
        "Дім": house_name,
        "Поточне місцезнаходження": llm_data.get("Поточне місцезнаходження", VALID_LOCATIONS_ORDERED[0]),
        "Поточна сцена": llm_data.get("Поточна сцена", ""),
        "Регіон": llm_data.get("Регіон", origin_region),
        "Світогляд": str(llm_data.get("Світогляд", "")).strip(),
        "Риси": str(llm_data.get("Риси", heritage_trait_names)).strip(),
        "Вади": str(llm_data.get("Вади", "")).strip(),
        "Особисті риси": llm_data.get("personality_traits", []),
        "Зв'язок": str(llm_data.get("bond", "")).strip(),
        "Вада персонажа": str(llm_data.get("flaw", "")).strip(),
        "Вороги": "",
        "Друзі": "",

        # Legacy compatibility fields (engine.py and mechanics.py read these)
        "Здоров'я": hp_current,   # mirrors hp_current; updated by apply_dnd_impacts
        "Енергія": 1000,          # legacy energy scale 0-1000; Phase 5 keeps it active
        "Інвентар": inventory_str,
        "Особисте Золото": gold_from_kit,
        "Зброя": kit.get("weapon_main", "-"),
        "Броня": armor_str or "Без броні",
        "Транспорт": "None",
        "Ігровий час": "298 рік В.Е., 1-й місяць, День 1, 08:00",
        "Час_хвилини": 8 * 60,
        "Годинники": {},
        "Репутація (Рідний регіон)": 0,
        "temp_debuffs": {},
        "training_cooldowns": {},
        "reputation": {"regions": {}, "factions": {}},
    }

    print(f"✅ [D&D] Профіль створено: {char_name} | {suggested_class} L{level} | {suggested_heritage} | HP {hp_current}/{hp_max} | AC {ac}")
    return profile


async def get_narrative_intro(profile):
    """Асинхронно генерує атмосферний вступ на основі локації та профілю гравця.
    Повертає dict: {narrative_text, action_prompt, suggested_actions}."""
    print(f"📜 Пишу вступ для {profile.get('Ім' + chr(39) + 'я')}...")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    current_location = profile.get("Поточне місцезнаходження", "Вестерос")
    current_scene = profile.get("Поточна сцена", "") or current_location

    prompt = build_game_intro_prompt(profile_json, current_location, current_scene)
    try:
        def _sync_gen():
            return model.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen)
        raw_text = response.text.strip()
        parsed = clean_and_parse_json(raw_text)
        if parsed and parsed.get("narrative_text"):
            # Soft-warn: чи модель дотрималась scene_constraint
            scene_check = (parsed.get("scene_check", "") or "").strip()
            expected = current_scene.strip()
            if scene_check and scene_check != expected:
                print(f"⚠️ [SCENE MISMATCH] LLM scene_check='{scene_check}' "
                      f"≠ profile['Поточна сцена']='{expected}'. Narrative may not match profile.")
            return parsed
        else:
            return {
                "narrative_text": f"Ви прибули у {current_location}. Вітер дме в обличчя.",
                "action_prompt": "Що ви робите?",
                "suggested_actions": []
            }
    except Exception as e:
        print(f"❌ Помилка генерації вступу: {e}")
        return {
            "narrative_text": f"Ви прибули у {current_location}. Вітер дме в обличчя.",
            "action_prompt": "Що ви робите?",
            "suggested_actions": []
        }


async def background_canon_generation(user_id, excluded_name=None):
    """Асинхронний ФОНОВИЙ ПРОЦЕС: Створення кістяка світу. Завантажує ЖОРСТКИЙ КАНОН."""
    print("🌍 [CANON GEN] Завантажую канонічну базу даних...")

    await ensure_user_npc_sheet(user_id)

    def _sync_db_ops():
        import time as _time
        tab_name = _npc_tab_name(user_id)
        worksheet = db.get_sheet(tab_name)
        if not worksheet: return False

        try:
            worksheet.clear()
            headers = ["Location", "Scene", "Name", "Description", "Character", "Goal", "Secrets",
                       "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon", "Inventory",
                       "Reputation_Score", "Region"]
            worksheet.append_row(headers)
            _time.sleep(2)  # Пауза після clear+headers, щоб API встиг обробити
        except Exception as e:
            print(f"❌ [CANON GEN ERROR] Clear failed: {e}")
            return False

        try:
            rows_to_add = []
            for npc in get_canon_npcs_copy():
                name = npc.get("Name", "Unknown")
                if excluded_name and find_best_match(name, [excluded_name], threshold=0.8):
                    continue

                row = [
                    npc.get("Location", "Westeros"),
                    npc.get("Scene") or npc.get("Location", "Вестерос"),
                    name,
                    npc.get("Description", "-"),
                    npc.get("Character", "-"),
                    npc.get("Goal", "-"),
                    npc.get("Secrets", "-"),
                    npc.get("Relation_Player", "Neutral"),
                    npc.get("Memory_Anchor", "-"),
                    npc.get("Relation_NPCs", "-"),
                    npc.get("Status", "Active"),
                    npc.get("Is_Canon", "TRUE"),
                    npc.get("Inventory", "Пусто"),
                    npc.get("Reputation_Score", 0),
                    npc.get("Region", ""),
                ]
                rows_to_add.append(row)

            if not rows_to_add:
                print("⚠️ [CANON GEN] Жодного NPC для запису!")
                return False

            print(f"📋 [CANON GEN] Підготовлено {len(rows_to_add)} канонічних NPC для запису...")

            # Батчінг: пишемо по 15 рядків з паузою між батчами (захист від API rate limit)
            BATCH_SIZE = 15
            total_written = 0
            for i in range(0, len(rows_to_add), BATCH_SIZE):
                batch = rows_to_add[i:i + BATCH_SIZE]
                try:
                    worksheet.append_rows(batch)
                    total_written += len(batch)
                    print(f"📝 [CANON GEN] Batch {i // BATCH_SIZE + 1}: записано {len(batch)} NPC")
                except Exception as e:
                    print(f"❌ [CANON GEN BATCH ERROR] Batch {i // BATCH_SIZE + 1}: {type(e).__name__}: {e}")
                    _time.sleep(3)
                    try:
                        worksheet.append_rows(batch)
                        total_written += len(batch)
                        print(f"🔄 [CANON GEN] Batch {i // BATCH_SIZE + 1}: retry успішний")
                    except Exception as e2:
                        print(f"❌ [CANON GEN BATCH RETRY FAILED] {type(e2).__name__}: {e2}")
                if i + BATCH_SIZE < len(rows_to_add):
                    _time.sleep(2)

            # Верифікація: перечитуємо таблицю і перевіряємо кількість записаних рядків
            _time.sleep(1)
            verify_rows = worksheet.get_all_values()
            actual_count = len(verify_rows) - 1  # мінус заголовок
            print(f"🔍 [CANON GEN VERIFY] Очікувано {total_written}, знайдено {actual_count} рядків у таблиці")
            if actual_count < total_written:
                print(f"⚠️ [CANON GEN] DATA LOSS: {total_written - actual_count} рядків зникло!")

            return total_written if total_written > 0 else False

        except Exception as e:
            print(f"❌ [CANON GEN CRITICAL] Row building/writing failed: {type(e).__name__}: {e}")
            return False

    added_count = await asyncio.to_thread(_sync_db_ops)
    if added_count:
        print(f"✅ [CANON GEN] Світ заселено! Додано {added_count} канонічних легенд.")
        await refresh_npc_database(user_id)
    else:
        print("❌ [CANON GEN] Не вдалося записати жодного канонічного NPC!")


async def populate_contextual_npcs(user_id, location, situation_context="Normal day, calm atmosphere", excluded_name=None, excluded_dead_names: set = None):
    """Асинхронно генерує NPC, які відповідають ПОТОЧНІЙ СИТУАЦІЇ в локації. Блокує канонічні імена."""
    print(f"🏘️ [LOCAL POP] Аналізую локацію: {location}...")

    def _sync_db_read():
        tab_name = _npc_tab_name(user_id)
        worksheet = db.get_sheet(tab_name)
        if not worksheet: return None, None, []
        canon_names_local = []
        try:
            all_rows = worksheet.get_all_values()
            if not all_rows:
                print("⚠️ [LOCAL POP] Таблиця NPC_DB порожня!")
                return None, None, []

            headers = all_rows[0]

            # Динамічний пошук колонок замість хардкоду індексів
            try:
                canon_col_idx = headers.index("Is_Canon")
            except ValueError:
                print(f"❌ [LOCAL POP ERROR] Колонку 'Is_Canon' не знайдено! Заголовки: {headers}")
                return None, None, []

            try:
                name_col_idx = headers.index("Name")
            except ValueError:
                name_col_idx = 2  # fallback

            preserved_rows = [headers]

            for row in all_rows[1:]:
                if len(row) > canon_col_idx and str(row[canon_col_idx]).upper() == "TRUE":
                    preserved_rows.append(row)
                    canon_name = str(row[name_col_idx]).strip() if len(row) > name_col_idx else ""
                    if canon_name:
                        canon_names_local.append(canon_name)

            print(f"🔍 [LOCAL POP] Знайдено {len(preserved_rows) - 1} канонічних NPC з {len(all_rows) - 1} загальних")

            # Захист: якщо є рядки але жоден не канонічний — не стираємо таблицю
            if len(all_rows) > 1 and len(preserved_rows) == 1:
                print(f"⚠️ [LOCAL POP] {len(all_rows) - 1} рядків існує, але 0 канонічних! "
                      f"Пропускаємо деструктивне очищення. Перевірте Is_Canon колонку (idx={canon_col_idx}).")
                # Повертаємо worksheet без очищення, щоб не втратити дані
                return worksheet, all_rows, canon_names_local

            worksheet.clear()
            worksheet.update(preserved_rows)
            return worksheet, preserved_rows, canon_names_local
        except Exception as e:
            print(f"❌ [LOCAL POP ERROR] Cleanup failed: {e}")
            return None, None, []

    worksheet, _, canon_names = await asyncio.to_thread(_sync_db_read)
    if not worksheet: return

    blacklist_str = ", ".join(canon_names)

    _scenes_block = format_scenes_for_prompt(location)
    prompt = build_populate_npcs_prompt(location, situation_context, blacklist_str, scenes_block_str=_scenes_block)
    try:
        def _sync_ai_gen():
            return model.generate_content(prompt)

        response = await asyncio.to_thread(_sync_ai_gen)
        npc_list = clean_and_parse_json(response.text)

        if not npc_list: return

        new_rows = []
        for npc in npc_list:
            ai_gen_name = npc.get("Name", "Unknown").strip()

            if excluded_name and find_best_match(ai_gen_name, [excluded_name], threshold=0.8):
                continue

            # Fix 2: Блок мертвих NPC — fuzzy-пошук по excluded_dead_names
            if excluded_dead_names:
                dead_matches = difflib.get_close_matches(ai_gen_name, excluded_dead_names, n=1, cutoff=0.7)
                if dead_matches:
                    print(f"⚰️ [POP BLOCKED] AI намагався додати мертвого NPC '{ai_gen_name}' (схожий на '{dead_matches[0]}'). Пропускаємо.")
                    continue

            is_canon_clone = False
            if canon_names:
                matches = difflib.get_close_matches(ai_gen_name, canon_names, n=1, cutoff=0.7)
                if matches:
                    is_canon_clone = True
                    print(
                        f"🛡️ [ANTI-CANON FILTER] Заблоковано генерацію '{ai_gen_name}'. Занадто схоже на канон '{matches[0]}'.")

            forbidden_houses = ["старк", "ланністер", "таргарієн", "баратеон", "тірелл", "грейджой", "мартелл", "аррен",
                                "таллі", "болтон", "мормонт", "кхал"]
            if any(house in ai_gen_name.lower() for house in forbidden_houses):
                is_canon_clone = True
                print(f"🛡️ [ANTI-CANON FILTER] Заблоковано '{ai_gen_name}' через використання прізвища Великого Дому.")

            if is_canon_clone:
                continue

            # ОНОВЛЕНО: Додано Scene, Inventory, Reputation_Score та Region
            from database.canon_npc import _map_relation_to_score
            initial_rep = _map_relation_to_score(npc.get("Relation_Player", "Neutral"))
            _scene_raw = npc.get("Scene", location)
            _scene_words = str(_scene_raw).split()
            _scene_val = " ".join(_scene_words[:3])
            if not _scene_val or _scene_val.strip().lower() in ("невідомо", "global", "unknown"):
                _scene_val = location
            row = [
                location,
                _scene_val,
                ai_gen_name,
                npc.get("Description", "-"),
                npc.get("Character", "-"),
                npc.get("Goal", "-"),
                npc.get("Secrets", "-"),
                npc.get("Relation_Player", "Neutral"),
                npc.get("Memory_Anchor", "-"),
                npc.get("Relation_NPCs", "-"),
                "Active",
                "FALSE",
                "Пусто",
                initial_rep,
                get_region_for_location(location) or "",
            ]
            new_rows.append(row)

        if new_rows:
            def _sync_db_write():
                worksheet.append_rows(new_rows)

            await asyncio.to_thread(_sync_db_write)

            print(f"✅ [LOCAL POP] Локацію {location} заселено ({len(new_rows)} нових NPC).")
            await refresh_npc_database(user_id)
        else:
            print(f"⚠️ [LOCAL POP] ШІ не згенерував жодного валідного NPC для {location}.")

    except Exception as e:
        print(f"❌ [LOCAL POP AI ERROR] {e}")