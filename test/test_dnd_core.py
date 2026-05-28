"""Unit tests for core/dnd_core.py and core/dnd_skills.py (Фаза 1 QA)."""

import pytest
from unittest.mock import patch

from core.dnd_core import (
    ability_modifier,
    proficiency_bonus,
    clamp_dc,
    roll_d20,
    ability_check,
    skill_check,
    saving_throw,
    CheckResult,
    LEGAL_DCS,
    score_to_relation_text,
    map_relation_to_score,
    RELATION_TO_SCORE,
)
from core.dnd_skills import SKILLS, is_valid_skill, get_ability_for_skill


# ---------------------------------------------------------------------------
# ability_modifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected_mod", [
    (10, 0),
    (11, 0),
    (8, -1),
    (18, 4),
    (20, 5),
    (1, -5),
    (3, -4),
])
def test_ability_modifier_standard_values(score, expected_mod):
    assert ability_modifier(score) == expected_mod


def test_ability_modifier_score_10_gives_zero():
    assert ability_modifier(10) == 0


def test_ability_modifier_score_11_gives_zero():
    assert ability_modifier(11) == 0


def test_ability_modifier_score_8_gives_minus_one():
    assert ability_modifier(8) == -1


def test_ability_modifier_score_18_gives_plus_four():
    assert ability_modifier(18) == 4


def test_ability_modifier_score_20_gives_plus_five():
    assert ability_modifier(20) == 5


def test_ability_modifier_score_1_gives_minus_five():
    assert ability_modifier(1) == -5


def test_ability_modifier_score_3_gives_minus_four():
    assert ability_modifier(3) == -4


def test_ability_modifier_clamp_low_score_not_below_minus_five():
    # score=1 is the minimum in D&D; clamp protects from any lower value
    result = ability_modifier(1)
    assert result >= -5


def test_ability_modifier_clamp_high_score_not_above_ten():
    # score=30 would be (30-10)//2 = 10 — max allowed
    result = ability_modifier(30)
    assert result <= 10


# ---------------------------------------------------------------------------
# proficiency_bonus
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level,expected_pb", [
    (1, 2),
    (4, 2),
    (5, 3),
    (8, 3),
    (9, 4),
    (12, 4),
    (13, 5),
    (16, 5),
    (17, 6),
    (20, 6),
])
def test_proficiency_bonus_standard_levels(level, expected_pb):
    assert proficiency_bonus(level) == expected_pb


def test_proficiency_bonus_level_zero_fallback_gives_two():
    # L0 is invalid: code clamps it to L1 silently → pb=2
    assert proficiency_bonus(0) == 2


def test_proficiency_bonus_level_twenty_one_fallback_gives_six():
    # L21 is over cap: code clamps to L20 silently → pb=6
    assert proficiency_bonus(21) == 6


def test_proficiency_bonus_does_not_raise_for_zero():
    # Must not raise — silently corrected fallback is the contract
    try:
        proficiency_bonus(0)
    except Exception as exc:
        pytest.fail(f"proficiency_bonus(0) raised unexpectedly: {exc}")


