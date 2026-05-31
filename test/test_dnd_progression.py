"""Unit tests for core/dnd_progression.py (Phase 3 QA).

Coverage:
- XP_TABLE structure and known values
- xp_required_for_level: valid inputs, boundary errors
- get_level_for_xp: threshold boundary probes, edge cases
- award_xp: profile mutation, level-up detection, multi-level grants
- level_up: HP calculation with hp_roll override, hit dice pool, features,
            prof bonus change, ASI flag (L4, L5 Knight)
- apply_asi: valid splits, invalid key, wrong sum, negative delta, cap violation
- xp_for_encounter_difficulty: known table values, invalid difficulty, clamped level
"""

import pytest

from core.dnd_progression import (
    XP_TABLE,
    MAX_LEVEL,
    XPAwardResult,
    LevelUpResult,
    xp_required_for_level,
    get_level_for_xp,
    award_xp,
    level_up,
    apply_asi,
    apply_pending_levelups,
    xp_for_encounter_difficulty,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _knight_profile(level: int = 1, xp: int = 0, con: int = 10) -> dict:
    """Minimal Knight profile for level_up tests."""
    return {
        "class": "Knight",
        "level": level,
        "xp": xp,
        "hp_max": 10,      # Knight d10, CON 10 → +0 mod → 10 at L1
        "hp_current": 10,
        "hit_dice_total": level,
        "hit_dice_used": 0,
        "ability_scores": {
            "STR": 16, "DEX": 10, "CON": con,
            "INT": 10, "WIS": 10, "CHA": 14,
        },
        "features": [],
        "proficiency_bonus": 2,
        "asi_pending": False,
    }


# ---------------------------------------------------------------------------
# 1. XP_TABLE structure
# ---------------------------------------------------------------------------

def test_xp_table_has_20_entries():
    assert len(XP_TABLE) == 20


def test_xp_table_level_1_is_zero():
    assert XP_TABLE[1] == 0


def test_xp_table_level_2():
    assert XP_TABLE[2] == 300


def test_xp_table_level_5():
    assert XP_TABLE[5] == 6_500


def test_xp_table_level_20():
    assert XP_TABLE[20] == 355_000


def test_xp_table_monotonically_increasing():
    values = [XP_TABLE[lvl] for lvl in range(1, 21)]
    for i in range(len(values) - 1):
        assert values[i] < values[i + 1], (
            f"XP_TABLE not monotone at L{i+1}→L{i+2}: "
            f"{values[i]} >= {values[i+1]}"
        )


# ---------------------------------------------------------------------------
# 2. xp_required_for_level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level,expected", [
    (1, 0),
    (5, 6_500),
    (20, 355_000),
])
def test_xp_required_for_level_known_values(level, expected):
    assert xp_required_for_level(level) == expected


def test_xp_required_for_level_0_raises():
    with pytest.raises(ValueError):
        xp_required_for_level(0)


def test_xp_required_for_level_21_raises():
    with pytest.raises(ValueError):
        xp_required_for_level(21)


# ---------------------------------------------------------------------------
# 3. get_level_for_xp
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("xp,expected_level", [
    (0, 1),
    (299, 1),
    (300, 2),
    (899, 2),
    (900, 3),
    (1_500, 3),
    (6_500, 5),
    (355_000, 20),
    (400_000, 20),   # above cap → clamped to 20
    (-100, 1),       # negative → level 1
])
def test_get_level_for_xp(xp, expected_level):
    assert get_level_for_xp(xp) == expected_level


# ---------------------------------------------------------------------------
# 4. award_xp
# ---------------------------------------------------------------------------

def test_award_xp_single_level_up():
    profile = {"xp": 0, "level": 1}
    result = award_xp(profile, 400)

    assert isinstance(result, XPAwardResult)
    assert result.xp_before == 0
    assert result.xp_after == 400
    assert result.level_before == 1
    assert result.level_after == 2
    assert result.leveled_up is True
    assert result.levels_gained == 1

    # Profile is mutated in-place
    assert profile["xp"] == 400
    assert profile["level"] == 2


def test_award_xp_big_grant_gains_multiple_levels():
    profile = {"xp": 0, "level": 1}
    result = award_xp(profile, 10_000)
    # At 10_000 XP the character is L5 (XP_TABLE[5]=6500, XP_TABLE[6]=14000)
    assert result.levels_gained >= 4


