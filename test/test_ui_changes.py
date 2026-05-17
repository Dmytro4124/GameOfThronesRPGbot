"""
test_ui_changes.py

Тести для UI-змін у bot/menus.py та bot/handlers.py.
Усього 25 тест-кейсів.

  Група A — menus.py (18 тест-кейсів):
    TestGetMainMenu (3):
      1. test_main_menu_has_map_button              — get_main_menu(): є "🗺 Карта"
      2. test_main_menu_no_inventory_button         — get_main_menu(): немає "🎒 Інвентар"
      3. test_main_menu_has_all_system_buttons      — get_main_menu(): всі системні кнопки (Профіль, Карта, Інструкція, Рестарт)

    TestGetDynamicMenuNoActions (3):
      4. test_dynamic_menu_no_actions_has_map       — get_dynamic_menu() без дій: є "🗺 Карта"
      5. test_dynamic_menu_no_actions_no_inventory  — get_dynamic_menu() без дій: немає "🎒 Інвентар"
      6. test_dynamic_menu_no_actions_has_system_buttons — всі системні кнопки присутні

    test_dynamic_menu_actions_map_present[...] (4 × parametrize — 1..4 дії):
      7–10. карта присутня для кожної кількості дій

    test_dynamic_menu_actions_no_inventory[...] (4 × parametrize):
      11–14. інвентар відсутній для кожної кількості дій

    test_dynamic_menu_actions_has_system_buttons[...] (4 × parametrize):
      15–18. всі системні кнопки присутні для кожної кількості дій

  Група B — handlers.py (7 тест-кейсів, структурні перевірки без aiogram-викликів):
    TestHandlersStructure (7):
      19. test_show_inventory_handler_does_not_exist       — модуль НЕ має атрибута show_inventory_handler
      20. test_show_profile_handler_exists                 — модуль МАЄ атрибут show_profile_handler
      21. test_cmd_map_exists                              — модуль МАЄ атрибут cmd_map
      22. test_show_profile_handler_source_has_gold        — source show_profile_handler містить "Особисте Золото"
      23. test_show_profile_handler_source_has_weapon      — source містить "Зброя"
      24. test_show_profile_handler_source_has_armor       — source містить "Броня"
      25. test_show_profile_handler_source_has_inventory   — source містить "Інвентар"
"""

import sys
import types
import inspect
import pytest
from unittest.mock import MagicMock


# ─── Хелпер: зібрати плоский set текстів кнопок з ReplyKeyboardMarkup ──────────

def _collect_button_texts(keyboard_markup) -> set:
    """Повертає set рядків text з кожного KeyboardButton у всіх рядках клавіатури."""
    return {btn.text for row in keyboard_markup.keyboard for btn in row}


# ─── Константи — очікувані кнопки ────────────────────────────────────────────

MAP_BTN = "🗺 Карта"
INVENTORY_BTN = "🎒 Інвентар"
PROFILE_BTN = "📜 Профіль"
HELP_BTN = "📖 Інструкція"
RESTART_BTN = "🔄 Рестарт"

SYSTEM_BTNS = {PROFILE_BTN, MAP_BTN, HELP_BTN, RESTART_BTN}


# ═══════════════════════════════════════════════════════════════════════════════
# Група A — menus.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetMainMenu:
    """Тести для get_main_menu() (= get_dynamic_menu без дій)."""

    def test_main_menu_has_map_button(self):
        """get_main_menu() повертає клавіатуру з кнопкою '🗺 Карта'."""
        from bot.menus import get_main_menu
        kb = get_main_menu()
        texts = _collect_button_texts(kb)
        assert MAP_BTN in texts, (
            f"'🗺 Карта' must be in keyboard. Got buttons: {texts!r}"
        )

    def test_main_menu_no_inventory_button(self):
        """get_main_menu() НЕ повертає кнопку '🎒 Інвентар'."""
        from bot.menus import get_main_menu
        kb = get_main_menu()
        texts = _collect_button_texts(kb)
        assert INVENTORY_BTN not in texts, (
            f"'🎒 Інвентар' must NOT be in keyboard. Got buttons: {texts!r}"
        )

    def test_main_menu_has_all_system_buttons(self):
        """get_main_menu() містить усі 4 системні кнопки: Профіль, Карта, Інструкція, Рестарт."""
        from bot.menus import get_main_menu
        kb = get_main_menu()
        texts = _collect_button_texts(kb)
        for btn in SYSTEM_BTNS:
            assert btn in texts, (
                f"System button {btn!r} must be present in main menu. Got: {texts!r}"
            )


