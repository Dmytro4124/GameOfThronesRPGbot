# database/operations.py
import json
import difflib
import re
import asyncio
import numpy as np
import gspread
from google import genai

from database.sheets import db
from config import (
    TAB_USERS,
    TAB_HOUSES,
    TAB_CHARACTER,
    TAB_KNOWLEDGE,
    TAB_NPC,
    GEMINI_API_KEY
)

# ================= ГЛОБАЛЬНІ КЕШІ =================
LORE_CACHE = []
NPC_CACHE = {}
LORE_VECTORS = None

# Ініціалізація клієнта Gemini
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ================= РОБОТА З БАЗОЮ ГРАВЦІВ (Users_DB) =================

async def get_user_data(user_id):
    """Асинхронно знаходить дані гравця за Telegram ID"""

    def _sync_fetch():
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

    return await asyncio.to_thread(_sync_fetch)


async def save_user_data(user_id, profile_data, char_name="Unknown"):
    """Асинхронно зберігає або оновлює дані гравця"""

    def _sync_save():
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

    return await asyncio.to_thread(_sync_save)


async def reset_and_fill_character_sheet(data_dict):
    """Асинхронне ПОВНЕ ПЕРЕЗАПИСУВАННЯ таблиці на старті гри"""

    def _sync_reset():
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

    return await asyncio.to_thread(_sync_reset)


# ================= РОБОТА З ДОМАМИ ТА РЕГІОНАМИ =================

async def get_unique_regions():
    """Асинхронно отримує список регіонів"""

    def _sync_get():
        try:
            sheet = db.get_sheet(TAB_HOUSES)
            records = sheet.get_all_records()
            regions = set(row['Регіон'] for row in records if row.get('Регіон'))
            return sorted(list(regions))
        except Exception as e:
            print(f"❌ Помилка читання регіонів: {e}")
            return []

    return await asyncio.to_thread(_sync_get)


async def get_houses_by_region(region):
    """Асинхронно отримує список домів у регіоні"""

    def _sync_get():
        try:
            sheet = db.get_sheet(TAB_HOUSES)
            records = sheet.get_all_records()
            return sorted([row['Рід'] for row in records if row.get('Регіон') == region])
        except:
            return []

    return await asyncio.to_thread(_sync_get)


async def get_house_stats_data(house_name):
    """Асинхронно отримує дані про Дім"""

    def _sync_get():
        try:
            sheet = db.get_sheet(TAB_HOUSES)
            records = sheet.get_all_records()
            for row in records:
                if row.get('Рід') == house_name:
                    return row
            return {}
        except:
            return {}

    return await asyncio.to_thread(_sync_get)


# ================= РОБОТА З ЛОРОМ (RAG / KnowledgeBase) =================

async def get_embedding(text: str):
    """Асинхронно отримує вектор тексту через Gemini"""

    def _sync_embed():
        response = gemini_client.models.embed_content(
            model='gemini-embedding-2-preview',
            contents=text
        )
        return response.embeddings[0].values

    try:
        return await asyncio.to_thread(_sync_embed)
    except Exception as e:
        print(f"⚠️ Помилка отримання вектора від Gemini: {e}")
        return None


async def load_lore_data():
    """Завантажує базу знань і векторизує її без блокування Event Loop"""
    global LORE_CACHE, LORE_VECTORS
    try:
        def _get_sheet_records():
            sheet = db.get_sheet(TAB_KNOWLEDGE)
            return sheet.get_all_records() if sheet else []

        records = await asyncio.to_thread(_get_sheet_records)
        LORE_CACHE = [row for row in records if str(row.get('Інформація', '')).strip()]

        if not LORE_CACHE:
            print("⚠️ База лору порожня.")
            return

        print(f"📚 Завантажено {len(LORE_CACHE)} записів лору. Починаю повільну векторизацію (обхід лімітів)...")

        texts_to_embed = [
            f"{row.get('Ключові слова', '')} {row.get('Інформація', '')}"
            for row in LORE_CACHE
        ]

        all_embeddings = []
        batch_size = 90

        for i in range(0, len(texts_to_embed), batch_size):
            batch = texts_to_embed[i:i + batch_size]

            def _embed_batch():
                return gemini_client.models.embed_content(
                    model='gemini-embedding-001',
                    contents=batch
                )

            response = await asyncio.to_thread(_embed_batch)
            all_embeddings.extend([emb.values for emb in response.embeddings])

            if i + batch_size < len(texts_to_embed):
                print(
                    f"⏳ Векторизовано {i + len(batch)} / {len(texts_to_embed)}. Пауза 60 сек для скидання квоти API...")
                await asyncio.sleep(60)

        LORE_VECTORS = np.array(all_embeddings)
        print(f"✅ [RAG] Вектори успішно створено через Gemini для {len(LORE_CACHE)} записів!")

    except Exception as e:
        print(f"⚠️ Не вдалося ініціалізувати векторну базу KnowledgeBase: {e}")
        LORE_CACHE = []
        LORE_VECTORS = None


