import telebot
from telebot import types
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google.api_core import exceptions as google_exceptions
import google.generativeai as genai
import json
import time
import re
import os
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# ================= КОНФІГУРАЦІЯ =================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Назви аркушів в гугл таблицях
TAB_HOUSES = 'Доми'  # Аркуш зі списком домів
TAB_CHARACTER = 'CharacterSheet'  # Аркуш персонажа
TAB_KNOWLEDGE = 'KnowledgeBase'
TAB_USERS = 'Users_DB'

# Файл з ключами
CREDENTIALS_FILE = 'credentials.json'

# ================= КОНТЕКСТ СВІТУ =================

GAME_ERA_CONTEXT = """
=== TIMELINE: year 298 (End of the Long Summer) ===
The world stands still in anticipation of disaster. “Winter is coming,” and you can feel it in the air.

1. POLITICAL SITUATION (POWDER KEG):
- Iron Throne: Robert Baratheon. The former “Demon of the Trident” who was feared by all is now fat,  perpetually drunk, and indifferent to ruling. He hates the Targaryens with all his heart. The country is in debt to the Lannisters, the Iron Bank, and others.
- Queen: Cersei Lannister. She surrounds the court with her people. The Lannisters behave as if they are already in power.
- Hand of the King: Jon Arryn died of a fever. Now the king needs a replacement.
- Current Main Event: A huge royal procession is slowly crawling along the King's Road to the North, to Winterfell, and will arrive in a few days.

2. HIDDEN THREATS (FOR GM ONLY - PLAYERS SHOULD NOT KNOW THIS):
- Secret: The queen's children are bastards from incest with Jaime (this is the most dangerous secret in the world).
- Secret 2: Jon Arryn was poisoned by Lysa Arryn in collusion with Petyr Baelish.
- North: The Night's Watch is weaker than ever. Scouts are disappearing beyond the Wall. Old people scare children with tales of the Others, but the lords do not believe it.
- Essos: Viserys Targaryen sells his sister Daenerys to Khal Drogo. There are no dragons yet (they are still petrified eggs).

3. WORLD ATMOSPHERE (GRIMDARK):
- Economy: Grain prices are rising. Peasants are hoarding supplies, fearing a long winter.
- Law: The roads are dangerous. “Bandits lurk behind every bush.” Knights are often no better than bandits.
- Mood: Anxiety. There is a smell of war in the air, although the swords are still in their sheaths.

IMPORTANT FOR THE PLOT:
- Ned Stark is still alive and is in Winterfell, having received news of a deserter from the night watch.
- The War of the Five Kings HAS NOT YET BEGUN.
- DRAGONS: Extinct. Illyrio currently holds three STONE EGGS. They are fossils. They cannot move, hatch, or hiss.
- MAGIC: Extremely rare and subtle. No fireballs, no resurrection (yet).
"""

# ================= ІНІЦІАЛІЗАЦІЯ =================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    # Вибір основної моделі тут
    model_name='gemma-3-27b-it',
)
model_worker = genai.GenerativeModel(
    # Вибір технічної моделі тут
    model_name='gemma-3-4b-it',
)

# Збереження стану гравців у пам'яті
user_sessions = {}


def get_main_menu():
    """Кнопки меню"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_profile = types.KeyboardButton("📜 Профіль")
    btn_inv = types.KeyboardButton("🎒 Інвентар")
    btn_roll = types.KeyboardButton("🎲 Кинути кубик")
    btn_help = types.KeyboardButton("🆘 Допомога")
    btn_restart = types.KeyboardButton("🔄 Рестарт")

    markup.add(btn_profile, btn_inv, btn_roll, btn_help, btn_restart)
    return markup


import random


def apply_system_impacts(profile, ai_impacts):
    """
    Приймає абстрактні теги від АІ і перетворює їх на конкретну математику.
    Повертає оновлений профіль і лог змін.
    """
    logs = []

    # === 1. ОБРОБКА ЧАСУ ===
    # Формат часу в профілі: "Рік 298, Місяць 1, День 1, Ранок"
    # Логіка зміни доби: Ранок -> День -> Вечір -> Ніч -> Ранок (+1 день)
    time_str = profile.get("Ігровий час", "День 1, Ранок")
    time_tag = ai_impacts.get("time_passed", "none")
    times_of_day = ["Ранок", "День", "Вечір", "Ніч"]

    if time_tag != "none":
        # 1. Розбираємо поточний час
        import re
        day_match = re.search(r'День (\d+)', time_str)
        if not day_match: day_match = re.search(r'(\d+)', time_str)
        current_day = int(day_match.group(1)) if day_match else 1

        current_phase = "Ранок"
        for t in times_of_day:
            if t in time_str:
                current_phase = t
                break

        # 2. Логіка зміни (short взагалі ігноруємо)
        new_phase = current_phase
        new_day = current_day

        if time_tag == "short":
            # Діалог = час стоїть на місці
            pass

        elif time_tag == "medium":
            try:
                idx = times_of_day.index(current_phase)
                if idx + 1 < len(times_of_day):
                    new_phase = times_of_day[idx + 1]
                else:
                    new_phase = "Ранок"
                    new_day += 1
            except:
                pass

        elif time_tag == "long":
            try:
                idx = times_of_day.index(current_phase)
                if idx + 2 < len(times_of_day):
                    new_phase = times_of_day[idx + 2]
                else:
                    new_phase = "Ранок"
                    new_day += 1
            except:
                pass

        elif time_tag == "sleep" or time_tag == "days":
            new_phase = "Ранок"
            new_day += 1

        # 3. ЗАПИСУЄМО І ЛОГУЄМО ТІЛЬКИ ЯКЩО БУЛИ ЗМІНИ
        # Якщо фаза та день ті самі - нічого не робимо і нічого не пишемо в чат
        if new_phase != current_phase or new_day != current_day:
            profile["Ігровий час"] = f"298 рік В.Е., 1-й місяць, День {new_day}, {new_phase}"

            # Формуємо лог
            if new_day != current_day:
                logs.append(f"⏳ Час: {current_phase} -> {new_phase} (Новий день!)")
            else:
                logs.append(f"⏳ Час: {current_phase} -> {new_phase}")

    # === 2. ОБРОБКА ЗДОРОВ'Я ===
    damage_tag = ai_impacts.get("health_impact", "none")
    hp_change = 0

    if damage_tag == "heal_small":
        hp_change = random.randint(5, 25)
    elif damage_tag == "heal_full":
        hp_change = 100
    elif damage_tag == "dmg_light":
        hp_change = -random.randint(1, 5)  # Подряпина
    elif damage_tag == "dmg_medium":
        hp_change = -random.randint(5, 25)  # Поранення
    elif damage_tag == "dmg_heavy":
        hp_change = -random.randint(25, 50)  # Критичний удар
    elif damage_tag == "dmg_fatal":
        hp_change = -100  # Смерть

    if hp_change != 0:
        old_hp = safe_int(profile.get("Здоров'я", 100))
        new_hp = max(0, min(100, old_hp + hp_change))
        profile["Здоров'я"] = new_hp
        logs.append(f"❤️ Здоров'я: {old_hp} -> {new_hp}")

    # === 3. ОБРОБКА ЗОЛОТА ===
    gold_tag = ai_impacts.get("gold_impact", "none")
    gold_change = 0

    # Використовуємо фіксовані суми, щоб АІ не придумав мільйон
    if gold_tag == "spend_small":
        gold_change = -random.randint(5, 15)  # Їжа, випивка
    elif gold_tag == "spend_medium":
        gold_change = -random.randint(50, 150)  # Спорядження
    elif gold_tag == "spend_large":
        gold_change = -random.randint(300, 800)  # Коні, хабарі
    elif gold_tag == "earn_small":
        gold_change = random.randint(10, 30)  # Підробіток
    elif gold_tag == "earn_medium":
        gold_change = random.randint(100, 300)  # Нагорода
    elif gold_tag == "earn_large":
        gold_change = random.randint(1000, 2000)  # Скарб

    if gold_change != 0:
        old_gold = safe_int(profile.get("Особисте Золото", 0))
        new_gold = max(0, old_gold + gold_change)
        profile["Особисте Золото"] = new_gold
        logs.append(f"💰 Золото: {gold_change:+}")

        # === 4. ОБРОБКА ІНВЕНТАРЯ (ДОДАНО!) ===
        # Отримуємо поточний інвентар як список
        current_inv_str = str(profile.get("Інвентар", ""))
        if current_inv_str == "-" or current_inv_str == "Пусто" or not current_inv_str:
            inventory_list = []
        else:
            # Розбиваємо рядок "Меч, Яблуко" на список ["Меч", "Яблуко"]
            inventory_list = [item.strip() for item in current_inv_str.split(',') if item.strip()]

        # Додавання речей
        new_items = ai_impacts.get("inventory_new")
        if new_items:
            if isinstance(new_items, str): new_items = [new_items]  # Захист від помилки формату
            for item in new_items:
                inventory_list.append(item)
                logs.append(f"➕ Отримано: {item}")

        # Видалення речей
        lost_items = ai_impacts.get("inventory_lost")
        if lost_items:
            if isinstance(lost_items, str): lost_items = [lost_items]
            for item in lost_items:
                # Шукаємо предмет нечутливо до регістру
                for existing_item in inventory_list:
                    if item.lower() in existing_item.lower():
                        inventory_list.remove(existing_item)
                        logs.append(f"➖ Втрачено: {existing_item}")
                        break

                        # Збираємо назад у рядок
        if not inventory_list:
            profile["Інвентар"] = "Пусто"
        else:
            profile["Інвентар"] = ", ".join(inventory_list)

            # === 5. ОБРОБКА НАВИЧОК (НОВЕ) ===
            skill_updates = ai_impacts.get("skill_impact", {})
            # Очікуємо формат: {"Бойові": 2, "Інтрига": 1}

            if skill_updates:
                for skill_name, val in skill_updates.items():
                    # Шукаємо правильну назву ключа в профілі
                    # (Бо в профілі може бути "Бойові навички" або просто "Бойові")
                    target_key = None
                    for key in profile.keys():
                        if skill_name in key:
                            target_key = key
                            break

                    if target_key:
                        old_val = safe_int(profile[target_key])
                        # Ліміт 100
                        new_val = min(100, old_val + val)
                        profile[target_key] = new_val
                        logs.append(f"📈 {skill_name}: +{val} (Стало {new_val})")

    return profile, logs


def clean_and_parse_json(text):
    """
    Витягує JSON з тексту. Підтримує і словники {}, і списки [].
    """
    try:
        # 1. Прибираємо Markdown обгортки
        text = text.replace("```json", "").replace("```", "").strip()

        # 2. Визначаємо, з чого починається JSON (об'єкт чи масив)
        start_obj = text.find('{')
        start_arr = text.find('[')

        # Знаходимо найпершу відкриваючу дужку (ігноруючи -1, якщо не знайдено)
        possible_starts = [i for i in [start_obj, start_arr] if i != -1]

        if not possible_starts:
            return None  # Немає дужок взагалі

        start_index = min(possible_starts)

        # Визначаємо тип і шукаємо відповідну закриваючу дужку
        if start_index == start_obj:
            end_index = text.rfind('}')
        else:
            end_index = text.rfind(']')

        if start_index != -1 and end_index != -1 and end_index > start_index:
            json_str = text[start_index: end_index + 1]
            return json.loads(json_str)
        else:
            return None

    except json.JSONDecodeError:
        return None
    except Exception as e:
        print(f"⚠️ Помилка парсингу: {e}")
        return None


# ================= РОБОТА З GOOGLE SHEETS =================

def get_client():
    """Підключення до Google API через змінні середовища"""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    # 1. Пробуємо знайти файл
    if os.path.exists(CREDENTIALS_FILE):
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    else:
        # 2. Якщо файлу немає, шукаємо змінну середовища
        json_creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if not json_creds:
            raise Exception("❌ Не знайдено файл credentials.json і змінну GOOGLE_CREDENTIALS_JSON")

        # Перетворюємо рядок JSON у словник
        creds_dict = json.loads(json_creds)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

    client = gspread.authorize(creds)
    return client


def get_sheet(worksheet_name):
    """Відкриває конкретний аркуш за ID таблиці"""
    client = get_client()
    return client.open_by_key(SPREADSHEET_ID).worksheet(worksheet_name)


# Глобальна змінна для кешування знань (щоб не читати таблицю щосекунди)
LORE_CACHE = []


def load_lore_data():
    """Завантажує базу знань у пам'ять бота при старті"""
    global LORE_CACHE
    try:
        sheet = get_sheet(TAB_KNOWLEDGE)
        records = sheet.get_all_records()
        LORE_CACHE = records
        print(f"📚 Завантажено {len(records)} записів лору.")
    except Exception as e:
        print(f"⚠️ Не вдалося завантажити KnowledgeBase: {e}")
        LORE_CACHE = []


