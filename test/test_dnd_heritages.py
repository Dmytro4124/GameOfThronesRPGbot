"""Unit tests for core/dnd_heritages.py (Фаза 1 QA)."""

import copy
import pytest

from core.dnd_heritages import (
    HERITAGES,
    HeritageDef,
    Trait,
    get_heritage,
    apply_heritage_bonuses,
    list_heritages,
)


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------

EXPECTED_HERITAGES = {
    "Westerosi (Andal)",
    "Valyrian Descent",
    "First Men (Stark line)",
    "Free Folk",
    "Red Priest",
    "Ironborn",
}


def test_heritages_has_six_entries():
    assert len(HERITAGES) == 6


def test_heritages_has_all_expected_keys():
    assert set(HERITAGES.keys()) == EXPECTED_HERITAGES


@pytest.mark.parametrize("heritage_name", sorted(EXPECTED_HERITAGES))
def test_heritage_def_has_ability_bonuses(heritage_name):
    h = HERITAGES[heritage_name]
    assert hasattr(h, "ability_bonuses")


@pytest.mark.parametrize("heritage_name", sorted(EXPECTED_HERITAGES))
def test_heritage_def_has_speed(heritage_name):
    h = HERITAGES[heritage_name]
    assert hasattr(h, "speed")


@pytest.mark.parametrize("heritage_name", sorted(EXPECTED_HERITAGES))
def test_heritage_def_has_languages(heritage_name):
    h = HERITAGES[heritage_name]
    assert hasattr(h, "languages")


@pytest.mark.parametrize("heritage_name", sorted(EXPECTED_HERITAGES))
def test_heritage_def_has_traits(heritage_name):
    h = HERITAGES[heritage_name]
    assert hasattr(h, "traits")


@pytest.mark.parametrize("heritage_name", sorted(EXPECTED_HERITAGES))
def test_heritage_def_has_no_magic_field(heritage_name):
    h = HERITAGES[heritage_name]
    assert hasattr(h, "no_magic")


# ---------------------------------------------------------------------------
# Specific values per plan spec
# ---------------------------------------------------------------------------

def test_free_folk_speed_is_thirty_five():
    assert get_heritage("Free Folk").speed == 35


@pytest.mark.parametrize("heritage_name", [
    "Westerosi (Andal)", "Valyrian Descent", "First Men (Stark line)",
    "Red Priest", "Ironborn",
])
def test_non_free_folk_speed_is_thirty(heritage_name):
    assert get_heritage(heritage_name).speed == 30


def test_valyrian_no_magic_is_false():
    assert get_heritage("Valyrian Descent").no_magic is False


def test_westerosi_andal_no_magic_is_true():
    assert get_heritage("Westerosi (Andal)").no_magic is True


def test_first_men_has_warging_trait():
    h = get_heritage("First Men (Stark line)")
    trait_names = [t.name for t in h.traits]
    assert "Варгінг" in trait_names


def test_red_priest_has_pyromancy_trait():
    h = get_heritage("Red Priest")
    trait_names = [t.name for t in h.traits]
    assert "Піромантія" in trait_names


# ---------------------------------------------------------------------------
# apply_heritage_bonuses — direct bonuses
# ---------------------------------------------------------------------------

def _base_profile() -> dict:
    """Return a standard-ability-scores profile for testing."""
    return {
        "ability_scores": {
            "STR": 14, "DEX": 10, "CON": 12,
            "INT": 10, "WIS": 10, "CHA": 10,
        }
    }


def test_apply_valyrian_adds_cha_plus_two():
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "Valyrian Descent")
    assert result["ability_scores"]["CHA"] == 12  # 10 + 2


def test_apply_valyrian_adds_int_plus_one():
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "Valyrian Descent")
    assert result["ability_scores"]["INT"] == 11  # 10 + 1


def test_apply_first_men_adds_wis_plus_two():
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "First Men (Stark line)")
    assert result["ability_scores"]["WIS"] == 12  # 10 + 2


def test_apply_first_men_adds_con_plus_one():
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "First Men (Stark line)")
    assert result["ability_scores"]["CON"] == 13  # 12 + 1


# ---------------------------------------------------------------------------
# apply_heritage_bonuses — Westerosi choice mechanic
# ---------------------------------------------------------------------------

def test_apply_westerosi_with_choices_applies_to_chosen_abilities():
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "Westerosi (Andal)", ability_choices=["STR", "DEX"])
    assert result["ability_scores"]["STR"] == 15  # 14 + 1
    assert result["ability_scores"]["DEX"] == 11  # 10 + 1


def test_apply_westerosi_without_choices_fallback_is_str_then_dex():
    # Without ability_choices → fallback: first two unchosen from STR,DEX,CON,INT,WIS,CHA
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "Westerosi (Andal)", ability_choices=None)
    # Fallback picks STR and DEX (first two alphabetically-iterated valid abilities)
    assert result["ability_scores"]["STR"] == 15  # 14 + 1
    assert result["ability_scores"]["DEX"] == 11  # 10 + 1


# ---------------------------------------------------------------------------
# apply_heritage_bonuses — score clamping at 20
# ---------------------------------------------------------------------------

def test_apply_heritage_bonus_clamps_score_at_twenty():
    # CHA = 19, Valyrian +2 → must be clamped to 20, not 21
    profile = {
        "ability_scores": {
            "STR": 10, "DEX": 10, "CON": 10,
            "INT": 10, "WIS": 10, "CHA": 19,
        }
    }
    result = apply_heritage_bonuses(profile, "Valyrian Descent")
    assert result["ability_scores"]["CHA"] == 20


def test_apply_heritage_bonus_exactly_twenty_stays_twenty():
    profile = {
        "ability_scores": {
            "STR": 10, "DEX": 10, "CON": 10,
            "INT": 10, "WIS": 10, "CHA": 20,
        }
    }
    result = apply_heritage_bonuses(profile, "Valyrian Descent")
    assert result["ability_scores"]["CHA"] == 20


# ---------------------------------------------------------------------------
# apply_heritage_bonuses — deep copy (no mutation of original profile)
# ---------------------------------------------------------------------------

def test_apply_heritage_bonuses_does_not_mutate_original_profile():
    profile = _base_profile()
    original_cha = profile["ability_scores"]["CHA"]
    apply_heritage_bonuses(profile, "Valyrian Descent")
    # Original profile must be unchanged
    assert profile["ability_scores"]["CHA"] == original_cha


def test_apply_heritage_bonuses_result_is_different_object():
    profile = _base_profile()
    result = apply_heritage_bonuses(profile, "Valyrian Descent")
    assert result is not profile


# ---------------------------------------------------------------------------
# list_heritages
# ---------------------------------------------------------------------------

def test_list_heritages_returns_six_entries():
    assert len(list_heritages()) == 6


def test_list_heritages_returns_sorted_list():
    result = list_heritages()
    assert result == sorted(result)


def test_list_heritages_contains_all_expected_names():
    result = list_heritages()
    assert set(result) == EXPECTED_HERITAGES


# ---------------------------------------------------------------------------
# get_heritage
# ---------------------------------------------------------------------------

def test_get_heritage_returns_heritage_def_for_valid_name():
    result = get_heritage("Free Folk")
    assert isinstance(result, HeritageDef)


def test_get_heritage_raises_key_error_for_unknown_heritage():
    with pytest.raises(KeyError):
        get_heritage("Dwarf")
