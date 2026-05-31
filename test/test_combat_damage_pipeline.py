"""Pack A — Combat damage pipeline tests.

Covers A1 (weapon source priority), A2 (ability_dmg_mod), A3 (versatile dice),
A4 (recompute_ac after ASI).  All tests are pure-unit; no DB or LLM calls.
"""

import copy
import pytest
from unittest.mock import patch

from core.dnd_combat import (
    CombatState,
    CombatantRef,
    player_attack,
)
from core.dnd_progression import apply_asi
from core.dnd_engine import recompute_ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(player: dict, npc: dict | None = None) -> CombatState:
    """Build a minimal CombatState for player_attack tests."""
    import copy as _copy

    _npc = npc or {
        "Name": "TargetNPC",
        "hp_current": 100,
        "hp_max": 100,
        "ac": 5,  # low so hits are guaranteed
        "Status": "Active",
        "conditions": [],
        "ability_scores": {"STR": 10, "DEX": 10},
        "features": [],
        "heritage": "",
    }
    npc_name = _npc.get("Name", "TargetNPC")
    player_snap = _copy.deepcopy(player)
    player_snap.setdefault("conditions", [])

    player_name = player.get("Ім'я", "Player")
    combatants = [
        CombatantRef(kind="player", name=player_name, init_roll=15),
        CombatantRef(kind="npc",    name=npc_name,    init_roll=8),
    ]
    return CombatState(
        chat_id=99,
        round=1,
        initiative_order=combatants,
        current_actor_idx=0,
        npcs={npc_name: _copy.deepcopy(_npc)},
        player_snapshot=player_snap,
    )


def _base_player(
    name="Hero",
    level=1,
    str_score=10,
    dex_score=10,
    equipped_weapon=None,
    equipment=None,
    weapon_legacy=None,
    equipped_shield=False,
):
    """Build a player profile.  Caller controls weapon source fields."""
    p = {
        "Ім'я": name,
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
        "equipped_shield": equipped_shield,
    }
    if equipped_weapon is not None:
        p["equipped_weapon"] = equipped_weapon
    if equipment is not None:
        p["equipment"] = equipment
    if weapon_legacy is not None:
        p["Зброя"] = weapon_legacy
    return p


# ===========================================================================
# A1 — Weapon source priority
# ===========================================================================

class TestA1WeaponSource:

    def test_primary_equipped_weapon_used(self):
        """When equipped_weapon is a valid dict it takes priority over equipment."""
        player = _base_player(
            str_score=14,  # mod +2
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_type": "slashing",
                "properties": [],
            },
            # This fallback should be IGNORED:
            equipment={"weapon_main": "Dagger (1d4 piercing)"},
        )
        state = _make_state(player)
        # d20=15 → hit (15+2+2=19 >= AC 5), d8=4 → raw_dice=4, ability=+2 → total=6
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        assert "slashing" in result.damage_type
        # damage = 4 (d8 die) + 0 (string mod) + 2 (STR_mod) = 6
        assert result.damage_total == 6

    def test_fallback_to_equipment_weapon_main(self):
        """No equipped_weapon → uses equipment['weapon_main'] string.
        weapon_name must be empty so the code keeps weapon_str = weapon_main
        (non-empty weapon_name that doesn't match weapon_main replaces it).
        """
        player = _base_player(
            str_score=10,  # mod +0
            equipment={"weapon_main": "Longsword (1d8 slashing)"},
        )
        state = _make_state(player)
        # d20=15 → hit (15+0+2=17 >= AC 5), d8=3 → raw_dice=3, ability=+0 → total=3
        # weapon_name="" ensures weapon_str stays as equipment["weapon_main"]
        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "")

        assert result.hit is True
        # damage = 3 (d8 die) + 0 (no string modifier) + 0 (STR_mod) = 3
        assert result.damage_total == 3

    def test_fallback_to_legacy_zbroya_field(self):
        """No equipped_weapon, no equipment → uses profile['Зброя']."""
        player = _base_player(
            str_score=10,
            weapon_legacy="Спис (1d6 колота)",
        )
        state = _make_state(player)
        # d20=15 → hit, d6=2 → raw_dice=2, ability=+0 → total=2
        with patch("random.randint", side_effect=[15, 2]):
            result = player_attack(state, "TargetNPC", "Спис")

        assert result.hit is True
        assert result.damage_total == 2  # 2 + 0 + 0

    def test_final_fallback_unarmed(self):
        """No weapon fields at all → Unarmed (1d4 bludgeoning), STR_mod applied."""
        player = _base_player(str_score=12)  # mod +1
        state = _make_state(player)
        # d20=15 → hit, d4=3 → raw_dice=3, ability=+1 → total=4
        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Unarmed")

        assert result.hit is True
        # 3 (d4) + 0 (no string mod for unarmed) + 1 (STR_mod) = 4
        assert result.damage_total == 4
        assert "bludgeoning" in result.damage_type


