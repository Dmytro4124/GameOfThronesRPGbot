"""test_combat_package2.py

Unit / integration tests for Package 2 combat-engine changes:

  1. Canon NPC statblock lookup  (database.operations.get_canon_npc_statblock)
  2. Tier-based fallback          (_guess_npc_tier + _NPC_TIER_DEFAULTS)
  3. initiate_combat snapshot     (tier defaults + canon statblock preservation)
  4. npc_attack with empty attacks list (tier fallback weapon, not old Unarmed +0/1d4)
  5. Engine-level dict-merge      (canon statblock merged into combat entry)

Mocking strategy:
  - database.sheets is stubbed in sys.modules BEFORE database.operations is
    imported, to prevent the GoogleSheetsDB singleton from trying to read
    SPREADSHEET_ID from env (which is absent in the test environment).
  - database.canon_npc and core.dnd_core / core.dnd_combat are imported
    directly; they have no gspread/Gemini side-effects on import.
  - The module-level _CANON_STATBLOCK_INDEX cache in database.operations is
    reset around each test that exercises the lookup (isolation).
  - random / dice patched deterministically where roll values matter.
  - No real Google Sheets tables are touched.

Each test covers exactly one assertion boundary.
"""

import copy
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub database.sheets before database.operations is imported.
# Without this, the GoogleSheetsDB singleton raises ValueError at collection
# time because SPREADSHEET_ID is not set in the CI/test environment.
# ---------------------------------------------------------------------------

if "database.sheets" not in sys.modules:
    _fake_sheets = types.ModuleType("database.sheets")
    _fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = _fake_sheets

# Also stub the genai.Client call inside database.operations so it doesn't
# need a real GEMINI_API_KEY at import time.
if "config" not in sys.modules:
    _fake_config = types.ModuleType("config")
    _fake_config.TAB_USERS = "Users_DB"
    _fake_config.TAB_HOUSES = "Houses_DB"
    _fake_config.TAB_CHARACTER = "Character"
    _fake_config.TAB_KNOWLEDGE = "KnowledgeBase"
    _fake_config.GEMINI_API_KEY = "stub-key-for-tests"
    sys.modules["config"] = _fake_config

# Stub google.genai so genai.Client() at module level doesn't need a real key.
if "google.genai" not in sys.modules:
    import google  # ensure the google namespace exists
    _fake_genai = types.ModuleType("google.genai")
    _fake_genai.Client = MagicMock(return_value=MagicMock())
    sys.modules["google.genai"] = _fake_genai
    # Also ensure google.genai is accessible as attribute on google
    google.genai = _fake_genai  # type: ignore[attr-defined]

# Now safe to import database.operations
import database.operations as _ops_module


# ---------------------------------------------------------------------------
# Autouse fixture: reset the lazy statblock index around every test.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_canon_index():
    """Wipe the lazy statblock index so every test builds it fresh."""
    _ops_module._CANON_STATBLOCK_INDEX = None
    yield
    _ops_module._CANON_STATBLOCK_INDEX = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _player(name="TestPlayer", level=3, hp=40, ac=16):
    return {
        "Ім'я": name,
        "level": level,
        "hp_current": hp,
        "hp_max": hp,
        "ac": ac,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "conditions": [],
        "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        "features": [],
        "heritage": "Westerosi (Andal)",
    }


# ===========================================================================
# SECTION 1 — get_canon_npc_statblock: lookup, deep-copy, case sensitivity,
#              stripped narrative fields
# ===========================================================================

