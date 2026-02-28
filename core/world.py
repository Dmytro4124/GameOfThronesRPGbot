# core/world.py
import json
from database.sheets import db
from database.operations import refresh_npc_database, find_best_match
from core.ai_client import model, ask_gemini, clean_and_parse_json
from core.prompts import GAME_ERA_CONTEXT
from config import TAB_NPC


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

    === TIME CONTEXT ===
    {GAME_ERA_CONTEXT}

    === TEXT REQUIREMENTS (GRIMDARK INTRO) ===
    1. **Atmosphere:** Start with sensory details. Smell, temperature, sounds.
    2. **Style:** Gloomy realism.
    3. **PLOT HOOK (INCITING INCIDENT) - CANON:** Tie the scene to REAL events from the beginning of the first book.
    4. **DON'TS:** NEVER write actions for the character. Stop the scene at the exact moment when the character has to react.

    === FINALE (CRITICALLY IMPORTANT) ===
    Your response MUST end with a QUESTION that presents the player with a specific choice.

    Write a creative text (3 paragraphs), Ukrainian language only.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Помилка генерації вступу: {e}")
        return f"Ви прибули у {current_location}. Вітер дме в обличчя. Що ви робите?"


def background_canon_generation(context_text, excluded_name=None):
    """ФОНОВИЙ ПРОЦЕС: Створення кістяка світу. Генерує ключових канонічних NPC."""
    print("🌍 [CANON GEN] Починаю створення глобального світу...")
    worksheet = db.get_sheet(TAB_NPC)
    if not worksheet: return

    try:
        worksheet.clear()
        headers = ["Location", "Name", "Description", "Character", "Goal", "Secrets", "Relation_Player",
                   "Relation_NPCs", "Status", "Is_Canon"]
        worksheet.append_row(headers)
    except Exception as e:
        print(f"❌ [CANON GEN ERROR] Clear failed: {e}")
        return

    regions_tasks = [
        ("The North & The Wall", "Starks, Night's Watch, Boltons"),
        ("King's Landing & South", "Lannisters, Baratheons, Tyrells, Small Council"),
        ("Essos & Across the Sea", "Targaryens, Dothraki, Illyrio")
    ]

    all_npcs = []
    for region, focus in regions_tasks:
        prompt = f"""
        ROLE: Game of Thrones Lore Keeper (Year 298 AC).
        TASK: Generate a JSON list of KEY CANON CHARACTERS (Year 298 AC) for: {region}.
        FOCUS ON: {focus}.

        === CURRENT TIMELINE & SITUATION ===
        {context_text}

        Generate 25-30 most important characters. Ukrainian language only.
        OUTPUT JSON ARRAY ONLY.
        """
        try:
            response = model.generate_content(prompt)
            data = clean_and_parse_json(response.text)
            if data and isinstance(data, list):
                all_npcs.extend(data)
        except Exception as e:
            print(f"⚠️ [CANON GEN] Skip region {region}: {e}")

    if all_npcs:
        rows_to_add = []
        for npc in all_npcs:
            ai_gen_name = npc.get("Name", "Unknown")
            if excluded_name and find_best_match(ai_gen_name, [excluded_name], threshold=0.8):
                continue

            row = [
                npc.get("Location", "Westeros"), ai_gen_name, npc.get("Description", "-"),
                npc.get("Character", "-"), npc.get("Goal", "-"), npc.get("Secrets", "-"),
                npc.get("Relation_Player", "Neutral"), npc.get("Relation_NPCs", "-"), "Active", "TRUE"
            ]
            rows_to_add.append(row)

        try:
            worksheet.append_rows(rows_to_add)
            print(f"✅ [CANON GEN] Світ заселено! Додано {len(rows_to_add)} легенд.")
            refresh_npc_database()
        except Exception as e:
            print(f"❌ [CANON GEN SAVE ERROR] {e}")


def populate_contextual_npcs(location, situation_context="Normal day, calm atmosphere", excluded_name=None):
    """Генерує NPC, які відповідають ПОТОЧНІЙ СИТУАЦІЇ в локації."""
    print(f"🏘️ [LOCAL POP] Аналізую локацію: {location}...")
    worksheet = db.get_sheet(TAB_NPC)
    if not worksheet: return

    try:
        all_rows = worksheet.get_all_values()
        canon_col_idx = 9
        headers = all_rows[0]
        preserved_rows = [headers]

        for row in all_rows[1:]:
            if len(row) > canon_col_idx and str(row[canon_col_idx]).upper() == "TRUE":
                preserved_rows.append(row)

        worksheet.clear()
        worksheet.update(preserved_rows)
    except Exception as e:
        print(f"❌ [LOCAL POP ERROR] Cleanup failed: {e}")
        return

    prompt = f"""
    ROLE: Narrative Designer for Game of Thrones.
    TASK: Populate the current location: "{location}" with 10-12 background NPCs.
    === CURRENT SITUATION ===
    {situation_context}

    OUTPUT JSON ARRAY ONLY. Ukrainian language.
    """
    try:
        response = model.generate_content(prompt)
        npc_list = clean_and_parse_json(response.text)

        if not npc_list: return

        new_rows = []
        for npc in npc_list:
            ai_gen_name = npc.get("Name", "Unknown")
            if excluded_name and find_best_match(ai_gen_name, [excluded_name], threshold=0.8):
                continue

            row = [
                location, ai_gen_name, npc.get("Description", "-"), npc.get("Character", "-"),
                npc.get("Goal", "-"), npc.get("Secrets", "-"), npc.get("Relation_Player", "Neutral"),
                npc.get("Relation_NPCs", "-"), "Active", "FALSE"
            ]
            new_rows.append(row)

        worksheet.append_rows(new_rows)
        print(f"✅ [LOCAL POP] Локацію {location} заселено ({len(new_rows)} NPC).")
        refresh_npc_database()
    except Exception as e:
        print(f"❌ [LOCAL POP AI ERROR] {e}")