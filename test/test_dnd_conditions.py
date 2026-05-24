"""Unit tests for core/dnd_conditions.py (Phase 2 QA).

Covers: CONDITION_NAMES registry, apply/remove/has, tick_conditions, condition_modifies,
get_condition_description — aligned with CLAUDE.md §5 invariants.
"""

import pytest
from unittest.mock import patch

from core.dnd_conditions import (
    CONDITION_NAMES,
    Condition,
    apply_condition,
    condition_modifies,
    get_condition_description,
    has_condition,
    remove_condition,
    tick_conditions,
)


# ---------------------------------------------------------------------------
# 1. CONDITION_NAMES registry
# ---------------------------------------------------------------------------

def test_condition_names_has_at_least_15_entries():
    assert len(CONDITION_NAMES) >= 15


def test_condition_names_contains_poisoned():
    assert "poisoned" in CONDITION_NAMES


def test_condition_names_contains_prone():
    assert "prone" in CONDITION_NAMES


def test_condition_names_contains_stunned():
    assert "stunned" in CONDITION_NAMES


def test_condition_names_contains_frightened():
    assert "frightened" in CONDITION_NAMES


def test_condition_names_contains_bleeding():
    assert "bleeding" in CONDITION_NAMES


def test_condition_names_contains_all_14_phb_plus_bleeding():
    expected = {
        "blinded", "charmed", "deafened", "frightened", "grappled",
        "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
        "prone", "restrained", "stunned", "unconscious", "bleeding",
    }
    assert expected.issubset(set(CONDITION_NAMES))


# ---------------------------------------------------------------------------
# 2. apply_condition
# ---------------------------------------------------------------------------

def test_apply_condition_adds_to_target_conditions():
    target = {}
    apply_condition(target, "poisoned", duration_rounds=3, source="Poison Arrow")
    assert "conditions" in target
    assert any(c["name"] == "poisoned" for c in target["conditions"])


def test_apply_condition_sets_correct_duration():
    target = {}
    apply_condition(target, "blinded", duration_rounds=2, source="Flash Powder")
    cond = next(c for c in target["conditions"] if c["name"] == "blinded")
    assert cond["duration_rounds"] == 2


def test_apply_condition_refresh_keeps_max_duration_new_longer():
    """Refresh: new duration > old → new wins."""
    target = {}
    apply_condition(target, "prone", duration_rounds=2, source="Trip")
    apply_condition(target, "prone", duration_rounds=5, source="Heavy Blow")
    prone_entries = [c for c in target["conditions"] if c["name"] == "prone"]
    assert len(prone_entries) == 1
    assert prone_entries[0]["duration_rounds"] == 5


def test_apply_condition_refresh_keeps_max_duration_old_longer():
    """Refresh: old duration > new → old wins."""
    target = {}
    apply_condition(target, "prone", duration_rounds=5, source="Trip")
    apply_condition(target, "prone", duration_rounds=2, source="Light Shove")
    prone_entries = [c for c in target["conditions"] if c["name"] == "prone"]
    assert prone_entries[0]["duration_rounds"] == 5


def test_apply_condition_indefinite_minus_one_beats_positive():
    """duration=-1 wins over any positive value in refresh."""
    target = {}
    apply_condition(target, "petrified", duration_rounds=5, source="Basilisk")
    apply_condition(target, "petrified", duration_rounds=-1, source="Permanent Curse")
    cond = next(c for c in target["conditions"] if c["name"] == "petrified")
    assert cond["duration_rounds"] == -1


def test_apply_condition_positive_cannot_override_minus_one():
    """Once set to -1, a positive duration refresh cannot remove it."""
    target = {}
    apply_condition(target, "petrified", duration_rounds=-1, source="Permanent Curse")
    apply_condition(target, "petrified", duration_rounds=10, source="Strong Spell")
    cond = next(c for c in target["conditions"] if c["name"] == "petrified")
    assert cond["duration_rounds"] == -1


