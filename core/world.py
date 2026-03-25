# core/world.py
import difflib
import json
import asyncio
from database.sheets import db
from database.operations import refresh_npc_database, find_best_match
from core.ai_client import model, ask_gemini, clean_and_parse_json
from core.prompts import GAME_ERA_CONTEXT
from core.world_constants import get_region_for_location
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
    "Регіон": "Вільні Міста",
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
        # Auto-derive регіон — перезаписуємо відповідь ШІ гарантованим значенням
        loc = profile.get("Поточне місцезнаходження", "")
        profile["Регіон"] = get_region_for_location(loc) or origin_region
        return profile
    return None


async def get_narrative_intro(profile):
    """Асинхронно генерує атмосферний вступ на основі локації та профілю гравця.
    Повертає dict: {narrative_text, action_prompt, suggested_actions}."""
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
2. CANON PLOT HOOKS (CRITICAL): Сцена ПОВИННА бути прив'язана до реальних подій початку першої книги (298 рік). Обери підходящий hook для локації героя:
   - NORTH / WINTERFELL: Підготовка до страти дезертира Нічної Варти (Ґаред) АБО прибуття королівського кортежу Баратеона.
   - THE WALL / CASTLE BLACK: Прибуття нових рекрутів (серед них Тіріон) АБО зникнення розвідників у Зачарованому Лісі.
   - KING'S LANDING: Траур за Джоном Арреном, чутки про отруту, інтриги щодо посади Правиці Короля.
   - THE VALE / EYRIE: Смерть Джона Аррена щойно оголошена — Лайса замкнулась з дитиною в Орлиному Гнізді, звинувачення проти Ланністерів ширяться по Долині.
   - THE REACH / HIGHGARDEN: Лорд Мейс Тайрелл лавірує між Баратеоном і Ланністером — кур'єри скачуть день і ніч, двір гуде від чуток.
   - DORNE / SUNSPEAR: Принц Доран мовчки скорботить за Елією. Оберін Мартелл повертається додому. Ненависть до Ланністерів — відкрита рана в кожному домі.
   - IRON ISLANDS / PYKE: Балон Грейджой береже старі образи після Залізного Повстання. Острів'яни нишпорять і чекають слабкості материка.
   - STORMLANDS / STORM'S END: Замок напівпорожній — Ренлі в Королівській Гавані. Банерлорди не знають кому клясти вірність після смерті Аррена.
   - RIVERLANDS / RIVERRUN: Лорд Хостер Талі хворіє. Кетелін Старк щойно вирушила на північ з тривожними звістками. Замок тихий, але напружений.
   - WESTERLANDS / CASTERLY ROCK: Тайвін Ланністер плете тіньову мережу. Підозрілість до чужинців максимальна — кожен гість може бути шпигуном.
   - ESSOS / PENTOS: Ілліріо Мопатіс готує Дейнеріс до оглядин Кхалом Дрого АБО параноя Візеріса наростає з кожним днем.
   - ESSOS / BRAAVOS або інші вільні міста: Тінь Залізного Трону довга. Вестеросські вигнанці і шпигуни — скрізь.
   - БУДЬ-ЯКА ІНША ЛОКАЦІЯ: Чутки про смерть Джона Аррена та виклик Еддарда Старка до Королівської Гавані ширяться по всіх Семи Королівствах — навіть найвіддаленіші замки відчувають наближення бурі.
3. CHARACTER INTEGRATION:
   - Якщо герой КАНОНІЧНИЙ: Починай точно зі сцени його першої появи в книзі.
   - Якщо герой НЕКАНОНІЧНИЙ (вигаданий): Зроби його свідком або дрібним учасником вищезгаданого canon hook.
4. AGENCY PRESERVATION:
   - НІКОЛИ не пиши дії за гравця.
   - Зупиняй сцену рівно в той момент, коли виникає напруга і потрібна реакція гравця.
</system_rules>

<thought_algorithm>
У ключі "reasoning" виконай Tree of Thoughts ПЕРЕД написанням прологу:
1. Sensory Anchor: Які 3 головні запахи/звуки цієї локації?
2. Canon Integration: Який hook підходить локації та як органічно вписати туди профіль героя?
3. Agency Check: В якій точці напруги текст має обірватися, щоб дати гравцю вибір?
4. Actions Draft: 4 принципово різні реакції гравця на цей момент (агресивна / соціальна / обережна / дика).
Після цього заповнюй інші ключі.
</thought_algorithm>

