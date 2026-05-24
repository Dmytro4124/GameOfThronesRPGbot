"""Unit tests for core/dnd_classes.py (Фаза 1 QA)."""

import pytest

from core.dnd_classes import (
    GOT_CLASSES,
    ClassDef,
    Feature,
    get_class,
    get_class_features_at_level,
    get_starting_hp,
    build_class_starting_kit,
)
from core.dnd_skills import is_valid_skill

# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------

EXPECTED_CLASSES = {
    "Knight", "Hedge Knight", "Maester", "Septon", "Sellsword",
    "Spy", "Courtier", "Bastard", "Wildling",
}

VALID_ABILITIES = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
VALID_HIT_DICE = {6, 8, 10, 12}


def test_got_classes_has_nine_entries():
    assert len(GOT_CLASSES) == 9


def test_got_classes_has_all_expected_keys():
    assert set(GOT_CLASSES.keys()) == EXPECTED_CLASSES


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_hit_die(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "hit_die")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_primary_abilities(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "primary_abilities")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_saves_proficient(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "saves_proficient")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_saves_proficient_has_two_elements(class_name):
    cls = GOT_CLASSES[class_name]
    assert len(cls.saves_proficient) == 2


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_armor_profs(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "armor_profs")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_weapon_profs(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "weapon_profs")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_skill_choices(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "skill_choices")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_skill_pool(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "skill_pool")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_level_features(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "level_features")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_level_features_has_keys_one_to_twenty(class_name):
    cls = GOT_CLASSES[class_name]
    assert set(cls.level_features.keys()) == set(range(1, 21))


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_starting_equipment(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "starting_equipment")


@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_class_def_has_required_field_description(class_name):
    cls = GOT_CLASSES[class_name]
    assert hasattr(cls, "description") and cls.description


# ---------------------------------------------------------------------------
# hit_die validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_all_hit_dice_are_valid_d_and_d_values(class_name):
    cls = GOT_CLASSES[class_name]
    assert cls.hit_die in VALID_HIT_DICE


@pytest.mark.parametrize("class_name,expected_hd", [
    ("Knight", 10),
    ("Hedge Knight", 10),
    ("Maester", 6),
    ("Septon", 8),
    ("Sellsword", 10),
    ("Spy", 8),
    ("Courtier", 6),
    ("Bastard", 8),
    ("Wildling", 12),
])
def test_hit_die_matches_plan_specification(class_name, expected_hd):
    assert GOT_CLASSES[class_name].hit_die == expected_hd


# ---------------------------------------------------------------------------
# saves_proficient validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_saves_proficient_abilities_are_valid(class_name):
    cls = GOT_CLASSES[class_name]
    for ability in cls.saves_proficient:
        assert ability in VALID_ABILITIES, (
            f"{class_name}: invalid save ability '{ability}'"
        )


# ---------------------------------------------------------------------------
# skill_pool validation (all skills must be real 5e skills)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("class_name", sorted(EXPECTED_CLASSES))
def test_skill_pool_contains_only_valid_dnd_skills(class_name):
    cls = GOT_CLASSES[class_name]
    for skill in cls.skill_pool:
        assert is_valid_skill(skill), (
            f"{class_name}: skill_pool contains invalid skill '{skill}'"
        )


# ---------------------------------------------------------------------------
# get_class
# ---------------------------------------------------------------------------

def test_get_class_returns_class_def_for_valid_name():
    result = get_class("Knight")
    assert isinstance(result, ClassDef)


def test_get_class_raises_key_error_for_unknown_class():
    with pytest.raises(KeyError):
        get_class("Wizard")


# ---------------------------------------------------------------------------
# get_class_features_at_level
# ---------------------------------------------------------------------------

def test_knight_level_one_has_knightly_code_feature():
    features = get_class_features_at_level("Knight", 1)
    names = [f.name for f in features]
    assert "Knightly Code" in names


def test_knight_level_one_has_vows_of_service_feature():
    features = get_class_features_at_level("Knight", 1)
    names = [f.name for f in features]
    assert "Vows of Service" in names


def test_knight_level_one_has_at_least_two_features():
    features = get_class_features_at_level("Knight", 1)
    assert len(features) >= 2


def test_wildling_level_three_has_wild_strength():
    features = get_class_features_at_level("Wildling", 3)
    names = [f.name for f in features]
    assert "Wild Strength" in names


def test_get_class_features_level_zero_raises_value_error():
    with pytest.raises(ValueError):
        get_class_features_at_level("Knight", 0)


def test_get_class_features_level_twenty_one_raises_value_error():
    with pytest.raises(ValueError):
        get_class_features_at_level("Knight", 21)


def test_get_class_features_invalid_class_raises_key_error():
    with pytest.raises(KeyError):
        get_class_features_at_level("Wizard", 1)


# ---------------------------------------------------------------------------
# Group A — Every level (L1-20) has ≥1 real feature, no TODO stubs
# ---------------------------------------------------------------------------

_ALL_LEVELS = list(range(1, 21))
_CLASS_LEVEL_PARAMS = [
    (cls, lvl)
    for cls in sorted(EXPECTED_CLASSES)
    for lvl in _ALL_LEVELS
]


@pytest.mark.parametrize("class_name,lvl", _CLASS_LEVEL_PARAMS)
def test_every_level_has_at_least_one_feature(class_name, lvl):
    features = get_class_features_at_level(class_name, lvl)
    assert len(features) >= 1, (
        f"{class_name} L{lvl}: feature list is empty"
    )


