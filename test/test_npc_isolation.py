# test/test_npc_isolation.py
"""
Тести per-player NPC isolation.
Перевіряють, що NPC-стан повністю ізольований між гравцями:
- кожен гравець читає/пише тільки свій аркуш NPC_<user_id>
- user_sessions не мають shared-стану між різними user_id

Примітка щодо async: у проєкті встановлено anyio але не pytest-asyncio.
Async-тести запускаються через asyncio.run() всередині синхронних функцій.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch, call
import gspread.exceptions


# ── Константи для тестів ──────────────────────────────────────────────────────

USER_A = 111_111_111
USER_B = 222_222_222

SHEET_A = f"NPC_{USER_A}"
SHEET_B = f"NPC_{USER_B}"

# Мінімальний валідний рядок NPC (відповідає заголовкам у refresh_npc_database)
def _npc_row(name: str, location: str = "Вінтерфелл", scene: str = "Невідомо",
             status: str = "Active", is_canon: str = "FALSE") -> dict:
    return {
        "Location": location,
        "Scene": scene,
        "Name": name,
        "Description": f"Опис {name}",
        "Character": "-",
        "Goal": "-",
        "Secrets": "-",
        "Relation_Player": "Нейтральний",
        "Memory_Anchor": "-",
        "Relation_NPCs": "-",
        "Status": status,
        "Is_Canon": is_canon,
        "Inventory": "Пусто",
        "Reputation_Score": 0,
        "Region": "Північ",
    }


# ── Fixture: чищення user_sessions між тестами ───────────────────────────────

@pytest.fixture(autouse=True)
def clean_user_sessions():
    """Чистить user_sessions до і після кожного тесту, щоб не залишати стан між тестами."""
    from core.engine import user_sessions
    user_sessions.clear()
    yield
    user_sessions.clear()


# ── Helper: будує мок-worksheet з заданими записами ──────────────────────────

def _make_worksheet_mock(records: list) -> MagicMock:
    ws = MagicMock()
    ws.get_all_records.return_value = records
    return ws


# ── Тест 1: маршрутизація аркуша ─────────────────────────────────────────────

def test_per_user_sheet_routing():
    """
    refresh_npc_database(user_a) повинна читати аркуш NPC_<user_a>,
    а не NPC_<user_b> і не NPC_DB.
    """
    ws_a = _make_worksheet_mock([_npc_row("Арія Старк")])
    ws_b = _make_worksheet_mock([_npc_row("Тіріон Ланністер")])

    def fake_get_sheet(tab_name):
        if tab_name == SHEET_A:
            return ws_a
        if tab_name == SHEET_B:
            return ws_b
        return None

    with patch("database.operations.db") as mock_db:
        mock_db.get_sheet.side_effect = fake_get_sheet

        from database.operations import refresh_npc_database
        asyncio.run(refresh_npc_database(USER_A))

        # Перевіряємо: get_sheet викликано з аркушем user_a
        mock_db.get_sheet.assert_called_with(SHEET_A)

        # Жодного виклику з аркушем user_b або старим NPC_DB
        called_args = [c.args[0] for c in mock_db.get_sheet.call_args_list]
        assert SHEET_B not in called_args, f"Несподіваний доступ до аркуша user_b: {called_args}"
        assert "NPC_DB" not in called_args, f"Несподіваний доступ до старого NPC_DB: {called_args}"


# ── Тест 2: ізоляція user_sessions після refresh ─────────────────────────────

def test_user_sessions_isolation():
    """
    Після refresh_npc_database для двох різних user_id їхні npc_cache —
    різні об'єкти. Зміна кешу user_a не зачіпає user_b.
    """
    ws_a = _make_worksheet_mock([_npc_row("Еддард Старк", location="Вінтерфелл")])
    ws_b = _make_worksheet_mock([_npc_row("Тайвін Ланністер", location="Королівська Гавань")])

    def fake_get_sheet(tab_name):
        if tab_name == SHEET_A:
            return ws_a
        if tab_name == SHEET_B:
            return ws_b
        return None

    with patch("database.operations.db") as mock_db:
        mock_db.get_sheet.side_effect = fake_get_sheet

        from database.operations import refresh_npc_database
        from core.engine import user_sessions

        asyncio.run(refresh_npc_database(USER_A))
        asyncio.run(refresh_npc_database(USER_B))

        cache_a = user_sessions[USER_A]["npc_cache"]
        cache_b = user_sessions[USER_B]["npc_cache"]

        # Це різні об'єкти в пам'яті
        assert cache_a is not cache_b

        # Кеш A містить NPC user_a, не містить NPC user_b
        all_names_a = [n["name"] for npcs in cache_a.values() for n in npcs]
        assert "Еддард Старк" in all_names_a
        assert "Тайвін Ланністер" not in all_names_a

        # Кеш B містить NPC user_b, не містить NPC user_a
        all_names_b = [n["name"] for npcs in cache_b.values() for n in npcs]
        assert "Тайвін Ланністер" in all_names_b
        assert "Еддард Старк" not in all_names_b

        # Мутація кешу A не зачіпає B
        cache_a["TEST_KEY"] = [{"name": "Тест", "scene": "x", "card": "", "reputation_score": 0, "region": ""}]
        assert "TEST_KEY" not in cache_b


# ── Тест 3: get_location_npcs ізоляція ───────────────────────────────────────

def test_get_location_npcs_isolation():
    """
    get_location_npcs(user_a, ...) повертає тільки NPC з кешу A.
    get_location_npcs(user_b, ...) для несоздаого session повертає ("", [], {}).
    """
    from core.engine import user_sessions
    from database.operations import get_location_npcs

    # Ручно заповнюємо session для user_a (обходимо gspread)
    user_sessions[USER_A] = {
        "npc_cache": {
            "Вінтерфелл": [
                {"name": "Еддард Старк", "scene": "невідомо", "card": "> **Еддард Старк**\n",
                 "reputation_score": 0, "region": "Північ"},
            ]
        },
        "dead_npc_names": set(),
    }
    # user_b сесії немає зовсім

    result_a_block, names_a, rep_a = get_location_npcs(USER_A, "Вінтерфелл", "невідомо")
    result_b_block, names_b, rep_b = get_location_npcs(USER_B, "Вінтерфелл", "невідомо")

    # User A — бачить свого NPC
    assert "Еддард Старк" in names_a
    assert result_a_block != ""

    # User B — session відсутня → порожній результат
    assert result_b_block == ""
    assert names_b == []
    assert rep_b == {}


# ── Тест 4: clear_npc_cache ізоляція ─────────────────────────────────────────

def test_clear_npc_cache_per_user():
    """
    clear_npc_cache(user_a) чистить тільки сесію user_a,
    не торкається user_b.
    """
    from core.engine import user_sessions
    from database.operations import clear_npc_cache

    user_sessions[USER_A] = {
        "npc_cache": {"Вінтерфелл": [{"name": "NPC_A"}]},
        "dead_npc_names": {"Ned"},
    }
    user_sessions[USER_B] = {
        "npc_cache": {"Королівська Гавань": [{"name": "NPC_B"}]},
        "dead_npc_names": {"Joffrey"},
    }

    clear_npc_cache(USER_A)

    # User A очищено
    assert user_sessions[USER_A]["npc_cache"] == {}
    assert user_sessions[USER_A]["dead_npc_names"] == set()

    # User B не змінено
    assert user_sessions[USER_B]["npc_cache"] == {"Королівська Гавань": [{"name": "NPC_B"}]}
    assert user_sessions[USER_B]["dead_npc_names"] == {"Joffrey"}


# ── Тест 5: evict_npc_from_cache ізоляція ────────────────────────────────────

def test_evict_npc_per_user():
    """
    evict_npc_from_cache(user_a, 'Тіріон') видаляє Тіріона тільки з кешу user_a.
    Кеш user_b не змінюється.
    """
    from core.engine import user_sessions
    from database.operations import evict_npc_from_cache

    user_sessions[USER_A] = {
        "npc_cache": {
            "Вінтерфелл": [
                {"name": "Тіріон", "scene": "x", "card": "", "reputation_score": 0, "region": ""},
                {"name": "Арія", "scene": "x", "card": "", "reputation_score": 0, "region": ""},
            ]
        },
        "dead_npc_names": set(),
    }
    user_sessions[USER_B] = {
        "npc_cache": {
            "Вінтерфелл": [
                {"name": "Тіріон", "scene": "x", "card": "", "reputation_score": 0, "region": ""},
            ]
        },
        "dead_npc_names": set(),
    }

    evict_npc_from_cache(USER_A, "Тіріон")

    # У кеші user_a Тіріона більше немає
    names_a = [n["name"] for n in user_sessions[USER_A]["npc_cache"].get("Вінтерфелл", [])]
    assert "Тіріон" not in names_a
    # Арія лишилась
    assert "Арія" in names_a

    # У кеші user_b Тіріон є
    names_b = [n["name"] for n in user_sessions[USER_B]["npc_cache"].get("Вінтерфелл", [])]
    assert "Тіріон" in names_b


# ── Тест 6: get_dead_npc_names ізоляція ──────────────────────────────────────

def test_get_dead_npc_names_per_user():
    """
    get_dead_npc_names повертає різні множини для різних user_id.
    """
    from core.engine import user_sessions
    from database.operations import get_dead_npc_names

    user_sessions[USER_A] = {
        "npc_cache": {},
        "dead_npc_names": {"Роб Старк", "Еддард Старк"},
    }
    user_sessions[USER_B] = {
        "npc_cache": {},
        "dead_npc_names": {"Джоффрі Баратеон"},
    }

    dead_a = get_dead_npc_names(USER_A)
    dead_b = get_dead_npc_names(USER_B)

    assert dead_a == {"Роб Старк", "Еддард Старк"}
    assert dead_b == {"Джоффрі Баратеон"}
    assert dead_a != dead_b

    # Повертає копію, не посилання — мутація не зачіпає session
    dead_a.add("Кейтлін Старк")
    assert "Кейтлін Старк" not in user_sessions[USER_A]["dead_npc_names"]


# ── Тест 7: ensure_user_npc_sheet idempotent ─────────────────────────────────

def test_ensure_user_npc_sheet_idempotent():
    """
    Перший виклик ensure_user_npc_sheet створює аркуш.
    Повторний виклик не дублює аркуш і не падає.
    """
    worksheet_mock = MagicMock()

    with patch("database.operations.db") as mock_db:
        # Перший виклик: аркуш не існує → створюємо
        mock_db.spreadsheet.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("not found")
        mock_db.spreadsheet.add_worksheet.return_value = worksheet_mock

        from database.operations import ensure_user_npc_sheet
        result_first = asyncio.run(ensure_user_npc_sheet(USER_A))
        assert result_first is True
        mock_db.spreadsheet.add_worksheet.assert_called_once()
        worksheet_mock.append_row.assert_called_once()

        # Другий виклик: аркуш вже існує → no-op
        mock_db.spreadsheet.worksheet.side_effect = None
        mock_db.spreadsheet.worksheet.return_value = worksheet_mock

        result_second = asyncio.run(ensure_user_npc_sheet(USER_A))
        assert result_second is True
        # add_worksheet НЕ викликається повторно
        mock_db.spreadsheet.add_worksheet.assert_called_once()


# ── Тест 8: delete_user_npc_sheet no-op if missing ───────────────────────────

def test_delete_user_npc_sheet_no_op_if_missing():
    """
    delete_user_npc_sheet не падає, якщо аркуш не існує (WorksheetNotFound).
    """
    with patch("database.operations.db") as mock_db:
        mock_db.spreadsheet.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("not found")

        from database.operations import delete_user_npc_sheet
        # Не повинно кидати виняток
        asyncio.run(delete_user_npc_sheet(USER_A))

        # del_worksheet НЕ був викликаний (нема чого видаляти)
        mock_db.spreadsheet.del_worksheet.assert_not_called()
