# test/test_character_creation_point_buy.py
"""
QA tests for:
  - core/character_creation.py  (point-buy math, pure unit)
  - core/world.py               (_build_deterministic_dnd_profile apply_heritage=False)
  - core/dnd_engine.py          (recompute_ac sanity check)

No handler / UI tests this round — aiogram mocking overhead not justified
given the sparse existing handler test patterns (see test_handlers.py which
itself mocks at a very coarse level). Skipped explicitly in the report.

§5.2 invariants targeted:
  - base ability scores 8-15 (pre-heritage)
  - POINT_BUY_BUDGET = 27
  - POINT_BUY_COSTS table correct (non-linear at 13→14 = 2 pts, 14→15 = 2 pts)
  - can_increment correctly uses marginal cost, not flat +1
"""

from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Pre-import isolation for core.world (imports database.sheets at module level)
#
# Strategy: save the original sys.modules entries for the three database stubs
# (or record them as absent), inject mocks, import what we need, then restore
# sys.modules to its prior state.  This prevents pollution of later test files
# that rely on the *real* (or their own mocked) database modules.
#
# Why we can't just use "if not in sys.modules" guard only:
#   The test file is alphabetically between test_bg_intro_flow.py and
#   test_combat_package2.py.  bg_intro_flow does NOT pre-inject database stubs,
#   so at collection time those keys are absent → we inject mocks.  Then
#   test_combat_package2.py expects the real (or its own mocked) module and
#   gets the MagicMock instead → 56 cross-contamination failures.
# ---------------------------------------------------------------------------

_DB_MODULE_KEYS = ("database.sheets", "database.operations", "database.canon_npc")

# Snapshot BEFORE injection
_pre_injection_db_state: dict = {
    k: sys.modules.get(k) for k in _DB_MODULE_KEYS
}


def _inject_db_mocks() -> None:
    """Install MagicMock stubs for the three database modules."""
    if "database.sheets" not in sys.modules or not isinstance(sys.modules["database.sheets"], MagicMock):
        fake_sheets = MagicMock()
        fake_sheets.db = MagicMock()
        sys.modules["database.sheets"] = fake_sheets
    if "database.operations" not in sys.modules or not isinstance(sys.modules["database.operations"], MagicMock):
        fake_ops = MagicMock()
        fake_ops.refresh_npc_database = MagicMock(return_value=None)
        fake_ops.find_best_match = MagicMock(return_value=None)
        fake_ops.ensure_user_npc_sheet = MagicMock(return_value=None)
        fake_ops._npc_tab_name = MagicMock(return_value="NPC_0")
        sys.modules["database.operations"] = fake_ops
    if "database.canon_npc" not in sys.modules or not isinstance(sys.modules["database.canon_npc"], MagicMock):
        fake_canon = MagicMock()
        fake_canon.get_canon_npcs_copy = MagicMock(return_value=[])
        sys.modules["database.canon_npc"] = fake_canon


def _restore_db_mocks() -> None:
    """Restore sys.modules to the state captured before injection."""
    for k, original in _pre_injection_db_state.items():
        if original is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = original


_inject_db_mocks()

# ---------------------------------------------------------------------------
# Imports (safe now that stubs are in sys.modules)
# ---------------------------------------------------------------------------

from core.character_creation import (
    ABILITY_KEYS,
    POINT_BUY_BUDGET,
    POINT_BUY_COSTS,
    POINT_BUY_MAX_BASE,
    POINT_BUY_MIN_BASE,
    point_buy_can_decrement,
    point_buy_can_increment,
    point_buy_cost,
    point_buy_initial,
    point_buy_remaining,
)
from core.world import _build_deterministic_dnd_profile
from core.dnd_heritages import apply_heritage_bonuses
from core.dnd_engine import recompute_ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_at(value: int) -> dict[str, int]:
    """Return a scores dict with all 6 abilities set to *value*."""
    return {ab: value for ab in ABILITY_KEYS}


def _scores(**kwargs: int) -> dict[str, int]:
    """Return a complete 6-ability scores dict.  Unspecified abilities default to 8."""
    base = _all_at(8)
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Cleanup fixture: restore sys.modules after all tests in this module complete
# so that later test files that use the real database modules are not polluted.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _restore_db_modules_after_module():
    """Yield (run all tests), then restore database sys.modules entries."""
    yield
    _restore_db_mocks()


