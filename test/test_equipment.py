"""
test_equipment.py

Unit tests for P3 (AC recompute) and P5 (structured weapon stats) infrastructure.

Coverage:
  - recompute_ac: no-armor, light armor, medium armor, heavy armor, negative DEX,
    shield bonus, ac_base edge cases.
  - apply_dnd_impacts: AC recompute hook triggered on inventory_new / inventory_lost.
  - generate_initial_stats (world.py): equipped_weapon populated for each of 9 classes.
  - dnd_combat.player_attack: finesse detection via structured field + keyword fallback.
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_dex(dex: int, **overrides) -> dict:
    """Minimal profile with given DEX score. No armor by default."""
    base = {
        "ability_scores": {"STR": 10, "DEX": dex, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "ac": 10,
        "equipped_armor": None,
        "equipped_shield": False,
    }
    base.update(overrides)
    return base


def _apply_updates_minimal(**overrides) -> dict:
    """Minimal updates dict for apply_dnd_impacts."""
    base = {
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "condition_apply": [],
        "condition_remove": [],
        "xp_award": 0,
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# recompute_ac — no-armor cases
# ---------------------------------------------------------------------------

class TestRecomputeAcNoArmor:

    def test_no_armor_dex_14_gives_12(self):
        """DEX 14 → mod +2 → 10+2 = 12 (unarmored)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(14)
        result = recompute_ac(profile)
        assert result == 12
        assert profile["ac"] == 12

    def test_no_armor_dex_8_gives_9(self):
        """DEX 8 → mod -1 → 10+(-1) = 9 (unarmored, negative DEX mod allowed)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(8)
        result = recompute_ac(profile)
        assert result == 9
        assert profile["ac"] == 9

    def test_no_armor_dex_10_gives_10(self):
        """DEX 10 → mod 0 → 10+0 = 10."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(10)
        result = recompute_ac(profile)
        assert result == 10

    def test_no_armor_dex_20_gives_15(self):
        """DEX 20 → mod +5 → 10+5 = 15 (max DEX char, no armor)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(20)
        result = recompute_ac(profile)
        assert result == 15

    def test_equipped_armor_none_explicit(self):
        """equipped_armor=None explicitly → falls through to no-armor path."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(12, equipped_armor=None)
        result = recompute_ac(profile)
        assert result == 11  # 10 + 1


# ---------------------------------------------------------------------------
# recompute_ac — light armor
# ---------------------------------------------------------------------------

class TestRecomputeAcLightArmor:

    def test_light_armor_adds_full_dex(self):
        """Light armor ac_base=11, DEX mod=+3 → 11+3=14."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            16,  # DEX 16 → mod +3
            equipped_armor={"ac_base": 11, "type": "light"},
        )
        result = recompute_ac(profile)
        assert result == 14

    def test_light_armor_negative_dex_reduces_ac(self):
        """Light armor ac_base=11, DEX -1 → 11+(-1)=10. Negative DEX allowed for light."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            8,  # DEX 8 → mod -1
            equipped_armor={"ac_base": 11, "type": "light"},
        )
        result = recompute_ac(profile)
        assert result == 10

    def test_unknown_armor_type_falls_to_light(self):
        """Unknown armor type string → treated as light (ac_base + DEX_mod)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            14,  # +2
            equipped_armor={"ac_base": 12, "type": "leather_exotic"},
        )
        result = recompute_ac(profile)
        assert result == 14  # 12 + 2


# ---------------------------------------------------------------------------
# recompute_ac — medium armor
# ---------------------------------------------------------------------------

class TestRecomputeAcMediumArmor:

    def test_medium_armor_caps_dex_at_plus2(self):
        """Medium armor ac_base=14, DEX +4 → 14+2=16 (DEX capped at +2)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            18,  # DEX 18 → mod +4
            equipped_armor={"ac_base": 14, "type": "medium"},
        )
        result = recompute_ac(profile)
        assert result == 16  # 14 + min(4, 2)

    def test_medium_armor_dex_plus1_not_capped(self):
        """Medium armor ac_base=14, DEX +1 → 14+1=15 (below cap, no clamping)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            12,  # DEX 12 → mod +1
            equipped_armor={"ac_base": 14, "type": "medium"},
        )
        result = recompute_ac(profile)
        assert result == 15

    def test_medium_armor_dex_negative_still_applied(self):
        """Medium armor ac_base=14, DEX -1 → 14 + min(-1, 2) = 14-1 = 13."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            8,  # DEX 8 → mod -1
            equipped_armor={"ac_base": 14, "type": "medium"},
        )
        result = recompute_ac(profile)
        assert result == 13


