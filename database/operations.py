# database/operations.py
import copy as _copy
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
import logging
from core.world_constants import (
    is_valid_location, is_valid_region, TRAVEL_LOCATION,
    NPC_LOCATION_BYPASS, get_region_for_location,
)
from core.reputation import apply_reputation_step
from config import (
    TAB_USERS,
    TAB_HOUSES,
    TAB_CHARACTER,
    TAB_KNOWLEDGE,
    GEMINI_API_KEY
)

# ================= ГЛОБАЛЬНІ КЕШІ ТА КОНСТАНТИ =================
LORE_CACHE = []
LORE_VECTORS = None

EMBEDDINGS_FILE = "lore_embeddings.npy"
LORE_HASH_FILE = "lore_hash.txt"
EMBEDDING_MODEL = "gemini-embedding-2-preview"  # ЄДИНА МОДЕЛЬ ДЛЯ ВСІХ ВЕКТОРІВ

# Ініціалізація клієнта Gemini
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Lock для запобігання race condition при паралельних записах у Users_DB.
# Гарантує, що findall→append_row та findall→update_cell виконуються атомарно:
# два паралельні /start не можуть обидва побачити "рядок не знайдено" і обидва
# зробити append_row, що призводило б до дублювання або перезапису чужих даних.
_users_db_lock: asyncio.Lock = asyncio.Lock()


def _find_user_row_exact(sheet, user_id) -> int | None:
    """Точний (НЕ regex/substring) пошук рядка user_id у колонці 1.

    КРИТИЧНО: gspread 6.x `Worksheet.findall(query, in_column=N)` обробляє query
    як regex search → "12345" знаходить "12345", "123456", "12345789" тощо.
    Для Telegram user_id (9-10 цифр) це призводить до COLLISION — Player 2
    знаходить рядок Player 1 і перезаписує його.

    Цей helper читає всю колонку через col_values і робить EXACT string match.
    Повертає 1-based row index або None.
    """
    target = str(user_id).strip()
    try:
        col_values = sheet.col_values(1)  # list[str], 1-індекс через enumerate(start=1)
        for idx, val in enumerate(col_values, start=1):
            if str(val).strip() == target:
                return idx
        return None
    except Exception as e:
        print(f"❌ _find_user_row_exact failed for {user_id}: {e}")
        return None


def _find_all_user_rows_exact(sheet, user_id) -> list[int]:
    """Як _find_user_row_exact, але повертає список усіх exact-match рядків
    (для cleanup дублікатів через delete)."""
    target = str(user_id).strip()
    rows: list[int] = []
    try:
        col_values = sheet.col_values(1)
        for idx, val in enumerate(col_values, start=1):
            if str(val).strip() == target:
                rows.append(idx)
        return rows
    except Exception as e:
        print(f"❌ _find_all_user_rows_exact failed for {user_id}: {e}")
        return []


# ================= РОБОТА З БАЗОЮ ГРАВЦІВ (Users_DB) =================

async def get_user_data(user_id):
    """Асинхронно знаходить дані гравця за Telegram ID"""

    def _sync_fetch():
        try:
            sheet = db.get_sheet(TAB_USERS)
            if not sheet: return None, None

            row = _find_user_row_exact(sheet, user_id)
            if row is None:
                return None, None

            raw_data = sheet.cell(row, 3).value

            if raw_data:
                return json.loads(raw_data), row
            return None, None
        except Exception as e:
            print(f"❌ Помилка читання БД: {e}")
            return None, None

    return await asyncio.to_thread(_sync_fetch)


async def save_user_data(user_id, profile_data, char_name="Unknown"):
    """Асинхронно зберігає або оновлює дані гравця.

    Захищено _users_db_lock: операція findall → write є атомарною для asyncio,
    тому два паралельні виклики для різних user_id не можуть перезаписати
    чужий рядок, а два виклики для одного user_id не створять дублікат.
    """
    async with _users_db_lock:
        def _sync_save():
            try:
                sheet = db.get_sheet(TAB_USERS)
                if not sheet: return False

                json_str = json.dumps(profile_data, ensure_ascii=False)
                # Читаємо col_values ОДИН раз — для пошуку і обчислення next_row
                try:
                    col_values = sheet.col_values(1)
                except Exception as _exc:
                    print(f"❌ [Users_DB] Не вдалося прочитати col_values: {_exc}")
                    return False

                target = str(user_id).strip()
                row: int | None = None
                for idx, val in enumerate(col_values, start=1):
                    if str(val).strip() == target:
                        row = idx
                        break

                if row is not None:
                    # Існуючий гравець: оновлюємо обидва поля одним batch-запитом
                    sheet.update(
                        f"B{row}:C{row}",
                        [[char_name, json_str]],
                    )
                    print(f"[Users_DB] Оновлено рядок {row} для user {user_id} (всього рядків: {len(col_values)})")
                else:
                    # Новий гравець: EXPLICIT update у наступний порожній рядок.
                    # Не використовуємо append_row бо gspread auto-detect табличного
                    # діапазону може хибно вирішити що "наступний" це row 2 (поверх Player 1).
                    next_row = len(col_values) + 1
                    sheet.update(
                        f"A{next_row}:C{next_row}",
                        [[str(user_id), char_name, json_str]],
                    )
                    print(f"[Users_DB] Додано рядок {next_row} для user {user_id} (всього рядків було: {len(col_values)})")
                return True
            except Exception as e:
                print(f"❌ Помилка збереження: {e}")
                return False

        return await asyncio.to_thread(_sync_save)


