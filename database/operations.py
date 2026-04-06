# database/operations.py
import json
import difflib
import re
import asyncio
import os
import hashlib
import numpy as np
import gspread
from google import genai

from database.sheets import db
from core.world_constants import is_valid_location, is_valid_region, TRAVEL_LOCATION
from config import (
    TAB_USERS,
    TAB_HOUSES,
    TAB_CHARACTER,
    TAB_KNOWLEDGE,
    TAB_NPC,
    GEMINI_API_KEY
)

# ================= ГЛОБАЛЬНІ КЕШІ ТА КОНСТАНТИ =================
LORE_CACHE = []
NPC_CACHE = {}
DEAD_NPC_NAMES: set = set()  # Імена NPC зі Status=Dead; оновлюється разом з NPC_CACHE
LORE_VECTORS = None

EMBEDDINGS_FILE = "lore_embeddings.npy"
LORE_HASH_FILE = "lore_hash.txt"
EMBEDDING_MODEL = "gemini-embedding-2-preview"  # ЄДИНА МОДЕЛЬ ДЛЯ ВСІХ ВЕКТОРІВ

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
                cell = cells[0]
                sheet.update_cell(cell.row, 3, json_str)
                sheet.update_cell(cell.row, 2, char_name)
            else:
                sheet.append_row([str(user_id), char_name, json_str])
            return True
        except Exception as e:
            print(f"❌ Помилка збереження: {e}")
            return False

    return await asyncio.to_thread(_sync_save)


async def delete_user_data(user_id):
    """Видаляє рядок гравця з Users_DB за Telegram ID. Для чистого рестарту гри."""

    def _sync_delete():
        try:
            sheet = db.get_sheet(TAB_USERS)
            if not sheet:
                return False
            cells = sheet.findall(str(user_id), in_column=1)
            for cell in reversed(cells):  # reversed щоб не зсунути індекси при видаленні
                sheet.delete_rows(cell.row)
            return len(cells) > 0
        except Exception as e:
            print(f"❌ Помилка видалення профілю {user_id}: {e}")
            return False

    return await asyncio.to_thread(_sync_delete)


def clear_npc_cache():
    """Очищує глобальний NPC-кеш. Викликати при рестарті гри."""
    global NPC_CACHE
    NPC_CACHE = {}
    print("🗑️ [NPC CACHE] Кеш очищено.")


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
            model=EMBEDDING_MODEL,
            contents=text
        )
        return response.embeddings[0].values

    try:
        return await asyncio.to_thread(_sync_embed)
    except Exception as e:
        print(f"⚠️ Помилка отримання вектора від Gemini: {e}")
        return None