<output_requirements>
- ВИКЛЮЧНО валідний JSON. Жодного тексту поза фігурними дужками.
- Усі значення текстових полів — Українською мовою.
- НІКОЛИ не використовуй подвійні лапки (") всередині значень рядків. Замінюй їх на одинарні (').
- Чотири обов'язкових ключі: "reasoning", "narrative_text", "action_prompt", "suggested_actions".
</output_requirements>

ВІДПОВІДАЙ СТРОГО У ФОРМАТІ JSON:
{{
  "reasoning": "Твоє внутрішнє міркування (Sensory Anchor / Canon Hook / Agency Check / Actions Draft)",
  "narrative_text": "Атмосферний пролог, 3 абзаци, Ukrainian, без чисел у тексті",
  "action_prompt": "Питання або ситуація що вимагає негайного вибору гравця (1-2 речення)",
  "suggested_actions": [
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}},
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}},
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}},
    {{"button": "Короткий label до 5 слів", "intent": "Розгорнутий намір від ПЕРШОЇ ОСОБИ, 10-15 слів, конкретні деталі"}}
  ]
}}

<few_shot_example>
{{
  "reasoning": "Локація: Вінтерфелл. Сенсорика: холод, запах мокрої вовни, іржа та кров на снігу. Canon hook: Страта Ґареда. Герой: вигаданий найманець. Інтеграція: свідок у натовпі. Точка зупинки: Нед заносить меч, герой помічає підозрілого з кинджалом. Дії: викликати варту / зупинити самому / відступити / непомітно стежити.",
  "narrative_text": "Ранковий холод Півночі пробирає до самих кісток, проникаючи крізь товстий вовняний плащ. Повітря важке — запах розтопленого снігу, кінського поту і вогкого каміння. Ви стоїте на внутрішньому дворі Вінтерфелла, де зібрався мовчазний натовп. У центрі, на дерев'яній плаxі, лежить змарнілий чоловік у чорному — дезертир з Нічної Варти, що незв'язно бурмоче про білих блукачів.\n\nЛорд Еддард Старк височіє над ним, його обличчя вирізьблене з сірого граніту. Він мовчки знімає важкий дворучний меч. Валірійська сталь 'Льоду' поглинає тьмяне світло. Тиша стає абсолютною, перериваючись лише різким карканням ворон.\n\nРаптом краєм ока ви помічаєте рух. Поруч хирлявий чоловік у брудному лахмітті непомітно тягнеться до кинджала під курткою, не зводячи погляду зі спини молодого Робба Старка. Сталь Еддарда злітає вгору.",
  "action_prompt": "Невідомий ось-ось вихопить зброю. Що ви зробите?",
  "suggested_actions": [
    {{"button": "Гукнути варту", "intent": "Я різко повертаюсь і кричу варті, вказуючи на підозрілого чоловіка в натовпі."}},
    {{"button": "Схопити його самому", "intent": "Я мовчки і швидко підходжу ззаду та перехоплюю руку зловмисника, не даючи вихопити кинджал."}},
    {{"button": "Відступити в натовп", "intent": "Я непомітно відсуваюсь подалі, розчиняючись у натовпі і спостерігаючи що буде далі."}},
    {{"button": "Стежити непомітно", "intent": "Я залишаюсь на місці, але фіксую обличчя підозрілого і готуюсь діяти якщо він справді нападе."}}
  ]
}}
</few_shot_example>
"""
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


async def populate_contextual_npcs(location, situation_context="Normal day, calm atmosphere", excluded_name=None):
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

    prompt = f"""
        ROLE: Narrative Designer for Game of Thrones (Grimdark Fantasy).
        TASK: Populate the current location: "{location}" with 6-8 background NPCs.

        === CURRENT SITUATION (CRITICAL) ===
        {situation_context}

        === CRITICAL NAMING RULES (ANTI-CANON) ===
        1. BACKGROUND ONLY: You are generating nobodies, commoners, guards, or local minor merchants. DO NOT generate main characters from the books/show.
        2. NO GREAT HOUSES: It is STRICTLY FORBIDDEN to use these surnames: Stark, Lannister, Targaryen, Baratheon, Tyrell, Greyjoy, Martell, Arryn, Tully, Bolton, Mormont.
        3. BLACKLIST: Absolutely DO NOT use any of these specific names or variations of them: {blacklist_str}.
        4. LORE-FRIENDLY NAMES (CRITICAL): Generate original names that fit the Game of Thrones / ASOIAF universe ONLY.
           - Westerosi names: Alys, Brynden, Maegor, Cassana, Willem, Harren, Tytos, Jocelyn, Osmund, Ronnet
           - Essosi names: Illyrio, Syrio, Belwas, Talea, Qotho, Malazza
           - ABSOLUTELY FORBIDDEN: Modern real-world names (Софія, Микола, Олена, Дмитро, Ігор, Віра, etc.). These break immersion completely.
           - Test: If a name could belong to a person in modern Ukraine/Russia/Europe, it is WRONG. Use medieval fantasy names.

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

            # ОНОВЛЕНО: Додано Scene, Inventory, Reputation_Score та Region
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