# ===========================================================================
# A2 — Ability modifier added to damage
# ===========================================================================

class TestA2AbilityModDamage:

    def test_str_mod_applied_for_melee(self):
        """STR 18 (mod +4) with non-finesse longsword.  Die=5 → total=5+4=9."""
        player = _base_player(
            str_score=18,  # mod +4
            dex_score=10,
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_type": "slashing",
                "properties": [],  # no finesse, no ranged
            },
        )
        state = _make_state(player)
        # d20=15 → hit, d8=5
        with patch("random.randint", side_effect=[15, 5]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        assert result.damage_total == 9  # 5 + 4 (STR)

    def test_dex_mod_wins_for_finesse_when_dex_higher(self):
        """DEX 16 (+3) > STR 10 (+0) with finesse → uses DEX_mod=+3."""
        player = _base_player(
            str_score=10,
            dex_score=16,  # mod +3
            equipped_weapon={
                "name": "Rapier",
                "damage_dice": "1d8",
                "damage_type": "piercing",
                "properties": ["finesse"],
            },
        )
        state = _make_state(player)
        # d20=15 → hit (uses DEX for attack too: 15+3+2=20), d8=4
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Rapier")

        assert result.hit is True
        assert result.damage_total == 7  # 4 + 3 (DEX)

    def test_str_mod_wins_for_finesse_when_str_higher(self):
        """STR 16 (+3) > DEX 12 (+1) with finesse → uses max(+3, +1)=+3."""
        player = _base_player(
            str_score=16,
            dex_score=12,
            equipped_weapon={
                "name": "Rapier",
                "damage_dice": "1d8",
                "damage_type": "piercing",
                "properties": ["finesse"],
            },
        )
        state = _make_state(player)
        # d20=15 → hit, d8=4 → damage = 4 + 3 = 7
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Rapier")

        assert result.hit is True
        assert result.damage_total == 7  # 4 + 3 (STR, since STR > DEX)

    def test_dex_mod_for_ranged(self):
        """DEX 14 (+2), STR 8 (-1) with ranged property → uses DEX_mod=+2."""
        player = _base_player(
            str_score=8,   # mod -1
            dex_score=14,  # mod +2
            equipped_weapon={
                "name": "Shortbow",
                "damage_dice": "1d6",
                "damage_type": "piercing",
                "properties": ["ranged"],
            },
        )
        state = _make_state(player)
        # d20=15 → hit (15+2+2=19), d6=3 → damage = 3 + 2 = 5
        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Shortbow")

        assert result.hit is True
        assert result.damage_total == 5  # 3 + 2 (DEX)

    def test_str_mod_for_thrown_non_finesse(self):
        """STR 14 (+2), DEX 10 (+0) with thrown (not finesse) → STR_mod=+2."""
        player = _base_player(
            str_score=14,
            dex_score=10,
            equipped_weapon={
                "name": "Handaxe",
                "damage_dice": "1d6",
                "damage_type": "slashing",
                "properties": ["thrown"],
            },
        )
        state = _make_state(player)
        # d20=15 → hit, d6=3 → damage = 3 + 2 = 5
        with patch("random.randint", side_effect=[15, 3]):
            result = player_attack(state, "TargetNPC", "Handaxe")

        assert result.hit is True
        assert result.damage_total == 5  # 3 + 2 (STR)

    def test_crit_doubles_dice_not_ability_mod(self):
        """Critical hit: dice are doubled, ability_mod added ONCE.

        STR 18 (+4), 1d8 weapon.  Crit → roll 2d8.  With side_effect=[20, 4, 3]:
        natural=20 (crit), die1=4, die2=3 → raw_dice=7, ability=+4 → total=11.
        """
        player = _base_player(
            str_score=18,
            equipped_weapon={
                "name": "Greatsword",
                "damage_dice": "1d8",
                "damage_type": "slashing",
                "properties": [],
            },
        )
        state = _make_state(player)
        # natural=20 (crit), then 2 damage dice rolls
        with patch("random.randint", side_effect=[20, 4, 3]):
            result = player_attack(state, "TargetNPC", "Greatsword")

        assert result.critical is True
        # raw_dice = 4+3=7 (2 dice), ability_dmg_mod=+4 → total=7+4=11
        assert result.damage_total == 11

    def test_floor_enforcement_str_negative(self):
        """STR 6 (mod -2), d4 unarmed, roll=1 → max(1, 1+0+(-2))=max(1,-1)=1."""
        player = _base_player(str_score=6)  # no weapon fields → Unarmed 1d4
        state = _make_state(player)
        # d20=15 → hit, d4=1
        with patch("random.randint", side_effect=[15, 1]):
            result = player_attack(state, "TargetNPC", "Unarmed")

        assert result.hit is True
        # 1 (die) + 0 (no string mod) + (-2) (STR) = -1 → clamped to 1
        assert result.damage_total == 1


# ===========================================================================
# A3 — Versatile dice selection
# ===========================================================================

class TestA3VersatileDice:

    def test_versatile_no_shield_uses_larger_die(self):
        """Versatile weapon without shield → uses damage_dice_versatile (1d10)."""
        player = _base_player(
            str_score=10,  # mod 0 to keep math clean
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_dice_versatile": "1d10",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
            equipped_shield=False,
        )
        state = _make_state(player)
        # d20=15 → hit, d10=6 → damage = 6 + 0 = 6
        with patch("random.randint", side_effect=[15, 6]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        assert result.damage_total == 6  # from d10 roll

    def test_versatile_with_shield_uses_smaller_die(self):
        """Versatile weapon WITH shield → falls back to one-handed die (1d8)."""
        player = _base_player(
            str_score=10,
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_dice_versatile": "1d10",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
            equipped_shield=True,
        )
        state = _make_state(player)
        # d20=15 → hit, d8=6 → damage = 6 + 0 = 6
        with patch("random.randint", side_effect=[15, 6]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        # must have used d8 die path (same roll value, but die_size=8 was used)
        assert result.damage_total == 6

    def test_versatile_missing_versatile_key_fallback_to_damage_dice(self):
        """Legacy profile: versatile in properties but no damage_dice_versatile key.
        Must silently fall back to primary damage_dice (1d8) — no KeyError."""
        player = _base_player(
            str_score=10,
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                # damage_dice_versatile is intentionally absent
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
            equipped_shield=False,
        )
        state = _make_state(player)
        # d20=15 → hit, d8=5
        with patch("random.randint", side_effect=[15, 5]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        assert result.damage_total == 5  # 5 (d8) + 0 (STR_mod=0)

    def test_world_knight_kit_versatile_parsed_correctly(self):
        """_build_deterministic_dnd_profile for Knight class parses
        'Довгий меч (1d8 рублюча, дворуч 1d10)' correctly after A3-PARSE fix.

        The fixed parser uses re.search (finds dice anywhere in segment) and
        independent keyword detection (no elif), so 'дворуч 1d10' segment both
        sets _has_дворуч=True AND appends '1d10' to _dice_parts_seen.
        Result: properties == ["versatile"] and damage_dice_versatile == "1d10".
        """
        from core.world import _build_deterministic_dnd_profile

        profile = _build_deterministic_dnd_profile(
            char_name="TestKnight",
            house_name="Stark",
            origin_region="The North",
            suggested_class="Knight",
        )

        ew = profile.get("equipped_weapon")
        assert ew is not None, "equipped_weapon must be set for Knight"
        assert ew["damage_dice"] == "1d8", (
            f"Primary one-handed die must be 1d8, got {ew['damage_dice']!r}"
        )
        # A3-PARSE fixed: parser now correctly detects versatile for 'дворуч 1d10' segment.
        assert ew["properties"] == ["versatile"], (
            f"Knight longsword must have properties=['versatile'], got {ew['properties']!r}"
        )
        assert ew.get("damage_dice_versatile") == "1d10", (
            f"Knight longsword must have damage_dice_versatile='1d10', got {ew.get('damage_dice_versatile')!r}"
        )
        assert "two-handed" not in ew["properties"], (
            "Knight longsword must NOT be flagged as 'two-handed' (it is versatile)"
        )

    def test_world_parsing_synthetic_versatile_weapon(self):
        """Verifies the FIXED A3 parser for format 'X (1d8 type, дворуч 1d10)'.

        The fixed parser uses re.search (not re.match) so dice are found anywhere
        in a segment, and keyword detection is an independent `if` (not `elif`).
        This allows 'дворуч 1d10' to both register '1d10' in _dice_parts_seen AND
        set _has_дворуч=True in the same iteration.

        After the fix all assertions reflect CORRECT expected behavior:
        - _w_dice == "1d8" (primary one-handed die, unchanged)
        - _w_dice_versatile == "1d10" (second die pattern found by re.search)
        - "versatile" in _w_props (two dice → versatile, not two-handed)
        - "two-handed" NOT in _w_props
        """
        import re as _re

        weapon_str = "Бойова сокира (1d8 рублюча, дворуч 1d10)"
        _w_props: list = []
        _w_dice = "1d4"
        _w_dice_versatile = None
        _has_dворуч = False
        _dice_parts_seen: list = []

        _inner_match = _re.search(r'\(([^)]+)\)', weapon_str)
        assert _inner_match, "Regex must find parentheses content"
        _inner = _inner_match.group(1)
        for _p in [p.strip() for p in _inner.split(",")]:
            _p_lower = _p.lower()
            # FIXED: use re.search so dice are found anywhere in segment
            _dice_match = _re.search(r'(\d+)d(\d+)', _p_lower)
            if _dice_match:
                _dice_str = f"{_dice_match.group(1)}d{_dice_match.group(2)}"
                _dice_parts_seen.append(_dice_str)
                if len(_dice_parts_seen) == 1:
                    _w_dice = _dice_str
            # FIXED: keyword check is independent `if`, not `elif`
            if any(kw in _p_lower for kw in ("дворуч", "two-handed")):
                _has_dворуч = True
        if _has_dворуч:
            if len(_dice_parts_seen) >= 2:
                _w_dice_versatile = _dice_parts_seen[1]
                _w_props.append("versatile")
            else:
                _w_props.append("two-handed")

        assert _w_dice == "1d8"
        # A3-PARSE fixed: second die segment 'дворуч 1d10' now found by re.search
        assert _w_dice_versatile == "1d10", (
            f"Expected '1d10' as versatile die, got {_w_dice_versatile!r}"
        )
        # A3-PARSE fixed: two dice patterns → versatile, not two-handed
        assert "versatile" in _w_props, (
            f"Expected 'versatile' in properties, got {_w_props!r}"
        )
        assert "two-handed" not in _w_props, (
            f"'two-handed' must not appear for a versatile weapon, got {_w_props!r}"
        )

    def test_world_parsing_two_handed_no_second_dice(self):
        """Weapon with дворуч + single NdS → 'two-handed', not versatile."""
        import re as _re

        weapon_str = "Велика сокира (1d12 рублюча, дворучна)"
        _w_props: list = []
        _has_dворуч = False
        _dice_parts_seen: list = []

        _inner_match = _re.search(r'\(([^)]+)\)', weapon_str)
        _inner = _inner_match.group(1)
        for _p in [p.strip() for p in _inner.split(",")]:
            _p_lower = _p.lower()
            if _re.match(r'\d+d\d+', _p_lower):
                m = _re.match(r'(\d+d\d+)', _p_lower)
                if m:
                    _dice_parts_seen.append(m.group(1))
            elif any(kw in _p_lower for kw in ("дворуч", "two-handed")):
                _has_dворуч = True
        if _has_dворуч:
            if len(_dice_parts_seen) >= 2:
                _w_props.append("versatile")
            else:
                _w_props.append("two-handed")

        assert "two-handed" in _w_props
        assert "versatile" not in _w_props


# ===========================================================================
# A4 — recompute_ac after ASI
# ===========================================================================

class TestA4RecomputeAcAfterAsi:

    def test_dex_asi_updates_unarmored_ac(self):
        """No armor, DEX 12 (+1) → AC 11 initially.
        After apply_asi(DEX +2) → DEX 14 (+2) → AC must become 12."""
        profile = {
            "ability_scores": {
                "STR": 10, "DEX": 12, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "asi_pending": True,
            "ac": 11,          # 10 + DEX_mod(1)
            "equipped_armor": None,
            "equipped_shield": False,
        }

        apply_asi(profile, {"DEX": 2})

        assert profile["ability_scores"]["DEX"] == 14
        assert profile["ac"] == 12, (
            f"AC must recompute to 10+2=12 after DEX 12→14, got {profile['ac']}"
        )

    def test_dex_asi_heavy_armor_ignores_dex(self):
        """Heavy armor AC=16 ignores DEX bonus.  ASI DEX +2 must not change AC."""
        profile = {
            "ability_scores": {
                "STR": 14, "DEX": 12, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "asi_pending": True,
            "ac": 16,
            "equipped_armor": {"ac_base": 16, "type": "heavy"},
            "equipped_shield": False,
        }

        apply_asi(profile, {"DEX": 2})

        assert profile["ability_scores"]["DEX"] == 14
        assert profile["ac"] == 16, (
            f"Heavy armor AC must stay 16 regardless of DEX, got {profile['ac']}"
        )

    def test_str_asi_does_not_change_ac(self):
        """Idempotency: STR +2 has no effect on AC for unarmored character."""
        profile = {
            "ability_scores": {
                "STR": 14, "DEX": 12, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "asi_pending": True,
            "ac": 11,
            "equipped_armor": None,
            "equipped_shield": False,
        }

        apply_asi(profile, {"STR": 2})

        assert profile["ability_scores"]["STR"] == 16
        # DEX unchanged, armor unchanged → AC unchanged
        assert profile["ac"] == 11, (
            f"AC must remain 11 when only STR increased, got {profile['ac']}"
        )

    def test_dex_asi_medium_armor_capped_at_plus2(self):
        """Medium armor (ac_base=14): DEX_mod capped at +2.
        DEX 12 (+1) → initial AC=15 (14+1).  After ASI DEX +2 → DEX 14 (+2) → AC=16."""
        profile = {
            "ability_scores": {
                "STR": 10, "DEX": 12, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "asi_pending": True,
            "ac": 15,
            "equipped_armor": {"ac_base": 14, "type": "medium"},
            "equipped_shield": False,
        }

        apply_asi(profile, {"DEX": 2})

        assert profile["ability_scores"]["DEX"] == 14
        assert profile["ac"] == 16, (
            f"Medium armor 14+min(2,2)=16, got {profile['ac']}"
        )

    def test_shield_bonus_preserved_after_dex_asi(self):
        """Shield +2 must be preserved when AC recomputes after DEX ASI."""
        profile = {
            "ability_scores": {
                "STR": 10, "DEX": 12, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "asi_pending": True,
            "ac": 13,  # 10 + 1 (DEX) + 2 (shield)
            "equipped_armor": None,
            "equipped_shield": True,
        }

        apply_asi(profile, {"DEX": 2})

        # New AC: 10 + 2 (DEX) + 2 (shield) = 14
        assert profile["ac"] == 14, (
            f"AC must be 14 (10+2+2) after DEX increase with shield, got {profile['ac']}"
        )


# ===========================================================================
# recompute_ac standalone — direct unit tests
# ===========================================================================

class TestRecomputeAcDirect:
    """Direct unit tests for recompute_ac (imported from dnd_engine).

    These are fast, isolated, and serve as a reference for what A4 calls internally.
    """

    def test_no_armor_uses_dex_mod(self):
        profile = {
            "ability_scores": {"DEX": 14},
            "equipped_armor": None,
            "equipped_shield": False,
        }
        assert recompute_ac(profile) == 12  # 10 + 2

    def test_light_armor_adds_dex_mod(self):
        profile = {
            "ability_scores": {"DEX": 14},
            "equipped_armor": {"ac_base": 11, "type": "light"},
            "equipped_shield": False,
        }
        assert recompute_ac(profile) == 13  # 11 + 2

    def test_medium_armor_caps_dex_at_2(self):
        profile = {
            "ability_scores": {"DEX": 18},  # mod +4, but capped at +2
            "equipped_armor": {"ac_base": 14, "type": "medium"},
            "equipped_shield": False,
        }
        assert recompute_ac(profile) == 16  # 14 + 2 (not 14+4)

    def test_heavy_armor_ignores_dex(self):
        profile = {
            "ability_scores": {"DEX": 18},
            "equipped_armor": {"ac_base": 16, "type": "heavy"},
            "equipped_shield": False,
        }
        assert recompute_ac(profile) == 16  # pure base, no DEX

    def test_shield_adds_two(self):
        profile = {
            "ability_scores": {"DEX": 10},
            "equipped_armor": None,
            "equipped_shield": True,
        }
        assert recompute_ac(profile) == 12  # 10 + 0 + 2

    def test_negative_dex_mod_applied(self):
        profile = {
            "ability_scores": {"DEX": 6},  # mod -2
            "equipped_armor": None,
            "equipped_shield": False,
        }
        assert recompute_ac(profile) == 8  # 10 - 2

    def test_mutates_profile_ac_key(self):
        profile = {
            "ability_scores": {"DEX": 14},
            "equipped_armor": None,
            "equipped_shield": False,
            "ac": 0,  # stale
        }
        recompute_ac(profile)
        assert profile["ac"] == 12
