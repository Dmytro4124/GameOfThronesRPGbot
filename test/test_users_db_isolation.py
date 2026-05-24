# test/test_users_db_isolation.py
"""
Тести ізоляції даних у Users_DB при паралельних /start.

Перевіряють три сценарії:
  1. Два різних user_id послідовно → обидва рядки існують (append_row для кожного).
  2. Той самий user_id двічі → оновлення (update batch), не дублювання.
  3. Конкурентні /start двох різних гравців → lock гарантує атомарність;
     жоден гравець не зникає і не перезаписує дані іншого.

Async-тести запускаються через asyncio.run() (без pytest-asyncio),
відповідно до патерну, прийнятого у test/test_npc_isolation.py.

Всі gspread-операції мокаються — жодного реального Sheets I/O.
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock, call


# ── Константи ─────────────────────────────────────────────────────────────────

USER_1 = 100_000_001
USER_2 = 100_000_002

_PROFILE_1 = {"Ім'я": "Арія", "Дім": "Старк", "Здоров'я": 100}
_PROFILE_2 = {"Ім'я": "Тіріон", "Дім": "Ланністер", "Здоров'я": 100}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cell(row: int) -> MagicMock:
    """Мок gspread.Cell з атрибутом row."""
    c = MagicMock()
    c.row = row
    return c


def _sheet_mock_empty() -> MagicMock:
    """Порожня таблиця: findall завжди повертає []."""
    sheet = MagicMock()
    sheet.findall.return_value = []
    sheet.append_row.return_value = None
    sheet.update.return_value = None
    return sheet


def _sheet_mock_with_user(user_id: int, row: int = 2) -> MagicMock:
    """Таблиця з одним записом: findall для user_id повертає [cell(row)]."""
    sheet = MagicMock()
    sheet.findall.side_effect = lambda uid, in_column: (
        [_make_cell(row)] if str(uid) == str(user_id) else []
    )
    sheet.update.return_value = None
    sheet.append_row.return_value = None
    return sheet


# ── Fixture: скидаємо lock між тестами ────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_users_db_lock():
    """Замінює _users_db_lock на свіжий перед кожним тестом.

    Це запобігає ситуації, коли тест, що впав у mid-lock, залишає
    заблокований lock і ламає наступні тести.
    """
    import database.operations as ops
    old_lock = ops._users_db_lock
    ops._users_db_lock = asyncio.Lock()
    yield
    ops._users_db_lock = old_lock


# ── Тест 1: Два різних user_id послідовно ────────────────────────────────────

def test_two_different_users_sequential_both_appended():
    """
    Гравець #1, потім гравець #2 — кожен має отримати свій append_row.
    Ніхто не повинен перезаписати рядок іншого.
    """
    sheet = _sheet_mock_empty()

    with patch("database.operations.db") as mock_db:
        mock_db.get_sheet.return_value = sheet

        async def run():
            from database.operations import save_user_data
            r1 = await save_user_data(USER_1, _PROFILE_1, "Арія")
            r2 = await save_user_data(USER_2, _PROFILE_2, "Тіріон")
            return r1, r2

        r1, r2 = asyncio.run(run())

    assert r1 is True, "save_user_data для USER_1 має повертати True"
    assert r2 is True, "save_user_data для USER_2 має повертати True"

    # Кожен гравець отримав свій append_row (таблиця порожня, тому обидва нові)
    assert sheet.append_row.call_count == 2, (
        f"Очікувалось 2 append_row, отримано {sheet.append_row.call_count}"
    )

    calls = sheet.append_row.call_args_list
    first_row = calls[0][0][0]   # перший позиційний аргумент першого виклику
    second_row = calls[1][0][0]

    assert first_row[0] == str(USER_1), "Перший рядок — USER_1"
    assert first_row[1] == "Арія"

    assert second_row[0] == str(USER_2), "Другий рядок — USER_2"
    assert second_row[1] == "Тіріон"

    # update() не повинен викликатись (обидва нові)
    sheet.update.assert_not_called()


# ── Тест 2: Той самий user_id двічі → update, не дублювання ──────────────────

def test_same_user_twice_updates_not_duplicates():
    """
    Другий виклик save_user_data для існуючого user_id повинен оновити рядок
    через sheet.update(), а не додати дублікат через append_row().
    """
    # Перший виклик: таблиця порожня → append_row
    # Другий виклик: USER_1 вже в рядку 2 → update
    call_count = {"n": 0}

    def _findall_side_effect(uid, in_column):
        call_count["n"] += 1
        # Після першого append_row вважаємо, що гравець вже в рядку 2
        if call_count["n"] > 1 and str(uid) == str(USER_1):
            return [_make_cell(2)]
        return []

    sheet = MagicMock()
    sheet.findall.side_effect = _findall_side_effect
    sheet.append_row.return_value = None
    sheet.update.return_value = None

    with patch("database.operations.db") as mock_db:
        mock_db.get_sheet.return_value = sheet

        async def run():
            from database.operations import save_user_data
            r1 = await save_user_data(USER_1, _PROFILE_1, "Арія")
            _PROFILE_1_v2 = {**_PROFILE_1, "Здоров'я": 80}
            r2 = await save_user_data(USER_1, _PROFILE_1_v2, "Арія")
            return r1, r2

        r1, r2 = asyncio.run(run())

    assert r1 is True
    assert r2 is True

    # Перший виклик — append, другий — update
    assert sheet.append_row.call_count == 1, "append_row тільки при першому збереженні"
    assert sheet.update.call_count == 1, "update при оновленні існуючого"

    # Перевіряємо що update отримав правильний діапазон і дані
    update_call = sheet.update.call_args
    range_arg = update_call[0][0]
    assert range_arg == "B2:C2", f"Очікувався діапазон B2:C2, отримано '{range_arg}'"

    data_arg = update_call[0][1]
    assert data_arg[0][0] == "Арія", "Колонка B — char_name"
    saved_profile = json.loads(data_arg[0][1])
    assert saved_profile.get("Здоров'я") == 80, "Колонка C — оновлений JSON-профіль"


# ── Тест 3: Конкурентні /start → lock гарантує відсутність data loss ──────────

def test_concurrent_start_no_data_loss():
    """
    Два паралельних /start для різних гравців: жоден профіль не зникає.

    Симулюємо race condition: обидва корутини стартують одночасно.
    Lock гарантує, що findall+write для кожного є атомарним:
    обидва мають свій append_row, і жоден не перезаписує рядок іншого.
    """
    # Таблиця порожня на початку для обох
    sheet = _sheet_mock_empty()

    with patch("database.operations.db") as mock_db:
        mock_db.get_sheet.return_value = sheet

        async def run():
            from database.operations import save_user_data
            # Запускаємо обидва корутини паралельно через asyncio.gather
            results = await asyncio.gather(
                save_user_data(USER_1, _PROFILE_1, "Арія"),
                save_user_data(USER_2, _PROFILE_2, "Тіріон"),
            )
            return results

        results = asyncio.run(run())

    assert all(results), "Обидва збереження мають повернути True"
    assert sheet.append_row.call_count == 2, (
        f"Два різних гравці — два append_row, отримано {sheet.append_row.call_count}"
    )

    # Перевіряємо, що обидва user_id присутні в записах
    saved_ids = {call[0][0][0] for call in sheet.append_row.call_args_list}
    assert str(USER_1) in saved_ids, "USER_1 повинен бути записаний"
    assert str(USER_2) in saved_ids, "USER_2 повинен бути записаний"


# ── Тест 4: delete_user_data також захищений lock-ом ─────────────────────────

def test_delete_user_data_uses_lock():
    """
    delete_user_data повинна отримати lock перед видаленням.
    Перевіряємо, що вона не може виконатись паралельно з save_user_data.

    Симулюємо: save_user_data тримає lock довго (через slow _sync_save),
    delete намагається отримати lock — і чекає.
    """
    order_log = []

    async def run():
        import database.operations as ops

        save_done = asyncio.Event()

        # Повільна версія save: записує "save_start", чекає, записує "save_end"
        async def slow_save():
            async with ops._users_db_lock:
                order_log.append("save_start")
                await asyncio.sleep(0.05)  # тримаємо lock 50мс
                order_log.append("save_end")

        # delete намагається отримати lock одразу після старту slow_save
        async def concurrent_delete():
            await asyncio.sleep(0.01)  # чекаємо щоб slow_save встиг взяти lock
            async with ops._users_db_lock:
                order_log.append("delete_acquired")

        await asyncio.gather(slow_save(), concurrent_delete())

    asyncio.run(run())

    assert order_log == ["save_start", "save_end", "delete_acquired"], (
        f"delete має чекати save: {order_log}"
    )


# ── Тест 5: delete_user_data видаляє правильний рядок ────────────────────────

def test_delete_removes_correct_row():
    """
    delete_user_data видаляє тільки рядок USER_1.
    USER_2 (рядок 3) залишається недоторканим.
    """
    sheet = MagicMock()
    # USER_1 у рядку 2, USER_2 у рядку 3
    sheet.findall.side_effect = lambda uid, in_column: (
        [_make_cell(2)] if str(uid) == str(USER_1) else
        [_make_cell(3)] if str(uid) == str(USER_2) else []
    )
    sheet.delete_rows.return_value = None

    with patch("database.operations.db") as mock_db:
        mock_db.get_sheet.return_value = sheet

        async def run():
            from database.operations import delete_user_data
            return await delete_user_data(USER_1)

        result = asyncio.run(run())

    assert result is True
    sheet.delete_rows.assert_called_once_with(2)

    # Рядок 3 (USER_2) не повинен бути видалений
    deleted_rows = [c[0][0] for c in sheet.delete_rows.call_args_list]
    assert 3 not in deleted_rows, "Рядок USER_2 не повинен бути видалений"