# ===========================================================================
# Section 1: point_buy_initial()
# ===========================================================================

class TestPointBuyInitial:

    def test_returns_dict(self):
        assert isinstance(point_buy_initial(), dict)

    def test_has_all_six_ability_keys(self):
        result = point_buy_initial()
        assert set(result.keys()) == set(ABILITY_KEYS)

    def test_all_values_are_eight(self):
        result = point_buy_initial()
        for ab in ABILITY_KEYS:
            assert result[ab] == 8, f"{ab} should be 8, got {result[ab]}"

    def test_returns_new_dict_each_call(self):
        """Mutations to the returned dict must not affect a second call."""
        d1 = point_buy_initial()
        d1["STR"] = 15
        d2 = point_buy_initial()
        assert d2["STR"] == 8


# ===========================================================================
# Section 2: point_buy_cost() — canonical cost table
# ===========================================================================

class TestPointBuyCost:

    def test_all_eights_costs_zero(self):
        assert point_buy_cost(_all_at(8)) == 0

    def test_remaining_all_eights_is_27(self):
        assert point_buy_remaining(_all_at(8)) == 27

    @pytest.mark.parametrize("scores,expected_cost", [
        # Canonical balanced: STR15 DEX14 CON13 INT12 WIS10 CHA8
        ({"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}, 27),
        # Specialist (three 15s): STR15 DEX15 CON15 INT8 WIS8 CHA8
        ({"STR": 15, "DEX": 15, "CON": 15, "INT": 8, "WIS": 8, "CHA": 8}, 27),
        # Balanced no 15s: STR14 DEX14 CON13 INT12 WIS10 CHA8
        ({"STR": 14, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}, 25),
    ])
    def test_canonical_totals(self, scores, expected_cost):
        assert point_buy_cost(scores) == expected_cost

    def test_canonical_balanced_remaining_zero(self):
        scores = {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}
        assert point_buy_remaining(scores) == 0

    def test_balanced_no_15s_remaining_two(self):
        scores = {"STR": 14, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}
        assert point_buy_remaining(scores) == 2

    def test_overspend_all_13s_cost_30(self):
        """All 13s costs 30 (6 × 5 = 30) — over the 27-point budget."""
        assert point_buy_cost(_all_at(13)) == 30

    def test_overspend_all_13s_remaining_negative(self):
        """point_buy_remaining must return -3 (does NOT raise per contract)."""
        assert point_buy_remaining(_all_at(13)) == -3

    def test_cost_table_13_to_14_nonlinear(self):
        """POINT_BUY_COSTS[14] - POINT_BUY_COSTS[13] must be exactly 2 (non-linear step)."""
        marginal = POINT_BUY_COSTS[14] - POINT_BUY_COSTS[13]
        assert marginal == 2, (
            f"Marginal cost 13→14 should be 2 (5→7), got {marginal}. "
            "PHB point-buy table non-linear step at 13→14."
        )

    def test_cost_table_14_to_15_nonlinear(self):
        """POINT_BUY_COSTS[15] - POINT_BUY_COSTS[14] must be exactly 2 (9-7=2)."""
        marginal = POINT_BUY_COSTS[15] - POINT_BUY_COSTS[14]
        assert marginal == 2, (
            f"Marginal cost 14→15 should be 2 (7→9), got {marginal}."
        )


# ===========================================================================
# Section 3: point_buy_cost() — ValueError on bad input
# ===========================================================================

class TestPointBuyCostValidation:

    def test_raises_on_missing_key_five_abilities(self):
        """Only 5 keys — ValueError expected."""
        bad = {ab: 8 for ab in ABILITY_KEYS if ab != "CHA"}
        with pytest.raises(ValueError, match="missing"):
            point_buy_cost(bad)

    def test_raises_on_extra_key(self):
        """Extra unknown key → ValueError."""
        bad = _all_at(8)
        bad["LUK"] = 10
        with pytest.raises(ValueError, match="unexpected"):
            point_buy_cost(bad)

    def test_raises_on_non_int_float(self):
        """float value (e.g. 12.5) → ValueError."""
        bad = _all_at(8)
        bad["STR"] = 12.5
        with pytest.raises(ValueError):
            point_buy_cost(bad)

    def test_raises_on_non_int_string(self):
        """string value → ValueError."""
        bad = _all_at(8)
        bad["DEX"] = "12"
        with pytest.raises(ValueError):
            point_buy_cost(bad)

    def test_raises_on_score_below_min(self):
        """Score 7 is below POINT_BUY_MIN_BASE (8) → ValueError."""
        bad = _all_at(8)
        bad["CON"] = 7
        with pytest.raises(ValueError):
            point_buy_cost(bad)

    def test_raises_on_score_above_max(self):
        """Score 16 is above POINT_BUY_MAX_BASE (15) → ValueError."""
        bad = _all_at(8)
        bad["INT"] = 16
        with pytest.raises(ValueError):
            point_buy_cost(bad)


# ===========================================================================
# Section 4: point_buy_can_increment()
# ===========================================================================

class TestPointBuyCanIncrement:

    def test_from_initial_all_abilities_can_increment(self):
        """From all-8 start (27 pts remaining), every ability can be incremented."""
        scores = point_buy_initial()
        for ab in ABILITY_KEYS:
            assert point_buy_can_increment(scores, ab), (
                f"Expected can_increment({ab}) == True from initial all-8 scores"
            )

    def test_at_max_score_cannot_increment(self):
        """An ability at 15 (POINT_BUY_MAX_BASE) cannot be incremented."""
        scores = _scores(STR=15)
        # Cost: 9 + 5×0 = 9; remaining = 18 — budget not the constraint; ceiling is.
        assert not point_buy_can_increment(scores, "STR")

    def test_cannot_increment_when_budget_exhausted_flat(self):
        """If remaining == 0, no increment is possible regardless of current score."""
        scores = {"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}
        # cost == 27, remaining == 0
        for ab in ABILITY_KEYS:
            assert not point_buy_can_increment(scores, ab), (
                f"Expected can_increment({ab}) == False when budget exhausted"
            )

    def test_critical_marginal_cost_13_to_14_needs_2_pts(self):
        """
        Regression for flat-+1 bug: STR=13 has marginal cost 2 for the next step.
        With only 1 point remaining, can_increment(STR) must return False.

        Construct: STR=13 (cost 5) + DEX=14 (cost 7) + CON=14 (cost 7) = 19 pts.
        remaining = 27 - 19 = 8.  That's still enough.

        We need remaining == 1.  Let's use:
          STR=13 (5) + DEX=14 (7) + CON=14 (7) + INT=13 (5) = 24.  remaining = 3.
          Add WIS=10 (2) = 26.  remaining = 1.  CHA stays at 8 (0).

        At this point: STR=13, remaining=1.  13→14 costs 2 → cannot increment.
        """
        scores = {
            "STR": 13,  # cost 5
            "DEX": 14,  # cost 7
            "CON": 14,  # cost 7
            "INT": 13,  # cost 5
            "WIS": 10,  # cost 2
            "CHA": 8,   # cost 0
        }
        total_cost = point_buy_cost(scores)
        remaining = point_buy_remaining(scores)
        assert total_cost == 26, f"Expected total_cost=26, got {total_cost}"
        assert remaining == 1, f"Expected remaining=1, got {remaining}"
        # 13→14 costs 2 marginal points; only 1 remaining → must be False
        assert not point_buy_can_increment(scores, "STR"), (
            "can_increment STR at score=13 with only 1 pt remaining must be False "
            "(marginal cost is 2, not 1). Flat-+1 bug if this fails."
        )

    def test_can_increment_true_when_marginal_exactly_fits(self):
        """
        STR=13 with exactly 2 remaining points: marginal cost for 13→14 is 2 → True.

        Construct: STR=13 (5) + DEX=14 (7) + CON=13 (5) + INT=10 (2) + WIS=8 (0) + CHA=8 (0)
        = 19 pts.  remaining = 8.  Easier: use fewer pts.

        Simple: STR=13 (5), all others at 8 (0). total = 5, remaining = 22.
        Then we need remaining == 2 exactly.  Use:
          STR=13 (5) + DEX=14 (7) + CON=14 (7) + INT=8 (0) + WIS=8 (0) + CHA=8 (0) = 19.
          remaining = 8.  Add INT=12 (4): remaining = 4.  Add WIS=9 (1): remaining = 3.
          Add CHA=9 (1): remaining = 2.  STR stays 13, marginal = 2.

        With remaining == 2, can_increment(STR) must be True.
        """
        scores = {
            "STR": 13,  # cost 5
            "DEX": 14,  # cost 7
            "CON": 14,  # cost 7
            "INT": 12,  # cost 4
            "WIS":  9,  # cost 1
            "CHA":  9,  # cost 1
        }
        total_cost = point_buy_cost(scores)
        remaining = point_buy_remaining(scores)
        assert total_cost == 25, f"Expected total_cost=25, got {total_cost}"
        assert remaining == 2, f"Expected remaining=2, got {remaining}"
        assert point_buy_can_increment(scores, "STR"), (
            "can_increment STR at score=13 with 2 pts remaining must be True "
            "(marginal cost is exactly 2)."
        )

    def test_raises_on_unknown_ability_key(self):
        """Unknown ability key (e.g. 'LUK') → ValueError."""
        scores = point_buy_initial()
        with pytest.raises(ValueError):
            point_buy_can_increment(scores, "LUK")


# ===========================================================================
# Section 5: point_buy_can_decrement()
# ===========================================================================

class TestPointBuyCanDecrement:

    def test_from_initial_all_false(self):
        """From all-8 (floor), no ability can be decremented."""
        scores = point_buy_initial()
        for ab in ABILITY_KEYS:
            assert not point_buy_can_decrement(scores, ab), (
                f"Expected can_decrement({ab}) == False at score=8 (POINT_BUY_MIN_BASE)"
            )

    def test_at_nine_can_decrement(self):
        """Score=9 > 8 → can_decrement True."""
        scores = _scores(STR=9)
        assert point_buy_can_decrement(scores, "STR")

    def test_at_fifteen_can_decrement(self):
        """Score=15 → can_decrement True (ceiling doesn't prevent going down)."""
        scores = _scores(STR=15)
        assert point_buy_can_decrement(scores, "STR")

    def test_raises_on_unknown_ability_key(self):
        """Unknown key → ValueError."""
        scores = point_buy_initial()
        with pytest.raises(ValueError):
            point_buy_can_decrement(scores, "LUK")


# ===========================================================================
# Section 6: core/world.py — _build_deterministic_dnd_profile apply_heritage=False
# ===========================================================================

class TestBuildDeterministicApplyHeritageFalse:

    def test_ability_scores_in_base_range_no_heritage(self):
        """When apply_heritage=False, all ability scores must be in [8, 15]."""
        profile = _build_deterministic_dnd_profile(
            "Тест", "Таргарієн", "The Crownlands",
            "Courtier", "Valyrian Descent",
            apply_heritage=False,
        )
        scores = profile["ability_scores"]
        for ab, val in scores.items():
            assert 8 <= val <= 15, (
                f"apply_heritage=False: {ab}={val} outside base range [8,15]"
            )

    def test_heritage_field_set_regardless_of_flag(self):
        """profile['heritage'] must be set to the requested heritage even when apply_heritage=False."""
        profile = _build_deterministic_dnd_profile(
            "Тест", "Таргарієн", "The Crownlands",
            "Courtier", "Valyrian Descent",
            apply_heritage=False,
        )
        assert profile["heritage"] == "Valyrian Descent"

    def test_valyrian_no_cha_int_bonus_when_apply_heritage_false(self):
        """
        Valyrian Descent: CHA+2 INT+1 must NOT be applied when apply_heritage=False.

        Courtier primary_abilities = [CHA, INT]:
          standard array slot 0 (15) → CHA, slot 1 (14) → INT.
          With apply_heritage=True: CHA=17, INT=15.
          With apply_heritage=False: CHA=15, INT=14.
        """
        profile_no_heritage = _build_deterministic_dnd_profile(
            "Тест", "Таргарієн", "The Crownlands",
            "Courtier", "Valyrian Descent",
            apply_heritage=False,
        )
        scores = profile_no_heritage["ability_scores"]
        # CHA must be base (15 from standard array slot 0), not 17
        assert scores["CHA"] == 15, (
            f"apply_heritage=False: CHA should be 15 (base), got {scores['CHA']}. "
            "Valyrian +2 should not be applied."
        )
        # INT must be base (14 from standard array slot 1), not 15
        assert scores["INT"] == 14, (
            f"apply_heritage=False: INT should be 14 (base), got {scores['INT']}. "
            "Valyrian +1 should not be applied."
        )

    def test_apply_heritage_true_default_behavior_unchanged(self):
        """apply_heritage=True (default) must still produce heritage-boosted scores."""
        profile_with = _build_deterministic_dnd_profile(
            "Тест", "Таргарієн", "The Crownlands",
            "Courtier", "Valyrian Descent",
            apply_heritage=True,
        )
        scores = profile_with["ability_scores"]
        # Valyrian +2 CHA: 15 base → 17
        assert scores["CHA"] == 17, (
            f"apply_heritage=True: CHA should be 17, got {scores['CHA']}"
        )
        assert scores["INT"] == 15, (
            f"apply_heritage=True: INT should be 15, got {scores['INT']}"
        )

    def test_apply_heritage_false_then_manual_bonuses_applied(self):
        """
        Integration: after apply_heritage=False, manually calling apply_heritage_bonuses
        must yield the same result as apply_heritage=True.

        This validates the two-phase flow the new handlers.py uses:
          1. generate_initial_stats(apply_heritage=False) → base profile
          2. player confirms point-buy  → apply_heritage_bonuses(profile, heritage)
        """
        profile_base = _build_deterministic_dnd_profile(
            "Тест", "Таргарієн", "The Crownlands",
            "Courtier", "Valyrian Descent",
            apply_heritage=False,
        )
        profile_manual = apply_heritage_bonuses(
            profile_base, "Valyrian Descent", ability_choices=None
        )

        profile_auto = _build_deterministic_dnd_profile(
            "Тест", "Таргарієн", "The Crownlands",
            "Courtier", "Valyrian Descent",
            apply_heritage=True,
        )
        # ability_scores must match between manual two-phase and one-shot
        assert profile_manual["ability_scores"] == profile_auto["ability_scores"], (
            "Two-phase (False + manual apply) must produce same ability_scores as one-shot (True)."
        )

    def test_andal_heritage_false_scores_in_range(self):
        """Westerosi (Andal) with apply_heritage=False: base scores [8, 15]."""
        profile = _build_deterministic_dnd_profile(
            "Тест", "Ланністер", "The Westerlands",
            "Knight", "Westerosi (Andal)",
            apply_heritage=False,
        )
        for ab, val in profile["ability_scores"].items():
            assert 8 <= val <= 15, f"{ab}={val} outside [8,15] for Andal no-heritage profile"

    def test_first_men_heritage_false_no_wisdom_con_bonus(self):
        """
        First Men (Stark line): WIS+2 CON+1 must NOT be applied when apply_heritage=False.

        Knight primary_abilities = [STR, CHA]:
          ordered_slots = [STR, CHA, DEX, CON, INT, WIS] (primaries first, then rest in order)
          standard array [15, 14, 13, 12, 10, 8] distributed:
            STR=15, CHA=14, DEX=13, CON=12, INT=10, WIS=8.
          With apply_heritage=False: WIS=8 (no +2), CON=12 (no +1).
        """
        profile = _build_deterministic_dnd_profile(
            "Тест", "Старк", "The North",
            "Knight", "First Men (Stark line)",
            apply_heritage=False,
        )
        scores = profile["ability_scores"]
        assert scores["WIS"] == 8, (
            f"apply_heritage=False: WIS should be 8 (base), got {scores['WIS']}"
        )
        assert scores["CON"] == 12, (
            f"apply_heritage=False: CON should be 12 (base standard array slot 3), got {scores['CON']}"
        )

    def test_all_heritages_false_scores_in_base_range(self):
        """For all 6 heritages, apply_heritage=False keeps scores in [8,15]."""
        heritages = [
            "Westerosi (Andal)", "Valyrian Descent", "First Men (Stark line)",
            "Free Folk", "Red Priest", "Ironborn",
        ]
        for heritage in heritages:
            profile = _build_deterministic_dnd_profile(
                "Тест", "Дім", "Вестерос",
                "Knight", heritage,
                apply_heritage=False,
            )
            for ab, val in profile["ability_scores"].items():
                assert 8 <= val <= 15, (
                    f"Heritage={heritage}, apply_heritage=False: "
                    f"{ab}={val} outside [8,15]"
                )


# ===========================================================================
# Section 7: recompute_ac() sanity regression
# ===========================================================================

class TestRecomputeAc:

    def _base_profile(self, dex: int) -> dict:
        """Minimal profile with no armor and specified DEX score."""
        return {
            "ability_scores": {
                "STR": 10, "DEX": dex, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "equipped_armor": None,
            "equipped_shield": False,
        }

    def test_no_armor_dex14_ac12(self):
        """No armor, DEX=14 → DEX mod = +2 → AC = 10 + 2 = 12."""
        profile = self._base_profile(dex=14)
        result = recompute_ac(profile)
        assert result == 12, f"Expected AC=12, got {result}"
        assert profile["ac"] == 12

    def test_no_armor_dex8_ac9(self):
        """No armor, DEX=8 → DEX mod = -1 → AC = 10 + (-1) = 9."""
        profile = self._base_profile(dex=8)
        result = recompute_ac(profile)
        assert result == 9, f"Expected AC=9, got {result}"
        assert profile["ac"] == 9

    def test_no_armor_dex10_ac10(self):
        """No armor, DEX=10 → DEX mod = 0 → AC = 10."""
        profile = self._base_profile(dex=10)
        result = recompute_ac(profile)
        assert result == 10

    def test_heavy_armor_no_dex_bonus(self):
        """Heavy armor (chainmail, AC 16): DEX mod not applied."""
        profile = self._base_profile(dex=14)
        profile["equipped_armor"] = {"ac_base": 16, "type": "heavy"}
        result = recompute_ac(profile)
        assert result == 16, f"Heavy armor: expected AC=16, got {result}"

    def test_medium_armor_dex_capped_at_2(self):
        """Medium armor (breastplate, AC 14): max DEX bonus +2."""
        profile = self._base_profile(dex=18)  # DEX mod = +4; capped to +2
        profile["equipped_armor"] = {"ac_base": 14, "type": "medium"}
        result = recompute_ac(profile)
        assert result == 16, f"Medium armor+DEX18: expected AC=16 (14+2), got {result}"

    def test_light_armor_full_dex_bonus(self):
        """Light armor (leather, AC 11): full DEX modifier applied."""
        profile = self._base_profile(dex=16)  # DEX mod = +3
        profile["equipped_armor"] = {"ac_base": 11, "type": "light"}
        result = recompute_ac(profile)
        assert result == 14, f"Light armor+DEX16: expected AC=14 (11+3), got {result}"

    def test_shield_adds_two(self):
        """Shield adds +2 regardless of armor type."""
        profile = self._base_profile(dex=10)
        profile["equipped_shield"] = True
        result = recompute_ac(profile)
        assert result == 12, f"Shield: expected AC=12 (10+2), got {result}"

    def test_mutates_profile_ac_field(self):
        """recompute_ac must write the new AC value to profile['ac']."""
        profile = self._base_profile(dex=12)
        profile["ac"] = 999  # stale value
        recompute_ac(profile)
        assert profile["ac"] == 11  # 10 + DEX mod(12) = 10 + 1


# ===========================================================================
# Section 8: POINT_BUY_COSTS table completeness
# ===========================================================================

class TestPointBuyCostsConstant:

    def test_costs_table_has_all_8_entries(self):
        """POINT_BUY_COSTS must have entries for exactly scores 8-15 (8 entries)."""
        assert set(POINT_BUY_COSTS.keys()) == {8, 9, 10, 11, 12, 13, 14, 15}

    def test_score_8_costs_zero(self):
        assert POINT_BUY_COSTS[8] == 0

    def test_score_15_costs_nine(self):
        assert POINT_BUY_COSTS[15] == 9

    def test_budget_is_27(self):
        assert POINT_BUY_BUDGET == 27

    def test_min_base_is_8(self):
        assert POINT_BUY_MIN_BASE == 8

    def test_max_base_is_15(self):
        assert POINT_BUY_MAX_BASE == 15

    def test_ability_keys_tuple_has_six_entries(self):
        assert len(ABILITY_KEYS) == 6

    def test_ability_keys_match_dnd_abilities(self):
        assert set(ABILITY_KEYS) == {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