async def load_lore_data():
    """Завантажує базу знань з розумним локальним кешуванням (MD5 Hash)"""
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

        # 1. Генеруємо унікальний хеш поточного стану таблиці лору
        current_lore_str = json.dumps(LORE_CACHE, sort_keys=True)
        current_hash = hashlib.md5(current_lore_str.encode('utf-8')).hexdigest()

        # 2. Перевіряємо, чи є валідний локальний кеш
        def _sync_load_cache():
            if not (os.path.exists(EMBEDDINGS_FILE) and os.path.exists(LORE_HASH_FILE)):
                return None, None
            with open(LORE_HASH_FILE, 'r') as f:
                saved_hash = f.read().strip()
            if saved_hash == current_hash:
                return saved_hash, np.load(EMBEDDINGS_FILE)
            return saved_hash, None

        saved_hash, cached_vectors = await asyncio.to_thread(_sync_load_cache)
        if saved_hash == current_hash and cached_vectors is not None:
            print(f"⚡ Локальний кеш актуальний. Завантажую {len(LORE_CACHE)} векторів з {EMBEDDINGS_FILE}...")
            LORE_VECTORS = cached_vectors
            return

        print(f"📚 Локальний кеш відсутній або застарів. Починаю повну векторизацію (Модель: {EMBEDDING_MODEL})...")

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
                    model=EMBEDDING_MODEL,
                    contents=batch
                )

            response = await asyncio.to_thread(_embed_batch)
            all_embeddings.extend([emb.values for emb in response.embeddings])

            if i + batch_size < len(texts_to_embed):
                print(
                    f"⏳ Векторизовано {i + len(batch)} / {len(texts_to_embed)}. Пауза 60 сек для скидання квоти API...")
                await asyncio.sleep(60)

        LORE_VECTORS = np.array(all_embeddings)

        # 3. Зберігаємо нові вектори та новий хеш на диск
        def _sync_save_cache():
            np.save(EMBEDDINGS_FILE, LORE_VECTORS)
            with open(LORE_HASH_FILE, 'w') as f:
                f.write(current_hash)

        await asyncio.to_thread(_sync_save_cache)

        print(f"✅ [RAG] Вектори успішно створено та закешовано локально!")

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
    """Асинхронно завантажує NPC з Google Sheets у пам'ять бота (З урахуванням Сцени)"""
    global NPC_CACHE, DEAD_NPC_NAMES

    def _sync_refresh():
        try:
            worksheet = db.get_sheet(TAB_NPC)
            if not worksheet: return False

            raw_data = worksheet.get_all_records()
            new_cache = {}
            dead_names = set()
            count = 0

            for row in raw_data:
                status = str(row.get("Status", "Active")).strip()
                if status.lower() != "active":
                    if status.lower() == "dead":
                        dead_name = str(row.get("Name", "")).strip()
                        if dead_name:
                            dead_names.add(dead_name)
                    continue

                loc = str(row.get("Location", "GLOBAL")).strip()
                scene = str(row.get("Scene", "Невідомо")).strip()  # ДОДАНО СЦЕНУ
                name = str(row.get("Name", "Unknown")).strip()
                is_canon = str(row.get("Is_Canon", "FALSE")).upper() == "TRUE"

                type_tag = "[CANON/BOSS]" if is_canon else "[LOCAL/BACKGROUND]"

                rep_score = 0
                raw_rep = row.get("Reputation_Score", "0")
                try:
                    rep_score = int(str(raw_rep).strip()) if str(raw_rep).strip() else 0
                except (ValueError, TypeError):
                    rep_score = 0

                region = str(row.get("Region", "")).strip()

                npc_card = f"> **{name}** {type_tag}\n"
                if row.get("Description"): npc_card += f"- **Visual:** {row.get('Description')}\n"
                if row.get("Character"): npc_card += f"- **Personality:** {row.get('Character')}\n"
                if row.get("Goal"): npc_card += f"- **Goal:** {row.get('Goal')}\n"
                if row.get("Relation_Player"): npc_card += f"- **Attitude to Player:** {row.get('Relation_Player')}\n"
                raw_anchor = str(row.get("Memory_Anchor", "")).strip()
                if raw_anchor and raw_anchor != "-":
                    try:
                        anchor_list = json.loads(raw_anchor)
                        if isinstance(anchor_list, list) and anchor_list:
                            anchor_text = " | ".join(
                                e.get("event", str(e)) for e in anchor_list if isinstance(e, dict)
                            ) or raw_anchor
                        else:
                            anchor_text = raw_anchor
                    except (json.JSONDecodeError, TypeError):
                        anchor_text = raw_anchor
                    npc_card += f"- **Memory Anchor:** {anchor_text}\n"
                if row.get("Relation_NPCs"): npc_card += f"- **Attitude to other NPC:** {row.get('Relation_NPCs')}\n"
                if row.get("Inventory") and str(row.get("Inventory")).strip() not in ["", "-"]:
                    npc_card += f"- **Inventory (Items & Gold):** {row.get('Inventory')}\n"
                if row.get("Secrets"):
                    npc_card += f"- **[SECRET/GM ONLY]:** {row.get('Secrets')}\n"

                if loc not in new_cache:
                    new_cache[loc] = []

                # Зберігаємо структуру, щоб мати змогу фільтрувати за сценою пізніше
                new_cache[loc].append({
                    "name": name,
                    "scene": scene,
                    "card": npc_card,
                    "reputation_score": rep_score,
                    "region": region,
                })
                count += 1

            return new_cache, dead_names, count
        except Exception as e:
            print(f"❌ [NPC DB ERROR] {e}")
            return None, set(), 0

    result, dead_result, count = await asyncio.to_thread(_sync_refresh)
    if result is not None:
        NPC_CACHE = result
        DEAD_NPC_NAMES = dead_result
        print(f"✅ [NPC DB] Завантажено {count} активних, {len(dead_result)} мертвих персонажів.", flush=True)
        return True
    return False


