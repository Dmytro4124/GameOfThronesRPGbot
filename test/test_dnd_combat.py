"""Unit tests for core/dnd_combat.py (Phase 2 QA).

Covers: roll_initiative, parse_damage_dice, _roll_damage, initiate_combat,
player_attack, apply_damage, end_round, player_flee_check, cleanup_combat,
and one E2E mini-encounter.
"""

import pytest
from unittest.mock import patch, call

from core.dnd_combat import (
    AttackResult,
    CombatState,
    CombatantRef,
    _roll_damage,
    apply_damage,
    cleanup_combat,
    end_round,
    initiate_combat,
    parse_damage_dice,
    player_attack,
    player_flee_check,
    npc_decide_action,
    npc_attack,
)
from core.dnd_conditions import apply_condition


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _player(name="Arya", level=1, hp=11, ac=18, str_score=16, dex_score=14):
    return {
        "Ім'я": name,
        "level": level,
        "hp_current": hp,
        "hp_max": hp,
        "ac": ac,
        "ability_scores": {"STR": str_score, "DEX": dex_score, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "saves_proficient": ["STR", "CON"],
        "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        "conditions": [],
        "features": [],
        "heritage": "Westerosi (Andal)",
    }


def _goblin(name="Goblin", hp=7, ac=12, cr="1/4"):
    return {
        "Name": name,
        "hp_current": hp,
        "hp_max": hp,
        "ac": ac,
        "Status": "Active",
        "cr": cr,
        "conditions": [],
        "ability_scores": {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        "attacks": [{"name": "Dagger", "to_hit": 4, "dmg": "1d4+2 piercing", "range": 5}],
        "features": [],
        "heritage": "",
    }


def _bandit_captain(name="Bandit Captain", hp=65, ac=15, cr="2"):
    return {
        "Name": name,
        "hp_current": hp,
        "hp_max": hp,
        "ac": ac,
        "Status": "Active",
        "cr": cr,
        "conditions": [],
        "ability_scores": {"STR": 15, "DEX": 16, "CON": 14, "INT": 14, "WIS": 11, "CHA": 14},
        "attacks": [{"name": "Shortsword", "to_hit": 5, "dmg": "1d6+3 slashing", "range": 5}],
        "features": [],
        "heritage": "",
    }


def _make_combat_state(player, npcs_list, chat_id=42):
    """Build a CombatState manually without rolling initiative (uses initiate_combat)."""
    import copy
    npc_snapshots = {}
    for npc in npcs_list:
        npc_name = npc.get("Name", npc.get("name", "Unknown"))
        snap = copy.deepcopy(npc)
        snap.setdefault("hp_current", snap.get("hp_max", 10))
        snap.setdefault("ac", 10)
        snap.setdefault("conditions", [])
        snap.setdefault("Status", "Active")
        npc_snapshots[npc_name] = snap

    player_snap = copy.deepcopy(player)
    player_snap.setdefault("conditions", [])

    player_name = player.get("Ім'я", player.get("name", "Player"))
    combatants = [CombatantRef(kind="player", name=player_name, init_roll=15)]
    for npc in npcs_list:
        npc_name = npc.get("Name", "Unknown")
        combatants.append(CombatantRef(kind="npc", name=npc_name, init_roll=8))

    return CombatState(
        chat_id=chat_id,
        round=1,
        initiative_order=combatants,
        current_actor_idx=0,
        npcs=npc_snapshots,
        player_snapshot=player_snap,
    )


# ---------------------------------------------------------------------------
# 1. roll_initiative
# ---------------------------------------------------------------------------

def test_roll_initiative_adds_dex_modifier():
    """DEX=14 → modifier=+2; mock d20=10 → expected=12."""
    profile = {"ability_scores": {"DEX": 14}}
    with patch("random.randint", return_value=10):
        from core.dnd_combat import roll_initiative
        result = roll_initiative(profile)
    assert result == 12


def test_roll_initiative_negative_dex_modifier():
    """DEX=8 → modifier=-1; mock d20=10 → expected=9."""
    profile = {"ability_scores": {"DEX": 8}}
    with patch("random.randint", return_value=10):
        from core.dnd_combat import roll_initiative
        result = roll_initiative(profile)
    assert result == 9


# ---------------------------------------------------------------------------
# 2. parse_damage_dice
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dice_str,expected", [
    ("1d8+3 slashing",  (1, 8,  3, "slashing")),
    ("2d6 piercing",    (2, 6,  0, "piercing")),
    ("1d12 bludgeoning", (1, 12, 0, "bludgeoning")),
    ("1d4-1 bludgeoning", (1, 4, -1, "bludgeoning")),
])
def test_parse_damage_dice_standard_formats(dice_str, expected):
    assert parse_damage_dice(dice_str) == expected


