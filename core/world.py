# core/world.py
import difflib
import json
from database.sheets import db
from database.operations import refresh_npc_database, find_best_match
from core.ai_client import model, ask_gemini, clean_and_parse_json
from core.prompts import GAME_ERA_CONTEXT
from config import TAB_NPC
from database.canon_npc import CANON_NPCS


def get_canon_characters(house_name):
    """Повертає список відомих канонічних персонажів для конкретного дому."""
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
    prompt = f"""
    Write a list in Ukrainian of the 4 most famous characters from House {house_name} (Game of Thrones).
    Return ONLY a JSON array of strings.
    Example: ["Name 1", "Name 2"]
    """
    result = ask_gemini(prompt)
    return result if result else []


def generate_initial_stats(char_name, house_name, house_data):
    """Генерує стартовий профіль героя."""
    origin_region = house_data.get('Регіон', 'Вестерос')
    print(f"🎲 Генерую статистику для {char_name}...")

    prompt = f"""
    Create a starting profile for the RPG “Game of Thrones.”
    TIME CONTEXT: {GAME_ERA_CONTEXT}

    CHARACTER: {char_name}
    HOUSE: {house_name} (Origin: {origin_region})

    === LOCATION RULES (CRITICAL) ===
    Determine “Current Location” based on the CANON of the first book (298 CE), not the region of origin.
    Examples: Jorah Mormont -> Pentos. Theon Greyjoy -> Winterfell.

    Заповни JSON Українською:
    {{
        "Ім'я": "{char_name}",
        "Дім": "{house_name}",
        "Титул": "Лорд/Леді/Бастард/Лицар/Вигнанець",
        "Поточне місцезнаходження": "Назва локації (Замок або Місто)",
        "Володіння": "Назва замку або -",
        "Світогляд": "Короткий опис характеру",
        "Здоров'я": 100,
        "Живучість": 100,
        "Енергія": 100,
        "Особисте Золото": "Integer (100-5000 в залежності від статусу)",
        "Бойові навички": "Integer (0-100)",
        "Військові навички": "Integer (0-100)",
        "Інтрига": "Integer (0-100)",
        "Управління": "Integer (0-100)",
        "Риси": "2-3 риси характеру(Позитивні)",
        "Вади": "1-2 вади",
        "Репутація (Рідний регіон)": "Integer (0-100)",
        "Репутація (Столиця)": "Integer (-50 до 100)",
        "Репутація (Інші регіони)": "Integer (-50 до 100)",
        "Статус при дворі": "Текст",
        "Зброя": "Текст",
        "Броня": "Текст",
        "Транспорт": "Текст",
        "Інвентар": "Список речей через кому",
        "Ігровий час": "298 рік В.Е., 1-й місяць, День 1 (Ранок)",
        "Вороги": "Імена або фракції",
        "Друзі": "Імена або фракції",
        "Важливі події": "Текст, на початку пусто"
    }}
    """
    profile = ask_gemini(prompt)
    if profile:
        profile["Ім'я"] = char_name
        profile["Дім"] = house_name
        return profile
    return None


def get_narrative_intro(profile):
    """Генерує атмосферний вступ на основі локації та профілю гравця."""
    print(f"📜 Пишу вступ для {profile.get('Ім' + chr(39) + 'я')}...")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    current_location = profile.get("Поточне місцезнаходження", "Вестерос")

    prompt = f"""
        YOU ARE THE AUTHOR OF THE NOVEL “A GAME OF THRONES” (GEORGE MARTIN).
        Your goal: Write the first scene for the character (Prologue).

        === HERO ===
        {profile_json}

        === START LOCATION ===
        {current_location}
        (Describe this location as atmospherically as possible, using the canon of 298 B.E.)

        === TIME CONTEXT ===
        {GAME_ERA_CONTEXT}

        === TEXT REQUIREMENTS (GRIMDARK INTRO) ===
        1. **Atmosphere:** Start with sensory details. Smell (sea, manure, incense, steel), temperature (the cold of the North or the heat of Essos), sounds.
        2. **Style:** Gloomy realism. No “friendly merchants.” If it's Pentos, it smells of spices and slavery. If it's Winterfell, it smells of wolves and winter.
        3. **The character's situation:** - If it's an outcast (like Jorah), emphasize their longing for home or poverty.
    - If it's a lord, emphasize the burden of responsibility and the hypocrisy of the court.

    4. **PLOT HOOK (INCITING INCIDENT) - CANON:**
           You must tie the scene to REAL events from the beginning of the first book, depending on the location:

    - **NORTH (WINTERFELL):** * Main event: Lord Eddard prepares to execute a deserter from the Night's Watch (Gareth). 
             * Or: News arrives that King Robert's huge procession is on its way.

           - **ESSOS (Pentos):** * Main event: Illyrio Mopatis prepares Daenerys for her viewing by Khal Drogo.
             * Or: Viserys is nervous about the delay of the wedding.

    - **KING'S LANDING:** * Main event: The court is in mourning. Hand of the King Jon Arryn has died suddenly. Rumors of poison.
             * Or: Intrigue over who will become the new Hand of the King.

           - **THE WALL (Black Castle):** * Main event: Arrival of new recruits (including Tyrion as a guest).
             * Or: Scouts have disappeared in the Haunted Forest. Strange corpses have been found.

           **IF THIS IS A CANON CHARACTER ({profile.get("Ім'я")}):** Start with the scene where we first meet him in the book.

           **IF IT IS A FICTIONAL CHARACTER:**
           Include him in these events as a witness or participant (for example, he stands in the crowd during the execution or serves at court during the mourning).

        5. **DON'TS:**
           - NEVER write actions for the character (“You took the sword”).
           - Stop the scene at the exact moment when the character has to react.

        === FINALE (CRITICALLY IMPORTANT) ===
        Your response MUST end with a QUESTION that presents the player with a specific choice.
    - BAD: “What do you do?” (Too vague).
    - GOOD: "The guard squints suspiciously, waiting for an explanation. Will you show him the House seal or try to bribe him?"
    - GOOD: “The screams are getting closer. Will you hide in the shadows or face the danger?”

    Write a creative text (3 paragraphs), Ukrainian language only.
        """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Помилка генерації вступу: {e}")
        return f"Ви прибули у {current_location}. Вітер дме в обличчя. Що ви робите?"


