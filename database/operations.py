import json
import gspread
import difflib
import re
import numpy as np

from database.sheets import db
from config import (
    TAB_USERS,
    TAB_HOUSES,
    TAB_CHARACTER,
    TAB_KNOWLEDGE,
    TAB_NPC
)

# ================= ГЛОБАЛЬНІ КЕШІ =================
LORE_CACHE = []
NPC_CACHE = {}


# ================= РОБОТА З БАЗОЮ ГРАВЦІВ (Users_DB) =================

def get_user_data(user_id):
    """Знаходить дані гравця за Telegram ID"""
    try:
        sheet = db.get_sheet(TAB_USERS)
        if not sheet: return None, None

        cells = sheet.findall(str(user_id), in_column=1)
        if not cells:
            return None, None

        cell = cells[0]
        raw_data = sheet.cell(cell.row, 3).value

        if raw_data:
            return json.loads(raw_data), cell.row
        return None, None
    except Exception as e:
        print(f"❌ Помилка читання БД: {e}")
        return None, None


def save_user_data(user_id, profile_data, char_name="Unknown"):
    """Зберігає або оновлює дані гравця"""
    try:
        sheet = db.get_sheet(TAB_USERS)
        if not sheet: return False

        json_str = json.dumps(profile_data, ensure_ascii=False)
        cells = sheet.findall(str(user_id), in_column=1)

        if cells:
            # Оновлення
            cell = cells[0]
            sheet.update_cell(cell.row, 3, json_str)
            sheet.update_cell(cell.row, 2, char_name)
        else:
            # Створення
            sheet.append_row([str(user_id), char_name, json_str])
        return True
    except Exception as e:
        print(f"❌ Помилка збереження: {e}")
        return False


def reset_and_fill_character_sheet(data_dict):
    """ПОВНЕ ПЕРЕЗАПИСУВАННЯ таблиці на старті гри"""
    try:
        sheet = db.get_sheet(TAB_CHARACTER)
        if not sheet: return False

        keys_col = sheet.col_values(1)

        cells_to_update = []
        for i, key in enumerate(keys_col):
            if not key: continue
            new_val = data_dict.get(key, "-")
            cells_to_update.append(gspread.Cell(i + 1, 2, new_val))

        sheet.update_cells(cells_to_update)
        return True
    except Exception as e:
        print(f"❌ Помилка ініціалізації: {e}")
        return False


# ================= РОБОТА З ДОМАМИ ТА РЕГІОНАМИ =================

def get_unique_regions():
    """Отримує список регіонів"""
    try:
        sheet = db.get_sheet(TAB_HOUSES)
        records = sheet.get_all_records()
        regions = set(row['Регіон'] for row in records if row.get('Регіон'))
        return sorted(list(regions))
    except Exception as e:
        print(f"❌ Помилка читання регіонів: {e}")
        return []


def get_houses_by_region(region):
    """Отримує список домів у регіоні"""
    try:
        sheet = db.get_sheet(TAB_HOUSES)
        records = sheet.get_all_records()
        return sorted([row['Рід'] for row in records if row.get('Регіон') == region])
    except:
        return []


def get_house_stats_data(house_name):
    """Отримує дані про Дім"""
    try:
        sheet = db.get_sheet(TAB_HOUSES)
        records = sheet.get_all_records()
        for row in records:
            if row.get('Рід') == house_name:
                return row
        return {}
    except:
        return {}


# ================= РОБОТА З ЛОРОМ (RAG / KnowledgeBase) =================
import numpy as np
from google import genai
from config import GEMINI_API_KEY

# Ініціалізація клієнта Gemini для векторів
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Глобальні змінні
LORE_VECTORS = None
LORE_CACHE = []


def get_embedding(text: str):
    """Отримує вектор тексту через безкоштовне API Gemini"""
    try:
        response = gemini_client.models.embed_content(
            model='text-embedding-004',
            contents=text
        )
        return response.embeddings[0].values
    except Exception as e:
        print(f"⚠️ Помилка отримання вектора від Gemini: {e}")
        return None