def get_dead_npc_names() -> set:
    """Повертає копію множини імен мертвих NPC (Status=Dead)."""
    return set(DEAD_NPC_NAMES)


def evict_npc_from_cache(matched_name: str):
    """Миттєво видаляє NPC з NPC_CACHE без звернення до Google Sheets."""
    global NPC_CACHE
    for loc in list(NPC_CACHE.keys()):
        NPC_CACHE[loc] = [n for n in NPC_CACHE[loc] if n.get("name") != matched_name]


def get_location_npcs(current_location, current_scene, current_region=None):
    """Синхронна функція. Повертає опис NPC, список легальних імен та dict репутації.
    Підтримує 3-рівневу фільтрацію: Регіон → Локація → Сцена.
    Якщо current_location == TRAVEL_LOCATION — показує регіональних мандрівників.
    """
    found_npcs = []
    legal_names = []
    reputation_context = {}

    is_traveling = str(current_location).strip() == TRAVEL_LOCATION
    target_loc = str(current_location).strip().lower()
    target_scene = str(current_scene).strip().lower()
    if not target_scene:
        target_scene = "невідомо"

    for loc_key, npcs_list in NPC_CACHE.items():
        if loc_key == "GLOBAL":
            continue  # обробляємо окремо нижче

        if is_traveling:
            # Travel-режим: показуємо NPC чий регіон збігається і чия сцена = global/empty
            if current_region:
                for npc_data in npcs_list:
                    npc_region = npc_data.get("region", "").strip()
                    npc_scene = str(npc_data.get("scene", "")).strip().lower()
                    if npc_region == current_region and npc_scene in ("global", "", "невідомо"):
                        found_npcs.append(npc_data["card"])
                        legal_names.append(npc_data["name"])
                        reputation_context[npc_data["name"]] = npc_data.get("reputation_score", 0)
        else:
            # Нормальний режим: точний збіг локації + сцени з підтримкою приватних сцен
            if loc_key.lower() == target_loc:
                _GENERIC_SCENES = ("невідомо", "global", "")
                player_in_specific_scene = target_scene not in _GENERIC_SCENES
                for npc_data in npcs_list:
                    npc_scene = str(npc_data.get("scene", "невідомо")).strip().lower()
                    if player_in_specific_scene:
                        # Іменована/приватна сцена: ТІЛЬКИ NPC явно прив'язані до цієї сцени
                        show = (npc_scene == target_scene)
                    else:
                        # Загальна сцена: показуємо NPC без прив'язки АБО з такою ж сценою
                        show = (npc_scene == target_scene or npc_scene in _GENERIC_SCENES)
                    if show:
                        found_npcs.append(npc_data["card"])
                        legal_names.append(npc_data["name"])
                        reputation_context[npc_data["name"]] = npc_data.get("reputation_score", 0)

    # Завжди додаємо абсолютно глобальних NPC
    if "GLOBAL" in NPC_CACHE:
        for npc_data in NPC_CACHE["GLOBAL"]:
            found_npcs.append(npc_data["card"])
            legal_names.append(npc_data["name"])
            reputation_context[npc_data["name"]] = npc_data.get("reputation_score", 0)

    if not found_npcs:
        return "", [], {}

    npc_block = "=== 👥 VISIBLE NPC ROSTER (STRICTLY IN THIS SCENE) ===\n"
    npc_block += "GM INSTRUCTION: Only these characters are physically present here.\n"
    npc_block += "DO NOT HALLUCINATE NEW CHARACTERS IF A SUITABLE ONE IS HERE.\n\n"
    npc_block += "\n".join(found_npcs)

    return npc_block, legal_names, reputation_context


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