def test_parse_damage_dice_spell_prefix_returns_safe_default():
    result = parse_damage_dice("spell:burning_hands")
    assert result == (2, 6, 0, "fire")


def test_parse_damage_dice_empty_string_returns_fallback():
    result = parse_damage_dice("")
    # Must not raise; returns a safe tuple
    assert isinstance(result, tuple)
    assert len(result) == 4


def test_parse_damage_dice_missing_type_defaults_to_slashing():
    result = parse_damage_dice("1d6")
    assert result[3] == "slashing"


# ---------------------------------------------------------------------------
# 3. _roll_damage
# ---------------------------------------------------------------------------

def test_roll_damage_normal_adds_modifier():
    """1d8+3: mock die=5 → 5+3=8."""
    with patch("core.dnd_combat.random.randint", return_value=5):
        result = _roll_damage(1, 8, 3, critical=False)
    assert result == 8


def test_roll_damage_critical_doubles_dice():
    """1d8+3 crit: 2 dice rolls (5+4=9) +3 = 12."""
    with patch("core.dnd_combat.random.randint", side_effect=[5, 4]):
        result = _roll_damage(1, 8, 3, critical=True)
    assert result == 12


def test_roll_damage_minimum_is_one():
    """1d4-3 with die=1 → 1-3=-2, clamped to 1."""
    with patch("core.dnd_combat.random.randint", return_value=1):
        result = _roll_damage(1, 4, -3, critical=False)
    assert result == 1


def test_roll_damage_flat_damage_no_dice():
    """Flat damage path (die_size=0): num_dice acts as flat value."""
    result = _roll_damage(0, 0, 5, critical=False)
    assert result == max(1, 5)


def test_roll_damage_flat_minimum_one_even_with_negative_modifier():
    result = _roll_damage(0, 0, -10, critical=False)
    assert result == 1


# ---------------------------------------------------------------------------
# 4. initiate_combat
# ---------------------------------------------------------------------------

def test_initiate_combat_creates_correct_number_of_combatants():
    """1 player + 2 NPCs → 3 CombatantRef entries."""
    player = _player()
    goblin1 = _goblin("Goblin A")
    goblin2 = _goblin("Goblin B")
    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(player, [goblin1, goblin2], chat_id=1)
    assert len(state.initiative_order) == 3


def test_initiate_combat_npcs_dict_populated():
    player = _player()
    goblin = _goblin("Goblin X")
    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(player, [goblin], chat_id=2)
    assert "Goblin X" in state.npcs


def test_initiate_combat_round_starts_at_one():
    player = _player()
    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(player, [_goblin()], chat_id=3)
    assert state.round == 1


def test_initiate_combat_phase_is_ongoing():
    player = _player()
    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(player, [_goblin()], chat_id=4)
    assert state.phase == "ONGOING"


def test_initiate_combat_tied_initiative_npc_goes_first():
    """Tied initiative → NPC first (kind='npc' before 'player')."""
    player = _player()
    goblin = _goblin()
    # All roll the same value → NPC sort key wins
    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(player, [goblin], chat_id=5)
    # Player has DEX=14 → mod+2, Goblin has DEX=14 → mod+2
    # Both same init → NPC should be first
    assert state.initiative_order[0].kind == "npc"


def test_initiate_combat_npc_snapshot_has_required_fields():
    player = _player()
    goblin = _goblin("TestGoblin")
    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(player, [goblin], chat_id=6)
    snap = state.npcs["TestGoblin"]
    assert "hp_current" in snap
    assert "ac" in snap
    assert "conditions" in snap
    assert "Status" in snap


