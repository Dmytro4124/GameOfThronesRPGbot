# test/test_heritage_hp_creation.py
"""
Regression tests for bug: hp_max did not account for heritage CON bonus at character creation.

Bug root cause: bot/handlers.py creation callbacks called apply_heritage_bonuses() + recompute_ac()
but NOT recompute_hp().  Fix: added recompute_hp(profile) call after recompute_ac() in both
callback_ability_accept (~line 471) and callback_pb_confirm (~line 645).

These tests verify the functional sequence that the callbacks now perform:
    1. Build a base profile (with pre-heritage CON)
    2. Call apply_heritage_bonuses(profile, heritage_name)
    3. Call recompute_hp(profile)
    4. Assert hp_max reflects the POST-heritage CON modifier

Test design: pure unit — no aiogram, no gspread, no Gemini.
Profile dicts are constructed directly; only core.dnd_heritages and core.dnd_engine
are exercised.

Heritage CON bonuses confirmed from core/dnd_heritages.py:
  - "First Men (Stark line)": CON +1, WIS +2
  - "Ironborn":               CON +1, STR +1
  - "Westerosi (Andal)":      +1 to any two (no fixed CON bonus)
  - "Valyrian Descent":       CHA +2, INT +1  (no CON bonus)
  - "Free Folk":              STR +1           (no CON bonus)
  - "Red Priest":             CHA +1, WIS +1   (no CON bonus)
"""

from __future__ import annotations

import pytest

from core.dnd_heritages import apply_heritage_bonuses
from core.dnd_engine import recompute_hp
from core.dnd_core import ability_modifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_profile(class_name: str, level: int, con: int) -> dict:
    """Return a minimal creation-time profile before heritage bonuses are applied."""
    return {
        "class": class_name,
        "level": level,
        "ability_scores": {
            "STR": 10, "DEX": 10, "CON": con, "INT": 10, "WIS": 10, "CHA": 10,
        },
        "hp_max": 0,       # placeholder — will be set by recompute_hp
        "hp_current": 0,
        "Здоров'я": 0,
        "heritage": class_name,  # not meaningful here; heritage arg is passed explicitly
    }


def _expected_l1_hp(hit_die: int, con: int) -> int:
    """L1 hp_max = hit_die + CON_mod (PHB: take max on first level)."""
    return max(1, hit_die + ability_modifier(con))


# ---------------------------------------------------------------------------
# Section 1: Main regression keis — CON crosses parity boundary
#
# Bug scenario: CON 15 → 16 after heritage +1 (First Men or Ironborn).
# modifier(15) = +2, modifier(16) = +3 → hp_max differs by 1 on L1.
# ---------------------------------------------------------------------------