async def update_npcs_in_db(updates, legal_names_list_deprecated=None,
                            player_new_location: str = None, player_location_changed: bool = False,
                            player_new_scene: str = None, player_scene_changed: bool = False,
                            companion_npcs: list = None):
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
        target_cols = ["status", "location", "scene", "relation_player", "memory_anchor", "goal", "secrets",
                       "description", "character",
                       "relation_npcs", "inventory", "reputation_score", "region"]

        for target in target_cols:
            if target in headers:
                col_map[target] = headers.index(target) + 1

        db_names_map = {}
        legal_names_lower_map = {}
        name_col_idx = headers.index("name") if "name" in headers else 2

        for i, row in enumerate(all_values[1:], start=2):
            if len(row) <= name_col_idx:
                continue
            raw_name = str(row[name_col_idx]).strip()
            if raw_name:
                db_names_map[raw_name] = i
                norm_name = raw_name.lower().replace("’", "’").replace("`", "’").replace("’", "’")
                legal_names_lower_map[norm_name] = raw_name

        cells_to_update = []
        memory_anchor_updates = []  # [(real_name, event_text)] — обробляється окремо через append
        global_legal_norm_names = list(legal_names_lower_map.keys())

        for update in updates:
            target_name = str(update.get("Name")).strip()
            if not target_name:
                continue

            norm_target = target_name.lower().replace("’", "’").replace("`", "’").replace("’", "’")
            matches = difflib.get_close_matches(norm_target, global_legal_norm_names, n=1, cutoff=0.65)

            if matches:
                best_norm_match = matches[0]
                real_original_name = legal_names_lower_map[best_norm_match]
                row_idx = db_names_map[real_original_name]

                if target_name != real_original_name:
                    print(f"🔧 [АВТОКОРЕКЦІЯ] ‘{target_name}’ виправлено на ‘{real_original_name}’")
                else:
                    print(f"✅ [MATCH] ‘{real_original_name}’ знайдено.")

                # Смерть незворотна: якщо NPC вже Dead у DB — блокуємо будь-які оновлення
                if "status" in col_map:
                    status_col_idx = col_map["status"] - 1
                    row_data = all_values[row_idx - 1]
                    current_db_status = row_data[status_col_idx].strip().lower() if len(row_data) > status_col_idx else ""
                    if current_db_status == "dead":
                        print(f"🚫 [СМЕРТЬ НЕЗВОРОТНА] ‘{real_original_name}’ вже мертвий — оновлення заблоковано.")
                        continue

                for field, new_val in update.items():
                    field_key = field.lower()
                    if field_key == "name": continue

                    val_str = str(new_val).strip()

                    if not val_str or val_str.lower() in ["", "-", "none", "null", "same", "без змін"]:
                        continue

                    if len(val_str) < 3 and field_key not in ["status"]:
                        continue

                    if field_key == "location":
                        if not is_valid_location(val_str) or val_str == TRAVEL_LOCATION:
                            print(f"🚫 [NPC ЛОКАЦІЯ ЗАБЛОКОВАНА] ШІ намагався записати неканонічне значення "
                                  f"’{val_str}’ у Location для NPC ‘{real_original_name}’. Пропускаємо.")
                            continue
                        if player_location_changed and player_new_location and val_str.strip() == player_new_location.strip():
                            if real_original_name not in (companion_npcs or []):
                                print(f"🚫 [TELEPORT BLOCKED] ШІ намагався перемістити ‘{real_original_name}’ "
                                      f"у ‘{val_str}’ = нова локація гравця. Пропускаємо телепортацію.")
                                continue
                            else:
                                print(f"✅ [COMPANION TRAVEL] ‘{real_original_name}’ подорожує з гравцем → ‘{val_str}’")

                    if field_key == "region" and not is_valid_region(val_str):
                        print(f"🚫 [NPC РЕГІОН ЗАБЛОКОВАНИЙ] Неканонічне значення ‘{val_str}’ "
                              f"для NPC ‘{real_original_name}’. Пропускаємо.")
                        continue

                    if field_key == "scene":
                        words = val_str.split()
                        if len(words) > 3:
                            val_str = " ".join(words[:3])
                            print(f"✂️ [СЦЕНА ОБРІЗАНА] ‘{new_val}’ → ‘{val_str}’ для ‘{real_original_name}’")
                        if val_str.strip().lower() in ("невідомо", "global", "unknown", ""):
                            print(f"🚫 [СЦЕНА ЗАБЛОКОВАНА] Сміттєве значення ‘{val_str}’ для ‘{real_original_name}’.")
                            continue
                        # Валідація через LOCATION_SCENES — autocorrect через difflib
                        if "location" in col_map:
                            _loc_col_idx = col_map["location"] - 1
                            _row_data = all_values[row_idx - 1]
                            _npc_location = _row_data[_loc_col_idx].strip() if len(_row_data) > _loc_col_idx else ""
                            if _npc_location:
                                from core.world_constants import is_valid_scene, get_scenes_for_location
                                if not is_valid_scene(_npc_location, val_str):
                                    _valid = get_scenes_for_location(_npc_location)
                                    _close = difflib.get_close_matches(val_str, _valid, n=1, cutoff=0.4)
                                    if _close:
                                        print(f"🔧 [СЦЕНА АВТОКОРЕКЦІЯ] ‘{val_str}’ → ‘{_close[0]}’ для ‘{real_original_name}’")
                                        val_str = _close[0]
                                    else:
                                        print(f"🚫 [СЦЕНА НЕВАЛІДНА] ‘{val_str}’ не в списку для ‘{_npc_location}’. Пропускаємо.")
                                        continue

                    # Scene Drag Guard: блокуємо перетягування NPC до нової сцени гравця
                    if field_key == "scene" and player_scene_changed and player_new_scene:
                        if val_str.strip().lower() == player_new_scene.strip().lower():
                            if real_original_name in (companion_npcs or []):
                                print(f"✅ [COMPANION SCENE] '{real_original_name}' переходить з гравцем → '{val_str}'")
                            elif "scene" in col_map:
                                _scene_col_idx = col_map["scene"] - 1
                                _cur_npc_scene = row_data[_scene_col_idx].strip() if len(row_data) > _scene_col_idx else ""
                                if _cur_npc_scene.lower() != player_new_scene.strip().lower():
                                    print(f"🚫 [SCENE DRAG BLOCKED] '{real_original_name}' намагається перейти до "
                                          f"'{val_str}' = нова сцена гравця. Поточна сцена NPC: '{_cur_npc_scene}'. Блокуємо.")
                                    continue

                    # Memory_Anchor НЕ перезаписуємо напряму — збираємо для append_memory_anchor,
                    # щоб зберегти JSON-масив з 5 останніх подій (FIFO).
                    if field_key == "memory_anchor":
                        memory_anchor_updates.append((real_original_name, val_str))
                        continue

                    if field_key in col_map:
                        col_idx = col_map[field_key]
                        cells_to_update.append(gspread.Cell(row_idx, col_idx, val_str))
            else:
                print(f"🛡️ [БЛОКУВАННЯ ГАЛЮЦИНАЦІЇ] ШІ придумав NPC ‘{target_name}’. Ігноруємо!")

        if cells_to_update:
            worksheet.update_cells(cells_to_update)
            print(f"💾 [SAVED] Оновлено {len(cells_to_update)} полів існуючих NPC.")
            return True, memory_anchor_updates
        return bool(memory_anchor_updates), memory_anchor_updates

    try:
        has_updated, anchor_updates = await asyncio.to_thread(_sync_update)
        if has_updated:
            await refresh_npc_database()
        for npc_name, event_text in anchor_updates:
            await append_memory_anchor(npc_name, event_text, game_day=0, rep_change=0)
    except Exception as e:
        print(f"❌ [UPDATE ERROR] {e}")


