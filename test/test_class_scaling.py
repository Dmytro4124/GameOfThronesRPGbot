"""Pack B — Extra Attack + Fighting Style tests.

B1: get_attacks_per_action — unit tests for the helper that determines
    how many attacks a character makes per action.
B1b: execute_combat_round attack-loop — integration smoke tests (stubbed
    CombatState + monkeypatched player_attack).
B2a: fighting_style field set at character creation (_build_deterministic_dnd_profile).
B2b: Defense style +1 AC (recompute_ac).
B2c: Dueling style +2 damage (player_attack).
B2d: Great Weapon Fighting reroll 1/2 dice (_roll_damage).

All tests are pure-unit; no DB or LLM calls.
"""

import copy
import pytest
from unittest.mock import patch, MagicMock

from core.dnd_classes import get_attacks_per_action
from core.dnd_combat import (
    CombatState,
    CombatantRef,
    player_attack,
    _roll_damage,
)
from core.dnd_engine import recompute_ac


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_combat_damage_pipeline.py conventions)
# ---------------------------------------------------------------------------

def _make_state(player: dict, npc: dict | None = None) -> CombatState:
    """Build a minimal CombatState for player_attack tests."""
    _npc = npc or {
        "Name": "TargetNPC",
        "hp_current": 100,
        "hp_max": 100,
        "ac": 5,  # low so hits are almost guaranteed with any positive roll
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
    return CombatState(
        chat_id=99,
        round=1,
        initiative_order=combatants,
        current_actor_idx=0,
        npcs={npc_name: copy.deepcopy(_npc)},
        player_snapshot=player_snap,
    )


def _base_player(
    name="Hero",
    level=1,
    str_score=10,
    dex_score=10,
    equipped_weapon=None,
    fighting_style=None,
    equipped_shield=False,
):
    """Build a minimal player profile for combat tests."""
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
        "fighting_style": fighting_style,
    }
    if equipped_weapon is not None:
        p["equipped_weapon"] = equipped_weapon
    return p


# ===========================================================================
# B1 — get_attacks_per_action
# ===========================================================================

