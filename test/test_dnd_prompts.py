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


@pytest.mark.parametrize("dc_value", [str(dc) for dc in __import__("core.dnd_core", fromlist=["LEGAL_DCS"]).LEGAL_DCS])
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


# ─────────────────────────────────────────────────────────────────────────────
# 10. Class features context in build_normal_resolve_prompt
# ─────────────────────────────────────────────────────────────────────────────

from core.dnd_classes import Feature


def _courtier_l3_profile():
    """Minimal Courtier L3 profile with two L1 features."""
    return {
        "Ім'я": "Петір Бейліш",
        "Дім": "Бейліш",
        "level": 3,
        "class": "Courtier",
        "ability_scores": {"STR": 8, "DEX": 12, "CON": 10, "INT": 14, "WIS": 12, "CHA": 16},
        "proficiency_bonus": 2,
        "skill_profs": ["Persuasion", "Deception", "Insight"],
        "conditions": [],
        "hp_current": 20,
        "hp_max": 20,
        "ac": 12,
        "features": [
            Feature(name="Срібний язик", desc="1/day: перекидаєш Переконання або Обман після бачення результату; береш новий кидок навіть якщо він нижчий.", source="Courtier L1"),
            Feature(name="Шляхетне поводження", desc="Перевага на CHA-перевірки (Переконання, Обман, Гра, Залякування) проти осіб рівного або нижчого соціального статусу.", source="Courtier L1"),
        ],
    }


def _call_normal_resolve(profile):
    return build_normal_resolve_prompt(
        user_input="test action",
        profile=profile,
        current_scene="Тронний Зал",
        npcs_in_scene=[],
        last_turn_summary="Гра почалась.",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )


def test_normal_resolve_prompt_includes_features_block():
    """Worker prompt must include feature names and sources when features are present."""
    profile = _courtier_l3_profile()
    result = _call_normal_resolve(profile)
    assert "Срібний язик" in result, "Expected 'Срібний язик' feature name in prompt"
    assert "Шляхетне поводження" in result, "Expected 'Шляхетне поводження' feature name in prompt"
    assert "Courtier L1" in result, "Expected feature source 'Courtier L1' in prompt"
    assert "<class_features>" in result, "Expected <class_features> XML block in prompt"


def test_normal_resolve_prompt_omits_features_block_when_empty():
    """If features list is empty — the <class_features> block must NOT appear."""
    profile = _courtier_l3_profile()
    profile["features"] = []
    result = _call_normal_resolve(profile)
    assert "<class_features>" not in result, "Expected no <class_features> block when features list is empty"
    assert "Class features" not in result, "Expected no class features noise when list is empty"


def test_normal_resolve_prompt_omits_features_block_when_key_absent():
    """If 'features' key is absent from profile — no crash and no features block."""
    profile = _courtier_l3_profile()
    del profile["features"]
    result = _call_normal_resolve(profile)
    assert isinstance(result, str)
    assert "<class_features>" not in result


def test_normal_resolve_prompt_truncates_long_desc():
    """Feature descriptions longer than 120 chars must be truncated with '...' in prompt."""
    long_desc = "А" * 130  # 130 Ukrainian chars > 120 limit
    profile = _courtier_l3_profile()
    profile["features"] = [Feature(name="ТестФіча", desc=long_desc, source="Test L1")]
    result = _call_normal_resolve(profile)
    # The full 130-char desc must NOT appear; truncated version with '...' must appear
    assert long_desc not in result, "Long description should be truncated"
    assert "ТестФіча" in result, "Feature name must still appear after truncation"
    assert "..." in result, "Truncated description must end with '...'"


def test_normal_resolve_prompt_gate0_instruction_present():
    """GATE 0 feature-check instruction must be in thinking_directives when features present."""
    profile = _courtier_l3_profile()
    result = _call_normal_resolve(profile)
    assert "GATE 0" in result, "Expected GATE 0 class features check in thinking_directives"
    assert "advantage_reason" in result, "Expected advantage_reason instruction in GATE 0"


def test_normal_resolve_prompt_features_supports_dict_format():
    """Features stored as plain dicts (not Feature dataclass) must also render correctly."""
    profile = _courtier_l3_profile()
    profile["features"] = [
        {"name": "Срібний язик", "desc": "1/day reroll.", "source": "Courtier L1"},
    ]
    result = _call_normal_resolve(profile)
    assert "Срібний язик" in result
    assert "Courtier L1" in result


