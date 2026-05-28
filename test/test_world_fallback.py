"""
test_world_fallback.py

Tests for _build_deterministic_dnd_profile (core/world.py).

Goal: verify that the LLM-failure fallback at character creation returns a
profile that is structurally identical to the successful LLM path —
same top-level keys, correct D&D invariants (§5.2), JSON-serialisable.

Isolation: core.world imports database.sheets at module level (triggers
GoogleSheetsDB singleton which requires SPREADSHEET_ID).  We inject a mock
into sys.modules before world is first imported — same pattern used in
test_game_intro_scene.py.
"""

import json
import sys
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Pre-import isolation: inject database stubs into sys.modules BEFORE
# core.world is first collected/imported.  This must run at module level.
# ---------------------------------------------------------------------------

def _ensure_db_mocked():
    if "database.sheets" not in sys.modules:
        fake_sheets = MagicMock()
        fake_sheets.db = MagicMock()
        sys.modules["database.sheets"] = fake_sheets
    if "database.operations" not in sys.modules:
        fake_ops = MagicMock()
        fake_ops.refresh_npc_database = MagicMock(return_value=None)
        fake_ops.find_best_match = MagicMock(return_value=None)
        fake_ops.ensure_user_npc_sheet = MagicMock(return_value=None)
        fake_ops._npc_tab_name = MagicMock(return_value="NPC_0")
        sys.modules["database.operations"] = fake_ops
    if "database.canon_npc" not in sys.modules:
        fake_canon = MagicMock()
        fake_canon.get_canon_npcs_copy = MagicMock(return_value=[])
        sys.modules["database.canon_npc"] = fake_canon


_ensure_db_mocked()

# Now safe to import core.world
from core.world import _build_deterministic_dnd_profile  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

VALID_ABILITIES = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}

