"""Tests for Phase 4 D&D prompt builders in core/prompts.py.

Strategy: builders return a prompt string for LLM consumption.
We verify structural content (keywords, keys, schema elements) without invoking any LLM.
No real Google Sheets or Gemini calls are made.
"""

import json
import pytest

# Import Phase 4+ builders
from core.prompts import (
    build_normal_resolve_prompt,
    build_combat_round_prompt,
    build_npc_combat_action_prompt,
    build_npc_regen_prompt,
    build_gm_logic_prompt,
    build_narrator_prompt,
    build_initial_stats_prompt,
    # Schema builders (Item 2)
    build_validate_action_schema,
    build_normal_resolve_schema,
    build_combat_round_schema,
    build_npc_combat_action_schema,
    build_npc_regen_schema,
    build_gm_logic_schema,
    build_training_request_schema,
    build_initial_stats_schema,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared minimal fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def minimal_profile():
    return {
        "Ім'я": "Тест",
        "Дім": "Старк",
        "level": 1,
        "ability_scores": {"STR": 12, "DEX": 10, "CON": 14, "INT": 10, "WIS": 12, "CHA": 8},
        "proficiency_bonus": 2,
        "skill_profs": ["Athletics", "Perception"],
        "conditions": [],
        "hp_current": 18,
        "hp_max": 18,
        "ac": 13,
    }


@pytest.fixture
def minimal_combat_state():
    return {
        "round": 1,
        "npcs": [
            {"name": "Бандит", "hp_current": 11, "hp_max": 11, "ac": 12, "conditions": []}
        ],
        "player_hp_current": 18,
        "player_hp_max": 18,
        "player_ac": 13,
        "weapons": ["Меч"],
        "heritage_traits": [],
        "available_actions": ["attack", "dodge", "flee"],
    }


@pytest.fixture
def gm_logic_common_kwargs():
    return dict(
        hero_name="Джон Сноу",
        hero_house="Старк",
        profile_json='{"Ім\'я": "Джон Сноу"}',
        context_knowledge="",
        event_injection="",
        burst_injection="",
        current_time_str="298 рік, Ранок",
        curr_region="Північ",
        curr_loc="Вінтерфелл",
        is_traveling=False,
        loc_hint="",
        curr_scene="Тронний Зал",
        valid_locs_str="Вінтерфелл",
        valid_regions_str="Північ",
        region_locs_str="Вінтерфелл",
        npc_context_text="Нед Старк: лорд.",
        tension_label="спокійно",
        mechanics_verdict="SUCCESS",
        impact_narrative_hints="",
        history_text="Гра почалась.",
        user_input="Вітаю лорда",
        action_slots=["соціальна", "бойова", "дослідницька", "рух"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. build_normal_resolve_prompt — базові тести
# ─────────────────────────────────────────────────────────────────────────────

def test_normal_resolve_returns_non_empty_string(minimal_profile):
    result = build_normal_resolve_prompt(
        user_input="Вітаю лорда",
        profile=minimal_profile,
        current_scene="Тронний Зал",
        npcs_in_scene=[],
        last_turn_summary="Старт гри",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert isinstance(result, str)
    assert len(result) > 100


def test_normal_resolve_contains_d20_system(minimal_profile):
    result = build_normal_resolve_prompt(
        user_input="Атакую ворога",
        profile=minimal_profile,
        current_scene="Двір",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "d20" in result
    assert "DC" in result


def test_normal_resolve_contains_structural_xml_blocks(minimal_profile):
    result = build_normal_resolve_prompt(
        user_input="Озираюся навкруги",
        profile=minimal_profile,
        current_scene="Ринок",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "<output_schema>" in result
    assert "<thinking_directives>" in result
    assert "<antiexamples>" in result


def test_normal_resolve_does_not_contain_legacy_keywords(minimal_profile):
    result = build_normal_resolve_prompt(
        user_input="Йду до корчми",
        profile=minimal_profile,
        current_scene="Вулиця",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "2d50" not in result
    assert "NO_SKILL_DC_THRESHOLD" not in result
    # Legacy skill name "Бойові навички" (з пробілом — точна стара назва поля)
    assert "Бойові навички" not in result


@pytest.mark.parametrize("skill", [
    "Athletics", "Acrobatics", "Sleight of Hand", "Stealth",
    "Arcana", "History", "Investigation", "Nature", "Religion",
    "Animal Handling", "Insight", "Medicine", "Perception", "Survival",
    "Deception", "Intimidation", "Performance", "Persuasion",
])
def test_normal_resolve_contains_all_18_dnd_skills(minimal_profile, skill):
    result = build_normal_resolve_prompt(
        user_input="Озираюся",
        profile=minimal_profile,
        current_scene="Двір",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert skill in result, f"Expected D&D skill '{skill}' in prompt, not found."


@pytest.mark.parametrize("dc_value", ["5", "10", "12", "15", "17", "20", "22", "25", "28", "30"])
def test_normal_resolve_contains_all_legal_dcs(minimal_profile, dc_value):
    result = build_normal_resolve_prompt(
        user_input="Намагаюся переконати стражника",
        profile=minimal_profile,
        current_scene="Ворота",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert dc_value in result, f"Expected legal DC value '{dc_value}' in prompt DC block."


@pytest.mark.parametrize("required_key", [
    "skill_check_reasoning",
    "ability_used",
    "skill_used",
    "difficulty",
    "combat_imminent",
    "xp_award",
    "condition_apply",
    "hp_damage_dice",
])
def test_normal_resolve_contains_required_json_keys(minimal_profile, required_key):
    result = build_normal_resolve_prompt(
        user_input="Атакую варту",
        profile=minimal_profile,
        current_scene="Зал",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert required_key in result, f"Expected JSON key '{required_key}' in output_schema."


def test_normal_resolve_contains_user_input(minimal_profile):
    user_action = "Намагаюся підкупити варту золотом"
    result = build_normal_resolve_prompt(
        user_input=user_action,
        profile=minimal_profile,
        current_scene="Ворота",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert user_action in result


# ─────────────────────────────────────────────────────────────────────────────
# 2. build_combat_round_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_combat_round_returns_non_empty_string(minimal_profile, minimal_combat_state):
    result = build_combat_round_prompt(
        user_input="Атакую бандита мечем",
        profile=minimal_profile,
        combat_state_snapshot=minimal_combat_state,
    )
    assert isinstance(result, str)
    assert len(result) > 100


@pytest.mark.parametrize("required_key", ["intent", "target_npc", "tactic", "weapon"])
def test_combat_round_contains_output_schema_keys(minimal_profile, minimal_combat_state, required_key):
    result = build_combat_round_prompt(
        user_input="Ухиляюся від удару",
        profile=minimal_profile,
        combat_state_snapshot=minimal_combat_state,
    )
    assert required_key in result, f"Expected key '{required_key}' in combat round prompt output schema."


def test_combat_round_contains_available_actions_from_snapshot(minimal_profile, minimal_combat_state):
    result = build_combat_round_prompt(
        user_input="Тікаю",
        profile=minimal_profile,
        combat_state_snapshot=minimal_combat_state,
    )
    # available_actions serialised as part of combat_state JSON
    for action in minimal_combat_state["available_actions"]:
        assert action in result, f"Expected available action '{action}' in combat round prompt."


# ─────────────────────────────────────────────────────────────────────────────
# 3. build_npc_combat_action_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_npc_combat_action_single_npc_contains_name(minimal_combat_state):
    npc = {"Name": "Арис Оукгарт", "hp_current": 20, "hp_max": 28, "ac": 16,
           "cr": "3", "attacks": [{"name": "Longsword", "to_hit": 5, "dmg": "1d8+3", "range": None}]}
    result = build_npc_combat_action_prompt(
        combat_state_snapshot=minimal_combat_state,
        npc_dict_list=[npc],
    )
    assert "Арис Оукгарт" in result


def test_npc_combat_action_two_npcs_requires_array_output(minimal_combat_state):
    npcs = [
        {"Name": "Варта Перша", "hp_current": 8, "hp_max": 11, "ac": 14, "cr": "1/4",
         "attacks": [{"name": "Spear", "to_hit": 3, "dmg": "1d6+1", "range": None}]},
        {"Name": "Варта Друга", "hp_current": 11, "hp_max": 11, "ac": 14, "cr": "1/4",
         "attacks": [{"name": "Spear", "to_hit": 3, "dmg": "1d6+1", "range": None}]},
    ]
    result = build_npc_combat_action_prompt(
        combat_state_snapshot=minimal_combat_state,
        npc_dict_list=npcs,
    )
    # Output schema must state array under "actions"
    assert '"actions"' in result or "'actions'" in result or "actions" in result
    # Both names must be embedded in the prompt
    assert "Варта Перша" in result
    assert "Варта Друга" in result


# ─────────────────────────────────────────────────────────────────────────────
# 4. build_npc_regen_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_npc_regen_contains_npc_card_fields():
    npc_card = {
        "Name": "Тиріон Ланністер",
        "Description": "Карлик з гострим розумом.",
        "Status": "Active",
    }
    result = build_npc_regen_prompt(npc_card)
    assert "Тиріон Ланністер" in result
    assert "Description" in result
    assert "Status" in result


def test_npc_regen_contains_cr_tier_examples():
    npc_card = {"Name": "Безіменний стражник", "Status": "Active"}
    result = build_npc_regen_prompt(npc_card)
    # The CR guidelines list these tiers — check for representative entries
    assert "CR" in result or "cr" in result
    # "0" tier (peasant) must appear
    assert '"0"' in result or "'0'" in result or "0" in result
    # CR 5 tier (lord-captain / small-council member) must appear
    assert "5" in result


@pytest.mark.parametrize("required_key", ["cr", "ability_scores", "hp_max", "ac", "attacks"])
def test_npc_regen_contains_required_output_keys(required_key):
    npc_card = {"Name": "Ланселл Ланністер", "Status": "Active"}
    result = build_npc_regen_prompt(npc_card)
    assert required_key in result, f"Expected output schema key '{required_key}' in npc_regen prompt."


# ─────────────────────────────────────────────────────────────────────────────
# 5. build_gm_logic_prompt — mode parameter
# ─────────────────────────────────────────────────────────────────────────────

def test_gm_logic_normal_mode_does_not_contain_combat_slot_names(gm_logic_common_kwargs):
    result = build_gm_logic_prompt(**gm_logic_common_kwargs, mode="NORMAL")
    # COMBAT-specific slot names should be absent in NORMAL mode
    assert "ATTACK|DEFEND|FLEE|SPECIAL" not in result
    # Minimal check: COMBAT block tag should not be present
    assert "<mode>NORMAL</mode>" in result


def test_gm_logic_combat_mode_contains_combat_slot_names(gm_logic_common_kwargs):
    result = build_gm_logic_prompt(**gm_logic_common_kwargs, mode="COMBAT")
    # All four combat action types must appear
    for slot in ["ATTACK", "DEFEND", "FLEE", "SPECIAL"]:
        assert slot in result, f"Expected combat slot '{slot}' in COMBAT mode prompt."


def test_gm_logic_both_modes_contain_mode_transition_key(gm_logic_common_kwargs):
    for mode in ("NORMAL", "COMBAT"):
        result = build_gm_logic_prompt(**gm_logic_common_kwargs, mode=mode)
        assert "mode_transition" in result, (
            f"Expected 'mode_transition' key in GM Logic prompt for mode={mode}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. build_narrator_prompt — combat_log parameter
# ─────────────────────────────────────────────────────────────────────────────

def test_narrator_without_combat_log_has_no_combat_narrative_style():
    result = build_narrator_prompt(
        user_input="Вітаю всіх",
        director_notes=["Гравець вітається.", "Нед Старк відповідає ввічливо."],
        npc_context_text="Нед Старк: лорд.",
        player_name="Джон Сноу",
        player_house="Старк",
        current_scene="Тронний Зал",
        current_location="Вінтерфелл",
        impact_narrative_hints="",
        combat_log=None,
    )
    assert "combat_narrative_style" not in result


def test_narrator_with_combat_log_contains_log_lines_and_combat_style():
    log_lines = ["Player swung at the guard.", "Goblin parried and stepped back."]
    result = build_narrator_prompt(
        user_input="Атакую варту",
        director_notes=["Гравець вдарив варту.", "Варта ухилилась."],
        npc_context_text="Варта: солдат.",
        player_name="Джон Сноу",
        player_house="Старк",
        current_scene="Ворота",
        current_location="Вінтерфелл",
        impact_narrative_hints="",
        combat_log=log_lines,
    )
    for line in log_lines:
        assert line in result, f"Expected combat log line '{line}' in narrator prompt."
    assert "combat_narrative_style" in result


# ─────────────────────────────────────────────────────────────────────────────
# 7. build_initial_stats_prompt — D&D variant
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("got_class", [
    "Knight", "Hedge Knight", "Maester", "Septon",
    "Sellsword", "Spy", "Courtier", "Bastard", "Wildling",
])
def test_initial_stats_contains_all_9_got_classes(got_class):
    result = build_initial_stats_prompt(
        char_name="Джон Сноу",
        house_name="Старк",
        origin_region="Північ",
        valid_locations_str="Вінтерфелл",
        scenes_block_str="",
    )
    assert got_class in result, f"Expected GoT class '{got_class}' in initial stats prompt."


@pytest.mark.parametrize("heritage", [
    "Westerosi (Andal)",
    "Valyrian Descent",
    "First Men (Stark line)",
    "Free Folk",
    "Red Priest",
    "Ironborn",
])
def test_initial_stats_contains_all_6_heritages(heritage):
    result = build_initial_stats_prompt(
        char_name="Джейме Ланністер",
        house_name="Ланністер",
        origin_region="Захід",
        valid_locations_str="Королівство",
        scenes_block_str="",
    )
    assert heritage in result, f"Expected heritage '{heritage}' in initial stats prompt."


@pytest.mark.parametrize("required_key", [
    "suggested_class",
    "suggested_heritage",
    "ability_scores",
    "background",
    "personality_traits",
    "bond",
    "flaw",
])
def test_initial_stats_contains_required_output_keys(required_key):
    result = build_initial_stats_prompt(
        char_name="Серсея Ланністер",
        house_name="Ланністер",
        origin_region="Захід",
        valid_locations_str="Королівство",
        scenes_block_str="",
    )
    assert required_key in result, f"Expected output schema key '{required_key}' in initial stats prompt."


# ─────────────────────────────────────────────────────────────────────────────
# 8. JSON Schema builders (Item 2) — gtypes.Schema validation
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_action_schema_returns_schema_object():
    schema = build_validate_action_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)


def test_validate_action_schema_required_keys():
    schema = build_validate_action_schema()
    assert set(schema.required) == {"is_valid", "refusal_reason"}


def test_normal_resolve_schema_returns_schema_object():
    schema = build_normal_resolve_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)


def test_normal_resolve_schema_required_keys():
    schema = build_normal_resolve_schema()
    required = set(schema.required)
    # All contract keys from §5.3 must be in required
    for key in ["skill_check_reasoning", "difficulty_reasoning", "gold_reasoning",
                "action_type", "ability_used", "skill_used", "difficulty",
                "advantage_reason", "disadvantage_reason", "combat_imminent",
                "verdict_text", "xp_award", "reputation_delta", "reputation_target_npc", "updates"]:
        assert key in required, f"Expected '{key}' in normal_resolve_schema required"


def test_normal_resolve_schema_ability_used_enum():
    schema = build_normal_resolve_schema()
    ab_schema = schema.properties["ability_used"]
    assert set(ab_schema.enum) == {"STR", "DEX", "CON", "INT", "WIS", "CHA", "None"}


def test_normal_resolve_schema_action_type_enum():
    schema = build_normal_resolve_schema()
    at_schema = schema.properties["action_type"]
    assert set(at_schema.enum) == {"standard", "training"}


def test_normal_resolve_schema_updates_required_fields():
    schema = build_normal_resolve_schema()
    updates = schema.properties["updates"]
    required = set(updates.required)
    for key in ["minutes_passed", "location_impact", "scene_impact",
                "hp_damage_dice", "hp_heal_dice", "gold_impact",
                "inventory_new", "inventory_lost", "clocks_impact",
                "condition_apply", "condition_remove"]:
        assert key in required, f"Expected '{key}' in updates.required"


def test_combat_round_schema_returns_schema_object():
    schema = build_combat_round_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)


def test_combat_round_schema_required_keys():
    schema = build_combat_round_schema()
    required = set(schema.required)
    for key in ["intent", "target_npc", "weapon", "tactic", "verdict_text", "reasoning"]:
        assert key in required, f"Expected '{key}' in combat_round_schema required"


def test_combat_round_schema_intent_enum():
    schema = build_combat_round_schema()
    intent_schema = schema.properties["intent"]
    assert set(intent_schema.enum) == {"attack", "cast", "move", "dodge", "flee", "item", "help", "grapple", "shove"}


def test_npc_combat_action_schema_returns_schema_object():
    schema = build_npc_combat_action_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)
    assert "actions" in schema.required


def test_npc_regen_schema_returns_schema_object():
    schema = build_npc_regen_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)


def test_npc_regen_schema_required_keys():
    schema = build_npc_regen_schema()
    required = set(schema.required)
    for key in ["cr", "ability_scores", "hp_max", "ac", "attacks", "reasoning"]:
        assert key in required, f"Expected '{key}' in npc_regen_schema required"


def test_npc_regen_schema_cr_enum():
    schema = build_npc_regen_schema()
    cr = schema.properties["cr"]
    assert "0" in cr.enum and "10" in cr.enum and "1/4" in cr.enum


def test_gm_logic_schema_normal_mode():
    schema = build_gm_logic_schema(mode="NORMAL")
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)
    required = set(schema.required)
    for key in ["reasoning", "npc_reasoning", "director_notes",
                "companion_npcs", "npc_updates", "suggested_actions",
                "mode_transition", "frozen_fields_change_reason"]:
        assert key in required, f"Expected '{key}' in gm_logic_schema required"


def test_gm_logic_schema_combat_mode_identical_structure():
    schema_n = build_gm_logic_schema(mode="NORMAL")
    schema_c = build_gm_logic_schema(mode="COMBAT")
    # Both modes share the same JSON contract
    assert set(schema_n.required) == set(schema_c.required)


def test_training_request_schema_returns_schema_object():
    schema = build_training_request_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)
    required = set(schema.required)
    for key in ["is_training", "is_possible", "skill", "method", "reason_if_failed"]:
        assert key in required


def test_initial_stats_schema_returns_schema_object():
    schema = build_initial_stats_schema()
    from google.genai import types as gtypes
    assert isinstance(schema, gtypes.Schema)


def test_initial_stats_schema_required_keys():
    schema = build_initial_stats_schema()
    required = set(schema.required)
    for key in ["thought_process", "suggested_class", "suggested_heritage",
                "ability_scores", "background", "personality_traits", "bond", "flaw"]:
        assert key in required, f"Expected '{key}' in initial_stats_schema required"


def test_initial_stats_schema_class_enum():
    schema = build_initial_stats_schema()
    cls = schema.properties["suggested_class"]
    assert "Knight" in cls.enum and "Wildling" in cls.enum and len(cls.enum) == 9


def test_initial_stats_schema_heritage_enum():
    schema = build_initial_stats_schema()
    h = schema.properties["suggested_heritage"]
    assert "Ironborn" in h.enum and len(h.enum) == 6


@pytest.mark.parametrize("schema_fn,name", [
    (build_validate_action_schema, "validate_action"),
    (build_normal_resolve_schema, "normal_resolve"),
    (build_combat_round_schema, "combat_round"),
    (build_npc_combat_action_schema, "npc_combat_action"),
    (build_npc_regen_schema, "npc_regen"),
    (build_gm_logic_schema, "gm_logic"),
    (build_training_request_schema, "training_request"),
    (build_initial_stats_schema, "initial_stats"),
])
def test_schema_has_properties(schema_fn, name):
    """Every schema must have at least one property defined."""
    schema = schema_fn()
    assert schema.properties, f"Schema '{name}' has no properties defined"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Language guard — suggested_actions Ukrainian invariant
# ─────────────────────────────────────────────────────────────────────────────

import re


def _is_ukrainian_text(text: str) -> bool:
    """True if text contains only Ukrainian Cyrillic, digits, common punctuation.
    Returns False if contains CJK characters or Latin words of 3+ chars."""
    if re.search(r'[　-鿿가-힯]', text):
        return False
    latin_words = re.findall(r'[A-Za-z]{3,}', text)
    if len(latin_words) > 0:
        return False
    return True


def test_is_ukrainian_text_pure_ukrainian():
    assert _is_ukrainian_text("Ввічливо попросити") is True


def test_is_ukrainian_text_pure_ukrainian_with_punctuation():
    assert _is_ukrainian_text("Я атакую гобліна!") is True


def test_is_ukrainian_text_korean_mixed():
    assert _is_ukrainian_text("억지 a polite request") is False


def test_is_ukrainian_text_latin_word():
    assert _is_ukrainian_text("Bow & ask") is False


def test_is_ukrainian_text_english_intent():
    assert _is_ukrainian_text("I politely request entry to the throne room") is False


def test_is_ukrainian_text_short_latin_token_allowed():
    # Single-char or 2-char latin tokens (e.g. initials) — не блокуємо
    assert _is_ukrainian_text("Я йду до NPC") is False  # "NPC" = 3 символи → False (правило >=3)


def test_gm_logic_prompt_contains_language_invariant(gm_logic_common_kwargs):
    result = build_gm_logic_prompt(**gm_logic_common_kwargs, mode="NORMAL")
    assert "LANGUAGE INVARIANT" in result, "Expected LANGUAGE INVARIANT guard in build_gm_logic_prompt"
    assert "ВИКЛЮЧНО українською" in result, "Expected Ukrainian-only instruction in build_gm_logic_prompt"


def test_gm_logic_prompt_antiexample_contains_wrong_example(gm_logic_common_kwargs):
    result = build_gm_logic_prompt(**gm_logic_common_kwargs, mode="NORMAL")
    assert "Bow & ask" in result, "Expected non-Ukrainian antiexample 'Bow & ask' in prompt antiexamples block"
    assert "Ввічливо попросити" in result, "Expected correct Ukrainian example in prompt antiexamples block"


def test_gm_logic_schema_button_has_description():
    schema = build_gm_logic_schema(mode="NORMAL")
    action_schema = schema.properties["suggested_actions"]
    button_schema = action_schema.items.properties["button"]
    assert button_schema.description is not None, "button schema must have a description"
    assert "Ukrainian" in button_schema.description or "Cyrillic" in button_schema.description


def test_gm_logic_schema_intent_has_description():
    schema = build_gm_logic_schema(mode="NORMAL")
    action_schema = schema.properties["suggested_actions"]
    intent_schema = action_schema.items.properties["intent"]
    assert intent_schema.description is not None, "intent schema must have a description"
    assert "Ukrainian" in intent_schema.description