class TestGetCanonNpcStatblock:
    """Tests for database.operations.get_canon_npc_statblock()."""

    def test_khal_drogo_ac_is_18(self):
        """Кхал Дрого must be found with ac=18."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None, "get_canon_npc_statblock('Кхал Дрого') must not return None"
        assert result["ac"] == 18, f"Expected ac=18, got {result['ac']}"

    def test_khal_drogo_hp_max_is_150(self):
        """Кхал Дрого must have hp_max=150."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert result["hp_max"] == 150, f"Expected hp_max=150, got {result['hp_max']}"

    def test_khal_drogo_attacks_to_hit_is_7(self):
        """Кхал Дрого attacks[0] must have to_hit=7."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        attacks = result.get("attacks", [])
        assert len(attacks) >= 1, "Attacks list must be non-empty"
        assert attacks[0]["to_hit"] == 7, (
            f"Expected to_hit=7 on attacks[0], got {attacks[0]['to_hit']}"
        )

    def test_khal_drogo_attacks_dmg_contains_2d6_plus_5(self):
        """Кхал Дрого attacks[0] dmg must contain '2d6+5'."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        attacks = result.get("attacks", [])
        assert len(attacks) >= 1
        assert "2d6+5" in attacks[0]["dmg"], (
            f"Expected '2d6+5' in dmg, got {attacks[0]['dmg']!r}"
        )

    def test_deep_copy_mutation_does_not_corrupt_cache(self):
        """Mutating the returned dict must NOT affect a subsequent call's values."""
        from database.operations import get_canon_npc_statblock
        first = get_canon_npc_statblock("Кхал Дрого")
        assert first is not None
        original_ac = first["ac"]  # 18

        first["ac"] = 999
        first["hp_max"] = 1

        second = get_canon_npc_statblock("Кхал Дрого")
        assert second is not None
        assert second["ac"] == original_ac, (
            f"Cache was corrupted: second call returned ac={second['ac']}, "
            f"expected {original_ac}"
        )
        assert second["hp_max"] == 150, (
            f"Cache was corrupted: second call returned hp_max={second['hp_max']}, expected 150"
        )

    def test_deep_copy_returned_dicts_are_distinct_objects(self):
        """Two successive calls must return different dict objects (deep copies)."""
        from database.operations import get_canon_npc_statblock
        first = get_canon_npc_statblock("Кхал Дрого")
        second = get_canon_npc_statblock("Кхал Дрого")
        assert first is not second, (
            "Each call must return a NEW dict object, not the cached reference"
        )

    def test_unknown_name_returns_none(self):
        """An NPC name not in canon registry must return None."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Неіснуючий NPC X9999")
        assert result is None, f"Expected None for unknown name, got: {result}"

    def test_wrong_case_returns_none(self):
        """Case-sensitive matching: lowercase 'кхал дрого' must return None."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("кхал дрого")
        assert result is None, (
            f"Case-insensitive match must NOT succeed. Expected None, got: {result}"
        )

    def test_wrong_case_mixed_returns_none(self):
        """Mixed-case 'Кхал ДРОГО' must also return None."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал ДРОГО")
        assert result is None

    def test_description_field_stripped(self):
        """Returned dict must NOT contain 'Description' (narrative field)."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert "Description" not in result, (
            f"'Description' is narrative and must be stripped. Keys: {list(result.keys())}"
        )

    def test_goal_field_stripped(self):
        """Returned dict must NOT contain 'Goal'."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert "Goal" not in result, f"'Goal' must be stripped. Keys: {list(result.keys())}"

    def test_secrets_field_stripped(self):
        """Returned dict must NOT contain 'Secrets'."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert "Secrets" not in result

    def test_memory_anchor_field_stripped(self):
        """Returned dict must NOT contain 'Memory_Anchor'."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert "Memory_Anchor" not in result

    def test_relation_player_field_stripped(self):
        """Returned dict must NOT contain 'Relation_Player'."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert "Relation_Player" not in result

    def test_statblock_required_fields_present(self):
        """Statblock must contain the documented required fields."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        for field in ("Name", "ability_scores", "hp_max", "hp_current", "ac", "attacks"):
            assert field in result, (
                f"Required field '{field}' missing. Keys: {list(result.keys())}"
            )

    def test_name_field_preserved(self):
        """The 'Name' field must be present and equal to the lookup key."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Кхал Дрого")
        assert result is not None
        assert result["Name"] == "Кхал Дрого"


# ===========================================================================
# SECTION 2 — _guess_npc_tier + _NPC_TIER_DEFAULTS sanity
# ===========================================================================