def background_canon_generation(context_text, excluded_name=None):
    """ФОНОВИЙ ПРОЦЕС: Створення кістяка світу. Завантажує ЖОРСТКИЙ КАНОН."""
    print("🌍 [CANON GEN] Завантажую канонічну базу даних...")
    worksheet = db.get_sheet(TAB_NPC)
    if not worksheet: return

    try:
        worksheet.clear()
        # НАШІ ОНОВЛЕНІ ЗАГОЛОВКИ З MEMORY ANCHOR
        headers = ["Location", "Name", "Description", "Character", "Goal", "Secrets",
                   "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon"]
        worksheet.append_row(headers)
    except Exception as e:
        print(f"❌ [CANON GEN ERROR] Clear failed: {e}")
        return

    rows_to_add = []

    for npc in CANON_NPCS:
        name = npc.get("Name", "Unknown")
        # Перевірка, чи це не сам гравець
        if excluded_name and find_best_match(name, [excluded_name], threshold=0.8):
            continue

        row = [
            npc.get("Location", "Westeros"),
            name,
            npc.get("Description", "-"),
            npc.get("Character", "-"),
            npc.get("Goal", "-"),
            npc.get("Secrets", "-"),
            npc.get("Relation_Player", "Neutral"),
            npc.get("Memory_Anchor", "-"),
            npc.get("Relation_NPCs", "-"),
            npc.get("Status", "Active"),
            npc.get("Is_Canon", "TRUE")
        ]
        rows_to_add.append(row)

    if rows_to_add:
        try:
            worksheet.append_rows(rows_to_add)
            print(f"✅ [CANON GEN] Світ заселено! Додано {len(rows_to_add)} канонічних легенд.")
            from database.operations import refresh_npc_database
            refresh_npc_database()
        except Exception as e:
            print(f"❌ [CANON GEN SAVE ERROR] {e}")


