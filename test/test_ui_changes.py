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
    _stub_keys = [
        "database.sheets",
        "database.operations",
        "core.world",
        "core.intro_cache",
        "bot.help_text",
        "bot.handlers",
    ]
    _orig = {k: sys.modules.get(k) for k in _stub_keys}

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

    # Stub core.world (включно з build_fallback_intro, доданим у Phase 9)
    fake_world = types.ModuleType("core.world")
    for name in [
        "get_canon_characters", "generate_initial_stats", "get_narrative_intro",
        "background_canon_generation", "populate_contextual_npcs", "build_fallback_intro",
    ]:
        setattr(fake_world, name, MagicMock())
    sys.modules["core.world"] = fake_world

    # Stub core.intro_cache
    fake_intro_cache = types.ModuleType("core.intro_cache")
    fake_intro_cache.get_cached_intro = MagicMock(return_value=None)
    fake_intro_cache.set_cached_intro = MagicMock()
    sys.modules["core.intro_cache"] = fake_intro_cache

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

    def test_profile_helpers_source_has_gold(self, handlers_module):
        """Код display-хелперів містить 'Особисте Золото' (поле золота гравця)."""
        src_dnd = inspect.getsource(handlers_module._build_dnd_profile_text)
        src_legacy = inspect.getsource(handlers_module._build_legacy_profile_text)
        combined = src_dnd + src_legacy
        assert "Особисте Золото" in combined, (
            "Profile helpers must reference 'Особисте Золото' (gold field)."
        )

    def test_profile_helpers_source_has_weapon(self, handlers_module):
        """Код display-хелперів містить 'Зброя'."""
        src_dnd = inspect.getsource(handlers_module._build_dnd_profile_text)
        src_legacy = inspect.getsource(handlers_module._build_legacy_profile_text)
        combined = src_dnd + src_legacy
        assert "Зброя" in combined, (
            "Profile helpers must reference 'Зброя' (weapon field)."
        )

    def test_profile_helpers_source_has_armor(self, handlers_module):
        """Код display-хелперів містить 'Броня'."""
        src_dnd = inspect.getsource(handlers_module._build_dnd_profile_text)
        src_legacy = inspect.getsource(handlers_module._build_legacy_profile_text)
        combined = src_dnd + src_legacy
        assert "Броня" in combined, (
            "Profile helpers must reference 'Броня' (armor field)."
        )

    def test_profile_helpers_source_has_inventory(self, handlers_module):
        """Код display-хелперів містить 'Інвентар'."""
        src_dnd = inspect.getsource(handlers_module._build_dnd_profile_text)
        src_legacy = inspect.getsource(handlers_module._build_legacy_profile_text)
        combined = src_dnd + src_legacy
        assert "Інвентар" in combined, (
            "Profile helpers must reference 'Інвентар' (inventory field)."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Група C — D&D profile display (нові тести Phase 9)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_dnd_profile(**overrides) -> dict:
    """Мінімальний валідний D&D-профіль для тестів."""
    base = {
        "Ім'я": "Джон Сноу",
        "Дім": "Старк",
        "Титул": "Лорд-командувач",
        "Світогляд": "Законно-добрий",
        "Поточне місцезнаходження": "Вінтерфелл",
        "Регіон": "Північ",
        "Поточна сцена": "Тронна зала",
        "Ігровий час": "298 р.З., весна",
        "Особисте Золото": 50,
        "Зброя": "Довгий меч (Longclaw)",
        "Броня": "Кольчуга",
        "Інвентар": "Факел, мотузка",
        "class": "Knight",
        "heritage": "First Men (Stark line)",
        "level": 3,
        "xp": 900,
        "hp_current": 24,
        "hp_max": 30,
        "ac": 16,
        "proficiency_bonus": 2,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 13, "CHA": 11},
        "saves_proficient": ["STR", "CON"],
        "skill_profs": ["Athletics", "Persuasion"],
        "skill_expertise": [],
        "features": [
            {"name": "Knightly Code", "desc": "Перевага на рятівні кидки проти Переляку.", "source": "Knight L1"},
            {"name": "Extra Attack", "desc": "Атакуєш двічі у рамках однієї дії Attack.", "source": "Knight L5"},
        ],
        "conditions": [],
        "mode": "NORMAL",
        "Репутація (Рідний регіон)": 15,
    }
    base.update(overrides)
    return base