# ─────────────────────────────────────────────────────────────────────────────
# 10b. Off-by-one boundary tests for desc truncation (WARN-3)
# ─────────────────────────────────────────────────────────────────────────────

def test_feature_desc_exactly_120_chars_not_truncated():
    """Edge case: desc with exactly 120 chars must NOT be truncated (len > 120 is False)."""
    desc_120 = "Б" * 120
    profile = _courtier_l3_profile()
    profile["features"] = [{"name": "TestFeature", "desc": desc_120, "source": "Test"}]
    result = _call_normal_resolve(profile)
    # Full 120-char string must be present verbatim
    assert desc_120 in result, "120-char desc must NOT be truncated"
    # No ellipsis suffix appended to it
    assert (desc_120 + "...") not in result, "120-char desc must not gain '...' suffix"


def test_feature_desc_121_chars_is_truncated():
    """Edge case: desc with 121 chars must be truncated to first 117 chars + '...'."""
    desc_121 = "В" * 121
    profile = _courtier_l3_profile()
    profile["features"] = [{"name": "TestFeature", "desc": desc_121, "source": "Test"}]
    result = _call_normal_resolve(profile)
    # Truncation: desc[:117] + "..."
    truncated_expected = "В" * 117 + "..."
    assert truncated_expected in result, "121-char desc must be truncated to first 117 chars + '...'"
    # Full 121-char string must NOT appear
    assert desc_121 not in result, "Full 121-char desc must not be present after truncation"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Heritage traits in Worker prompt (Round 2 fire resistance activation)
# ─────────────────────────────────────────────────────────────────────────────

def _valyrian_profile():
    """Minimal Valyrian Descent profile — no class features, only heritage traits."""
    return {
        "Ім'я": "Дейнеріс",
        "Дім": "Таргарієн",
        "level": 2,
        "class": "Courtier",
        "heritage": "Valyrian Descent",
        "ability_scores": {"STR": 8, "DEX": 10, "CON": 10, "INT": 14, "WIS": 12, "CHA": 18},
        "proficiency_bonus": 2,
        "skill_profs": ["Persuasion"],
        "conditions": [],
        "hp_current": 14,
        "hp_max": 14,
        "ac": 11,
        "features": [],  # no class features — only heritage traits
    }


def test_heritage_traits_in_prompt():
    """Valyrian heritage traits appear in Worker prompt — Опір вогню must be present."""
    profile = _valyrian_profile()
    prompt = build_normal_resolve_prompt(
        user_input="Хапаю розпечене вугілля голою рукою",
        profile=profile,
        current_scene="Базарна площа",
        npcs_in_scene=[],
        last_turn_summary="Гра почалась.",
        current_location="Пентос",
        npc_reputation_context=None,
        clocks_info=None,
    )
    # Heritage trait name must be present
    assert "Опір вогню" in prompt, "Expected 'Опір вогню' Valyrian trait in Worker prompt"
    # At least one of the fire-related keywords must appear in desc or GATE instruction
    assert "fire" in prompt.lower() or "вогн" in prompt.lower(), (
        "Expected fire-related keyword in prompt for Valyrian fire resistance trait"
    )
    # Heritage section header must appear
    assert "Heritage" in prompt, "Expected 'Heritage' section label in <class_features> block"


def test_heritage_traits_visible_even_without_class_features():
    """<class_features> block must appear when heritage has traits but class features list is empty."""
    profile = _valyrian_profile()
    profile["features"] = []  # explicitly empty
    prompt = build_normal_resolve_prompt(
        user_input="Дивлюся на вогонь",
        profile=profile,
        current_scene="Таверна",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Пентос",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "<class_features>" in prompt, (
        "Expected <class_features> block even when class features=[] if heritage traits exist"
    )
    assert "Valyrian Descent" in prompt, "Expected heritage name in features block"


def test_hp_damage_type_in_output_schema():
    """hp_damage_type must be documented in output_schema block."""
    prompt = build_normal_resolve_prompt(
        user_input="Атакую варту",
        profile=_valyrian_profile(),
        current_scene="Зал",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Пентос",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "hp_damage_type" in prompt, (
        "Expected 'hp_damage_type' key in output_schema and/or GATE 6 instruction"
    )