async def delete_user_data(user_id):
    """Видаляє рядок гравця з Users_DB за Telegram ID. Для чистого рестарту гри.

    Захищено _users_db_lock: видалення рядка зсуває індекси всіх рядків нижче,
    тому воно не може відбуватись паралельно з іншим записом у ту саму таблицю.
    """
    async with _users_db_lock:
        def _sync_delete():
            try:
                sheet = db.get_sheet(TAB_USERS)
                if not sheet:
                    return False
                rows = _find_all_user_rows_exact(sheet, user_id)
                for row in reversed(rows):  # reversed щоб не зсунути індекси при видаленні
                    sheet.delete_rows(row)
                    print(f"[Users_DB] Видалено рядок {row} для user {user_id}")
                return len(rows) > 0
            except Exception as e:
                print(f"❌ Помилка видалення профілю {user_id}: {e}")
                return False

        return await asyncio.to_thread(_sync_delete)


def clear_npc_cache(user_id=None):
    """Очищує per-user NPC-кеш у user_sessions. Викликати при рестарті гри.
    Якщо user_id=None — no-op (сесій поза user_id не існує у новій архітектурі).
    """
    from core.engine import user_sessions
    if user_id is None:
        print("[NPC CACHE] clear_npc_cache викликано без user_id — no-op.")
        return
    session = user_sessions.get(user_id)
    if session is not None:
        session["npc_cache"] = {}
        session["dead_npc_names"] = set()
    print(f"[NPC CACHE] Кеш очищено для user {user_id}.")


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


# ================= A/B LOG (Narrator) =================

TAB_AB_LOG = "AB_Log"
AB_LOG_HEADERS = [
    "ts", "type", "turn_id", "user_id", "mode", "vote", "vote_raw", "vote_ms", "reason",
    "shown_order", "gemma_ms", "flash_lite_ms", "gemma_attempt", "flash_lite_attempt",
    "gemma_fallback", "flash_lite_fallback", "gemma_len", "flash_lite_len",
    "outcome", "difficulty", "natural_roll", "note", "gemma_text", "flash_lite_text",
    "record_json",
]
AB_CELL_LIMIT = 45000
_AB_MODELS = ("gemma", "flash_lite")
_ab_log_lock = asyncio.Lock()
_ab_sheet_ready = False
_ab_ws = None  # кешований worksheet (щоб не робити зайвий worksheet() lookup на кожен append)


def _ab_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    return str(v)[:AB_CELL_LIMIT]


def _ab_truncate_text(s, limit: int):
    if not isinstance(s, str) or len(s) <= limit:
        return s
    cut = len(s) - limit
    return s[:max(limit, 0)] + f"…[truncated {cut} chars]"


def _ab_record_json(record: dict) -> str:
    """JSON запису, гарантовано <= AB_CELL_LIMIT символів (обрізає prompt, потім тексти результатів)."""
    full = json.dumps(record, ensure_ascii=False, default=str)
    if len(full) <= AB_CELL_LIMIT:
        return full
    rec = _copy.deepcopy(record)
    prompt = rec.get("narrator_prompt")
    if isinstance(prompt, str):
        excess = len(full) - AB_CELL_LIMIT + 60  # запас під маркер
        keep = max(len(prompt) - excess, 0)
        rec["narrator_prompt"] = prompt[:keep] + f"…[truncated {len(prompt) - keep} chars]"
    out = json.dumps(rec, ensure_ascii=False, default=str)
    if len(out) <= AB_CELL_LIMIT:
        return out
    results = rec.get("results")
    if isinstance(results, dict):
        for r in results.values():
            if isinstance(r, dict) and isinstance(r.get("text"), str):
                r["text"] = _ab_truncate_text(r["text"], 8000)
    rec["narrator_prompt"] = _ab_truncate_text(rec.get("narrator_prompt"), 2000)
    out = json.dumps(rec, ensure_ascii=False, default=str)
    if len(out) > AB_CELL_LIMIT:
        out = out[:AB_CELL_LIMIT]  # останній рубіж (JSON буде зіпсований -> read відновить з колонок)
    return out


def _ab_build_row(record: dict) -> list:
    results = record.get("results") or {}
    mech = record.get("mechanics") or {}
    cols = {
        "ts": record.get("ts"), "type": record.get("type"), "turn_id": record.get("turn_id"),
        "user_id": record.get("user_id"), "mode": record.get("mode"), "vote": record.get("vote"),
        "vote_raw": record.get("vote_raw"), "vote_ms": record.get("vote_ms"),
        "reason": record.get("reason"), "shown_order": record.get("shown_order"),
        "outcome": mech.get("outcome"), "difficulty": mech.get("difficulty"),
        "natural_roll": mech.get("natural_roll"), "note": record.get("note"),
    }
    for m in _AB_MODELS:
        r = results.get(m) or {}
        cols[f"{m}_ms"] = r.get("total_ms")
        cols[f"{m}_attempt"] = r.get("final_attempt")
        cols[f"{m}_fallback"] = r.get("used_fallback")
        cols[f"{m}_len"] = r.get("len")
        cols[f"{m}_text"] = r.get("text")
    cols["record_json"] = _ab_record_json(record)
    return [_ab_cell(cols.get(h)) for h in AB_LOG_HEADERS]


