"""Tests for core/dnd_engine.py:recompute_hp and its two call sites.

Sections:
  A. Direct unit tests on recompute_hp
  B. Integration via apply_asi (dnd_progression.py)
  C. Cheat command via cmd_setability (cheats.py)
  D. Regression — no spurious mutations

Assumptions (fixture):
  - Minimal profile contains: class, level, ability_scores (with CON),
    hp_max, hp_current keys.  No gspread / Gemini calls are required.
  - hit_die_avg formula used in assertions: hit_die // 2 + 1
    e.g. d10 avg=6, d6 avg=4, d8 avg=5.
  - hp_max formula: hit_die + CON_mod + (level-1) * (hit_die_avg + CON_mod)
    (simplified from PHB: L1 takes max; each subsequent level uses average).

Class hit_die reference (from core/dnd_classes.py):
  Knight=10, Maester=6, Septon=8, Sellsword=10.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.dnd_engine import recompute_hp
from core.dnd_core import ability_modifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile(
    class_name: str,
    level: int,
    con: int,
    hp_max: int = None,
    hp_current: int = None,
) -> dict:
    """Build a minimal profile for recompute_hp testing.

    If hp_max is None it is set to a placeholder of 0 (triggers recompute).
    If hp_current is None it is set to hp_max (full health).
    """
    _hp_max = hp_max if hp_max is not None else 0
    _hp_current = hp_current if hp_current is not None else _hp_max
    return {
        "class": class_name,
        "level": level,
        "ability_scores": {"STR": 10, "DEX": 10, "CON": con, "INT": 10, "WIS": 10, "CHA": 10},
        "hp_max": _hp_max,
        "hp_current": _hp_current,
        "Здоров'я": 100,
    }


def _expected_hp_max(hit_die: int, level: int, con: int) -> int:
    """Compute expected hp_max using the formula documented in recompute_hp."""
    con_mod = ability_modifier(con)
    hit_die_avg = (hit_die // 2) + 1
    return max(1, hit_die + con_mod + (level - 1) * (hit_die_avg + con_mod))


# ---------------------------------------------------------------------------
# Section A: Direct unit tests on recompute_hp
# ---------------------------------------------------------------------------

class TestRecomputeHpDirect:

    def test_A1_knight_l1_con14(self):
        """L1 Knight (d10), CON 14 (mod +2): hp_max = 10 + 2 = 12."""
        p = _profile("Knight", 1, 14, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == 12

    def test_A2_knight_l1_con8(self):
        """L1 Knight (d10), CON 8 (mod -1): hp_max = 10 - 1 = 9."""
        p = _profile("Knight", 1, 8, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == 9

    def test_A3_knight_l1_con1(self):
        """L1 Knight (d10), CON 1 (mod -5): hp_max = 10 - 5 = 5 (floor not triggered)."""
        p = _profile("Knight", 1, 1, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == 5

    def test_A4_maester_l1_con3(self):
        """L1 Maester (d6), CON 3 (mod -4): hp_max = max(1, 6 - 4) = 2."""
        con = 3
        con_mod = ability_modifier(con)  # -4
        expected = max(1, 6 + con_mod)
        p = _profile("Maester", 1, con, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == expected

    def test_A5_septon_l1_con1_floor(self):
        """L1 Septon (d8), CON 1 (mod -5): hp_max = max(1, 8 - 5) = 3.

        Note: test plan mentioned d6/Septon — Septon is actually d8 per dnd_classes.py.
        Floor of 1 only triggers when result would be 0 or negative.
        """
        # Septon d8, CON 1 (mod -5): 8 + (-5) = 3, above floor
        p = _profile("Septon", 1, 1, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == 3
        assert p["hp_max"] >= 1

    def test_A5b_floor_via_extreme_con(self):
        """Floor of 1: even with a deeply negative CON the hp_max cannot drop below 1.

        We use an unknown class (hit_die=8 fallback) with CON 1 (mod -5).
        Since the class has no entry, fallback hit_die=8. hp_max=max(1, 8-5)=3.
        To actually hit floor we need a class where hit_die + con_mod <= 0.
        We patch the class lookup to inject a custom hit_die of 4 and use CON 1:
        max(1, 4 + (-5)) = max(1, -1) = 1.
        """
        from core.dnd_classes import ClassDef
        fake_cls = ClassDef(
            name="TinyCls",
            hit_die=4,
            primary_abilities=["STR"],
            saves_proficient=["STR"],
            armor_profs=[],
            weapon_profs=[],
            skill_choices=1,
            skill_pool=["Athletics"],
            level_features={i: [] for i in range(1, 21)},
            starting_equipment={},
            description="Test class",
        )
        p = {
            "class": "TinyCls",
            "level": 1,
            "ability_scores": {"CON": 1},
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
        }
        with patch("core.dnd_classes.GOT_CLASSES", {"TinyCls": fake_cls}):
            recompute_hp(p)
        assert p["hp_max"] == 1

    def test_A6_sellsword_l5_con14(self):
        """L5 Sellsword (d10), CON 14 (mod +2): hp_max = 10 + 4*6 + 5*2 = 44."""
        expected = _expected_hp_max(10, 5, 14)  # 10 + 2 + 4*(6+2) = 10+2+32 = 44
        p = _profile("Sellsword", 5, 14, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == expected
        assert p["hp_max"] == 44

    def test_A7_maester_l10_con16(self):
        """L10 Maester (d6), CON 16 (mod +3): hp_max = 6 + 9*4 + 10*3 = 72."""
        expected = _expected_hp_max(6, 10, 16)
        p = _profile("Maester", 10, 16, hp_max=0)
        recompute_hp(p)
        assert p["hp_max"] == expected
        assert p["hp_max"] == 72

    def test_A8_ratio_preserved_on_increase(self):
        """hp_current ratio preserved when hp_max increases (CON bump 14→16).

        L1 Knight: old hp_max=10, hp_current=6 (60%).
        New hp_max (CON 16, mod +3) = 10 + 0 + 1*3 = 13.
        New hp_current = round(13 * 0.6) = 8.
        """
        p = _profile("Knight", 1, 16, hp_max=10, hp_current=6)
        # Force recompute by setting hp_max to something that doesn't match
        # Expected new value for CON 16 (mod +3): 10 + 3 = 13
        # But profile has hp_max=10 ≠ 13, so recompute runs
        recompute_hp(p)
        assert p["hp_max"] == 13
        assert p["hp_current"] == round(13 * 0.6)  # 8

    def test_A9_ratio_preserved_on_decrease(self):
        """hp_current ratio preserved when hp_max decreases (CON 16→14).

        L1 Knight, CON 14 (mod +2): new hp_max = 10 + 2 = 12.
        Old: hp_max=20 (stale), hp_current=10 (50%).
        New hp_current = round(12 * 0.5) = 6.
        """
        # Profile has stale hp_max=20 but correct CON=14
        p = _profile("Knight", 1, 14, hp_max=20, hp_current=10)
        recompute_hp(p)
        assert p["hp_max"] == 12
        assert p["hp_current"] == round(12 * 0.5)  # 6

    def test_A10_noop_when_already_correct(self):
        """No mutation when hp_max already equals computed value."""
        # L1 Knight, CON 14 → hp_max=12
        p = _profile("Knight", 1, 14, hp_max=12, hp_current=9)
        old_hp_max = p["hp_max"]
        old_hp_current = p["hp_current"]
        recompute_hp(p)
        # First call: no-op (already correct)
        assert p["hp_max"] == old_hp_max
        assert p["hp_current"] == old_hp_current
        # Second call: still no-op
        recompute_hp(p)
        assert p["hp_max"] == old_hp_max
        assert p["hp_current"] == old_hp_current

    def test_A11_legacy_health_sync(self):
        """After recompute, profile['Здоров'я'] == round(100 * hp_current / hp_max)."""
        p = _profile("Knight", 1, 14, hp_max=0, hp_current=0)
        recompute_hp(p)
        expected_pct = round(100 * p["hp_current"] / p["hp_max"]) if p["hp_max"] > 0 else 0
        assert p["Здоров'я"] == expected_pct

    def test_A11_legacy_health_sync_partial(self):
        """Legacy sync works when hp_current is partial (damaged profile)."""
        # L5 Sellsword CON 14: hp_max=44, set hp_current to stale value that triggers ratio
        # Use hp_max=30 (stale) with hp_current=15 (50%)
        p = _profile("Sellsword", 5, 14, hp_max=30, hp_current=15)
        recompute_hp(p)
        new_pct = round(100 * p["hp_current"] / p["hp_max"])
        assert p["Здоров'я"] == new_pct

    def test_A12_old_hp_max_zero_no_crash(self):
        """old_hp_max == 0 (corrupt profile) does not raise; hp_current set to new_hp_max."""
        p = _profile("Knight", 1, 14, hp_max=0, hp_current=0)
        # Should not raise
        recompute_hp(p)
        assert p["hp_max"] >= 1
        # When old_hp_max was 0, hp_current is set to new_hp_max (full health assumed)
        assert p["hp_current"] == p["hp_max"]

    def test_A13_unknown_class_fallback(self):
        """Unknown class uses fallback hit_die=8; no exception raised."""
        p = {
            "class": "DragonRider",
            "level": 1,
            "ability_scores": {"CON": 10},
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
        }
        # Should not raise
        recompute_hp(p)
        assert p["hp_max"] >= 1
        # With hit_die=8 fallback and CON 10 (mod 0): hp_max = 8
        assert p["hp_max"] == 8

    def test_A13_unknown_class_fallback_with_con_bonus(self):
        """Unknown class (hit_die=8 fallback), CON 14 (mod +2): hp_max = 8 + 2 = 10."""
        p = {
            "class": "UnknownClass",
            "level": 1,
            "ability_scores": {"CON": 14},
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
        }
        recompute_hp(p)
        assert p["hp_max"] == 10


# ---------------------------------------------------------------------------
# Parametrized: formula correctness across classes and levels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("class_name,hit_die,level,con,expected_hp_max", [
    # L1 cases
    ("Knight",    10, 1, 10, 10),   # 10 + 0 + 0*6 = 10
    ("Knight",    10, 1, 14, 12),   # 10 + 2 = 12
    ("Knight",    10, 1, 8,   9),   # 10 - 1 = 9
    ("Maester",    6, 1, 10,  6),   # 6 + 0 = 6
    ("Maester",    6, 1, 16,  9),   # 6 + 3 = 9
    ("Septon",     8, 1, 10,  8),   # 8 + 0 = 8
    ("Sellsword", 10, 1, 10, 10),   # 10 + 0 = 10
    # Multi-level cases (formula: hit_die + con_mod + (L-1)*(hit_die_avg + con_mod))
    ("Knight",    10, 5, 14, 44),   # 10+2 + 4*(6+2) = 12+32 = 44
    ("Maester",   6, 10, 16, 72),   # 6+3 + 9*(4+3) = 9+63 = 72
    ("Sellsword", 10, 3, 12, 25),   # 10+1 + 2*(6+1) = 11+14 = 25
])
def test_hp_max_formula_parametrized(class_name, hit_die, level, con, expected_hp_max):
    """Parametrized verification of the hp_max formula across classes and levels."""
    p = _profile(class_name, level, con, hp_max=0)
    recompute_hp(p)
    assert p["hp_max"] == expected_hp_max, (
        f"{class_name} L{level} CON {con}: expected {expected_hp_max}, got {p['hp_max']}"
    )


