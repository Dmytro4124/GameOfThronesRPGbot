"""test_combat_package3.py

Unit tests for Package 3 — HP-truth (combat_state authoritative).

Coverage:
  A. Prompt rendering — build_gm_logic_prompt with npc_hp_snapshot
     A1. COMBAT + non-empty snapshot → <npc_hp_snapshot> block rendered
     A2. Wound-tier labels at boundary HP values (parametrized)
     A3. NORMAL mode + snapshot → block NOT rendered
     A4. COMBAT + snapshot=None → block NOT rendered
     A5. COMBAT + snapshot={} (empty dict) → block NOT rendered
     A6. Defensive: hp_max=0 → no crash; renders HP X/?-like or empty tier
     A7. Backward-compat: missing npc_hp_snapshot kwarg → no crash

  B. Engine snapshot build logic (replicated from engine.py lines 1040-1055)
     B1. Normal shape — each entry has hp_current, hp_max, ac, conditions
     B2. conditions list is a COPY — mutation does not affect source
     B3. Missing 'ac' in source → defaults to 10
     B4. combat_state returns None → snapshot stays None

  C. Engine strip step (replicated from engine.py lines 1150-1175)
     C1. COMBAT strip removes hp_current and conditions from all entries
     C2. Status, Memory_Anchor, Name preserved after COMBAT strip
     C3. Unconditional guard: hp_current=None → popped (both modes)
     C4. Unconditional guard: hp_current="forty" (non-int) → popped
     C5. NORMAL mode: valid int hp_current and conditions NOT stripped
     C6. Mutation propagation: _gm_npc_updates items share identity with
         ai_data["npc_updates"] → mutations propagate

Mocking strategy:
  - core.prompts imported directly (pure string builder, no LLM or DB).
  - combat_state / dnd_combat imported directly; registries cleaned per test.
  - No real Google Sheets or Gemini calls made.
  - Each test is one assertion boundary.
"""

import copy
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub modules that trigger side-effects at import time.
# Must be done before importing anything that transitively pulls them in.
# ---------------------------------------------------------------------------

if "database.sheets" not in sys.modules:
    _fake_sheets = types.ModuleType("database.sheets")
    _fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = _fake_sheets

if "config" not in sys.modules:
    _fake_config = types.ModuleType("config")
    _fake_config.TAB_USERS = "Users_DB"
    _fake_config.TAB_HOUSES = "Houses_DB"
    _fake_config.TAB_CHARACTER = "Character"
    _fake_config.TAB_KNOWLEDGE = "KnowledgeBase"
    _fake_config.GEMINI_API_KEY = "stub-key-for-tests"
    _fake_config.PUPPET_USERS = set()
    _fake_config.EROTIC_USERS = set()
    sys.modules["config"] = _fake_config

if "google.genai" not in sys.modules:
    import google
    _fake_genai = types.ModuleType("google.genai")
    _fake_genai.Client = MagicMock(return_value=MagicMock())
    sys.modules["google.genai"] = _fake_genai
    google.genai = _fake_genai  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Now safe to import project modules
# ---------------------------------------------------------------------------

from core.prompts import build_gm_logic_prompt
import core.combat_state as _cs_module
from core.combat_state import (
    clear_combat_state,
    get_combat_state,
    set_combat_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_combat_registry():
    """Reset combat registries around every test."""
    _cs_module._combat_states.clear()
    _cs_module._state_locks.clear()
    yield
    _cs_module._combat_states.clear()
    _cs_module._state_locks.clear()


@pytest.fixture
def gm_kwargs():
    """Minimal valid kwargs for build_gm_logic_prompt."""
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
        curr_scene="Двір",
        valid_locs_str='"Вінтерфелл"',
        valid_regions_str='"Північ"',
        region_locs_str='"Вінтерфелл"',
        npc_context_text="Кхал Дрого: воїн.",
        tension_label="напружено",
        mechanics_verdict="SUCCESS",
        impact_narrative_hints="",
        history_text="Бій почався.",
        user_input="Атакую Дрого",
        action_slots=["соціальна", "бойова", "дослідницька", "рух"],
    )


# ---------------------------------------------------------------------------
# Section A: Prompt rendering
# ---------------------------------------------------------------------------

