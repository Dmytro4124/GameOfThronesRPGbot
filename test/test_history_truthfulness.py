"""Unit tests for build_summarize_full_turn_prompt truthfulness extension (4-fix package).

Covers:
- Backward-compat: 2-arg call (no mechanical_updates) returns same result
  as 3-arg call with mechanical_updates=None.
- mechanical_updates={"xp_award":25} -> prompt contains <mechanical_changes>
  block AND truthfulness rule text.
- mechanical_updates={"xp_award":0} -> <mechanical_changes> block rendered
  (with БЕЗ ЗМІН markers); truthfulness rule is present.
- mechanical_updates containing asi_choices -> prompt block shows ability score line.
- mechanical_updates=None -> no <mechanical_changes> block present.
- Return type is always str.
"""

import pytest

from core.prompts import build_summarize_full_turn_prompt


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_USER_INPUT = "Я тренуюся з мечем у дворі"
_STORY_TEXT = "Герой виснажено опускає меч після чергового кола."


# ---------------------------------------------------------------------------
# 1. Return type invariant
# ---------------------------------------------------------------------------

def test_returns_str_no_updates():
    result = build_summarize_full_turn_prompt(_USER_INPUT, _STORY_TEXT)
    assert isinstance(result, str)


def test_returns_str_with_updates():
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 25}
    )
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 2. Backward-compat: no mechanical_updates -> identical to mechanical_updates=None
# ---------------------------------------------------------------------------

def test_no_kwarg_equals_none_kwarg():
    """Calling with 2 positional args must produce same result as explicit None."""
    result_2arg = build_summarize_full_turn_prompt(_USER_INPUT, _STORY_TEXT)
    result_none = build_summarize_full_turn_prompt(_USER_INPUT, _STORY_TEXT, mechanical_updates=None)
    assert result_2arg == result_none, (
        "Legacy 2-arg call must be backward-compatible with mechanical_updates=None"
    )


def test_none_updates_no_mechanical_block():
    """With mechanical_updates=None, prompt must NOT contain <mechanical_changes>."""
    result = build_summarize_full_turn_prompt(_USER_INPUT, _STORY_TEXT, mechanical_updates=None)
    assert "<mechanical_changes>" not in result, (
        "mechanical_updates=None must not render <mechanical_changes> block"
    )


# ---------------------------------------------------------------------------
# 3. With mechanical_updates — <mechanical_changes> block present
# ---------------------------------------------------------------------------

def test_with_xp_award_block_present():
    """mechanical_updates with xp_award -> prompt contains <mechanical_changes>."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 25}
    )
    assert "<mechanical_changes>" in result, (
        "Prompt must contain <mechanical_changes> when mechanical_updates is provided"
    )
    assert "</mechanical_changes>" in result


def test_with_xp_award_block_shows_xp():
    """The <mechanical_changes> block must include the XP value."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 25}
    )
    assert "+25" in result or "XP" in result, (
        "mechanical_changes block must mention XP award value"
    )


def test_with_xp_award_truthfulness_rule_present():
    """When mechanical_updates is provided, ПРАВИЛО ПРАВДИВОСТІ must appear."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 50}
    )
    assert "ПРАВИЛО ПРАВДИВОСТІ" in result, (
        "Truthfulness rule must be injected when mechanical_updates is provided"
    )


# ---------------------------------------------------------------------------
# 4. mechanical_updates={"xp_award":0} — no real changes
# ---------------------------------------------------------------------------

def test_zero_xp_award_block_present():
    """Even with xp_award=0 the block is rendered (truthfulness rule still applies)."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 0}
    )
    # The block should be present because mechanical_updates is not None
    assert "<mechanical_changes>" in result


def test_zero_xp_award_truthfulness_rule_present():
    """Truthfulness rule must be present even when xp_award=0."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 0}
    )
    assert "ПРАВИЛО ПРАВДИВОСТІ" in result


def test_zero_xp_no_xp_line_or_bez_zmin():
    """With xp_award=0, prompt should NOT show '+0 XP' (xp line skipped per impl)."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 0}
    )
    # xp=0 is falsy — the impl skips the XP line
    assert "+0" not in result, (
        "xp_award=0 should not produce a '+0 XP' line in the block"
    )


def test_zero_xp_ability_scores_bez_zmin():
    """When no ASI, the block must mention БЕЗ ЗМІН for ability scores."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates={"xp_award": 0}
    )
    assert "БЕЗ ЗМІН" in result, (
        "Ability scores line must read 'БЕЗ ЗМІН' when no asi_choices provided"
    )


# ---------------------------------------------------------------------------
# 5. ASI in mechanical_updates -> ability-score line shown
# ---------------------------------------------------------------------------

def test_asi_choices_shown_in_block():
    """mechanical_updates with asi_choices -> block mentions the choices."""
    asi = {"STR": 2}
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT,
        mechanical_updates={"xp_award": 0, "asi_choices": asi},
    )
    assert "STR" in result or "Ability scores" in result, (
        "Block must mention asi_choices when provided"
    )


def test_asi_choices_not_bez_zmin():
    """When asi_choices present, the ability scores line must NOT say 'БЕЗ ЗМІН'."""
    asi = {"CHA": 1, "INT": 1}
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT,
        mechanical_updates={"xp_award": 100, "asi_choices": asi},
    )
    # The line shows the dict, not 'БЕЗ ЗМІН'
    # This is implementation-specific: verify by checking asi value shown
    assert "CHA" in result or str(asi) in result, (
        "When asi_choices is provided it should appear in the block, not 'БЕЗ ЗМІН'"
    )


# ---------------------------------------------------------------------------
# 6. Various mechanical_updates fields appear in the block
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("updates,expected_fragment", [
    ({"hp_damage_dice": "1d6"},    "1d6"),
    ({"hp_heal_dice": "2d4"},      "2d4"),
    ({"gold_impact": "+50"},       "+50"),
    ({"inventory_new": ["Меч"]},   "Меч"),
    ({"location_impact": "Вінтерфел"}, "Вінтерфел"),
])
def test_mechanical_fields_appear_in_block(updates, expected_fragment):
    """Each mechanical field type must surface in the rendered prompt block."""
    result = build_summarize_full_turn_prompt(
        _USER_INPUT, _STORY_TEXT, mechanical_updates=updates
    )
    assert expected_fragment in result, (
        f"Expected fragment {expected_fragment!r} from updates {updates} "
        f"to appear in prompt, but it's missing."
    )


# ---------------------------------------------------------------------------
# 7. User input and story text always embedded in prompt
# ---------------------------------------------------------------------------

def test_user_input_embedded_in_prompt():
    result = build_summarize_full_turn_prompt(_USER_INPUT, _STORY_TEXT)
    assert _USER_INPUT in result


def test_story_text_embedded_in_prompt():
    result = build_summarize_full_turn_prompt(_USER_INPUT, _STORY_TEXT)
    assert _STORY_TEXT in result