class TestDnDProfileDisplay:
    """Тести рендерингу D&D-профілю через _build_dnd_profile_text."""

    def test_dnd_profile_contains_class(self, handlers_module):
        """D&D профіль містить рядок з 'Клас:'."""
        profile = _make_dnd_profile()
        text = handlers_module._build_dnd_profile_text(profile, chat_id=123)
        assert "Клас:" in text, f"Expected 'Клас:' in profile text. Got:\n{text[:500]}"

    def test_dnd_profile_contains_ac(self, handlers_module):
        """D&D профіль містить 'AC:'."""
        profile = _make_dnd_profile()
        text = handlers_module._build_dnd_profile_text(profile, chat_id=123)
        assert "AC:" in text, f"Expected 'AC:' in profile text. Got:\n{text[:500]}"

    def test_dnd_profile_contains_hp(self, handlers_module):
        """D&D профіль містить 'HP:'."""
        profile = _make_dnd_profile()
        text = handlers_module._build_dnd_profile_text(profile, chat_id=123)
        assert "HP:" in text, f"Expected 'HP:' in profile text. Got:\n{text[:500]}"

    def test_dnd_profile_contains_ability_scores(self, handlers_module):
        """D&D профіль містить рядок зі здібностями (STR, DEX тощо)."""
        profile = _make_dnd_profile()
        text = handlers_module._build_dnd_profile_text(profile, chat_id=123)
        assert "STR" in text and "DEX" in text and "CON" in text, (
            f"Expected STR/DEX/CON ability scores in profile text. Got:\n{text[:600]}"
        )

    def test_legacy_profile_fallback_no_crash(self, handlers_module):
        """Legacy профіль (без ability_scores) не кидає виняток і повертає рядок."""
        legacy = {
            "Ім'я": "Старий герой",
            "Дім": "Ланністер",
            "Поточне місцезнаходження": "Ланніспорт",
            "Ігровий час": "298 р.З.",
            "Особисте Золото": 100,
            "Зброя": "Меч",
            "Броня": "Шкіряна",
            "Інвентар": "Нічого",
            "Репутація (Рідний регіон)": 0,
            "Здоров'я": 80,
            "Енергія": 600,
            "Бойові навички": 45,
            "Військові навички": 30,
            "Інтрига": 20,
            "Управління": 35,
        }
        text = handlers_module._build_legacy_profile_text(legacy)
        assert isinstance(text, str) and len(text) > 20, (
            f"Legacy profile must return non-empty string. Got: {text!r}"
        )

    def test_combat_mode_profile_contains_active_combat(self, handlers_module):
        """Профіль із mode=COMBAT містить 'АКТИВНИЙ БІЙ'."""
        profile = _make_dnd_profile(mode="COMBAT")
        # combat_state не існує в тесті — exception буде спіймано в except
        text = handlers_module._build_dnd_profile_text(profile, chat_id=999)
        assert "АКТИВНИЙ БІЙ" in text, (
            f"Expected 'АКТИВНИЙ БІЙ' in COMBAT mode profile. Got:\n{text[:600]}"
        )

    def test_conditions_block_shown_when_nonempty(self, handlers_module):
        """Якщо conditions не порожній — профіль містить 'Стани:'."""
        profile = _make_dnd_profile(conditions=[{"name": "Poisoned", "duration": "3 rounds", "source": "dart"}])
        text = handlers_module._build_dnd_profile_text(profile, chat_id=123)
        assert "Стани:" in text, f"Expected 'Стани:' when conditions present. Got:\n{text[:600]}"

    def test_max_level_shows_max_xp(self, handlers_module):
        """Для level=20 наступний XP показує 'MAX'."""
        profile = _make_dnd_profile(level=20, xp=355000)
        text = handlers_module._build_dnd_profile_text(profile, chat_id=123)
        assert "MAX" in text, f"Expected 'MAX' for level 20 next XP. Got:\n{text[:500]}"


def _load_real_help_text() -> str:
    """Завантажує HELP_TEXT безпосередньо з файлу, минаючи sys.modules (захист від stub)."""
    import importlib
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).parent.parent / "bot" / "help_text.py"
    spec = importlib.util.spec_from_file_location("_bot_help_text_real", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.HELP_TEXT


class TestInstructionText:
    """Тести тексту інструкції (HELP_TEXT у bot/help_text.py).

    Читаємо файл через importlib.util щоб уникнути stub із handlers_module fixture.
    """

    def test_help_text_contains_1d20(self):
        """HELP_TEXT містить '1d20'."""
        text = _load_real_help_text()
        assert "1d20" in text, "HELP_TEXT must mention '1d20' (D&D roll system)."

    def test_help_text_contains_dc(self):
        """HELP_TEXT містить 'DC'."""
        text = _load_real_help_text()
        assert "DC" in text, "HELP_TEXT must mention 'DC' (Difficulty Class)."

    def test_help_text_contains_18_skills(self):
        """HELP_TEXT згадує 18 навичок."""
        text = _load_real_help_text()
        assert "18 навичок" in text, "HELP_TEXT must mention '18 навичок'."

    def test_help_text_contains_got_classes(self):
        """HELP_TEXT містить '9 GoT-класів'."""
        text = _load_real_help_text()
        assert "9 GoT-класів" in text, "HELP_TEXT must mention '9 GoT-класів'."

    def test_help_text_within_telegram_limit(self):
        """HELP_TEXT не перевищує 4096 символів (ліміт Telegram)."""
        text = _load_real_help_text()
        assert len(text) <= 4096, (
            f"HELP_TEXT exceeds Telegram 4096 char limit: {len(text)} chars."
        )