class TestPromptRenderingSnapshotBlock:
    """A. build_gm_logic_prompt with npc_hp_snapshot

    NOTE on detection strategy: the static prompt text references the string
    '<npc_hp_snapshot>' in several rule/antiexample lines regardless of whether
    a runtime data block was injected.  To distinguish "data block rendered" from
    "tag mentioned in rules", we check for the unique sentinel line that only
    appears inside the RUNTIME data block:
        'АВТОРИТЕТНИЙ ПОТОЧНИЙ СТАН NPC У БОЮ'
    Tests that verify the block is ABSENT check for this sentinel.
    Tests that verify the block IS PRESENT can use '<npc_hp_snapshot>' (which
    will be in the text in both cases) AND an NPC-specific data string.
    """

    # Sentinel that only appears in the RUNTIME data block (not in static rules)
    _DATA_SENTINEL = "АВТОРИТЕТНИЙ ПОТОЧНИЙ СТАН NPC У БОЮ"

    def test_a1_combat_with_snapshot_renders_npc_hp_snapshot_tag(self, gm_kwargs):
        """A1. COMBAT + non-empty snapshot → runtime data sentinel present."""
        snapshot = {"Дрого": {"hp_current": 75, "hp_max": 150, "ac": 18, "conditions": []}}
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=snapshot)
        assert self._DATA_SENTINEL in result

    def test_a1_combat_with_snapshot_renders_hp_fraction(self, gm_kwargs):
        """A1. COMBAT + non-empty snapshot → HP ratio '75/150' present."""
        snapshot = {"Дрого": {"hp_current": 75, "hp_max": 150, "ac": 18, "conditions": []}}
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=snapshot)
        assert "75/150" in result

    def test_a1_combat_with_snapshot_renders_ac(self, gm_kwargs):
        """A1. COMBAT + non-empty snapshot → 'AC 18' present."""
        snapshot = {"Дрого": {"hp_current": 75, "hp_max": 150, "ac": 18, "conditions": []}}
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=snapshot)
        assert "AC 18" in result

    def test_a1_combat_with_snapshot_renders_wound_tier_for_50pct(self, gm_kwargs):
        """A1. 75/150 = 50% → wound tier label 'поранений' present."""
        snapshot = {"Дрого": {"hp_current": 75, "hp_max": 150, "ac": 18, "conditions": []}}
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=snapshot)
        assert "поранений" in result

    @pytest.mark.parametrize("hp_current,hp_max,expected_label", [
        # hp == hp_max (100%) → no wound label
        (150, 150, None),
        # hp == 50% of hp_max → "(поранений)"
        (75, 150, "(поранений)"),
        # hp == 25% of hp_max → "(критично поранений)"
        (37, 148, "(критично поранений)"),
        # hp == 10% of hp_max → "(критична загроза)"
        (15, 150, "(критична загроза)"),
        # hp == 0 → "(критична загроза)"
        (0, 150, "(критична загроза)"),
    ])
    def test_a2_wound_tier_labels_at_boundaries(
        self, gm_kwargs, hp_current, hp_max, expected_label
    ):
        """A2. Wound-tier label rendered (or absent) at boundary HP values."""
        snapshot = {"Бандит": {"hp_current": hp_current, "hp_max": hp_max, "ac": 12, "conditions": []}}
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=snapshot)
        if expected_label is None:
            # Full HP → no wound label anywhere in the snapshot block
            assert "(поранений)" not in result
            assert "(критично поранений)" not in result
            assert "(критична загроза)" not in result
        else:
            assert expected_label in result

    def test_a3_normal_mode_with_snapshot_does_not_render_block(self, gm_kwargs):
        """A3. NORMAL mode + snapshot → runtime data block NOT rendered (sentinel absent)."""
        snapshot = {"Дрого": {"hp_current": 75, "hp_max": 150, "ac": 18, "conditions": []}}
        result = build_gm_logic_prompt(**gm_kwargs, mode="NORMAL", npc_hp_snapshot=snapshot)
        assert self._DATA_SENTINEL not in result

    def test_a4_combat_snapshot_none_does_not_render_block(self, gm_kwargs):
        """A4. COMBAT + snapshot=None → runtime data block NOT rendered (sentinel absent)."""
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=None)
        assert self._DATA_SENTINEL not in result

    def test_a5_combat_snapshot_empty_dict_does_not_render_block(self, gm_kwargs):
        """A5. COMBAT + snapshot={} (empty dict) → runtime data block NOT rendered (sentinel absent)."""
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot={})
        assert self._DATA_SENTINEL not in result

    def test_a6_defensive_hp_max_zero_does_not_crash(self, gm_kwargs):
        """A6. hp_max=0 → function does not raise; block is rendered (hp_max=0 is truthy snap)."""
        snapshot = {"Дрого": {"hp_current": 5, "hp_max": 0, "ac": 12, "conditions": []}}
        # Must not raise
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT", npc_hp_snapshot=snapshot)
        assert isinstance(result, str)
        # With hp_max=0 the wound tier function returns "" (guard: hp_max <= 0 → "")
        # The line should still appear (rendered) but with no tier label
        assert "5/0" in result  # HP raw value still rendered

    def test_a7_backward_compat_missing_kwarg_does_not_crash(self, gm_kwargs):
        """A7. Existing call without npc_hp_snapshot kwarg keeps working; no runtime data block."""
        # Should not raise TypeError
        result = build_gm_logic_prompt(**gm_kwargs, mode="COMBAT")
        assert isinstance(result, str)
        # No runtime data block when kwarg omitted (sentinel absent)
        assert self._DATA_SENTINEL not in result

    def test_a7_backward_compat_normal_mode_missing_kwarg(self, gm_kwargs):
        """A7. Existing NORMAL call without npc_hp_snapshot kwarg keeps working."""
        result = build_gm_logic_prompt(**gm_kwargs, mode="NORMAL")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Section B: Engine snapshot build logic
