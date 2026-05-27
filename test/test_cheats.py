"""Unit tests for core/cheats.py D&D-aware admin commands.

Covers Issue 4 regression: /heal, /sethp, /addskill, /setskill, /setability.
Strategy: mock get_user_data / save_user_data / message.answer; no real Sheets calls.

Import isolation: database.sheets.GoogleSheetsDB creates a singleton at import time
and requires SPREADSHEET_ID env var.  We stub out database.sheets and
database.operations using a module-scoped autouse fixture with patch.dict as a
context manager, so the stubs are installed before cheats is imported and are
automatically removed when the fixture tears down (no global state leaks).
"""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-scoped autouse fixture: installs sys.modules stubs only for the
# duration of this test module and removes them automatically on teardown.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _stub_database_modules():
    """Replace database.sheets and database.operations with MagicMocks for the
    entire test module, then restore the originals (or remove if not present)
    after all tests finish.  Uses patch.dict as a context manager so teardown
    is guaranteed even if collection or an early test raises.
    """
    sheets_stub = MagicMock()
    sheets_stub.db = MagicMock()

    ops_stub = MagicMock()
    ops_stub.get_user_data = AsyncMock()
    ops_stub.save_user_data = AsyncMock(return_value=True)
    ops_stub.find_best_match = MagicMock(return_value=None)
    ops_stub.evict_npc_from_cache = MagicMock()
    ops_stub.refresh_npc_database = AsyncMock()

    stubs = {
        "database.sheets": sheets_stub,
        "database.operations": ops_stub,
    }

    with patch.dict(sys.modules, stubs):
        # Force (re)import of core.cheats while stubs are active so the module
        # resolves its top-level imports against our mocks.
        import core.cheats  # noqa: F401  (ensure cached version uses stubs)
        importlib.reload(core.cheats)
        yield  # all tests in this module run here

    # After the context manager exits, patch.dict automatically restores
    # sys.modules to its pre-fixture state — no manual .stop() needed.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(text: str) -> MagicMock:
    """Build a minimal message mock."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = 999
    msg.answer = AsyncMock()
    return msg


def _dnd_profile(**overrides) -> dict:
    """Minimal D&D profile with hp_current/hp_max."""
    base = {
        "Ім'я": "TestHero",
        "hp_current": 30,
        "hp_max": 50,
        "Здоров'я": 60,       # legacy UI field: round(100 * 30/50) = 60
        "Енергія": 1000,
        "Особисте Золото": 200,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "skills": {},
        "Інвентар": "Sword",
        "Поточне місцезнаходження": "Вінтерфелл",
        "Поточна сцена": "Great Hall",
        "Регіон": "Північ",
        "Ігровий час": "298 рік В.Е., 1-й місяць, День 1 (Ранок)",
    }
    base.update(overrides)
    return base


def _legacy_profile(**overrides) -> dict:
    """Minimal legacy profile (no hp_current)."""
    base = {
        "Ім'я": "LegacyHero",
        "Здоров'я": 70,
        "Енергія": 800,
        "Особисте Золото": 100,
        "Інвентар": "Dagger",
        "Поточне місцезнаходження": "Вінтерфелл",
        "Ігровий час": "298 рік В.Е., 1-й місяць, День 1 (Ранок)",
    }
    base.update(overrides)
    return base


def _run(coro):
    return asyncio.run(coro)


def _mock_get_save(profile: dict):
    """Return (get_patch, save_patch) that simulate DB read/write for one profile."""
    get_patch = patch(
        "core.cheats.get_user_data",
        new=AsyncMock(return_value=(profile, 1)),
    )
    save_patch = patch(
        "core.cheats.save_user_data",
        new=AsyncMock(return_value=True),
    )
    return get_patch, save_patch


# ---------------------------------------------------------------------------
# /heal
# ---------------------------------------------------------------------------

class TestCmdHeal:
    def test_heal_dnd_sets_hp_current_to_hp_max(self):
        """D&D profile: /heal must set hp_current = hp_max."""
        profile = _dnd_profile(hp_current=10, hp_max=50)
        msg = _make_message("/heal")
        get_p, save_p = _mock_get_save(profile)

        async def _run_heal():
            from core.cheats import cmd_heal
            await cmd_heal(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_run_heal())

        assert profile["hp_current"] == 50, (
            f"hp_current must be 50 (hp_max) after /heal. Got {profile['hp_current']}"
        )

    def test_heal_dnd_syncs_legacy_ui_field(self):
        """D&D profile: /heal must sync 'Здоров'я' to 100 (full health UI indicator)."""
        profile = _dnd_profile(hp_current=5, hp_max=40)
        msg = _make_message("/heal")
        get_p, save_p = _mock_get_save(profile)

        async def _run_heal():
            from core.cheats import cmd_heal
            await cmd_heal(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_run_heal())

        _hp_key = "Здоров'я"
        assert profile[_hp_key] == 100, (
            f"'Здоров'я' UI field must be 100 after full heal. Got {profile[_hp_key]}"
        )

    def test_heal_dnd_missing_hp_max_uses_fallback(self):
        """If hp_max is missing or 0, /heal falls back to 10 and warns the admin."""
        profile = _dnd_profile(hp_current=5)
        del profile["hp_max"]
        msg = _make_message("/heal")
        get_p, save_p = _mock_get_save(profile)

        async def _run_heal():
            from core.cheats import cmd_heal
            await cmd_heal(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_run_heal())

        # Must have used fallback hp_max=10
        assert profile.get("hp_max") == 10
        assert profile["hp_current"] == 10

    def test_heal_legacy_profile_sets_zdravo_to_100(self):
        """Legacy profile (no hp_current): /heal sets 'Здоров'я' = 100."""
        profile = _legacy_profile(Здоров=70)
        profile["Здоров'я"] = 70
        msg = _make_message("/heal")
        get_p, save_p = _mock_get_save(profile)

        async def _run_heal():
            from core.cheats import cmd_heal
            await cmd_heal(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_run_heal())

        assert profile["Здоров'я"] == 100

    def test_heal_does_not_write_to_zdravo_on_dnd_profile(self):
        """D&D profile: /heal must NOT set 'Здоров'я' = 100 via the legacy path;
        it must go through hp_current → legacy UI sync only."""
        profile = _dnd_profile(hp_current=10, hp_max=40)
        profile["Здоров'я"] = 25  # set a non-trivial initial value
        msg = _make_message("/heal")
        get_p, save_p = _mock_get_save(profile)

        async def _run_heal():
            from core.cheats import cmd_heal
            await cmd_heal(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_run_heal())

        # After full heal hp_current=40, hp_max=40 → 'Здоров'я' = 100
        assert profile["hp_current"] == 40
        assert profile["Здоров'я"] == 100  # synced from hp ratio