async def update_npc_reputation(npc_name, delta):
    """Атомарне оновлення Reputation_Score NPC у Google Sheets. Clamp до [-100, 100]."""
    if not npc_name or not delta:
        return

    def _sync_rep_update():
        worksheet = db.get_sheet(TAB_NPC)
        all_values = worksheet.get_all_values()
        if not all_values:
            return False

        headers = [h.strip().lower() for h in all_values[0]]
        if "reputation_score" not in headers:
            print(f"⚠️ [REP] Колонка 'Reputation_Score' відсутня в NPC_DB. Пропускаємо.")
            return False

        rep_col = headers.index("reputation_score") + 1
        name_col = headers.index("name") + 1 if "name" in headers else 3

        for i, row in enumerate(all_values[1:], start=2):
            raw_name = str(row[name_col - 1]).strip()
            norm_db = raw_name.lower().replace("'", "'").replace("`", "'").replace("\u2019", "'")
            norm_target = npc_name.lower().replace("'", "'").replace("`", "'").replace("\u2019", "'")

            if norm_db == norm_target or (difflib.SequenceMatcher(None, norm_db, norm_target).ratio() > 0.75):
                old_val = 0
                try:
                    old_val = int(str(row[rep_col - 1]).strip()) if str(row[rep_col - 1]).strip() else 0
                except (ValueError, TypeError):
                    old_val = 0
                new_val = max(-100, min(100, old_val + delta))
                worksheet.update_cell(i, rep_col, new_val)
                print(f"📊 [REP] {raw_name}: {old_val} -> {new_val} (delta {delta:+d})")
                # Автоматично синхронізуємо текстовий ярлик із новим Score
                from database.canon_npc import _score_to_relation_text
                if "relation_player" in headers:
                    rel_col = headers.index("relation_player") + 1
                    new_relation = _score_to_relation_text(new_val)
                    worksheet.update_cell(i, rel_col, new_relation)
                    print(f"🔄 [SYNC] {raw_name}: Relation_Player → '{new_relation}'")
                return True

        print(f"⚠️ [REP] NPC '{npc_name}' не знайдено в NPC_DB.")
        return False

    try:
        updated = await asyncio.to_thread(_sync_rep_update)
        if updated:
            await refresh_npc_database()
    except Exception as e:
        print(f"❌ [REP UPDATE ERROR] {e}")