class TestGuestNpcTierAndDefaults:
    """Tests for core.dnd_combat._guess_npc_tier and _NPC_TIER_DEFAULTS."""

    # --- Tag-based detection ---

    @pytest.mark.parametrize("tags,expected_tier", [
        (["veteran"],   "veteran"),
        (["knight"],    "veteran"),
        (["sellsword"], "sellsword"),
        (["mercenary"], "sellsword"),
        (["guard"],     "guard"),
        (["watch"],     "guard"),
        (["commoner"],  "commoner"),
    ])
    def test_tier_from_tag(self, tags, expected_tier):
        """Tag-based tier resolution covers veteran/sellsword/guard/commoner."""
        from core.dnd_combat import _guess_npc_tier
        npc = {"Name": "NPC", "tags": tags}
        result = _guess_npc_tier(npc)
        assert result == expected_tier, (
            f"tags={tags} expected '{expected_tier}', got '{result}'"
        )

    # --- Name-based detection (Ukrainian substrings) ---

    @pytest.mark.parametrize("name,expected_tier", [
        ("Лицар Тиреля",    "veteran"),
        ("Лицар",           "veteran"),
        ("Найманець",       "sellsword"),
        ("Найманий вбивця", "sellsword"),
        ("Бандит ринку",    "sellsword"),
        ("Варта Брами",     "guard"),
        ("Стражник",        "guard"),
        ("Капітан Варти",   "guard"),
        ("Селянин",         "commoner"),
        ("Слуга корчми",    "commoner"),
        ("Торговець",       "commoner"),
    ])
    def test_tier_from_name(self, name, expected_tier):
        """Name-based tier resolution via Ukrainian substring hints."""
        from core.dnd_combat import _guess_npc_tier
        npc = {"Name": name, "tags": []}
        result = _guess_npc_tier(npc)
        assert result == expected_tier, (
            f"Name={name!r} expected '{expected_tier}', got '{result}'"
        )

    def test_empty_dict_defaults_to_guard(self):
        """An empty NPC dict must default to 'guard'."""
        from core.dnd_combat import _guess_npc_tier
        result = _guess_npc_tier({})
        assert result == "guard", f"Empty dict must default to 'guard', got '{result}'"

    def test_unrecognised_tags_and_name_defaults_to_guard(self):
        """Completely unrecognised tags and name must default to 'guard'."""
        from core.dnd_combat import _guess_npc_tier
        npc = {"Name": "Невизначена сутність", "tags": ["humanoid"]}
        result = _guess_npc_tier(npc)
        assert result == "guard", (
            f"Unrecognised NPC must default to 'guard', got '{result}'"
        )

    # --- _NPC_TIER_DEFAULTS sanity for 'guard' (the critical default tier) ---

    def test_guard_tier_defaults_hp_is_11(self):
        """Guard tier default hp must be 11."""
        from core.dnd_combat import _NPC_TIER_DEFAULTS
        assert _NPC_TIER_DEFAULTS["guard"]["hp"] == 11, (
            f"Guard hp changed — expected 11, got {_NPC_TIER_DEFAULTS['guard']['hp']}"
        )

    def test_guard_tier_defaults_ac_is_16(self):
        """Guard tier default ac must be 16."""
        from core.dnd_combat import _NPC_TIER_DEFAULTS
        assert _NPC_TIER_DEFAULTS["guard"]["ac"] == 16, (
            f"Guard ac changed — expected 16, got {_NPC_TIER_DEFAULTS['guard']['ac']}"
        )

    def test_guard_tier_defaults_to_hit_is_3(self):
        """Guard tier attack to_hit must be +3."""
        from core.dnd_combat import _NPC_TIER_DEFAULTS
        assert _NPC_TIER_DEFAULTS["guard"]["attack"]["to_hit"] == 3, (
            f"Guard to_hit changed — expected 3, "
            f"got {_NPC_TIER_DEFAULTS['guard']['attack']['to_hit']}"
        )

    def test_all_four_tiers_present(self):
        """All four tiers must exist in _NPC_TIER_DEFAULTS."""
        from core.dnd_combat import _NPC_TIER_DEFAULTS
        for tier in ("commoner", "guard", "sellsword", "veteran"):
            assert tier in _NPC_TIER_DEFAULTS, (
                f"Tier '{tier}' missing from _NPC_TIER_DEFAULTS"
            )

    def test_each_tier_has_required_keys(self):
        """Each tier dict must have: hp, ac, to_hit, attack."""
        from core.dnd_combat import _NPC_TIER_DEFAULTS
        for tier, data in _NPC_TIER_DEFAULTS.items():
            for key in ("hp", "ac", "to_hit", "attack"):
                assert key in data, f"Tier '{tier}' missing key '{key}'"

    def test_each_tier_attack_has_required_subkeys(self):
        """Each tier attack dict must have: name, to_hit, dmg."""
        from core.dnd_combat import _NPC_TIER_DEFAULTS
        for tier, data in _NPC_TIER_DEFAULTS.items():
            attack = data["attack"]
            for key in ("name", "to_hit", "dmg"):
                assert key in attack, f"Tier '{tier}' attack missing key '{key}'"