# ---------------------------------------------------------------------------
# recompute_ac — heavy armor
# ---------------------------------------------------------------------------

class TestRecomputeAcHeavyArmor:

    def test_heavy_armor_ignores_dex(self):
        """Heavy armor ac_base=16, DEX +5 → AC=16 (no DEX bonus for heavy)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            20,  # DEX 20 → mod +5
            equipped_armor={"ac_base": 16, "type": "heavy"},
        )
        result = recompute_ac(profile)
        assert result == 16

    def test_heavy_armor_negative_dex_ignored(self):
        """Heavy armor ac_base=16, DEX -2 → AC=16 (heavy ignores negative DEX too)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            6,  # DEX 6 → mod -2
            equipped_armor={"ac_base": 16, "type": "heavy"},
        )
        result = recompute_ac(profile)
        assert result == 16


# ---------------------------------------------------------------------------
# recompute_ac — shield bonus
# ---------------------------------------------------------------------------

class TestRecomputeAcShield:

    def test_shield_adds_2_no_armor(self):
        """Shield with no armor: 10 + DEX_mod + 2."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(10, equipped_shield=True)
        result = recompute_ac(profile)
        assert result == 12  # 10+0+2

    def test_shield_adds_2_with_heavy_armor(self):
        """Heavy armor ac_base=16 + shield → AC=18."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            10,
            equipped_armor={"ac_base": 16, "type": "heavy"},
            equipped_shield=True,
        )
        result = recompute_ac(profile)
        assert result == 18

    def test_no_shield_key_defaults_to_false(self):
        """Profile without equipped_shield key → no shield bonus."""
        from core.dnd_engine import recompute_ac
        profile = {"ability_scores": {"DEX": 14}, "ac": 10}
        result = recompute_ac(profile)
        assert result == 12  # no shield, 10 + 2


# ---------------------------------------------------------------------------
# recompute_ac — edge cases
# ---------------------------------------------------------------------------