def test_apply_condition_unknown_name_raises_value_error():
    target = {}
    with pytest.raises(ValueError):
        apply_condition(target, "confused", duration_rounds=2, source="Test")


# ---------------------------------------------------------------------------
# 3. remove_condition
# ---------------------------------------------------------------------------

def test_remove_condition_returns_true_when_present():
    target = {}
    apply_condition(target, "prone", duration_rounds=2, source="Trip")
    result = remove_condition(target, "prone")
    assert result is True


def test_remove_condition_actually_removes_it():
    target = {}
    apply_condition(target, "prone", duration_rounds=2, source="Trip")
    remove_condition(target, "prone")
    assert not has_condition(target, "prone")


def test_remove_condition_returns_false_when_not_present():
    target = {}
    result = remove_condition(target, "prone")
    assert result is False


def test_remove_condition_only_removes_named_condition():
    target = {}
    apply_condition(target, "prone", duration_rounds=2, source="Trip")
    apply_condition(target, "poisoned", duration_rounds=3, source="Poison")
    remove_condition(target, "prone")
    assert has_condition(target, "poisoned")
    assert not has_condition(target, "prone")


# ---------------------------------------------------------------------------
# 4. has_condition
# ---------------------------------------------------------------------------

def test_has_condition_true_when_present():
    target = {}
    apply_condition(target, "blinded", duration_rounds=1, source="Sand")
    assert has_condition(target, "blinded") is True


def test_has_condition_false_when_absent():
    target = {}
    assert has_condition(target, "blinded") is False


def test_has_condition_false_after_removal():
    target = {}
    apply_condition(target, "blinded", duration_rounds=1, source="Sand")
    remove_condition(target, "blinded")
    assert has_condition(target, "blinded") is False


# ---------------------------------------------------------------------------
# 5. tick_conditions
# ---------------------------------------------------------------------------

def test_tick_conditions_decrements_duration_by_one():
    target = {"name": "TestTarget", "conditions": []}
    apply_condition(target, "prone", duration_rounds=2, source="Trip")
    tick_conditions(target)
    cond = next(c for c in target["conditions"] if c["name"] == "prone")
    assert cond["duration_rounds"] == 1


def test_tick_conditions_removes_expired_condition_at_zero():
    target = {"name": "TestTarget", "conditions": []}
    apply_condition(target, "prone", duration_rounds=1, source="Trip")
    tick_conditions(target)
    assert not has_condition(target, "prone")


def test_tick_conditions_returns_log_on_expiry():
    target = {"name": "Soldier", "conditions": []}
    apply_condition(target, "prone", duration_rounds=1, source="Trip")
    log = tick_conditions(target)
    assert any("prone" in line or "Soldier" in line for line in log)


def test_tick_conditions_indefinite_minus_one_never_decrements():
    target = {"name": "PetrifiedOne", "conditions": []}
    apply_condition(target, "petrified", duration_rounds=-1, source="Basilisk")
    tick_conditions(target)
    tick_conditions(target)
    cond = next(c for c in target["conditions"] if c["name"] == "petrified")
    assert cond["duration_rounds"] == -1


def test_tick_conditions_indefinite_minus_one_not_removed():
    target = {"name": "PetrifiedOne", "conditions": []}
    apply_condition(target, "petrified", duration_rounds=-1, source="Basilisk")
    for _ in range(5):
        tick_conditions(target)
    assert has_condition(target, "petrified")


def test_tick_conditions_bleeding_deals_damage_and_returns_log():
    target = {"name": "Victim", "hp_current": 20, "conditions": []}
    apply_condition(target, "bleeding", duration_rounds=3, source="Sword Gash")
    with patch("random.randint", return_value=3):
        log = tick_conditions(target)
    assert target["hp_current"] == 17
    assert len(log) >= 1
    assert any("3" in line or "кровотеч" in line.lower() for line in log)