# ===========================================================================
# SECTION 3 — initiate_combat: tier-based snapshot defaults and canon preservation
# ===========================================================================

class TestInitiateCombatTierDefaults:
    """Tests for tier-based statblock injection in initiate_combat."""

    def test_non_canon_guard_name_gets_guard_hp_and_ac(self):
        """NPC 'Стражник Брами' (no statblock fields) → guard tier:
        hp_current==hp_max==11, ac==16 after initiate_combat."""
        from core.dnd_combat import initiate_combat

        player = _player()
        npc = {"Name": "Стражник Брами", "Relation_Player": "Ворожий"}

        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc], chat_id=3001)

        snap = state.npcs.get("Стражник Брами")
        assert snap is not None, "NPC must appear in CombatState.npcs"
        assert snap["hp_max"] == 11, (
            f"Guard tier hp_max must be 11, got {snap['hp_max']}"
        )
        assert snap["hp_current"] == snap["hp_max"], (
            f"hp_current must equal hp_max on init: "
            f"hp_current={snap['hp_current']}, hp_max={snap['hp_max']}"
        )
        assert snap["ac"] == 16, f"Guard tier ac must be 16, got {snap['ac']}"

    def test_hp_current_equals_hp_max_for_new_npc(self):
        """hp_current must equal hp_max upon snapshot creation (any tier)."""
        from core.dnd_combat import initiate_combat

        player = _player()
        npc = {"Name": "Варта", "tags": ["guard"]}

        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc], chat_id=3002)

        snap = state.npcs["Варта"]
        assert snap["hp_current"] == snap["hp_max"], (
            f"hp_current ({snap['hp_current']}) != hp_max ({snap['hp_max']})"
        )

    def test_canon_enriched_npc_hp_max_not_overwritten(self):
        """Canon NPC merged into combat entry (as engine does) must keep hp_max=150.

        This guards against the B2 bug: tier defaults overwriting canon hp_max.
        """
        from core.dnd_combat import initiate_combat
        from database.operations import get_canon_npc_statblock

        canon_sb = get_canon_npc_statblock("Кхал Дрого")
        assert canon_sb is not None

        npc_combat_entry = {"Name": "Кхал Дрого", "Relation_Player": "Ворожа"}
        npc_combat_entry.update(canon_sb)
        npc_combat_entry["Name"] = "Кхал Дрого"

        player = _player()
        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc_combat_entry], chat_id=3003)

        snap = state.npcs.get("Кхал Дрого")
        assert snap is not None, "Кхал Дрого must be in CombatState.npcs"
        assert snap["hp_max"] == 150, (
            f"B2 BUG: hp_max was overwritten by tier defaults. "
            f"Expected 150 (canon), got {snap['hp_max']}. "
            f"initiate_combat must NOT overwrite hp_max when already present."
        )

    def test_canon_enriched_npc_ac_not_overwritten(self):
        """Canon ac=18 for Кхал Дрого must survive initiate_combat."""
        from core.dnd_combat import initiate_combat
        from database.operations import get_canon_npc_statblock

        canon_sb = get_canon_npc_statblock("Кхал Дрого")
        npc_combat_entry = {"Name": "Кхал Дрого", "Relation_Player": "Ворожа"}
        npc_combat_entry.update(canon_sb)
        npc_combat_entry["Name"] = "Кхал Дрого"

        player = _player()
        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc_combat_entry], chat_id=3004)

        snap = state.npcs["Кхал Дрого"]
        assert snap["ac"] == 18, (
            f"B2 BUG: ac was overwritten by tier defaults. "
            f"Expected 18 (canon), got {snap['ac']}."
        )

    def test_canon_enriched_npc_attacks_list_preserved(self):
        """Canon attacks list (to_hit=7, 2d6+5) must survive initiate_combat."""
        from core.dnd_combat import initiate_combat
        from database.operations import get_canon_npc_statblock

        canon_sb = get_canon_npc_statblock("Кхал Дрого")
        npc_combat_entry = {"Name": "Кхал Дрого", "Relation_Player": "Ворожа"}
        npc_combat_entry.update(canon_sb)
        npc_combat_entry["Name"] = "Кхал Дрого"

        player = _player()
        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc_combat_entry], chat_id=3005)

        snap = state.npcs["Кхал Дрого"]
        attacks = snap.get("attacks", [])
        assert len(attacks) >= 1, "Canon attacks list must be non-empty after initiate_combat"
        assert attacks[0]["to_hit"] == 7, (
            f"Canon attack to_hit=7 must be preserved; got {attacks[0]['to_hit']}"
        )
        assert "2d6+5" in attacks[0]["dmg"], (
            f"Canon attack dmg '2d6+5' must be preserved; got {attacks[0]['dmg']!r}"
        )

    def test_veteran_tag_npc_gets_veteran_stats(self):
        """NPC with tag 'veteran' must receive veteran tier hp=58, ac=17."""
        from core.dnd_combat import initiate_combat

        player = _player()
        npc = {"Name": "Лицар", "tags": ["veteran"]}

        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc], chat_id=3006)

        snap = state.npcs["Лицар"]
        assert snap["hp_max"] == 58, f"Veteran hp_max must be 58, got {snap['hp_max']}"
        assert snap["ac"] == 17, f"Veteran ac must be 17, got {snap['ac']}"

    def test_commoner_tag_npc_gets_commoner_stats(self):
        """NPC with tag 'commoner' must receive commoner tier hp=4, ac=10."""
        from core.dnd_combat import initiate_combat

        player = _player()
        npc = {"Name": "Селянин", "tags": ["commoner"]}

        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [npc], chat_id=3007)

        snap = state.npcs["Селянин"]
        assert snap["hp_max"] == 4, f"Commoner hp_max must be 4, got {snap['hp_max']}"
        assert snap["ac"] == 10, f"Commoner ac must be 10, got {snap['ac']}"