# ---------------------------------------------------------------------------
# /sethp
# ---------------------------------------------------------------------------

class TestCmdSetHP:
    def test_sethp_writes_to_dnd_hp_current(self):
        """/sethp 25 on D&D profile must set hp_current=25, not 'Здоров'я'=25."""
        profile = _dnd_profile(hp_current=30, hp_max=50)
        msg = _make_message("/sethp 25")
        get_p, save_p = _mock_get_save(profile)

        async def _run_sethp():
            from core.cheats import cmd_sethp
            await cmd_sethp(msg, MagicMock(), 42, ["25"])

        with get_p, save_p:
            _run(_run_sethp())

        assert profile["hp_current"] == 25, (
            f"hp_current must be 25. Got {profile['hp_current']}"
        )

    def test_sethp_syncs_legacy_ui_zdravo_field(self):
        """/sethp must sync 'Здоров'я' = round(100 * hp_current / hp_max)."""
        profile = _dnd_profile(hp_current=30, hp_max=50)
        msg = _make_message("/sethp 25")
        get_p, save_p = _mock_get_save(profile)

        async def _run_sethp():
            from core.cheats import cmd_sethp
            await cmd_sethp(msg, MagicMock(), 42, ["25"])

        with get_p, save_p:
            _run(_run_sethp())

        _hp_key = "Здоров'я"
        expected_ui = round(100 * 25 / 50)  # 50
        assert profile[_hp_key] == expected_ui, (
            f"'Здоров'я' UI must be {expected_ui}. Got {profile[_hp_key]}"
        )

    def test_sethp_clamps_to_hp_max(self):
        """/sethp 999 on profile with hp_max=50 → hp_current clamped to 50."""
        profile = _dnd_profile(hp_current=30, hp_max=50)
        msg = _make_message("/sethp 999")
        get_p, save_p = _mock_get_save(profile)

        async def _run_sethp():
            from core.cheats import cmd_sethp
            await cmd_sethp(msg, MagicMock(), 42, ["999"])

        with get_p, save_p:
            _run(_run_sethp())

        assert profile["hp_current"] == 50, (
            f"hp_current must be clamped to hp_max=50. Got {profile['hp_current']}"
        )

    def test_sethp_clamps_to_zero(self):
        """/sethp -10 → hp_current clamped to 0."""
        profile = _dnd_profile(hp_current=30, hp_max=50)
        msg = _make_message("/sethp -10")
        get_p, save_p = _mock_get_save(profile)

        async def _run_sethp():
            from core.cheats import cmd_sethp
            await cmd_sethp(msg, MagicMock(), 42, ["-10"])

        with get_p, save_p:
            _run(_run_sethp())

        assert profile["hp_current"] == 0, (
            f"hp_current must be clamped to 0. Got {profile['hp_current']}"
        )

    def test_sethp_legacy_profile_writes_zdravo(self):
        """/sethp on legacy profile (no hp_current) writes to 'Здоров'я' clamped to [0,100]."""
        profile = _legacy_profile()
        profile["Здоров'я"] = 80
        msg = _make_message("/sethp 50")
        get_p, save_p = _mock_get_save(profile)

        async def _run_sethp():
            from core.cheats import cmd_sethp
            await cmd_sethp(msg, MagicMock(), 42, ["50"])

        with get_p, save_p:
            _run(_run_sethp())

        assert profile["Здоров'я"] == 50
        assert "hp_current" not in profile, "Legacy profile must not gain hp_current key"

    def test_sethp_rejects_non_numeric_input(self):
        """/sethp abc → error message, profile unchanged."""
        profile = _dnd_profile(hp_current=30, hp_max=50)
        msg = _make_message("/sethp abc")
        get_p, save_p = _mock_get_save(profile)

        async def _run_sethp():
            from core.cheats import cmd_sethp
            await cmd_sethp(msg, MagicMock(), 42, ["abc"])

        with get_p, save_p:
            _run(_run_sethp())

        assert profile["hp_current"] == 30, "HP must be unchanged on invalid input"
        msg.answer.assert_called()  # error message was sent