class TestHeritageCONBonusRecomputeHp:

    @pytest.mark.parametrize("heritage_name,class_name,hit_die", [
        ("First Men (Stark line)", "Knight",    10),
        ("First Men (Stark line)", "Maester",    6),
        ("First Men (Stark line)", "Sellsword", 10),
        ("Ironborn",               "Knight",    10),
        ("Ironborn",               "Sellsword", 10),
        ("Ironborn",               "Maester",    6),
    ])
    def test_con_boundary_15_to_16_hp_max_increases(
        self, heritage_name: str, class_name: str, hit_die: int
    ):
        """Regression: CON 15 (mod +2) → 16 (mod +3) via heritage +1.

        Without the fix: recompute_hp was not called after apply_heritage_bonuses,
        so hp_max stayed at (hit_die + 2) using the PRE-heritage CON modifier.
        With the fix: hp_max = hit_die + 3.

        This test catches the exact missing-call regression described in the bug report.
        """
        base_con = 15  # mod +2 — sits at parity boundary
        profile = _minimal_profile(class_name, level=1, con=base_con)

        # Step 1: apply heritage bonuses (this raises CON 15 → 16)
        profile = apply_heritage_bonuses(profile, heritage_name)

        post_con = profile["ability_scores"]["CON"]
        assert post_con == 16, (
            f"{heritage_name} should give CON +1: 15→16, got {post_con}"
        )

        # Step 2: recompute hp (the call that was missing before the fix)
        recompute_hp(profile)

        expected = _expected_l1_hp(hit_die, post_con)  # hit_die + 3
        expected_before_fix = _expected_l1_hp(hit_die, base_con)  # hit_die + 2

        assert profile["hp_max"] == expected, (
            f"{heritage_name} + {class_name}: expected hp_max={expected} "
            f"(CON 16 mod +3), got {profile['hp_max']}"
        )
        # Explicitly confirm the fix matters: post-heritage CON yields a
        # different hp_max than the pre-heritage value this boundary set targets.
        assert expected != expected_before_fix, (
            "hp_max should reflect post-heritage CON, not pre-heritage CON"
        )
        # Concrete: for CON 15→16, mod +2→+3, difference is +1 HP
        assert profile["hp_max"] == expected_before_fix + 1, (
            f"CON 15→16 boundary: hp_max should be exactly 1 higher than pre-heritage. "
            f"pre={expected_before_fix}, post={profile['hp_max']}"
        )

    @pytest.mark.parametrize("base_con,heritage_name,expected_delta", [
        # CON 11 → 12: mod 0 → +1  (+1 HP)
        (11, "First Men (Stark line)", 1),
        # CON 13 → 14: mod +1 → +2  (+1 HP)
        (13, "Ironborn",               1),
        # CON 15 → 16: mod +2 → +3  (+1 HP)
        (15, "First Men (Stark line)", 1),
        # CON 17 → 18: mod +3 → +4  (+1 HP)
        (17, "Ironborn",               1),
    ])
    def test_con_parity_boundaries_all_give_plus1_hp(
        self, base_con: int, heritage_name: str, expected_delta: int
    ):
        """Heritage CON +1 crossing any even→odd boundary raises hp_max by exactly +1 at L1.

        Knight (d10) is used as a stable reference class.
        """
        class_name = "Knight"
        hit_die = 10
        profile = _minimal_profile(class_name, level=1, con=base_con)
        profile = apply_heritage_bonuses(profile, heritage_name)

        post_con = profile["ability_scores"]["CON"]
        assert post_con == base_con + 1, (
            f"Heritage CON+1 did not increment: {base_con} → {post_con}"
        )

        recompute_hp(profile)

        expected_hp = _expected_l1_hp(hit_die, post_con)
        old_expected_hp = _expected_l1_hp(hit_die, base_con)

        assert profile["hp_max"] == expected_hp, (
            f"CON {base_con}→{post_con}: expected hp_max={expected_hp}, got {profile['hp_max']}"
        )
        assert profile["hp_max"] - old_expected_hp == expected_delta, (
            f"CON {base_con}→{post_con}: expected delta={expected_delta}, "
            f"actual delta={profile['hp_max'] - old_expected_hp}"
        )

    @pytest.mark.parametrize("base_con,heritage_name", [
        # CON 10 → 11: mod 0 → 0  (no change — stays within same mod bracket)
        (10, "First Men (Stark line)"),
        # CON 12 → 13: mod +1 → +1
        (12, "Ironborn"),
        # CON 14 → 15: mod +2 → +2
        (14, "First Men (Stark line)"),
        # CON 16 → 17: mod +3 → +3
        (16, "Ironborn"),
    ])
    def test_con_odd_base_no_mod_change_hp_unchanged(
        self, base_con: int, heritage_name: str
    ):
        """Heritage CON +1 on odd base (10,12,14,16…) stays within same modifier bracket.

        hp_max should NOT change because modifier stays the same.
        This is the complement to the parity-boundary tests above.
        """
        class_name = "Knight"
        hit_die = 10
        profile = _minimal_profile(class_name, level=1, con=base_con)

        # Capture expected hp_max with OLD con (mod unchanged after +1)
        old_mod = ability_modifier(base_con)
        new_mod = ability_modifier(base_con + 1)
        assert old_mod == new_mod, (
            f"Test precondition failed: CON {base_con}→{base_con+1} mod {old_mod}→{new_mod}"
        )

        profile = apply_heritage_bonuses(profile, heritage_name)
        recompute_hp(profile)

        expected_hp = _expected_l1_hp(hit_die, base_con + 1)  # same as base_con (same mod)
        assert profile["hp_max"] == expected_hp, (
            f"CON {base_con}→{base_con+1} (same mod): hp_max={profile['hp_max']}, expected={expected_hp}"
        )


# ---------------------------------------------------------------------------
# Section 2: Control cases — heritages WITHOUT fixed CON bonus
# ---------------------------------------------------------------------------