def test_hp_damage_type_in_json_template():
    """hp_damage_type must appear in the JSON output template at end of prompt."""
    prompt = build_normal_resolve_prompt(
        user_input="Стрибаю з вежі",
        profile=_valyrian_profile(),
        current_scene="Вежа",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Пентос",
        npc_reputation_context=None,
        clocks_info=None,
    )
    # The JSON template block contains the key with its default value
    assert '"hp_damage_type": "physical"' in prompt, (
        "Expected '\"hp_damage_type\": \"physical\"' in JSON output template"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 12. Reputation delta two-field schema (outcome-based selection bug fix)
# ─────────────────────────────────────────────────────────────────────────────

def test_reputation_delta_two_fields_in_schema(minimal_profile):
    """§5.3 contract: both reputation_delta_success and reputation_delta_failure
    must be present in the Worker prompt output_schema.

    Worker evaluates BOTH scenarios because it cannot know the roll outcome.
    Engine selects the correct field after the d20 roll.
    """
    prompt = build_normal_resolve_prompt(
        user_input="Намагаюся переконати Кхала Дрого",
        profile=minimal_profile,
        current_scene="Шатро",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Пентос",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "reputation_delta_success" in prompt, (
        "Expected 'reputation_delta_success' key in output_schema — Worker must evaluate success case."
    )
    assert "reputation_delta_failure" in prompt, (
        "Expected 'reputation_delta_failure' key in output_schema — Worker must evaluate failure case."
    )


def test_reputation_delta_old_single_field_absent(minimal_profile):
    """The old single 'reputation_delta' field must NOT appear as a top-level output key
    (only the two new split fields should be in the schema definition).

    Note: the substring 'reputation_delta' still appears as part of
    'reputation_delta_success' / 'reputation_delta_failure' — this is expected.
    We assert the bare exact key pattern is absent from the schema definition.
    """
    import re
    prompt = build_normal_resolve_prompt(
        user_input="Тестова дія",
        profile=minimal_profile,
        current_scene="Двір",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    # The pattern 'reputation_delta :' or '"reputation_delta":' as standalone key
    # (not followed by _success or _failure) must be absent from output_schema.
    # We check the <output_schema> block only to avoid false positives from
    # backward-compat comments elsewhere in the prompt.
    schema_start = prompt.find("<output_schema>")
    schema_end = prompt.find("</output_schema>")
    if schema_start != -1 and schema_end != -1:
        schema_block = prompt[schema_start:schema_end]
        # Match 'reputation_delta' not followed by _success or _failure
        bare_delta = re.search(r'reputation_delta(?!_success|_failure)', schema_block)
        assert bare_delta is None, (
            "Old single 'reputation_delta' key must not appear in <output_schema> — "
            "replaced by reputation_delta_success and reputation_delta_failure."
        )


def test_reputation_gate_instruction_present(minimal_profile):
    """GATE REP instruction (asymmetry examples) must be in <thinking_directives>."""
    prompt = build_normal_resolve_prompt(
        user_input="Тестова дія",
        profile=minimal_profile,
        current_scene="Двір",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "GATE REP" in prompt, (
        "Expected 'GATE REP' reputation instruction in <thinking_directives>."
    )
    assert "reputation_delta_success" in prompt, (
        "Expected 'reputation_delta_success' in GATE REP instruction."
    )
    assert "reputation_delta_failure" in prompt, (
        "Expected 'reputation_delta_failure' in GATE REP instruction."
    )


def test_reputation_delta_range_7_in_schema(minimal_profile):
    """Schema documents -7..+7 range (extended from -5..+5 in Round 2)."""
    prompt = build_normal_resolve_prompt(
        user_input="Намагаюся переконати Кхала Дрого",
        profile=minimal_profile,
        current_scene="Шатро",
        npcs_in_scene=[],
        last_turn_summary="Старт",
        current_location="Пентос",
        npc_reputation_context=None,
        clocks_info=None,
    )
    assert "-7" in prompt, "Expected '-7' lower bound in reputation_delta schema"
    assert "+7" in prompt, "Expected '+7' upper bound in reputation_delta schema"
    # Old narrow range must no longer be the stated boundary
    assert "-5..+5" not in prompt, (
        "Old '-5..+5' range must not appear in prompt — replaced by -7..+7"
    )
    # TRIVIAL → 0 rule must be present
    assert "trivial" in prompt.lower() or "тривіальна" in prompt.lower() or "TRIVIAL" in prompt, (
        "Expected TRIVIAL→0 rule in prompt (anti-farming guard)"
    )
    # Calibration anchors must be present
    assert "бенкет" in prompt, "Expected 'бенкет' calibration anchor in prompt"
    assert "посадив на трон" in prompt, "Expected 'посадив на трон' calibration anchor in prompt"