# ---------------------------------------------------------------------------
# /setenergy
# ---------------------------------------------------------------------------

class TestCmdSetEnergy:
    def test_setenergy_uses_0_1000_scale(self):
        """/setenergy 500 → 'Енергія' = 500 (not clamped to 100 as in old code)."""
        profile = _dnd_profile(Енергія=1000)
        profile["Енергія"] = 1000
        msg = _make_message("/setenergy 500")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setenergy():
            from core.cheats import cmd_setenergy
            await cmd_setenergy(msg, MagicMock(), 42, ["500"])

        with get_p, save_p:
            _run(_run_setenergy())

        assert profile["Енергія"] == 500

    def test_setenergy_clamps_at_1000(self):
        """/setenergy 9999 → 'Енергія' = 1000 (cap)."""
        profile = _dnd_profile()
        profile["Енергія"] = 200
        msg = _make_message("/setenergy 9999")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setenergy():
            from core.cheats import cmd_setenergy
            await cmd_setenergy(msg, MagicMock(), 42, ["9999"])

        with get_p, save_p:
            _run(_run_setenergy())

        assert profile["Енергія"] == 1000, (
            f"Energy must be capped at 1000. Got {profile['Енергія']}"
        )


# ---------------------------------------------------------------------------
# /addskill
# ---------------------------------------------------------------------------