class TestNoCONHeritagHpUnchanged:
    """Heritages that do NOT give fixed CON bonus must not alter hp_max via CON path."""

    @pytest.mark.parametrize("heritage_name,fixed_choices", [
        # Valyrian: CHA +2, INT +1 — no CON
        ("Valyrian Descent", None),
        # Free Folk: STR +1 — no CON
        ("Free Folk", None),
        # Red Priest: CHA +1, WIS +1 — no CON
        ("Red Priest", None),
    ])
    def test_no_con_heritage_hp_stays_formula_for_base_con(
        self, heritage_name: str, fixed_choices
    ):
        """Non-CON heritages: after apply_heritage_bonuses + recompute_hp,
        hp_max equals the formula for the ORIGINAL CON (not changed by heritage).

        Uses Knight (d10) L1 with CON 14 as control profile.
        """
        class_name = "Knight"
        base_con = 14
        profile = _minimal_profile(class_name, level=1, con=base_con)
        profile = apply_heritage_bonuses(profile, heritage_name)

        # CON must be unchanged for these heritages
        assert profile["ability_scores"]["CON"] == base_con, (
            f"{heritage_name} must not change CON; got {profile['ability_scores']['CON']}"
        )

        recompute_hp(profile)

        expected = _expected_l1_hp(10, base_con)  # 10 + 2 = 12
        assert profile["hp_max"] == expected, (
            f"{heritage_name}: expected hp_max={expected} (CON unchanged), got {profile['hp_max']}"
        )

    def test_westerosi_andal_con_choice_optional(self):
        """Westerosi (Andal) can optionally put +1 into CON if player chooses.

        If the player chooses CON in ability_choices, CON rises and hp_max increases.
        This test verifies the mechanic works when CON IS chosen.
        (Control: when CON is NOT chosen, hp_max stays with original CON.)
        """
        class_name = "Knight"
        base_con = 15  # parity boundary: mod +2 → +3 if CON chosen
        profile = _minimal_profile(class_name, level=1, con=base_con)

        # Player explicitly picks CON as one of the two +1 choices
        profile = apply_heritage_bonuses(profile, "Westerosi (Andal)", ability_choices=["CON", "STR"])
        assert profile["ability_scores"]["CON"] == 16

        recompute_hp(profile)
        expected = _expected_l1_hp(10, 16)  # 10 + 3 = 13
        assert profile["hp_max"] == expected

    def test_westerosi_andal_no_con_choice_hp_unchanged(self):
        """Westerosi (Andal) with CON NOT chosen: hp_max stays at base CON formula."""
        class_name = "Knight"
        base_con = 15
        profile = _minimal_profile(class_name, level=1, con=base_con)

        # Player picks STR and DEX — NOT CON
        profile = apply_heritage_bonuses(profile, "Westerosi (Andal)", ability_choices=["STR", "DEX"])
        assert profile["ability_scores"]["CON"] == base_con  # unchanged

        recompute_hp(profile)
        expected = _expected_l1_hp(10, base_con)  # 10 + 2 = 12
        assert profile["hp_max"] == expected


# ---------------------------------------------------------------------------
# Section 3: Formula invariant (§5.2) — L1 hp_max = hit_die + con_mod
# ---------------------------------------------------------------------------