def test_tick_conditions_bleeding_minimum_hp_zero():
    target = {"name": "Victim", "hp_current": 1, "conditions": []}
    apply_condition(target, "bleeding", duration_rounds=3, source="Sword Gash")
    with patch("random.randint", return_value=4):
        tick_conditions(target)
    assert target["hp_current"] == 0


# ---------------------------------------------------------------------------
# 6. condition_modifies
# ---------------------------------------------------------------------------

def test_condition_modifies_blinded_actor_attack_gives_disadvantage():
    actor = {}
    apply_condition(actor, "blinded", duration_rounds=2, source="Flash")
    result = condition_modifies("attack", actor=actor)
    assert result["disadvantage"] is True


def test_condition_modifies_poisoned_actor_attack_gives_disadvantage():
    actor = {}
    apply_condition(actor, "poisoned", duration_rounds=3, source="Poison")
    result = condition_modifies("attack", actor=actor)
    assert result["disadvantage"] is True


def test_condition_modifies_poisoned_actor_check_gives_disadvantage():
    """PHB: poisoned = disadvantage on attack rolls AND ability checks."""
    actor = {}
    apply_condition(actor, "poisoned", duration_rounds=3, source="Poison")
    result = condition_modifies("check", actor=actor)
    assert result["disadvantage"] is True


def test_condition_modifies_paralyzed_actor_gives_blocked():
    actor = {}
    apply_condition(actor, "paralyzed", duration_rounds=-1, source="Spell")
    result = condition_modifies("attack", actor=actor)
    assert result["blocked"] is True


def test_condition_modifies_frightened_actor_attack_gives_disadvantage():
    actor = {}
    apply_condition(actor, "frightened", duration_rounds=2, source="Dragon Roar")
    result = condition_modifies("attack", actor=actor)
    assert result["disadvantage"] is True


def test_condition_modifies_prone_actor_attack_gives_disadvantage():
    actor = {}
    apply_condition(actor, "prone", duration_rounds=1, source="Knockdown")
    result = condition_modifies("attack", actor=actor)
    assert result["disadvantage"] is True


def test_condition_modifies_charmed_actor_attacking_charmer_gives_blocked():
    """Charmed actor cannot attack their charmer — blocked when charmer matches target name."""
    actor = {}
    apply_condition(
        actor, "charmed", duration_rounds=-1, source="Cersei",
        save_dc=0, save_ability=""
    )
    # Manually set charmer in extra field
    for entry in actor["conditions"]:
        if entry["name"] == "charmed":
            entry["extra"] = {"charmer": "Cersei"}

    target = {"name": "Cersei"}
    result = condition_modifies("attack", actor=actor, target=target)
    assert result["blocked"] is True


def test_condition_modifies_incapacitated_actor_gives_blocked():
    actor = {}
    apply_condition(actor, "incapacitated", duration_rounds=2, source="Stun")
    result = condition_modifies("attack", actor=actor)
    assert result["blocked"] is True


def test_condition_modifies_stunned_actor_gives_blocked():
    actor = {}
    apply_condition(actor, "stunned", duration_rounds=1, source="Mace Strike")
    result = condition_modifies("any", actor=actor)
    assert result["blocked"] is True


def test_condition_modifies_no_conditions_gives_no_modifiers():
    actor = {}
    result = condition_modifies("attack", actor=actor)
    assert result["advantage"] is False
    assert result["disadvantage"] is False
    assert result["blocked"] is False


# ---------------------------------------------------------------------------
# 7. get_condition_description
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cond_name", [
    "blinded", "charmed", "deafened", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious", "bleeding",
])
def test_get_condition_description_returns_nonempty_string(cond_name):
    desc = get_condition_description(cond_name)
    assert isinstance(desc, str)
    assert len(desc) > 0


def test_get_condition_description_unknown_condition_returns_fallback():
    desc = get_condition_description("confused")
    assert "confused" in desc or len(desc) > 0