class TestCmdAddSkill:
    def test_addskill_valid_skill_updates_profile(self):
        """/addskill Athletics 10 → profile['skills']['Athletics'] = 10."""
        profile = _dnd_profile()
        msg = _make_message("/addskill Athletics 10")
        get_p, save_p = _mock_get_save(profile)

        async def _run_addskill():
            from core.cheats import cmd_addskill
            await cmd_addskill(msg, MagicMock(), 42, ["Athletics", "10"])

        with get_p, save_p:
            _run(_run_addskill())

        assert profile.get("skills", {}).get("Athletics") == 10

    def test_addskill_case_insensitive_match(self):
        """/addskill athletics 5 (lowercase) → resolved to 'Athletics'."""
        profile = _dnd_profile()
        msg = _make_message("/addskill athletics 5")
        get_p, save_p = _mock_get_save(profile)

        async def _run_addskill():
            from core.cheats import cmd_addskill
            await cmd_addskill(msg, MagicMock(), 42, ["athletics", "5"])

        with get_p, save_p:
            _run(_run_addskill())

        assert profile.get("skills", {}).get("Athletics") == 5

    def test_addskill_rejects_unknown_skill(self):
        """/addskill FlyingMagic 10 → error, profile unchanged."""
        profile = _dnd_profile()
        msg = _make_message("/addskill FlyingMagic 10")
        get_p, save_p = _mock_get_save(profile)

        async def _run_addskill():
            from core.cheats import cmd_addskill
            await cmd_addskill(msg, MagicMock(), 42, ["FlyingMagic", "10"])

        with get_p, save_p:
            _run(_run_addskill())

        # Profile skills must be unchanged (key not added)
        assert "FlyingMagic" not in profile.get("skills", {}), (
            "Unknown skill must not be written to profile"
        )
        # Error message was sent
        answer_calls = msg.answer.call_args_list
        assert any("Невідома" in str(c) or "D&D" in str(c) for c in answer_calls), (
            f"Error message must mention unknown skill. Calls: {answer_calls}"
        )

    def test_addskill_clamps_to_100(self):
        """/addskill Stealth 999 from base 0 → clamped to 100."""
        profile = _dnd_profile()
        msg = _make_message("/addskill Stealth 999")
        get_p, save_p = _mock_get_save(profile)

        async def _run_addskill():
            from core.cheats import cmd_addskill
            await cmd_addskill(msg, MagicMock(), 42, ["Stealth", "999"])

        with get_p, save_p:
            _run(_run_addskill())

        assert profile["skills"]["Stealth"] == 100


# ---------------------------------------------------------------------------
# /setskill
# ---------------------------------------------------------------------------

class TestCmdSetSkill:
    def test_setskill_valid_skill_sets_absolute_value(self):
        """/setskill Perception 75 → profile['skills']['Perception'] = 75."""
        profile = _dnd_profile()
        msg = _make_message("/setskill Perception 75")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setskill():
            from core.cheats import cmd_setskill
            await cmd_setskill(msg, MagicMock(), 42, ["Perception", "75"])

        with get_p, save_p:
            _run(_run_setskill())

        assert profile["skills"]["Perception"] == 75

    def test_setskill_rejects_unknown_skill(self):
        """/setskill Cooking 50 → error (not a D&D 5e skill)."""
        profile = _dnd_profile()
        msg = _make_message("/setskill Cooking 50")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setskill():
            from core.cheats import cmd_setskill
            await cmd_setskill(msg, MagicMock(), 42, ["Cooking", "50"])

        with get_p, save_p:
            _run(_run_setskill())

        assert "Cooking" not in profile.get("skills", {}), (
            "Unknown skill must not be written"
        )

    def test_setskill_clamps_to_0_100(self):
        """/setskill History 150 → clamped to 100."""
        profile = _dnd_profile()
        msg = _make_message("/setskill History 150")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setskill():
            from core.cheats import cmd_setskill
            await cmd_setskill(msg, MagicMock(), 42, ["History", "150"])

        with get_p, save_p:
            _run(_run_setskill())

        assert profile["skills"]["History"] == 100


