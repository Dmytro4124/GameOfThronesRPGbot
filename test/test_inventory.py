"""
test_inventory.py
Unit tests for core/inventory.py — structured inventory with quantity tracking.

Coverage:
  - parse_inventory: str / list[str] / list[dict] / mixed / empty / sentinel strings
  - format_inventory: display string generation
  - add_item: new item / increment existing / case-insensitive match / quantity floor
  - remove_item: partial / excess / not found / case-insensitive / removes entry at 0
  - _parse_one: "name x3" / "name" / empty / Cyrillic х / × variants
  - apply_system_impacts integration: inventory_new / inventory_lost with quantities
  - backward compat: existing CSV-string profiles work correctly
"""

import pytest
from core.inventory import (
    parse_inventory,
    format_inventory,
    add_item,
    remove_item,
    _parse_one,
)


# ---------------------------------------------------------------------------
# _parse_one
# ---------------------------------------------------------------------------

class TestParseOne:
    def test_plain_name(self):
        assert _parse_one("Рапіра") == {"name": "Рапіра", "quantity": 1}

    def test_latin_x_quantity(self):
        assert _parse_one("Лист x3") == {"name": "Лист", "quantity": 3}

    def test_cyrillic_х_quantity(self):
        # Cyrillic х (U+0445) — common OCR confusion with ASCII x
        assert _parse_one("Стріла х5") == {"name": "Стріла", "quantity": 5}

    def test_unicode_times_quantity(self):
        assert _parse_one("Зілля × 2") == {"name": "Зілля", "quantity": 2}

    def test_multiword_name(self):
        assert _parse_one("Запечатаний лист x3") == {"name": "Запечатаний лист", "quantity": 3}

    def test_empty_string_returns_none(self):
        assert _parse_one("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_one("   ") is None

    def test_strips_surrounding_whitespace(self):
        result = _parse_one("  Рапіра  ")
        assert result == {"name": "Рапіра", "quantity": 1}

    def test_quantity_with_spaces(self):
        assert _parse_one("Лист x 3") == {"name": "Лист", "quantity": 3}


# ---------------------------------------------------------------------------
# parse_inventory
# ---------------------------------------------------------------------------

class TestParseInventory:
    def test_csv_string(self):
        result = parse_inventory("Рапіра, Лист x3")
        assert result == [
            {"name": "Рапіра", "quantity": 1},
            {"name": "Лист", "quantity": 3},
        ]

    def test_csv_string_with_plain_items(self):
        result = parse_inventory("Рапіра, Вишукані шати, Перстень")
        assert result == [
            {"name": "Рапіра", "quantity": 1},
            {"name": "Вишукані шати", "quantity": 1},
            {"name": "Перстень", "quantity": 1},
        ]

    def test_list_of_strings(self):
        result = parse_inventory(["Рапіра", "Лист x3"])
        assert result == [
            {"name": "Рапіра", "quantity": 1},
            {"name": "Лист", "quantity": 3},
        ]

    def test_already_structured_list(self):
        items = [{"name": "Рапіра", "quantity": 1}]
        result = parse_inventory(items)
        assert result == items

    def test_already_structured_clamps_quantity(self):
        items = [{"name": "Рапіра", "quantity": 0}]
        result = parse_inventory(items)
        assert result[0]["quantity"] == 1

    def test_empty_string(self):
        assert parse_inventory("") == []

    def test_none(self):
        assert parse_inventory(None) == []

    def test_empty_list(self):
        assert parse_inventory([]) == []

    def test_sentinel_strings(self):
        assert parse_inventory("Пусто") == []
        assert parse_inventory("-") == []
        assert parse_inventory("Нічого") == []
        assert parse_inventory("порожній") == []

    def test_mixed_list_dicts_and_strings(self):
        mixed = [{"name": "Рапіра", "quantity": 1}, "Лист x3"]
        result = parse_inventory(mixed)
        assert result == [
            {"name": "Рапіра", "quantity": 1},
            {"name": "Лист", "quantity": 3},
        ]

    def test_full_legacy_csv(self):
        """Simulate a real existing profile inventory string."""
        raw = "Рапіра, Вишукані шати, Перстень з гербом, Запечатані рекомендаційні листи x3"
        result = parse_inventory(raw)
        assert len(result) == 4
        assert result[3] == {"name": "Запечатані рекомендаційні листи", "quantity": 3}


# ---------------------------------------------------------------------------
# format_inventory
# ---------------------------------------------------------------------------

class TestFormatInventory:
    def test_single_item_no_qty_suffix(self):
        items = [{"name": "Рапіра", "quantity": 1}]
        assert format_inventory(items) == "Рапіра"

    def test_item_with_qty_suffix(self):
        items = [{"name": "Лист", "quantity": 3}]
        assert format_inventory(items) == "Лист x3"

    def test_multiple_items(self):
        items = [
            {"name": "Лист", "quantity": 3},
            {"name": "Рапіра", "quantity": 1},
        ]
        assert format_inventory(items) == "Лист x3, Рапіра"

    def test_empty_list(self):
        assert format_inventory([]) == "Пусто"

    def test_roundtrip(self):
        """parse → format round-trip preserves canonical display."""
        original = "Рапіра, Лист x3, Зілля"
        items = parse_inventory(original)
        result = format_inventory(items)
        assert result == original


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------

class TestAddItem:
    def test_add_new_item(self):
        inv = []
        add_item(inv, "Рапіра")
        assert inv == [{"name": "Рапіра", "quantity": 1}]

    def test_increment_existing_item(self):
        inv = [{"name": "Лист", "quantity": 2}]
        add_item(inv, "Лист", 3)
        assert inv == [{"name": "Лист", "quantity": 5}]

    def test_case_insensitive_match(self):
        inv = [{"name": "лист", "quantity": 1}]
        add_item(inv, "Лист", 2)
        assert inv == [{"name": "лист", "quantity": 3}]

    def test_quantity_floor_at_1(self):
        inv = []
        add_item(inv, "Рапіра", 0)
        assert inv[0]["quantity"] == 1

    def test_negative_quantity_floor(self):
        inv = []
        add_item(inv, "Рапіра", -5)
        assert inv[0]["quantity"] == 1

    def test_add_with_explicit_quantity(self):
        inv = []
        add_item(inv, "Стріла", 10)
        assert inv == [{"name": "Стріла", "quantity": 10}]

    def test_strips_whitespace_from_name(self):
        inv = [{"name": "Рапіра", "quantity": 1}]
        add_item(inv, "  Рапіра  ", 1)
        assert inv == [{"name": "Рапіра", "quantity": 2}]


# ---------------------------------------------------------------------------
# remove_item
# ---------------------------------------------------------------------------

class TestRemoveItem:
    def test_remove_partial(self):
        inv = [{"name": "Лист", "quantity": 5}]
        removed = remove_item(inv, "Лист", 2)
        assert removed == 2
        assert inv == [{"name": "Лист", "quantity": 3}]

    def test_remove_excess_caps_at_available(self):
        inv = [{"name": "Лист", "quantity": 2}]
        removed = remove_item(inv, "Лист", 5)
        assert removed == 2
        assert inv == []  # entry removed when qty reaches 0

    def test_remove_all(self):
        inv = [{"name": "Лист", "quantity": 3}]
        removed = remove_item(inv, "Лист", 3)
        assert removed == 3
        assert inv == []

    def test_remove_not_found_returns_0(self):
        inv = [{"name": "Рапіра", "quantity": 1}]
        removed = remove_item(inv, "Лист")
        assert removed == 0
        assert len(inv) == 1

    def test_remove_case_insensitive(self):
        inv = [{"name": "Лист", "quantity": 3}]
        removed = remove_item(inv, "лист", 1)
        assert removed == 1
        assert inv[0]["quantity"] == 2

    def test_remove_quantity_1_default(self):
        inv = [{"name": "Зілля", "quantity": 4}]
        removed = remove_item(inv, "Зілля")
        assert removed == 1
        assert inv[0]["quantity"] == 3

    def test_remove_entry_eliminated_at_zero(self):
        inv = [{"name": "Лист", "quantity": 1}]
        remove_item(inv, "Лист")
        assert inv == []

    def test_does_not_remove_other_items(self):
        inv = [
            {"name": "Рапіра", "quantity": 1},
            {"name": "Лист", "quantity": 3},
        ]
        remove_item(inv, "Лист", 3)
        assert inv == [{"name": "Рапіра", "quantity": 1}]


# ---------------------------------------------------------------------------
# apply_system_impacts integration
# ---------------------------------------------------------------------------

class TestApplySystemImpactsInventory:
    """Integration tests: apply_system_impacts processes inventory_new / inventory_lost
    via core.inventory helpers, with quantity tracking."""

    def _make_profile(self, inv_raw=None):
        return {
            "Інвентар": inv_raw if inv_raw is not None else [],
            "Здоров'я": 100,
            "Енергія": 1000,
            "Особисте Золото": 0,
        }

    def test_add_new_item(self):
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Меч")
        updates = {"inventory_new": ["Щит"]}
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        names = [i["name"] for i in items]
        assert "Меч" in names
        assert "Щит" in names

    def test_remove_item_basic(self):
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Меч, Яблуко")
        updates = {"inventory_lost": ["Яблуко"]}
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        names = [i["name"] for i in items]
        assert "Яблуко" not in names
        assert "Меч" in names

    def test_remove_case_insensitive(self):
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Меч, Яблуко")
        updates = {"inventory_lost": ["яблуко"]}
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        names = [i["name"] for i in items]
        assert "Яблуко" not in names

    def test_consume_partial_quantity(self):
        """User consumes 1 of 3 sealed letters — 2 remain."""
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Лист x3, Рапіра")
        updates = {"inventory_lost": ["Лист"]}  # default: remove 1
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        letter = next((i for i in items if i["name"] == "Лист"), None)
        assert letter is not None
        assert letter["quantity"] == 2

    def test_consume_all_of_quantity(self):
        """Consume all 3 letters — entry disappears."""
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Лист x3, Рапіра")
        updates = {"inventory_lost": ["Лист x3"]}
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        names = [i["name"] for i in items]
        assert "Лист" not in names
        assert "Рапіра" in names

    def test_add_item_with_quantity(self):
        """inventory_new includes quantity string → parsed correctly."""
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile([])
        updates = {
            "inventory_new": ["Стріла x10"],
            "gold_impact": "earn_small",  # provide transaction context to avoid hallucination log
        }
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        arrow = next((i for i in items if i["name"] == "Стріла"), None)
        assert arrow is not None
        assert arrow["quantity"] == 10

    def test_increment_existing_item_on_add(self):
        """Adding an existing item increments its quantity."""
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Зілля x2")
        updates = {
            "inventory_new": ["Зілля"],
            "gold_impact": "earn_small",
        }
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        potion = next((i for i in items if i["name"] == "Зілля"), None)
        assert potion is not None
        assert potion["quantity"] == 3

    def test_profile_stored_as_list_dict(self):
        """After apply_system_impacts, profile['Інвентар'] is list[dict]."""
        from core.mechanics import apply_system_impacts

        profile = self._make_profile("Меч, Лист x3")
        updates = {}
        updated, _ = apply_system_impacts(profile, updates)
        assert isinstance(updated["Інвентар"], list)
        assert all(isinstance(i, dict) for i in updated["Інвентар"])

    def test_backward_compat_csv_string_profile(self):
        """Existing profiles with CSV string inventory are migrated transparently."""
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        # Simulates a real existing Viserys-style profile
        profile = self._make_profile("Рапіра, Вишукані шати, Перстень з гербом, Запечатані рекомендаційні листи x3")
        updates = {"inventory_lost": ["Запечатані рекомендаційні листи"]}
        updated, logs = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        letter = next((i for i in items if i["name"] == "Запечатані рекомендаційні листи"), None)
        assert letter is not None
        assert letter["quantity"] == 2
        assert any("Втрачено" in log for log in logs)

    def test_empty_inventory_after_remove_last(self):
        """Removing the only item leaves an empty list (not 'Пусто' string)."""
        from core.mechanics import apply_system_impacts
        from core.inventory import parse_inventory

        profile = self._make_profile("Меч")
        updates = {"inventory_lost": ["Меч"]}
        updated, _ = apply_system_impacts(profile, updates)
        items = parse_inventory(updated["Інвентар"])
        assert items == []