# ---------------------------------------------------------------------------
# 5. player_attack
# ---------------------------------------------------------------------------

def test_player_attack_hit_deals_damage():
    """STR=16 (mod+3), L1 prof+2: nat 15 → to_hit=15+3+2=20 ≥ AC 12 → HIT.

    roll_d20() lives in core.dnd_core and calls random.randint from that module.
    _roll_damage() lives in core.dnd_combat and calls random.randint from that module.
    Because both share the same `random` module singleton we patch the underlying
    random.randint once with a sequenced side_effect: first call = attack roll (15),
    second call = damage die roll (5).
    """
    player = _player(str_score=16, level=1)
    goblin = _goblin(ac=12, hp=20)
    state = _make_combat_state(player, [goblin])

    # Call sequence: roll_d20 (1 call=15), then _roll_damage die (1 call=5)
    with patch("random.randint", side_effect=[15, 5]):
        result = player_attack(state, "Goblin", "Longsword")

    assert result.hit is True
    assert result.natural_roll == 15
    assert result.to_hit_total == 20  # 15+3+2
    assert result.damage_total == 8   # 5+3


def test_player_attack_nat_20_is_critical():
    """Nat 20: roll_d20 (1 call=20), then _roll_damage crit = 2 dice (calls: 5, 4)."""
    player = _player(str_score=16, level=1)
    goblin = _goblin(ac=12, hp=20)
    state = _make_combat_state(player, [goblin])

    with patch("random.randint", side_effect=[20, 5, 4]):
        result = player_attack(state, "Goblin", "Longsword")

    assert result.natural_roll == 20
    assert result.critical is True
    assert result.hit is True


def test_player_attack_nat_1_is_automatic_miss():
    """Nat 1 on attack roll = automatic miss per D&D 5e, regardless of to_hit total."""
    player = _player(str_score=16, level=1)
    goblin = _goblin(ac=5, hp=20)  # Very low AC — but nat 1 still misses
    state = _make_combat_state(player, [goblin])

    with patch("random.randint", return_value=1):
        result = player_attack(state, "Goblin", "Longsword")

    assert result.natural_roll == 1
    assert result.hit is False
    assert result.critical is False


def test_player_attack_reduces_npc_hp():
    player = _player(str_score=16, level=1)
    goblin = _goblin(ac=5, hp=20)
    state = _make_combat_state(player, [goblin])

    # Attack roll (15) then damage die (5)
    with patch("random.randint", side_effect=[15, 5]):
        player_attack(state, "Goblin", "Longsword")

    assert state.npcs["Goblin"]["hp_current"] < 20


def test_player_attack_kills_npc_at_zero_hp():
    player = _player(str_score=16, level=1)
    goblin = _goblin(ac=5, hp=5)  # 5 HP, will take 8 damage (die=5+3)
    state = _make_combat_state(player, [goblin])

    # Attack roll (15) then damage die (5)
    with patch("random.randint", side_effect=[15, 5]):
        player_attack(state, "Goblin", "Longsword")

    assert state.npcs["Goblin"]["Status"] == "Dead"


# ---------------------------------------------------------------------------
# 6. apply_damage
# ---------------------------------------------------------------------------

def test_apply_damage_valyrian_halves_fire_damage():
    player = _player()
    player["heritage"] = "Valyrian Descent"
    player["hp_current"] = 50
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    player_name = player["Ім'я"]

    apply_damage(state, player_name, 20, "fire")
    assert state.player_snapshot["hp_current"] == 40  # 50 - 10 (half of 20)


def test_apply_damage_westerosi_takes_full_fire_damage():
    player = _player()
    player["heritage"] = "Westerosi (Andal)"
    player["hp_current"] = 50
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    player_name = player["Ім'я"]

    apply_damage(state, player_name, 20, "fire")
    assert state.player_snapshot["hp_current"] == 30  # 50 - 20


def test_apply_damage_npc_reduced_to_zero_sets_dead():
    player = _player()
    goblin = _goblin(hp=10)
    state = _make_combat_state(player, [goblin])

    apply_damage(state, "Goblin", 50, "slashing")
    assert state.npcs["Goblin"]["Status"] == "Dead"