# ---------------------------------------------------------------------------
# /setability
# ---------------------------------------------------------------------------

class TestCmdSetAbility:
    def test_setability_valid_str_score(self):
        """/setability STR 18 → profile['ability_scores']['STR'] = 18."""
        profile = _dnd_profile()
        msg = _make_message("/setability STR 18")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setability():
            from core.cheats import cmd_setability
            await cmd_setability(msg, MagicMock(), 42, ["STR", "18"])

        with get_p, save_p:
            _run(_run_setability())

        assert profile["ability_scores"]["STR"] == 18

    def test_setability_case_insensitive_ability(self):
        """/setability str 14 (lowercase) → 'STR' written correctly."""
        profile = _dnd_profile()
        msg = _make_message("/setability str 14")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setability():
            from core.cheats import cmd_setability
            await cmd_setability(msg, MagicMock(), 42, ["str", "14"])

        with get_p, save_p:
            _run(_run_setability())

        assert profile["ability_scores"]["STR"] == 14

    def test_setability_rejects_invalid_ability_name(self):
        """/setability LUCK 15 → error (not a valid D&D ability)."""
        profile = _dnd_profile()
        msg = _make_message("/setability LUCK 15")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setability():
            from core.cheats import cmd_setability
            await cmd_setability(msg, MagicMock(), 42, ["LUCK", "15"])

        with get_p, save_p:
            _run(_run_setability())

        assert "LUCK" not in profile.get("ability_scores", {}), (
            "Invalid ability must not be written to profile"
        )

    def test_setability_validates_range_min(self):
        """/setability DEX 0 → rejected (below 1)."""
        profile = _dnd_profile()
        msg = _make_message("/setability DEX 0")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setability():
            from core.cheats import cmd_setability
            await cmd_setability(msg, MagicMock(), 42, ["DEX", "0"])

        with get_p, save_p:
            _run(_run_setability())

        # DEX must not have been changed to 0 (original was 12)
        assert profile["ability_scores"]["DEX"] != 0, (
            "Value 0 is below range [1,20] and must be rejected"
        )

    def test_setability_validates_range_max(self):
        """/setability CON 21 → rejected (above 20)."""
        profile = _dnd_profile()
        original_con = profile["ability_scores"]["CON"]
        msg = _make_message("/setability CON 21")
        get_p, save_p = _mock_get_save(profile)

        async def _run_setability():
            from core.cheats import cmd_setability
            await cmd_setability(msg, MagicMock(), 42, ["CON", "21"])

        with get_p, save_p:
            _run(_run_setability())

        assert profile["ability_scores"]["CON"] == original_con, (
            "Value 21 is above range [1,20] and must be rejected"
        )

    def test_setability_accepts_boundary_values(self):
        """/setability WIS 1 and /setability WIS 20 are both valid (boundary cases)."""
        profile = _dnd_profile()
        get_p, save_p = _mock_get_save(profile)

        # Test lower bound: 1
        msg = _make_message("/setability WIS 1")
        async def _run_min():
            from core.cheats import cmd_setability
            await cmd_setability(msg, MagicMock(), 42, ["WIS", "1"])
        with get_p, save_p:
            _run(_run_min())
        assert profile["ability_scores"]["WIS"] == 1

        # Test upper bound: 20
        msg2 = _make_message("/setability WIS 20")
        async def _run_max():
            from core.cheats import cmd_setability
            await cmd_setability(msg2, MagicMock(), 42, ["WIS", "20"])
        with get_p, save_p:
            _run(_run_max())
        assert profile["ability_scores"]["WIS"] == 20


