"""Unit tests for core/dnd_rest.py (Phase 3 QA).

Coverage:
- can_short_rest: HP=0, no hit dice, happy path
- can_long_rest: HP=0, happy path
- short_rest: HP restoration math (mocked random), cap at hp_max,
              clamping when hit_dice_spent > available
- long_rest: full HP restore, hit dice regain formula, condition clearing
             (poisoned removed, petrified NOT removed)
- RestResult fields populated correctly for both rest types
"""

import pytest
from unittest.mock import patch

from core.dnd_rest import (
    can_short_rest,
    can_long_rest,
    short_rest,
    long_rest,
    RestResult,
    _LONG_REST_CLEARED_CONDITIONS,
    _SHORT_REST_DURATION_MINUTES,
    _LONG_REST_DURATION_MINUTES,
)
from core.dnd_conditions import apply_condition, has_condition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _knight_profile(
    hp_current: int = 20,
    hp_max: int = 20,
    hit_dice_total: int = 3,
    hit_dice_used: int = 0,
    con: int = 14,   # CON_mod = +2
) -> dict:
    return {
        "class": "Knight",
        "hp_current": hp_current,
        "hp_max": hp_max,
        "hit_dice_total": hit_dice_total,
        "hit_dice_used": hit_dice_used,
        "ability_scores": {
            "STR": 16, "DEX": 10, "CON": con,
            "INT": 10, "WIS": 10, "CHA": 14,
        },
        "conditions": [],
    }


# ---------------------------------------------------------------------------
# 1. can_short_rest
# ---------------------------------------------------------------------------

def test_can_short_rest_happy_path():
    profile = _knight_profile(hp_current=5, hit_dice_total=3, hit_dice_used=0)
    ok, reason = can_short_rest(profile)
    assert ok is True
    assert reason == ""


def test_can_short_rest_hp_zero_returns_false():
    profile = _knight_profile(hp_current=0)
    ok, reason = can_short_rest(profile)
    assert ok is False
    assert reason != ""


def test_can_short_rest_all_hit_dice_used_returns_false():
    profile = _knight_profile(hit_dice_total=3, hit_dice_used=3)
    ok, reason = can_short_rest(profile)
    assert ok is False
    assert reason != ""


# ---------------------------------------------------------------------------
# 2. can_long_rest
# ---------------------------------------------------------------------------

def test_can_long_rest_happy_path():
    profile = _knight_profile(hp_current=5)
    ok, reason = can_long_rest(profile)
    assert ok is True
    assert reason == ""


def test_can_long_rest_hp_zero_returns_false():
    profile = _knight_profile(hp_current=0)
    ok, reason = can_long_rest(profile)
    assert ok is False
    assert reason != ""


# ---------------------------------------------------------------------------
# 3. short_rest
# ---------------------------------------------------------------------------

def test_short_rest_hp_restoration_math():
    """Knight (d10, CON=14 → CON_mod=2), spend 2 HD, mock roll=5 each.
    Each die: gained=max(1, 5+2)=7
    hp_deficit = 20-8 = 12
    die 1: actual_gain=min(7, 12-0)=7  total=7
    die 2: actual_gain=min(7, 12-7)=5  total=12
    → hp_current=8+12=20, hit_dice_used=2
    """
    profile = _knight_profile(
        hp_current=8, hp_max=20,
        hit_dice_total=3, hit_dice_used=0,
        con=14,
    )
    with patch("core.dnd_rest._roll_die", return_value=5):
        result = short_rest(profile, hit_dice_spent=2)

    assert profile["hp_current"] == 20
    assert profile["hit_dice_used"] == 2
    assert result.hp_restored == 12


def test_short_rest_result_type_and_fields():
    profile = _knight_profile(hp_current=10, hp_max=20, hit_dice_total=3, hit_dice_used=0)
    with patch("core.dnd_rest._roll_die", return_value=5):
        result = short_rest(profile, hit_dice_spent=1)

    assert isinstance(result, RestResult)
    assert result.rest_type == "short"
    assert result.duration_minutes == _SHORT_REST_DURATION_MINUTES
    assert result.hit_dice_regained == 0
    assert result.conditions_cleared == []
    assert result.hp_restored >= 0


