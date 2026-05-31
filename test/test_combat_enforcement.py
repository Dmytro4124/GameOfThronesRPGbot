"""Pack C — Dodge enforcement + weapon/armor proficiency tests.

Real proficiency table extracted from core/dnd_classes.py (source of truth):

| Class        | weapon_profs                                              | armor_profs                         |
|--------------|-----------------------------------------------------------|-------------------------------------|
| Knight       | ["simple", "martial"]                                     | ["light", "medium", "heavy", "shields"] |
| Hedge Knight | ["simple", "martial"]                                     | ["light", "medium", "shields"]      |
| Maester      | ["simple"]                                                | []                                  |
| Septon       | ["simple"]                                                | ["light", "medium"]                 |
| Sellsword    | ["simple", "martial"]                                     | ["light", "medium"]                 |
| Spy          | ["simple", "hand crossbows", "longswords", "rapiers", "shortswords"] | ["light"] |
| Courtier     | ["simple", "rapiers"]                                     | ["light"]                           |
| Bastard      | ["simple", "martial"]                                     | ["light", "medium"]                 |
| Wildling     | ["simple", "martial"]                                     | ["light", "medium"]                 |

Key observations:
- Maester: armor_profs=[] (no armor at all)
- Spy: specific named weapons instead of "martial" category
- Courtier: only "rapiers" as a specific weapon plus "simple"
- Knight/Hedge Knight/Sellsword/Bastard/Wildling: have "martial" → proficient with everything
- Septon: "simple" only + light+medium armor
- Armor hierarchy: "heavy" ⊃ "medium" ⊃ "light"

All tests are pure-unit; no DB or LLM calls.
"""

import copy
import pytest
from unittest.mock import patch