async def append_memory_anchor(npc_name, event_text, game_day=0, rep_change=0):
    """Додає подію до Memory_Anchor як JSON-масив (FIFO 5 записів)."""
    if not npc_name or not event_text:
        return

    def _sync_memory_update():
        worksheet = db.get_sheet(TAB_NPC)
        all_values = worksheet.get_all_values()
        if not all_values:
            return False

        headers = [h.strip().lower() for h in all_values[0]]
        if "memory_anchor" not in headers:
            return False

        mem_col = headers.index("memory_anchor") + 1
        name_col = headers.index("name") + 1 if "name" in headers else 3

        for i, row in enumerate(all_values[1:], start=2):
            raw_name = str(row[name_col - 1]).strip()
            norm_db = raw_name.lower().replace("'", "'").replace("`", "'").replace("\u2019", "'")
            norm_target = npc_name.lower().replace("'", "'").replace("`", "'").replace("\u2019", "'")

            if norm_db == norm_target or (difflib.SequenceMatcher(None, norm_db, norm_target).ratio() > 0.75):
                old_val = str(row[mem_col - 1]).strip()
                # Парсимо існуючий масив або конвертуємо legacy рядок
                memory_list = []
                if old_val and old_val != "-":
                    try:
                        memory_list = json.loads(old_val)
                        if not isinstance(memory_list, list):
                            memory_list = [{"event": old_val, "day": 0, "rep_change": 0}]
                    except (json.JSONDecodeError, TypeError):
                        memory_list = [{"event": old_val, "day": 0, "rep_change": 0}]

                # Додаємо нову подію
                memory_list.append({"event": event_text, "day": game_day, "rep_change": rep_change})
                # FIFO — тримаємо тільки останні 5
                memory_list = memory_list[-5:]

                worksheet.update_cell(i, mem_col, json.dumps(memory_list, ensure_ascii=False))
                print(f"📝 [MEMORY] {raw_name}: додано '{event_text}' (всього {len(memory_list)} записів)")
                return True

        return False

    try:
        await asyncio.to_thread(_sync_memory_update)
    except Exception as e:
        print(f"❌ [MEMORY UPDATE ERROR] {e}")