class TestGetDynamicMenuNoActions:
    """get_dynamic_menu() без аргументів (actions=None)."""

    def test_dynamic_menu_no_actions_has_map(self):
        """get_dynamic_menu() без дій: є '🗺 Карта'."""
        from bot.menus import get_dynamic_menu
        kb = get_dynamic_menu()
        texts = _collect_button_texts(kb)
        assert MAP_BTN in texts, (
            f"'🗺 Карта' must be present. Got: {texts!r}"
        )

    def test_dynamic_menu_no_actions_no_inventory(self):
        """get_dynamic_menu() без дій: відсутня '🎒 Інвентар'."""
        from bot.menus import get_dynamic_menu
        kb = get_dynamic_menu()
        texts = _collect_button_texts(kb)
        assert INVENTORY_BTN not in texts, (
            f"'🎒 Інвентар' must NOT be present. Got: {texts!r}"
        )

    def test_dynamic_menu_no_actions_has_system_buttons(self):
        """get_dynamic_menu() без дій: всі системні кнопки присутні."""
        from bot.menus import get_dynamic_menu
        kb = get_dynamic_menu()
        texts = _collect_button_texts(kb)
        for btn in SYSTEM_BTNS:
            assert btn in texts, (
                f"System button {btn!r} must be present. Got: {texts!r}"
            )


@pytest.mark.parametrize("actions,count", [
    (["Дія Один"], 1),
    (["Дія Один", "Дія Два"], 2),
    (["Дія Один", "Дія Два", "Дія Три"], 3),
    (["Дія Один", "Дія Два", "Дія Три", "Дія Чотири"], 4),
])
def test_dynamic_menu_actions_map_present(actions, count):
    """get_dynamic_menu(actions) для 1–4 дій: '🗺 Карта' завжди присутня."""
    from bot.menus import get_dynamic_menu
    kb = get_dynamic_menu(actions=actions)
    texts = _collect_button_texts(kb)
    assert MAP_BTN in texts, (
        f"'🗺 Карта' must be present for {count} actions. Got buttons: {texts!r}"
    )


@pytest.mark.parametrize("actions,count", [
    (["Дія Один"], 1),
    (["Дія Один", "Дія Два"], 2),
    (["Дія Один", "Дія Два", "Дія Три"], 3),
    (["Дія Один", "Дія Два", "Дія Три", "Дія Чотири"], 4),
])
def test_dynamic_menu_actions_no_inventory(actions, count):
    """get_dynamic_menu(actions) для 1–4 дій: '🎒 Інвентар' завжди відсутня."""
    from bot.menus import get_dynamic_menu
    kb = get_dynamic_menu(actions=actions)
    texts = _collect_button_texts(kb)
    assert INVENTORY_BTN not in texts, (
        f"'🎒 Інвентар' must NOT be present for {count} actions. Got buttons: {texts!r}"
    )