def test_apply_damage_hp_never_goes_below_zero():
    player = _player()
    goblin = _goblin(hp=5)
    state = _make_combat_state(player, [goblin])

    apply_damage(state, "Goblin", 1000, "slashing")
    assert state.npcs["Goblin"]["hp_current"] == 0


def test_apply_damage_player_dead_sets_phase():
    player = _player(hp=5)
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    player_name = player["Ім'я"]

    apply_damage(state, player_name, 1000, "slashing")
    assert state.phase == "PLAYER_DEAD"


# ---------------------------------------------------------------------------
# 7. end_round
# ---------------------------------------------------------------------------

def test_end_round_all_npcs_dead_gives_player_win():
    player = _player()
    goblin = _goblin(hp=0)
    state = _make_combat_state(player, [goblin])
    state.npcs["Goblin"]["Status"] = "Dead"

    phase = end_round(state)
    assert phase == "PLAYER_WIN"


def test_end_round_player_hp_zero_gives_player_dead():
    player = _player(hp=0)
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    state.player_snapshot["hp_current"] = 0

    phase = end_round(state)
    assert phase == "PLAYER_DEAD"


def test_end_round_timeout_after_round_limit():
    player = _player()
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    # round_limit=10, set round=11 (already past limit)
    state.round_limit = 10
    state.round = 11  # After increment in end_round it becomes 12 > 10

    phase = end_round(state)
    assert phase == "TIMEOUT"


def test_end_round_ongoing_increments_round_counter():
    player = _player()
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    state.round = 1

    end_round(state)
    assert state.round == 2


def test_end_round_ongoing_resets_actor_index():
    player = _player()
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    state.current_actor_idx = 2

    end_round(state)
    assert state.current_actor_idx == 0


def test_end_round_ongoing_phase_stays_ongoing():
    player = _player()
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])

    phase = end_round(state)
    assert phase == "ONGOING"


# ---------------------------------------------------------------------------
# 8. player_flee_check
# ---------------------------------------------------------------------------

def test_player_flee_check_success_sets_phase_player_fled():
    """DEX=14 (mod+2), enemy DEX=12 (mod+1) → DC=11. Mock d20=15 → 15+2=17 ≥ 11 → flees."""
    player = _player(dex_score=14)
    goblin = _goblin()
    goblin["ability_scores"]["DEX"] = 12  # mod+1 → DC=11
    state = _make_combat_state(player, [goblin])

    with patch("random.randint", return_value=15):
        success, result = player_flee_check(state)

    assert success is True
    assert state.phase == "PLAYER_FLED"


def test_player_flee_check_fail_phase_stays_ongoing():
    """DEX=14 (mod+2), enemy DEX=14 (mod+2) → DC=12. Mock d20=5 → 5+2=7 < 12 → fail."""
    player = _player(dex_score=14)
    goblin = _goblin()
    goblin["ability_scores"]["DEX"] = 14  # mod+2 → DC=12
    state = _make_combat_state(player, [goblin])

    with patch("random.randint", return_value=5):
        success, result = player_flee_check(state)

    assert success is False
    assert state.phase == "ONGOING"


def test_player_flee_check_dc_is_max_enemy_dex_mod_plus_10():
    """Verify DC formula: max(alive enemy DEX_mod) + 10."""
    player = _player(dex_score=10)  # mod+0
    goblin = _goblin()
    goblin["ability_scores"]["DEX"] = 12  # mod+1
    state = _make_combat_state(player, [goblin])

    # DC should be 1+10=11. nat 1 = auto fail
    with patch("random.randint", return_value=1):
        success, check_result = player_flee_check(state)

    assert success is False
    assert check_result.dc == 11


# ---------------------------------------------------------------------------
# 9. cleanup_combat
# ---------------------------------------------------------------------------

def test_cleanup_combat_killed_goblin_cr_1_4_gives_50_xp():
    player = _player()
    goblin = _goblin(cr="1/4")
    state = _make_combat_state(player, [goblin])
    state.npcs["Goblin"]["Status"] = "Dead"
    state.npcs["Goblin"]["hp_current"] = 0
    state.phase = "PLAYER_WIN"

    result = cleanup_combat(state)
    assert result["player_xp_gained"] == 50