class TestRecomputeAcEdgeCases:

    def test_ac_base_zero_uses_safety_floor(self):
        """ac_base=0 → treated as 10 (safety floor)."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(
            10,
            equipped_armor={"ac_base": 0, "type": "heavy"},
        )
        result = recompute_ac(profile)
        assert result == 10  # floor 10, heavy ignores dex

    def test_missing_dex_defaults_to_10(self):
        """Profile missing ability_scores entirely → DEX defaults to 10, mod=0."""
        from core.dnd_engine import recompute_ac
        profile = {"ac": 10}
        result = recompute_ac(profile)
        assert result == 10  # 10 + 0

    def test_mutates_profile_ac(self):
        """recompute_ac must mutate profile['ac'] in-place, not just return."""
        from core.dnd_engine import recompute_ac
        profile = _profile_dex(14, ac=10)  # stale ac=10
        recompute_ac(profile)
        assert profile["ac"] == 12  # updated to 10 + 2


# ---------------------------------------------------------------------------
# apply_dnd_impacts: AC recompute hook
# ---------------------------------------------------------------------------

class TestApplyDndImpactsAcRecompute:

    def _base_profile(self, dex: int = 12) -> dict:
        """Profile with D&D fields and legacy fields needed by apply_system_impacts."""
        return {
            "Ім'я": "TestHero",
            "Дім": "Stark",
            "Здоров'я": 100,
            "Енергія": 1000,
            "Особисте Золото": 100,
            "Поточне місцезнаходження": "Вінтерфелл",
            "Поточна сцена": "Great Hall",
            "Регіон": "Північ",
            "Ігровий час": "298 рік В.Е., 1-й місяць, День 1, 08:00",
            "Час_хвилини": 480,
            "Інвентар": "Longsword",
            "Годинники": {},
            "level": 1,
            "xp": 0,
            "ability_scores": {"STR": 10, "DEX": dex, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "skill_profs": [],
            "skill_expertise": [],
            "saves_proficient": [],
            "conditions": [],
            "hp_current": 10,
            "hp_max": 10,
            "ac": 99,          # intentionally stale to detect recompute
            "equipped_armor": None,
            "equipped_shield": False,
            "temp_debuffs": {},
            "training_cooldowns": {},
        }

    def test_inventory_new_triggers_recompute(self):
        """inventory_new non-empty → recompute_ac called → ac updated from stale 99."""
        profile = self._base_profile(dex=14)
        updates = _apply_updates_minimal(inventory_new=["Кольчуга"])

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        # No equipped_armor set → fallback to 10 + DEX_mod(14) = 12
        assert result_profile["ac"] == 12, (
            f"AC should be recomputed to 12 (no armor, DEX 14). Got {result_profile['ac']}"
        )

    def test_inventory_lost_triggers_recompute(self):
        """inventory_lost non-empty → recompute_ac called."""
        profile = self._base_profile(dex=10)
        profile["Інвентар"] = "Sword, Shield"
        updates = _apply_updates_minimal(inventory_lost=["Shield"])

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        # No equipped_armor → 10 + 0 = 10
        assert result_profile["ac"] == 10, (
            f"AC should be 10 after recompute (no armor, DEX 10). Got {result_profile['ac']}"
        )

    def test_no_inventory_change_skips_recompute(self):
        """When inventory_new and inventory_lost are both empty → profile['ac'] unchanged."""
        profile = self._base_profile(dex=14)
        profile["ac"] = 99  # stale
        updates = _apply_updates_minimal()  # both lists empty

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        # recompute_ac NOT called → ac stays 99
        assert result_profile["ac"] == 99, (
            f"ac must not be recomputed when inventory unchanged. Got {result_profile['ac']}"
        )

    def test_ac_recompute_logged_when_ac_changed(self):
        """When AC changes after recompute, a log entry 'AC: X -> Y' must appear."""
        profile = self._base_profile(dex=14)
        profile["ac"] = 99  # will change to 12
        updates = _apply_updates_minimal(inventory_new=["Кольчуга"])

        from core.dnd_engine import apply_dnd_impacts
        _, logs = apply_dnd_impacts(profile, updates)

        ac_logs = [l for l in logs if "AC:" in l and "99" in l and "12" in l]
        assert ac_logs, (
            f"Logs must contain 'AC: 99 -> 12'. Got logs: {logs}"
        )

    def test_ac_no_log_when_ac_unchanged(self):
        """When AC does not change after recompute, no 'AC:' log entry emitted."""
        profile = self._base_profile(dex=14)
        profile["ac"] = 12  # already correct for DEX 14, no armor
        updates = _apply_updates_minimal(inventory_new=["Поклажа"])

        from core.dnd_engine import apply_dnd_impacts
        _, logs = apply_dnd_impacts(profile, updates)

        ac_logs = [l for l in logs if l.startswith("AC:")]
        assert not ac_logs, (
            f"No 'AC:' log expected when AC unchanged. Got: {ac_logs}"
        )


# ---------------------------------------------------------------------------
# generate_initial_stats: equipped_weapon populated
# ---------------------------------------------------------------------------

class TestGenerateInitialStatsEquippedWeapon:
    """Verify equipped_weapon is present for all 9 GoT classes via build_class_starting_kit."""

    _ALL_CLASSES = [
        "Knight",
        "Hedge Knight",
        "Maester",
        "Septon",
        "Sellsword",
        "Spy",
        "Courtier",
        "Bastard",
        "Wildling",
    ]

    def _build_equipped_weapon(self, class_name: str) -> dict | None:
        """Reproduce the equipped_weapon build logic from world.py for a given class."""
        import re
        from core.dnd_classes import build_class_starting_kit
        kit = build_class_starting_kit(class_name)
        weapon_main_str = kit.get("weapon_main", "") or ""
        if not weapon_main_str:
            return None

        w_name = weapon_main_str.split("(")[0].strip()
        w_props: list[str] = []
        w_dice = "1d4"
        w_dmg_type = "bludgeoning"

        inner_match = re.search(r'\(([^)]+)\)', weapon_main_str)
        if inner_match:
            inner = inner_match.group(1)
            parts = [p.strip() for p in inner.split(",")]
            for p in parts:
                p_lower = p.lower()
                if re.match(r'\d+d\d+', p_lower):
                    dice_match = re.match(r'(\d+d\d+)\s*(.*)', p_lower)
                    if dice_match:
                        w_dice = dice_match.group(1)
                        dmg_raw = dice_match.group(2).strip()
                        if dmg_raw:
                            w_dmg_type = dmg_raw
                elif any(kw in p_lower for kw in ("фінесс", "finesse")):
                    w_props.append("finesse")
                elif any(kw in p_lower for kw in ("дворуч", "two-handed")):
                    w_props.append("two-handed")
                elif any(kw in p_lower for kw in ("легка", "light")):
                    w_props.append("light")
                elif any(kw in p_lower for kw in ("метн", "thrown")):
                    w_props.append("thrown")
                elif any(kw in p_lower for kw in ("80/", "20/", "range")):
                    w_props.append("ranged")
        return {"name": w_name, "damage_dice": w_dice, "damage_type": w_dmg_type, "properties": w_props}

    @pytest.mark.parametrize("class_name", _ALL_CLASSES)
    def test_equipped_weapon_has_required_keys(self, class_name: str):
        """equipped_weapon for every class must have name, damage_dice, damage_type, properties."""
        weapon = self._build_equipped_weapon(class_name)
        assert weapon is not None, f"{class_name}: equipped_weapon must not be None (has weapon_main)"
        for key in ("name", "damage_dice", "damage_type", "properties"):
            assert key in weapon, (
                f"{class_name}: equipped_weapon missing key '{key}'. Got: {weapon}"
            )
        assert isinstance(weapon["properties"], list), (
            f"{class_name}: equipped_weapon['properties'] must be a list"
        )

    @pytest.mark.parametrize("class_name,expected_finesse", [
        ("Knight", False),       # Довгий меч — no finesse
        ("Hedge Knight", False), # Довгий меч — no finesse
        ("Maester", False),      # Палиця — no finesse
        ("Septon", False),       # Булава — no finesse
        ("Sellsword", True),     # Короткий меч (1d6 колота, фінесс) — has finesse
        ("Spy", True),           # Кинджал (1d4 колота, фінесс, метний 20/60)
        ("Courtier", True),      # Рапіра (1d8 колота, фінесс)
        ("Bastard", False),      # Довгий меч — no finesse
        ("Wildling", False),     # Велика сокира — no finesse
    ])
    def test_finesse_property_correct_per_class(self, class_name: str, expected_finesse: bool):
        """finesse in properties[] matches actual weapon_main string for each class."""
        weapon = self._build_equipped_weapon(class_name)
        assert weapon is not None
        has_finesse = "finesse" in weapon.get("properties", [])
        assert has_finesse == expected_finesse, (
            f"{class_name}: expected finesse={expected_finesse}, got {weapon['properties']}"
        )


# ---------------------------------------------------------------------------
# dnd_combat.player_attack: finesse detection
# ---------------------------------------------------------------------------

class TestFinesseDetectionInCombat:
    """Verify that player_attack uses equipped_weapon["properties"] when available."""

    def _make_combat_state(self, player_profile: dict, npc: dict):
        """Build a minimal CombatState for player_attack tests."""
        import copy
        from core.dnd_combat import CombatState, CombatantRef
        npc_copy = copy.deepcopy(npc)
        npc_name = npc_copy.get("Name", "Enemy")
        npc_copy.setdefault("hp_current", 10)
        npc_copy.setdefault("hp_max", 10)
        npc_copy.setdefault("ac", 5)   # low AC so attacks hit
        npc_copy.setdefault("Status", "Active")
        npc_copy.setdefault("conditions", [])
        return CombatState(
            chat_id=1,
            round=1,
            initiative_order=[
                CombatantRef(kind="player", name=player_profile.get("Ім'я", "Player"), init_roll=15),
                CombatantRef(kind="npc", name=npc_name, init_roll=10),
            ],
            current_actor_idx=0,
            npcs={npc_name: npc_copy},
            player_snapshot=copy.deepcopy(player_profile),
        )

    def test_finesse_from_equipped_weapon_uses_dex_when_higher(self):
        """Player with DEX > STR + finesse weapon → DEX mod used for attack."""
        from unittest.mock import patch
        player = {
            "Ім'я": "TestPlayer",
            "level": 1,
            "ability_scores": {"STR": 8, "DEX": 18, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "conditions": [],
            "equipped_weapon": {
                "name": "Кинджал",
                "damage_dice": "1d4",
                "damage_type": "колота",
                "properties": ["finesse", "thrown"],
            },
            "equipment": {"weapon_main": "Кинджал (1d4 колота, фінесс)"},
            "hp_current": 20,
            "hp_max": 20,
            "ac": 13,
            "features": [],
        }
        npc = {"Name": "Dummy", "hp_current": 30, "hp_max": 30, "ac": 5, "Status": "Active", "conditions": [], "attacks": []}
        state = self._make_combat_state(player, npc)

        # DEX mod = +4 (DEX 18), STR mod = -1 (STR 8)
        # With finesse → attack_mod = max(-1, +4) = +4
        # Patch roll to nat 10 so we can check to_hit_total
        with patch("core.dnd_combat.roll_d20", return_value=(10, [10])):
            from core.dnd_combat import player_attack
            result = player_attack(state, "Dummy", "Кинджал")

        # +4 DEX mod + 2 prof = 16 total; nat 10 + 4 + 2 = 16
        prof_bonus = 2  # level 1
        expected_total = 10 + 4 + prof_bonus  # nat + dex_mod + prof
        assert result.to_hit_total == expected_total, (
            f"Finesse with DEX 18 (mod +4): to_hit_total expected {expected_total}, "
            f"got {result.to_hit_total}"
        )

    def test_non_finesse_weapon_uses_str(self):
        """Non-finesse weapon → STR mod used even when DEX is higher."""
        from unittest.mock import patch
        player = {
            "Ім'я": "TestPlayer",
            "level": 1,
            "ability_scores": {"STR": 8, "DEX": 18, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "conditions": [],
            "equipped_weapon": {
                "name": "Довгий меч",
                "damage_dice": "1d8",
                "damage_type": "рублюча",
                "properties": [],  # no finesse
            },
            "equipment": {"weapon_main": "Довгий меч (1d8 рублюча)"},
            "hp_current": 20,
            "hp_max": 20,
            "ac": 13,
            "features": [],
        }
        npc = {"Name": "Dummy", "hp_current": 30, "hp_max": 30, "ac": 5, "Status": "Active", "conditions": [], "attacks": []}
        state = self._make_combat_state(player, npc)

        # STR mod = -1 (STR 8); non-finesse → attack_mod = -1
        with patch("core.dnd_combat.roll_d20", return_value=(10, [10])):
            from core.dnd_combat import player_attack
            result = player_attack(state, "Dummy", "Довгий меч")

        prof_bonus = 2  # level 1
        expected_total = 10 + (-1) + prof_bonus  # nat + str_mod + prof = 11
        assert result.to_hit_total == expected_total, (
            f"Non-finesse longsword with STR 8 (mod -1): to_hit_total expected {expected_total}, "
            f"got {result.to_hit_total}"
        )

    def test_keyword_fallback_when_no_equipped_weapon(self):
        """Profile without equipped_weapon key → keyword detection used for finesse."""
        from unittest.mock import patch
        player = {
            "Ім'я": "TestPlayer",
            "level": 1,
            "ability_scores": {"STR": 8, "DEX": 18, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
            "conditions": [],
            # No equipped_weapon key at all
            "equipment": {"weapon_main": "rapier (1d8 piercing, finesse)"},
            "hp_current": 20,
            "hp_max": 20,
            "ac": 13,
            "features": [],
        }
        npc = {"Name": "Dummy", "hp_current": 30, "hp_max": 30, "ac": 5, "Status": "Active", "conditions": [], "attacks": []}
        state = self._make_combat_state(player, npc)

        with patch("core.dnd_combat.roll_d20", return_value=(10, [10])):
            from core.dnd_combat import player_attack
            result = player_attack(state, "Dummy", "rapier")

        # "rapier" keyword → finesse → DEX mod +4 used
        prof_bonus = 2
        expected_total = 10 + 4 + prof_bonus  # nat + dex_mod + prof
        assert result.to_hit_total == expected_total, (
            f"Keyword fallback 'rapier': to_hit_total expected {expected_total}, "
            f"got {result.to_hit_total}"
        )
