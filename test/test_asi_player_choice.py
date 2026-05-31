"""Unit tests for ASI player-choice flow (4-fix package, 2026-05).

Covers:
- apply_pending_levelups at L4 milestone: leaves asi_pending=True, does NOT
  auto-distribute ability scores, logs the pending message.
- apply_pending_levelups at non-ASI level (L5->L6): no ASI log line.
- apply_asi valid usages: {STR:2}, {CHA:1,INT:1} — mutate scores, clear flag.
- apply_asi cap enforcement: STR=20 + {STR:1,DEX:1} raises ValueError.
- apply_asi validation errors: sum>2, >2 abilities.
"""

import pytest

from core.dnd_progression import apply_asi, apply_pending_levelups


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _knight_profile(level: int = 1, **ability_overrides) -> dict:
    """Minimal Knight profile; ability_scores can be overridden by kwargs."""
    scores = {
        "STR": 16, "DEX": 10, "CON": 10,
        "INT": 10, "WIS": 10, "CHA": 14,
    }
    scores.update(ability_overrides)
    return {
        "class": "Knight",
        "level": level,
        "xp": 0,
        "hp_max": 10 + (level - 1) * 6,   # rough estimate, exact value irrelevant
        "hp_current": 10,
        "hit_dice_total": level,
        "hit_dice_used": 0,
        "ability_scores": scores,
        "features": [],
        "proficiency_bonus": 2,
        "asi_pending": False,
    }


def _courtier_profile(level: int = 1) -> dict:
    return {
        "class": "Courtier",
        "level": level,
        "xp": 0,
        "hp_max": 10 + (level - 1) * 4,
        "hp_current": 10,
        "hit_dice_total": level,
        "hit_dice_used": 0,
        "ability_scores": {
            "STR": 10, "DEX": 14, "CON": 10,
            "INT": 14, "WIS": 12, "CHA": 16,
        },
        "features": [],
        "proficiency_bonus": 2,
        "asi_pending": False,
    }


# ---------------------------------------------------------------------------
# 1. apply_pending_levelups at L4 milestone — MUST leave asi_pending=True,
#    MUST NOT auto-distribute ability scores, MUST log pending message.
# ---------------------------------------------------------------------------

def test_levelup_l4_leaves_asi_pending_true():
    """L3->L4: after apply_pending_levelups, profile['asi_pending'] must be True."""
    profile = _knight_profile(level=3)
    scores_before = dict(profile["ability_scores"])

    apply_pending_levelups(profile, level_before=3, levels_gained=1, class_name="Knight")

    assert profile["asi_pending"] is True, (
        "apply_pending_levelups at L4 must leave asi_pending=True (player must confirm via UI)"
    )


def test_levelup_l4_does_not_change_ability_scores():
    """L3->L4: ability_scores must be UNCHANGED — no auto-distribution."""
    profile = _knight_profile(level=3)
    scores_before = {k: v for k, v in profile["ability_scores"].items()}

    apply_pending_levelups(profile, level_before=3, levels_gained=1, class_name="Knight")

    assert profile["ability_scores"] == scores_before, (
        f"ability_scores must not change at L4 (no auto-ASI). "
        f"Before: {scores_before}, After: {profile['ability_scores']}"
    )


def test_levelup_l4_logs_pending_message():
    """L3->L4: logs must contain the UI-pending message."""
    profile = _knight_profile(level=3)

    logs = apply_pending_levelups(profile, level_before=3, levels_gained=1, class_name="Knight")

    pending_logs = [l for l in logs if "Ability Score Improvement" in l]
    assert len(pending_logs) >= 1, (
        f"Expected log line about pending ASI, got: {logs}"
    )
    # Must mention the level and UI action
    assert any("UI" in l or "кнопки" in l for l in pending_logs), (
        f"Pending log must mention UI/кнопки. Got: {pending_logs}"
    )


def test_levelup_l4_logs_correct_level_number():
    """The pending ASI UI-prompt log line must mention level 4.

    apply_pending_levelups emits two 'Ability Score Improvement' lines:
      1. 'New feature: Ability Score Improvement ...'  (class feature desc)
      2. '⚠️ Ability Score Improvement (рівень 4): оберіть ...'  (UI prompt)
    This test targets the second (pending/UI) line specifically.
    """
    profile = _knight_profile(level=3)

    logs = apply_pending_levelups(profile, level_before=3, levels_gained=1, class_name="Knight")

    # Find specifically the pending UI log line (contains 'UI' or 'кнопки')
    pending_log = next(
        (l for l in logs if "Ability Score Improvement" in l and ("UI" in l or "кнопки" in l)),
        None,
    )
    assert pending_log is not None, (
        f"Expected a pending-ASI UI-prompt log line. All logs: {logs}"
    )
    assert "4" in pending_log, (
        f"Pending ASI UI-prompt log must mention level 4. Got: {pending_log!r}"
    )