def get_relevant_context(user_text, current_location):
    """
    Шукає в базі інформацію, яка відповідає словам гравця або локації.
    """
    found_info = []

    # Об'єднуємо текст гравця і локацію для пошуку
    search_text = (user_text + " " + current_location).lower()

    for row in LORE_CACHE:
        keywords = str(row.get('Ключові слова', '')).lower().split(',')
        content = row.get('Інформація', '')

        # Перевіряємо кожне ключове слово
        for key in keywords:
            key = key.strip()
            if key and key in search_text:
                # Якщо знайшли збіг - додаємо інформацію
                found_info.append(f"- {content}")
                break  # Достатньо одного збігу на рядок

    if found_info:
        # Обмежуємо кількість, щоб не перевантажити АІ (максимум 5 фактів)
        return "\n".join(found_info[:5])
    else:
        return "Немає особливих відомостей."


# --- ЧИТАННЯ ДАНИХ ДЛЯ МЕНЮ ---

def send_safe_message(chat_id, text, reply_to_message_id=None):
    """
    Універсальна функція для відправки повідомлень.
    Автоматично розбиває довгі тексти на частини.
    """
    max_len = 4000

    if len(text) <= max_len:
        # Якщо текст короткий
        if reply_to_message_id:
            try:
                bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, parse_mode='Markdown')
            except:
                # Якщо Markdown зламався тоді відправляємо без нього
                bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
        else:
            try:
                bot.send_message(chat_id, text, parse_mode='Markdown')
            except:
                bot.send_message(chat_id, text)
    else:
        # Якщо текст довгий - ріжемо
        parts = [text[i:i + max_len] for i in range(0, len(text), max_len)]
        for part in parts:
            try:
                bot.send_message(chat_id, part, parse_mode='Markdown')
            except:
                bot.send_message(chat_id, part)
            time.sleep(0.5)  # Пауза, щоб Telegram не заблокував за спам


def get_unique_regions():
    """Отримує список регіонів з таблиці 'Доми'"""
    try:
        sheet = get_sheet(TAB_HOUSES)
        records = sheet.get_all_records()
        # Збираємо назви регіонів
        regions = set(row['Регіон'] for row in records if row.get('Регіон'))
        return sorted(list(regions))
    except Exception as e:
        print(f"❌ Помилка читання регіонів: {e}")
        return []


def get_houses_by_region(region):
    """Отримує список домів у регіоні"""
    try:
        sheet = get_sheet(TAB_HOUSES)
        records = sheet.get_all_records()
        houses = [row['Рід'] for row in records if row.get('Регіон') == region]
        return sorted(houses)
    except:
        return []


def get_house_stats_data(house_name):
    """Отримує дані про Дім (багатство, армію)"""
    try:
        sheet = get_sheet(TAB_HOUSES)
        records = sheet.get_all_records()
        for row in records:
            if row.get('Рід') == house_name:
                return row
        return {}
    except:
        return {}


# ================= РОБОТА З БАЗОЮ ГРАВЦІВ (Users_DB) =================