from core.dnd_combat import (
    CombatState,
    CombatantRef,
    player_attack,
    npc_attack,
    is_proficient_with_weapon,
    is_proficient_with_armor,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_state(player: dict, npc: dict | None = None, extra_npcs: dict | None = None) -> CombatState:
    """Build a minimal CombatState for tests."""
    _npc = npc or {
        "Name": "TargetNPC",
        "hp_current": 100,
        "hp_max": 100,
        "ac": 5,   # low → most rolls hit
        "Status": "Active",
        "conditions": [],
        "ability_scores": {"STR": 10, "DEX": 10},
        "features": [],
        "heritage": "",
    }
    npc_name = _npc.get("Name", "TargetNPC")
    player_snap = copy.deepcopy(player)
    player_snap.setdefault("conditions", [])

    player_name = player.get("Ім'я", "Player")
    combatants = [
        CombatantRef(kind="player", name=player_name, init_roll=15),
        CombatantRef(kind="npc",    name=npc_name,    init_roll=8),
    ]
    npcs = {npc_name: copy.deepcopy(_npc)}
    if extra_npcs:
        npcs.update(extra_npcs)
        for en_name in extra_npcs:
            combatants.append(CombatantRef(kind="npc", name=en_name, init_roll=6))

    return CombatState(
        chat_id=99,
        round=1,
        initiative_order=combatants,
        current_actor_idx=0,
        npcs=npcs,
        player_snapshot=player_snap,
    )


def _base_player(
    name="Hero",
    level=1,
    str_score=14,
    dex_score=10,
    player_class="Knight",
    equipped_weapon=None,
    equipment=None,
    equipped_armor=None,
):
    """Build a minimal player profile."""
    p = {
        "Ім'я": name,
        "class": player_class,
        "level": level,
        "hp_current": 30,
        "hp_max": 30,
        "ac": 10,
        "ability_scores": {
            "STR": str_score,
            "DEX": dex_score,
            "CON": 10,
            "INT": 10,
            "WIS": 10,
            "CHA": 10,
        },
        "saves_proficient": [],
        "conditions": [],
        "features": [],
        "heritage": "Westerosi (Andal)",
        "equipped_shield": False,
    }
    if equipped_weapon is not None:
        p["equipped_weapon"] = equipped_weapon
    if equipment is not None:
        p["equipment"] = equipment
    if equipped_armor is not None:
        p["equipped_armor"] = equipped_armor
    return p


def _longsword():
    return {
        "name": "Довгий меч",
        "damage_dice": "1d8",
        "damage_type": "slashing",
        "properties": [],
    }


def _dagger():
    return {
        "name": "Кинджал",
        "damage_dice": "1d4",
        "damage_type": "piercing",
        "properties": ["finesse"],
    }


def _rapier():
    return {
        "name": "Рапіра",
        "damage_dice": "1d8",
        "damage_type": "piercing",
        "properties": ["finesse"],
    }


# ===========================================================================
# C1 — Dodge enforcement
# ===========================================================================

class TestC1DodgeEnforcement:

    def test_npc_dodging_causes_player_attack_disadvantage(self):
        """NPC with _dodge_this_round=True → player_attack rolls 2d20 and takes min."""
        player = _base_player(equipped_weapon=_longsword())
        state = _make_state(player)
        # Flag the NPC as dodging
        state.npcs["TargetNPC"]["_dodge_this_round"] = True

        d20_calls = []
        def _capture_randint(lo, hi):
            val = 15 if len(d20_calls) < 2 else 4
            d20_calls.append(val)
            return val

        # With disadvantage: roll_d20 makes 2 calls, returns min
        with patch("random.randint", side_effect=[15, 12, 4]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        # roll_d20 with disadvantage: calls randint twice for d20, then once for damage
        # We verify disadvantage fired by checking final_dis on the result path:
        # the natural roll must be min(15, 12)=12, not the first roll
        assert result.natural_roll == 12, (
            f"Dodge should apply disadvantage (min of 2 rolls). "
            f"Expected natural=12, got {result.natural_roll}"
        )

    def test_npc_not_dodging_causes_single_d20_roll(self):
        """NPC without _dodge_this_round → normal roll (no disadvantage from dodge)."""
        player = _base_player(equipped_weapon=_longsword())
        state = _make_state(player)
        # No dodge flag set

        # Normal roll: 1 d20 call then 1 damage roll
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 15
        assert result.hit is True

    def test_player_dodging_causes_npc_attack_disadvantage(self):
        """player_snapshot with _dodge_this_round=True → npc_attack rolls 2d20, takes min."""
        player = _base_player()
        state = _make_state(player)
        state.player_snapshot["_dodge_this_round"] = True
        # Give NPC a proper attack
        state.npcs["TargetNPC"]["attacks"] = [
            {"name": "Меч", "to_hit": 3, "dmg": "1d6 slashing"}
        ]
        state.player_snapshot["ac"] = 20  # high AC to make hit/miss irrelevant for this check

        with patch("random.randint", side_effect=[18, 10]):
            result = npc_attack(state, "TargetNPC", target="player")

        # Disadvantage: min(18, 10)=10
        assert result.natural_roll == 10, (
            f"Player dodge should impose disadvantage on NPC attack. "
            f"Expected natural=10, got {result.natural_roll}"
        )

    def test_player_not_dodging_npc_attack_normal(self):
        """No _dodge_this_round on player → npc_attack uses single roll."""
        player = _base_player()
        state = _make_state(player)
        # No dodge flag on player
        state.npcs["TargetNPC"]["attacks"] = [
            {"name": "Меч", "to_hit": 3, "dmg": "1d6 slashing"}
        ]
        state.player_snapshot["ac"] = 5  # guaranteed hit

        with patch("random.randint", side_effect=[15, 3]):
            result = npc_attack(state, "TargetNPC", target="player")

        # Single roll, no disadvantage
        assert result.natural_roll == 15
        assert result.hit is True

    def test_dodge_flag_is_npc_specific(self):
        """NPC_A dodges; player attacks NPC_B — NPC_B attack should have no dodge disadvantage."""
        player = _base_player(equipped_weapon=_longsword())
        npc_a = {
            "Name": "NPC_A",
            "hp_current": 50,
            "hp_max": 50,
            "ac": 5,
            "Status": "Active",
            "conditions": [],
            "features": [],
            "heritage": "",
        }
        npc_b = {
            "Name": "NPC_B",
            "hp_current": 50,
            "hp_max": 50,
            "ac": 5,
            "Status": "Active",
            "conditions": [],
            "features": [],
            "heritage": "",
        }
        state = _make_state(player, npc=npc_a, extra_npcs={"NPC_B": copy.deepcopy(npc_b)})

        # Only NPC_A is dodging
        state.npcs["NPC_A"]["_dodge_this_round"] = True

        # Attack NPC_B — no dodge flag on NPC_B
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "NPC_B", "Довгий меч")

        # Should be single roll (15), no disadvantage from NPC_A's dodge
        assert result.natural_roll == 15, (
            f"NPC_B has no dodge flag — expected single roll natural=15, got {result.natural_roll}"
        )

    def test_dodge_flag_cleared_means_no_disadvantage_second_access(self):
        """After clearing _dodge_this_round, flag is gone — next check reads False."""
        player = _base_player(equipped_weapon=_longsword())
        state = _make_state(player)

        # Simulate: flag is set, then cleared (as done in execute_combat_round)
        state.npcs["TargetNPC"]["_dodge_this_round"] = True
        state.npcs["TargetNPC"].pop("_dodge_this_round", None)

        assert not state.npcs["TargetNPC"].get("_dodge_this_round", False), (
            "After clearing _dodge_this_round, flag must be absent/False"
        )

        # Attack without dodge flag → no disadvantage
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 15


# ===========================================================================
# C2 — Weapon proficiency (is_proficient_with_weapon)
# ===========================================================================

class TestC2WeaponProficiencyFunction:
    """Unit tests for is_proficient_with_weapon standalone function."""

    # --- Classes with "martial" → proficient with everything ---

    def test_knight_longsword_proficient(self):
        """Knight has martial → proficient with Довгий меч."""
        assert is_proficient_with_weapon("Knight", "Довгий меч") is True

    def test_knight_rapier_proficient(self):
        """Knight has martial → proficient with any weapon including рапіра."""
        assert is_proficient_with_weapon("Knight", "Рапіра") is True

    def test_hedge_knight_longsword_proficient(self):
        assert is_proficient_with_weapon("Hedge Knight", "Довгий меч") is True

    def test_sellsword_longsword_proficient(self):
        assert is_proficient_with_weapon("Sellsword", "Довгий меч") is True

    def test_bastard_longsword_proficient(self):
        assert is_proficient_with_weapon("Bastard", "Довгий меч") is True

    def test_wildling_greataxe_proficient(self):
        assert is_proficient_with_weapon("Wildling", "Велика сокира") is True

    # --- Maester: simple only ---

    def test_maester_dagger_proficient(self):
        """Maester has simple → кинджал is simple → proficient."""
        assert is_proficient_with_weapon("Maester", "Кинджал") is True

    def test_maester_staff_proficient(self):
        """Maester has simple → палиця is simple → proficient."""
        assert is_proficient_with_weapon("Maester", "Палиця") is True

    def test_maester_longsword_not_proficient(self):
        """Maester has only simple → Довгий меч is martial → NOT proficient."""
        assert is_proficient_with_weapon("Maester", "Довгий меч") is False

    def test_maester_rapier_not_proficient(self):
        """Maester has only simple → Рапіра is martial → NOT proficient."""
        assert is_proficient_with_weapon("Maester", "Рапіра") is False

    # --- Septon: simple only ---

    def test_septon_mace_proficient(self):
        """Septon has simple → булава is simple → proficient."""
        assert is_proficient_with_weapon("Septon", "Булава") is True

    def test_septon_longsword_not_proficient(self):
        """Septon has only simple → Довгий меч is martial → NOT proficient."""
        assert is_proficient_with_weapon("Septon", "Довгий меч") is False

    # --- Spy: specific named weapons (hand crossbows, longswords, rapiers, shortswords) ---

    def test_spy_rapier_proficient(self):
        """Spy has 'rapiers' in weapon_profs → Рапіра should match via alias."""
        assert is_proficient_with_weapon("Spy", "Рапіра") is True

    def test_spy_longsword_proficient(self):
        """Spy has 'longswords' → Довгий меч should match via _UA_TO_EN_STEMS."""
        assert is_proficient_with_weapon("Spy", "Довгий меч") is True

    def test_spy_shortsword_proficient(self):
        """Spy has 'shortswords' → Короткий меч should match via _UA_TO_EN_STEMS."""
        assert is_proficient_with_weapon("Spy", "Короткий меч") is True

    def test_spy_dagger_proficient(self):
        """Spy has 'simple' → dagger (кинджал) is simple → proficient."""
        assert is_proficient_with_weapon("Spy", "Кинджал") is True

    def test_spy_greataxe_not_proficient(self):
        """Spy has no 'martial' and велика сокира is martial → NOT proficient."""
        assert is_proficient_with_weapon("Spy", "Велика сокира") is False

    # --- Courtier: rapiers + simple ---

    def test_courtier_rapier_proficient(self):
        """Courtier has 'rapiers' → Рапіра proficient."""
        assert is_proficient_with_weapon("Courtier", "Рапіра") is True

    def test_courtier_dagger_proficient(self):
        """Courtier has 'simple' → Кинджал (simple) proficient."""
        assert is_proficient_with_weapon("Courtier", "Кинджал") is True

    def test_courtier_longsword_not_proficient(self):
        """Courtier has only 'rapiers' and 'simple'; Довгий меч is martial → NOT proficient."""
        assert is_proficient_with_weapon("Courtier", "Довгий меч") is False

    def test_courtier_greataxe_not_proficient(self):
        """Courtier: Велика сокира is martial → NOT proficient."""
        assert is_proficient_with_weapon("Courtier", "Велика сокира") is False

    # --- Fail-safe cases ---

    def test_unknown_weapon_failsafe_returns_true(self):
        """Unknown weapon name → fail-safe True (don't penalize missing data)."""
        assert is_proficient_with_weapon("Maester", "стільчик") is True

    def test_unknown_class_failsafe_returns_true(self):
        """Unknown class → fail-safe True."""
        assert is_proficient_with_weapon("Archmage", "Довгий меч") is True

    def test_empty_class_failsafe_returns_true(self):
        """Empty class string → fail-safe True."""
        assert is_proficient_with_weapon("", "Довгий меч") is True

    def test_empty_weapon_failsafe_returns_true(self):
        """Empty weapon string → fail-safe True."""
        assert is_proficient_with_weapon("Maester", "") is True


# ===========================================================================
# C2 — Weapon proficiency integrated into player_attack
# ===========================================================================

class TestC2WeaponProficiencyInAttack:
    """Verify that player_attack uses/ignores proficiency bonus based on C2 logic."""

    def test_knight_longsword_gets_proficiency_bonus(self):
        """Knight with longsword: proficiency=+2 at L1 must be added to to_hit."""
        player = _base_player(
            player_class="Knight",
            level=1,
            str_score=10,       # mod=0, isolate proficiency effect
            equipped_weapon=_longsword(),
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        # to_hit = 15 (natural) + 0 (STR_mod) + 2 (prof) = 17
        assert result.to_hit_total == 17, (
            f"Knight at L1 should get +2 proficiency with longsword. "
            f"Expected to_hit=17, got {result.to_hit_total}"
        )

    def test_maester_longsword_no_proficiency_bonus(self):
        """Maester with longsword: NOT proficient → prof=0, no proficiency added to to_hit."""
        player = _base_player(
            player_class="Maester",
            level=1,
            str_score=10,       # mod=0
            equipped_weapon=_longsword(),
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        # to_hit = 15 (natural) + 0 (STR_mod) + 0 (no prof) = 15
        assert result.to_hit_total == 15, (
            f"Maester should NOT get proficiency with longsword. "
            f"Expected to_hit=15, got {result.to_hit_total}"
        )

    def test_maester_dagger_gets_proficiency_bonus(self):
        """Maester with dagger (simple): proficient → prof=+2 added."""
        player = _base_player(
            player_class="Maester",
            level=1,
            str_score=10,
            dex_score=10,
            equipped_weapon=_dagger(),  # dagger is finesse, uses max(STR, DEX) = 0
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 2]):
            result = player_attack(state, "TargetNPC", "Кинджал")

        # to_hit = 15 + 0 (finesse: max(0,0)) + 2 (prof) = 17
        assert result.to_hit_total == 17, (
            f"Maester should be proficient with dagger (simple). "
            f"Expected to_hit=17, got {result.to_hit_total}"
        )

    def test_knight_vs_maester_proficiency_difference(self):
        """Verify Knight vs Maester proficiency difference for same weapon/roll."""
        weapon = _longsword()

        player_knight = _base_player(
            player_class="Knight",
            level=1,
            str_score=10,
            equipped_weapon=weapon,
        )
        player_maester = _base_player(
            player_class="Maester",
            level=1,
            str_score=10,
            equipped_weapon=weapon,
        )

        state_k = _make_state(player_knight)
        state_m = _make_state(player_maester)

        with patch("random.randint", side_effect=[15, 3]):
            result_k = player_attack(state_k, "TargetNPC", "Довгий меч")
        with patch("random.randint", side_effect=[15, 3]):
            result_m = player_attack(state_m, "TargetNPC", "Довгий меч")

        # Knight: 15+0+2=17; Maester: 15+0+0=15; difference=2
        diff = result_k.to_hit_total - result_m.to_hit_total
        assert diff == 2, (
            f"Knight to_hit should exceed Maester by exactly +2 (proficiency). "
            f"Knight={result_k.to_hit_total}, Maester={result_m.to_hit_total}, diff={diff}"
        )


# ===========================================================================
# C3 — Armor proficiency (is_proficient_with_armor)
# ===========================================================================

class TestC3ArmorProficiencyFunction:
    """Unit tests for is_proficient_with_armor standalone function."""

    # --- Knight: light/medium/heavy/shields ---

    def test_knight_heavy_armor_proficient(self):
        assert is_proficient_with_armor("Knight", "heavy") is True

    def test_knight_medium_armor_proficient(self):
        assert is_proficient_with_armor("Knight", "medium") is True

    def test_knight_light_armor_proficient(self):
        assert is_proficient_with_armor("Knight", "light") is True

    # --- Maester: no armor at all ---

    def test_maester_heavy_armor_not_proficient(self):
        """Maester has empty armor_profs → NOT proficient with heavy."""
        assert is_proficient_with_armor("Maester", "heavy") is False

    def test_maester_medium_armor_not_proficient(self):
        """Maester has empty armor_profs → NOT proficient with medium."""
        assert is_proficient_with_armor("Maester", "medium") is False

    def test_maester_light_armor_not_proficient(self):
        """Maester has empty armor_profs → NOT proficient with light."""
        assert is_proficient_with_armor("Maester", "light") is False

    # --- Septon: light + medium ---

    def test_septon_light_armor_proficient(self):
        assert is_proficient_with_armor("Septon", "light") is True

    def test_septon_medium_armor_proficient(self):
        assert is_proficient_with_armor("Septon", "medium") is True

    def test_septon_heavy_armor_not_proficient(self):
        """Septon has only light+medium → heavy NOT proficient."""
        assert is_proficient_with_armor("Septon", "heavy") is False

    # --- Sellsword: light + medium ---

    def test_sellsword_medium_armor_proficient(self):
        assert is_proficient_with_armor("Sellsword", "medium") is True

    def test_sellsword_heavy_armor_not_proficient(self):
        assert is_proficient_with_armor("Sellsword", "heavy") is False

    # --- Spy: light only ---

    def test_spy_light_armor_proficient(self):
        assert is_proficient_with_armor("Spy", "light") is True

    def test_spy_medium_armor_not_proficient(self):
        assert is_proficient_with_armor("Spy", "medium") is False

    def test_spy_heavy_armor_not_proficient(self):
        assert is_proficient_with_armor("Spy", "heavy") is False

    # --- Courtier: light only ---

    def test_courtier_light_armor_proficient(self):
        assert is_proficient_with_armor("Courtier", "light") is True

    def test_courtier_medium_armor_not_proficient(self):
        assert is_proficient_with_armor("Courtier", "medium") is False

    # --- Hierarchy tests ---

    def test_heavy_prof_implies_medium(self):
        """Knight has heavy → must also be proficient with medium."""
        assert is_proficient_with_armor("Knight", "medium") is True

    def test_heavy_prof_implies_light(self):
        """Knight has heavy → must also be proficient with light."""
        assert is_proficient_with_armor("Knight", "light") is True

    def test_medium_prof_implies_light(self):
        """Septon has medium → must also be proficient with light."""
        assert is_proficient_with_armor("Septon", "light") is True

    def test_medium_prof_does_not_imply_heavy(self):
        """Septon has medium but NOT heavy → should fail for heavy."""
        assert is_proficient_with_armor("Septon", "heavy") is False

    # --- Fail-safe cases ---

    def test_unknown_class_failsafe_returns_true(self):
        assert is_proficient_with_armor("Archmage", "heavy") is True

    def test_empty_class_failsafe_returns_true(self):
        assert is_proficient_with_armor("", "heavy") is True

    def test_empty_armor_type_failsafe_returns_true(self):
        assert is_proficient_with_armor("Maester", "") is True

    def test_none_class_equivalent_empty(self):
        """Passing None as class_name (edge case) — fail-safe."""
        # is_proficient_with_armor checks: if not class_name → True
        assert is_proficient_with_armor(None, "heavy") is True


# ===========================================================================
# C3 — Armor proficiency integrated into player_attack
# ===========================================================================

class TestC3ArmorProficiencyInAttack:
    """Verify player_attack applies disadvantage when wearing non-proficient armor."""

    def test_knight_chainmail_no_disadvantage(self):
        """Knight (heavy proficient) in chainmail (medium) → no armor-based disadvantage."""
        player = _base_player(
            player_class="Knight",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
            equipped_armor={"ac_base": 16, "type": "medium"},
        )
        state = _make_state(player)

        # Single roll (no disadvantage) → 15 natural
        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 15, (
            f"Knight in medium armor should have no disadvantage. natural={result.natural_roll}"
        )

    def test_maester_chainmail_causes_disadvantage(self):
        """Maester (no armor prof) wearing chainmail (medium) → disadvantage on attacks."""
        player = _base_player(
            player_class="Maester",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
            equipped_armor={"ac_base": 16, "type": "medium"},
        )
        state = _make_state(player)

        # Disadvantage: roll_d20 makes 2 calls, takes min(18, 10)=10
        with patch("random.randint", side_effect=[18, 10, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 10, (
            f"Maester wearing medium armor should get disadvantage (min of 2 rolls). "
            f"Expected natural=10, got {result.natural_roll}"
        )

    def test_no_equipped_armor_no_disadvantage(self):
        """No equipped_armor at all → C3 block skipped, no penalty."""
        player = _base_player(
            player_class="Maester",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
            # equipped_armor=None (default)
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 15, (
            "No equipped armor → no disadvantage regardless of class"
        )

    def test_sellsword_plate_heavy_causes_disadvantage(self):
        """Sellsword (medium only) wearing plate (heavy) → disadvantage."""
        player = _base_player(
            player_class="Sellsword",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
            equipped_armor={"ac_base": 18, "type": "heavy"},
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[18, 10, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 10, (
            f"Sellsword in heavy armor should get disadvantage. natural={result.natural_roll}"
        )

    def test_sellsword_medium_no_disadvantage(self):
        """Sellsword (medium proficient) wearing medium armor → no disadvantage."""
        player = _base_player(
            player_class="Sellsword",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
            equipped_armor={"ac_base": 14, "type": "medium"},
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        assert result.natural_roll == 15

    def test_septon_heavy_armor_causes_disadvantage(self):
        """Septon (light+medium only) wearing heavy → disadvantage."""
        player = _base_player(
            player_class="Septon",
            level=1,
            str_score=10,
            equipped_weapon={
                "name": "Булава",
                "damage_dice": "1d6",
                "damage_type": "bludgeoning",
                "properties": [],
            },
            equipped_armor={"ac_base": 16, "type": "heavy"},
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[18, 10, 3]):
            result = player_attack(state, "TargetNPC", "Булава")

        assert result.natural_roll == 10, (
            f"Septon in heavy armor should get disadvantage. natural={result.natural_roll}"
        )


# ===========================================================================
# C1+C3 combination — both flags simultaneously
# ===========================================================================

class TestC1C3Combined:
    """Verify that dodge + armor penalty both stack (both impose disadvantage)."""

    def test_dodge_and_armor_penalty_both_cause_disadvantage(self):
        """Maester (non-proficient armor) attacking a dodging NPC — disadvantage applies."""
        player = _base_player(
            player_class="Maester",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
            equipped_armor={"ac_base": 16, "type": "medium"},
        )
        state = _make_state(player)
        state.npcs["TargetNPC"]["_dodge_this_round"] = True

        # Disadvantage rolls 2d20 → takes min
        with patch("random.randint", side_effect=[18, 10, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч")

        # final_dis=True from BOTH armor AND dodge; roll_d20 with dis rolls 2 times
        assert result.natural_roll == 10, (
            f"Both armor penalty and dodge should cause disadvantage. natural={result.natural_roll}"
        )

    def test_advantage_plus_disadvantage_cancel_out(self):
        """Advantage (from tactic) + disadvantage (from dodge) → cancel → normal single roll."""
        player = _base_player(
            player_class="Knight",
            level=1,
            str_score=10,
            equipped_weapon=_longsword(),
        )
        state = _make_state(player)
        state.npcs["TargetNPC"]["_dodge_this_round"] = True

        # D&D: advantage + disadvantage → normal. Pass advantage=True, NPC dodge sets dis=True.
        # roll_d20(advantage=True, disadvantage=True) → both cancelled → single roll
        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Довгий меч", advantage=True, disadvantage=False)

        # final_adv=True, final_dis=True (from dodge) → cancel → single roll → natural=15
        assert result.natural_roll == 15, (
            f"Advantage + disadvantage should cancel. natural={result.natural_roll}"
        )