# Keys that MUST be present — matches generate_initial_stats output exactly.
REQUIRED_KEYS = {
    # D&D core
    "level", "xp", "class", "heritage", "background",
    "ability_scores", "proficiency_bonus",
    "hp_max", "hp_current", "hit_dice_total",
    "ac", "skill_profs", "skill_expertise", "saves_proficient",
    "features", "conditions", "mode", "combat_state_ref", "asi_pending",
    "languages",
    # Narrative
    "Ім'я", "Дім", "Поточне місцезнаходження", "Поточна сцена", "Регіон",
    "Світогляд", "Риси", "Вади", "Особисті риси", "Зв'язок", "Вада персонажа",
    "Вороги", "Друзі",
    # Legacy compatibility (§5.2)
    "Здоров'я", "Енергія", "Інвентар", "Особисте Золото",
    "Зброя", "Броня", "Транспорт", "Ігровий час", "Час_хвилини",
    "Годинники", "Репутація (Рідний регіон)", "temp_debuffs",
    "training_cooldowns", "reputation",
    # Structured equipment
    "equipped_weapon", "equipped_armor", "equipped_shield",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeterministicDndProfile:

    def test_has_all_required_keys(self):
        """Profile must contain every key produced by the successful LLM path."""
        profile = _build_deterministic_dnd_profile("Тест", "Старк", "The North", "Knight", "Westerosi (Andal)")
        missing = REQUIRED_KEYS - set(profile.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_ability_scores_all_six_abilities(self):
        """ability_scores must contain exactly the 6 D&D abilities."""
        profile = _build_deterministic_dnd_profile("Тест", "Старк", "The North", "Knight", "Westerosi (Andal)")
        assert set(profile["ability_scores"].keys()) == VALID_ABILITIES

    def test_ability_scores_range(self):
        """All ability scores must be 1-20 (§5.2 D&D invariant)."""
        for class_name in ("Knight", "Maester", "Courtier", "Sellsword"):
            profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", class_name, "Westerosi (Andal)")
            for ab, val in profile["ability_scores"].items():
                assert 1 <= val <= 20, (
                    f"{class_name}: ability {ab}={val} out of range 1-20"
                )

    def test_hp_max_positive(self):
        """hp_max must be > 0 for any class."""
        for class_name in ("Knight", "Maester", "Septon", "Bastard"):
            profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", class_name, "Westerosi (Andal)")
            assert profile["hp_max"] > 0, f"{class_name}: hp_max must be positive"

    def test_hp_current_equals_hp_max(self):
        """New character: hp_current must equal hp_max."""
        profile = _build_deterministic_dnd_profile("Тест", "Старк", "The North", "Knight", "Westerosi (Andal)")
        assert profile["hp_current"] == profile["hp_max"]

    def test_level_one(self):
        """Fallback must always produce L1."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Courtier", "Valyrian Descent")
        assert profile["level"] == 1

    def test_xp_zero(self):
        """L1 start: xp must be 0."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Courtier", "Valyrian Descent")
        assert profile["xp"] == 0

    def test_proficiency_bonus_two(self):
        """L1 proficiency bonus is always +2 (§5.2)."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        assert profile["proficiency_bonus"] == 2

    def test_energy_1000(self):
        """Legacy energy scale starts at 1000 (§5.2 Енергія invariant)."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        assert profile["Енергія"] == 1000

    def test_legacy_health_mirrors_hp_current(self):
        """Здоров'я (legacy UI) must equal hp_current at creation."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        assert profile["Здоров'я"] == profile["hp_current"]

    def test_mode_normal(self):
        """New character starts in NORMAL FSM mode."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        assert profile["mode"] == "NORMAL"

    def test_skill_profs_non_empty(self):
        """Every class must grant at least 1 skill proficiency."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        assert len(profile["skill_profs"]) >= 1

    def test_equipped_weapon_structure(self):
        """equipped_weapon must be None or a dict with required keys."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        w = profile["equipped_weapon"]
        if w is not None:
            assert "name" in w
            assert "damage_dice" in w
            assert "damage_type" in w
            assert "properties" in w
            assert isinstance(w["properties"], list)

    def test_json_serializable(self):
        """Profile must be fully JSON-serializable (no Feature objects etc.)."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Maester", "First Men (Stark line)")
        try:
            serialized = json.dumps(profile, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            pytest.fail(f"Profile is not JSON-serializable: {exc}")
        parsed = json.loads(serialized)
        assert set(parsed.keys()) == set(profile.keys())

    def test_char_name_and_house_recorded(self):
        """Ім'я and Дім must match arguments."""
        profile = _build_deterministic_dnd_profile("Джон", "Старк", "The North", "Knight", "First Men (Stark line)")
        assert profile["Ім'я"] == "Джон"
        assert profile["Дім"] == "Старк"

    def test_unknown_class_falls_back_to_knight(self):
        """Unknown class name must not raise — falls back to Knight."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "InvalidClass", "Westerosi (Andal)")
        assert profile["class"] == "Knight"
        assert profile["level"] == 1
        assert profile["hp_max"] > 0

    def test_unknown_heritage_falls_back_to_andal(self):
        """Unknown heritage name must not raise — falls back to Westerosi (Andal)."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "InvalidHeritage")
        assert profile["heritage"] == "Westerosi (Andal)"
        assert profile["level"] == 1

    def test_features_are_dicts(self):
        """features must be list[dict] with name/desc/source keys (JSON-safe)."""
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Westerosi (Andal)")
        assert isinstance(profile["features"], list)
        assert len(profile["features"]) >= 1
        for feat in profile["features"]:
            assert isinstance(feat, dict)
            assert "name" in feat
            assert "desc" in feat
            assert "source" in feat

    def test_valyrian_heritage_boosts_cha_int(self):
        """Valyrian Descent applies CHA+2, INT+1 on top of standard array.

        Courtier primary_abilities = [CHA, INT]:
          standard array slot 0 (15) → CHA, slot 1 (14) → INT
          After Valyrian: CHA = min(20, 15+2)=17, INT = min(20, 14+1)=15
        """
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Courtier", "Valyrian Descent")
        scores = profile["ability_scores"]
        assert scores["CHA"] == 17
        assert scores["INT"] == 15

    def test_standard_array_primary_gets_15(self):
        """Knight (STR/CHA primary) gets 15 in STR before heritage bonuses.

        Knight primary_abilities = ["STR", "CHA"] per GOT_CLASSES.
        With Valyrian heritage (CHA+2, INT+1), STR stays at 15.
        """
        profile = _build_deterministic_dnd_profile("Тест", "Дім", "Вестерос", "Knight", "Valyrian Descent")
        scores = profile["ability_scores"]
        # STR = slot 0 of standard array (15), no Valyrian bonus on STR
        assert scores["STR"] == 15
        # CHA = slot 1 (14) + Valyrian +2 = 16
        assert scores["CHA"] == 16