def _ab_get_or_create_sheet(create: bool):
    """Sync. Повертає worksheet або None (create=False і аркуша нема)."""
    global _ab_sheet_ready, _ab_ws
    if _ab_sheet_ready and _ab_ws is not None:
        return _ab_ws
    try:
        ws = db.spreadsheet.worksheet(TAB_AB_LOG)
        _ab_sheet_ready, _ab_ws = True, ws
        return ws
    except gspread.exceptions.WorksheetNotFound:
        if not create:
            return None
    try:
        ws = db.spreadsheet.add_worksheet(title=TAB_AB_LOG, rows=1000, cols=len(AB_LOG_HEADERS))
        ws.append_row(AB_LOG_HEADERS, value_input_option="RAW")
    except Exception:
        # race: аркуш міг бути створений паралельно
        ws = db.spreadsheet.worksheet(TAB_AB_LOG)
    _ab_sheet_ready, _ab_ws = True, ws
    return ws


async def append_ab_log_row(record: dict) -> bool:
    """Додає один рядок в аркуш AB_Log. Never raises: при помилці лог + False."""
    try:
        def _sync():
            row = _ab_build_row(record)
            ws = _ab_get_or_create_sheet(create=True)
            try:
                ws.append_row(row, value_input_option="RAW")
            except gspread.exceptions.WorksheetNotFound:
                # аркуш видалили вручну -> скинути кеш і створити знову
                global _ab_sheet_ready
                _ab_sheet_ready = False  # скинути кеш
                ws2 = _ab_get_or_create_sheet(create=True)
                ws2.append_row(row, value_input_option="RAW")
            return True

        async with _ab_log_lock:
            return await asyncio.to_thread(_sync)
    except Exception as e:  # noqa: BLE001
        print(f"[AB_LOG ERROR] append failed: {type(e).__name__}: {e}")
        return False