def populate_contextual_npcs(location, situation_context="Normal day, calm atmosphere", excluded_name=None):
    """Генерує NPC, які відповідають ПОТОЧНІЙ СИТУАЦІЇ в локації. Блокує канонічні імена."""
    print(f"🏘️ [LOCAL POP] Аналізую локацію: {location}...")
    worksheet = db.get_sheet(TAB_NPC)
    if not worksheet: return

    canon_names = []  # Збираємо чорний список імен

    try:
        all_rows = worksheet.get_all_values()
        canon_col_idx = 10
        headers = all_rows[0]
        preserved_rows = [headers]

        for row in all_rows[1:]:
            if len(row) > canon_col_idx and str(row[canon_col_idx]).upper() == "TRUE":
                preserved_rows.append(row)
                # Зберігаємо ім'я канонічного персонажа (колонка Name зазвичай має індекс 1)
                canon_name = str(row[1]).strip()
                if canon_name:
                    canon_names.append(canon_name)

        worksheet.clear()
        worksheet.update(preserved_rows)
    except Exception as e:
        print(f"❌ [LOCAL POP ERROR] Cleanup failed: {e}")
        return

    # Формуємо рядок чорного списку для промпту (беремо перші 50, щоб не перевантажити промпт, або всі)
    blacklist_str = ", ".join(canon_names)

    prompt = f"""
        ROLE: Narrative Designer for Game of Thrones (Grimdark Fantasy).
        TASK: Populate the current location: "{location}" with 10-12 background NPCs.

        === CURRENT SITUATION (CRITICAL) ===
        {situation_context}

        === CRITICAL NAMING RULES (ANTI-CANON) ===
        1. BACKGROUND ONLY: You are generating nobodies, commoners, guards, or local minor merchants. DO NOT generate main characters from the books/show.
        2. NO GREAT HOUSES: It is STRICTLY FORBIDDEN to use these surnames: Stark, Lannister, Targaryen, Baratheon, Tyrell, Greyjoy, Martell, Arryn, Tully, Bolton, Mormont.
        3. BLACKLIST: Absolutely DO NOT use any of these specific names or variations of them: {blacklist_str}.
        4. LORE-FRIENDLY: Generate original, region-appropriate names (e.g., Pentoshi names for Pentos, Northmen names for the North).

        INSTRUCTION:
        1. **Adapt to the Situation:** - If context says "War/Siege" -> Generate wounded soldiers, starving refugees, looting mercenaries.
           - If context says "Festival/Tourney" -> Generate drunk knights, pickpockets, singers.
           - If context says "Mourning" -> Generate silent sisters, crying servants, paranoid guards.
        2. **Diverse Cast:** Do not just make 10 guards. We need beggars, nobles, merchants, criminals.
        3. **Relationships:** Their "Goal" and "Secrets" must be tied to the Current Situation.

        REQUIREMENTS:
        - Create a DIVERSE mix (Social standing, professions, hostility).
        - "Secrets" must be interesting plot hooks, but not break canonical story.
        - Output 'Location' field in UKRAINIAN language (e.g., 'Вінтерфел', 'Пентос', 'Стіна').

        === LANGUAGE REQUIREMENT (CRITICAL) ===
        Responce should be in UKRAINIAN language only

        OUTPUT JSON ARRAY ONLY:
        [
          {{
            "Name": "Name",
            "Description": "Atmospheric visual description",
            "Character": "Personality traits",
            "Goal": "Current desire",
            "Secrets": "Hidden info",
            "Relation_Player": "Initial reaction",
            "Memory_Anchor": "-",
            "Relation_NPCs": "Connection to local groups"
          }}
        ]
        """
    try:
        # Виклик моделі
        response = model.generate_content(prompt)
        npc_list = clean_and_parse_json(response.text)

        if not npc_list: return

        new_rows = []
        for npc in npc_list:
            ai_gen_name = npc.get("Name", "Unknown").strip()

            # 1. Перевірка на excluded_name (Ім'я Гравця)
            if excluded_name and find_best_match(ai_gen_name, [excluded_name], threshold=0.8):
                continue

            # 2. АНТИ-КАНОН ФІЛЬТР (Fuzzy Matching)
            # Перевіряємо, чи не згенерував ШІ щось дуже схоже на канонічне ім'я (наприклад "Джон Сноу" або "Арія")
            is_canon_clone = False
            if canon_names:
                # cutoff=0.7 означає 70% схожості. Відловить "Робб Старк" якщо є "Роб Старк".
                matches = difflib.get_close_matches(ai_gen_name, canon_names, n=1, cutoff=0.7)
                if matches:
                    is_canon_clone = True
                    print(
                        f"🛡️ [ANTI-CANON FILTER] Заблоковано генерацію '{ai_gen_name}'. Занадто схоже на канон '{matches[0]}'.")

            # Перевірка на заборонені прізвища (на випадок, якщо ШІ вигадає "Васю Ланністера")
            forbidden_houses = ["старк", "ланністер", "таргарієн", "баратеон", "тірелл", "грейджой", "мартелл", "аррен",
                                "таллі", "болтон", "мормонт", "кхал"]
            if any(house in ai_gen_name.lower() for house in forbidden_houses):
                is_canon_clone = True
                print(f"🛡️ [ANTI-CANON FILTER] Заблоковано '{ai_gen_name}' через використання прізвища Великого Дому.")

            if is_canon_clone:
                continue  # Пропускаємо цього NPC і не додаємо в базу!

            row = [
                location, ai_gen_name, npc.get("Description", "-"), npc.get("Character", "-"),
                npc.get("Goal", "-"), npc.get("Secrets", "-"), npc.get("Relation_Player", "Neutral"),
                npc.get("Memory_Anchor", "-"), npc.get("Relation_NPCs", "-"), "Active", "FALSE"
            ]
            new_rows.append(row)

        if new_rows:
            worksheet.append_rows(new_rows)
            print(f"✅ [LOCAL POP] Локацію {location} заселено ({len(new_rows)} нових NPC).")
            refresh_npc_database()
        else:
            print(f"⚠️ [LOCAL POP] ШІ не згенерував жодного валідного NPC для {location}.")

    except Exception as e:
        print(f"❌ [LOCAL POP AI ERROR] {e}")