class TestB1GetAttacksPerAction:
    """Unit tests for core.dnd_classes.get_attacks_per_action."""

    # --- Knight ---
    def test_knight_l4_returns_1(self):
        """Knight L4 has not yet gained Extra Attack (arrives at L5)."""
        assert get_attacks_per_action("Knight", 4) == 1

    def test_knight_l5_returns_2(self):
        """Knight L5 gains Extra Attack → 2 attacks."""
        assert get_attacks_per_action("Knight", 5) == 2

    def test_knight_l10_returns_2(self):
        """Knight L10: still only one Extra Attack feature → 2 attacks total."""
        assert get_attacks_per_action("Knight", 10) == 2

    def test_knight_l20_returns_2(self):
        """Knight L20: no Extra Attack (2) in class data → still 2 attacks."""
        assert get_attacks_per_action("Knight", 20) == 2

    # --- Classes that share Extra Attack at L5 ---
    @pytest.mark.parametrize("cls_name", [
        "Hedge Knight",
        "Sellsword",
        "Bastard",
        "Wildling",
    ])
    def test_martial_classes_l5_returns_2(self, cls_name):
        """All martial classes with Extra Attack at L5 should return 2 at L5."""
        assert get_attacks_per_action(cls_name, 5) == 2, (
            f"{cls_name} L5 should have 2 attacks (Extra Attack at L5)"
        )

    # --- Sellsword Extra Attack (2) at L11 ---
    def test_sellsword_l10_returns_2(self):
        """Sellsword L10: has Extra Attack (L5) but not yet Extra Attack (2) → 2 attacks."""
        assert get_attacks_per_action("Sellsword", 10) == 2

    def test_sellsword_l11_returns_3(self):
        """Sellsword L11 gains Extra Attack (2) → 3 attacks total."""
        assert get_attacks_per_action("Sellsword", 11) == 3

    def test_sellsword_l20_returns_3(self):
        """Sellsword L20: maximum attacks remains 3."""
        assert get_attacks_per_action("Sellsword", 20) == 3

    # --- Non-martial classes (no Extra Attack) ---
    @pytest.mark.parametrize("cls_name", [
        "Maester",
        "Septon",
        "Spy",
        "Courtier",
    ])
    def test_non_martial_l20_returns_1(self, cls_name):
        """Non-martial classes have no Extra Attack feature → always 1 attack."""
        assert get_attacks_per_action(cls_name, 20) == 1, (
            f"{cls_name} L20 should have 1 attack (no Extra Attack feature)"
        )

    # --- Edge cases ---
    def test_level_zero_returns_1(self):
        """Level 0 is below-floor input — must return safe default 1."""
        assert get_attacks_per_action("Knight", 0) == 1

    def test_level_negative_returns_1(self):
        """Negative level is invalid — must return safe default 1."""
        assert get_attacks_per_action("Knight", -1) == 1

    def test_unknown_class_returns_1(self):
        """Unknown class string — must return safe default 1 without raising."""
        assert get_attacks_per_action("Targaryen Dragon Rider", 10) == 1

    def test_level_cap_at_20(self):
        """Level > 20 should not enumerate out-of-range levels; result == level 20."""
        # Sellsword L11+ = 3; passing level=25 must return same as level=20.
        assert get_attacks_per_action("Sellsword", 25) == get_attacks_per_action("Sellsword", 20)

    # --- Wildling check (class key is "Wildling", not "Wildling Raider") ---
    def test_wildling_class_key_is_wildling(self):
        """Verifies the correct GOT_CLASSES key for the Wildling class."""
        # This will also fail if the class was accidentally named "Wildling Raider"
        result = get_attacks_per_action("Wildling", 5)
        assert result == 2, (
            f"Wildling L5 should return 2 attacks (Extra Attack), got {result}. "
            "If this is 1, the class may be stored under a different key in GOT_CLASSES."
        )

    def test_bastard_l5_extra_attack_present(self):
        """Bastard has Extra Attack at L5 per class definition — verify."""
        result = get_attacks_per_action("Bastard", 5)
        assert result == 2, (
            f"Bastard L5 should have Extra Attack → 2 attacks, got {result}"
        )


# ===========================================================================
# B1b — Extra Attack loop in execute_combat_round (integration smoke)
# ===========================================================================