def get_user_data(user_id):
    """Знаходить дані конкретного гравця за його Telegram ID """
    try:
        sheet = get_sheet(TAB_USERS)

        # Повертаємо список знайдених клітинок або пустий список []
        cells = sheet.findall(str(user_id), in_column=1)

        if not cells:
            return None, None  # Гравця не знайдено, повертаємо пустоту

        # Якщо знайшли - беремо першу знахідку
        cell = cells[0]

        # Беремо дані з 3-ї колонки (Game_Data)
        raw_data = sheet.cell(cell.row, 3).value

        if raw_data:
            return json.loads(raw_data), cell.row
        return None, None

    except Exception as e:
        print(f"❌ Помилка читання БД: {e}")
        return None, None


def save_user_data(user_id, profile_data, char_name="Unknown"):
    """Зберігає або оновлює дані гравця """
    try:
        sheet = get_sheet(TAB_USERS)
        json_str = json.dumps(profile_data, ensure_ascii=False)

        # 1. Перевіряємо, чи є вже такий гравець
        cells = sheet.findall(str(user_id), in_column=1)

        if cells:
            # ОНОВЛЕННЯ (UPDATE):
            cell = cells[0]
            sheet.update_cell(cell.row, 3, json_str)
            sheet.update_cell(cell.row, 2, char_name)
        else:
            # СТВОРЕННЯ (CREATE):
            sheet.append_row([str(user_id), char_name, json_str])

        return True
    except Exception as e:
        print(f"❌ Помилка збереження: {e}")
        return False


def reset_and_fill_character_sheet(data_dict):
    """ПОВНЕ ПЕРЕЗАПИСУВАННЯ таблиці на старті гри"""
    try:
        sheet = get_sheet(TAB_CHARACTER)
        keys_col = sheet.col_values(1)  # Всі ключі з колонки А

        cells_to_update = []
        for i, key in enumerate(keys_col):
            if not key: continue
            # Беремо значення з data_dict або ставимо прочерк
            new_val = data_dict.get(key, "-")
            cells_to_update.append(gspread.Cell(i + 1, 2, new_val))

        sheet.update_cells(cells_to_update)
        return True
    except Exception as e:
        print(f"❌ Помилка ініціалізації: {e}")
        return False


# ================= ЛОГІКА AI =================

def ask_gemini(prompt):
    """
    Універсальна функція запиту з повторними спробами та очищенням JSON.
    """
    retries = 3
    delay = 2

    strict_prompt = prompt + "\n\nВАЖЛИВО: Відповідай ТІЛЬКИ валідним JSON кодом. Без Markdown. Без слів 'Ось ваш JSON'."

    for attempt in range(retries):
        try:
            # Виклик моделі
            response = model.generate_content(strict_prompt)

            # Спроба очистити та розпарсити
            result = clean_and_parse_json(response.text)

            if result:
                return result
            else:
                print(f"⚠️ Спроба {attempt + 1}: Отримано не JSON. Текст: {response.text[:50]}...")
                time.sleep(delay)

        except Exception as e:
            print(f"❌ Помилка API (спроба {attempt + 1}): {e}")
            time.sleep(delay)
            delay += 2

    print("❌ Не вдалося отримати JSON від AI.")
    return None


def get_canon_characters(house_name):
    # 1. Словник з готовими персонажами (щоб не смикати AI)
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

    # 2. Якщо дім відомий - повертаємо список миттєво
    if house_name in PRESET_CHARACTERS:
        print(f"⚡ Використовую кеш для дому {house_name}")
        return PRESET_CHARACTERS[house_name]

    print(f"🔍 Запитую AI про персонажів дому {house_name}...")
    prompt = f"""
    Write a list in Ukrainian of the 4 most famous characters from House {house_name} (Game of Thrones) .
Return ONLY a JSON array of strings.
Example: [“Name 1”, “Name 2”]

    """
    result = ask_gemini(prompt)
    # Якщо результат є - повертаємо, якщо ні - пустий список (щоб код не впав)
    return result if result else []


def get_narrative_intro(profile):
    """Генерує атмосферний вступ на основі локації"""
    print(f"📜 Пишу вступ для {profile.get("Ім'я")}...")

    # Перетворюємо весь профіль на текст для контексту
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)

    # Визначаємо локацію (вона вже правильна завдяки попередній функції)
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
        # Тут не потрібен JSON, нам потрібен просто гарний текст
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Помилка генерації вступу: {e}")
        return f"Ви прибули у {current_location}. Вітер дме в обличчя, а навколо чути гомін. Що ви робите?"