# ---------------------------------------------------------------------------

# TODO: replicates engine.py snapshot/strip logic — keep in sync with core/engine.py:1040-1055. Maintenance risk if engine drifts.
# Replicate the snapshot-build logic from engine.py (lines 1040–1055) as a
# standalone pure function so we can test it without running the full engine.

def _build_snapshot_from_combat_state(combat_state_obj) -> dict | None:
    """Pure replication of engine.py snapshot build (B1-B4)."""
    if combat_state_obj is None:
        return None
    snapshot = {}
    for npc_name, npc_data in combat_state_obj.npcs.items():
        snapshot[npc_name] = {
            "hp_current": npc_data.get("hp_current", 0),
            "hp_max":     npc_data.get("hp_max",     0),
            "ac":         npc_data.get("ac",         10),
            "conditions": list(npc_data.get("conditions", [])),
        }
    return snapshot


class TestEngineSnapshotBuild:
    """B. Engine snapshot build logic."""

    def _make_fake_combat_state(self, npcs: dict):
        """Create a minimal fake CombatState-like object with .npcs attribute."""
        obj = MagicMock()
        obj.npcs = npcs
        return obj

    def test_b1_snapshot_has_correct_shape(self):
        """B1. Each entry has hp_current, hp_max, ac, conditions."""
        source_npcs = {
            "Дрого": {"hp_current": 120, "hp_max": 150, "ac": 18, "conditions": ["bleeding"]},
            "Джорах": {"hp_current": 30, "hp_max": 60, "ac": 16, "conditions": []},
        }
        cs = self._make_fake_combat_state(source_npcs)
        result = _build_snapshot_from_combat_state(cs)

        for name in ("Дрого", "Джорах"):
            assert name in result
            entry = result[name]
            for key in ("hp_current", "hp_max", "ac", "conditions"):
                assert key in entry, f"Key '{key}' missing from snapshot entry '{name}'"

    def test_b1_snapshot_values_match_source(self):
        """B1. Values in snapshot match source combat_state data."""
        source_npcs = {
            "Дрого": {"hp_current": 120, "hp_max": 150, "ac": 18, "conditions": ["bleeding"]},
        }
        cs = self._make_fake_combat_state(source_npcs)
        result = _build_snapshot_from_combat_state(cs)

        assert result["Дрого"]["hp_current"] == 120
        assert result["Дрого"]["hp_max"] == 150
        assert result["Дрого"]["ac"] == 18
        assert result["Дрого"]["conditions"] == ["bleeding"]

    def test_b2_conditions_is_copy_not_reference(self):
        """B2. conditions list is a COPY — mutation does not affect source."""
        original_conditions = ["bleeding"]
        source_npcs = {
            "Дрого": {"hp_current": 120, "hp_max": 150, "ac": 18, "conditions": original_conditions},
        }
        cs = self._make_fake_combat_state(source_npcs)
        result = _build_snapshot_from_combat_state(cs)

        # Mutate the snapshot's conditions list
        result["Дрого"]["conditions"].append("poisoned")

        # The source must be unchanged
        assert original_conditions == ["bleeding"], (
            "Mutating snapshot's conditions should NOT affect the original list in combat_state"
        )

    def test_b3_missing_ac_defaults_to_10(self):
        """B3. NPC in combat_state missing 'ac' → snapshot defaults to 10."""
        source_npcs = {
            "Без_Брони": {"hp_current": 10, "hp_max": 20, "conditions": []},
            # 'ac' deliberately absent
        }
        cs = self._make_fake_combat_state(source_npcs)
        result = _build_snapshot_from_combat_state(cs)

        assert result["Без_Брони"]["ac"] == 10, (
            f"Expected ac=10 default, got {result['Без_Брони']['ac']}"
        )

    def test_b4_combat_state_none_returns_none(self):
        """B4. _get_combat_state_for_engine returns None → snapshot stays None."""
        result = _build_snapshot_from_combat_state(None)
        assert result is None