# ===========================================================================
# SECTION 4 — npc_attack with empty attacks list uses tier fallback weapon
# ===========================================================================

def _make_state_with_empty_attacks_npc(npc_name: str, tags: list = None):
    """Build a CombatState whose NPC has attacks=[] to force tier fallback."""
    from core.dnd_combat import CombatState, CombatantRef

    player = _player()
    player_snap = copy.deepcopy(player)
    player_snap.setdefault("conditions", [])

    npc_snap = {
        "Name": npc_name,
        "hp_current": 11,
        "hp_max": 11,
        "ac": 16,
        "Status": "Active",
        "conditions": [],
        "attacks": [],
        "tags": tags if tags is not None else ["guard"],
        "ability_scores": {
            "STR": 12, "DEX": 10, "CON": 12,
            "INT": 10, "WIS": 10, "CHA": 8,
        },
    }

    player_ref = CombatantRef(kind="player", name="TestPlayer", init_roll=15)
    npc_ref    = CombatantRef(kind="npc",    name=npc_name,     init_roll=8)

    return CombatState(
        chat_id=4001,
        round=1,
        initiative_order=[npc_ref, player_ref],
        current_actor_idx=0,
        npcs={npc_name: npc_snap},
        player_snapshot=player_snap,
    )


class TestNpcAttackTierFallback:
    """Tests that npc_attack uses tier-appropriate weapon when attacks=[]."""

    def test_empty_attacks_uses_guard_to_hit_not_zero(self):
        """npc_attack with attacks=[] must use guard tier to_hit=+3, not old +0.

        nat=15, guard to_hit=3 → to_hit_total=18.
        If old fallback was used (+0), to_hit_total would be 15 (regression).
        """
        from core.dnd_combat import npc_attack, _NPC_TIER_DEFAULTS

        state = _make_state_with_empty_attacks_npc("Стражник")
        guard_to_hit = _NPC_TIER_DEFAULTS["guard"]["attack"]["to_hit"]  # 3

        with patch("core.dnd_combat.random.randint", side_effect=[15, 4]):
            result = npc_attack(state, "Стражник", target="player")

        expected_to_hit = 15 + guard_to_hit
        assert result.to_hit_total == expected_to_hit, (
            f"npc_attack with attacks=[] must use guard to_hit={guard_to_hit}. "
            f"Expected to_hit_total={expected_to_hit}, got {result.to_hit_total}. "
            f"If to_hit_total==15, the OLD +0 fallback was used (regression)."
        )

    def test_empty_attacks_weapon_not_unarmed(self):
        """Weapon name from tier fallback must NOT be 'Unarmed' (old legacy name).

        Note: nat=20 is a critical hit → guard weapon 1d8+1 rolls 2 dice (crit rule),
        so we need 3 side_effect values: 1 for the attack roll + 2 for damage dice.
        """
        from core.dnd_combat import npc_attack

        state = _make_state_with_empty_attacks_npc("Варта")

        # [attack_roll=20, damage_die1=5, damage_die2=3]  (critical → 2 damage dice)
        with patch("core.dnd_combat.random.randint", side_effect=[20, 5, 3]):
            result = npc_attack(state, "Варта", target="player")

        assert result.weapon != "Unarmed", (
            f"Tier fallback must use tier weapon, not 'Unarmed'. Got weapon={result.weapon!r}"
        )

    def test_empty_attacks_guard_tier_weapon_name_is_spis(self):
        """Guard tier fallback weapon must be named 'Спис' (as documented in _NPC_TIER_DEFAULTS)."""
        from core.dnd_combat import npc_attack, _NPC_TIER_DEFAULTS

        state = _make_state_with_empty_attacks_npc("Охоронець")
        expected_weapon = _NPC_TIER_DEFAULTS["guard"]["attack"]["name"]  # 'Спис'

        with patch("core.dnd_combat.random.randint", side_effect=[15, 4]):
            result = npc_attack(state, "Охоронець", target="player")

        assert result.weapon == expected_weapon, (
            f"Guard tier weapon must be '{expected_weapon}', got {result.weapon!r}"
        )

    def test_empty_attacks_damage_dice_str_matches_tier(self):
        """On a hit, damage_dice_str must reflect the guard tier dice (1d8+1 piercing),
        NOT '1d4 bludgeoning' from the old unarmed fallback."""
        from core.dnd_combat import npc_attack, _NPC_TIER_DEFAULTS

        state = _make_state_with_empty_attacks_npc("Охоронець2")
        guard_dmg_str = _NPC_TIER_DEFAULTS["guard"]["attack"]["dmg"]  # "1d8+1 piercing"

        # nat=20 crit → guaranteed hit; two damage dice rolls for crit
        with patch("core.dnd_combat.random.randint", side_effect=[20, 5, 3]):
            result = npc_attack(state, "Охоронець2", target="player")

        assert result.hit is True, "nat=20 must be a hit"
        assert result.damage_dice_str == guard_dmg_str, (
            f"damage_dice_str must be '{guard_dmg_str}' (tier weapon), "
            f"got '{result.damage_dice_str}'. "
            f"If '1d4 bludgeoning' the OLD unarmed fallback was used (regression)."
        )

    def test_non_empty_attacks_list_uses_first_entry(self):
        """When attacks list is non-empty, npc_attack must use attacks[0] (unchanged behaviour)."""
        from core.dnd_combat import CombatState, CombatantRef, npc_attack

        player = _player()
        player_snap = copy.deepcopy(player)
        player_snap.setdefault("conditions", [])

        npc_snap = {
            "Name": "Воїн",
            "hp_current": 20,
            "hp_max": 20,
            "ac": 14,
            "Status": "Active",
            "conditions": [],
            "attacks": [{"name": "Арбалет", "to_hit": 5, "dmg": "1d10+3 piercing", "range": 80}],
            "tags": [],
            "ability_scores": {
                "STR": 14, "DEX": 14, "CON": 12,
                "INT": 10, "WIS": 10, "CHA": 8,
            },
        }

        player_ref = CombatantRef(kind="player", name="TestPlayer", init_roll=12)
        npc_ref    = CombatantRef(kind="npc",    name="Воїн",       init_roll=10)

        state = CombatState(
            chat_id=4099,
            round=1,
            initiative_order=[npc_ref, player_ref],
            current_actor_idx=0,
            npcs={"Воїн": npc_snap},
            player_snapshot=player_snap,
        )

        with patch("core.dnd_combat.random.randint", side_effect=[18, 6]):
            result = npc_attack(state, "Воїн", target="player")

        assert result.to_hit_total == 18 + 5, (
            f"attacks[0] to_hit=5 must be used. Expected {18+5}, got {result.to_hit_total}"
        )
        assert result.weapon == "Арбалет", (
            f"weapon must be 'Арбалет' from attacks[0], got {result.weapon!r}"
        )