def test_cleanup_combat_killed_bandit_captain_cr_2_gives_450_xp():
    player = _player()
    bc = _bandit_captain()
    state = _make_combat_state(player, [bc])
    state.npcs["Bandit Captain"]["Status"] = "Dead"
    state.npcs["Bandit Captain"]["hp_current"] = 0
    state.phase = "PLAYER_WIN"

    result = cleanup_combat(state)
    assert result["player_xp_gained"] == 450


def test_cleanup_combat_returns_all_required_keys():
    player = _player()
    goblin = _goblin()
    state = _make_combat_state(player, [goblin])
    state.npcs["Goblin"]["Status"] = "Dead"
    state.phase = "PLAYER_WIN"

    result = cleanup_combat(state)
    assert "player_hp_change" in result
    assert "player_xp_gained" in result
    assert "npcs_status_changes" in result
    assert "combat_summary" in result


def test_cleanup_combat_hp_change_reflects_damage_taken():
    player = _player(hp=20)
    state = _make_combat_state(player, [_goblin()])
    state.player_snapshot["hp_current"] = 12  # took 8 damage
    state.phase = "PLAYER_WIN"
    state.npcs["Goblin"]["Status"] = "Dead"

    result = cleanup_combat(state)
    assert result["player_hp_change"] == -8


def test_cleanup_combat_npc_changes_list_per_npc():
    player = _player()
    goblin1 = _goblin("G1", cr="1/4")
    goblin2 = _goblin("G2", cr="1/4")
    state = _make_combat_state(player, [goblin1, goblin2])
    state.npcs["G1"]["Status"] = "Dead"
    state.npcs["G2"]["Status"] = "Dead"
    state.phase = "PLAYER_WIN"

    result = cleanup_combat(state)
    assert len(result["npcs_status_changes"]) == 2
    assert result["player_xp_gained"] == 100  # 2 × 50


# ---------------------------------------------------------------------------
# 10. E2E mini-encounter: Player (Knight L1) vs 2 Goblins — player wins in 2 rounds
# ---------------------------------------------------------------------------

def test_e2e_player_wins_against_two_goblins_100_xp():
    """
    Knight L1: HP=11, AC=18, Longsword 1d8+3, STR=16(+3), prof=2
    2× Goblin: HP=7, AC=12, CR=1/4

    Round 1: Player attacks Goblin A with nat=15 → to_hit=20 ≥ 12 → HIT, dmg=5+3=8 → Goblin A dead (7-8=0).
             Player attacks Goblin B with nat=15 → HIT, dmg=5+3=8 → Goblin B dead.
    Both dead → PLAYER_WIN, XP = 2×50 = 100.

    We mock random.randint in core.dnd_core (attack rolls) and core.dnd_combat (damage rolls).
    """
    player = _player(name="Knight", level=1, hp=11, ac=18, str_score=16, dex_score=14)
    goblin_a = _goblin("Goblin A", hp=7, ac=12, cr="1/4")
    goblin_b = _goblin("Goblin B", hp=7, ac=12, cr="1/4")

    state = _make_combat_state(player, [goblin_a, goblin_b], chat_id=99)

    # Attack Goblin A: nat=15 → to_hit=20 ≥ AC 12 → HIT; damage die=5 → 8 dmg > 7 hp → Dead
    # Call sequence per attack: roll_d20 (1 randint call), then _roll_damage (1 randint call)
    with patch("random.randint", side_effect=[15, 5]):
        result_a = player_attack(state, "Goblin A", "Longsword")

    assert result_a.hit is True
    assert state.npcs["Goblin A"]["Status"] == "Dead"

    # Attack Goblin B same way
    with patch("random.randint", side_effect=[15, 5]):
        result_b = player_attack(state, "Goblin B", "Longsword")

    assert result_b.hit is True
    assert state.npcs["Goblin B"]["Status"] == "Dead"

    # End round — both dead → PLAYER_WIN
    phase = end_round(state)
    assert phase == "PLAYER_WIN"

    # Cleanup
    result = cleanup_combat(state)
    assert result["player_xp_gained"] == 100
    assert result["combat_summary"] is not None