def test_short_rest_cap_at_hp_max():
    """Starting at hp_max — no HP should be restored."""
    profile = _knight_profile(hp_current=20, hp_max=20, hit_dice_total=3, hit_dice_used=0)
    with patch("core.dnd_rest._roll_die", return_value=10):
        result = short_rest(profile, hit_dice_spent=2)

    assert profile["hp_current"] == 20
    assert result.hp_restored == 0


def test_short_rest_requested_more_dice_than_available():
    """Requesting 5 HD when only 2 available → clamped to 2, no error."""
    profile = _knight_profile(
        hp_current=5, hp_max=20,
        hit_dice_total=3, hit_dice_used=1,  # 2 available
    )
    with patch("core.dnd_rest._roll_die", return_value=5):
        result = short_rest(profile, hit_dice_spent=5)

    assert result.hit_dice_spent <= 2
    assert profile["hit_dice_used"] <= 3   # never exceeds total


def test_short_rest_zero_dice_spent_restores_nothing():
    profile = _knight_profile(hp_current=10, hp_max=20, hit_dice_total=3, hit_dice_used=0)
    result = short_rest(profile, hit_dice_spent=0)
    assert result.hp_restored == 0
    assert profile["hp_current"] == 10


# ---------------------------------------------------------------------------
# 4. long_rest
# ---------------------------------------------------------------------------

def test_long_rest_full_hp_restored():
    profile = _knight_profile(hp_current=3, hp_max=20, hit_dice_total=4, hit_dice_used=3)
    long_rest(profile)
    assert profile["hp_current"] == 20


def test_long_rest_hit_dice_regained_formula():
    """hit_dice_total=4, hit_dice_used=3 → regain=max(1, 4//2)=2 → used=max(0,3-2)=1."""
    profile = _knight_profile(hp_current=3, hp_max=20, hit_dice_total=4, hit_dice_used=3)
    result = long_rest(profile)
    assert result.hit_dice_regained == 2
    assert profile["hit_dice_used"] == 1


def test_long_rest_poisoned_condition_cleared():
    profile = _knight_profile(hp_current=5, hp_max=20, hit_dice_total=4, hit_dice_used=0)
    apply_condition(profile, "poisoned", duration_rounds=-1, source="test")
    assert has_condition(profile, "poisoned")

    result = long_rest(profile)

    assert not has_condition(profile, "poisoned")
    assert "poisoned" in result.conditions_cleared


def test_long_rest_petrified_condition_not_cleared():
    """petrified is magical — NOT in _LONG_REST_CLEARED_CONDITIONS."""
    profile = _knight_profile(hp_current=5, hp_max=20, hit_dice_total=4, hit_dice_used=0)
    apply_condition(profile, "petrified", duration_rounds=-1, source="test")
    assert has_condition(profile, "petrified")

    result = long_rest(profile)

    assert has_condition(profile, "petrified")
    assert "petrified" not in result.conditions_cleared


def test_long_rest_mixed_conditions():
    """Both poisoned and petrified present — only poisoned cleared."""
    profile = _knight_profile(hp_current=5, hp_max=20, hit_dice_total=4, hit_dice_used=0)
    apply_condition(profile, "poisoned", duration_rounds=-1, source="test")
    apply_condition(profile, "petrified", duration_rounds=-1, source="test")

    result = long_rest(profile)

    assert "poisoned" in result.conditions_cleared
    assert "petrified" not in result.conditions_cleared
    assert not has_condition(profile, "poisoned")
    assert has_condition(profile, "petrified")


def test_long_rest_no_hit_dice_used_regains_zero():
    """hit_dice_used=0 → nothing to regain → actual_regained=0."""
    profile = _knight_profile(hp_current=10, hp_max=20, hit_dice_total=4, hit_dice_used=0)
    result = long_rest(profile)
    assert result.hit_dice_regained == 0
    assert profile["hit_dice_used"] == 0


def test_long_rest_result_type_and_fields():
    profile = _knight_profile(hp_current=3, hp_max=20, hit_dice_total=4, hit_dice_used=2)
    result = long_rest(profile)

    assert isinstance(result, RestResult)
    assert result.rest_type == "long"
    assert result.duration_minutes == _LONG_REST_DURATION_MINUTES
    assert result.hit_dice_spent == 0
    assert result.hp_restored >= 0
    assert isinstance(result.conditions_cleared, list)
    assert isinstance(result.log, str)
    assert len(result.log) > 0
