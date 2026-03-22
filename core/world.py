# core/world.py
import difflib
import json
import asyncio
from database.sheets import db
from database.operations import refresh_npc_database, find_best_match
from core.ai_client import model, ask_gemini, clean_and_parse_json
from core.prompts import GAME_ERA_CONTEXT
from config import TAB_NPC
from database.canon_npc import CANON_NPCS


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
    prompt = f"""
    Write a list in Ukrainian of the 4 most famous characters from House {house_name} (Game of Thrones).
    Return ONLY a JSON array of strings.
    Example: ["Name 1", "Name 2"]
    """

    result = await ask_gemini(prompt)
    return result if result else []


async def generate_initial_stats(char_name, house_name, house_data):
    """Асинхронно генерує стартовий профіль героя."""
    origin_region = house_data.get('Регіон', 'Вестерос')
    print(f"🎲 Генерую статистику для {char_name}...")

    prompt = f"""
    <role>
Ти — Архімейстер Цитаделі та провідний Game Balance Designer для RPG "Game of Thrones". Твоя експертиза — глибоке знання лору (канону) та жорсткий математичний баланс ігрової системи. 
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

<input_data>
TIME CONTEXT: {GAME_ERA_CONTEXT}
CHARACTER: {char_name}
HOUSE: {house_name} (Origin: {origin_region})
</input_data>

<system_rules>
1. LOCATION RULES (CRITICAL): Визначай "Поточне місцезнаходження" СУВОРО за каноном першої книги (298 CE), а не за регіоном походження. (Наприклад: Jorah Mormont -> Pentos, Theon Greyjoy -> Winterfell).
2. PERMISSION TO FAIL: Якщо {char_name} є неканонічним або повністю вигаданим, не галюцинуй. Прямо скажи "Персонаж не канонічний" в аналізі та згенеруй йому стандартний профіль для його {origin_region} без історичних зв'язків.
3. МАТЕМАТИКА РУШІЯ:
   - Особисте Золото: Лорд (3000-5000), Еліта (1000-2999), Лицар (500-999), Інші (10-200).
   - Навички (0-100): 90-100 (Легенда), 70-89 (Еліта), 50-69 (Добрий рівень), 20-49 (Середній), 0-19 (Некомпетентний). Не став 100 випадково.
</system_rules>

<output_requirements>
- Твоя відповідь має бути ВИКЛЮЧНО валідним JSON-об'єктом. Жодного тексту до чи після фігурних дужок.
- Використовуй тільки одинарні лапки (') всередині текстових значень.
- Першим ключем у JSON ЗАВЖДИ має бути "thought_process", де ти виконаєш логічну перевірку перед заповненням полів.
</output_requirements>

<thought_algorithm>
Ти маєш застосувати ToT (Tree of Thoughts) та Adversarial Validation у ключі "thought_process":
1. Branch 1 (Lore Master): Визначає статус та канонічну локацію на вказаний час.
2. Branch 2 (System Balancer): Пропонує цифри статів на основі статусу (Математика Рушія).
3. Adversarial Check: Критикує гілки ("Чи не забагато золота для бастарда?", "Чи точно він у 298 році тут?").
4. Синтез: Фінальне рішення для JSON.
</thought_algorithm>

<few_shot_example>
{{
    "thought_process": "Branch 1: Jorah Mormont у 298 році — вигнанець у Пентосі. Branch 2: Як колишній лорд і лицар, має високі бойові навички (80), військовий досвід (65), але як вигнанець має мало золота (150). Adversarial Check: 150 золота підходить під правило 'Інші (10-200)'. Локація Пентос є каноном. Синтез успішний.",
    "Ім'я": "Джорах Мормонт",
    "Дім": "Мормонт",
    "Титул": "Вигнанець / Лицар",
    "Поточне місцезнаходження": "Пентос",
    "Володіння": "-",
    "Світогляд": "Відданий, меланхолійний, шукає спокути",
    "Здоров'я": 100,
    "Живучість": 100,
    "Енергія": 100,
    "Особисте Золото": 150,
    "Бойові навички": 80,
    "Військові навички": 65,
    "Інтрига": 40,
    "Управління": 50,
    "Риси": "Досвідчений воїн, Поліглот",
    "Вади": "Знеславлений, Вигнанець",
    "Репутація (Рідний регіон)": -50,
    "Репутація (Столиця)": -30,
    "Репутація (Інші регіони)": 10,
    "Статус при дворі": "Відсутній",
    "Зброя": "Довгий меч (звичайна сталь)",
    "Броня": "Кільчуга і пластини",
    "Транспорт": "Звичайний бойовий кінь",
    "Інвентар": "Похідний мішок, бурдюк, монети",
    "Ігровий час": "298 рік В.Е., 1-й місяць, День 1 (Ранок)",
    "Вороги": "Еддард Старк",
    "Друзі": "Ілліріо Мопатіс, Візеріс Таргарієн",
    "Важливі події": ""
}}
</few_shot_example>

Заповни JSON Українською мовою для наступного запиту:
    """

    profile = await ask_gemini(prompt)
    if profile:
        profile["Ім'я"] = char_name
        profile["Дім"] = house_name
        return profile
    return None