@pytest.mark.parametrize("actions,count", [
    (["Дія Один"], 1),
    (["Дія Один", "Дія Два"], 2),
    (["Дія Один", "Дія Два", "Дія Три"], 3),
    (["Дія Один", "Дія Два", "Дія Три", "Дія Чотири"], 4),
])
def test_dynamic_menu_actions_has_system_buttons(actions, count):
    """get_dynamic_menu(actions) для 1–4 дій: всі системні кнопки присутні."""
    from bot.menus import get_dynamic_menu
    kb = get_dynamic_menu(actions=actions)
    texts = _collect_button_texts(kb)
    for btn in SYSTEM_BTNS:
        assert btn in texts, (
            f"System button {btn!r} must be present for {count} actions. Got: {texts!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Фікстура: ізольований імпорт bot.handlers без реальних зовнішніх залежностей
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def handlers_module():
    """
    Повертає модуль bot.handlers із замоканими зовнішніми залежностями:
    database.sheets, database.operations, core.world.
    Після тесту повертає оригінальний стан sys.modules.
    """
    _orig = {
        "database.sheets": sys.modules.get("database.sheets"),
        "database.operations": sys.modules.get("database.operations"),
        "core.world": sys.modules.get("core.world"),
        "bot.help_text": sys.modules.get("bot.help_text"),
        "bot.handlers": sys.modules.get("bot.handlers"),
    }

    # Stub database.sheets
    fake_sheets = types.ModuleType("database.sheets")
    fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = fake_sheets

    # Stub database.operations
    fake_ops = types.ModuleType("database.operations")
    for name in [
        "get_unique_regions", "get_houses_by_region", "get_house_stats_data",
        "get_user_data", "save_user_data", "delete_user_data",
        "clear_npc_cache", "delete_user_npc_sheet",
        "get_relevant_context", "get_location_npcs", "update_npcs_in_db",
        "update_npc_reputation", "append_memory_anchor", "get_dead_npc_names",
        "refresh_npc_database", "find_best_match", "ensure_user_npc_sheet",
        "_npc_tab_name",
    ]:
        setattr(fake_ops, name, MagicMock())
    sys.modules["database.operations"] = fake_ops

    # Stub core.world
    fake_world = types.ModuleType("core.world")
    for name in [
        "get_canon_characters", "generate_initial_stats", "get_narrative_intro",
        "background_canon_generation", "populate_contextual_npcs",
    ]:
        setattr(fake_world, name, MagicMock())
    sys.modules["core.world"] = fake_world

    # Stub bot.help_text (імпортується у handlers.py: from bot.help_text import HELP_TEXT, ADMIN_HELP_TEXT)
    fake_help_text = types.ModuleType("bot.help_text")
    fake_help_text.HELP_TEXT = "STUB_HELP_TEXT"
    fake_help_text.ADMIN_HELP_TEXT = "STUB_ADMIN_HELP_TEXT"
    sys.modules["bot.help_text"] = fake_help_text

    # Примусово перезавантажити bot.handlers
    sys.modules.pop("bot.handlers", None)
    import bot.handlers as hm

    yield hm

    # Відновлюємо оригінальні модулі
    for key, val in _orig.items():
        if val is not None:
            sys.modules[key] = val
        else:
            sys.modules.pop(key, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Група B — handlers.py (структурні перевірки)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandlersStructure:
    """Структурні тести bot/handlers.py — без реальних aiogram-викликів."""

    def test_show_inventory_handler_does_not_exist(self, handlers_module):
        """show_inventory_handler більше не є атрибутом модуля bot.handlers."""
        assert not hasattr(handlers_module, "show_inventory_handler"), (
            "show_inventory_handler must NOT exist in bot.handlers after the refactor. "
            "It was merged into show_profile_handler."
        )

    def test_show_profile_handler_exists(self, handlers_module):
        """show_profile_handler присутній у bot.handlers."""
        assert hasattr(handlers_module, "show_profile_handler"), (
            "show_profile_handler must exist in bot.handlers."
        )

    def test_cmd_map_exists(self, handlers_module):
        """cmd_map присутній у bot.handlers."""
        assert hasattr(handlers_module, "cmd_map"), (
            "cmd_map must exist in bot.handlers."
        )

    def test_show_profile_handler_source_has_gold(self, handlers_module):
        """Вихідний код show_profile_handler містить 'Особисте Золото'."""
        src = inspect.getsource(handlers_module.show_profile_handler)
        assert "Особисте Золото" in src, (
            "show_profile_handler must reference 'Особисте Золото' (gold field). "
            f"Source snippet: {src[:300]!r}"
        )

    def test_show_profile_handler_source_has_weapon(self, handlers_module):
        """Вихідний код show_profile_handler містить 'Зброя'."""
        src = inspect.getsource(handlers_module.show_profile_handler)
        assert "Зброя" in src, (
            "show_profile_handler must reference 'Зброя' (weapon field). "
            f"Source snippet: {src[:300]!r}"
        )

    def test_show_profile_handler_source_has_armor(self, handlers_module):
        """Вихідний код show_profile_handler містить 'Броня'."""
        src = inspect.getsource(handlers_module.show_profile_handler)
        assert "Броня" in src, (
            "show_profile_handler must reference 'Броня' (armor field). "
            f"Source snippet: {src[:300]!r}"
        )

    def test_show_profile_handler_source_has_inventory(self, handlers_module):
        """Вихідний код show_profile_handler містить 'Інвентар'."""
        src = inspect.getsource(handlers_module.show_profile_handler)
        assert "Інвентар" in src, (
            "show_profile_handler must reference 'Інвентар' (inventory field). "
            f"Source snippet: {src[:300]!r}"
        )