# ---------------------------------------------------------------------------
# /additem  (structured inventory)
# ---------------------------------------------------------------------------

class TestCmdAddItem:
    def test_additem_appends_new_item_to_structured_inventory(self):
        """/additem Зілля on structured inventory → new entry added."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Рапіра", "quantity": 1}]})
        msg = _make_message("/additem Зілля")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_additem
            await cmd_additem(msg, MagicMock(), 42, ["Зілля"])

        with get_p, save_p:
            _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        names = [i["name"] for i in inv]
        assert "Зілля" in names, f"Expected 'Зілля' in inventory. Got: {names}"
        assert "Рапіра" in names, "Pre-existing item must not be removed"

    def test_additem_with_quantity_parses_x_format(self):
        """/additem Лист x3 → item with quantity=3 added."""
        profile = _dnd_profile(**{"Інвентар": []})
        msg = _make_message("/additem Лист x3")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_additem
            await cmd_additem(msg, MagicMock(), 42, ["Лист", "x3"])

        with get_p, save_p:
            _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        letter = next((i for i in inv if i["name"] == "Лист"), None)
        assert letter is not None, "Item 'Лист' must be present after /additem 'Лист x3'"
        assert letter["quantity"] == 3, f"Expected quantity=3. Got: {letter['quantity']}"

    def test_additem_increments_existing_item(self):
        """/additem Лист x3 when 'Лист x2' already exists → quantity becomes 5."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Лист", "quantity": 2}]})
        msg = _make_message("/additem Лист x3")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_additem
            await cmd_additem(msg, MagicMock(), 42, ["Лист", "x3"])

        with get_p, save_p:
            _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        letter = next((i for i in inv if i["name"] == "Лист"), None)
        assert letter is not None
        assert letter["quantity"] == 5, f"Expected quantity=5 (2+3). Got: {letter['quantity']}"

    def test_additem_on_legacy_string_inventory_migrates(self):
        """/additem on old CSV-string profile auto-migrates and appends correctly."""
        profile = _dnd_profile(**{"Інвентар": "Меч, Щит"})
        msg = _make_message("/additem Зілля")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_additem
            await cmd_additem(msg, MagicMock(), 42, ["Зілля"])

        with get_p, save_p:
            _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        names = [i["name"] for i in inv]
        assert "Зілля" in names
        assert "Меч" in names
        assert "Щит" in names

    def test_additem_profile_stored_as_list_dict(self):
        """/additem must leave profile['Інвентар'] as list[dict], not string."""
        profile = _dnd_profile(**{"Інвентар": []})
        msg = _make_message("/additem Зілля")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_additem
            await cmd_additem(msg, MagicMock(), 42, ["Зілля"])

        with get_p, save_p:
            _run(_coro())

        assert isinstance(profile["Інвентар"], list), (
            f"profile['Інвентар'] must be list, got {type(profile['Інвентар'])}"
        )
        assert all(isinstance(i, dict) for i in profile["Інвентар"])


# ---------------------------------------------------------------------------
# /delitem  (structured inventory)
# ---------------------------------------------------------------------------