class TestB1bExtraAttackLoop:
    """Integration smoke tests for the attack-loop in execute_combat_round.

    Strategy: patch player_attack in dnd_combat_engine module so no real
    dice are rolled, then drive the async execute_combat_round and count calls.
    The intent is fixed to 'attack' by patching parse_player_combat_intent.
    NPC actions (execute_npc_actions) are also stubbed to isolate the player loop.

    Uses asyncio.run() directly (no pytest-asyncio needed).
    """

    def _make_minimal_state(self):
        """CombatState with Knight L5 player vs one NPC with 100 HP."""
        npc = {
            "Name": "Боєць",
            "hp_current": 100,
            "hp_max": 100,
            "ac": 5,
            "Status": "Active",
            "conditions": [],
            "ability_scores": {"STR": 10, "DEX": 10},
            "features": [],
            "heritage": "",
        }
        player = {
            "Ім'я": "Лицар",
            "class": "Knight",
            "level": 5,
            "hp_current": 40,
            "hp_max": 40,
            "ac": 16,
            "ability_scores": {
                "STR": 14, "DEX": 10, "CON": 12,
                "INT": 10, "WIS": 10, "CHA": 12,
            },
            "saves_proficient": ["STR", "CON"],
            "conditions": [],
            "features": [],
            "heritage": "Westerosi (Andal)",
            "equipped_shield": True,
            "fighting_style": "Defense",
            "equipped_weapon": {
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_dice_versatile": "1d10",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
        }
        combatants = [
            CombatantRef(kind="player", name="Лицар",  init_roll=15),
            CombatantRef(kind="npc",    name="Боєць", init_roll=8),
        ]
        state = CombatState(
            chat_id=42,
            round=1,
            initiative_order=combatants,
            current_actor_idx=0,
            npcs={"Боєць": copy.deepcopy(npc)},
            player_snapshot=copy.deepcopy(player),
        )
        return state

    def test_knight_l5_two_attacks_called(self):
        """Knight L5 → player_attack must be called exactly 2 times (Extra Attack)."""
        import asyncio
        from unittest.mock import AsyncMock, patch as _patch
        import core.dnd_combat_engine as _eng
        from core.combat_state import set_combat_state, clear_combat_state

        minimal_combat_state = self._make_minimal_state()
        call_count = 0

        # Stub AttackResult (hit, target alive)
        stub_hit = MagicMock()
        stub_hit.log_line = "test hit"
        stub_hit.hit = True

        def _mock_attack(state, target_name, weapon_name, advantage=False, disadvantage=False):
            nonlocal call_count
            call_count += 1
            return stub_hit

        mock_intent = {
            "intent": "attack",
            "target_npc": "Боєць",
            "weapon": "Longsword",
            "tactic": "normal",
        }

        set_combat_state(42, minimal_combat_state)
        try:
            with _patch.object(_eng, "parse_player_combat_intent", new=AsyncMock(return_value=mock_intent)), \
                 _patch.object(_eng, "player_attack", side_effect=_mock_attack), \
                 _patch.object(_eng, "execute_npc_actions", new=AsyncMock(return_value=[])):
                asyncio.run(_eng.execute_combat_round(
                    chat_id=42,
                    user_input="Атакую ворога",
                    profile=copy.deepcopy(minimal_combat_state.player_snapshot),
                ))
        finally:
            clear_combat_state(42)

        assert call_count == 2, (
            f"Knight L5 should make exactly 2 attacks (Extra Attack), "
            f"but player_attack was called {call_count} time(s)."
        )

    def test_attack_stops_when_target_drops_after_first_hit(self):
        """If target drops to 0 HP on the first attack, the loop must stop (no second attack)."""
        import asyncio
        from unittest.mock import AsyncMock, patch as _patch
        import core.dnd_combat_engine as _eng
        from core.combat_state import set_combat_state, clear_combat_state

        minimal_combat_state = self._make_minimal_state()
        call_count = 0

        def _mock_attack_kills(state, target_name, weapon_name, advantage=False, disadvantage=False):
            nonlocal call_count
            call_count += 1
            # Kill the NPC on first hit
            npc_data = state.npcs.get(target_name)
            if npc_data:
                npc_data["hp_current"] = 0
                npc_data["Status"] = "Dead"
            result = MagicMock()
            result.log_line = "перший удар вбиває"
            result.hit = True
            return result

        mock_intent = {
            "intent": "attack",
            "target_npc": "Боєць",
            "weapon": "Longsword",
            "tactic": "normal",
        }

        set_combat_state(42, minimal_combat_state)
        try:
            with _patch.object(_eng, "parse_player_combat_intent", new=AsyncMock(return_value=mock_intent)), \
                 _patch.object(_eng, "player_attack", side_effect=_mock_attack_kills), \
                 _patch.object(_eng, "execute_npc_actions", new=AsyncMock(return_value=[])):
                asyncio.run(_eng.execute_combat_round(
                    chat_id=42,
                    user_input="Атакую ворога",
                    profile=copy.deepcopy(minimal_combat_state.player_snapshot),
                ))
        finally:
            clear_combat_state(42)

        assert call_count == 1, (
            f"Once target drops to 0 HP the attack loop must stop. "
            f"Expected 1 attack call, got {call_count}."
        )


# ===========================================================================
# B2a — fighting_style set at character creation
# ===========================================================================

class TestB2aFightingStyleAtCreation:
    """Tests for _build_deterministic_dnd_profile: fighting_style field."""

    @pytest.mark.parametrize("cls_name", [
        "Knight",
        "Hedge Knight",
        "Sellsword",
        "Bastard",
    ])
    def test_fighting_style_classes_get_defense(self, cls_name):
        """Classes with L2 Fighting Style feature should receive 'Defense' by default."""
        from core.world import _build_deterministic_dnd_profile

        profile = _build_deterministic_dnd_profile(
            char_name="TestChar",
            house_name="Stark",
            origin_region="The North",
            suggested_class=cls_name,
        )
        assert profile.get("fighting_style") == "Defense", (
            f"{cls_name}: expected fighting_style='Defense', got {profile.get('fighting_style')!r}"
        )

    @pytest.mark.parametrize("cls_name", [
        "Maester",
        "Septon",
        "Courtier",
        "Wildling",
    ])
    def test_non_fighting_style_classes_get_none(self, cls_name):
        """Classes WITHOUT L2 Fighting Style must have fighting_style=None."""
        from core.world import _build_deterministic_dnd_profile

        profile = _build_deterministic_dnd_profile(
            char_name="TestChar",
            house_name="Stark",
            origin_region="The North",
            suggested_class=cls_name,
        )
        assert profile.get("fighting_style") is None, (
            f"{cls_name}: expected fighting_style=None, got {profile.get('fighting_style')!r}"
        )

    def test_spy_no_fighting_style(self):
        """Spy class has no L2 Fighting Style feature → fighting_style must be None."""
        from core.world import _build_deterministic_dnd_profile

        profile = _build_deterministic_dnd_profile(
            char_name="TestSpy",
            house_name="Lannister",
            origin_region="The Westerlands",
            suggested_class="Spy",
        )
        assert profile.get("fighting_style") is None, (
            f"Spy: expected None, got {profile.get('fighting_style')!r}"
        )


# ===========================================================================
# B2b — Defense Fighting Style +1 AC
# ===========================================================================

class TestB2bDefenseAC:
    """Tests for recompute_ac: Defense fighting style adds +1 AC when wearing armor."""

    def _profile_with_armor(self, ac_base=16, armor_type="heavy",
                             dex_score=10, fighting_style="Defense",
                             equipped_shield=False):
        return {
            "ability_scores": {
                "STR": 10, "DEX": dex_score, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "equipped_armor": {"ac_base": ac_base, "type": armor_type},
            "equipped_shield": equipped_shield,
            "fighting_style": fighting_style,
        }

    def test_defense_adds_plus_one_with_armor(self):
        """Defense + armor → AC must be +1 compared to no fighting style."""
        profile_defense = self._profile_with_armor(
            ac_base=16, armor_type="heavy", dex_score=10, fighting_style="Defense"
        )
        profile_none = self._profile_with_armor(
            ac_base=16, armor_type="heavy", dex_score=10, fighting_style=None
        )

        ac_defense = recompute_ac(profile_defense)
        ac_none = recompute_ac(profile_none)

        assert ac_defense == ac_none + 1, (
            f"Defense style should add +1 AC. Got defense={ac_defense}, none={ac_none}"
        )

    def test_defense_no_bonus_without_armor(self):
        """Defense without armor → no +1 bonus (PHB: requires wearing armor)."""
        profile = {
            "ability_scores": {
                "STR": 10, "DEX": 10, "CON": 10,
                "INT": 10, "WIS": 10, "CHA": 10,
            },
            "equipped_armor": None,
            "equipped_shield": False,
            "fighting_style": "Defense",
        }
        # Unarmored: 10 + DEX_mod(0) = 10 (no +1 since no armor)
        ac = recompute_ac(profile)
        assert ac == 10, (
            f"Defense without armor must NOT add +1. Expected 10, got {ac}"
        )

    def test_non_defense_style_no_bonus(self):
        """Dueling style with armor → no +1 AC (Defense-only bonus)."""
        profile = self._profile_with_armor(
            ac_base=16, armor_type="heavy", dex_score=10, fighting_style="Dueling"
        )
        profile_none = self._profile_with_armor(
            ac_base=16, armor_type="heavy", dex_score=10, fighting_style=None
        )

        ac_dueling = recompute_ac(profile)
        ac_none = recompute_ac(profile_none)

        assert ac_dueling == ac_none, (
            f"Dueling style must NOT add AC bonus. Got dueling={ac_dueling}, none={ac_none}"
        )

    def test_defense_with_medium_armor_and_dex(self):
        """Defense + medium armor + DEX 12 (+1): AC = 14 + min(1,2) + 1 (Defense) = 16."""
        profile = self._profile_with_armor(
            ac_base=14, armor_type="medium", dex_score=12, fighting_style="Defense"
        )
        ac = recompute_ac(profile)
        assert ac == 16, f"Expected 14+1+1=16, got {ac}"

    def test_defense_with_shield(self):
        """Defense + heavy armor + shield: 16 + 2 (shield) + 1 (Defense) = 19."""
        profile = self._profile_with_armor(
            ac_base=16, armor_type="heavy", dex_score=10,
            fighting_style="Defense", equipped_shield=True
        )
        ac = recompute_ac(profile)
        assert ac == 19, f"Expected 16+2+1=19, got {ac}"

    def test_fighting_style_absent_key_no_crash(self):
        """Profile without fighting_style key at all → no crash, no bonus."""
        profile = {
            "ability_scores": {"DEX": 10},
            "equipped_armor": {"ac_base": 16, "type": "heavy"},
            "equipped_shield": False,
            # fighting_style key deliberately absent
        }
        # Should not raise; armor-only AC = 16
        ac = recompute_ac(profile)
        assert ac == 16

    def test_fighting_style_none_no_crash(self):
        """Profile with fighting_style=None (non-FS class) → no crash, no bonus."""
        profile = {
            "ability_scores": {"DEX": 10},
            "equipped_armor": {"ac_base": 16, "type": "heavy"},
            "equipped_shield": False,
            "fighting_style": None,
        }
        ac = recompute_ac(profile)
        assert ac == 16


# ===========================================================================
# B2c — Dueling Fighting Style +2 damage
# ===========================================================================

class TestB2cDueling:
    """Tests for player_attack: Dueling +2 damage when one-handed weapon + no off-hand."""

    def _longsword_versatile(self):
        """Longsword: versatile weapon (has 'versatile' in properties)."""
        return {
            "name": "Longsword",
            "damage_dice": "1d8",
            "damage_dice_versatile": "1d10",
            "damage_type": "slashing",
            "properties": ["versatile"],
        }

    def _greatsword_two_handed(self):
        """Greatsword: two-handed weapon."""
        return {
            "name": "Greatsword",
            "damage_dice": "2d6",
            "damage_type": "slashing",
            "properties": ["two-handed"],
        }

    def _shortsword_one_handed(self):
        """Shortsword: one-handed weapon (no two-handed, no versatile)."""
        return {
            "name": "Shortsword",
            "damage_dice": "1d6",
            "damage_type": "piercing",
            "properties": ["finesse"],  # one-handed, no two-handed
        }

    def test_dueling_no_bonus_for_versatile_no_shield(self):
        """Versatile longsword without shield = two-handed grip → Dueling does NOT apply.

        Per spec: versatile used two-handed (no shield) DISQUALIFIES Dueling.
        """
        player = _base_player(
            str_score=10,  # mod 0 for clean math
            equipped_weapon=self._longsword_versatile(),
            fighting_style="Dueling",
            equipped_shield=False,
        )
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 5]):
            result_with_style = player_attack(state, "TargetNPC", "Longsword")

        # Compare against same attack without fighting style
        player_no_style = _base_player(
            str_score=10,
            equipped_weapon=self._longsword_versatile(),
            fighting_style=None,
            equipped_shield=False,
        )
        state_no_style = _make_state(player_no_style)
        with patch("random.randint", side_effect=[15, 5]):
            result_no_style = player_attack(state_no_style, "TargetNPC", "Longsword")

        assert result_with_style.damage_total == result_no_style.damage_total, (
            "Versatile weapon without shield (two-handed grip) must NOT get Dueling +2. "
            f"With style: {result_with_style.damage_total}, without: {result_no_style.damage_total}"
        )

    def test_dueling_applies_for_versatile_with_shield(self):
        """Versatile longsword WITH shield = one-handed grip → Dueling +2 applies."""
        player = _base_player(
            str_score=10,  # mod 0
            equipped_weapon=self._longsword_versatile(),
            fighting_style="Dueling",
            equipped_shield=True,   # shield → one-handed grip
        )
        state = _make_state(player)

        # d20=15 → hit, d8=5 → damage = 5 + 0 (STR) + 2 (Dueling) = 7
        with patch("random.randint", side_effect=[15, 5]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        assert result.damage_total == 7, (
            f"Versatile + shield: Dueling +2 must apply. "
            f"Expected 7 (5 die + 2 Dueling), got {result.damage_total}"
        )

    def test_dueling_applies_for_one_handed_no_off_hand(self):
        """One-handed weapon (no two-handed, no versatile) + Dueling → +2 damage."""
        player = _base_player(
            str_score=10,
            # finesse weapon, but that's fine — Dueling checks properties only
            equipped_weapon=self._shortsword_one_handed(),
            fighting_style="Dueling",
            equipped_shield=False,
        )
        state = _make_state(player)

        # d20=15 → hit, d6=4 → damage = 4 + 0 (DEX or STR, both 0 mod) + 2 (Dueling) = 6
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Shortsword")

        assert result.hit is True
        assert result.damage_total == 6, (
            f"One-handed + no off-hand + Dueling: expected 6 (4+0+2), got {result.damage_total}"
        )

    def test_dueling_no_bonus_for_two_handed(self):
        """Two-handed weapon (greatsword) + Dueling → NO +2 bonus."""
        player = _base_player(
            str_score=10,
            equipped_weapon=self._greatsword_two_handed(),
            fighting_style="Dueling",
            equipped_shield=False,
        )
        state_style = _make_state(player)

        player_no_style = _base_player(
            str_score=10,
            equipped_weapon=self._greatsword_two_handed(),
            fighting_style=None,
        )
        state_no_style = _make_state(player_no_style)

        # 2d6: mock d20=15 → hit, d6_1=3, d6_2=2 → raw_dice=5
        with patch("random.randint", side_effect=[15, 3, 2]):
            result_style = player_attack(state_style, "TargetNPC", "Greatsword")
        with patch("random.randint", side_effect=[15, 3, 2]):
            result_no_style = player_attack(state_no_style, "TargetNPC", "Greatsword")

        assert result_style.damage_total == result_no_style.damage_total, (
            "Two-handed weapon must NOT receive Dueling +2 bonus."
        )

    def test_dueling_no_fighting_style_no_bonus(self):
        """Without fighting_style, one-handed weapon gets no +2 (sanity check)."""
        player = _base_player(
            str_score=10,
            equipped_weapon=self._shortsword_one_handed(),
            fighting_style=None,
        )
        state = _make_state(player)

        # d20=15 → hit, d6=4 → damage = 4 + 0 (STR mod for non-finesse, but shortsword is finesse)
        # finesse: max(STR_mod, DEX_mod) = max(0, 0) = 0 → total = 4
        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Shortsword")

        assert result.hit is True
        assert result.damage_total == 4, (
            f"No fighting style: expected 4, got {result.damage_total}"
        )

    def test_dueling_profile_missing_fighting_style_key_no_crash(self):
        """If fighting_style key is entirely absent → no crash, no bonus."""
        player = _base_player(str_score=10, equipped_weapon=self._shortsword_one_handed())
        # Remove the key (not None, fully absent)
        player.pop("fighting_style", None)
        state = _make_state(player)

        with patch("random.randint", side_effect=[15, 4]):
            result = player_attack(state, "TargetNPC", "Shortsword")

        assert result.hit is True
        # No Dueling bonus → damage = 4 + 0 = 4
        assert result.damage_total == 4


# ===========================================================================
# B2d — Great Weapon Fighting reroll 1s and 2s
# ===========================================================================

class TestB2dGreatWeaponFighting:
    """Tests for _roll_damage (reroll_1_2=True) and player_attack (Great Weapon)."""

    # --- Direct unit tests on _roll_damage ---

    def test_roll_of_1_is_rerolled(self):
        """Die rolls 1 → reroll occurs; reroll result (5) is used."""
        # First call: 1 (initial roll, triggers reroll)
        # Second call: 5 (reroll result)
        with patch("random.randint", side_effect=[1, 5]):
            result = _roll_damage(1, 6, 0, reroll_1_2=True)
        # 5 (rerolled) + 0 (modifier) = 5
        assert result == 5

    def test_roll_of_2_is_rerolled(self):
        """Die rolls 2 → reroll occurs; reroll result (4) is used."""
        with patch("random.randint", side_effect=[2, 4]):
            result = _roll_damage(1, 6, 0, reroll_1_2=True)
        assert result == 4

    def test_roll_of_3_is_not_rerolled(self):
        """Die rolls 3 → no reroll; exactly one call to randint."""
        with patch("random.randint", side_effect=[3]) as mock_rand:
            result = _roll_damage(1, 6, 0, reroll_1_2=True)
        # Only 1 call should have been made (no reroll for rolls >= 3)
        assert mock_rand.call_count == 1
        assert result == 3

    def test_reroll_happens_only_once_sequence_1_then_2(self):
        """Sequence [1, 2]: first roll=1 (reroll), reroll=2 → final=2 (not rerolled again).

        PHB: reroll once and take the new value even if lower.
        """
        with patch("random.randint", side_effect=[1, 2]) as mock_rand:
            result = _roll_damage(1, 6, 0, reroll_1_2=True)
        # Only 2 calls: initial roll(1) + reroll(2). Third call would indicate wrong behavior.
        assert mock_rand.call_count == 2, (
            f"Expected exactly 2 randint calls (initial + one reroll), got {mock_rand.call_count}"
        )
        assert result == 2, f"Expected final value 2 (rerolled from 1, got 2), got {result}"

    def test_reroll_1_2_false_no_reroll_on_low_roll(self):
        """Without reroll_1_2, a roll of 1 is kept as-is."""
        with patch("random.randint", side_effect=[1]) as mock_rand:
            result = _roll_damage(1, 6, 0, reroll_1_2=False)
        assert mock_rand.call_count == 1
        assert result == 1  # no floor boost from reroll; min(1, total) from max(1, ...) = 1

    def test_two_dice_both_rerolled_independently(self):
        """2d6: first die=1 (reroll→5), second die=2 (reroll→4) → total=9."""
        with patch("random.randint", side_effect=[1, 5, 2, 4]):
            result = _roll_damage(2, 6, 0, reroll_1_2=True)
        assert result == 9  # 5 + 4

    def test_modifier_still_applied_after_reroll(self):
        """Reroll happens, then flat modifier (+3) is applied to the total."""
        with patch("random.randint", side_effect=[2, 6]):
            result = _roll_damage(1, 8, 3, reroll_1_2=True)
        # rerolled to 6, modifier +3 → total = 9
        assert result == 9

    # --- player_attack with Great Weapon style via properties ---

    def test_great_weapon_two_handed_activates_reroll(self):
        """Two-handed weapon + Great Weapon style: reroll_1_2 passed to _roll_damage.

        Mock roll=1 then reroll=6; damage should be 6+STR_mod, not 1+STR_mod.
        """
        player = _base_player(
            str_score=10,  # mod 0 for clean math
            equipped_weapon={
                "name": "Greatsword",
                "damage_dice": "1d8",
                "damage_type": "slashing",
                "properties": ["two-handed"],
            },
            fighting_style="Great Weapon",
            equipped_shield=False,
        )
        state = _make_state(player)

        # d20=15 → hit (15+0+2=17 >= AC 5)
        # d8=1 → reroll triggered → reroll=6 → damage = 6 + 0 (STR) = 6
        with patch("random.randint", side_effect=[15, 1, 6]):
            result = player_attack(state, "TargetNPC", "Greatsword")

        assert result.hit is True
        assert result.damage_total == 6, (
            f"Great Weapon should reroll 1 → 6. Expected 6, got {result.damage_total}"
        )

    def test_great_weapon_versatile_no_shield_activates_reroll(self):
        """Versatile without shield = two-handed grip → Great Weapon activates."""
        player = _base_player(
            str_score=10,
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_dice_versatile": "1d10",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
            fighting_style="Great Weapon",
            equipped_shield=False,
        )
        state = _make_state(player)

        # d20=15 → hit; versatile no shield → uses d10
        # d10=2 → reroll triggered → reroll=8 → damage = 8
        with patch("random.randint", side_effect=[15, 2, 8]):
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        assert result.damage_total == 8, (
            f"Great Weapon + versatile-no-shield: reroll 2 → 8. Expected 8, got {result.damage_total}"
        )

    def test_great_weapon_versatile_with_shield_no_reroll(self):
        """Versatile WITH shield = one-handed grip → Great Weapon does NOT activate."""
        player = _base_player(
            str_score=10,
            equipped_weapon={
                "name": "Longsword",
                "damage_dice": "1d8",
                "damage_dice_versatile": "1d10",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
            fighting_style="Great Weapon",
            equipped_shield=True,  # one-handed grip → GWF inactive
        )
        state = _make_state(player)

        # d20=15 → hit; one-handed grip → d8 die; roll=2 (should NOT be rerolled)
        with patch("random.randint", side_effect=[15, 2]) as mock_rand:
            result = player_attack(state, "TargetNPC", "Longsword")

        assert result.hit is True
        # Only 2 calls: d20 + d8 (no reroll)
        assert mock_rand.call_count == 2, (
            f"Versatile+shield should NOT reroll: expected 2 randint calls, got {mock_rand.call_count}"
        )
        # damage = 2 + 0 (STR_mod) = 2
        assert result.damage_total == 2

    def test_great_weapon_one_handed_no_reroll(self):
        """One-handed weapon (no two-handed, no versatile) + Great Weapon → NO reroll."""
        player = _base_player(
            str_score=10,
            equipped_weapon={
                "name": "Shortsword",
                "damage_dice": "1d6",
                "damage_type": "piercing",
                "properties": [],  # no two-handed, no versatile
            },
            fighting_style="Great Weapon",
            equipped_shield=False,
        )
        state = _make_state(player)

        # d20=15 → hit; d6=1 — should NOT be rerolled (weapon is not two-handed)
        with patch("random.randint", side_effect=[15, 1]) as mock_rand:
            result = player_attack(state, "TargetNPC", "Shortsword")

        assert result.hit is True
        assert mock_rand.call_count == 2, (
            f"One-handed weapon should NOT trigger GWF reroll: expected 2 calls, got {mock_rand.call_count}"
        )
        # min(1, 1+0+0)=1, so damage floor kicks in
        assert result.damage_total == 1