# ===========================================================================
# SECTION 5 — Engine-level dict-merge: canon statblock merged into combat entry
# ===========================================================================

class TestEngineStatblockMerge:
    """Tests that the engine.py statblock merge pattern is semantically correct."""

    def _engine_merge(self, npc_name: str):
        """Replicate the exact merge logic from engine.py lines 891-901."""
        from database.operations import get_canon_npc_statblock
        entry = {"Name": npc_name, "Relation_Player": "Ворожа"}
        statblock = get_canon_npc_statblock(npc_name)
        if statblock:
            entry.update(statblock)
            entry["Name"] = npc_name  # engine re-overwrites Name
        return entry, statblock

    def test_merge_name_not_corrupted_by_statblock(self):
        """After merge, Name must remain the engine-resolved name."""
        entry, _sb = self._engine_merge("Кхал Дрого")
        assert entry["Name"] == "Кхал Дрого", (
            f"Name must be engine-resolved. Got: {entry['Name']!r}"
        )

    def test_merge_ac_from_statblock_present(self):
        """After merge, ac must come from statblock (not missing/default)."""
        entry, _sb = self._engine_merge("Кхал Дрого")
        assert "ac" in entry, "ac must be present after merge"
        assert entry["ac"] == 18

    def test_merge_hp_max_from_statblock(self):
        """After merge, hp_max must be 150 (canon value)."""
        entry, _sb = self._engine_merge("Кхал Дрого")
        assert entry.get("hp_max") == 150

    def test_merge_relation_player_preserved(self):
        """Relation_Player set before merge must survive (statblock does not include it)."""
        entry, _sb = self._engine_merge("Кхал Дрого")
        assert entry["Relation_Player"] == "Ворожа", (
            f"Relation_Player must survive the merge. Got: {entry['Relation_Player']!r}"
        )

    def test_non_canon_npc_statblock_lookup_returns_none(self):
        """A non-canon NPC name triggers the tier-fallback path (returns None)."""
        from database.operations import get_canon_npc_statblock
        result = get_canon_npc_statblock("Безіменний Стражник")
        assert result is None, (
            f"Non-canon NPC must return None → tier fallback. Got: {result}"
        )

    def test_full_pipeline_canon_hp_max_survives_initiate_combat(self):
        """End-to-end: engine merge + initiate_combat → hp_max stays at 150.

        This is the primary B2-class regression guard.
        """
        from core.dnd_combat import initiate_combat

        entry, statblock = self._engine_merge("Кхал Дрого")
        assert statblock is not None, "Precondition: Кхал Дрого must be canon"

        player = _player()
        with patch("core.dnd_core.random.randint", return_value=10):
            state = initiate_combat(player, [entry], chat_id=5001)

        snap = state.npcs.get("Кхал Дрого")
        assert snap is not None, "Кхал Дрого must appear in CombatState.npcs"
        assert snap["hp_max"] == 150, (
            f"B2 BUG: hp_max overwritten by tier defaults. "
            f"Expected 150 (canon), got {snap['hp_max']}. "
            f"initiate_combat must NOT overwrite hp_max when it already exists."
        )
        assert snap["ac"] == 18, (
            f"B2 BUG: ac overwritten by tier defaults. Expected 18, got {snap['ac']}."
        )