def load_lore_data():
    """Завантажує базу знань і векторизує її через Google API"""
    global LORE_CACHE, LORE_VECTORS
    try:
        sheet = db.get_sheet(TAB_KNOWLEDGE)
        if not sheet: return

        records = sheet.get_all_records()
        LORE_CACHE = [row for row in records if str(row.get('Інформація', '')).strip()]

        if not LORE_CACHE:
            print("⚠️ База лору порожня.")
            return

        print(f"📚 Завантажено {len(LORE_CACHE)} записів лору. Відправка на векторизацію в Google...")

        texts_to_embed = [
            f"{row.get('Ключові слова', '')} {row.get('Інформація', '')}"
            for row in LORE_CACHE
        ]

        # Отримуємо вектори батчем (щоб було швидко)
        response = gemini_client.models.embed_content(
            model='text-embedding-004',
            contents=texts_to_embed
        )

        # Перетворюємо список векторів у Numpy масив
        embeddings_list = [emb.values for emb in response.embeddings]
        LORE_VECTORS = np.array(embeddings_list)

        print(f"✅ [RAG] Вектори успішно створено через Gemini для {len(LORE_CACHE)} записів!")

    except Exception as e:
        print(f"⚠️ Не вдалося ініціалізувати векторну базу KnowledgeBase: {e}")
        LORE_CACHE = []
        LORE_VECTORS = None


def get_relevant_context(user_text, current_location):
    """Семантичний пошук найрелевантнішого лору"""
    if LORE_VECTORS is None or not LORE_CACHE:
        return "Немає особливих відомостей."

    try:
        search_query = f"{current_location}. {user_text}"
        query_vector_list = get_embedding(search_query)

        if not query_vector_list:
            return "Немає особливих відомостей."

        query_vector = np.array(query_vector_list)

        # Рахуємо дистанцію між масивами
        distances = np.linalg.norm(LORE_VECTORS - query_vector, axis=1)

        # Топ-3 результати
        top_k = 3
        top_indices = np.argsort(distances)[:top_k]

        found_info = []
        for idx in top_indices:
            # Для Gemini embeddings дистанція зазвичай менша, ставимо поріг ~1.2
            if distances[idx] < 1.2:
                content = LORE_CACHE[idx].get('Інформація', '')
                found_info.append(f"- {content}")

        if found_info:
            return "\n".join(found_info)

        return "Немає особливих відомостей."

    except Exception as e:
        print(f"⚠️ Помилка векторного пошуку: {e}")
        return "Немає особливих відомостей."

# ================= РОБОТА З NPC =================

def refresh_npc_database():
    """Завантажує NPC з Google Sheets у пам'ять бота"""
    global NPC_CACHE
    try:
        worksheet = db.get_sheet(TAB_NPC)
        if not worksheet: return False

        raw_data = worksheet.get_all_records()
        new_cache = {}
        count = 0

        for row in raw_data:
            status = str(row.get("Status", "Active")).strip()
            if status.lower() != "active": continue

            loc = str(row.get("Location", "GLOBAL")).strip()
            name = str(row.get("Name", "Unknown")).strip()
            is_canon = str(row.get("Is_Canon", "FALSE")).upper() == "TRUE"

            type_tag = "[CANON/BOSS]" if is_canon else "[LOCAL/BACKGROUND]"

            npc_card = f"> **{name}** {type_tag}\n"
            if row.get("Description"): npc_card += f"- **Visual:** {row.get('Description')}\n"
            if row.get("Character"): npc_card += f"- **Personality:** {row.get('Character')}\n"
            if row.get("Goal"): npc_card += f"- **Goal:** {row.get('Goal')}\n"
            if row.get("Relation_Player"): npc_card += f"- **Attitude to Player:** {row.get('Relation_Player')}\n"
            if row.get("Memory_Anchor") and str(row.get("Memory_Anchor")).strip() != "-":
                npc_card += f"- **Memory Anchor (WHY they feel this way):** {row.get('Memory_Anchor')}\n"
            if row.get("Relation_NPCs"): npc_card += f"- **Attitude to other NPC:** {row.get('Relation_NPCs')}\n"
            if row.get("Inventory") and str(row.get("Inventory")).strip() not in ["", "-"]:
                npc_card += f"- **Inventory (Items & Gold):** {row.get('Inventory')}\n"
            if row.get(
                "Secrets"): npc_card += f"- **[SECRET/GM ONLY]:** (GUIDE BEHAVIOR, DO NOT REVEAL): {row.get('Secrets')}\n"

            if loc not in new_cache:
                new_cache[loc] = []
            new_cache[loc].append(npc_card)
            count += 1

        NPC_CACHE = new_cache
        print(f"✅ [NPC DB] Завантажено {count} персонажів.", flush=True)
        return True
    except Exception as e:
        print(f"❌ [NPC DB ERROR] {e}", flush=True)
        return False