# ---------------------------------------------------------------------------
# Section B: Integration via apply_asi
# ---------------------------------------------------------------------------

class TestApplyAsiTriggersRecomputeHp:

    def _asi_profile(
        self,
        class_name="Knight",
        level=4,
        con=14,
        hp_max=30,
        hp_current=20,
        extra_abilities: dict = None,
    ) -> dict:
        """Knight L4 profile with hp_max set explicitly for ASI tests."""
        ab = {"STR": 15, "DEX": 12, "CON": con, "INT": 10, "WIS": 10, "CHA": 14}
        if extra_abilities:
            ab.update(extra_abilities)
        return {
            "class": class_name,
            "level": level,
            "ability_scores": ab,
            "hp_max": hp_max,
            "hp_current": hp_current,
            "Здоров'я": round(100 * hp_current / hp_max) if hp_max > 0 else 0,
            "asi_pending": True,
            "equipped_armor": None,
            "features": [],
        }

    def test_B14_con_asi_triggers_hp_recompute(self):
        """CON ASI (+2) triggers recompute_hp; hp_max becomes average-formula value.

        L4 Knight, CON 14 → 16 (mod +2 → +3).
        Formula: hp_max = 10 + 3 + 3*(6+3) = 13 + 27 = 40.
        (Pack-A-Risk: random-roll hp_max overwritten with average-formula value.)
        """
        from core.dnd_progression import apply_asi
        p = self._asi_profile(class_name="Knight", level=4, con=14, hp_max=30, hp_current=20)
        apply_asi(p, {"CON": 2})
        assert p["ability_scores"]["CON"] == 16
        # hp_max must now equal average-formula value for L4 Knight CON 16
        expected = _expected_hp_max(10, 4, 16)  # 10+3 + 3*(6+3) = 13+27 = 40
        assert p["hp_max"] == expected

    def test_B15_str_asi_does_not_change_hp(self):
        """STR ASI does NOT trigger HP change: recompute is called but CON unchanged.

        L4 Knight CON 14: formula hp_max = 10+2 + 3*(6+2) = 12+24 = 36.
        Profile has hp_max=36 (pre-set to formula value) → no-op guard fires.
        """
        from core.dnd_progression import apply_asi
        # Pre-set hp_max to the formula value so recompute is a no-op
        formula_hp = _expected_hp_max(10, 4, 14)  # 36
        p = self._asi_profile(class_name="Knight", level=4, con=14, hp_max=formula_hp)
        apply_asi(p, {"STR": 2})
        assert p["hp_max"] == formula_hp  # unchanged

    def test_B15b_str_asi_non_formula_hp_stays_after_recompute(self):
        """STR ASI: recompute_hp recalculates (CON unchanged), hp_max = formula value.

        If the profile had a non-formula hp_max (random-roll stale), after STR ASI
        recompute_hp runs for all abilities and corrects hp_max to the formula value.
        STR ASI did not change CON, so new formula == old formula (just corrects stale).
        """
        from core.dnd_progression import apply_asi
        # Stale hp_max (30 instead of formula 36 for L4 Knight CON 14)
        formula_hp = _expected_hp_max(10, 4, 14)  # 36
        p = self._asi_profile(class_name="Knight", level=4, con=14, hp_max=30, hp_current=20)
        apply_asi(p, {"STR": 2})
        # recompute_hp was called; hp_max is now corrected to formula regardless of STR change
        assert p["hp_max"] == formula_hp

    def test_B16_dex_asi_updates_ac_not_hp(self):
        """DEX ASI triggers AC recompute; HP should be corrected to formula value.

        L1 Knight CON 14, hp_max pre-set to formula value (12) → HP no-op.
        DEX 12→14 triggers recompute_ac: unarmored AC = 10 + DEX_mod(14) = 12.
        """
        from core.dnd_progression import apply_asi
        formula_hp = _expected_hp_max(10, 1, 14)  # 12
        p = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {"STR": 15, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 14},
            "hp_max": formula_hp,
            "hp_current": formula_hp,
            "Здоров'я": 100,
            "ac": 10,
            "equipped_armor": None,
            "equipped_shield": False,
            "asi_pending": True,
            "features": [],
        }
        apply_asi(p, {"DEX": 2})
        # HP unchanged (formula hp_max was already correct — no-op)
        assert p["hp_max"] == formula_hp
        # AC recomputed: 10 + ability_modifier(14) = 10 + 2 = 12
        assert p["ac"] == 12