async def get_relevant_context(user_text, current_location):
    """Семантичний пошук найрелевантнішого лору (Асинхронний)"""
    if LORE_VECTORS is None or not LORE_CACHE:
        return "Немає особливих відомостей."

    try:
        search_query = f"{current_location}. {user_text}"
        query_vector_list = await get_embedding(search_query)

        if not query_vector_list:
            return "Немає особливих відомостей."

        query_vector = np.array(query_vector_list)
        distances = np.linalg.norm(LORE_VECTORS - query_vector, axis=1)

        top_k = 3
        top_indices = np.argsort(distances)[:top_k]

        found_info = []
        for idx in top_indices:
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

async def refresh_npc_database():
    """Асинхронно завантажує NPC з Google Sheets у пам'ять бота"""
    global NPC_CACHE

    def _sync_refresh():
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
                if row.get("Secrets"):
                    npc_card += f"- **[SECRET/GM ONLY]:** (GUIDE BEHAVIOR, DO NOT REVEAL): {row.get('Secrets')}\n"

                if loc not in new_cache:
                    new_cache[loc] = []
                new_cache[loc].append(npc_card)
                count += 1

            return new_cache, count
        except Exception as e:
            print(f"❌ [NPC DB ERROR] {e}")
            return None, 0

    result, count = await asyncio.to_thread(_sync_refresh)
    if result is not None:
        NPC_CACHE = result
        print(f"✅ [NPC DB] Завантажено {count} персонажів.", flush=True)
        return True
    return False


def get_location_npcs(current_location):
    """Синхронна функція (працює лише з оперативною пам'яттю). Повертає опис NPC та список легальних імен."""
    found_npcs = []

    for loc_key, npcs_list in NPC_CACHE.items():
        if loc_key.lower() in current_location.lower():
            found_npcs.extend(npcs_list)

    if "GLOBAL" in NPC_CACHE:
        found_npcs.extend(NPC_CACHE["GLOBAL"])

    if not found_npcs:
        return "", []

    legal_names = []

    for npc_string in found_npcs:
        match = re.search(r'^[-*\s]*([^:]+):', npc_string)
        if match:
            clean_name = match.group(1).strip()
            legal_names.append(clean_name)
        else:
            words = npc_string.replace("-", "").strip().split()
            clean_name = " ".join(words[:2])
            legal_names.append(clean_name)

    npc_block = "=== 👥 VISIBLE NPC ROSTER (PRIORITY USE!) ===\n"
    npc_block += "GM INSTRUCTION: If player interacts with someone, PICK FROM THIS LIST first.\n"
    npc_block += "DO NOT HALLUCINATE NEW CHARACTERS IF A SUITABLE ONE IS HERE.\n\n"
    npc_block += "\n".join(found_npcs)

    return npc_block, legal_names


# ================= ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ БАЗИ =================

def find_best_match(query_name, distinct_names, threshold=0.75):
    """
    Синхронна функція. Шукає найбільш схоже ім'я зі списку.
    Включає 'Анти-родинний запобіжник', щоб не плутати Дейнеріс та Візеріса Таргарієнів.
    """
    if not query_name or not distinct_names:
        return None

    query = query_name.lower().strip()

    for real_name in distinct_names:
        if query == real_name.lower().strip():
            return real_name

    matches = difflib.get_close_matches(query, distinct_names, n=1, cutoff=threshold)

    if matches:
        best_match = matches[0]
        best_match_low = best_match.lower().strip()

        query_words = query.split()
        match_words = best_match_low.split()

        if query_words and match_words:
            query_first_name = query_words[0]
            match_first_name = match_words[0]

            first_name_ratio = difflib.SequenceMatcher(None, query_first_name, match_first_name).ratio()

            if first_name_ratio < 0.65:
                return None

        return best_match

    return None