@pytest.mark.parametrize("class_name,lvl", _CLASS_LEVEL_PARAMS)
def test_no_feature_has_todo_name(class_name, lvl):
    features = get_class_features_at_level(class_name, lvl)
    for f in features:
        assert f.name != "TODO", (
            f"{class_name} L{lvl}: feature with name='TODO' found (stub not replaced)"
        )


@pytest.mark.parametrize("class_name,lvl", _CLASS_LEVEL_PARAMS)
def test_no_feature_desc_contains_todo(class_name, lvl):
    features = get_class_features_at_level(class_name, lvl)
    for f in features:
        assert "TODO" not in f.desc, (
            f"{class_name} L{lvl}: feature '{f.name}' desc contains 'TODO'"
        )


@pytest.mark.parametrize("class_name,lvl", _CLASS_LEVEL_PARAMS)
def test_feature_source_starts_with_class_name(class_name, lvl):
    features = get_class_features_at_level(class_name, lvl)
    for f in features:
        assert f.source.startswith(class_name), (
            f"{class_name} L{lvl}: feature '{f.name}' source='{f.source}' "
            f"does not start with class name '{class_name}'"
        )


# ---------------------------------------------------------------------------
# Group B — ASI appears on L4/8/12/16/19 for all 9 classes
# ---------------------------------------------------------------------------

_ASI_LEVELS = [4, 8, 12, 16, 19]
_ASI_PARAMS = [
    (cls, lvl)
    for cls in sorted(EXPECTED_CLASSES)
    for lvl in _ASI_LEVELS
]


@pytest.mark.parametrize("class_name,lvl", _ASI_PARAMS)
def test_asi_present_at_standard_levels(class_name, lvl):
    features = get_class_features_at_level(class_name, lvl)
    names = [f.name for f in features]
    assert "Ability Score Improvement" in names, (
        f"{class_name} L{lvl}: 'Ability Score Improvement' not found; got {names}"
    )


# ---------------------------------------------------------------------------
# Group C — Extra Attack on L5 for martial classes
# ---------------------------------------------------------------------------

# Criterion: hit_die >= 10 OR STR in primary_abilities.
# Resulting set: Knight (d10, STR), Hedge Knight (d10, STR), Sellsword (d10, STR),
#                Bastard (d8, STR), Wildling (d12, STR).
_MARTIAL_CLASSES = ["Bastard", "Hedge Knight", "Knight", "Sellsword", "Wildling"]


@pytest.mark.parametrize("class_name", _MARTIAL_CLASSES)
def test_extra_attack_on_level_five_for_martial_classes(class_name):
    features = get_class_features_at_level(class_name, 5)
    names = [f.name for f in features]
    assert "Extra Attack" in names, (
        f"{class_name} L5: 'Extra Attack' not found; got {names}"
    )


# ---------------------------------------------------------------------------
# Mutation safety — get_class_features_at_level returns an independent copy
# ---------------------------------------------------------------------------

def test_mutation_of_returned_list_does_not_affect_singleton():
    before_len = len(GOT_CLASSES["Knight"].level_features[1])
    returned = get_class_features_at_level("Knight", 1)
    returned.append(Feature("X", "x", "X"))
    after_len = len(GOT_CLASSES["Knight"].level_features[1])
    assert before_len == after_len, (
        "Appending to the returned list mutated the GOT_CLASSES singleton"
    )


# ---------------------------------------------------------------------------
# Heritage quick-check — Red Priest languages
# ---------------------------------------------------------------------------

def test_red_priest_heritage_languages_contain_high_valyrian():
    from core.dnd_heritages import HERITAGES
    languages = HERITAGES["Red Priest"].languages
    assert "High Valyrian" in languages, (
        f"Red Priest languages={languages!r}; expected 'High Valyrian' (not 'Valyrian')"
    )


# ---------------------------------------------------------------------------
# get_starting_hp
# ---------------------------------------------------------------------------

def test_get_starting_hp_knight_con_mod_two():
    # Knight hit_die=10, CON+2 → 12
    assert get_starting_hp("Knight", 2) == 12


def test_get_starting_hp_maester_con_mod_zero():
    # Maester hit_die=6, CON+0 → 6
    assert get_starting_hp("Maester", 0) == 6


def test_get_starting_hp_wildling_con_mod_minus_one():
    # Wildling hit_die=12, CON-1 → 11
    assert get_starting_hp("Wildling", -1) == 11


def test_get_starting_hp_minimum_one_with_extreme_negative_con():
    # Wildling hit_die=12, CON-20 → max(1, 12-20)=max(1,-8)=1
    assert get_starting_hp("Wildling", -20) == 1


# ---------------------------------------------------------------------------
# build_class_starting_kit
# ---------------------------------------------------------------------------

def test_build_class_starting_kit_knight_gold_is_seventy_five():
    kit = build_class_starting_kit("Knight")
    assert kit["gold"] == 75


def test_build_class_starting_kit_returns_a_copy_not_same_object():
    kit1 = build_class_starting_kit("Knight")
    kit2 = build_class_starting_kit("Knight")
    assert kit1 is not kit2


def test_build_class_starting_kit_mutation_does_not_affect_class_definition():
    kit = build_class_starting_kit("Knight")
    kit["items"].append("MUTATED_ITEM")
    # Fetch a fresh kit — original class data must be unaffected
    fresh_kit = build_class_starting_kit("Knight")
    assert "MUTATED_ITEM" not in fresh_kit["items"]


def test_build_class_starting_kit_contains_items_key():
    kit = build_class_starting_kit("Spy")
    assert "items" in kit
    assert isinstance(kit["items"], list)


def test_build_class_starting_kit_invalid_class_raises_key_error():
    with pytest.raises(KeyError):
        build_class_starting_kit("Wizard")