# ---------------------------------------------------------------------------
# Section C: Engine strip step logic
# ---------------------------------------------------------------------------

# TODO: replicates engine.py snapshot/strip logic — keep in sync with core/engine.py:1150-1175. Maintenance risk if engine drifts.
# Replicate the strip step from engine.py (lines 1150–1175) as a standalone
# pure function so we can test it without running the full engine.

def _apply_strip_logic(ai_data: dict, mode: str) -> list:
    """Pure replication of engine.py B2+B3 strip logic."""
    gm_npc_updates = (
        ai_data.get("npc_updates")
        if isinstance(ai_data.get("npc_updates"), list)
        else []
    )

    # B3 — Unconditional guard: strip hp_current if None or non-int (both modes)
    for u in gm_npc_updates:
        if not isinstance(u, dict):
            continue
        hp_val = u.get("hp_current")
        if hp_val is None or not isinstance(hp_val, int):
            u.pop("hp_current", None)

    # B2 — COMBAT: strip hp_current AND conditions
    if mode == "COMBAT":
        for u in gm_npc_updates:
            if not isinstance(u, dict):
                continue
            u.pop("hp_current", None)
            u.pop("conditions", None)

    return gm_npc_updates


class TestEngineStripStep:
    """C. Engine strip step."""

    def test_c1_combat_strip_removes_hp_current(self):
        """C1. COMBAT strip removes hp_current from all entries."""
        ai_data = {
            "npc_updates": [
                {"Name": "X", "hp_current": 42, "conditions": ["wounded"], "Status": "Active"},
                {"Name": "Y", "hp_current": 7},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="COMBAT")
        for entry in result:
            assert "hp_current" not in entry, (
                f"hp_current should be stripped in COMBAT. Entry: {entry}"
            )

    def test_c1_combat_strip_removes_conditions(self):
        """C1. COMBAT strip removes conditions from all entries."""
        ai_data = {
            "npc_updates": [
                {"Name": "X", "hp_current": 42, "conditions": ["wounded"], "Status": "Active"},
                {"Name": "Y", "hp_current": 7, "conditions": []},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="COMBAT")
        for entry in result:
            assert "conditions" not in entry, (
                f"conditions should be stripped in COMBAT. Entry: {entry}"
            )

    def test_c2_status_preserved_after_combat_strip(self):
        """C2. Status preserved after COMBAT strip."""
        ai_data = {
            "npc_updates": [
                {"Name": "X", "hp_current": 42, "conditions": ["wounded"],
                 "Status": "Active", "Memory_Anchor": "foo"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="COMBAT")
        assert result[0]["Status"] == "Active"

    def test_c2_memory_anchor_preserved_after_combat_strip(self):
        """C2. Memory_Anchor preserved after COMBAT strip."""
        ai_data = {
            "npc_updates": [
                {"Name": "X", "hp_current": 42, "conditions": ["wounded"],
                 "Status": "Active", "Memory_Anchor": "foo"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="COMBAT")
        assert result[0]["Memory_Anchor"] == "foo"

    def test_c2_name_preserved_after_combat_strip(self):
        """C2. Name preserved after COMBAT strip."""
        ai_data = {
            "npc_updates": [
                {"Name": "X", "hp_current": 42, "conditions": ["wounded"],
                 "Status": "Active", "Memory_Anchor": "foo"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="COMBAT")
        assert result[0]["Name"] == "X"

    def test_c3_unconditional_guard_hp_current_none_popped(self):
        """C3. hp_current=None → popped by unconditional guard (NORMAL mode)."""
        ai_data = {
            "npc_updates": [
                {"Name": "A", "hp_current": None, "Status": "Active"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="NORMAL")
        assert "hp_current" not in result[0], (
            "hp_current=None must be stripped even in NORMAL mode"
        )

    def test_c4_unconditional_guard_hp_current_string_popped(self):
        """C4. hp_current='forty' (non-int) → popped by unconditional guard."""
        ai_data = {
            "npc_updates": [
                {"Name": "B", "hp_current": "forty", "Status": "Active"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="NORMAL")
        assert "hp_current" not in result[0], (
            "hp_current='forty' (non-int string) must be stripped by unconditional guard"
        )

    def test_c4_unconditional_guard_hp_current_float_popped(self):
        """C4. hp_current=42.5 (float, not int) → popped by unconditional guard."""
        ai_data = {
            "npc_updates": [
                {"Name": "C", "hp_current": 42.5, "Status": "Active"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="NORMAL")
        assert "hp_current" not in result[0], (
            "hp_current=42.5 (float) must be stripped — only int is valid"
        )

    def test_c5_normal_mode_preserves_valid_int_hp_current(self):
        """C5. NORMAL mode: valid int hp_current is NOT stripped."""
        ai_data = {
            "npc_updates": [
                {"Name": "D", "hp_current": 42, "conditions": ["bleeding"], "Status": "Active"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="NORMAL")
        assert result[0].get("hp_current") == 42, (
            "Valid int hp_current must be preserved in NORMAL mode"
        )

    def test_c5_normal_mode_preserves_conditions(self):
        """C5. NORMAL mode: conditions list is NOT stripped."""
        ai_data = {
            "npc_updates": [
                {"Name": "D", "hp_current": 42, "conditions": ["bleeding"], "Status": "Active"},
            ]
        }
        result = _apply_strip_logic(ai_data, mode="NORMAL")
        assert "conditions" in result[0], (
            "conditions must be preserved in NORMAL mode"
        )
        assert result[0]["conditions"] == ["bleeding"]

    def test_c6_mutation_propagates_to_ai_data(self):
        """C6. Mutations to _gm_npc_updates entries propagate to ai_data['npc_updates'].

        engine.py relies on ai_data['npc_updates'] and _gm_npc_updates pointing
        to the same list objects. Verify that in-place dict mutation on list items
        from ai_data.get('npc_updates') propagates back through the same reference.
        """
        entry = {"Name": "X", "hp_current": 42, "conditions": ["wounded"], "Status": "Active"}
        ai_data = {"npc_updates": [entry]}

        # Get the same reference the engine gets
        gm_npc_updates = ai_data.get("npc_updates")

        # Apply in-place mutation (as engine does with .pop())
        for u in gm_npc_updates:
            u.pop("hp_current", None)
            u.pop("conditions", None)

        # Confirm the mutation is visible through ai_data
        assert "hp_current" not in ai_data["npc_updates"][0], (
            "In-place mutation of list items from ai_data['npc_updates'] must "
            "propagate to ai_data — they must share the same dict object identity"
        )
        assert "conditions" not in ai_data["npc_updates"][0]

    def test_c6_list_itself_is_same_object(self):
        """C6. ai_data['npc_updates'] and gm_npc_updates are the same list object."""
        ai_data = {"npc_updates": [{"Name": "X", "hp_current": 5}]}
        gm_npc_updates = ai_data.get("npc_updates")
        assert gm_npc_updates is ai_data["npc_updates"], (
            "ai_data.get('npc_updates') must return the same list object, not a copy"
        )