def get_location_npcs(current_location):
    """Повертає текст з описом NPC для поточної локації ТА список легальних імен."""
    found_npcs = []

    for loc_key, npcs_list in NPC_CACHE.items():
        if loc_key.lower() in current_location.lower():
            found_npcs.extend(npcs_list)

    if "GLOBAL" in NPC_CACHE:
        found_npcs.extend(NPC_CACHE["GLOBAL"])

    # Якщо нікого немає - повертаємо ДВА порожніх значення
    if not found_npcs:
        return "", []

    legal_names = []

    # Витягуємо чисті імена (як у твоїй таблиці)
    for npc_string in found_npcs:
        # Шукаємо текст до першої двокрапки (ігноруючи дефіси чи зірочки на початку)
        match = re.search(r'^[-*\s]*([^:]+):', npc_string)
        if match:
            clean_name = match.group(1).strip()
            legal_names.append(clean_name)
        else:
            # Fallback: якщо раптом немає двокрапки, беремо перші 2-3 слова
            words = npc_string.replace("-", "").strip().split()
            clean_name = " ".join(words[:2])  # Беремо перші два слова (напр. "Джон Сноу")
            legal_names.append(clean_name)

    npc_block = "=== 👥 VISIBLE NPC ROSTER (PRIORITY USE!) ===\n"
    npc_block += "GM INSTRUCTION: If player interacts with someone, PICK FROM THIS LIST first.\n"
    npc_block += "DO NOT HALLUCINATE NEW CHARACTERS IF A SUITABLE ONE IS HERE.\n\n"
    npc_block += "\n".join(found_npcs)

    # Повертаємо кортеж: текст для промпту + список ["Дейнеріс Таргарієн", "Ілліріо Мопатіс", ...]
    return npc_block, legal_names


# ================= ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ БАЗИ =================

def find_best_match(query_name, distinct_names, threshold=0.75):
    """
    Шукає найбільш схоже ім'я зі списку.
    Включає 'Анти-родинний запобіжник', щоб не плутати Дейнеріс та Візеріса Таргарієнів.
    """
    if not query_name or not distinct_names:
        return None

    query = query_name.lower().strip()

    # 1. АБСОЛЮТНИЙ ПРІОРИТЕТ: Точний збіг (ігноруючи регістр)
    for real_name in distinct_names:
        if query == real_name.lower().strip():
            return real_name

    # 2. Нечіткий пошук з базовим порогом
    matches = difflib.get_close_matches(query, distinct_names, n=1, cutoff=threshold)

    if matches:
        best_match = matches[0]
        best_match_low = best_match.lower().strip()

        # 3. АНТИ-РОДИННИЙ ЗАПОБІЖНИК (Перевірка імені)
        query_words = query.split()
        match_words = best_match_low.split()

        if query_words and match_words:
            query_first_name = query_words[0]
            match_first_name = match_words[0]

            # Математично порівнюємо ТІЛЬКИ перші імена (Візеріс vs Дейнеріс)
            first_name_ratio = difflib.SequenceMatcher(None, query_first_name, match_first_name).ratio()

            # Якщо імена відрізняються надто сильно (менше 65% збігу) — це різні люди з одним прізвищем!
            if first_name_ratio < 0.65:
                return None

        return best_match

    return None


import difflib
import json
import gspread