# ---------------------------------------------------------------------------
# Section C: Cheat command via cmd_setability (isolated without Sheets)
# ---------------------------------------------------------------------------

class TestCmdSetabilityRecomputeHp:
    """cmd_setability integrates recompute_hp/recompute_ac for CON/DEX changes.

    We test the recompute call path directly (not the Telegram handler plumbing)
    because cmd_setability requires a live aiogram message object + gspread save.
    Instead, we replicate the core logic: write ability_scores, call recompute_hp.
    """

    def test_C17_setability_con_18_recomputes_hp(self):
        """Direct: setting CON=18 and calling recompute_hp produces correct hp_max.

        This mirrors the logic in cmd_setability lines 421-425.
        L1 Knight, CON 18 (mod +4): hp_max = 10 + 4 = 14.
        """
        profile = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {"STR": 14, "DEX": 10, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "hp_max": 12,   # old value for CON 14
            "hp_current": 10,
            "Здоров'я": 83,
        }
        # Replicate what cmd_setability does
        profile["ability_scores"]["CON"] = 18
        recompute_hp(profile)
        assert profile["ability_scores"]["CON"] == 18
        expected = _expected_hp_max(10, 1, 18)  # 10 + 4 = 14
        assert profile["hp_max"] == expected

    def test_C18_setability_dex_18_triggers_ac_not_hp(self):
        """Direct: setting DEX=18 triggers recompute_ac; HP should be corrected by recompute_hp.

        This mirrors cmd_setability: DEX triggers recompute_ac; CON triggers recompute_hp.
        DEX change does NOT directly trigger recompute_hp in cmd_setability.
        """
        from core.dnd_engine import recompute_ac
        profile = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {"STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "hp_max": 12,
            "hp_current": 12,
            "Здоров'я": 100,
            "ac": 10,
            "equipped_armor": None,
            "equipped_shield": False,
        }
        # Replicate cmd_setability for DEX
        profile["ability_scores"]["DEX"] = 18
        recompute_ac(profile)
        # AC updated: 10 + DEX_mod(18)=4 = 14
        assert profile["ac"] == 14
        # HP NOT changed by cmd_setability for DEX (no call to recompute_hp)
        assert profile["hp_max"] == 12

    @pytest.mark.anyio
    async def test_C17_async_cmd_setability_con_flow(self):
        """Async integration: cmd_setability with CON calls recompute_hp on the profile.

        We mock get_user_data / save_user_data to avoid real Sheets access.
        The test verifies that after cmd_setability runs, hp_max is recomputed.
        """
        from core.cheats import cmd_setability

        # Build a realistic L1 Knight profile stored in "Sheets"
        stored_profile = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {"STR": 15, "DEX": 10, "CON": 14, "INT": 10, "WIS": 10, "CHA": 14},
            "hp_max": 12,    # formula value for CON 14
            "hp_current": 10,
            "Здоров'я": 83,
            "Ім'я": "TestChar",
        }

        message = MagicMock()
        message.answer = AsyncMock()
        bot = MagicMock()
        chat_id = 12345

        with patch("core.cheats.get_user_data", new_callable=AsyncMock) as mock_get, \
             patch("core.cheats.save_user_data", new_callable=AsyncMock) as mock_save:

            mock_get.return_value = (dict(stored_profile), "row_ref")
            mock_save.return_value = None

            await cmd_setability(message, bot, chat_id, ["CON", "18"])

            # The profile passed to save_user_data should have updated CON and hp_max
            assert mock_save.called
            saved_profile = mock_save.call_args[0][1]
            assert saved_profile["ability_scores"]["CON"] == 18
            # hp_max must be recomputed: L1 Knight CON 18 (mod+4) = 10+4 = 14
            assert saved_profile["hp_max"] == _expected_hp_max(10, 1, 18)