def test_award_xp_zero_amount_no_level_up():
    profile = {"xp": 0, "level": 1}
    result = award_xp(profile, 0)
    assert result.leveled_up is False
    assert result.levels_gained == 0
    assert profile["xp"] == 0
    assert profile["level"] == 1


def test_award_xp_negative_treated_as_zero():
    profile = {"xp": 100, "level": 1}
    result = award_xp(profile, -50)
    assert result.xp_after == 100
    assert result.levels_gained == 0


def test_award_xp_reason_does_not_affect_math():
    p1 = {"xp": 0, "level": 1}
    p2 = {"xp": 0, "level": 1}
    r1 = award_xp(p1, 500, reason="")
    r2 = award_xp(p2, 500, reason="Defeated an enemy")
    assert r1.xp_after == r2.xp_after
    assert r1.level_after == r2.level_after


# ---------------------------------------------------------------------------
# 5. level_up
# ---------------------------------------------------------------------------

def test_level_up_knight_l1_to_l2_hp_increase():
    """hp_roll=8, CON=10 → CON_mod=0 → hp_gained=8 → hp_max=18."""
    profile = _knight_profile(level=1)
    result = level_up(profile, "Knight", hp_roll=8)

    assert isinstance(result, LevelUpResult)
    assert result.new_level == 2
    assert result.hp_gained == 8          # rolled 8 + CON_mod 0
    assert profile["hp_max"] == 18        # 10 + 8
    assert profile["level"] == 2


def test_level_up_hit_dice_total_increments():
    profile = _knight_profile(level=1)
    level_up(profile, "Knight", hp_roll=5)
    assert profile["hit_dice_total"] == 2


def test_level_up_features_extended():
    """L1→L2 Knight gets Fighting Style feature."""
    profile = _knight_profile(level=1)
    result = level_up(profile, "Knight", hp_roll=5)
    assert len(result.new_features) > 0
    assert any(f.name == "Fighting Style" for f in result.new_features)


def test_level_up_l4_knight_asi_pending_true():
    """At L4, ASI flag must be set True."""
    profile = _knight_profile(level=3)
    result = level_up(profile, "Knight", hp_roll=5)
    assert result.asi_pending is True
    assert profile["asi_pending"] is True


def test_level_up_l4_knight_prof_bonus_still_2():
    """L1-4 all have prof_bonus=2; no change at L4."""
    profile = _knight_profile(level=3)
    result = level_up(profile, "Knight", hp_roll=5)
    assert result.proficiency_bonus_changed is False
    assert profile["proficiency_bonus"] == 2


def test_level_up_l5_knight_asi_pending_false():
    """L5 is not an ASI level → asi_pending=False."""
    profile = _knight_profile(level=4)
    # L4 is ASI level; first mark it pending, then level up to L5
    profile["asi_pending"] = True
    result = level_up(profile, "Knight", hp_roll=5)
    assert result.new_level == 5
    assert result.asi_pending is False
    assert profile["asi_pending"] is False


def test_level_up_l5_knight_prof_bonus_increases():
    """L5 pushes prof_bonus from 2 to 3."""
    profile = _knight_profile(level=4)
    result = level_up(profile, "Knight", hp_roll=5)
    assert result.proficiency_bonus_changed is True
    assert profile["proficiency_bonus"] == 3


def test_level_up_l5_knight_extra_attack_feature():
    profile = _knight_profile(level=4)
    result = level_up(profile, "Knight", hp_roll=5)
    assert any(f.name == "Extra Attack" for f in result.new_features)


def test_level_up_hp_roll_override_deterministic():
    """hp_roll=8, CON=14 → CON_mod=2 → hp_gained=10."""
    profile = _knight_profile(level=1, con=14)
    result = level_up(profile, "Knight", hp_roll=8)
    assert result.hp_gained == 10        # 8 + 2
    assert profile["hp_max"] == 20       # 10 + 10


def test_level_up_hp_min_1_when_roll_plus_con_negative():
    """CON=1 → CON_mod=-5; hp_roll=1 → rolled+mod=-4 → hp_gained=max(1,...)=1."""
    profile = _knight_profile(level=1, con=1)
    result = level_up(profile, "Knight", hp_roll=1)
    assert result.hp_gained == 1