# ---------------------------------------------------------------------------
# 2. apply_pending_levelups at non-ASI level (L5->L6): no ASI, no log.
# ---------------------------------------------------------------------------

def test_levelup_l6_no_asi_pending():
    """L5->L6 is not an ASI level — asi_pending must remain False."""
    profile = _knight_profile(level=5)
    profile["asi_pending"] = False  # explicitly cleared

    apply_pending_levelups(profile, level_before=5, levels_gained=1, class_name="Knight")

    assert profile["asi_pending"] is False, (
        "L6 is not an ASI level — asi_pending must stay False"
    )


def test_levelup_l6_no_asi_log():
    """L5->L6: no 'Ability Score Improvement' message in logs."""
    profile = _knight_profile(level=5)

    logs = apply_pending_levelups(profile, level_before=5, levels_gained=1, class_name="Knight")

    asi_logs = [l for l in logs if "Ability Score Improvement" in l]
    assert asi_logs == [], (
        f"L6 is not an ASI level — expected no ASI log lines, got: {asi_logs}"
    )


# ---------------------------------------------------------------------------
# 3. apply_asi valid usages
# ---------------------------------------------------------------------------

def test_apply_asi_single_plus2_mutates_score():
    """apply_asi({STR:2}) raises STR by 2."""
    profile = _knight_profile(STR=14)
    profile["asi_pending"] = True

    apply_asi(profile, {"STR": 2})

    assert profile["ability_scores"]["STR"] == 16


def test_apply_asi_single_plus2_clears_pending():
    """apply_asi success must set asi_pending=False."""
    profile = _knight_profile(STR=14)
    profile["asi_pending"] = True

    apply_asi(profile, {"STR": 2})

    assert profile["asi_pending"] is False


def test_apply_asi_split_cha_int():
    """apply_asi({CHA:1, INT:1}) raises both CHA and INT by 1."""
    profile = _courtier_profile()
    profile["asi_pending"] = True
    cha_before = profile["ability_scores"]["CHA"]  # 16
    int_before = profile["ability_scores"]["INT"]  # 14

    apply_asi(profile, {"CHA": 1, "INT": 1})

    assert profile["ability_scores"]["CHA"] == cha_before + 1
    assert profile["ability_scores"]["INT"] == int_before + 1
    assert profile["asi_pending"] is False


def test_apply_asi_other_abilities_unchanged():
    """apply_asi({STR:2}) must NOT change DEX, CON, INT, WIS, CHA."""
    profile = _knight_profile()
    profile["asi_pending"] = True
    before = {k: v for k, v in profile["ability_scores"].items()}

    apply_asi(profile, {"STR": 2})

    for ab in ["DEX", "CON", "INT", "WIS", "CHA"]:
        assert profile["ability_scores"][ab] == before[ab], (
            f"{ab} must not change when only STR was improved"
        )


def test_apply_asi_exactly_at_cap_allowed():
    """STR=18, +2 -> 20 == cap -> OK (not capped over)."""
    profile = _knight_profile(STR=18)
    profile["asi_pending"] = True

    apply_asi(profile, {"STR": 2})

    assert profile["ability_scores"]["STR"] == 20


# ---------------------------------------------------------------------------
# 4. apply_asi cap enforcement: STR=20 + {STR:1, DEX:1} raises ValueError
# ---------------------------------------------------------------------------

def test_apply_asi_at_cap_str20_raises():
    """STR=20, {STR:1,DEX:1} -> STR would become 21 -> raises ValueError."""
    profile = _knight_profile(STR=20)
    profile["asi_pending"] = True

    with pytest.raises(ValueError, match="cap"):
        apply_asi(profile, {"STR": 1, "DEX": 1})


def test_apply_asi_at_cap_does_not_partially_apply():
    """When ValueError is raised, ability scores must not be partially mutated."""
    profile = _knight_profile(STR=20, DEX=10)
    profile["asi_pending"] = True
    dex_before = profile["ability_scores"]["DEX"]

    with pytest.raises(ValueError):
        apply_asi(profile, {"STR": 1, "DEX": 1})

    # DEX must remain unchanged — cap validation runs before apply
    assert profile["ability_scores"]["DEX"] == dex_before, (
        "apply_asi must not partially mutate ability_scores when validation fails"
    )


# ---------------------------------------------------------------------------
# 5. apply_asi validation errors: sum>2, 3 abilities, negative delta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("choices,reason", [
    ({"STR": 3},               "sum=3 single ability"),
    ({"STR": 1, "DEX": 2},    "sum=3 split"),
    ({"STR": 1, "DEX": 1, "CON": 1}, "3 abilities"),
])
def test_apply_asi_invalid_choices_raises(choices, reason):
    """Choices that violate sum==2 or >2-ability rules raise ValueError."""
    profile = _knight_profile()
    profile["asi_pending"] = True

    with pytest.raises(ValueError):
        apply_asi(profile, choices)