class TestCmdDelItem:
    def test_delitem_removes_one_by_default(self):
        """/delitem Лист when quantity=3 → quantity becomes 2."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Лист", "quantity": 3}]})
        msg = _make_message("/delitem Лист")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_delitem
            await cmd_delitem(msg, MagicMock(), 42, ["Лист"])

        with patch("core.cheats.find_best_match", return_value="Лист"):
            with get_p, save_p:
                _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        letter = next((i for i in inv if i["name"] == "Лист"), None)
        assert letter is not None, "Item must still exist (qty 3→2)"
        assert letter["quantity"] == 2, f"Expected quantity=2. Got: {letter['quantity']}"

    def test_delitem_with_quantity_removes_specified_amount(self):
        """/delitem Лист x2 when quantity=3 → quantity becomes 1."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Лист", "quantity": 3}]})
        msg = _make_message("/delitem Лист x2")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_delitem
            await cmd_delitem(msg, MagicMock(), 42, ["Лист", "x2"])

        with patch("core.cheats.find_best_match", return_value="Лист"):
            with get_p, save_p:
                _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        letter = next((i for i in inv if i["name"] == "Лист"), None)
        assert letter is not None
        assert letter["quantity"] == 1

    def test_delitem_removes_entry_when_quantity_reaches_zero(self):
        """/delitem Лист when quantity=1 → entry fully removed."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Лист", "quantity": 1}]})
        msg = _make_message("/delitem Лист")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_delitem
            await cmd_delitem(msg, MagicMock(), 42, ["Лист"])

        with patch("core.cheats.find_best_match", return_value="Лист"):
            with get_p, save_p:
                _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        names = [i["name"] for i in inv]
        assert "Лист" not in names

    def test_delitem_not_found_reports_error(self):
        """/delitem when item absent → error message, inventory unchanged."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Рапіра", "quantity": 1}]})
        msg = _make_message("/delitem Зілля")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_delitem
            await cmd_delitem(msg, MagicMock(), 42, ["Зілля"])

        # find_best_match returns None → "not found" path
        with patch("core.cheats.find_best_match", return_value=None):
            with get_p, save_p:
                _run(_coro())

        from core.inventory import parse_inventory
        inv = parse_inventory(profile["Інвентар"])
        assert len(inv) == 1, "Inventory must be unchanged when item not found"
        msg.answer.assert_called()
        assert any("не знайдено" in str(c) for c in msg.answer.call_args_list)

    def test_delitem_profile_stored_as_list_dict(self):
        """/delitem must leave profile['Інвентар'] as list[dict]."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Лист", "quantity": 3}]})
        msg = _make_message("/delitem Лист")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_delitem
            await cmd_delitem(msg, MagicMock(), 42, ["Лист"])

        with patch("core.cheats.find_best_match", return_value="Лист"):
            with get_p, save_p:
                _run(_coro())

        assert isinstance(profile["Інвентар"], list)


# ---------------------------------------------------------------------------
# /clearinv  (structured inventory)
# ---------------------------------------------------------------------------

class TestCmdClearInv:
    def test_clearinv_resets_to_empty_list(self):
        """/clearinv must set profile['Інвентар'] = [] (not empty string)."""
        profile = _dnd_profile(**{"Інвентар": [{"name": "Рапіра", "quantity": 1}]})
        msg = _make_message("/clearinv")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_clearinv
            await cmd_clearinv(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_coro())

        assert profile["Інвентар"] == [], (
            f"Expected [], got {profile['Інвентар']!r}"
        )

    def test_clearinv_result_is_list_not_string(self):
        """/clearinv must not leave a string value (regression vs old '' pattern)."""
        profile = _dnd_profile(**{"Інвентар": "Меч, Щит"})
        msg = _make_message("/clearinv")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_clearinv
            await cmd_clearinv(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_coro())

        assert not isinstance(profile["Інвентар"], str), (
            "profile['Інвентар'] must be list after /clearinv, not string"
        )
        assert profile["Інвентар"] == []

    def test_clearinv_on_already_empty_inventory(self):
        """/clearinv on empty inventory is idempotent."""
        profile = _dnd_profile(**{"Інвентар": []})
        msg = _make_message("/clearinv")
        get_p, save_p = _mock_get_save(profile)

        async def _coro():
            from core.cheats import cmd_clearinv
            await cmd_clearinv(msg, MagicMock(), 42, [])

        with get_p, save_p:
            _run(_coro())

        assert profile["Інвентар"] == []


# ---------------------------------------------------------------------------
# /debugmode  (admin-only pipeline trace toggle)
# ---------------------------------------------------------------------------

class TestCmdDebugMode:
    """Tests for cmd_debugmode: admin-only toggle for full pipeline trace."""

    def setup_method(self):
        """Ensure DEBUG_USERS is clean before each test."""
        import importlib
        import core.cheats as _cheats
        _cheats.DEBUG_USERS.clear()

    def teardown_method(self):
        """Clean up DEBUG_USERS after each test."""
        import core.cheats as _cheats
        _cheats.DEBUG_USERS.clear()

    def test_non_admin_receives_refusal(self):
        """Non-admin chat_id must receive refusal and NOT be added to DEBUG_USERS."""
        non_admin_id = 99999999
        msg = _make_message("/debugmode")

        async def _coro():
            from core.cheats import cmd_debugmode
            await cmd_debugmode(msg, MagicMock(), non_admin_id, [])

        with patch("core.cheats.ADMIN_TELEGRAM_IDS", [494157543, 778186089]):
            _run(_coro())

        import core.cheats as _cheats
        assert non_admin_id not in _cheats.DEBUG_USERS, (
            "Non-admin must not be added to DEBUG_USERS"
        )
        msg.answer.assert_called_once()
        call_text = str(msg.answer.call_args_list[0])
        assert "адмін" in call_text.lower() or "admin" in call_text.lower() or "лише" in call_text.lower(), (
            f"Refusal message must mention admin restriction. Got: {call_text}"
        )

    def test_admin_toggle_on_adds_to_debug_users(self):
        """Admin /debugmode when OFF must add chat_id to DEBUG_USERS."""
        admin_id = 494157543
        msg = _make_message("/debugmode")

        async def _coro():
            from core.cheats import cmd_debugmode
            await cmd_debugmode(msg, MagicMock(), admin_id, [])

        with patch("core.cheats.ADMIN_TELEGRAM_IDS", [admin_id]):
            _run(_coro())

        import core.cheats as _cheats
        assert admin_id in _cheats.DEBUG_USERS, (
            "Admin chat_id must be in DEBUG_USERS after toggle-on"
        )

    def test_admin_toggle_off_removes_from_debug_users(self):
        """Admin /debugmode when already ON must remove chat_id from DEBUG_USERS."""
        admin_id = 494157543

        import core.cheats as _cheats
        _cheats.DEBUG_USERS.add(admin_id)  # pre-populate: already ON

        msg = _make_message("/debugmode")

        async def _coro():
            from core.cheats import cmd_debugmode
            await cmd_debugmode(msg, MagicMock(), admin_id, [])

        with patch("core.cheats.ADMIN_TELEGRAM_IDS", [admin_id]):
            _run(_coro())

        assert admin_id not in _cheats.DEBUG_USERS, (
            "Admin chat_id must be removed from DEBUG_USERS after toggle-off"
        )

    def test_toggle_on_sends_confirmation_message(self):
        """Toggle-on must send a confirmation message describing what will be traced."""
        admin_id = 494157543
        msg = _make_message("/debugmode")

        async def _coro():
            from core.cheats import cmd_debugmode
            await cmd_debugmode(msg, MagicMock(), admin_id, [])

        with patch("core.cheats.ADMIN_TELEGRAM_IDS", [admin_id]):
            _run(_coro())

        msg.answer.assert_called_once()
        # Message must mention debug mode and pipeline
        call_text = str(msg.answer.call_args_list[0]).lower()
        assert "debug" in call_text or "увімкнено" in call_text, (
            f"Toggle-on message must confirm activation. Got: {call_text}"
        )

    def test_toggle_off_sends_deactivation_message(self):
        """Toggle-off must send a deactivation confirmation message."""
        admin_id = 494157543

        import core.cheats as _cheats
        _cheats.DEBUG_USERS.add(admin_id)

        msg = _make_message("/debugmode")

        async def _coro():
            from core.cheats import cmd_debugmode
            await cmd_debugmode(msg, MagicMock(), admin_id, [])

        with patch("core.cheats.ADMIN_TELEGRAM_IDS", [admin_id]):
            _run(_coro())

        msg.answer.assert_called_once()
        call_text = str(msg.answer.call_args_list[0]).lower()
        assert "вимкнено" in call_text or "debug" in call_text, (
            f"Toggle-off message must confirm deactivation. Got: {call_text}"
        )