def update_npcs_in_db(updates, legal_names_list_deprecated=None):
    """
    Оновлює існуючих NPC.
    Вбудовано жорстку нормалізацію регістру та апострофів для difflib.
    """
    if not updates:
        return

    print(f"📝 [NPC UPDATE] Обробка змін: {json.dumps(updates, ensure_ascii=False)}")

    try:
        worksheet = db.get_sheet(TAB_NPC)
        all_values = worksheet.get_all_values()

        if not all_values: return

        headers = [h.strip().lower() for h in all_values[0]]
        col_map = {}
        target_cols = ["status", "relation_player", "memory_anchor", "goal", "secrets", "description", "character", "relation_npcs", "inventory"]

        for target in target_cols:
            if target in headers:
                col_map[target] = headers.index(target) + 1

        # Створюємо дві мапи: одну для БД, іншу для нормалізованого пошуку
        db_names_map = {}
        legal_names_lower_map = {}

        for i, row in enumerate(all_values[1:], start=2):
            raw_name = str(row[1]).strip()
            if raw_name:
                db_names_map[raw_name] = i

                # НОРМАЛІЗАЦІЯ: нижній регістр + заміна всіх видів апострофів
                norm_name = raw_name.lower().replace("’", "'").replace("`", "'").replace("‘", "'")
                legal_names_lower_map[norm_name] = raw_name

        cells_to_update = []
        global_legal_norm_names = list(legal_names_lower_map.keys())

        for update in updates:
            target_name = str(update.get("Name")).strip()
            if not target_name:
                continue

            # Нормалізуємо ім'я, яке прийшло від ШІ
            norm_target = target_name.lower().replace("’", "'").replace("`", "'").replace("‘", "'")

            # Шукаємо збіг за НОРМАЛІЗОВАНИМИ рядками
            matches = difflib.get_close_matches(norm_target, global_legal_norm_names, n=1, cutoff=0.65)

            if matches:
                best_norm_match = matches[0]
                # Відновлюємо оригінальне канонічне ім'я з бази
                real_original_name = legal_names_lower_map[best_norm_match]
                row_idx = db_names_map[real_original_name]

                if target_name != real_original_name:
                    print(f"🔧 [АВТОКОРЕКЦІЯ] '{target_name}' виправлено на '{real_original_name}'")
                else:
                    print(f"✅ [MATCH] '{real_original_name}' знайдено.")

                for field, new_val in update.items():
                    field_key = field.lower()
                    if field_key == "name": continue

                    val_str = str(new_val).strip()

                    # Фільтр пустоти
                    if not val_str or val_str.lower() in ["", "-", "none", "null", "same", "без змін"]:
                        continue

                    if len(val_str) < 3 and field_key not in ["status"]:
                        continue

                    if field_key in col_map:
                        col_idx = col_map[field_key]
                        cells_to_update.append(gspread.Cell(row_idx, col_idx, val_str))
                    else:
                        # ПЕРЕВІРКА ЧЕРЕЗ ЗОВНІШНЄ API (Замість сліпого блокування)
                        from core.external_api import fetch_character_from_api

                        print(f"🔍 [API CHECK] Персонажа '{target_name}' немає локально. Шукаємо в API Ice & Fire...")
                        api_npc = fetch_character_from_api(target_name)

                        if api_npc:
                            print(f"🌐 [API SUCCESS] Знайдено канонічного персонажа: {api_npc['Name']}! Додаємо в базу.")

                            # Збираємо дані у масив для запису в кінець таблиці.
                            # УВАГА: Переконайся, що порядок цих змінних відповідає порядку колонок у твоїй Google Таблиці (NPC)
                            new_row = [
                                api_npc["Status"],
                                api_npc["Name"],
                                api_npc["Location"],
                                api_npc["Description"],
                                api_npc["Character"],
                                api_npc["Goal"],
                                api_npc["Relation_Player"],
                                api_npc["Memory_Anchor"],
                                api_npc["Relation_NPCs"],
                                api_npc["Secrets"],
                                api_npc["Is_Canon"],
                                api_npc["Inventory"]
                            ]

                            try:
                                worksheet.append_row(new_row)
                                print(f"💾 [SAVED] {api_npc['Name']} успішно завантажений у Вестерос.")
                            except Exception as append_err:
                                print(
                                    f"❌ [API SAVE ERROR] Не вдалося записати {api_npc['Name']} у таблицю: {append_err}")
                        else:
                            # Якщо немає ні в нас, ні в API - це 100% галюцинація
                            print(f"🛡️ [БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ] ШІ придумав NPC '{target_name}'. Ігноруємо!")

        if cells_to_update:
            worksheet.update_cells(cells_to_update)
            print(f"💾 [SAVED] Оновлено {len(cells_to_update)} полів існуючих NPC.")
            refresh_npc_database()

    except Exception as e:
        print(f"❌ [UPDATE ERROR] {e}")