def test_level_up_at_max_level_raises():
    profile = _knight_profile(level=20)
    with pytest.raises(ValueError):
        level_up(profile, "Knight", hp_roll=5)


def test_level_up_unknown_class_raises():
    profile = _knight_profile(level=1)
    with pytest.raises(KeyError):
        level_up(profile, "InvalidClass", hp_roll=5)


# ---------------------------------------------------------------------------
# 6. apply_asi
# ---------------------------------------------------------------------------

def _profile_with_str(str_val: int) -> dict:
    return {
        "ability_scores": {
            "STR": str_val, "DEX": 10, "CON": 10,
            "INT": 10, "WIS": 10, "CHA": 10,
        },
        "asi_pending": True,
    }


def test_apply_asi_single_ability_plus2():
    profile = _profile_with_str(16)
    apply_asi(profile, {"STR": 2})
    assert profile["ability_scores"]["STR"] == 18


def test_apply_asi_split_two_abilities():
    profile = {
        "ability_scores": {
            "STR": 14, "DEX": 12, "CON": 10,
            "INT": 10, "WIS": 10, "CHA": 10,
        },
        "asi_pending": True,
    }
    apply_asi(profile, {"STR": 1, "DEX": 1})
    assert profile["ability_scores"]["STR"] == 15
    assert profile["ability_scores"]["DEX"] == 13


def test_apply_asi_clears_pending_flag():
    profile = _profile_with_str(14)
    apply_asi(profile, {"STR": 2})
    assert profile["asi_pending"] is False


def test_apply_asi_sum_3_raises():
    profile = _profile_with_str(14)
    with pytest.raises(ValueError):
        apply_asi(profile, {"STR": 3})


def test_apply_asi_sum_mixed_raises():
    profile = _profile_with_str(14)
    with pytest.raises(ValueError):
        apply_asi(profile, {"STR": 2, "DEX": 1})   # sum=3


def test_apply_asi_negative_delta_raises():
    profile = _profile_with_str(14)
    with pytest.raises(ValueError):
        apply_asi(profile, {"STR": -1, "DEX": 3})


def test_apply_asi_invalid_key_raises():
    profile = _profile_with_str(14)
    with pytest.raises(ValueError):
        apply_asi(profile, {"XYZ": 2})


def test_apply_asi_cap_violation_raises():
    """STR=19, +2 → 21 > 20 cap → ValueError."""
    profile = _profile_with_str(19)
    with pytest.raises(ValueError):
        apply_asi(profile, {"STR": 2})


def test_apply_asi_exactly_at_cap_allowed():
    """STR=18, +2 → 20 == cap → OK."""
    profile = _profile_with_str(18)
    apply_asi(profile, {"STR": 2})
    assert profile["ability_scores"]["STR"] == 20


# ---------------------------------------------------------------------------
# 7. xp_for_encounter_difficulty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level,difficulty,expected", [
    (1,  "medium", 50),
    (5,  "medium", 500),
    (10, "deadly", 2_800),
    (1,  "easy",   25),
])
def test_xp_for_encounter_difficulty_known_values(level, difficulty, expected):
    assert xp_for_encounter_difficulty(level, difficulty) == expected


def test_xp_for_encounter_difficulty_invalid_raises():
    with pytest.raises(ValueError):
        xp_for_encounter_difficulty(1, "invalid_difficulty")


def test_xp_for_encounter_difficulty_level_25_clamped_to_20():
    """Level 25 is clamped to 20 — same result as level 20."""
    result_25 = xp_for_encounter_difficulty(25, "medium")
    result_20 = xp_for_encounter_difficulty(20, "medium")
    assert result_25 == result_20


# ---------------------------------------------------------------------------
# 8. apply_pending_levelups (training path integration)
# ---------------------------------------------------------------------------

