# test/test_worker_gold.py
"""
Tests for the Gold input context line added to Worker NORMAL <player_state> block.

Change under test (core/prompts.py:build_normal_resolve_prompt):
  - Inline gold extraction:
      gold = int(profile.get("Особисте Золото", profile.get("gold", 0)) or 0)
  - New line in <player_state> f-string between "Active conditions:" and "Current location:":
      Gold: {gold} золотих

Invariants covered: §5.3 Worker NORMAL — INPUT context only; <output_schema> UNCHANGED.

Strategy: call build_normal_resolve_prompt directly with a minimal profile fixture.
No LLM, no Sheets, no async — pure string-output assertions.
"""

import pytest
from core.prompts import build_normal_resolve_prompt


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _minimal_profile(**overrides) -> dict:
    """Return a minimal profile that satisfies build_normal_resolve_prompt requirements.

    Fields required by the function body:
      - ability_scores       → ab_line construction
      - proficiency_bonus    → prof inline
      - skill_profs          → skill_profs line
      - conditions           → cond_line construction
      - hp_current / hp_max  → profile_str (JSON-dumped) — optional but avoids missing-key noise
      - Особисте Золото      → gold extraction (the field under test)
      - Інвентар             → profile_str
    """
    base = {
        "Ім'я": "Тест Герой",
        "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "proficiency_bonus": 2,
        "skill_profs": [],
        "conditions": [],
        "hp_current": 30,
        "hp_max": 30,
        "Особисте Золото": 0,
        "Інвентар": "Меч",
    }
    base.update(overrides)
    return base


_COMMON_KWARGS = dict(
    user_input="Піднятися на вежу",
    current_scene="Двір замку",
    npcs_in_scene=[],
    last_turn_summary="Герой щойно прибув.",
    current_location="Вінтерфелл",
    npc_reputation_context={},
    clocks_info={},
    nearby_canonical_locs=[],
    all_canonical_locs_grouped="",
)


def _build(profile: dict) -> str:
    """Convenience wrapper: call build_normal_resolve_prompt with the given profile."""
    return build_normal_resolve_prompt(profile=profile, **_COMMON_KWARGS)


# ---------------------------------------------------------------------------
# Group A: Gold line presence and value rendering
# ---------------------------------------------------------------------------

class TestGoldLinePresentInPlayerState:
    """A: The 'Gold: N золотих' line appears in the prompt with the correct value."""

    def test_gold_250_rendered_correctly(self):
        """A1: profile with 'Особисте Золото': 250 → prompt contains 'Gold: 250 золотих'."""
        profile = _minimal_profile(**{"Особисте Золото": 250})
        prompt = _build(profile)
        assert "Gold: 250 золотих" in prompt, (
            f"Expected 'Gold: 250 золотих' in prompt. Excerpt: {prompt[:600]}"
        )

    def test_default_zero_when_both_keys_absent(self):
        """A2: profile without 'Особисте Золото' and without 'gold' → 'Gold: 0 золотих'."""
        profile = _minimal_profile()
        # Remove the default gold key so neither key is present
        del profile["Особисте Золото"]
        prompt = _build(profile)
        assert "Gold: 0 золотих" in prompt, (
            f"Expected 'Gold: 0 золотих' for no-gold profile. Excerpt: {prompt[:600]}"
        )

    def test_fallback_to_gold_key(self):
        """A3: profile with 'gold': 75 only (no 'Особисте Золото') → 'Gold: 75 золотих'."""
        profile = _minimal_profile()
        del profile["Особисте Золото"]
        profile["gold"] = 75
        prompt = _build(profile)
        assert "Gold: 75 золотих" in prompt, (
            f"Expected 'Gold: 75 золотих' from fallback 'gold' key. Excerpt: {prompt[:600]}"
        )

    def test_string_coercion_sheets_style(self):
        """A4: 'Особисте Золото': '300' (Sheets stores strings) → 'Gold: 300 золотих'."""
        profile = _minimal_profile(**{"Особисте Золото": "300"})
        prompt = _build(profile)
        assert "Gold: 300 золотих" in prompt, (
            f"Expected 'Gold: 300 золотих' from string '300'. Excerpt: {prompt[:600]}"
        )

    def test_none_value_yields_zero(self):
        """A5: 'Особисте Золото': None → 'Gold: 0 золотих' (None or 0 branch of try/except)."""
        profile = _minimal_profile(**{"Особисте Золото": None})
        prompt = _build(profile)
        assert "Gold: 0 золотих" in prompt, (
            f"Expected 'Gold: 0 золотих' for None value. Excerpt: {prompt[:600]}"
        )

    def test_malformed_string_yields_zero(self):
        """A6: 'Особисте Золото': 'abc' → ValueError caught → 'Gold: 0 золотих'."""
        profile = _minimal_profile(**{"Особисте Золото": "abc"})
        prompt = _build(profile)
        assert "Gold: 0 золотих" in prompt, (
            f"Expected 'Gold: 0 золотих' for malformed 'abc'. Excerpt: {prompt[:600]}"
        )

    def test_big_gold(self):
        """A10: 'Особисте Золото': 1000000 → 'Gold: 1000000 золотих'."""
        profile = _minimal_profile(**{"Особисте Золото": 1000000})
        prompt = _build(profile)
        assert "Gold: 1000000 золотих" in prompt

    def test_negative_gold_debt(self):
        """A11: 'Особисте Золото': -50 → 'Gold: -50 золотих' (debt preserved via int())."""
        profile = _minimal_profile(**{"Особисте Золото": -50})
        prompt = _build(profile)
        assert "Gold: -50 золотих" in prompt, (
            f"Expected 'Gold: -50 золотих' for negative/debt gold. Excerpt: {prompt[:600]}"
        )

    def test_zero_explicit(self):
        """A_zero: 'Особисте Золото': 0 → 'Gold: 0 золотих'."""
        profile = _minimal_profile(**{"Особисте Золото": 0})
        prompt = _build(profile)
        assert "Gold: 0 золотих" in prompt


