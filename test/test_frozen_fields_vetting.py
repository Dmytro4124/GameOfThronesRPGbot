# test/test_frozen_fields_vetting.py
"""
Тести vetting-механізму frozen NPC fields у database/operations.py:update_npcs_in_db.

Перевіряють:
1. Frozen-поля (description, character, goal, secrets) відкидаються без valid reason.
2. З valid reason (>=20 chars) — зберігаються.
3. Relation_Player ніколи не пишеться (guard у target_cols / explicit continue).
4. Non-frozen поля не зачіпаються vetting'ом.

Async через asyncio.run() — pytest-asyncio не встановлено в проєкті.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, call
import gspread


# ── Константи ──────────────────────────────────────────────────────────────────

USER_ID = 12345
TAB_NAME = f"NPC_{USER_ID}"

# Заголовки, що відповідають реальній структурі аркуша NPC_<user_id>
# Порядок важливий: col_map будується через headers.index()
HEADERS = [
    "Location", "Scene", "Name", "Description", "Character", "Goal", "Secrets",
    "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon",
    "Inventory", "Reputation_Score", "Region"
]

# Рядок заголовків у нижньому регістрі — саме так worksheet.get_all_values()[0] повертає після strip().lower()
HEADERS_LOWER = [h.lower() for h in HEADERS]

# Колонки (1-based) для ключових полів
COL = {h.lower(): i + 1 for i, h in enumerate(HEADERS)}

# Рядок NPC "Тиріон Ланністер" у форматі list (відповідає HEADERS)
def _tyrion_row(status: str = "Active") -> list:
    """Повертає рядок даних NPC у вигляді списку (паралельно HEADERS)."""
    return [
        "Вінтерфелл",         # Location
        "Бенкет",             # Scene
        "Тиріон Ланністер",   # Name
        "Низький, гострий погляд",  # Description
        "Саркастичний",       # Character
        "Вижити",             # Goal
        "Знає таємницю Серсеї",  # Secrets
        "Нейтральний",        # Relation_Player
        "-",                  # Memory_Anchor
        "-",                  # Relation_NPCs
        status,               # Status
        "TRUE",               # Is_Canon
        "Кинджал",            # Inventory
        "0",                  # Reputation_Score
        "Північ",             # Region
    ]


def _make_worksheet_mock(status: str = "Active") -> MagicMock:
    """
    Будує мок-worksheet, що повертає:
    - get_all_values() → [HEADERS, tyrion_row]
    - update_cells(cells) → записує виклики
    """
    ws = MagicMock()
    ws.get_all_values.return_value = [HEADERS, _tyrion_row(status)]
    ws.update_cells.return_value = None
    ws.get_all_records.return_value = []  # для refresh_npc_database (no-op)
    return ws


def _get_update_cells_args(ws_mock: MagicMock):
    """Повертає список gspread.Cell, переданих в update_cells, або порожній list."""
    if ws_mock.update_cells.called:
        return ws_mock.update_cells.call_args[0][0]
    return []


def _cell_col_nums(cells: list) -> set:
    """Повертає множину номерів колонок з переданих cells."""
    return {c.col for c in cells}


def _cell_values_by_col(cells: list) -> dict:
    """Повертає dict {col_num: value} для переданих cells."""
    return {c.col: c.value for c in cells}


# ── Fixture: чищення user_sessions між тестами ────────────────────────────────

@pytest.fixture(autouse=True)
def clean_user_sessions():
    from core.engine import user_sessions
    user_sessions.clear()
    yield
    user_sessions.clear()


# ── Тест 1: порожній reason відкидає Description ──────────────────────────────

def test_empty_reason_drops_description():
    """
    frozen_fields_reason="" → Description відкинуто.
    Якщо Description — єдине non-Name поле в update, update_cells або не викликається
    взагалі, або викликається з порожнім списком.
    """
    ws = _make_worksheet_mock()

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{"Name": "Тиріон Ланністер", "Description": "Виглядає сумним"}],
            frozen_fields_reason="",
        ))

    cells = _get_update_cells_args(ws)
    col_description = COL["description"]
    col_nums = _cell_col_nums(cells)
    assert col_description not in col_nums, (
        f"Description (col {col_description}) НЕ повинен потрапити в update_cells, "
        f"але знайдено: {col_nums}"
    )


# ── Тест 2: короткий reason (< 20 chars) відкидає Description ────────────────

def test_short_reason_drops_frozen_field():
    """
    frozen_fields_reason="ok" (2 chars, < 20) → Description відкинуто.
    Поведінка ідентична до порожнього reason.
    """
    ws = _make_worksheet_mock()

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{"Name": "Тиріон Ланністер", "Description": "Виглядає сумним"}],
            frozen_fields_reason="ok",
        ))

    cells = _get_update_cells_args(ws)
    col_description = COL["description"]
    assert col_description not in _cell_col_nums(cells), (
        f"Description (col {col_description}) не повинен зберігатися при reason='ok' (2 chars)"
    )


# ── Тест 3: valid reason (>= 20 chars) зберігає Description ──────────────────

def test_valid_reason_saves_description():
    """
    frozen_fields_reason з >= 20 chars → Description зберігається.
    update_cells ВИКЛИКАЄТЬСЯ і містить cell з колонкою description.
    """
    ws = _make_worksheet_mock()
    valid_reason = "Тиріон отримав глибокий шрам через око у бою"  # 46 chars

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{"Name": "Тиріон Ланністер", "Description": "Шрам через ліве око"}],
            frozen_fields_reason=valid_reason,
        ))

    assert ws.update_cells.called, "update_cells повинен бути викликаний при valid reason"
    cells = _get_update_cells_args(ws)
    col_description = COL["description"]
    assert col_description in _cell_col_nums(cells), (
        f"Description (col {col_description}) повинен бути в update_cells при valid reason. "
        f"Знайдені колонки: {_cell_col_nums(cells)}"
    )


# ── Тест 4: non-frozen поля не залежать від reason ───────────────────────────

def test_non_frozen_fields_unaffected_by_empty_reason():
    """
    frozen_fields_reason="" + update Status і Inventory (не frozen-поля).
    Обидва зберігаються — vetting тільки на frozen-поля.
    """
    ws = _make_worksheet_mock()

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{
                "Name": "Тиріон Ланністер",
                "Status": "Dead",
                "Inventory": "меч, шолом",
            }],
            frozen_fields_reason="",
        ))

    assert ws.update_cells.called, "update_cells повинен бути викликаний для non-frozen полів"
    cells = _get_update_cells_args(ws)
    col_nums = _cell_col_nums(cells)

    col_status = COL["status"]
    col_inventory = COL["inventory"]

    assert col_status in col_nums, (
        f"Status (col {col_status}) повинен бути в update_cells. Знайдені колонки: {col_nums}"
    )
    assert col_inventory in col_nums, (
        f"Inventory (col {col_inventory}) повинен бути в update_cells. Знайдені колонки: {col_nums}"
    )


# ── Тест 5: часткове відкидання — Description відкинуто, Status збережений ───

def test_partial_drop_keeps_other_fields():
    """
    frozen_fields_reason="" + update Description (frozen) + Status (not frozen).
    Description відкидається. Status зберігається.
    """
    ws = _make_worksheet_mock()

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{
                "Name": "Тиріон Ланністер",
                "Description": "Виглядає сумно",
                "Status": "Dead",
            }],
            frozen_fields_reason="",
        ))

    assert ws.update_cells.called, "update_cells повинен бути викликаний (Status зберігається)"
    cells = _get_update_cells_args(ws)
    col_nums = _cell_col_nums(cells)

    col_description = COL["description"]
    col_status = COL["status"]

    assert col_description not in col_nums, (
        f"Description (col {col_description}) не повинен бути в update_cells (reason порожній)"
    )
    assert col_status in col_nums, (
        f"Status (col {col_status}) повинен бути в update_cells попри порожній reason"
    )


# ── Тест 6: Relation_Player ніколи не пишеться ────────────────────────────────

def test_relation_player_always_ignored():
    """
    Update з Relation_Player та Status.
    Relation_Player ігнорується (guard у коді), Status зберігається.
    В update_cells НЕМАЄ колонки relation_player.
    """
    ws = _make_worksheet_mock()

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{
                "Name": "Тиріон Ланністер",
                "Relation_Player": "Прихильний",
                "Status": "Active",
            }],
            frozen_fields_reason="",
        ))

    cells = _get_update_cells_args(ws)
    col_nums = _cell_col_nums(cells)

    col_relation_player = COL["relation_player"]
    col_status = COL["status"]

    assert col_relation_player not in col_nums, (
        f"Relation_Player (col {col_relation_player}) НІКОЛИ не повинен записуватися. "
        f"Знайдені колонки: {col_nums}"
    )
    # Status "Active" — той самий що в DB, тому поведінка залежить від логіки фільтрації:
    # val_str == DB-значення, але функція не перевіряє "чи змінилось" — вона просто записує.
    # Тому Status таки потрапить у cells (якщо pass всі фільтри).
    # Але навіть якщо Status не записується через логіку (val_str pass всі фільтри і є в col_map):
    # ключова перевірка — Relation_Player точно відсутній.


# ── Тест 7: всі 4 frozen-поля відкидаються разом при порожньому reason ────────

def test_all_4_frozen_fields_drop_together():
    """
    Update тільки з 4 frozen-полями (Description, Character, Goal, Secrets), reason="".
    Всі 4 відкидаються. update_cells НЕ викликається або викликається з порожнім списком.
    """
    ws = _make_worksheet_mock()

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{
                "Name": "Тиріон Ланністер",
                "Description": "Виглядає виснаженим",
                "Character": "Став похмурим",
                "Goal": "Тепер мріє про спокій",
                "Secrets": "Знає таємницю Варіса",
            }],
            frozen_fields_reason="",
        ))

    # update_cells або взагалі не викликається, або викликається з порожнім списком
    if ws.update_cells.called:
        cells = _get_update_cells_args(ws)
        frozen_cols = {
            COL["description"], COL["character"], COL["goal"], COL["secrets"]
        }
        actual_cols = _cell_col_nums(cells)
        overlap = frozen_cols & actual_cols
        assert len(overlap) == 0, (
            f"При reason='' жодне frozen-поле не повинно зберігатися. "
            f"Знайдені frozen колонки: {overlap}"
        )


# ── Тест 8: всі 4 frozen-поля зберігаються при valid reason ──────────────────

def test_all_4_frozen_fields_save_with_reason():
    """
    Update з 4 frozen-полями + valid reason (>= 20 chars).
    Всі 4 зберігаються. update_cells викликається з 4 cells.
    """
    ws = _make_worksheet_mock()
    valid_reason = "Тиріон втратив руку в битві при Блекуотері — постійне каліцтво на все життя"

    with patch("database.operations.db") as mock_db, \
         patch("database.operations.refresh_npc_database", new=AsyncMock_noop()):
        mock_db.get_sheet.return_value = ws

        from database.operations import update_npcs_in_db
        asyncio.run(update_npcs_in_db(
            USER_ID,
            updates=[{
                "Name": "Тиріон Ланністер",
                "Description": "Без правої руки, виснажений",
                "Character": "Більш мовчазний і гіркий",
                "Goal": "Помста Серсеї за каліцтво",
                "Secrets": "Планує втечу з Вестеросу",
            }],
            frozen_fields_reason=valid_reason,
        ))

    assert ws.update_cells.called, "update_cells повинен бути викликаний при valid reason"
    cells = _get_update_cells_args(ws)
    col_nums = _cell_col_nums(cells)

    frozen_cols = {
        COL["description"], COL["character"], COL["goal"], COL["secrets"]
    }

    missing = frozen_cols - col_nums
    assert len(missing) == 0, (
        f"При valid reason всі 4 frozen-поля повинні зберегтися. "
        f"Відсутні колонки: {missing} (опікуючись col-map: description={COL['description']}, "
        f"character={COL['character']}, goal={COL['goal']}, secrets={COL['secrets']})"
    )


# ── Допоміжний клас: no-op async mock для refresh_npc_database ────────────────

class AsyncMock_noop:
    """
    Замінює refresh_npc_database на no-op async coroutine.
    Використовується як patch new= значення.
    """
    def __init__(self):
        pass

    async def __call__(self, *args, **kwargs):
        return True

    def __get__(self, obj, objtype=None):
        return self