# ---------------------------------------------------------------------------
# Section D: Regression — no spurious mutations
# ---------------------------------------------------------------------------

class TestRecomputeHpRegression:

    def test_D19_character_creation_hp_noop(self):
        """Profile built by _build_deterministic_dnd_profile has correct L1 hp_max.

        Calling recompute_hp on it should be a no-op (no-op guard: already correct).
        """
        from core.world import _build_deterministic_dnd_profile
        profile = _build_deterministic_dnd_profile(
            "Тест", "Старк", "The North", "Knight", "Westerosi (Andal)"
        )
        hp_max_before = profile["hp_max"]
        hp_current_before = profile.get("hp_current", hp_max_before)

        recompute_hp(profile)

        assert profile["hp_max"] == hp_max_before
        assert profile.get("hp_current", hp_max_before) == hp_current_before

    def test_D19_maester_character_creation_hp_noop(self):
        """Maester L1 via _build_deterministic_dnd_profile — recompute is no-op."""
        from core.world import _build_deterministic_dnd_profile
        profile = _build_deterministic_dnd_profile(
            "Тест", "Цитадель", "Reach", "Maester", "Westerosi (Andal)"
        )
        hp_max_before = profile["hp_max"]
        recompute_hp(profile)
        assert profile["hp_max"] == hp_max_before

    def test_D20_level_up_hp_accumulation_not_overwritten_before_recompute(self):
        """level_up accumulates random hp rolls; recompute_hp is NOT called here.

        After 3 level_ups, hp_max reflects accumulated random rolls.
        The test asserts hp_max >= minimum possible (all rolls = 1 + CON_mod).
        This verifies level_up is not inadvertently calling recompute_hp.

        Note: apply_asi (not level_up) calls recompute_hp. If recompute_hp were
        called inside level_up, hp_max would equal the average-formula value —
        not the sum of individual rolls. We verify this does NOT happen.
        """
        from core.dnd_progression import level_up

        profile = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {"STR": 15, "DEX": 10, "CON": 14, "INT": 10, "WIS": 10, "CHA": 14},
            "hp_max": 12,    # L1 Knight CON 14 (mod+2): 10+2=12
            "hp_current": 12,
            "Здоров'я": 100,
            "hit_dice_total": 1,
            "hit_dice_used": 0,
            "features": [],
            "proficiency_bonus": 2,
            "asi_pending": False,
            "xp": 0,
        }

        # Perform 3 level-ups with explicit hp_roll=5 (deterministic)
        # Each level: hp_gained = max(1, 5 + CON_mod(14)) = max(1, 5+2) = 7
        for _ in range(3):
            level_up(profile, "Knight", hp_roll=5)

        # After 3 level-ups: hp_max = 12 + 3*7 = 33
        assert profile["hp_max"] == 12 + 3 * 7
        assert profile["level"] == 4

        # Verify recompute_hp was NOT called inside level_up (if it were, hp_max
        # would equal the formula value for L4 Knight CON 14, not the rolled sum)
        formula_hp_l4 = _expected_hp_max(10, 4, 14)  # 36
        # Rolled accumulation (33) != formula (36), confirming no implicit recompute
        # (This holds because hp_roll=5 < average of 6 for d10)
        assert profile["hp_max"] != formula_hp_l4, (
            "level_up should NOT call recompute_hp internally. "
            f"Got {profile['hp_max']}, formula would be {formula_hp_l4}"
        )

    def test_D19_hp_max_matches_formula_at_l1(self):
        """L1 character hp_max from world.py == recompute_hp formula for all major classes."""
        from core.world import _build_deterministic_dnd_profile
        from core.dnd_classes import GOT_CLASSES

        for class_name, cls_def in GOT_CLASSES.items():
            profile = _build_deterministic_dnd_profile(
                "Тест", "Дім", "Вестерос", class_name, "Westerosi (Andal)",
                apply_heritage=False,
            )
            con = profile["ability_scores"]["CON"]
            expected = _expected_hp_max(cls_def.hit_die, 1, con)
            actual = profile["hp_max"]
            # After recompute, must be no-op (already matches formula)
            recompute_hp(profile)
            assert profile["hp_max"] == actual, (
                f"{class_name}: recompute_hp mutated hp_max from {actual} to {profile['hp_max']}"
            )
            # Also verify the hp_max matches expected formula
            assert actual == expected, (
                f"{class_name} L1 CON {con}: expected hp_max={expected}, got {actual}"
            )