def test_proficiency_bonus_does_not_raise_for_negative():
    try:
        proficiency_bonus(-5)
    except Exception as exc:
        pytest.fail(f"proficiency_bonus(-5) raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# clamp_dc
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", LEGAL_DCS)
def test_clamp_dc_legal_values_returned_unchanged(value):
    assert clamp_dc(value) == value


def test_clamp_dc_below_minimum_returns_two():
    # After DC=2 added to LEGAL_DCS, 3 clamps to nearest lower legal = 2
    assert clamp_dc(3) == 2


def test_clamp_dc_zero_or_negative_returns_two():
    # Values <= 2 clamp to LEGAL_DCS[0] = 2
    assert clamp_dc(1) == 2
    assert clamp_dc(0) == 2
    assert clamp_dc(-5) == 2


def test_clamp_dc_above_maximum_returns_twentytwo():
    assert clamp_dc(50) == 22


def test_clamp_dc_thirteen_returns_twelve_nearest_lower():
    # 13 is equidistant between 12 and 15 (dist=1 vs dist=2) — nearest is 12
    assert clamp_dc(13) == 12


def test_clamp_dc_fourteen_returns_fifteen():
    # 14 is closer to 15 (dist=1) than to 12 (dist=2)
    assert clamp_dc(14) == 15


def test_clamp_dc_eighteen_returns_seventeen():
    # 18 is closer to 17 (dist=1) than to 20 (dist=2)
    assert clamp_dc(18) == 17


def test_clamp_dc_result_always_in_legal_dcs():
    for value in range(-10, 60):
        assert clamp_dc(value) in LEGAL_DCS


# ---------------------------------------------------------------------------
# roll_d20
# ---------------------------------------------------------------------------

def test_roll_d20_normal_returns_single_roll():
    with patch("core.dnd_core.random.randint", return_value=15):
        result, raw = roll_d20()
    assert result == 15
    assert raw == [15]


def test_roll_d20_advantage_returns_max_of_two_rolls():
    with patch("core.dnd_core.random.randint", side_effect=[5, 18]):
        result, raw = roll_d20(advantage=True)
    assert result == 18
    assert raw == [5, 18]


def test_roll_d20_disadvantage_returns_min_of_two_rolls():
    with patch("core.dnd_core.random.randint", side_effect=[5, 18]):
        result, raw = roll_d20(disadvantage=True)
    assert result == 5
    assert raw == [5, 18]


def test_roll_d20_adv_and_dis_both_true_cancel_to_normal():
    # Both True → advantage=False, disadvantage=False → single roll
    with patch("core.dnd_core.random.randint", return_value=12) as mock_roll:
        result, raw = roll_d20(advantage=True, disadvantage=True)
    assert mock_roll.call_count == 1
    assert result == 12
    assert raw == [12]


def test_roll_d20_equal_rolls_under_advantage_returns_that_value():
    with patch("core.dnd_core.random.randint", side_effect=[12, 12]):
        result, raw = roll_d20(advantage=True)
    assert result == 12
    assert raw == [12, 12]


# ---------------------------------------------------------------------------
# CheckResult semantics: nat-1 / nat-20 override
# ---------------------------------------------------------------------------

def test_ability_check_nat_1_forces_failure_even_when_total_ge_dc():
    # nat 1, high CON mod + prof might push total ≥ DC but success must be False
    profile = {"ability_scores": {"STR": 30}, "level": 20}  # ab_mod=10, prof=6
    with patch("core.dnd_core.random.randint", return_value=1):
        cr = ability_check(profile, "STR", dc=5)
    assert cr.natural == 1
    assert cr.success is False
    assert cr.critical == "fail"


def test_ability_check_nat_20_forces_success_even_when_total_lt_dc():
    # nat 20, terrible mod, high DC — success must still be True
    profile = {"ability_scores": {"STR": 1}, "level": 1}  # ab_mod=-5
    with patch("core.dnd_core.random.randint", return_value=20):
        cr = ability_check(profile, "STR", dc=30)
    assert cr.natural == 20
    assert cr.success is True
    assert cr.critical == "success"


def test_ability_check_normal_hit_success_true():
    # nat 10, STR 14 (mod+2), dc 10 → total=12 ≥ dc → success
    profile = {"ability_scores": {"STR": 14}, "level": 1}
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = ability_check(profile, "STR", dc=10)
    assert cr.natural == 10
    assert cr.success is True
    assert cr.critical == "none"


def test_ability_check_normal_miss_success_false():
    # nat 10, STR 8 (mod-1), dc 12 → total=9 < dc → failure
    profile = {"ability_scores": {"STR": 8}, "level": 1}
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = ability_check(profile, "STR", dc=12)
    assert cr.natural == 10
    assert cr.success is False
    assert cr.critical == "none"


# ---------------------------------------------------------------------------
# ability_check — profile fallbacks and proficiency
# ---------------------------------------------------------------------------

def test_ability_check_missing_ability_scores_fallback_to_ten():
    # profile without ability_scores — treated as score=10 → mod=0
    profile = {"level": 5}
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = ability_check(profile, "STR", dc=10)
    assert cr.ability_mod == 0


def test_ability_check_missing_level_fallback_to_level_one():
    # profile without level — treated as L1, prof_bonus=2
    profile = {"ability_scores": {"STR": 16}}
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = ability_check(profile, "STR", dc=10, proficient=True)
    assert cr.prof_bonus == 2  # L1 → pb=2


def test_ability_check_proficiency_applied_correctly():
    # STR 16 (mod+3), L5 (pb=3), dc=15, nat=10 → total=10+3+3=16 ≥ 15 → success
    profile = {"ability_scores": {"STR": 16}, "level": 5}
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = ability_check(profile, "STR", dc=15, proficient=True)
    assert cr.ability_mod == 3
    assert cr.prof_bonus == 3
    assert cr.total == 16
    assert cr.success is True


def test_ability_check_no_proficiency_zero_prof_bonus():
    # STR 16 (mod+3), L5, dc=15, nat=10 → total=13 < 15 → fail
    profile = {"ability_scores": {"STR": 16}, "level": 5}
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = ability_check(profile, "STR", dc=15, proficient=False)
    assert cr.prof_bonus == 0
    assert cr.total == 13
    assert cr.success is False


# ---------------------------------------------------------------------------
# skill_check — proficiency / expertise
# ---------------------------------------------------------------------------

def test_skill_check_with_proficiency_applies_prof_bonus():
    profile = {
        "ability_scores": {"STR": 16},  # mod +3
        "level": 4,                      # pb = 2
        "skill_profs": ["Athletics"],
        "skill_expertise": [],
    }
    with patch("core.dnd_core.random.randint", return_value=8):
        cr = skill_check(profile, "Athletics", dc=15)
    # 8 + 3(STR) + 2(prof) = 13 < 15 → fail
    assert cr.prof_bonus == 2
    assert cr.total == 13
    assert cr.success is False


def test_skill_check_with_expertise_applies_double_prof_bonus():
    profile = {
        "ability_scores": {"STR": 16},  # mod +3
        "level": 4,                      # pb = 2
        "skill_profs": [],
        "skill_expertise": ["Athletics"],
    }
    with patch("core.dnd_core.random.randint", return_value=5):
        cr = skill_check(profile, "Athletics", dc=12)
    # 5 + 3 + 4(expertise=2*pb) = 12 ≥ 12 → success
    assert cr.prof_bonus == 4
    assert cr.total == 12
    assert cr.success is True


def test_skill_check_not_proficient_only_ability_mod():
    profile = {
        "ability_scores": {"DEX": 14},  # mod +2
        "level": 3,
        "skill_profs": [],
        "skill_expertise": [],
    }
    with patch("core.dnd_core.random.randint", return_value=10):
        cr = skill_check(profile, "Stealth", dc=12)
    # 10 + 2 = 12 ≥ 12 → success, prof=0
    assert cr.prof_bonus == 0
    assert cr.total == 12
    assert cr.success is True


def test_skill_check_invalid_skill_raises_key_error():
    profile = {"ability_scores": {"STR": 14}, "level": 1}
    with pytest.raises(KeyError):
        skill_check(profile, "Cooking", dc=10)


# ---------------------------------------------------------------------------
# saving_throw
# ---------------------------------------------------------------------------

def test_saving_throw_proficient_save_applies_prof_bonus():
    profile = {
        "ability_scores": {"CON": 14},  # mod +2
        "level": 5,                      # pb = 3
        "saves_proficient": ["CON"],
    }
    with patch("core.dnd_core.random.randint", return_value=7):
        cr = saving_throw(profile, "CON", dc=12)
    # 7 + 2 + 3 = 12 ≥ 12 → success
    assert cr.prof_bonus == 3
    assert cr.success is True


def test_saving_throw_not_proficient_no_prof_bonus():
    profile = {
        "ability_scores": {"CON": 14},  # mod +2
        "level": 5,
        "saves_proficient": [],
    }
    with patch("core.dnd_core.random.randint", return_value=7):
        cr = saving_throw(profile, "CON", dc=12)
    # 7 + 2 + 0 = 9 < 12 → fail
    assert cr.prof_bonus == 0
    assert cr.success is False


# ---------------------------------------------------------------------------
# SKILLS map (dnd_skills)
# ---------------------------------------------------------------------------

def test_skills_map_has_eighteen_entries():
    assert len(SKILLS) == 18


def test_skills_all_values_are_valid_abilities():
    valid = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
    for skill, ability in SKILLS.items():
        assert ability in valid, f"{skill} maps to invalid ability {ability}"


def test_skills_athletics_maps_to_str():
    assert SKILLS["Athletics"] == "STR"


def test_skills_insight_maps_to_wis():
    assert SKILLS["Insight"] == "WIS"


def test_skills_stealth_maps_to_dex():
    assert SKILLS["Stealth"] == "DEX"


def test_skills_persuasion_maps_to_cha():
    assert SKILLS["Persuasion"] == "CHA"


def test_is_valid_skill_returns_true_for_known_skill():
    assert is_valid_skill("Athletics") is True


def test_is_valid_skill_returns_false_for_unknown_skill():
    assert is_valid_skill("Cooking") is False


def test_get_ability_for_skill_raises_key_error_for_invalid():
    with pytest.raises(KeyError):
        get_ability_for_skill("Cooking")


# ---------------------------------------------------------------------------
# Regression guards: DC downgrade (25/28/30 removed, max = 22)
# ---------------------------------------------------------------------------

def test_legal_dcs_no_longer_contains_25_28_30():
    """Guard: тестовий зниження складності — DC 25/28/30 повинні бути недосяжні."""
    for dc in (25, 28, 30):
        assert dc not in LEGAL_DCS
    assert max(LEGAL_DCS) == 22


def test_clamp_dc_25_now_returns_22():
    """clamp_dc(25) перш повертав 25 (legal), тепер 22 (новий max)."""
    assert clamp_dc(25) == 22
    assert clamp_dc(28) == 22
    assert clamp_dc(30) == 22


# ---------------------------------------------------------------------------
# score_to_relation_text — 15-step relation scale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    # upper boundary
    (100,  "Абсолютна довіра"),
    (85,   "Абсолютна довіра"),
    # each step, at exact threshold
    (70,   "Глибока довіра"),
    (60,   "Довіряє"),
    (45,   "Дружній"),
    (30,   "Прихильний"),
    (20,   "Тепле ставлення"),
    (10,   "Обережно відкритий"),
    (0,    "Нейтральний"),
    (-5,   "Нейтральний"),
    (-6,   "Холодний"),
    (-15,  "Холодний"),
    (-16,  "Підозрілий"),
    (-25,  "Підозрілий"),
    (-26,  "Глибока підозра"),
    (-35,  "Глибока підозра"),
    (-36,  "Ворожий"),
    (-50,  "Ворожий"),
    (-51,  "Відкрита ворожість"),
    (-65,  "Відкрита ворожість"),
    (-66,  "Кривавий ворог"),
    (-80,  "Кривавий ворог"),
    # below minimum threshold
    (-81,  "Смертельна ненависть"),
    (-100, "Смертельна ненависть"),
])
def test_score_to_relation_text_boundaries(score, expected):
    assert score_to_relation_text(score) == expected, (
        f"score_to_relation_text({score}) expected {expected!r}, "
        f"got {score_to_relation_text(score)!r}"
    )