async def get_narrative_intro(profile):
    """Асинхронно генерує атмосферний вступ на основі локації та профілю гравця."""
    print(f"📜 Пишу вступ для {profile.get('Ім' + chr(39) + 'я')}...")
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    current_location = profile.get("Поточне місцезнаходження", "Вестерос")

    prompt = f"""
        <role>
Ти — Джордж Р. Р. Мартін, майстер похмурого фентезі (grimdark) та суворий Game Master RPG "Game of Thrones". Твоя мета — написати ідеальний, атмосферний пролог для гравця, суворо дотримуючись канону.
</role>

<execution_mode>
ТИ ВИКОНУЄШ ЦЕЙ ПРОМПТ ЯК ПРОГРАМУ, КРОК ЗА КРОКОМ.
</execution_mode>

<input_data>
HERO_PROFILE: {profile_json}
START_LOCATION: {current_location}
TIME_CONTEXT: {GAME_ERA_CONTEXT}
</input_data>

<system_rules>
1. ATMOSPHERE (GRIMDARK): Реалізм. Жодних "дружніх купців" чи казковості. Сенсорика обов'язкова: запах моря, гною, крові, сталі, пахощі Пентоса або льодяний холод Півночі.
2. CANON PLOT HOOKS (CRITICAL): Сцена ПОВИННА бути прив'язана до реальних подій початку першої книги (298 рік):
   - NORTH (Winterfell): Підготовка до страти дезертира Нічної Варти (Ґареда) АБО прибуття королівського кортежу.
   - ESSOS (Pentos): Ілліріо Мопатіс готує Дейнеріс до оглядин Кхалом Дрого АБО параноя Візеріса.
   - KING'S LANDING: Траур за Джоном Арреном, чутки про отруту, політичні інтриги щодо посади Правиці.
   - THE WALL: Прибуття нових рекрутів (з Тіріоном) АБО зникнення розвідників у Зачарованому Лісі.
3. CHARACTER INTEGRATION & PERMISSION TO FAIL:
   - Якщо герой КАНОНІЧНИЙ: Починай точно зі сцени його першої появи в книзі.
   - Якщо герой НЕКАНОНІЧНИЙ (вигаданий): Не вигадуй йому великої історії. Зроби його свідком або дрібним учасником вищезгаданих канонічних подій.
4. AGENCY PRESERVATION (DON'TS):
   - НІКОЛИ не пиши дії за гравця (наприклад: "Ви дістали меч і пішли").
   - Зупиняй сцену рівно в той момент, коли виникає напруга і потрібна реакція гравця.
</system_rules>

<output_requirements>
- ВИКЛЮЧНО валідний JSON.
- Усі значення текстових полів мають бути Українською мовою.
- Жодного тексту поза фігурними дужками.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень. Замінюй їх на одинарні (').
- Формат JSON має містити три ключі: "scene_planning" (твої думки), "narrative_text" (сам текст прологу, 3 абзаци) та "action_prompt" (питання до гравця).
</output_requirements>

<thought_algorithm>
У ключі "scene_planning" ти ПОВИНЕН виконати Tree of Thoughts:
1. Sensory Anchor: Які 3 головні запахи/звуки цієї локації?
2. Canon Integration: Яка подія з правил підходить цій локації та як логічно вписати туди профіль героя?
3. Agency Check: У якій точці напруги текст має обірватися, щоб дати гравцю вибір?
Після цього заповнюй інші ключі.
</thought_algorithm>

<few_shot_example>
{{
  "scene_planning": "Локація: Вінтерфелл. Сенсорика: холод, запах мокрої вовни, іржа та кров. Подія: Страта Ґареда. Герой: вигаданий найманець. Інтеграція: герой стоїть у натовпі свідків. Точка зупинки: Нед Старк заносить меч, а герой помічає злодія в натовпі.",
  "narrative_text": "Ранковий холод Півночі пробирає до самих кісток, проникаючи крізь товстий вовняний плащ. Повітря важке, воно пахне розтопленим снігом, кінським потом та вогкістю каміння. Ви стоїте на внутрішньому дворі Вінтерфелла, де зібрався мовчазний натовп. У центрі, на дерев'яній пласі, лежить змарнілий чоловік у чорному одязі — дезертир з Нічної Варти, який незв'язно бурмоче про білих блукачів.\n\nЛорд Еддард Старк височіє над ним, його обличчя нагадує витесаний з сірого граніту монумент. Він мовчки знімає з плечей важкий дворучний меч. Валирійська сталь 'Льоду' немов поглинає тьмяне світло півночі. Тиша стає абсолютною, перериваючись лише різким карканням ворон на старих мурах.\n\nРаптом, краєм ока, ви помічаєте швидкий рух. Поруч із вами хирлявий чоловік у брудному лахмітті непомітно тягнеться до кинджала під курткою, не зводячи погляду зі спини молодого Робба Старка. Сталь Еддарда злітає вгору для смертельного удару.",
  "action_prompt": "Невідомий ось-ось вихопить зброю. Викликнете варту, спробуєте знешкодити його самостійно чи розчинитесь у натовпі, уникаючи зайвих проблем?"
}}
</few_shot_example>
        """
    try:
        def _sync_gen():
            return model.generate_content(prompt)

        response = await asyncio.to_thread(_sync_gen)
        return response.text
    except Exception as e:
        print(f"❌ Помилка генерації вступу: {e}")
        return f"Ви прибули у {current_location}. Вітер дме в обличчя. Що ви робите?"


