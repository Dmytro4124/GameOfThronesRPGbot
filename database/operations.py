import json
import gspread
import difflib

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


# ================= РОБОТА З ЛОРОМ (KnowledgeBase) =================

def load_lore_data():
    """Завантажує базу знань у пам'ять бота при старті"""
    global LORE_CACHE
    try:
        sheet = db.get_sheet(TAB_KNOWLEDGE)
        if not sheet: return

        records = sheet.get_all_records()
        LORE_CACHE = records
        print(f"📚 Завантажено {len(records)} записів лору.")
    except Exception as e:
        print(f"⚠️ Не вдалося завантажити KnowledgeBase: {e}")
        LORE_CACHE = []


def get_relevant_context(user_text, current_location):
    """Шукає в базі інформацію, яка відповідає словам гравця або локації."""
    found_info = []
    search_text = (user_text + " " + current_location).lower()

    for row in LORE_CACHE:
        keywords = str(row.get('Ключові слова', '')).lower().split(',')
        content = row.get('Інформація', '')

        for key in keywords:
            key = key.strip()
            if key and key in search_text:
                found_info.append(f"- {content}")
                break

    if found_info:
        return "\n".join(found_info[:5])
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
            if row.get("Relation_NPCs"): npc_card += f"- **Attitude to other NPC:** {row.get('Relation_NPCs')}\n"
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
    """Повертає текст з описом NPC для поточної локації."""
    found_npcs = []

    for loc_key, npcs_list in NPC_CACHE.items():
        if loc_key.lower() in current_location.lower():
            found_npcs.extend(npcs_list)

    if "GLOBAL" in NPC_CACHE:
        found_npcs.extend(NPC_CACHE["GLOBAL"])

    if not found_npcs:
        return ""

    npc_block = "=== 👥 VISIBLE NPC ROSTER (PRIORITY USE!) ===\n"
    npc_block += "GM INSTRUCTION: If player interacts with someone, PICK FROM THIS LIST first.\n"
    npc_block += "DO NOT HALLUCINATE NEW CHARACTERS IF A SUITABLE ONE IS HERE.\n\n"
    npc_block += "\n".join(found_npcs)

    return npc_block


# ================= ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ БАЗИ =================

def find_best_match(query_name, distinct_names, threshold=0.65):
    """Шукає найбільш схоже ім'я зі списку (використовується для оновлення NPC)."""
    if not query_name or not distinct_names:
        return None

    query = query_name.lower().strip()

    for real_name in distinct_names:
        r_low = real_name.lower()
        if query == r_low or query in r_low or r_low in query:
            return real_name

    matches = difflib.get_close_matches(query, distinct_names, n=1, cutoff=threshold)
    if matches:
        return matches[0]

    return None


def update_npcs_in_db(updates):
    """Приймає список змін для NPC і записує їх у базу."""
    if not updates: return

    print(f"📝 [NPC UPDATE] Обробка змін: {json.dumps(updates, ensure_ascii=False)}")

    try:
        worksheet = db.get_sheet(TAB_NPC)
        all_values = worksheet.get_all_values()

        if not all_values: return

        headers = [h.strip().lower() for h in all_values[0]]
        col_map = {}
        target_cols = ["status", "relation_player", "goal", "secrets", "description", "character"]

        for target in target_cols:
            if target in headers:
                col_map[target] = headers.index(target) + 1

        db_names_map = {}
        for i, row in enumerate(all_values[1:], start=2):
            raw_name = str(row[1]).strip()
            if raw_name:
                db_names_map[raw_name] = i

        cells_to_update = []
        available_names_list = list(db_names_map.keys())

        for update in updates:
            target_name = str(update.get("Name")).strip()
            best_match = find_best_match(target_name, available_names_list)

            if best_match:
                row_idx = db_names_map[best_match]
                print(f"✅ [MATCH] '{target_name}' ототожнено з '{best_match}' (Рядок {row_idx})")

                for field, new_val in update.items():
                    field_key = field.lower()
                    if field_key == "name": continue

                    if field_key in col_map:
                        col_idx = col_map[field_key]
                        cells_to_update.append(gspread.Cell(row_idx, col_idx, str(new_val)))
            else:
                print(f"⚠️ [NO MATCH] Не вдалося знайти NPC: '{target_name}' в базі.")

        if cells_to_update:
            worksheet.update_cells(cells_to_update)
            print(f"💾 [SAVED] Оновлено {len(cells_to_update)} полів.")
            refresh_npc_database()  # Оновлюємо кеш у пам'яті
        else:
            print("⚠️ [NPC UPDATE] Немає співпадінь для запису.")

    except Exception as e:
        print(f"❌ [UPDATE ERROR] {e}")