def test_score_to_relation_text_zero_is_neutral():
    assert score_to_relation_text(0) == "Нейтральний"


def test_score_to_relation_text_high_positive_is_absolute_trust():
    assert score_to_relation_text(100) == "Абсолютна довіра"


def test_score_to_relation_text_very_negative_is_deadly_hate():
    assert score_to_relation_text(-200) == "Смертельна ненависть"


def test_score_to_relation_text_returns_string():
    for score in range(-100, 101, 5):
        result = score_to_relation_text(score)
        assert isinstance(result, str) and len(result) > 0, (
            f"score_to_relation_text({score}) must return non-empty string"
        )


# ---------------------------------------------------------------------------
# map_relation_to_score — reverse mapping
# ---------------------------------------------------------------------------

def test_map_relation_to_score_neutral_returns_zero():
    assert map_relation_to_score("нейтральний") == 0


def test_map_relation_to_score_case_insensitive():
    assert map_relation_to_score("НЕЙТРАЛЬНИЙ") == 0
    assert map_relation_to_score("Нейтральний") == 0


def test_map_relation_to_score_empty_returns_zero():
    assert map_relation_to_score("") == 0
    assert map_relation_to_score(None) == 0


def test_map_relation_to_score_unknown_returns_zero():
    assert map_relation_to_score("невідомо") == 0