def generate_initial_stats(char_name, house_name, house_data):
    """Генерує стартовий профіль, враховуючи КАНОНІЧНЕ місцезнаходження"""
    # Це регіон походження Дому (для довідки), але не обов'язково місце старту
    origin_region = house_data.get('Регіон', 'Вестерос')

    print(f"🎲 Генерую статистику для {char_name}...")

    # Переконуємося, що змінні контексту існують
    context = GAME_ERA_CONTEXT if 'GAME_ERA_CONTEXT' in globals() else "Час: Початок Гри Престолів."

    prompt = f"""
    Create a starting profile for the RPG “Game of Thrones.”

    TIME CONTEXT:
    {context}

    CHARACTER: {char_name}
    HOUSE: {house_name} (Origin: {origin_region})

    === LOCATION RULES (CRITICAL) ===
    Determine “Current Location” based on the CANON of the first book (298 CE), not the region of origin.
    Examples of exceptions:
    1. Jorah Mormont -> House from the North, but starting in Essos (Pentos).
    2. Theon Greyjoy -> House from the Iron Islands, but starting in Winterfell (foster child).
    3. Jaime Lannister -> House from the Westerlands, but started in King's Landing.
    4. Daenerys Targaryen -> Started in Pentos (Essos).
    5. Stannis Baratheon -> Started on Dragonstone.

    If a character does not have a specific canonical location (or is a fictional character), use the region of origin ({origin_region}).

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
        "Риси": "2-3 риси характеру",
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

    prompt += "\n\nВАЖЛИВО: Ти - комп'ютерна система. Відповідай ТІЛЬКИ чистим JSON кодом. Без вступу, без пояснень, без Markdown."

    # Використовуємо нашу функцію запиту
    profile = ask_gemini(prompt)

    if profile:
        # Примусово перезаписуємо ім'я та дім, щоб не було галюцинацій,
        # АЛЕ НЕ ПЕРЕЗАПИСУЄМО ЛОКАЦІЮ (залишаємо те, що вирішив АІ)
        profile["Ім'я"] = char_name
        profile["Дім"] = house_name
        return profile
    else:
        return None


def safe_int(value):
    """Допоміжна функція: перетворює будь-що на ціле число або повертає 0"""
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return 0


def validate_action(user_input, profile):
    """
    Окрема функція-фільтр (Цензор).
    Перевіряє, чи не намагається гравець зламати гру чітами, анахронізмами чи діями за когось іншого.
    """
    char_name = profile.get("Ім'я", "Герой")

    prompt = f"""
    You are the Lore Keeper for the game “Game of Thrones” (Medieval Fantasy).

    Your task: Check the player's action for “legality.”

    PLAYER: {char_name}
    ACTION: “{user_input}”

    === THE GOLDEN RULE: INTENT ONLY ===
    The player is allowed to describe ONLY what they TRY to do.
    The player is FORBIDDEN from describing the RESULT of their action.
    The result is determined by the Game Master, not the player.

    === EXAMPLES OF VIOLATIONS (RETURN is_valid: false) ===
    1. **Deciding the hit:**
       - ❌ "I cut off his head." (Player decided the hit landed and killed).
       - ❌ "I shoot him in the eye." (Too specific result).
       - ✅ CORRECT: "I swing my sword at his neck." / "I aim for his eye."
    
    2. **Deciding the social outcome:**
       - ❌ "I convince him to give me the keys." (Player decided success).
       - ❌ "I scare him and he runs away." (Player controlled the NPC's reaction).
       - ✅ CORRECT: "I demand the keys persuasively." / "I scream to scare him."

    3. **Deciding the world state:**
       - ❌ "I find a secret door." (Player decided there is a door).
       - ✅ CORRECT: "I search the room for secrets."
       
    4. It is FORBIDDEN to write actions for NPCs (other characters).
        - ❌ NOT ALLOWED: “Jorah Mormont draws his sword and kills the Night King.” (The player controls Jorah).
        - ✅ ALLOWED: “I shout to Jorah, ‘Kill him!’” (The player controls their voice).
        - ✅ ACCEPTABLE: “I order Jorah to attack.” (The player gives the order, and the GM decides whether the NPC will carry it out or not).

    5. It is FORBIDDEN to describe the CONSEQUENCES of your actions as fact.
        - ❌ NOT ALLOWED: “I hit the guard, and his head flies off his shoulders.” (The player decided the outcome).
        - ✅ ALLOWED: “I hit the guard in the neck with my sword with all my strength.” (The player describes the attempt, the outcome is up to the GM).

    === PROHIBITION CRITERIA (RETURN is_valid: false) ===
    1. **Anachronisms:** Mention of modern technologies (F-16, telephone, automatic weapon, internet, NATO, Biden).
    2. **Cheat codes:** Attempts to change your stats (“Give me 100,000 gold,” “I become immortal,” “All enemies die”).
    3. **Meta-gaming:** Attempts to control the plot as an author (“I want a dragon to fly in and save everyone,” “Skip to the end of the game”).
    4. **Absurdity:** Actions that are physically impossible for a human (unless it is magic available in the lore, e.g., wargs).

    === PERMISSION CRITERIA (RETURN is_valid: true) ===
    1. Allow risky actions (“I attack the king” is stupid, but legal. The head GM will kill him).
    2. Allow jokes and conversations.
    3. Allow any actions that are possible in the physical world of Westeros.
    
    === YOUR VERDICT ===
    If the player describes the RESULT, reject the action.
    
    === FORBID TO PLAY AFTER DEATH ===
    If {profile.get("Здоров'я", 100) < 0 } Game is ended and player can not continue.

    RESPONSE (JSON):
    {{
        “is_valid”: true or false,
        “refusal_reason”: “A short ironic comment in Ukrainian explaining why it is impossible (only if false). For example: ‘The gods don't know what an F-16 is.’”
    }}
    """

    try:
        response = model_worker.generate_content(prompt)
        result = clean_and_parse_json(response.text)

        # Якщо JSON не розпарсився, вважаємо дію допустимою (презумпція невинуватості),
        # щоб не блокувати гру через помилку АІ.
        if not result:
            return True, ""

        return result.get("is_valid", True), result.get("refusal_reason", "Це неможливо.")

    except Exception as e:
        print(f"⚠️ Помилка валідатора: {e}")
        return True, ""


def summarize_turn(gm_response):
    """
    Стискає хід в одне речення для історії.
    Використовує Gemma-4b.
    """
    # Беремо тільки текст відповіді (без системних логів)
    clean_story = gm_response.split("📊")[0][:800]

    prompt = f"""
    Summarize this RPG turn into ONE short sentence (Ukrainian).
    Keep names and outcomes.
    GM: "{clean_story}"

    Output example: "Джон спробував вдарити вартового, але той ухилився."
    """

    try:
        # Використовуємо WORKER (4b)
        resp = model_worker.generate_content(prompt)
        return resp.text.strip()
    except:
        return f"Гравець: "


def check_skill_mechanics(user_input, profile):
    """
    Визначає складність дії та проводить перевірку навичок (Dice Roll) у Python.
    Повертає текстову інструкцію для Сюжетної Моделі.
    """
    # 1. Запитуємо Worker: Що це за дія?
    prompt = f"""
    Analyze player action: "{user_input}"
    Hero Stats: {json.dumps(profile, ensure_ascii=False)}

    Task: Determine the Skill needed and Difficulty (0-100).

    Difficulty Guide:
    0 = Talking, walking, looking (No check needed).
    20 = Easy (Slapping a drunkard).
    60 = Medium (Fighting a guard, lying to a merchant).
    90 = Hard (Fighting a knight, lying to a King).
    110 = Impossible (Flying, killing a dragon with bare hands).

    Skills: "Бойові", "Військові", "Інтрига", "Управління", "None".

    Return JSON: {{ "skill": "...", "difficulty": 0-100, "reason": "..." }}
    """

    try:
        # Використовуємо маленьку модель для швидкості
        resp = model_worker.generate_content(prompt)
        data = clean_and_parse_json(resp.text)

        if not data: return "RESULT: AUTO_SUCCESS (Simple Action)"

        difficulty = data.get("difficulty", 0)
        skill_name = data.get("skill", "None")

        # Якщо складність 0 - кидати кубик не треба
        if difficulty == 0:
            return "RESULT: NARRATIVE_CHOICE (No mechanics needed)"

        # 2. Математика в Python (Чесна гра)
        player_stat = safe_int(profile.get(f"{skill_name} навички", 0))
        # Якщо ключа немає (напр "Інтрига"), спробуємо прямий збіг
        if player_stat == 0: player_stat = safe_int(profile.get(skill_name, 10))

        import random
        dice_roll = random.randint(1, 40)
        total_score = player_stat + (dice_roll // 2)  # Формула: Стат + d100/2

        # 3. Визначаємо результат
        if total_score >= difficulty:
            outcome = "SUCCESS"
            details = "Action succeeds."
        else:
            outcome = "FAILURE"
            details = "Action fails. Player gets hurt or rejected."

        stat_increase_msg = ""

        if difficulty >= 50 and dice_roll >= 95:
            # Гравець перевершив себе!
            new_stat = min(100, player_stat + 1)

            # Оновлюємо профіль прямо тут (або повертаємо інструкцію для оновлення)
            # Для надійності краще повернути інструкцію, яку обробить apply_system_impacts,
            # але оскільки ця функція лише читає, ми додамо спец-тег у вивід.
            stat_increase_msg = f"BONUS: CRITICAL INSIGHT! {skill_name} increased by +1!"

            # ХАК: Ми можемо записати це в глобальну змінну або передати наверх,
            # але найпростіше - додати це в текст VERDICT, щоб main-модель це описала,
            # а apply_system_impacts ми навчимо ловити це.

        # ... (формування outcome SUCCESS/FAILURE) ...

        # Формуємо рядок, який піде в Промпт Сюжетника
        return f"""
        MECHANICAL VERDICT:
        - Action Difficulty: {difficulty}
        - Player Skill ({skill_name}): {player_stat}
        - Dice Roll: {dice_roll}
        - FINAL RESULT: {outcome.upper()}!
        - INSTRUCTION: You MUST describe a {outcome} scenario. {details}
        {stat_increase_msg}
        """

    except Exception as e:
        print(f"Skill Check Error: {e}")
        return "RESULT: UNKNOWN (Proceed with logic)"


def process_training_request(user_input, profile):
    """
    Перевіряє, чи хоче гравець тренуватись.
    Повертає JSON з оновленнями (ціна, час, стат) або None.
    """
    # Список ключових слів (можна розширити)
    training_keywords = ["тренув", "вчив", "практив", "train", "practice", "study"]
    if not any(word in user_input.lower() for word in training_keywords):
        return None

    # Промпт для Worker-моделі, щоб розібрати деталі
    prompt = f"""
    User Input: "{user_input}"
    Current Stats: {json.dumps(profile, ensure_ascii=False)}

    Task: Is the user trying to improve a skill?
    Rules for Training:
    1. Requires a teacher or books (in context).
    2. Takes TIME (days/weeks).
    3. Costs GOLD (payment to teacher).

    Determine:
    - Which skill? ("Бойові", "Військові", "Інтрига", "Управління")
    - Is it possible in current context?

    Return JSON: {{ "is_training": true, "skill": "...", "reason": "..." }} OR {{ "is_training": false }}
    """

    try:
        resp = model_worker.generate_content(prompt)
        data = clean_and_parse_json(resp.text)

        if not data or not data.get("is_training"): return None

        skill_target = data.get("skill")
        current_val = safe_int(profile.get(f"{skill_target} навички", 0))
        if current_val == 0: current_val = safe_int(profile.get(skill_target, 10))

        # === МАТЕМАТИКА БАЛАНСУ (HARDCORE) ===
        # Чим вищий рівень, тим дорожче і довше
        # Формула ціни: Поточний рівень * 5 золотих
        cost_gold = current_val * 5
        # Формула часу: Мінімум 3 дні + (Рівень / 20)
        cost_time_days = 3 + int(current_val / 20)

        user_gold = safe_int(profile.get("Особисте Золото", 0))

        if user_gold < cost_gold:
            return {
                "error": f"Не вистачає золота. Потрібно {cost_gold}, а є {user_gold}."
            }

        # Успішне тренування
        return {
            "success": True,
            "skill": skill_target,
            "cost_gold": cost_gold,
            "cost_time": cost_time_days,
            "stat_gain": 2,  # +2 пункти за сесію тренування
            "story_prompt": f"SYSTEM: Player spends {cost_time_days} days and {cost_gold} gold training {skill_target}. Describe the grueling process."
        }

    except:
        return None

def process_game_turn(chat_id, user_input):
    """Обробка ходу гравця тут"""
    user_id = chat_id

    # 1. Завантаження даних
    profile, row_id = get_user_data(user_id)
    if not profile: return "❌ Профіль не знайдено."

    session = user_sessions.get(chat_id, {})
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"state": "GAME_ACTIVE", "history": []}
    history = session.get('history', [])

    # 2. Контекст
    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-40:]])
    mini_profile = {
        "Name": profile.get("Ім'я"),
        "Loc": profile.get("Поточне місцезнаходження"),
        "HP": profile.get("Здоров'я"),
        "Gold": profile.get("Особисте Золото"),
        "Time": profile.get("Ігровий час"),  # ВАЖЛИВО: АІ має бачити час
        "Inv": profile.get("Інвентар")  # ВАЖЛИВО: АІ має бачити інвентар
    }
    profile_json = json.dumps(profile, ensure_ascii=False, indent=2)
    curr_loc = profile.get("Поточне місцезнаходження", "")

    # Знання з бази
    context_knowledge = get_relevant_context(user_input, curr_loc)

    mechanics_instruction = check_skill_mechanics(user_input, profile)

    # 1. ПЕРЕВІРКА НА ТРЕНУВАННЯ (Explicit Training)
    training_result = process_training_request(user_input, profile)

    mechanics_note = ""
    extra_updates = {}

    if training_result:
        if "error" in training_result:
            # Якщо не вистачає грошей - кажемо АІ про це
            mechanics_note = f"SYSTEM: Player tried to train but FAILED. Reason: {training_result['error']}. Describe their disappointment."
        else:
            # Якщо успіх - готуємо дані для АІ і Профілю
            mechanics_note = training_result['story_prompt']
            # Примусові оновлення, які ми передамо в apply_system_impacts
            extra_updates = {
                "gold_impact": f"spend_{training_result['cost_gold']}",
                # Це треба адаптувати під вашу логіку або просто відняти вручну
                "skill_impact": {training_result['skill']: training_result['stat_gain']},
                "time_passed": "days"  # Примусовий пропуск часу
            }
            # ХАК: Віднімаємо золото і час вручну, щоб не плутати АІ тегами
            # (Або можна довіритись АІ, але вручну надійніше)
            current_gold = safe_int(profile.get("Особисте Золото", 0))
            profile["Особисте Золото"] = max(0, current_gold - training_result['cost_gold'])
            # Час оновиться через тег "days"

    else:
        # 2. ЯКЩО НЕ ТРЕНУВАННЯ - ЗВИЧАЙНА ПЕРЕВІРКА (Skill Check)
        mechanics_note = check_skill_mechanics(user_input, profile)

    # --- ПРОМПТ ---
    prompt = f"""
    YOU — MERCILESS GAME MASTER (GM) IN THE WORLD OF “GAME OF THRONES.” NOT A NOVELIST. DO NOT WRITE A BOOK. RUN A GAME.
    Your task: Run the game, balancing between the plot, player freedom, and CLEAR MECHANICS, to create a realistic, dangerous, and dark story. The world does not revolve around the player.
    YOU ARE NOT A FRIEND. YOU ARE A SIMULATOR OF A CRUEL REALITY (Game of Thrones).
    Your goal is NOT to tell a heroic story, but to honestly simulate the consequences of stupidity, arrogance, and physics.

    === HERO ===
    {profile_json}
    (GM NOTE: Player currently has {profile.get("Особисте Золото")} gold. Use this to limit their purchases).

    === WORLD CONTEXT ===
    {GAME_ERA_CONTEXT}
    {context_knowledge}
    
    === TAGS GUIDE ===
    1. **time_passed** (How much time has passed):
       - “short” (pure dialogue, thoughts, quick look - NO TIME ADVANCE)
       - “medium” (an hour or two - walk, exploration)
       - “long” (half a day - travel, work)
       - “sleep” (night - rest)

    2. **health_impact** (Health consequences):
       - “none” (no change)
       - “dmg_light” (bruise, scratch)
       - “dmg_medium” (sword wound, burn)
       - “dmg_heavy” (critical injury, loss of consciousness)
       - “dmg_fatal” (death)
       - “heal_small” (rest)

    3. **gold_impact** (Economy):
        - “none”
        - “spend_small” (food, small items)
        - “spend_medium” (weapons, clothing)
        - “spend_large” (horses, houses)
        - “earn_small” (found a coin)
        - “earn_medium” (quest reward)
        - “earn_large” (grand treasure - rare!)
    
    4. **inventory**   
        - "inventory_new": ["Item Name"] (If obtained).
        - "inventory_lost": ["Item Name"] (If lost/eaten). 
        
    === THE GOLDEN LAWS OF AGENCY (VIOLATION = FAILURE) ===
    1. **NEVER TOUCH THE PLAYER:** You control NPCs, Weather, and Physics. The Player controls ONLY their Hero.
       - ❌ BAD: "Illyrio screams, and you turn around to leave, feeling angry." (You controlled the player's action and emotion).
       - ✅ GOOD: "Illyrio screams insults at your back. The door is ahead. What do you do?"
    
    2. **NO MIND READING:** Never tell the player what they feel, think, or realize.
       - ❌ BAD: "You realized he was lying." / "You felt fear."
       - ✅ GOOD: "His eyes dart nervously." / "His hand trembles on the hilt." (Show, don't tell).

    3. **COMBAT PACING (EXCHANGE, NOT MOVIE):**
       - Do NOT resolve the whole fight in one turn.
       - Describe ONE exchange. If the player attacks -> Describe the enemy's defense/counter-attack -> STOP.
       - ❌ BAD: "You cut his throat and then killed his two guards."
       - ✅ GOOD: "You lunge at his throat. He barely parries, stepping back, and calls for help. Two guards appear. Action?"

    4. **ANTI-RAILROADING:**
       - If the player leaves ("I walk away"), the scene MUST end. Do not make the NPC continue talking to a wall.
       - Describe the new location (Street/Corridor).
       
    5. **INTENT vs RESULT:** The player describes their INTENT ("I try to kill him"). YOU determine the RESULT based on logic.
       - If the player tries something impossible -> Describe the failure.
    
    6. **THE STOP SIGNAL:** You MUST stop writing immediately after the NPC reacts or the event happens.
       - BAD: "The guard attacks, you dodge, and then you kill him." (You played the whole fight).
       - GOOD: "The guard swings his sword at your head! What do you do?" (Stops for reaction).
    
    === DOCTRINE OF RESISTANCE (CRITICAL RULES) ===
    1. **NO "YES-MAN":** Do not agree with the player just to move the plot. If they try to repair a ship in one night, say NO. It takes weeks. If they try to intimidate a powerful Lord with no army, the Lord must laugh and throw them out.
    2. **NPC POWER:** Illyrio Mopatis, Tywin Lannister, and Iron Bank envoys are SMARTER and MORE POWERFUL than the player.
       - Illyrio is not a servant. He owns the player. If the player is rude, Illyrio cuts off funding or locks the door.
       - Merchants have guards. You cannot just rob a port without the City Watch (2000 spears) reacting.
    3. **PHYSICS & TIME:** - Repairing a ship = Weeks.
       - Gathering an army = Months.
       - Traveling = Days.
       - If the player ignores time ("I do it quickly"), FORCE a "time_passed": "long" tag and describe the weeks lost.
    4. **CONSEQUENCES:** If the player commits a crime (murder, theft) in a Free City, the Magisters WILL send the City Watch. The player is not invisible.
    5. **PAUSE RULE:** Do NOT describe a long chain of actions for NPCs.
       - BAD: "Drogo laughs, grabs you, beats you, and throws you out." (Player had no chance to react).
       - GOOD: "Drogo laughs and reaches for your throat. What do you do?" (Stops for player reaction).
    
    **IF THE PLAYER FAILS:** Describe the failure painfully. Do not give them what they want. Make them suffer the consequences.

    === PLOT RULES ===
    1. LISTEN TO THE PLAYER: If the player writes “I'm moving on” or “I'm ignoring this,” YOU MUST change the scene.
       - It is FORBIDDEN to describe the same body/place if the player has left.
       - Describe the road, a new threat, or arrival at the tavern.  

    === ATMOSPHERE AND COMPLEXITY (GRIMDARK) ===
    1. **Mortality:** Everyone dies in this world. The player does NOT have “plot armor.”
    - If a player with weak skills engages in combat with a knight, they must lose and suffer serious injuries or die.
    - If a player insults a powerful lord, they will be executed or thrown into a dungeon.
    - Don't be afraid to kill a character or cripple them if it is a logical consequence of their stupidity.
    2. **Cruelty:** Describe the world realistically. Dirt, blood, betrayal, injustice. Do not embellish reality.
    3. **Consequences:** Every action has a price. Victory is never pure (you won, but broke your arm/lost a friend/ruined your reputation).
    4. **DIFFICULTY CHECK** Analyze the action. Is it difficult?
    - **Impossible:** Asking a King to give up the throne -> FAIL immediately.
    - **Hard:** Capturing a ship alone -> FAIL with injury ("dmg_medium").
    - **Medium:** Bribing a guard with little gold -> FAIL or High Cost.
    **IF THE PLAYER FAILS:** Describe the failure painfully. Do not give them what they want. Make them suffer the consequences.

    === DIALOGUE RULES (PING-PONG MODE) — CRITICAL! ===
    1. If the player starts a conversation (“I approach...”, “I say to him...”):
       - Write ONLY THE FIRST REACTION or RESPONSE of the NPC.
       - NEVER write the continuation of the dialogue for the player.
       - NEVER summarize the conversation (“You talked about everything and went your separate ways”).
    2. Speed: One player turn = One NPC line.
    3. NPC character: They can lie, interrupt, ignore, or walk away if they get tired of the player. Don't make them “reference bureaus.”


    === SYSTEM VERDICT (MANDATORY TO FOLLOW) ===
    {mechanics_instruction}
    {mechanics_note}
    (IF RESULT is FAILURE -> Describe how the hero fails painfully. Do NOT let them win.
     IF RESULT is SUCCESS -> Describe a triumph.)
    
    === PLOT MODE ===
    1. **Freedom of Action, but Realistic Consequences** The player can do anything. Don't say “no,” say “you tried, and this is what happened.”
    2. **Story Magnet:** If the player's action is trivial (“going for a walk”), throw in an event that leads to the main story (found a letter, saw a spy, met an important NPC).
    3. **Side Quests:** Only if the player is wasting time (“waiting,” “moving on”), generate an interesting random encounter that reveals lore.
    4. **Plot adaptation:** If the player's actions break the canon, adapt the world, create consequences, but do not prohibit the action. If the player breaks the canon, the world reacts aggressively (they are put on the wanted list, assassins are sent after them).
    5. **Continuity of the plot:** The world lives its own life. War is constantly looming.
    6. **Punishment for inaction:** If the player acts passively, the world punishes them (they are robbed, accused of a crime, forcibly mobilized for war).

    === FINAL (CRITICALLY IMPORTANT) ===
    Your response MUST end with a QUESTION that presents the player with a specific choice.
    - BAD: “What are you doing?” (Too vague).
    - GOOD: “The guard squints suspiciously, waiting for an explanation. Will you show him the House seal or try to bribe him?”
    - GOOD: “The screams are getting closer. Will you hide in the shadows or face the danger head-on?”

    === HISTORY OF EVENTS (PAST) ===
    {history_text}

    === CURRENT TURN (PRESENT) ===
    PLAYER SAYS/DOES: “{user_input}”

    === YOUR TASK (FUTURE) ===
    Write a creative continuation of the story, responding ONLY TO THE PLAYER'S CURRENT ACTION in Ukrainian.
    1. Describe the consequences of the action, keep it focused on the IMMEDIATE reaction of the world.
    2. If the player has changed the subject, follow the new subject and forget the old one.
    3. End with a QUESTION or a DILEMMA to which the player must respond.
    4. Fill in the JSON TAGS based on the player's actions using the AVAILABLE TAGS form === TAGS GUIDE ===:
    - If the player is moving/waiting/sleeping -> fill in Time has passed!
    - If the player buys/finds items -> fill in New inventory!
    - If the player eats/sells/loses items -> fill in Lost inventory!


    RESPONSE FORMAT (JSON ONLY):
    {{
        "story": "Your story text here...",
        "updates": {{ 
             "time_passed": "...",
             "health_impact": "...",
             "gold_impact": "...",
             "inventory_new": [],
             "inventory_lost": []
        }}
    }}
    """

    prompt += "\n\nВАЖЛИВО: Відповідай ТІЛЬКИ JSON."

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        ai_data = clean_and_parse_json(raw_text)

        # Fallback
        if not ai_data and '"story":' in raw_text:
            try:
                story_match = re.search(r'"story":\s*"(.*?)(?<!\\)"', raw_text, re.DOTALL)
                extracted_story = story_match.group(1).replace('\\"', '"').replace('\\n',
                                                                                   '\n') if story_match else raw_text
                ai_data = {"story": extracted_story, "impacts": {}}
            except:
                ai_data = {"story": raw_text, "impacts": {}}

        if not ai_data: ai_data = {"story": raw_text, "impacts": {}}

        story = ai_data.get("story", "...")
        if "Тут напиши" in story or len(story) < 20:
            fix_resp = model.generate_content(
                f"Ти GM. Гравець: {user_input}. Опиши наслідки художньо. Закінчи питанням.")
            story = fix_resp.text

        impacts = ai_data.get("updates", {})

        # --- PYTHON-МЕНЕДЖЕР ---
        profile, logs = apply_system_impacts(profile, impacts)
        # ----------------------------------

        final_updates = ai_data.get("updates", {})
        if extra_updates:
            # Зливаємо словники
            # Увага: тут треба обережно, щоб не затерти теги АІ, але наші важливіші
            # Для простоти передамо skill_impact окремо, бо АІ його сам не згенерує
            final_updates["skill_impact"] = extra_updates.get("skill_impact")
            if "time_passed" in extra_updates: final_updates["time_passed"] = "days"

        profile, logs = apply_system_impacts(profile, final_updates)


        # Перевірка на смерть
        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"

        save_user_data(user_id, profile, profile.get("Ім'я"))

        short_turn_history = summarize_turn(story)
        short_user_input = summarize_turn(user_input)
        history.append({"role": "User", "content": short_user_input})
        history.append({"role": "GM", "content": short_turn_history})
        #history.append({"role": "GM", "content": story})
        session['history'] = history[-30:]

        change_log = "\n\n📊 *Системні зміни:*\n" + "\n".join(logs) if logs else ""
        if logs:
            change_log = "\n\n📊 " + " | ".join(logs)
        return story + change_log


    except Exception as e:
        import traceback
        traceback.print_exc()  # Це покаже в консолі точний рядок помилки
        print(f"❌ Помилка AI: {e}")
        return f"Щось пішло не так... (Помилка: {str(e)})"  # Виведемо помилку гравцю, щоб ви її побачили



# ================= ОБРОБНИКИ ТЕЛЕГРАМ =================

@bot.message_handler(commands=['start'])
def start_handler(message):
    """Початок гри: вибір регіону"""
    bot.send_message(message.chat.id, "🗺️ *Зчитую карту Вестеросу...*", parse_mode='Markdown')
    regions = get_unique_regions()

    if not regions:
        bot.send_message(message.chat.id, "❌ Не знайдено регіонів у таблиці 'Доми' (або помилка доступу).")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(r, callback_data=f"reg_{r}") for r in regions]
    markup.add(*btns)

    user_sessions[message.chat.id] = {"state": "REGION_SELECT", "history": []}
    bot.send_message(message.chat.id, "📍 *Оберіть регіон:*", reply_markup=markup, parse_mode='Markdown')


# 1. ОБРАНО РЕГІОН -> ПОКАЗАТИ ДОМИ
@bot.callback_query_handler(func=lambda call: call.data.startswith("reg_"))
def handle_region_selection(call):
    print(f"🔘 Натиснуто регіон: {call.data}")  # ДІАГНОСТИКА

    chat_id = call.message.chat.id

    try:
        parts = call.data.split("_")
        if len(parts) < 2:
            print("❌ Помилка: Невірний формат callback_data")
            return

        region_name = parts[1]

        # 2. ІНІЦІАЛІЗАЦІЯ СЕСІЇ
        if chat_id not in user_sessions:
            user_sessions[chat_id] = {}

        user_sessions[chat_id]['temp_region'] = region_name

        # 3. Отримуємо список домів
        houses = get_houses_by_region(region_name)

        if not houses:
            # Якщо список пустий - показуємо спливаюче повідомлення
            bot.answer_callback_query(call.id, f"У регіоні {region_name} немає доступних домів!", show_alert=True)
            return

        # 4. Будуємо меню
        markup = types.InlineKeyboardMarkup(row_width=2)

        buttons = []
        for house in houses:
            # Обрізаємо назву, щоб не перевищити ліміт Telegram (64 байти)
            clean_house = house.strip()
            buttons.append(types.InlineKeyboardButton(clean_house, callback_data=f"house_{clean_house[:20]}"))

        markup.add(*buttons)

        # Кнопка НАЗАД
        btn_back = types.InlineKeyboardButton("⬅️ Назад до регіонів", callback_data="back_to_regions")
        markup.add(btn_back)

        # 5. Редагуємо повідомлення
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🗺 Ви обрали регіон: *{region_name}*.\nОберіть Дім:",
            parse_mode="Markdown",
            reply_markup=markup
        )

    except Exception as e:
        print(f"❌ КРИТИЧНА ПОМИЛКА в handle_region_selection: {e}")
        import traceback
        traceback.print_exc()  # Покаже де саме впало
        bot.answer_callback_query(call.id, "Сталася помилка. Спробуйте /start")


# 2. ОБРАНО ДІМ -> ПОКАЗАТИ ПЕРСОНАЖІВ
@bot.callback_query_handler(func=lambda call: call.data.startswith("house_"))
def handle_house_selection(call):
    chat_id = call.message.chat.id
    house_name = call.data.split("_")[1]

    # 1. Зберігаємо вибраний дім
    if chat_id not in user_sessions: user_sessions[chat_id] = {}
    user_sessions[chat_id]['house_name'] = house_name

    # 2. Отримуємо список канонічних персонажів (ваша функція)
    # (Переконайтеся, що вона повертає список імен, напр. ["Джон Сноу", "Робб Старк"])
    characters = get_canon_characters(house_name)

    markup = types.InlineKeyboardMarkup(row_width=1)

    # Кнопки для кожного персонажа
    for char in characters:
        # callback_data має ліміт 64 байти, тому обережно з довгими іменами
        # Можна використовувати скорочення або індекси, якщо імена дуже довгі
        markup.add(types.InlineKeyboardButton(char, callback_data=f"char_{char[:20]}"))

    # Додаємо опцію "Свій персонаж" (якщо у вас така була)
    markup.add(types.InlineKeyboardButton("✨ Створити свого...", callback_data="char_custom"))

    # 3. КНОПКА "НАЗАД" (Повертає до списку домів)
    markup.add(types.InlineKeyboardButton("⬅️ Назад до списку Домів", callback_data="back_to_houses"))

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🏰 Дім: *{house_name}*.\nКим ви хочете бути?",
        parse_mode="Markdown",
        reply_markup=markup
    )


# 3. ОБРАНО ПЕРСОНАЖА -> ГЕНЕРАЦІЯ І СТАРТ
@bot.callback_query_handler(func=lambda call: call.data.startswith("char_"))
def handle_character_selection(call):
    chat_id = call.message.chat.id
    selection = call.data.split("_")[1]

    # Видаляємо меню, щоб не клікнули двічі
    bot.delete_message(chat_id, call.message.message_id)

    if selection == "custom":
        # Якщо обрали "Створити свого" - запитуємо ім'я
        msg = bot.send_message(chat_id, "✍️ Як зватимуть вашого героя?")
        bot.register_next_step_handler(msg, process_character_name_step)
    else:
        # Якщо обрали канонічного персонажа - зчитуємо повне ім'я з кнопки (або з data)
        # Тут selection - це те, що ми передали в callback_data
        char_name = selection

        bot.send_message(chat_id, f"⚔️ Ви обрали долю: *{char_name}*...", parse_mode='Markdown')

        # ЗАПУСК ГРИ
        start_game_with_character(chat_id, char_name)


# Функція для обробки введеного вручну імені (для custom)
def process_character_name_step(message):
    char_name = message.text
    start_game_with_character(message.chat.id, char_name)


def start_game_with_character(chat_id, char_name):
    """Фінальна ініціалізація гри для конкретного гравця"""
    user_id = chat_id  # У приватних чатах це одне й те саме

    session = user_sessions[chat_id]
    house_name = session['house_name']

    # Отримуємо інфо про Дім
    house_data = get_house_stats_data(house_name)

    # Генеруємо профіль через AI
    full_profile = generate_initial_stats(char_name, house_name, house_data)

    if full_profile:
        # 1. ЗБЕРІГАЄМО В НОВУ БД
        if save_user_data(user_id, full_profile, char_name):
            session['state'] = "GAME_ACTIVE"
            session['character_name'] = char_name
            session['history'] = []  # Очищаємо історію в пам'яті для нової гри

            # 2. Технічне повідомлення
            stats_msg = (
                f"✅ *Персонажа створено!*\n"
                f"👤 **{full_profile.get('Ім' + chr(39) + 'я')}**\n"
                f"📍 Локація: _{full_profile.get('Поточне місцезнаходження')}_"
            )
            bot.send_message(chat_id, stats_msg, parse_mode='Markdown')

            # 3. Художній вступ
            bot.send_chat_action(chat_id, 'typing')
            intro_story = get_narrative_intro(full_profile)

            send_safe_message(chat_id, intro_story)
            bot.send_message(chat_id, "⚔️ Ваш шлях починається...", reply_markup=get_main_menu())

            # 4. Записуємо в історію
            session['history'].append({"role": "GM", "content": intro_story})
        else:
            bot.send_message(chat_id, "❌ Помилка запису в базу даних.")
    else:
        bot.send_message(chat_id, "❌ Помилка генерації профілю.")


# Обробка введення імені для кастомного персонажа
@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get('state') == "CUSTOM_CHAR_NAME")
def handle_custom_name(message):
    chat_id = message.chat.id
    char_name = message.text

    bot.send_message(chat_id, f"🎲 Створюю унікального героя: *{char_name}*...", parse_mode='Markdown')
    start_game_with_character(chat_id, char_name)


# --- ОБРОБНИКИ МЕНЮ ---

@bot.message_handler(func=lambda m: m.text == "📜 Профіль")
def show_profile_handler(message):
    chat_id = message.chat.id

    # Перевірка чи гра почалась
    if chat_id not in user_sessions or user_sessions[chat_id].get('state') != "GAME_ACTIVE":
        bot.send_message(chat_id, "Спершу почніть гру через /start")
        return

    bot.send_chat_action(chat_id, 'typing')

    profile, _ = get_user_data(chat_id)

    if profile:
        text = (
            f"👤 *{profile.get('Ім' + chr(39) + 'я', 'Невідомий')}*\n"
            f"🏠 Дім: {profile.get('Дім')}\n"
            f"📍 {profile.get('Поточне місцезнаходження')}\n"
            f"📅 {profile.get('Ігровий час', 'Невідомий час')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"❤️ Здоров'я: {profile.get('Здоров' + chr(39) + 'я')} | ⚡ Енергія: {profile.get('Енергія')}\n"
            f"⚔️ Бойові: {profile.get('Бойові навички')} | 🛡️ Військові: {profile.get('Військові навички')}\n"
            f"🍷 Інтрига: {profile.get('Інтрига')} | ⚖️ Управління: {profile.get('Управління')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🗣 Репутація: {profile.get('Репутація (Рідний регіон)')}\n"
            f"💀 Вороги: {profile.get('Вороги')}"
        )
        bot.send_message(chat_id, text, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "❌ Не вдалося зчитати профіль.")


@bot.message_handler(func=lambda m: m.text == "🎒 Інвентар")
def show_inventory_handler(message):
    chat_id = message.chat.id
    if chat_id not in user_sessions: return

    bot.send_chat_action(chat_id, 'typing')
    profile, _ = get_user_data(chat_id)

    if profile:
        gold = profile.get('Особисте Золото', 0)
        items = profile.get('Інвентар', 'Пусто')
        weapon = profile.get('Зброя', '-')
        armor = profile.get('Броня', '-')

        text = (
            f"🎒 *Ваш мішок:*\n\n"
            f"💰 *Золото:* {gold} драконів\n"
            f"⚔️ *Зброя:* {weapon}\n"
            f"🛡 *Броня:* {armor}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Речі:* {items}"
        )
        bot.send_message(chat_id, text, parse_mode='Markdown')


@bot.message_handler(func=lambda m: m.text == "🎲 Кинути кубик")
def roll_dice_handler(message):
    import random
    result = random.randint(1, 100)

    # Інтерпретація результату
    if result == 1:
        outcome = "😱 КРИТИЧНИЙ ПРОВАЛ!"
    elif result <= 10:
        outcome = "💀 Жахливий провал"
    elif result <= 50:
        outcome = "❌ Невдача"
    elif result <= 90:
        outcome = "✅ Успіх"
    elif result < 100:
        outcome = "🔥 Чудовий успіх!"
    else:
        outcome = "👑 КРИТИЧНИЙ УСПІХ!"

    msg = f"🎲 Ви кинули d100:\n\n**Result: {result}**\n_{outcome}_"

    # Відправляємо в чат
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

    # ДОДАТКОВО: Можна додати цей кидок в історію, щоб AI його побачив!
    session = user_sessions.get(message.chat.id)
    if session and 'history' in session:
        session['history'].append(
            {"role": "User", "content": f"[СИСТЕМА]: Гравець кинув кубик і випало {result} ({outcome})."})


@bot.message_handler(func=lambda m: m.text == "🆘 Допомога")
def help_handler(message):
    text = (
        "📜 *Правила Гри:*\n"
        "1. Це рольова гра. Пишіть дії від першої особи (\"Я йду до короля...\").\n"
        "2. Ви можете робити все, що завгодно, але світ реагує реалістично.\n"
        "3. Ваші характеристики (Сила, Інтрига) впливають на успіх дій.\n"
        "4. Використовуйте 🎲 Кубик у спірних ситуаціях, якщо хочете довіритись долі.\n"
        "5. Щоб почати спочатку: /start"
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(func=lambda m: m.text == "🔄 Рестарт")
def restart_request_handler(message):
    chat_id = message.chat.id

    # Створюємо кнопки "під повідомленням" (Inline)
    markup = types.InlineKeyboardMarkup()
    btn_yes = types.InlineKeyboardButton("✅ Так, почати нову гру", callback_data="restart_confirm")
    btn_no = types.InlineKeyboardButton("❌ Скасувати", callback_data="restart_cancel")
    markup.add(btn_yes, btn_no)

    bot.send_message(
        chat_id,
        "⚠️ *УВАГА!*\nВи збираєтесь видалити свого персонажа і почати все спочатку.\nЦю дію неможливо скасувати.",
        parse_mode='Markdown',
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("restart_"))
def callback_restart_handler(call):
    chat_id = call.message.chat.id

    # Прибираємо кнопки після натискання, щоб не можна було клацнути двічі
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)

    if call.data == "restart_confirm":
        bot.send_message(chat_id, "💀 Старий світ зникає у темряві...")

        # Очищаємо сесію
        if chat_id in user_sessions:
            user_sessions[chat_id] = {}  # Скидаємо пам'ять

        # Викликаємо функцію старту (те саме, що /start)
        # Передаємо message об'єкт з callback, щоб start_handler зрозумів
        msg = call.message
        msg.from_user = call.from_user  # Підміна автора для коректної роботи
        start_handler(msg)

    elif call.data == "restart_cancel":
        bot.send_message(chat_id, "Фух, пригода продовжується! Будьте обережні.")


@bot.callback_query_handler(func=lambda call: call.data == "back_to_regions")
def back_to_regions_handler(call):
    """Повертає користувача до списку регіонів"""
    chat_id = call.message.chat.id

    # Отримуємо список регіонів
    regions = get_unique_regions()

    markup = types.InlineKeyboardMarkup(row_width=2)
    # Генеруємо кнопки регіонів
    btns = [types.InlineKeyboardButton(r, callback_data=f"reg_{r}") for r in regions]
    markup.add(*btns)

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="📍 *Оберіть регіон:*",
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.callback_query_handler(func=lambda call: call.data == "back_to_houses")
def back_to_houses_handler(call):
    """Повертає до вибору дому (всередині вибраного регіону)"""
    chat_id = call.message.chat.id

    # Дістаємо з пам'яті регіон, який ми зберегли раніше
    region_name = user_sessions.get(chat_id, {}).get('temp_region', 'Північ')

    # Отримуємо доми
    houses = get_houses_by_region(region_name)

    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(h, callback_data=f"house_{h}") for h in houses]
    markup.add(*buttons)

    # І тут теж не забуваємо кнопку "Назад" (до регіонів)
    markup.add(types.InlineKeyboardButton("⬅️ Назад до регіонів", callback_data="back_to_regions"))

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🗺 Регіон: *{region_name}*.\nОберіть Дім:",
        parse_mode="Markdown",
        reply_markup=markup
    )


# Обробка ігрових повідомлень
# Обробка ігрових повідомлень
@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get('state') == "GAME_ACTIVE")
def handle_game_turn(message):
    chat_id = message.chat.id
    user_text = message.text

    # Ігноруємо кнопки меню
    if user_text in ["📜 Профіль", "🎒 Інвентар", "🎲 Кинути кубик", "🆘 Допомога", "🔄 Рестарт"]:
        return

    bot.send_chat_action(chat_id, 'typing')

    # 1. ОТРИМУЄМО ПРОФІЛЬ (для контексту валідатора)
    profile, _ = get_user_data(chat_id)
    if not profile:
        bot.send_message(chat_id, "Помилка профілю. Натисніть /start")
        return

    # 2. --- ЗАПУСК ВАРТОВОГО РЕАЛЬНОСТІ ---
    is_valid, refusal_reason = validate_action(user_text, profile)

    if not is_valid:
        # Якщо дія неадекватна - відшиваємо гравця, не чіпаючи історію і головного GM
        bot.reply_to(message, f"🚫 *Дія відхилена:*\n_{refusal_reason}_", parse_mode='Markdown')
        return
    # --------------------------------------

    # 3. Якщо все ок, запускаємо основний сюжет (як і раніше)
    response = process_game_turn(chat_id, user_text)
    send_safe_message(chat_id, response, reply_to_message_id=message.message_id)


app = Flask('')


@app.route('/')
def home():
    return "Bot is alive!"


def run():
    # Render автоматично дає порт через змінну оточення PORT
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))


def keep_alive():
    t = Thread(target=run)
    t.start()


# -----------------------------

# Запуск
if __name__ == "__main__":
    print("🤖 Бот запущено! Valar Morghulis.")

    load_lore_data()

    keep_alive()

    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"❌ Помилка polling: {e}")
