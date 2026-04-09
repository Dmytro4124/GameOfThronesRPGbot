# core/world.py
import difflib
import json
import asyncio
from database.sheets import db
from database.operations import refresh_npc_database, find_best_match
from core.ai_client import model, model_gm_logic, ask_gemini, clean_and_parse_json
from core.prompts import (
    GAME_ERA_CONTEXT, build_famous_characters_prompt,
    build_initial_stats_prompt, build_game_intro_prompt, build_populate_npcs_prompt,
)
from core.world_constants import get_region_for_location, get_locations_for_region, is_valid_location, VALID_LOCATIONS_ORDERED, format_scenes_for_prompt, LOCATION_SCENES
from config import TAB_NPC
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


async def generate_initial_stats(char_name, house_name, house_data):
    """Асинхронно генерує стартовий профіль героя."""
    origin_region = house_data.get('Регіон', 'Вестерос')
    print(f"🎲 Генерую статистику для {char_name}...")

    valid_locations_str = ", ".join(f'"{loc}"' for loc in VALID_LOCATIONS_ORDERED)
    _scenes_block = format_scenes_for_prompt(origin_region)

    prompt = build_initial_stats_prompt(char_name, house_name, origin_region, valid_locations_str, scenes_block_str=_scenes_block)

    # Gemma 4 31B — одноразова генерація, якість важливіша за швидкість
    try:
        def _sync_gen_profile():
            return model_gm_logic.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen_profile)
        profile = clean_and_parse_json(response.text)
    except Exception as e:
        print(f"❌ Помилка генерації профілю (Gemma 4): {e}")
        profile = None
    if profile:
        profile["Ім'я"] = char_name
        profile["Дім"] = house_name
        # Validate Location — якщо ШІ написав назву регіону замість локації, ставимо першу валідну локацію регіону
        loc = profile.get("Поточне місцезнаходження", "")
        if not is_valid_location(loc):
            fallback_loc = VALID_LOCATIONS_ORDERED[0]
            print(f"⚠️ [INITIAL STATS] Невалідна локація '{loc}' → замінено на '{fallback_loc}'")
            profile["Поточне місцезнаходження"] = fallback_loc
            loc = fallback_loc
        # Auto-derive регіон — перезаписуємо відповідь ШІ гарантованим значенням
        profile["Регіон"] = get_region_for_location(loc) or origin_region
        # Гарантуємо наявність сцени — блокуємо "Невідомо"/GLOBAL/порожні значення
        curr_scene = profile.get("Поточна сцена", "")
        if not curr_scene or curr_scene.strip().lower() in ("невідомо", "global", "unknown", ""):
            profile["Поточна сцена"] = loc
        return profile
    return None


async def get_narrative_intro(profile):
    """Асинхронно генерує атмосферний вступ на основі локації та профілю гравця.
    Повертає dict: {narrative_text, action_prompt, suggested_actions}."""
    print(f"📜 Пишу вступ для {profile.get('Ім' + chr(39) + 'я')}...")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    current_location = profile.get("Поточне місцезнаходження", "Вестерос")

    prompt = build_game_intro_prompt(profile_json, current_location)  # noqa: uses narrative, no scenes needed
    try:
        def _sync_gen():
            return model.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen)
        raw_text = response.text.strip()
        parsed = clean_and_parse_json(raw_text)
        if parsed and parsed.get("narrative_text"):
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


async def background_canon_generation(excluded_name=None):
    """Асинхронний ФОНОВИЙ ПРОЦЕС: Створення кістяка світу. Завантажує ЖОРСТКИЙ КАНОН."""
    print("🌍 [CANON GEN] Завантажую канонічну базу даних...")

    def _sync_db_ops():
        import time as _time
        worksheet = db.get_sheet(TAB_NPC)
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
        from database.operations import refresh_npc_database
        await refresh_npc_database()
    else:
        print("❌ [CANON GEN] Не вдалося записати жодного канонічного NPC!")


async def populate_contextual_npcs(location, situation_context="Normal day, calm atmosphere", excluded_name=None, excluded_dead_names: set = None):
    """Асинхронно генерує NPC, які відповідають ПОТОЧНІЙ СИТУАЦІЇ в локації. Блокує канонічні імена."""
    print(f"🏘️ [LOCAL POP] Аналізую локацію: {location}...")

    def _sync_db_read():
        worksheet = db.get_sheet(TAB_NPC)
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
            from database.operations import refresh_npc_database
            await refresh_npc_database()
        else:
            print(f"⚠️ [LOCAL POP] ШІ не згенерував жодного валідного NPC для {location}.")

    except Exception as e:
        print(f"❌ [LOCAL POP AI ERROR] {e}")