def test_map_relation_to_score_absolute_trust():
    assert map_relation_to_score("абсолютна довіра") == 85


def test_map_relation_to_score_deadly_hate():
    assert map_relation_to_score("смертельна ненависть") == -80


def test_map_relation_to_score_legacy_friendly():
    """Backward compat: old "дружня" label must map to 30 (not 45 like canonical "дружній")."""
    assert map_relation_to_score("дружня") == 30


# ---------------------------------------------------------------------------
# RELATION_TO_SCORE dict structure
# ---------------------------------------------------------------------------

def test_relation_to_score_has_canonical_15_step_labels():
    canonical = {
        "смертельна ненависть", "кривавий ворог", "відкрита ворожість",
        "ворожий", "глибока підозра", "підозрілий", "холодний",
        "нейтральний", "обережно відкритий", "тепле ставлення",
        "прихильний", "дружній", "довіряє", "глибока довіра",
        "абсолютна довіра",
    }
    for label in canonical:
        assert label in RELATION_TO_SCORE, (
            f"Canonical label {label!r} missing from RELATION_TO_SCORE"
        )


# ---------------------------------------------------------------------------
# canon_npc re-export aliases point to the same objects as dnd_core public API
# ---------------------------------------------------------------------------

def test_canon_npc_reexports_score_to_relation_text():
    """_score_to_relation_text alias in canon_npc must be the same object as dnd_core public fn."""
    from database.canon_npc import _score_to_relation_text
    from core.dnd_core import score_to_relation_text
    assert _score_to_relation_text is score_to_relation_text


def test_canon_npc_reexports_map_relation_to_score():
    """_map_relation_to_score alias in canon_npc must be the same object as dnd_core public fn."""
    from database.canon_npc import _map_relation_to_score
    from core.dnd_core import map_relation_to_score
    assert _map_relation_to_score is map_relation_to_score


def test_canon_npc_reexports_relation_to_score_dict():
    """_RELATION_TO_SCORE alias in canon_npc must be the same object as dnd_core public dict."""
    from database.canon_npc import _RELATION_TO_SCORE
    from core.dnd_core import RELATION_TO_SCORE
    assert _RELATION_TO_SCORE is RELATION_TO_SCORE