# ---------------------------------------------------------------------------
# Group B: Position within prompt structure
# ---------------------------------------------------------------------------

class TestGoldLinePosition:
    """B: The gold line appears in the correct position in the prompt."""

    def test_gold_after_active_conditions_before_current_location(self):
        """A7: Gold line must appear AFTER 'Active conditions:' and BEFORE 'Current location:'."""
        profile = _minimal_profile(**{"Особисте Золото": 100})
        prompt = _build(profile)

        idx_conditions = prompt.find("Active conditions:")
        idx_gold = prompt.find("Gold:")
        idx_location = prompt.find("Current location:")

        assert idx_conditions != -1, "Expected 'Active conditions:' in prompt."
        assert idx_gold != -1, "Expected 'Gold:' in prompt."
        assert idx_location != -1, "Expected 'Current location:' in prompt."

        assert idx_conditions < idx_gold, (
            f"'Gold:' (pos {idx_gold}) must come AFTER 'Active conditions:' (pos {idx_conditions})."
        )
        assert idx_gold < idx_location, (
            f"'Gold:' (pos {idx_gold}) must come BEFORE 'Current location:' (pos {idx_location})."
        )

    def test_gold_inside_player_state_tags(self):
        """A8: Gold line must appear between <player_state> and </player_state> tags."""
        profile = _minimal_profile(**{"Особисте Золото": 50})
        prompt = _build(profile)

        idx_open = prompt.find("<player_state>")
        idx_close = prompt.find("</player_state>")
        idx_gold = prompt.find("Gold:")

        assert idx_open != -1, "Expected '<player_state>' tag in prompt."
        assert idx_close != -1, "Expected '</player_state>' tag in prompt."
        assert idx_gold != -1, "Expected 'Gold:' line in prompt."

        assert idx_open < idx_gold < idx_close, (
            f"'Gold:' (pos {idx_gold}) must be between <player_state> (pos {idx_open}) "
            f"and </player_state> (pos {idx_close})."
        )


# ---------------------------------------------------------------------------
# Group C: output_schema integrity — no schema drift
# ---------------------------------------------------------------------------