def _dnd_profile_with_hp(level: int = 1, hp_max: int = 10, class_name: str = "Courtier") -> dict:
    """Minimal profile simulating a character after award_xp() was called."""
    return {
        "class": class_name,
        "level": level,       # apply_pending_levelups will reset this to level_before
        "xp": 0,
        "hp_max": hp_max,
        "hp_current": hp_max,
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


def test_apply_pending_levelups_training_path_hp_increases():
    """Training XP triggers level_up — hp_max must increase after apply_pending_levelups."""
    profile = _dnd_profile_with_hp(level=2, hp_max=10)
    # Simulate: award_xp already set profile['level'] = 2 (level_before=1, levels_gained=1)
    logs = apply_pending_levelups(profile, level_before=1, levels_gained=1, class_name="Courtier")

    assert profile["level"] == 2
    assert profile["hp_max"] > 10, "hp_max must have increased after level_up"
    assert any("HP +" in log for log in logs), f"Expected HP log, got: {logs}"


def test_apply_pending_levelups_multi_level_gain():
    """Crossing two levels at once (e.g. L1 → L3 from training)."""
    profile = _dnd_profile_with_hp(level=3, hp_max=10)
    logs = apply_pending_levelups(profile, level_before=1, levels_gained=2, class_name="Courtier")

    assert profile["level"] == 3
    assert profile["hp_max"] > 10
    # Should have two HP log lines
    hp_logs = [l for l in logs if "HP +" in l]
    assert len(hp_logs) == 2, f"Expected 2 HP log lines for 2 levels, got: {hp_logs}"


def test_apply_pending_levelups_zero_levels_noop():
    """levels_gained=0 must be a no-op — profile and logs unchanged."""
    profile = _dnd_profile_with_hp(level=1, hp_max=10)
    original_level = profile["level"]
    original_hp = profile["hp_max"]

    logs = apply_pending_levelups(profile, level_before=1, levels_gained=0, class_name="Knight")

    assert logs == []
    assert profile["level"] == original_level
    assert profile["hp_max"] == original_hp


def test_apply_pending_levelups_unknown_class_fallback():
    """Unknown class falls back to Knight without raising."""
    profile = _dnd_profile_with_hp(level=2, hp_max=10, class_name="UnknownClass")
    logs = apply_pending_levelups(profile, level_before=1, levels_gained=1, class_name="UnknownClass")

    # Should not raise; Knight fallback applied
    assert profile["level"] == 2
    assert profile["hp_max"] > 10


def test_apply_pending_levelups_asi_at_l4():
    """L3 → L4: ASI must NOT be auto-distributed (4-fix package change).

    New invariant (2026-05): apply_pending_levelups leaves asi_pending=True
    and does NOT mutate ability_scores. The player confirms via UI.
    """
    profile = _dnd_profile_with_hp(level=4, hp_max=22, class_name="Courtier")
    cha_before = profile["ability_scores"]["CHA"]  # 16
    int_before = profile["ability_scores"]["INT"]  # 14

    logs = apply_pending_levelups(profile, level_before=3, levels_gained=1, class_name="Courtier")

    assert profile["level"] == 4

    # NEW INVARIANT: ability_scores must NOT have changed (no auto-ASI)
    cha_after = profile["ability_scores"]["CHA"]
    int_after = profile["ability_scores"]["INT"]
    assert cha_after == cha_before, (
        f"CHA must NOT change at L4 (ASI is now player-confirmed). "
        f"Before: {cha_before}, After: {cha_after}"
    )
    assert int_after == int_before, (
        f"INT must NOT change at L4 (ASI is now player-confirmed). "
        f"Before: {int_before}, After: {int_after}"
    )

    # NEW INVARIANT: asi_pending must be True (waiting for UI confirmation)
    assert profile["asi_pending"] is True, (
        "asi_pending must be True after L4 level-up — player must confirm via UI"
    )

    # Log must mention pending ASI
    assert any("Ability Score Improvement" in log for log in logs)


def test_features_stored_as_json_serializable_dicts():
    """Bug fix: profile['features'] must contain dicts, not Feature dataclass objects.
    json.dumps must work on profile after level-up."""
    import json

    profile = _dnd_profile_with_hp()
    profile["level"] = 1
    profile["class"] = "Courtier"

    apply_pending_levelups(profile, level_before=1, levels_gained=2, class_name="Courtier")

    # Must have features after level-up (Courtier L1+L2)
    assert len(profile.get("features", [])) > 0

    # Each feature must be a dict with required keys
    for feat in profile["features"]:
        assert isinstance(feat, dict), f"Feature must be dict, got {type(feat)}"
        assert "name" in feat
        assert "desc" in feat
        assert "source" in feat

    # CRITICAL: json.dumps must not raise TypeError
    try:
        json.dumps(profile)
    except TypeError as exc:
        pytest.fail(f"profile must be JSON-serializable after level-up: {exc}")