def _ab_int(v):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _ab_bool(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _ab_rebuild_record(d: dict) -> dict:
    """Мінімальний dict з колонок (коли record_json зіпсований/порожній)."""
    shown = d.get("shown_order") or ""
    try:
        shown_parsed = json.loads(shown) if shown else None
    except (ValueError, TypeError):
        shown_parsed = None
    results = {}
    for m in _AB_MODELS:
        text = d.get(f"{m}_text") or ""
        ms = _ab_int(d.get(f"{m}_ms"))
        ln = _ab_int(d.get(f"{m}_len"))
        if not (text or ms is not None or ln is not None or d.get(f"{m}_attempt")):
            continue
        att = d.get(f"{m}_attempt") or None
        att_i = _ab_int(att)
        results[m] = {
            "text": text or None,
            "len": ln if ln is not None else len(text),
            "total_ms": ms if ms is not None else 0,
            "final_attempt": att_i if att_i is not None else att,
            "used_fallback": _ab_bool(d.get(f"{m}_fallback")),
        }
    rec = {
        "type": d.get("type") or "turn",
        "ts": d.get("ts") or "",
        "turn_id": d.get("turn_id") or "",
        "user_id": d.get("user_id") or "",
        "mode": d.get("mode") or "",
        "vote": d.get("vote") or None,
        "vote_raw": d.get("vote_raw") or None,
        "vote_ms": _ab_int(d.get("vote_ms")),
        "reason": d.get("reason") or None,
        "shown_order": shown_parsed,
        "results": results,
    }
    if d.get("note"):
        rec["note"] = d["note"]
    return rec


async def read_ab_log_records() -> list[dict]:
    """Читає всі записи з AB_Log. Missing sheet -> []. Never raises."""
    try:
        def _sync():
            ws = _ab_get_or_create_sheet(create=False)
            if ws is None:
                return []
            return ws.get_all_values()

        rows = await asyncio.to_thread(_sync)
        out = []
        for row in rows[1:]:
            if not any(str(c).strip() for c in row):
                continue
            padded = list(row) + [""] * (len(AB_LOG_HEADERS) - len(row))
            d = dict(zip(AB_LOG_HEADERS, padded))
            rec = None
            try:
                rec = json.loads(d.get("record_json") or "")
            except (ValueError, TypeError):
                rec = None
            if not isinstance(rec, dict):
                rec = _ab_rebuild_record(d)
            out.append(rec)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[AB_LOG ERROR] read failed: {type(e).__name__}: {e}")
        return []


# ================= РОБОТА З NPC =================

FROZEN_NPC_FIELDS = ("description", "character", "goal", "secrets")
FROZEN_REASON_MIN_LENGTH = 20  # символів — захист від "ok" / "так" / "epic event"


def _npc_tab_name(user_id) -> str:
    """Повертає ім'я аркуша Google Sheets для NPC конкретного гравця."""
    return f"NPC_{user_id}"


async def ensure_user_npc_sheet(user_id) -> bool:
    """Idempotent: створює аркуш NPC_<user_id> якщо його не існує.
    Додає рядок заголовків відповідно до структури NPC_DB.
    Повертає True при успіху (або якщо аркуш вже існував).
    """
    tab_name = _npc_tab_name(user_id)
    headers = [
        "Location", "Scene", "Name", "Description", "Character", "Goal", "Secrets",
        "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon",
        "Inventory", "Reputation_Score", "Region"
    ]

    def _sync_ensure():
        try:
            db.spreadsheet.worksheet(tab_name)
            # Аркуш вже існує — нічого не робимо
            return True
        except gspread.exceptions.WorksheetNotFound:
            pass
        try:
            worksheet = db.spreadsheet.add_worksheet(title=tab_name, rows=200, cols=15)
            worksheet.append_row(headers)
            print(f"[NPC SHEET] Створено аркуш '{tab_name}' для user {user_id}.")
            return True
        except Exception as e:
            print(f"[NPC SHEET ERROR] Не вдалося створити аркуш '{tab_name}': {e}")
            return False

    return await asyncio.to_thread(_sync_ensure)


async def delete_user_npc_sheet(user_id):
    """Видаляє аркуш NPC_<user_id>. No-op якщо аркуш не існує.
    Викликати при /restart для повного скидання стану гравця.
    """
    tab_name = _npc_tab_name(user_id)

    def _sync_delete():
        try:
            worksheet = db.spreadsheet.worksheet(tab_name)
            db.spreadsheet.del_worksheet(worksheet)
            print(f"[NPC SHEET] Аркуш '{tab_name}' видалено для user {user_id}.")
        except gspread.exceptions.WorksheetNotFound:
            pass  # вже немає — no-op
        except Exception as e:
            print(f"[NPC SHEET ERROR] Не вдалося видалити аркуш '{tab_name}': {e}")

    await asyncio.to_thread(_sync_delete)


async def refresh_npc_database(user_id):
    """Асинхронно завантажує NPC гравця з аркуша NPC_<user_id> у user_sessions.
    Якщо аркуш не існує — повертає False (не кидає виняток).
    """
    from core.engine import user_sessions

    tab_name = _npc_tab_name(user_id)

    def _sync_refresh():
        try:
            worksheet = db.get_sheet(tab_name)
            if not worksheet:
                return None, set(), 0

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
                scene = str(row.get("Scene", "Невідомо")).strip()
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
            print(f"[NPC DB ERROR] {e}")
            return None, set(), 0

    # Дочекатись фонових записів move_npcs_with_player, інакше reload зі Sheets
    # "відкотить" щойно переміщених компаньйонів у кеші.
    await _await_pending_moves(user_id)

    result, dead_result, count = await asyncio.to_thread(_sync_refresh)

    # Оборонне програмування: якщо session не існує — ініціалізуємо
    if user_id not in user_sessions:
        user_sessions[user_id] = {"npc_cache": {}, "dead_npc_names": set()}

    if result is not None:
        user_sessions[user_id]["npc_cache"] = result
        user_sessions[user_id]["dead_npc_names"] = dead_result
        print(f"[NPC DB] user {user_id}: завантажено {count} активних, {len(dead_result)} мертвих.", flush=True)
        return True

    return False


def _norm_npc_name(name) -> str:
    return str(name).strip().lower().replace("’", "'").replace("`", "'")


# Фонові записи Location/Scene від move_npcs_with_player: user_id -> set[Task].
# Тримаємо сильні посилання (інакше Task може бути зібраний GC).
_pending_move_tasks: dict = {}

_logger = logging.getLogger(__name__)
PENDING_MOVES_TIMEOUT_SEC = 10
NPC_MOVE_RETRY_DELAY_SEC = 1.5


async def _await_pending_moves(user_id):
    tasks = list(_pending_move_tasks.get(user_id, ()))
    if not tasks:
        return
    # asyncio.wait НЕ скасовує таски при timeout — фоновий запис продовжується.
    _done, pending = await asyncio.wait(tasks, timeout=PENDING_MOVES_TIMEOUT_SEC)
    if pending:
        _logger.warning(
            "[NPC MOVE] user %s: фонові записи не завершились за %ss — продовжуємо без очікування.",
            user_id, PENDING_MOVES_TIMEOUT_SEC,
        )


async def _write_npc_moves_to_sheet(user_id, names: list, location: str, scene: str, prev_tasks=()):
    """Фоновий запис Location/Scene/Region для переміщених NPC. Не кидає виняток.

    prev_tasks — попередні записи цього користувача: чекаємо їх (усередині таски),
    щоб порядок запису в Sheets збігався з порядком ходів.
    """
    if prev_tasks:
        await asyncio.gather(*prev_tasks, return_exceptions=True)

    tab_name = _npc_tab_name(user_id)
    targets = {_norm_npc_name(n) for n in names}
    region = get_region_for_location(location)

    def _sync_write():
        worksheet = db.get_sheet(tab_name)
        if not worksheet:
            _logger.warning("[NPC MOVE] Аркуш '%s' не знайдено.", tab_name)
            return 0
        all_values = worksheet.get_all_values()
        if not all_values:
            return 0
        headers = [h.strip().lower() for h in all_values[0]]
        if "name" not in headers or "location" not in headers or "scene" not in headers:
            _logger.warning("[NPC MOVE] У '%s' немає колонок Name/Location/Scene.", tab_name)
            return 0
        name_i, loc_i, scene_i = headers.index("name"), headers.index("location"), headers.index("scene")
        region_i = headers.index("region") if (region and "region" in headers) else None
        cells = []
        for r, row in enumerate(all_values[1:], start=2):
            if len(row) > name_i and _norm_npc_name(row[name_i]) in targets:
                cells.append(gspread.Cell(r, loc_i + 1, location))
                cells.append(gspread.Cell(r, scene_i + 1, scene))
                if region_i is not None:
                    cells.append(gspread.Cell(r, region_i + 1, region))
        if cells:
            worksheet.update_cells(cells)
        return len({c.row for c in cells})

    last_exc = None
    for attempt in (1, 2):
        try:
            n = await asyncio.to_thread(_sync_write)
            _logger.info("[NPC MOVE] user %s: записано %s NPC у Sheets -> %s/%s.", user_id, n, location, scene)
            return
        except Exception as e:
            last_exc = e
            if attempt == 1:
                await asyncio.sleep(NPC_MOVE_RETRY_DELAY_SEC)
    _logger.warning(
        "[NPC MOVE] user %s: запис у Sheets не вдався після повтору (NPC=%s, location=%s): %s",
        user_id, list(names), location, last_exc,
    )


async def move_npcs_with_player(chat_id, names: list, location: str, scene: str) -> list:
    """Переносить компаньйонів гравця у нову локацію/сцену.

    Кеш оновлюється одразу (без await між читанням і записом — атомарно для event loop),
    запис у Sheets іде у фоновій задачі через asyncio.to_thread. Оминає TELEPORT/Scene Drag
    guard-и update_npcs_in_db навмисно: імена валідовані проти ростеру + успішний кидок.
    Повертає канонічні імена фактично переміщених NPC.
    """
    from core.engine import user_sessions

    if not names:
        return []
    location = str(location or "").strip()
    scene = str(scene or "").strip()
    if location == NPC_LOCATION_BYPASS:
        _logger.warning("[NPC MOVE] Відхилено службову локацію '%s'.", location)
        return []
    if not location or location == TRAVEL_LOCATION or not is_valid_location(location):
        _logger.info("[NPC MOVE] Пропуск: непридатна локація '%s'.", location)
        return []
    session = user_sessions.get(chat_id)
    if not session:
        return []
    npc_cache = session.get("npc_cache") or {}
    dead_norm = {_norm_npc_name(d) for d in session.get("dead_npc_names", set())}

    # Індекс Active NPC: norm_name -> (канонічне ім'я)
    index = {}
    for npcs_list in npc_cache.values():
        for npc in npcs_list:
            nm = npc.get("name")
            if nm:
                index.setdefault(_norm_npc_name(nm), nm)

    moved = []
    for raw in names:
        key = _norm_npc_name(raw)
        if key in dead_norm:
            _logger.info("[NPC MOVE] '%s' мертвий — пропуск.", raw)
            continue
        canon = index.get(key)
        if not canon:
            _logger.info("[NPC MOVE] '%s' не в кеші Active NPC — пропуск.", raw)
            continue
        if canon not in moved:
            moved.append(canon)
    if not moved:
        return []

    # --- Синхронне оновлення кешу (нижче немає await) ---
    moving = set(moved)
    entries = {}
    for loc_key in list(npc_cache.keys()):
        keep = []
        for npc in npc_cache[loc_key]:
            if npc.get("name") in moving and npc.get("name") not in entries:
                entries[npc["name"]] = npc
            else:
                keep.append(npc)
        npc_cache[loc_key] = keep
    dest = npc_cache.setdefault(location, [])
    for nm in moved:
        entry = entries[nm]
        entry["scene"] = scene or "невідомо"
        new_region = get_region_for_location(location)
        if new_region:
            entry["region"] = new_region
        dest.append(entry)
    session["npc_cache"] = npc_cache

    # --- Фоновий запис у Sheets (серіалізований per-user: чекає попередні таски) ---
    bucket = _pending_move_tasks.setdefault(chat_id, set())
    prev_tasks = tuple(bucket)
    task = asyncio.create_task(
        _write_npc_moves_to_sheet(chat_id, moved, location, scene or "невідомо", prev_tasks)
    )
    bucket.add(task)

    def _done(t, _b=bucket, _cid=chat_id):
        _b.discard(t)
        if not _b:
            _pending_move_tasks.pop(_cid, None)

    task.add_done_callback(_done)
    return moved


def get_dead_npc_names(user_id) -> set:
    """Повертає копію множини імен мертвих NPC для гравця (або порожній set)."""
    from core.engine import user_sessions
    session = user_sessions.get(user_id)
    if session is None:
        return set()
    return set(session.get("dead_npc_names", set()))


def evict_npc_from_cache(user_id, matched_name: str):
    """Миттєво видаляє NPC з per-user кешу без звернення до Google Sheets."""
    from core.engine import user_sessions
    session = user_sessions.get(user_id)
    if session is None:
        return
    npc_cache = session.get("npc_cache", {})
    for loc in list(npc_cache.keys()):
        npc_cache[loc] = [n for n in npc_cache[loc] if n.get("name") != matched_name]


def _render_card(npc_data: dict, family_norm: dict) -> str:
    """Картка NPC для ростера. Якщо NPC — родич героя, після 1-го рядка вставляє рядок
    зв'язку. Повертає НОВИЙ рядок; спільний кеш (npc_data["card"]) не мутується."""
    card = npc_data["card"]
    if not family_norm:
        return card
    relation = family_norm.get(_norm_npc_name(npc_data.get("name", "")))
    if not relation:
        return card
    relation = " ".join(str(relation).split())  # старі профілі можуть містити \n/зайві пробіли
    line = f"- **Родинний зв'язок з героєм:** {relation} героя\n"
    first, sep, rest = card.partition("\n")
    return first + sep + line + rest if sep else first + "\n" + line


def get_location_npcs(user_id, current_location, current_scene, current_region=None,
                      hero_family: dict | None = None):
    """Синхронна функція. Повертає опис NPC, список легальних імен та dict репутації.
    Читає з user_sessions[user_id]["npc_cache"].
    Якщо session немає або кеш порожній — повертає ("", [], {}).
    Підтримує 3-рівневу фільтрацію: Регіон → Локація → Сцена.
    Якщо current_location == TRAVEL_LOCATION — показує регіональних мандрівників.
    hero_family: {ім'я: relation} (relation з погляду героя). Лише на рендері додає рядок
    "Родинний зв'язок з героєм" у картки родичів; кеш не змінюється. None = без змін.
    """
    from core.engine import user_sessions

    family_norm = {}
    if isinstance(hero_family, dict):
        family_norm = {_norm_npc_name(k): str(v).strip() for k, v in hero_family.items()
                       if str(k).strip() and str(v).strip()}

    session = user_sessions.get(user_id)
    if session is None:
        return "", [], {}

    npc_cache = session.get("npc_cache", {})
    if not npc_cache:
        return "", [], {}

    found_npcs = []
    legal_names = []
    reputation_context = {}

    is_traveling = str(current_location).strip() == TRAVEL_LOCATION
    target_loc = str(current_location).strip().lower()
    target_scene = str(current_scene).strip().lower()
    if not target_scene:
        target_scene = "невідомо"

    for loc_key, npcs_list in npc_cache.items():
        if loc_key == "GLOBAL":
            continue  # обробляємо окремо нижче

        if is_traveling:
            # Travel-режим: показуємо NPC чий регіон збігається і чия сцена = global/empty
            if current_region:
                for npc_data in npcs_list:
                    npc_region = npc_data.get("region", "").strip()
                    npc_scene = str(npc_data.get("scene", "")).strip().lower()
                    if npc_region == current_region and npc_scene in ("global", "", "невідомо"):
                        found_npcs.append(_render_card(npc_data, family_norm))
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
                        found_npcs.append(_render_card(npc_data, family_norm))
                        legal_names.append(npc_data["name"])
                        reputation_context[npc_data["name"]] = npc_data.get("reputation_score", 0)

    # Завжди додаємо абсолютно глобальних NPC
    if "GLOBAL" in npc_cache:
        for npc_data in npc_cache["GLOBAL"]:
            found_npcs.append(_render_card(npc_data, family_norm))
            legal_names.append(npc_data["name"])
            reputation_context[npc_data["name"]] = npc_data.get("reputation_score", 0)

    if not found_npcs:
        return "", [], {}

    npc_block = "=== VISIBLE NPC ROSTER (STRICTLY IN THIS SCENE) ===\n"
    npc_block += "GM INSTRUCTION: Only these characters are physically present here.\n"
    npc_block += "DO NOT HALLUCINATE NEW CHARACTERS IF A SUITABLE ONE IS HERE.\n\n"
    npc_block += "\n".join(found_npcs)

    return npc_block, legal_names, reputation_context


# ================= КАНОНІЧНИЙ STATBLOCK LOOKUP (COMBAT INIT ONLY) =================
#
# get_canon_npc_statblock() is the single entry-point for the combat engine to obtain
# a full D&D statblock for a named canon NPC.  It is PURE Python / in-memory:
# no gspread calls, no Gemini calls, no asyncio.to_thread needed.
#
# The module-level cache _CANON_STATBLOCK_INDEX is built lazily on first call and
# never mutated afterwards.  Each cache entry is a FILTERED (shallow) dict of only
# the fields in _STATBLOCK_FIELDS — NOT a full deep-copy of the canon record.
# Deep-copy is applied only at lookup time in get_canon_npc_statblock() so combat
# mutations cannot corrupt the index.

# Statblock fields the combat engine needs.  Narrative / lore fields are stripped so
# the engine receives a clean, minimal dict.
_STATBLOCK_FIELDS = frozenset({
    "Name",
    "ability_scores",
    "hp_max",
    "hp_current",
    "ac",
    "attacks",
    "saves",
    "skills",
    "cr",
    "conditions",
    "speed",
    "tags",
})

# Lazy cache: None means "not built yet".
# safe: asyncio is single-threaded; no await between check and assignment.
_CANON_STATBLOCK_INDEX: dict | None = None


def _build_canon_statblock_index() -> dict:
    """Build a Name → statblock dict from the canon registry (called once)."""
    from database.canon_npc import get_canon_npcs_copy
    index: dict = {}
    for npc in get_canon_npcs_copy():
        name = npc.get("Name", "").strip()
        if not name:
            continue
        statblock = {k: v for k, v in npc.items() if k in _STATBLOCK_FIELDS}
        index[name] = statblock
    return index


def get_canon_npc_statblock(name: str) -> dict | None:
    """Return a deep-copied D&D statblock dict for the canon NPC with this name.

    Looks up ``name`` in the canon registry (``database.canon_npc.get_canon_npcs_copy()``).
    Returns a dict containing AT LEAST these fields (when present in canon data):
        ability_scores, hp_max, hp_current, ac, attacks, saves, skills,
        cr, conditions, speed, tags, Name.
    Returns None if no canon NPC with this name exists.

    Matching is case-sensitive on the canonical 'Name' field.

    Returns a deep copy so callers (combat engine) cannot mutate the canon
    registry through the returned object.

    NOTE: Pure-Python, in-memory only.  No gspread / Gemini I/O.
    Intended for COMBAT INITIALIZATION; do not use for narrative/lore queries.
    """
    global _CANON_STATBLOCK_INDEX
    if _CANON_STATBLOCK_INDEX is None:
        _CANON_STATBLOCK_INDEX = _build_canon_statblock_index()

    entry = _CANON_STATBLOCK_INDEX.get(name)
    if entry is None:
        return None
    return _copy.deepcopy(entry)


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


async def update_npcs_in_db(user_id, updates, legal_names_list_deprecated=None,
                            player_new_location: str = None, player_location_changed: bool = False,
                            player_new_scene: str = None, player_scene_changed: bool = False,
                            companion_npcs: list = None,
                            frozen_fields_reason: str = "",
                            locked_names: list = None):
    """
    Асинхронно оновлює існуючих NPC в аркуші NPC_<user_id>.
    Вбудовано жорстку нормалізацію регістру та апострофів для difflib.
    locked_names: імена, щойно переміщені move_npcs_with_player у цьому ході —
    Location/Scene від GM для них ігноруються (інші поля оновлюються як зазвичай).
    """
    if not updates:
        return
    _locked_norm = {_norm_npc_name(n) for n in (locked_names or [])}
    # Не змагатися з фоновим записом переміщення
    await _await_pending_moves(user_id)

    print(f"[NPC UPDATE] user {user_id}: обробка змін: {json.dumps(updates, ensure_ascii=False)}")

    tab_name = _npc_tab_name(user_id)

    def _sync_update():
        worksheet = db.get_sheet(tab_name)
        if not worksheet:
            print(f"[NPC UPDATE] Аркуш '{tab_name}' не знайдено.")
            return False, []
        all_values = worksheet.get_all_values()

        if not all_values:
            return False, []

        headers = [h.strip().lower() for h in all_values[0]]
        col_map = {}
        # hp_current intentionally NOT here — during COMBAT, combat_state is authoritative;
        # persisting GM-emitted hp_current would clobber it.  To re-enable, ensure all
        # callers respect the COMBAT-strip invariant in core/engine.py.
        target_cols = ["status", "location", "scene", "memory_anchor", "goal", "secrets",
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

                    if field_key == "relation_player":
                        print(f"🚫 [RELATION_PLAYER GUARD] '{real_original_name}': "
                              f"Relation_Player ігнорується (system-managed derivative). "
                              f"Use reputation_delta from Worker → update_npc_reputation flow.")
                        continue

                    if field_key in ("location", "scene") and _norm_npc_name(real_original_name) in _locked_norm:
                        print(f"🔒 [MOVE LOCK] '{real_original_name}': {field_key} від GM ігнорується "
                              f"(NPC щойно переміщено разом з гравцем).")
                        continue

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

                    # Frozen-fields vetting: відкидаємо ТІЛЬКИ це поле, не весь update.
                    # Інші поля того самого NPC-об'єкта (Status, Location тощо) зберігаються.
                    if field_key in FROZEN_NPC_FIELDS:
                        reason_clean = (frozen_fields_reason or "").strip()
                        if len(reason_clean) < FROZEN_REASON_MIN_LENGTH:
                            print(f"🚫 [FROZEN GUARD] '{real_original_name}': field '{field_key}' rejected — "
                                  f"reason missing or too short ({len(reason_clean)} chars). "
                                  f"Need >= {FROZEN_REASON_MIN_LENGTH} chars in frozen_fields_change_reason.")
                            continue
                        print(f"✅ [FROZEN APPROVED] '{real_original_name}': field '{field_key}' updated "
                              f"(reason: {reason_clean[:60]}...)")

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
            await refresh_npc_database(user_id)
        for npc_name, event_text in anchor_updates:
            await append_memory_anchor(user_id, npc_name, event_text, game_day=0, rep_change=0)
    except Exception as e:
        print(f"❌ [UPDATE ERROR] {e}")


FAMILY_SEED_CLOSE_SCORE = 20      # "Тепле ставлення"
FAMILY_SEED_DISTANT_SCORE = 10    # "Обережно відкритий"
_FAMILY_DISTANT_MARKERS = (
    "двоюрід", "троюрід", "дядьк", "тітк", "племін", "кузен", "кузин", "дід", "дєд", "баб",
    "онук", "зведен", "назван", "швагер", "шурин", "зять", "невіст", "свекр", "тесть", "тещ",
    "вихован", "опікун", "кревн", "далек",
)
_NEUTRAL_RELATION_TEXTS = {"", "-", "neutral", "нейтральний", "нейтральна", "нейтральне"}  # порівняння через casefold()


def _family_seed_score(relation: str) -> int:
    """Близькі (батьки/брати/сестри/подружжя/діти) -> 20; дальні/невідомі -> 10."""
    rel = str(relation or "").strip().casefold()
    if any(m in rel for m in _FAMILY_DISTANT_MARKERS):
        return FAMILY_SEED_DISTANT_SCORE
    return FAMILY_SEED_CLOSE_SCORE if rel else FAMILY_SEED_DISTANT_SCORE


async def seed_family_reputation(user_id, family: list) -> list:
    """Тепла стартова репутація родичів героя в аркуші NPC_<user_id> (per-user, спільні дані не чіпає).
    family: list[{"name","relation"}]. Зіставлення — через _norm_npc_name (точний збіг).
    Змінює лише Active-рядки з Reputation_Score==0 І нейтральним/порожнім Relation_Player
    (не перезаписує дефолти канону та зміни гравця). Батч-запис (один batch_update).
    Повертає список імен (як у аркуші), яким виставлено ставлення. Не кидає виняток."""
    try:
        wanted = {}
        for m in (family or []):
            if isinstance(m, dict) and str(m.get("name", "")).strip() and str(m.get("relation", "")).strip():
                wanted.setdefault(_norm_npc_name(m["name"]), str(m["relation"]).strip())
        if not wanted:
            return []

        tab_name = _npc_tab_name(user_id)

        def _sync_seed():
            worksheet = db.get_sheet(tab_name)
            if not worksheet:
                _logger.warning("[FAMILY REP] user %s: аркуш %s не знайдено", user_id, tab_name)
                return []
            all_values = worksheet.get_all_values()
            if not all_values:
                _logger.warning("[FAMILY REP] user %s: аркуш %s порожній", user_id, tab_name)
                return []
            headers = [h.strip().lower() for h in all_values[0]]
            if "name" not in headers or "reputation_score" not in headers:
                _logger.warning("[FAMILY REP] user %s: у %s немає колонок Name/Reputation_Score", user_id, tab_name)
                return []
            name_i = headers.index("name")
            rep_i = headers.index("reputation_score")
            rel_i = headers.index("relation_player") if "relation_player" in headers else None
            status_i = headers.index("status") if "status" in headers else None

            def _cell(row, idx):
                return str(row[idx]).strip() if idx is not None and len(row) > idx else ""

            from database.canon_npc import _score_to_relation_text
            from gspread.utils import rowcol_to_a1
            batch, seeded, done = [], [], set()
            for r, row in enumerate(all_values[1:], start=2):
                key = _norm_npc_name(_cell(row, name_i))
                if key not in wanted or key in done:
                    continue
                if status_i is not None and _cell(row, status_i).lower() not in ("", "active"):
                    continue
                try:
                    cur = int(_cell(row, rep_i) or 0)
                except (ValueError, TypeError):
                    continue
                if cur != 0 or _cell(row, rel_i).casefold() not in _NEUTRAL_RELATION_TEXTS:
                    continue
                score = _family_seed_score(wanted[key])
                batch.append({"range": rowcol_to_a1(r, rep_i + 1), "values": [[score]]})
                if rel_i is not None:
                    batch.append({"range": rowcol_to_a1(r, rel_i + 1),
                                  "values": [[_score_to_relation_text(score)]]})
                seeded.append(_cell(row, name_i))
                done.add(key)
            if batch:
                worksheet.batch_update(batch)
            return seeded

        seeded = await asyncio.to_thread(_sync_seed)
        if seeded:
            await refresh_npc_database(user_id)
        return seeded
    except Exception as e:
        _logger.warning("[FAMILY REP] user %s: не вдалося виставити стартову репутацію: %s", user_id, e)
        return []


async def update_npc_reputation(user_id, npc_name, delta):
    """Атомарне оновлення Reputation_Score NPC в аркуші NPC_<user_id>. Clamp до [-100, 100]."""
    if not npc_name or not delta:
        return

    tab_name = _npc_tab_name(user_id)

    def _sync_rep_update():
        worksheet = db.get_sheet(tab_name)
        if not worksheet:
            print(f"[REP] Аркуш '{tab_name}' не знайдено.")
            return False
        all_values = worksheet.get_all_values()
        if not all_values:
            return False

        headers = [h.strip().lower() for h in all_values[0]]
        if "reputation_score" not in headers:
            print(f"[REP] Колонка 'Reputation_Score' відсутня в '{tab_name}'. Пропускаємо.")
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
                new_val = apply_reputation_step(old_val, delta)
                worksheet.update_cell(i, rep_col, new_val)
                print(f"[REP] {raw_name}: {old_val} -> {new_val} (raw delta {delta:+d})")
                # Автоматично синхронізуємо текстовий ярлик із новим Score
                from database.canon_npc import _score_to_relation_text
                if "relation_player" in headers:
                    rel_col = headers.index("relation_player") + 1
                    new_relation = _score_to_relation_text(new_val)
                    worksheet.update_cell(i, rel_col, new_relation)
                    print(f"[SYNC] {raw_name}: Relation_Player -> '{new_relation}'")
                return True

        print(f"[REP] NPC '{npc_name}' не знайдено в '{tab_name}'.")
        return False

    try:
        updated = await asyncio.to_thread(_sync_rep_update)
        if updated:
            await refresh_npc_database(user_id)
    except Exception as e:
        print(f"[REP UPDATE ERROR] {e}")


async def append_memory_anchor(user_id, npc_name, event_text, game_day=0, rep_change=0):
    """Додає подію до Memory_Anchor NPC в аркуші NPC_<user_id> як JSON-масив (FIFO 5 записів)."""
    if not npc_name or not event_text:
        return

    tab_name = _npc_tab_name(user_id)

    def _sync_memory_update():
        worksheet = db.get_sheet(tab_name)
        if not worksheet:
            print(f"[MEMORY] Аркуш '{tab_name}' не знайдено.")
            return False
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
                print(f"[MEMORY] {raw_name}: додано '{event_text}' (всього {len(memory_list)} записів)")
                return True

        return False

    try:
        await asyncio.to_thread(_sync_memory_update)
    except Exception as e:
        print(f"[MEMORY UPDATE ERROR] {e}")