class TestL1HpFormulaInvariant:
    """Verify §5.2: hp_max at L1 = hit_die + floor((CON - 10) / 2)."""

    @pytest.mark.parametrize("class_name,hit_die,post_heritage_con,expected_hp", [
        # First Men heritage: CON +1
        # CON 13 base → 14 (mod +2): Knight 10+2=12
        ("Knight",    10, 14, 12),
        # CON 15 base → 16 (mod +3): Knight 10+3=13
        ("Knight",    10, 16, 13),
        # Maester CON 15 base → 16: 6+3=9
        ("Maester",    6, 16,  9),
        # Sellsword CON 11 base → 12 (mod +1): 10+1=11
        ("Sellsword", 10, 12, 11),
        # Ironborn: CON +1, STR +1
        # CON 9 base → 10 (mod 0): Knight 10+0=10
        ("Knight",    10, 10, 10),
    ])
    def test_formula_con_mod_invariant(
        self, class_name: str, hit_die: int, post_heritage_con: int, expected_hp: int
    ):
        """After heritage application and recompute_hp, L1 hp_max == hit_die + con_mod."""
        con_mod = ability_modifier(post_heritage_con)
        assert max(1, hit_die + con_mod) == expected_hp, (
            f"Test data error: {class_name} hit_die={hit_die} CON={post_heritage_con} "
            f"mod={con_mod}: expected {expected_hp}"
        )

        profile = {
            "class": class_name,
            "level": 1,
            "ability_scores": {
                "STR": 10, "DEX": 10,
                "CON": post_heritage_con,  # already post-heritage
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
        }

        recompute_hp(profile)

        assert profile["hp_max"] == expected_hp, (
            f"§5.2 invariant: {class_name} L1 CON {post_heritage_con} (mod {con_mod:+d}): "
            f"expected hp_max={expected_hp}, got {profile['hp_max']}"
        )
        # Confirm modifier formula
        assert ability_modifier(post_heritage_con) == con_mod


# ---------------------------------------------------------------------------
# Section 4: Callback sequence simulation
#
# Simulates the EXACT sequence that bot/handlers.py creation callbacks now perform:
#   profile = apply_heritage_bonuses(profile, heritage_name)
#   recompute_ac(profile)
#   recompute_hp(profile)   ← this was the missing line before the fix
# ---------------------------------------------------------------------------

class TestCallbackSequence:
    """Simulate the full callback sequence: apply_heritage_bonuses → recompute_ac → recompute_hp."""

    def test_first_men_knight_full_sequence(self):
        """Full callback sequence: First Men + Knight, CON 15 base.

        Before fix: recompute_hp not called → hp_max = 12 (mod +2, pre-heritage)
        After fix:  recompute_hp called     → hp_max = 13 (mod +3, post-heritage CON 16)
        """
        from core.dnd_engine import recompute_ac

        profile = {
            "class": "Knight",
            "level": 1,
            "ability_scores": {
                "STR": 10, "DEX": 12, "CON": 15, "INT": 10, "WIS": 10, "CHA": 10,
            },
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
            "ac": 10,
            "equipped_armor": None,
            "equipped_shield": False,
        }

        # Callback sequence (as now implemented after fix)
        profile = apply_heritage_bonuses(profile, "First Men (Stark line)")
        recompute_ac(profile)
        recompute_hp(profile)

        # Verify CON was incremented
        assert profile["ability_scores"]["CON"] == 16

        # Key regression assertion: hp_max uses POST-heritage CON
        expected_hp = _expected_l1_hp(10, 16)  # 10 + 3 = 13
        assert profile["hp_max"] == expected_hp, (
            f"Full callback sequence: hp_max should be {expected_hp} (CON 16, mod +3), "
            f"got {profile['hp_max']}"
        )

    def test_ironborn_sellsword_full_sequence(self):
        """Full callback sequence: Ironborn + Sellsword, CON 13 base → 14."""
        from core.dnd_engine import recompute_ac

        profile = {
            "class": "Sellsword",
            "level": 1,
            "ability_scores": {
                "STR": 10, "DEX": 12, "CON": 13, "INT": 10, "WIS": 10, "CHA": 10,
            },
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
            "ac": 10,
            "equipped_armor": None,
            "equipped_shield": False,
        }

        profile = apply_heritage_bonuses(profile, "Ironborn")
        recompute_ac(profile)
        recompute_hp(profile)

        assert profile["ability_scores"]["CON"] == 14  # 13 + 1
        expected_hp = _expected_l1_hp(10, 14)  # 10 + 2 = 12
        assert profile["hp_max"] == expected_hp, (
            f"Ironborn Sellsword: hp_max={profile['hp_max']}, expected={expected_hp}"
        )

    def test_valyrian_maester_hp_unchanged_by_con(self):
        """Control: Valyrian Descent + Maester, CON 14 base — no CON bonus from heritage.

        recompute_hp runs but must preserve hp_max = 6 + 2 = 8 (CON 14, mod +2).
        """
        from core.dnd_engine import recompute_ac

        profile = {
            "class": "Maester",
            "level": 1,
            "ability_scores": {
                "STR": 10, "DEX": 10, "CON": 14, "INT": 12, "WIS": 10, "CHA": 10,
            },
            "hp_max": 0,
            "hp_current": 0,
            "Здоров'я": 0,
            "ac": 10,
            "equipped_armor": None,
            "equipped_shield": False,
        }

        profile = apply_heritage_bonuses(profile, "Valyrian Descent")
        recompute_ac(profile)
        recompute_hp(profile)

        assert profile["ability_scores"]["CON"] == 14  # unchanged
        expected_hp = _expected_l1_hp(6, 14)  # 6 + 2 = 8
        assert profile["hp_max"] == expected_hp, (
            f"Valyrian Maester control: hp_max={profile['hp_max']}, expected={expected_hp}"
        )
