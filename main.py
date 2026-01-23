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
=== ЧАСОВА ЛІНІЯ: 298 рік від В.Е. (Кінець Довгого Літа) ===
Світ завмер в очікуванні катастрофи. "Зима Близько", і це відчувається у повітрі.

1. ПОЛІТИЧНА СИТУАЦІЯ (ПОРОХОВА БОЧКА):
   - Залізний Трон: Роберт Баратеон. Колишній "Демон Тризубця" якого боялися всі тепер жирний,  вічно п'яний і байдужий до правління. Всією душею ненавидить Таргарієнів. Країна в боргах перед Ланністерами, Залізним Банком та іншими.
   - Королева: Серсея Ланністер. Вона оточує двір своїми людьми. Ланністери поводяться так, ніби вже правлять.
   - Десниця Короля: Джон Аррен помер від гарячки. Тепер королю потрібна заміна.
   - Поточна Головна Подія: Величезний королівський кортеж повільно повзе Королівським Трактом на Північ, до Вінтерфеллу і прибуде через кілька днів.

2. ПРИХОВАНІ ЗАГРОЗИ (ТІЛЬКИ ДЛЯ GM - ГРАВЕЦЬ НЕ ПОВИНЕН ЦЬОГО ЗНАТИ):
   - Секрет: Діти королеви — бастарди від інцесту з Джейме (це найнебезпечніша таємниця світу).
   - Секрет 2: Джона Аррена отруїла Ліза Аррен за змовою з Петіром Бейлішем.
   - Північ: Нічна Варта слабка як ніколи. Розвідники зникають за Стіною. Старі лякають дітей казками про Інших, але лорди в це не вірять.
   - Ессос: Візеріс Таргарієн продає сестру Дейнеріс кхалу Дрого. Драконів ще немає (це поки що скам'янілі яйця).

3. АТМОСФЕРА СВІТУ (GRIMDARK):
   - Економіка: Ціни на зерно ростуть. Селяни ховають запаси, боячись довгої Зими.
   - Закон: На дорогах небезпечно. "Розбійники ховаються за кожним кущем". Лицарі часто не кращі за розбійників.
   - Настрій: Тривога. У повітрі пахне війною, хоча мечі ще у піхвах.

ВАЖЛИВО ДЛЯ СЮЖЕТУ:
- Нед Старк ще живий і знаходиться у Вінтерфеллі, отримав звістку про дезертира з нічної варти.
- Війна П'яти Королів ЩЕ НЕ ПОЧАЛАСЯ.
"""

# ================= ІНІЦІАЛІЗАЦІЯ =================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    #Вибір моделі тут
    model_name='gemma-3-27b-it',
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
        # Знаходимо поточну фазу
        current_phase = "День"
        for t in times_of_day:
            if t in time_str:
                current_phase = t
                break

        new_phase = current_phase
        day_increment = 0

        # Логіка зміни часу
        if time_tag == "short":
            pass  # Діалог, час майже не йде
        elif time_tag == "medium":
            # +1 фаза (Ранок -> День)
            try:
                idx = times_of_day.index(current_phase)
                if idx + 1 < len(times_of_day):
                    new_phase = times_of_day[idx + 1]
                else:
                    new_phase = "Ранок"
                    day_increment = 1
            except:
                pass
        elif time_tag == "long":
            # +2 фази (Ранок -> Вечір)
            try:
                idx = times_of_day.index(current_phase)
                if idx + 2 < len(times_of_day):
                    new_phase = times_of_day[idx + 2]
                else:
                    new_phase = "Ранок"
                    day_increment = 1
            except:
                pass
        elif time_tag == "sleep" or time_tag == "days":
            new_phase = "Ранок"
            day_increment = 1

        # Оновлюємо день
        if day_increment > 0:
            import re
            d_match = re.search(r'День (\d+)', time_str)
            if d_match:
                day_num = int(d_match.group(1)) + day_increment
                time_str = re.sub(r'День \d+', f'День {day_num}', time_str)
            else:
                # Якщо формат збився, додаємо вручну
                time_str += f", День {1 + day_increment}"

        # Оновлюємо фазу в рядку
        time_str = time_str.replace(current_phase, new_phase)

        profile["Ігровий час"] = time_str
        logs.append(f"⏳ Час: {current_phase} -> {new_phase}")

    # === 2. ОБРОБКА ЗДОРОВ'Я ===
    damage_tag = ai_impacts.get("health_impact", "none")
    hp_change = 0

    if damage_tag == "heal_small":
        hp_change = random.randint(5, 10)
    elif damage_tag == "heal_full":
        hp_change = 100
    elif damage_tag == "dmg_light":
        hp_change = -random.randint(1, 10)  # Подряпина
    elif damage_tag == "dmg_medium":
        hp_change = -random.randint(15, 30)  # Поранення
    elif damage_tag == "dmg_heavy":
        hp_change = -random.randint(40, 70)  # Критичний удар
    elif damage_tag == "dmg_fatal":
        hp_change = -100  # Смерть

    if hp_change != 0:
        old_hp = safe_int(profile.get("Здоров'я", 100))
        new_hp = max(0, min(100, old_hp + hp_change))
        profile["Здоров'я"] = new_hp
        logs.append(f"❤️ Здоров'я: {hp_change:+}")

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
    """Знаходить дані конкретного гравця за його Telegram ID (Безпечний метод)"""
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
    """Зберігає або оновлює дані гравця (Безпечний метод)"""
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
                print(f"⚠️ Спроба {attempt+1}: Отримано не JSON. Текст: {response.text[:50]}...")
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
        "Таргарієн": ["Данерис Таргарієн", "Візеріс Таргарієн", "Еймон Таргарієн"],
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
    Напиши список 4 найвідоміших персонажів з Дому {house_name} (Гра Престолів).
    Поверни ТІЛЬКИ JSON масив рядків.
    Приклад: ["Ім'я 1", "Ім'я 2"]
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
    ТИ — АВТОР РОМАНУ "ГРА ПРЕСТОЛІВ" (ДЖОРДЖ МАРТІН).
    Твоя мета: Написати першу сцену для персонажа (Пролог).

    === ГЕРОЙ ===
    {profile_json}

    === ЛОКАЦІЯ СТАРТУ ===
    {current_location}
    (Опиши цю локацію максимально атмосферно, використовуючи канон 298 року В.Е.)

    === КОНТЕКСТ ЧАСУ ===
    {GAME_ERA_CONTEXT}

    === ВИМОГИ ДО ТЕКСТУ (GRIMDARK INTRO) ===
    1. **Атмосфера:** Почни з сенсорних деталей. Запах (море, гній, пахощі, сталь), температура (холод Півночі чи спека Ессосу), звуки.
    2. **Стиль:** Похмурий реалізм. Ніяких "привітних торговців". Якщо це Пентос — там пахне прянощами і рабством. Якщо Вінтерфелл — вовками і зимою.
    3. **Становище героя:** - Якщо це Вигнанець (як Джорах) — підкресли його тугу за домом або злидні.
       - Якщо це Лорд — підкресли тягар відповідальності та лицемірство двору.
       
    4.**СЮЖЕТНИЙ ГАЧОК (INCITING INCIDENT) - КАНОН:**
       Ти мусиш прив'язати сцену до РЕАЛЬНИХ подій початку першої книги, залежно від локації:
       
       - **ПІВНІЧ (Вінтерфелл):** * Головна подія: Лорд Еддард готується стратити дезертира з Нічної Варти (Гареда). 
         * Або: Отримано звістку, що величезний кортеж Короля Роберта їде сюди.
         
       - **ЕССОС (Пентос):** * Головна подія: Ілліріо Мопатіс готує Дейнеріс до оглядин Кхалом Дрого.
         * Або: Візеріс нервує через затримку весілля.
         
       - **КОРОЛІВСЬКА ГАВАНЬ:** * Головна подія: Двір у жалобі. Десниця Джон Аррен раптово помер. Чутки про отруту.
         * Або: Інтриги навколо того, хто стане новим Десницею.
         
       - **СТІНА (Чорний Замок):** * Головна подія: Прибуття нових рекрутів (включно з Тіріоном як гостем).
         * Або: Розвідники зникли у Зачарованому Лісі. Знайдено дивні трупи.

       **ЯКЩО ЦЕ КАНОНІЧНИЙ ПЕРСОНАЖ ({profile.get("Ім'я")}):** Почни саме з тієї сцени, де ми вперше зустрічаємо його в книзі.
       
       **ЯКЩО ЦЕ ВИГАДАНИЙ ПЕРСОНАЖ:**
       Впиши його в ці події як свідка або учасника (наприклад, він стоїть у натовпі під час страти або служить при дворі під час трауру).

    5. **ЗАБОРОНИ:**
       - НІКОЛИ не пиши дії за героя ("Ви взяли меч").
       - Зупини сцену рівно в той момент, коли герой має відреагувати.
       
    === ФІНАЛ (КРИТИЧНО ВАЖЛИВО) ===
    Твоя відповідь ОБОВ'ЯЗКОВО має закінчуватися ПИТАННЯМ, яке ставить гравця перед конкретним вибором.
    - ПОГАНО: "Що ви робите?" (Занадто розмито).
    - ДОБРЕ: "Вартовий підозріло мружиться, чекаючи пояснень. Ви покажете йому печатку Дому чи спробуєте підкупити?"
    - ДОБРЕ: "Крики наближаються. Ви сховаєтеся в тіні чи вийдете назустріч небезпеці?"

    Напиши художній текст (3-4 абзаци). Українською мовою.
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
    Створи стартовий профіль для RPG "Гра Престолів".

    КОНТЕКСТ ЧАСУ:
    {context}

    ПЕРСОНАЖ: {char_name}
    ДІМ: {house_name} (Походження: {origin_region})

    === ПРАВИЛА ЛОКАЦІЇ (КРИТИЧНО) ===
    Визнач "Поточне місцезнаходження" на основі КАНОНУ першої книги (298 р. В.Е.), а не регіону походження.
    Приклади винятків:
    1. Джорах Мормонт -> Дім з Півночі, але старт в Ессосі (Пентос).
    2. Теон Грейджой -> Дім з Залізних Островів, але старт у Вінтерфеллі (вихованець).
    3. Джейме Ланністер -> Дім із Західних Земель, але старт у Королівській Гавані.
    4. Дейнеріс Таргарієн -> Старт у Пентосі (Ессос).
    5. Станніс Баратеон -> Старт на Драконячому камені.

    Якщо персонаж не має специфічного канонічного місця (або це вигаданий персонаж), використовуй регіон походження ({origin_region}).

    Заповни JSON (ключі українською!):
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
    Перевіряє, чи не намагається гравець зламати гру чітами або анахронізмами.
    """
    char_name = profile.get("Ім'я", "Герой")

    prompt = f"""
    Ти — Вартовий Реалізму (Lore Keeper) для гри "Гра Престолів" (Середньовічне фентезі).

    Твоє завдання: Перевірити дію гравця на "легальність".

    ГРАВЕЦЬ: {char_name}
    ДІЯ: "{user_input}"
    
    === ПРАВИЛА АГЕНТНОСТІ (ХТО КИМ КЕРУЄ) ===
    1. Гравець керує ТІЛЬКИ персонажем {char_name}.
    2. ЗАБОРОНЕНО писати дії за NPC (інших персонажів).
       - ❌ НЕДОПУСТИМО: "Джорах Мормонт вихоплює меч і вбиває Короля Ночі." (Гравець керує Джорахом).
       - ✅ ДОПУСТИМО: "Я кричу Джораху: 'Вбий його!'" (Гравець керує своїм голосом).
       - ✅ ДОПУСТИМО: "Я наказую Джораху атакувати." (Гравець віддає наказ, а виконає його NPC чи ні — вирішить GM).

    3. ЗАБОРОНЕНО описувати НАСЛІДКИ своїх дій як факт.
       - ❌ НЕДОПУСТИМО: "Я вдаряю стражника, і голова злітає з плечей." (Гравець вирішив результат).
       - ✅ ДОПУСТИМО: "Я щосили б'ю стражника мечем у шию." (Гравець описує спробу, результат за GM).

    === КРИТЕРІЇ ЗАБОРОНИ (ПОВЕРНИ is_valid: false) ===
    1. **Анахронізми:** Згадка сучасних технологій (F-16, телефон, автомат, інтернет, НАТО, Байден).
    2. **Чіт-коди:** Спроби змінити свої стати ("Дай мені 100000 золота", "Я стаю безсмертним", "Всі вороги помирають").
    3. **Мета-геймінг:** Спроби керувати сюжетом як автор ("Я хочу, щоб зараз прилетів дракон і всіх врятував", "Пропустити гру до фіналу").
    4. **Абсурд:** Дії, фізично неможливі для людини (якщо це не магія, доступна в лорі, наприклад, варги).

    === КРИТЕРІЇ ДОЗВОЛУ (ПОВЕРНИ is_valid: true) ===
    1. Дозволяй ризиковані дії ("Я нападаю на короля" — це тупо, але легально. Головний GM його вб'є).
    2. Дозволяй жарти та розмови.
    3. Дозволяй будь-які дії, які можливі у фізичному світі Вестеросу.

    ВІДПОВІДЬ (JSON):
    {{
        "is_valid": true або false,
        "refusal_reason": "Короткий іронічний коментар українською, чому це неможливо (тільки якщо false). Наприклад: 'Боги не знають, що таке F-16'."
    }}
    """

    # Використовуємо ту саму модель (вона швидка)
    # Можна зменшити max_tokens, бо відповідь коротка
    try:
        response = model.generate_content(prompt)
        result = clean_and_parse_json(response.text)

        # Якщо JSON не розпарсився, вважаємо дію допустимою (презумпція невинуватості),
        # щоб не блокувати гру через помилку АІ.
        if not result:
            return True, ""

        return result.get("is_valid", True), result.get("refusal_reason", "Це неможливо.")

    except Exception as e:
        print(f"⚠️ Помилка валідатора: {e}")
        return True, ""

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

    # --- ПРОМПТ ---
    prompt = f"""
    ТИ — Джордж Мартін (Автор Гри Престолів) що грає БЕЗЖАЛІСНОГО ГЕЙМ-МАЙСТЕРА (GM) У СВІТІ "ГРА ПРЕСТОЛІВ".
    Твоя задача: Вести гру, балансуючи між сюжетом, свободою гравця та ЧІТКИМИ МЕХАНІКАМИ, створити реалістичну, небезпечну та похмуру історію. Світ не крутиться навколо гравця.
    Уяви що гравець це один з персонажів якого ти задумав вбити

    === ГЕРОЙ ===
    {profile_json}

    === КОНТЕКСТ СВІТУ ===
    {GAME_ERA_CONTEXT}
    {context_knowledge}
    
    === ЗАВДАННЯ ===
    1. Напиши художню відповідь на дію гравця.
    2. Визнач КАТЕГОРІЇ наслідків (теги), а система сама порахує цифри.

    === ДОСТУПНІ ТЕГИ (ВИБИРАЙ МУДРО) ===
    1. **time_passed** (Скільки пройшло часу):
       - "short" (кілька хвилин - діалог)
       - "medium" (година-дві - прогулянка, дослідження)
       - "long" (півдня - подорож, робота)
       - "sleep" (ніч - відпочинок)
    
    2. **health_impact** (Наслідки для здоров'я):
       - "none" (без змін)
       - "dmg_light" (синець, подряпина)
       - "dmg_medium" (поранення мечем, опік)
       - "dmg_heavy" (критична травма, втрата свідомості)
       - "dmg_fatal" (смерть)
       - "heal_small" (відпочинок)
    
    3. **gold_impact** (Економіка):
       - "none"
       - "spend_small" (їжа, дрібниці)
       - "spend_medium" (зброя, одяг)
       - "spend_large" (коні, будинки)
       - "earn_small" (знайшов монету)
       - "earn_medium" (нагорода за квест)
       - "earn_large" (грандіозний скарб - рідкість!)
    
    === КРИТИЧНІ ПРАВИЛА (СЮЖЕТ) ===
    1. СЛУХАЙ ГРАВЦЯ: Якщо гравець пише "Я йду далі" або "Ігнорую" — ТИ ЗОБОВ'ЯЗАНИЙ змінити сцену.
       - ЗАБОРОНЕНО описувати те саме тіло/місце, якщо гравець пішов.
       - Опиши дорогу, нову загрозу або прибуття в таверну.
    2. НЕ ГРАЙ ЗА ГРАВЦЯ: Не пиши "Ви вирішили оглянути...", якщо гравець не казав цього.   
    
    === АТМОСФЕРА ТА СКЛАДНІСТЬ (GRIMDARK) ===
    1. **Смертність:** У цьому світі помирають усі. Гравець НЕ МАЄ "сюжетної броні" (Plot Armor).
       - Якщо гравець, маючи слабкі навички, лізе в бій з лицарем — він має програти і отримати тяжкі поранення або померти.
       - Якщо гравець ображає могутнього лорда — його страчують або кидають у темницю.
       - Не бійся вбивати персонажа або робити його калікою, якщо це логічний наслідок його дурості.
    2. **Жорстокість:** Описуй світ натуралістично. Бруд, кров, зрада, несправедливість. Не прикрашай дійсність.
    3. **Наслідки:** Кожна дія має ціну. Перемога ніколи не буває чистою (ти переміг, але зламав руку / втратив друга / зіпсував репутацію).
    
    === ЗАБОРОНА АВТО-ДІЙ (КРИТИЧНО ВАЖЛИВО!!!) ===
    1. НІКОЛИ не пиши дії за гравця. Ти керуєш світом, NPC і погодою. Гравець керує ТІЛЬКИ своїм персонажем.
    2. ЗАБОРОНЕНО писати фрази типу: "Ви вирішили...", "Ви відповіли...", "Ви погодилися і пішли...".
    3. ЗАБОРОНЕНО моделювати діалог за гравця. Не вигадуй його реплік.
    4. ЗУПИНЯЙСЯ перед моментом вибору.
       - ПОГАНО: "Ви підходите до вартового, даєте йому монету і він вас пропускає." (Ти вирішив за гравця, що він дав монету).
       - ДОБРЕ: "Ви підходите до вартового. Він підозріло мружиться і перегороджує шлях списом. 'Вхід платний', - гаркає він. Що ви робите?"
       
    === ПРАВИЛА ДІАЛОГІВ (РЕЖИМ PING-PONG) — КРИТИЧНО! ===
    1. Якщо гравець починає розмову ("Я підходжу до...", "Кажу йому..."):
       - Напиши ТІЛЬКИ ПЕРШУ РЕАКЦІЮ або ВІДПОВІДЬ NPC.
       - НІКОЛИ не пиши продовження діалогу за гравця.
       - НІКОЛИ не підсумовуй розмову ("Ви поговорили про все і розійшлися").
    2. Швидкість: Один хід гравця = Одна репліка NPC.
    3. Характер NPC: Вони можуть брехати, перебивати, ігнорувати або йти геть, якщо гравець їм набрид. Не роби їх "довідковими бюро".
    

    === ПРАВИЛА ПЕРЕВІРКИ НАВИЧОК ===
    1. Не вирішуй успіх випадково. Дивись на стат гравця (0-100) за формулою: Стат + кидок кубика (0-30).
       - < 60: Майже гарантована невдача.
       - 60-89: Ризиковано, можливий провал.
       - 90-110: Успіх у стандартних діях.
       - > 110: Майстерне виконання.
    2. **Бойові навички:** Використовуй для дуелей, турнірів чи інших боїв.
    3. **Військові навички** Використовуй для розробки стратегії, тактики, командування загоном/армією. 
    4. **Інтрига:** Використовуй, коли гравець бреше, намагається щось дізнатися, з кимось домовитись чи інтригувати.
    5. **Управління:** Використовуй для торгівлі або командування людьми.
    6. ЛІМІТИ: Стати не можуть бути менше 0 або більше 100.
    
    ВАЖЛИВО: Якщо гравець не має профільної навички для дії (наприклад, намагається битися мечем з навичкою 10) — він ГАРАНТОВАНО програє профісіоналу.

    === СЮЖЕТНИЙ РЕЖИМ ===
    1. **Абсолютна Свобода:** Гравець може робити що завгодно. Не кажи "ні", кажи "ти спробував, і ось що сталося".
    2. **Сюжетний Магніт:** Якщо дія гравця тривіальна ("йду гуляти"), підкинь подію, яка веде до головного сюжету (знайшов лист, побачив шпигуна, зустрів важливого NPC).
    3. **Сайдквести:** Якщо гравець пропускає час ("чекаю", "далі") — згенеруй цікаву випадкову зустріч (Encounter), яка розкриває лор.
    4. **Адаптація сюжету:** Якщо дії гравця ламають канон — адаптуй світ, створюй наслідки, але не забороняй дію. Якщо гравець ламає канон — світ реагує агресивно (оголошують у розшук, посилають вбивць).
    5. **Невпинність сюжету:** Світ живе своїм життям. Війна  безперервно насувається.
    6. **Покарання за бездіяльність:** Якщо гравець діє пасивно — світ карає його (його грабують, звинувачують у злочині, мобілізують на війну силою).
    
    === ФІНАЛ (КРИТИЧНО ВАЖЛИВО) ===
    Твоя відповідь ОБОВ'ЯЗКОВО має закінчуватися ПИТАННЯМ, яке ставить гравця перед конкретним вибором.
    - ПОГАНО: "Що ви робите?" (Занадто розмито).
    - ДОБРЕ: "Вартовий підозріло мружиться, чекаючи пояснень. Ви покажете йому печатку Дому чи спробуєте підкупити?"
    - ДОБРЕ: "Крики наближаються. Ви сховаєтеся в тіні чи вийдете назустріч небезпеці?"
    
    === ІСТОРІЯ ПОДІЙ (МИНУЛЕ) ===
    {history_text}

    === ПОТОЧНИЙ ХІД (ТЕПЕРІШНЄ) ===
    ГРАВЕЦЬ КАЖЕ/РОБИТЬ: "{user_input}"

    === ТВОЄ ЗАВДАННЯ (МАЙБУТНЄ) ===
    Напиши продовження історії, реагуючи САМЕ НА ПОТОЧНИЙ ХІД ГРАВЦЯ.
    1. Опиши наслідки дії.
    2. Якщо гравець змінив тему — підтримай нову тему, забудь стару.
    3. Закінчи ПИТАННЯМ або ДИЛЕМОЮ.
    4. Заповни JSON TAGS на основі дій гравця використовуючи ДОСТУПНІ ТЕГИ:
       - Якщо гравець рухається/чекає/спить -> заповни Часу пройшло!
       - Якщо гравець купує/знаходить речі -> заповни Новий інвентар!
       - Якщо гравець їсть/продає/втрачає речі -> заповни Втрачений інвертар!

    ВІДПОВІДЬ (JSON):
    {{
        "story": "Тут напиши детальний художній опис реакції світу на дії гравця (мінімум 3-4 речення). Обов'язково закінчи питанням.",
        "updates": {{ 
             "Часу пройшло": "...",
             "Здоров'я": 0,
             "Особисте Золото": 0
             "Новий інвентар": [],
             "Втрачений інвертар": [],
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

        impacts = ai_data.get("impacts", {})

        # --- PYTHON-МЕНЕДЖЕР ---
        profile, logs = apply_system_impacts(profile, impacts)
        # ----------------------------------

        # Перевірка на смерть
        if safe_int(profile.get("Здоров'я", 100)) <= 0:
            story += "\n\n💀 *ВАШ ДОЗОР ЗАКІНЧИВСЯ. Ви загинули.*"

        save_user_data(user_id, profile, profile.get("Ім'я"))

        history.append({"role": "User", "content": user_input})
        history.append({"role": "GM", "content": story})
        session['history'] = history[-30:]

        change_log = "\n\n📊 *Системні зміни:*\n" + "\n".join(logs) if logs else ""
        return story + change_log

        except Exception as e:
           print(f"❌ Помилка AI: {e}")
           return "Щось пішло не так... (Системна помилка)"


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