async def update_npcs_in_db(updates, legal_names_list_deprecated=None):
    """
    Асинхронно оновлює існуючих NPC.
    Вбудовано жорстку нормалізацію регістру та апострофів для difflib.
    """
    if not updates:
        return

    print(f"📝 [NPC UPDATE] Обробка змін: {json.dumps(updates, ensure_ascii=False)}")

    def _sync_update():
        worksheet = db.get_sheet(TAB_NPC)
        all_values = worksheet.get_all_values()

        if not all_values: return False

        headers = [h.strip().lower() for h in all_values[0]]
        col_map = {}
        target_cols = ["status", "relation_player", "memory_anchor", "goal", "secrets", "description", "character",
                       "relation_npcs", "inventory"]

        for target in target_cols:
            if target in headers:
                col_map[target] = headers.index(target) + 1

        db_names_map = {}
        legal_names_lower_map = {}

        for i, row in enumerate(all_values[1:], start=2):
            raw_name = str(row[1]).strip()
            if raw_name:
                db_names_map[raw_name] = i
                norm_name = raw_name.lower().replace("’", "'").replace("`", "'").replace("‘", "'")
                legal_names_lower_map[norm_name] = raw_name

        cells_to_update = []
        global_legal_norm_names = list(legal_names_lower_map.keys())

        for update in updates:
            target_name = str(update.get("Name")).strip()
            if not target_name:
                continue

            norm_target = target_name.lower().replace("’", "'").replace("`", "'").replace("‘", "'")
            matches = difflib.get_close_matches(norm_target, global_legal_norm_names, n=1, cutoff=0.65)

            if matches:
                best_norm_match = matches[0]
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

                    if not val_str or val_str.lower() in ["", "-", "none", "null", "same", "без змін"]:
                        continue

                    if len(val_str) < 3 and field_key not in ["status"]:
                        continue

                    if field_key in col_map:
                        col_idx = col_map[field_key]
                        cells_to_update.append(gspread.Cell(row_idx, col_idx, val_str))
            else:
                from core.external_api import fetch_character_from_api

                print(f"🔍 [API CHECK] Персонажа '{target_name}' немає локально. Шукаємо в API Ice & Fire...")
                api_npc = fetch_character_from_api(target_name)

                if api_npc:
                    print(f"🌐 [API SUCCESS] Знайдено канонічного персонажа: {api_npc['Name']}! Додаємо в базу.")

                    new_row = [
                        api_npc.get("Status", "Active"),
                        api_npc.get("Name", "Unknown"),
                        api_npc.get("Location", "GLOBAL"),
                        api_npc.get("Description", ""),
                        api_npc.get("Character", ""),
                        api_npc.get("Goal", ""),
                        api_npc.get("Relation_Player", ""),
                        api_npc.get("Memory_Anchor", ""),
                        api_npc.get("Relation_NPCs", ""),
                        api_npc.get("Secrets", ""),
                        api_npc.get("Is_Canon", "TRUE"),
                        api_npc.get("Inventory", "")
                    ]

                    try:
                        worksheet.append_row(new_row)
                        print(f"💾 [SAVED] {api_npc['Name']} успішно завантажений у Вестерос.")
                    except Exception as append_err:
                        print(f"❌ [API SAVE ERROR] Не вдалося записати {api_npc['Name']} у таблицю: {append_err}")
                else:
                    print(f"🛡️ [БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ] ШІ придумав NPC '{target_name}'. Ігноруємо!")

        if cells_to_update:
            worksheet.update_cells(cells_to_update)
            print(f"💾 [SAVED] Оновлено {len(cells_to_update)} полів існуючих NPC.")
            return True
        return False

    try:
        has_updated = await asyncio.to_thread(_sync_update)
        if has_updated:
            await refresh_npc_database()
    except Exception as e:
        print(f"❌ [UPDATE ERROR] {e}")