# ---------------------------------------------------------------------------
# Edge cases: missing keys and boundary CON values
# ---------------------------------------------------------------------------

class TestRecomputeHpEdgeCases:

    def test_missing_ability_scores_key(self):
        """Profile with no ability_scores key: CON defaults to 10 (mod 0), no crash."""
        p = {
            "class": "Knight",
            "level": 1,
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
        }
        recompute_hp(p)
        # CON defaulted to 10 (mod 0): hp_max = 10
        assert p["hp_max"] == 10

    def test_missing_level_key_defaults_to_1(self):
        """Profile with no level key: defaults to 1, no crash."""
        p = {
            "class": "Knight",
            "ability_scores": {"CON": 14},
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
        }
        recompute_hp(p)
        # L1 Knight CON 14: 10+2=12
        assert p["hp_max"] == 12

    def test_level_zero_treated_as_1(self):
        """Level 0 is invalid per profile invariant; safe_int floors to 1."""
        p = _profile("Knight", 0, 14, hp_max=0)
        recompute_hp(p)
        # max(1, 0) = 1 level → same as L1 Knight CON 14 = 12
        assert p["hp_max"] == 12

    def test_hp_current_never_negative_after_recompute(self):
        """hp_current is always >= 0 after recompute (max(0, ...) clamp)."""
        # Start with a hp_current that would go negative if ratio preserved badly
        # hp_current=0 already (dead character) — ratio = 0/old = 0, new = 0
        p = _profile("Knight", 5, 14, hp_max=30, hp_current=0)
        recompute_hp(p)
        assert p["hp_current"] >= 0

    def test_hp_current_capped_at_new_hp_max(self):
        """hp_current never exceeds hp_max after recompute (ratio clamped to 1.0)."""
        # Old hp_current > old_hp_max (corrupt/cheated profile)
        p = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {"CON": 14},
            "hp_max": 8,        # stale
            "hp_current": 9999, # corrupt: exceeds hp_max
            "Здоров'я": 100,
        }
        recompute_hp(p)
        assert p["hp_current"] <= p["hp_max"]

    def test_zdravya_sync_for_zero_hp_current(self):
        """Legacy 'Здоров'я' = 0 when hp_current = 0."""
        p = _profile("Knight", 1, 14, hp_max=30, hp_current=0)
        recompute_hp(p)
        if p["hp_max"] > 0 and p["hp_current"] == 0:
            assert p["Здоров'я"] == 0