async def background_canon_generation(context_text, excluded_name=None):
    """Асинхронний ФОНОВИЙ ПРОЦЕС: Створення кістяка світу. Завантажує ЖОРСТКИЙ КАНОН."""
    print("🌍 [CANON GEN] Завантажую канонічну базу даних...")

    def _sync_db_ops():
        worksheet = db.get_sheet(TAB_NPC)
        if not worksheet: return False

        try:
            worksheet.clear()
            # ОНОВЛЕНО: Додано Scene та Inventory у заголовки
            headers = ["Location", "Scene", "Name", "Description", "Character", "Goal", "Secrets",
                       "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon", "Inventory",
                       "Reputation_Score"]
            worksheet.append_row(headers)
        except Exception as e:
            print(f"❌ [CANON GEN ERROR] Clear failed: {e}")
            return False

        rows_to_add = []
        for npc in CANON_NPCS:
            name = npc.get("Name", "Unknown")
            if excluded_name and find_best_match(name, [excluded_name], threshold=0.8):
                continue

            # ОНОВЛЕНО: Додано Scene (індекс 1) та Inventory (індекс 12)
            row = [
                npc.get("Location", "Westeros"),
                npc.get("Scene", "Невідомо"),
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
                npc.get("Reputation_Score", 0)
            ]
            rows_to_add.append(row)

        if rows_to_add:
            try:
                worksheet.append_rows(rows_to_add)
                return len(rows_to_add)
            except Exception as e:
                print(f"❌ [CANON GEN SAVE ERROR] {e}")
                return False
        return False

    added_count = await asyncio.to_thread(_sync_db_ops)
    if added_count:
        print(f"✅ [CANON GEN] Світ заселено! Додано {added_count} канонічних легенд.")
        from database.operations import refresh_npc_database
        await refresh_npc_database()


async def populate_contextual_npcs(location, situation_context="Normal day, calm atmosphere", excluded_name=None):
    """Асинхронно генерує NPC, які відповідають ПОТОЧНІЙ СИТУАЦІЇ в локації. Блокує канонічні імена."""
    print(f"🏘️ [LOCAL POP] Аналізую локацію: {location}...")

    def _sync_db_read():
        worksheet = db.get_sheet(TAB_NPC)
        if not worksheet: return None, None, []
        canon_names_local = []
        try:
            all_rows = worksheet.get_all_values()
            # ОНОВЛЕНО: Тепер Is_Canon під індексом 11, бо ми додали Scene під індексом 1
            canon_col_idx = 11
            headers = all_rows[0]
            preserved_rows = [headers]

            for row in all_rows[1:]:
                if len(row) > canon_col_idx and str(row[canon_col_idx]).upper() == "TRUE":
                    preserved_rows.append(row)
                    canon_name = str(row[2]).strip() # ОНОВЛЕНО: Ім'я тепер під індексом 2
                    if canon_name:
                        canon_names_local.append(canon_name)

            worksheet.clear()
            worksheet.update(preserved_rows)
            return worksheet, preserved_rows, canon_names_local
        except Exception as e:
            print(f"❌ [LOCAL POP ERROR] Cleanup failed: {e}")
            return None, None, []

    worksheet, _, canon_names = await asyncio.to_thread(_sync_db_read)
    if not worksheet: return

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

        OUTPUT STRICTLY VALID JSON ARRAY ONLY. NO MARKDOWN. NO BACKTICKS. NO CODE FENCES. START WITH [ AND END WITH ]. NO TEXT BEFORE OR AFTER.
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

            # ОНОВЛЕНО: Додано Scene, Inventory та Reputation_Score
            from database.canon_npc import _map_relation_to_score
            initial_rep = _map_relation_to_score(npc.get("Relation_Player", "Neutral"))
            row = [
                location,
                "Невідомо",
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
                initial_rep
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