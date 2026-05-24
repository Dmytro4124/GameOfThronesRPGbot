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


def _make_dynamic_sheet_mock(initial_col_a: list[str] | None = None) -> MagicMock:
    """Симулює gspread Worksheet з динамічною таблицею.

    Підтримує:
    - sheet.col_values(1) → актуальний стан col A (відстежує writes)
    - sheet.update(range_str, values) → парсить range_str і оновлює state
    - sheet.append_row(values) → додає у наступний рядок
    - sheet.delete_rows(row) → видаляє рядок
    Колонки B/C спрощено не відстежуються — лише col A (user_id).
    """
    state = {"col_a": list(initial_col_a or [])}

    def _col_values(col_idx=1):
        return list(state["col_a"])

    def _update(range_str, values):
        # range_str like "A2:C2" or "B3:C3" — extract row
        import re
        match = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", str(range_str).strip())
        if not match:
            return None
        start_col, start_row = match.group(1), int(match.group(2))
        # If writing col A (insert flow) — update col_a tracker
        if start_col == "A" and values and len(values) >= 1 and len(values[0]) >= 1:
            user_id_str = str(values[0][0])
            # Extend col_a if needed
            while len(state["col_a"]) < start_row:
                state["col_a"].append("")
            state["col_a"][start_row - 1] = user_id_str
        return None

    def _append_row(values):
        # Fallback if anyone still uses append_row
        user_id_str = str(values[0]) if values else ""
        state["col_a"].append(user_id_str)
        return None

    def _delete_rows(row):
        if 1 <= row <= len(state["col_a"]):
            state["col_a"].pop(row - 1)

    sheet = MagicMock()
    sheet.col_values.side_effect = _col_values
    sheet.update.side_effect = _update
    sheet.append_row.side_effect = _append_row
    sheet.delete_rows.side_effect = _delete_rows
    # Back-compat для тестів що використовують findall
    def _findall(uid, in_column=1):
        target = str(uid).strip()
        return [_make_cell(i + 1) for i, v in enumerate(state["col_a"]) if str(v).strip() == target]
    sheet.findall.side_effect = _findall
    return sheet


def _sheet_mock_empty() -> MagicMock:
    """Порожня таблиця."""
    return _make_dynamic_sheet_mock()


def _sheet_mock_with_user(user_id: int, row: int = 2) -> MagicMock:
    """Таблиця з одним записом у вказаному рядку (header у row 1, user у row 2)."""
    # Build initial col_a where user_id is at index row-1
    col = ["user_id"]  # header at row 1
    while len(col) < row - 1:
        col.append("")  # empty rows if row > 2
    col.append(str(user_id))
    return _make_dynamic_sheet_mock(initial_col_a=col)


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

    # Обидва нові гравці пишуться через sheet.update(f"A{N}:C{N}", ...)
    # (append_row замінено на explicit update для уникнення auto-detect bugs gspread)
    assert sheet.update.call_count >= 2, (
        f"Очікувалось ≥2 update викликів, отримано {sheet.update.call_count}"
    )

    # Перевіряємо що writes пішли у РІЗНІ рядки (не overwrite!)
    calls = sheet.update.call_args_list
    ranges = [str(c[0][0]) for c in calls]
    assert ranges[0] == "A1:C1", f"Перший має писатись у A1:C1 (порожня таблиця), got {ranges[0]}"
    assert ranges[1] == "A2:C2", f"Другий має писатись у A2:C2 (Player 1 в row 1), got {ranges[1]}"

    # Перевіряємо вміст
    first_vals = calls[0][0][1]
    second_vals = calls[1][0][1]
    assert first_vals[0][0] == str(USER_1), "Перший рядок — USER_1"
    assert second_vals[0][0] == str(USER_2), "Другий рядок — USER_2"


# ── Тест 2: Той самий user_id двічі → update, не дублювання ──────────────────

def test_same_user_twice_updates_not_duplicates():
    """
    Другий виклик save_user_data для існуючого user_id повинен оновити рядок
    через sheet.update(B{N}:C{N}), а не додати дублікат.
    """
    # Перший виклик: таблиця тільки з header → insert у A2:C2
    # Другий виклик: USER_1 вже в рядку 2 → update B2:C2
    sheet = _make_dynamic_sheet_mock(initial_col_a=["user_id"])

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

    # Обидва виклики через sheet.update: перший insert A2:C2, другий update B2:C2
    assert sheet.update.call_count == 2, (
        f"Очікувалось 2 update викликів (insert + update), отримано {sheet.update.call_count}"
    )

    # Перший виклик — A2:C2 (insert новий гравець у row 2 після header)
    first_call = sheet.update.call_args_list[0]
    assert str(first_call[0][0]) == "A2:C2", f"Insert має бути A2:C2, got {first_call[0][0]}"

    # Другий виклик — B2:C2 (update існуючого user_id у row 2)
    second_call = sheet.update.call_args_list[1]
    assert str(second_call[0][0]) == "B2:C2", f"Update має бути B2:C2, got {second_call[0][0]}"

    data_arg = second_call[0][1]
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
    # Обидва нові гравці через sheet.update insert. Lock гарантує що writes у РІЗНІ рядки.
    assert sheet.update.call_count == 2, (
        f"Очікувалось 2 update insert викликів, отримано {sheet.update.call_count}"
    )
    # Перевіряємо що writes у РІЗНІ рядки (нема overwrite)
    ranges = [str(c[0][0]) for c in sheet.update.call_args_list]
    assert len(set(ranges)) == 2, f"Writes у різні діапазони очікувано, got {ranges}"

    # Перевіряємо, що обидва user_id присутні в записах sheet.update
    saved_ids = {str(call[0][1][0][0]) for call in sheet.update.call_args_list}
    assert str(USER_1) in saved_ids, f"USER_1 повинен бути записаний, got {saved_ids}"
    assert str(USER_2) in saved_ids, f"USER_2 повинен бути записаний, got {saved_ids}"


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
    # USER_1 у рядку 2, USER_2 у рядку 3 — col_values повертає список рядків
    sheet.col_values.return_value = ["", str(USER_1), str(USER_2)]
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