class TestOutputSchemaNoDrift:
    """C: <output_schema> block must NOT introduce a top-level 'gold' balance key.
    Only 'gold_impact' (delta) is a Worker output; the new 'Gold:' line is input only."""

    def test_output_schema_contains_gold_impact_not_gold_balance(self):
        """A9: <output_schema> contains 'gold_impact' (delta output) but not a bare 'gold' key."""
        profile = _minimal_profile(**{"Особисте Золото": 100})
        prompt = _build(profile)

        # Locate the output_schema block
        schema_start = prompt.find("<output_schema>")
        schema_end = prompt.find("</output_schema>")
        assert schema_start != -1, "Expected '<output_schema>' tag in prompt."
        assert schema_end != -1, "Expected '</output_schema>' tag in prompt."

        schema_block = prompt[schema_start:schema_end]

        # gold_impact MUST be present (it is the delta output key)
        assert "gold_impact" in schema_block, (
            "Expected 'gold_impact' in <output_schema> (delta output key must remain)."
        )

        # 'gold' as a bare top-level key must NOT appear — would signal schema drift.
        # Robust approach: collect every 'gold*' token (case-sensitive — JSON keys are
        # lowercase; the human-readable "Gold:" label is uppercase and lives outside
        # <output_schema>), then assert the residual contains no bare 'gold'. Future-proof
        # against new gold_* keys (e.g. gold_cap) — they'd fail loudly instead of silently
        # passing a narrow lookahead chain.
        import re
        all_gold_tokens = re.findall(r"gold\w*", schema_block)
        residual = [t for t in all_gold_tokens if t == "gold"]
        assert len(residual) == 0, (
            f"<output_schema> must not contain bare 'gold' balance key. "
            f"Found occurrences: {residual}. All gold tokens in schema: {set(all_gold_tokens)}\n"
            f"Schema block excerpt: {schema_block[:400]}"
        )

    def test_output_schema_has_gold_reasoning_key(self):
        """C2: <output_schema> must contain 'gold_reasoning' (the GATE 4 CoT field)."""
        profile = _minimal_profile()
        prompt = _build(profile)

        schema_start = prompt.find("<output_schema>")
        schema_end = prompt.find("</output_schema>")
        schema_block = prompt[schema_start:schema_end]

        assert "gold_reasoning" in schema_block, (
            "Expected 'gold_reasoning' in <output_schema>. "
            "This CoT field is required per §5.3 Worker NORMAL output contract."
        )


# ---------------------------------------------------------------------------
# Group D: Parametrized gold extraction semantics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gold_value,expected_str", [
    (0,          "Gold: 0 золотих"),
    (1,          "Gold: 1 золотих"),
    (999,        "Gold: 999 золотих"),
    ("500",      "Gold: 500 золотих"),    # Sheets string coercion
    (None,       "Gold: 0 золотих"),      # None → or 0
    ("",         "Gold: 0 золотих"),      # empty string → or 0 (falsy)
    ("abc",      "Gold: 0 золотих"),      # ValueError → except branch
    (-10,        "Gold: -10 золотих"),    # debt allowed
    (1000000,    "Gold: 1000000 золотих"),
    ({"a": 1},   "Gold: 0 золотих"),      # corrupted profile dict → TypeError → except
    ([1, 2, 3],  "Gold: 0 золотих"),      # corrupted profile list → TypeError → except
])
def test_gold_extraction_parametrized(gold_value, expected_str):
    """D: Parametrized gold extraction covers all natural input classes."""
    profile = _minimal_profile(**{"Особисте Золото": gold_value})
    prompt = _build(profile)
    assert expected_str in prompt, (
        f"Gold value {gold_value!r} should produce '{expected_str}'. "
        f"Prompt excerpt: {prompt[prompt.find('Active conditions:'):prompt.find('Current location:')+30]}"
    )


@pytest.mark.parametrize("profile_keys,expected_gold_str", [
    # Only 'gold' fallback key present
    ({"gold": 42},            "Gold: 42 золотих"),
    # 'Особисте Золото' takes priority over 'gold'
    ({"Особисте Золото": 99, "gold": 1}, "Gold: 99 золотих"),
    # Neither key present
    ({},                      "Gold: 0 золотих"),
])
def test_gold_key_priority_parametrized(profile_keys, expected_gold_str):
    """D2: Key priority: 'Особисте Золото' > 'gold' > 0 default."""
    profile = _minimal_profile()
    # Remove the default 'Особисте Золото' key, then apply overrides
    del profile["Особисте Золото"]
    profile.update(profile_keys)
    prompt = _build(profile)
    assert expected_gold_str in prompt, (
        f"Keys {profile_keys!r} should produce '{expected_gold_str}'. "
        f"Prompt excerpt: {prompt[prompt.find('Active conditions:'):prompt.find('Current location:')+30]}"
    )
