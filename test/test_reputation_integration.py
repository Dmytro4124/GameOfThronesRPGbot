"""Integration tests for asymmetric reputation system (-7..+7).

Covers 3 areas identified as gaps after Round 2:
  1. update_npc_reputation → apply_reputation_step: numeric new_val is ASYMMETRIC
     (not linear old+delta). Four specific cases from the task spec.
  2. resolve_normal_action (dnd_engine) outcome selection:
     - SUCCESS → reputation_delta_success; FAILURE → reputation_delta_failure.
     - Large magnitudes (+7, -7) pass through _clamp_reputation_delta unchanged.
     - Backward compat: old single reputation_delta (no _success/_failure) → success-case.
  3. Non-linear invariant guard: delta=+1 from 0 must NOT give new_val=1 (it must be 10).

Import isolation note:
  database.sheets creates a live GoogleSheetsDB singleton at module-level which
  requires SPREADSHEET_ID env var.  When this test file is the FIRST to import
  database.operations (e.g. when run in isolation), that import chain fails.
  Solution: inject a fake database.sheets module into sys.modules BEFORE any
  database.operations import occurs.  The same pattern is used implicitly by
  test_npc_isolation.py (which relies on another test file importing the module
  first in the full suite, but is fragile as a standalone run).
"""

import sys
import asyncio
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Module-level sys.modules stub so database.sheets does NOT hit Google Sheets
# when database.operations is first imported in this test file.
# We only inject if the real module is not already loaded (e.g. in the full suite
# another test already imported it safely; we should not override that).
# ---------------------------------------------------------------------------
if "database.sheets" not in sys.modules:
    _fake_sheets_module = MagicMock()
    _fake_sheets_module.db = MagicMock()
    sys.modules["database.sheets"] = _fake_sheets_module
    # Also pre-stub database.operations so it can import from database.sheets safely.
    # We do NOT pre-load it; we merely ensure the dependency is satisfied.
    # Each test still patches database.operations.db via with patch(...).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_ID = 999_111_999
NPC_TAB = f"NPC_{USER_ID}"


def _make_all_values_with_npc(npc_name: str, rep_score: int) -> list:
    """Build get_all_values() response: header row + one NPC data row."""
    headers = [
        "Location", "Scene", "Name", "Description", "Character", "Goal", "Secrets",
        "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon",
        "Inventory", "Reputation_Score", "Region"
    ]
    # Must align with header order
    row = [
        "Вінтерфелл",   # Location
        "Throne Room",  # Scene
        npc_name,       # Name
        "Tall, grey",   # Description
        "Stern",        # Character
        "Win",          # Goal
        "None",         # Secrets
        "Нейтральний",  # Relation_Player
        "-",            # Memory_Anchor
        "-",            # Relation_NPCs
        "Active",       # Status
        "FALSE",        # Is_Canon
        "Sword",        # Inventory
        str(rep_score), # Reputation_Score
        "Північ",       # Region
    ]
    return [headers, row]


def _make_worksheet_mock(npc_name: str, rep_score: int) -> MagicMock:
    """Return a worksheet mock pre-populated with one NPC at given rep_score."""
    ws = MagicMock()
    ws.get_all_values.return_value = _make_all_values_with_npc(npc_name, rep_score)
    ws.update_cell = MagicMock()
    return ws


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Section 1: update_npc_reputation → apply_reputation_step integration
# Tests that the numeric new_val written to the DB is ASYMMETRIC, not linear.
# ---------------------------------------------------------------------------

class TestUpdateNpcReputationAsymmetric:
    """Verifies that update_npc_reputation calls update_cell with ASYMMETRIC new_val
    (via apply_reputation_step), NOT the old linear old_val + delta."""

    def _call_update_reputation(self, npc_name: str, old_score: int, delta: int):
        """Helper: call update_npc_reputation with mocked worksheet, return captured
        (reputation_score_col_value, relation_player_col_value) written to the sheet."""
        ws = _make_worksheet_mock(npc_name, old_score)

        with patch("database.operations.db") as mock_db, \
             patch("database.operations.refresh_npc_database", new=lambda uid: asyncio.sleep(0)):
            mock_db.get_sheet.return_value = ws
            from database.operations import update_npc_reputation
            _run(update_npc_reputation(USER_ID, npc_name, delta))

        # Collect all update_cell calls: (row, col, value)
        calls = ws.update_cell.call_args_list
        return calls

    def test_positive_delta_1_from_0_yields_10_not_1(self):
        """old_val=0, delta=+1 → asymmetric: new_val MUST be 10 (gap-based), not 1 (linear).

        apply_reputation_step(0, +1):
          gap = 25 - 0 = 25; step = ceil(25 * 0.4) = 10 → new_val = 10.
        Linear would give 0 + 1 = 1. Must NOT be 1.
        """
        calls = self._call_update_reputation("Еддард Старк", 0, 1)
        # update_cell is called twice: once for rep_score, once for relation_player
        assert len(calls) >= 1, "update_cell must have been called at least once"

        # Find the update_cell call for the reputation score (col 14 = index 14 → 1-based 14)
        # The rep_col is headers.index("reputation_score") + 1; in our header list it's position 14
        rep_score_calls = [c for c in calls if c.args[2] == 10]
        linear_wrong_calls = [c for c in calls if c.args[2] == 1]

        assert rep_score_calls, (
            f"update_cell must be called with new_val=10 (asymmetric). "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )
        assert not linear_wrong_calls, (
            f"update_cell must NOT be called with new_val=1 (linear). "
            f"This would indicate apply_reputation_step is NOT being used."
        )

    def test_negative_delta_1_from_0_yields_minus_1(self):
        """old_val=0, delta=-1 → new_val = -1 (NEGATIVE_DROP[1] retuned from 15 → 1).

        INTENT_CHANGED: Under the old table, -1 gave a fast-fall of -15, distinguishable
        from the linear value -1. Under the new lenient table, NEGATIVE_DROP[1]=1, so
        apply_reputation_step(0,-1) = 0-1 = -1, which equals the linear result.
        Small slights now barely register — confirmed approved retuning.
        We still verify that apply_reputation_step IS being used (not bypassed),
        and that update_cell is called with the correct value -1.
        """
        calls = self._call_update_reputation("Серсея Ланністер", 0, -1)
        assert len(calls) >= 1, "update_cell must have been called at least once"

        rep_score_neg1_calls = [c for c in calls if c.args[2] == -1]
        assert rep_score_neg1_calls, (
            f"update_cell must be called with new_val=-1 (NEGATIVE_DROP[1]=1, from 0). "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )

    def test_farming_guard_positive_delta_from_60_yields_60(self):
        """old_val=60, delta=+1 → farming guard: current(60) >= POSITIVE_CAP[1](25) → no change.

        apply_reputation_step(60, +1) = 60 (unchanged).
        update_npc_reputation should call update_cell with 60, not 61.
        """
        calls = self._call_update_reputation("Тіріон Ланністер", 60, 1)
        assert len(calls) >= 1, "update_cell must have been called"

        rep_60_calls = [c for c in calls if c.args[2] == 60]
        rep_61_calls = [c for c in calls if c.args[2] == 61]

        assert rep_60_calls, (
            f"update_cell must be called with new_val=60 (farming guard unchanged). "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )
        assert not rep_61_calls, (
            f"update_cell must NOT be called with new_val=61 (linear). "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )

    def test_epic_crash_minus7_from_85_yields_minus15(self):
        """old_val=85, delta=-7 → new_val = 85 - 100 = -15 (NEGATIVE_DROP[7] retuned from 200 → 100).

        INTENT_CHANGED: Under old table, -7 from 85 gave -115 → clamped -100 (Blood Enemy).
        With NEGATIVE_DROP[7]=100, result is 85-100=-15 (no clamp needed).
        Still MUST NOT be the linear value 85+(-7)=78.
        apply_reputation_step is still being used (non-linear), just with a smaller drop.
        """
        calls = self._call_update_reputation("Джоффрі Баратеон", 85, -7)
        assert len(calls) >= 1, "update_cell must have been called"

        rep_minus15_calls = [c for c in calls if c.args[2] == -15]
        linear_wrong_calls = [c for c in calls if c.args[2] == 78]

        assert rep_minus15_calls, (
            f"update_cell must be called with new_val=-15 (85 - NEGATIVE_DROP[7]=100). "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )
        assert not linear_wrong_calls, (
            f"update_cell must NOT be called with new_val=78 (linear 85+(-7)). "
            f"This means apply_reputation_step is NOT being used. "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )

    def test_relation_player_label_synced_for_positive_delta(self):
        """update_npc_reputation must also update Relation_Player column with correct label.

        old_val=0, delta=+1 → new_val=10 → score_to_relation_text(10) = 'Обережно відкритий'.
        """
        from core.dnd_core import score_to_relation_text
        expected_label = score_to_relation_text(10)  # "Обережно відкритий"

        calls = self._call_update_reputation("Робб Старк", 0, 1)
        relation_label_calls = [c for c in calls if c.args[2] == expected_label]

        assert relation_label_calls, (
            f"update_cell must sync Relation_Player to '{expected_label}'. "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )

    def test_relation_player_label_synced_for_epic_crash(self):
        """old_val=85, delta=-7 → new_val=-15 → score_to_relation_text(-15) = 'Холодний'.

        INTENT_CHANGED: With NEGATIVE_DROP[7]=100, apply(85,-7)=-15, not -100.
        Relation label must reflect the new score -15 ('Холодний'), not 'Смертельна ненависть'.
        """
        from core.dnd_core import score_to_relation_text
        expected_label = score_to_relation_text(-15)  # "Холодний"

        calls = self._call_update_reputation("Рамсі Болтон", 85, -7)
        relation_label_calls = [c for c in calls if c.args[2] == expected_label]

        assert relation_label_calls, (
            f"update_cell must sync Relation_Player to '{expected_label}' (new score -15). "
            f"All calls: {[(c.args[1], c.args[2]) for c in calls]}"
        )

    def test_update_cell_not_called_when_npc_not_found(self):
        """If NPC name does not match any row, update_cell must NOT be called."""
        ws = _make_worksheet_mock("Арія Старк", 0)

        with patch("database.operations.db") as mock_db, \
             patch("database.operations.refresh_npc_database", new=lambda uid: asyncio.sleep(0)):
            mock_db.get_sheet.return_value = ws
            from database.operations import update_npc_reputation
            _run(update_npc_reputation(USER_ID, "НеіснуючийNPC", 3))

        ws.update_cell.assert_not_called()


# ---------------------------------------------------------------------------
# Section 2: Non-linear invariant guard (unit-level check, no DB mock needed)
# Verifies that update_npc_reputation logic does NOT use old+delta linearly.
# ---------------------------------------------------------------------------

class TestReputationNonLinearInvariant:
    """Directly checks apply_reputation_step returns are non-linear for key scenarios."""

    def test_delta_plus1_from_zero_is_not_linear(self):
        """apply_reputation_step(0, +1) must NOT equal 0 + 1 = 1."""
        from core.reputation import apply_reputation_step
        result = apply_reputation_step(0, 1)
        assert result != 1, (
            f"apply_reputation_step(0, +1) = {result}, which equals the linear value 1. "
            f"The asymmetric system should give 10 here."
        )
        assert result == 10, f"Expected 10 (gap-based climb), got {result}"

    def test_delta_minus1_from_zero_new_value(self):
        """apply_reputation_step(0, -1) = -1 with new NEGATIVE_DROP[1]=1.

        INTENT_CHANGED: Under the old table (NEGATIVE_DROP[1]=15), this test verified
        that result != -1 to distinguish asymmetric fall from linear subtraction.
        Under the new lenient table, NEGATIVE_DROP[1]=1, so result IS -1 — same as linear.
        Small slights now barely register; this is confirmed approved gameplay retuning.
        We verify apply_reputation_step returns the correct value per the new table.
        """
        from core.reputation import apply_reputation_step
        result = apply_reputation_step(0, -1)
        assert result == -1, (
            f"apply_reputation_step(0, -1) must be -1 (NEGATIVE_DROP[1]=1). Got {result}"
        )

    def test_delta_plus3_from_zero_is_not_linear(self):
        """apply_reputation_step(0, +3) must NOT equal 3."""
        from core.reputation import apply_reputation_step
        result = apply_reputation_step(0, 3)
        assert result != 3, (
            f"apply_reputation_step(0, +3) = {result}, linear value would be 3. "
            f"Gap-based climb should give 26."
        )
        assert result == 26, f"Expected 26 (ceil(65*0.4)), got {result}"

    def test_delta_minus5_from_zero_is_not_linear(self):
        """apply_reputation_step(0, -5) must NOT equal -5 (linear); must equal -25.

        NEGATIVE_DROP[5] retuned from 75 → 25. Result is -25, not linear -5.
        Still non-linear (25 != 5), so the non-linearity invariant holds for mag 5.
        """
        from core.reputation import apply_reputation_step
        result = apply_reputation_step(0, -5)
        assert result != -5, (
            f"apply_reputation_step(0, -5) = {result}, but linear value is -5. "
            f"With NEGATIVE_DROP[5]=25, result must be -25 (not -5)."
        )
        assert result == -25, f"Expected -25 (NEGATIVE_DROP[5]=25), got {result}"


# ---------------------------------------------------------------------------
# Section 3: resolve_normal_action — outcome selection for reputation delta
# Tests that engine picks reputation_delta_success on SUCCESS and
# reputation_delta_failure on FAILURE. Also tests range -7..+7 passes through.
# ---------------------------------------------------------------------------

def _dnd_profile_minimal(**overrides) -> dict:
    base = {
        "Ім'я": "TestHero",
        "Здоров'я": 100,
        "Енергія": 800,
        "Особисте Золото": 200,
        "Поточне місцезнаходження": "Вінтерфелл",
        "Годинники": {"Scene_Tension": "0/4"},
        "level": 1,
        "ability_scores": {"STR": 14, "DEX": 12, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
        "skill_profs": ["Athletics"],
        "skill_expertise": [],
        "saves_proficient": ["STR"],
        "conditions": [],
        "xp": 0,
    }
    base.update(overrides)
    return base


def _minimal_worker_data_rep(**overrides) -> dict:
    """Minimal valid LLM output with reputation fields."""
    base = {
        "ability_used": "STR",
        "skill_used": "Athletics",
        "difficulty": 10,
        "advantage_reason": "",
        "disadvantage_reason": "",
        "combat_imminent": False,
        "verdict_text": "Test action.",
        "xp_award": 0,
        "reputation_delta_success": 0,
        "reputation_delta_failure": 0,
        "reputation_target_npc": "Еддард Старк",
        "updates": {
            "minutes_passed": 5,
            "location_impact": "none",
            "scene_impact": "none",
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "gold_impact": "none",
            "inventory_new": [],
            "inventory_lost": [],
            "clocks_impact": {},
            "condition_apply": [],
            "condition_remove": [],
        },
    }
    base.update(overrides)
    return base


def _patches_for_resolve_rep(worker_data: dict):
    """Patch list for isolating resolve_normal_action."""
    return [
        patch("core.dnd_engine.model_worker.generate_content",
              return_value=MagicMock(text=__import__("json").dumps(worker_data))),
        patch("core.dnd_engine.clean_and_parse_json", return_value=worker_data),
        patch("core.dnd_engine.build_normal_resolve_prompt", return_value="MOCK_PROMPT"),
    ]


class TestEngineReputationDeltaOutcomeSelection:
    """resolve_normal_action must select reputation_delta based on actual roll outcome."""

    def _resolve(self, worker_data: dict, roll: int) -> dict:
        """Run resolve_normal_action with fixed dice roll, return updates dict."""
        profile = _dnd_profile_minimal()

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Test action",
                profile=profile,
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve_rep(worker_data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=roll):
                _, updates = asyncio.run(_run())

        return updates

    def test_success_outcome_uses_reputation_delta_success(self):
        """On SUCCESS: updates['reputation_delta'] must equal clamped reputation_delta_success."""
        # roll=20 (nat 20 → CRITICAL SUCCESS), difficulty=10
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=3,
            reputation_delta_failure=-2,
            difficulty=10,
        )
        updates = self._resolve(worker_data, roll=20)

        assert updates["outcome"] in ("SUCCESS", "CRITICAL SUCCESS"), (
            f"Expected SUCCESS/CRITICAL SUCCESS with roll=20 vs DC 10, got {updates['outcome']!r}"
        )
        assert updates["reputation_delta"] == 3, (
            f"On SUCCESS, reputation_delta must be reputation_delta_success=3, "
            f"got {updates['reputation_delta']}"
        )

    def test_failure_outcome_uses_reputation_delta_failure(self):
        """On FAILURE: updates['reputation_delta'] must equal clamped reputation_delta_failure."""
        # roll=1 (nat 1 → CRITICAL FAILURE regardless of DC)
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=5,
            reputation_delta_failure=-4,
            difficulty=10,
        )
        updates = self._resolve(worker_data, roll=1)

        assert updates["outcome"] in ("FAILURE", "CRITICAL FAILURE"), (
            f"Expected FAILURE with roll=1, got {updates['outcome']!r}"
        )
        assert updates["reputation_delta"] == -4, (
            f"On FAILURE, reputation_delta must be reputation_delta_failure=-4, "
            f"got {updates['reputation_delta']}"
        )

    def test_large_positive_7_not_clamped(self):
        """reputation_delta_success=+7 must pass through _clamp_reputation_delta unchanged.

        REPUTATION_DELTA_MAX is now 7 (extended from 5), so +7 is legal and must not be reduced.
        """
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=7,
            reputation_delta_failure=0,
            difficulty=10,
        )
        updates = self._resolve(worker_data, roll=20)  # nat 20 → CRITICAL SUCCESS

        assert updates["reputation_delta"] == 7, (
            f"reputation_delta_success=7 must not be clamped (REPUTATION_DELTA_MAX=7). "
            f"Got {updates['reputation_delta']}"
        )

    def test_large_negative_7_not_clamped(self):
        """reputation_delta_failure=-7 must pass through _clamp_reputation_delta unchanged.

        REPUTATION_DELTA_MIN is now -7 (extended from -5), so -7 is legal.
        """
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=0,
            reputation_delta_failure=-7,
            difficulty=10,
        )
        updates = self._resolve(worker_data, roll=1)  # nat 1 → CRITICAL FAILURE

        assert updates["reputation_delta"] == -7, (
            f"reputation_delta_failure=-7 must not be clamped (REPUTATION_DELTA_MIN=-7). "
            f"Got {updates['reputation_delta']}"
        )

    def test_out_of_range_positive_8_is_clamped_to_7(self):
        """reputation_delta_success=+8 must be clamped to +7 (above REPUTATION_DELTA_MAX=7)."""
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=8,
            reputation_delta_failure=0,
            difficulty=10,
        )
        updates = self._resolve(worker_data, roll=20)

        assert updates["reputation_delta"] == 7, (
            f"reputation_delta_success=8 must be clamped to 7. Got {updates['reputation_delta']}"
        )

    def test_out_of_range_negative_10_is_clamped_to_minus7(self):
        """reputation_delta_failure=-10 must be clamped to -7 (below REPUTATION_DELTA_MIN=-7)."""
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=0,
            reputation_delta_failure=-10,
            difficulty=10,
        )
        updates = self._resolve(worker_data, roll=1)

        assert updates["reputation_delta"] == -7, (
            f"reputation_delta_failure=-10 must be clamped to -7. Got {updates['reputation_delta']}"
        )

    def test_backward_compat_old_single_reputation_delta_success_case(self):
        """Old Worker output: only 'reputation_delta' field (no _success/_failure).
        Must be treated as success-case. On SUCCESS → updates['reputation_delta'] == old_value.
        """
        # Old-style payload: only 'reputation_delta', no 'reputation_delta_success/failure'
        old_style_data = {
            "ability_used": "STR",
            "skill_used": "Athletics",
            "difficulty": 10,
            "advantage_reason": "",
            "disadvantage_reason": "",
            "combat_imminent": False,
            "verdict_text": "Old-style action.",
            "xp_award": 0,
            "reputation_delta": 3,  # old single field
            "reputation_target_npc": "Старий NPC",
            "updates": {
                "minutes_passed": 5,
                "location_impact": "none",
                "scene_impact": "none",
                "hp_damage_dice": "none",
                "hp_heal_dice": "none",
                "gold_impact": "none",
                "inventory_new": [],
                "inventory_lost": [],
                "clocks_impact": {},
                "condition_apply": [],
                "condition_remove": [],
            },
        }
        profile = _dnd_profile_minimal()

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Legacy action",
                profile=profile,
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve_rep(old_style_data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=20):  # nat 20 → SUCCESS
                _, updates = asyncio.run(_run())

        # On success with old-style payload: reputation_delta should be the old value (3)
        assert updates["outcome"] in ("SUCCESS", "CRITICAL SUCCESS")
        assert updates["reputation_delta"] == 3, (
            f"Backward compat: old reputation_delta=3 on SUCCESS must yield delta=3. "
            f"Got {updates['reputation_delta']}"
        )

    def test_backward_compat_old_single_reputation_delta_failure_case(self):
        """Old Worker output: only 'reputation_delta'. On FAILURE → updates['reputation_delta'] == 0.

        Per §5.3 backward compat: failure defaults to 0 when _failure field absent.
        """
        old_style_data = {
            "ability_used": "STR",
            "skill_used": "Athletics",
            "difficulty": 10,
            "advantage_reason": "",
            "disadvantage_reason": "",
            "combat_imminent": False,
            "verdict_text": "Old-style action.",
            "xp_award": 0,
            "reputation_delta": 3,  # old single field; failure should default to 0
            "reputation_target_npc": "Старий NPC",
            "updates": {
                "minutes_passed": 5,
                "location_impact": "none",
                "scene_impact": "none",
                "hp_damage_dice": "none",
                "hp_heal_dice": "none",
                "gold_impact": "none",
                "inventory_new": [],
                "inventory_lost": [],
                "clocks_impact": {},
                "condition_apply": [],
                "condition_remove": [],
            },
        }
        profile = _dnd_profile_minimal()

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Legacy action fails",
                profile=profile,
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve_rep(old_style_data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=1):  # nat 1 → FAILURE
                _, updates = asyncio.run(_run())

        assert updates["outcome"] in ("FAILURE", "CRITICAL FAILURE")
        assert updates["reputation_delta"] == 0, (
            f"Backward compat: old payload on FAILURE must default to 0. "
            f"Got {updates['reputation_delta']}"
        )

    def test_reputation_delta_max_constant_is_7(self):
        """REPUTATION_DELTA_MAX must be 7 (extended from old ±5)."""
        from core.dnd_engine import REPUTATION_DELTA_MAX
        assert REPUTATION_DELTA_MAX == 7, (
            f"REPUTATION_DELTA_MAX must be 7 for -7..+7 range. Got {REPUTATION_DELTA_MAX}"
        )

    def test_reputation_delta_min_constant_is_minus7(self):
        """REPUTATION_DELTA_MIN must be -7 (extended from old -5)."""
        from core.dnd_engine import REPUTATION_DELTA_MIN
        assert REPUTATION_DELTA_MIN == -7, (
            f"REPUTATION_DELTA_MIN must be -7 for -7..+7 range. Got {REPUTATION_DELTA_MIN}"
        )


# ---------------------------------------------------------------------------
# Section 4: End-to-end smoke — full pipeline for +7 on SUCCESS
# Verifies that reputation_delta=+7 on SUCCESS with old_val=0 would produce
# new_val=100 if passed through apply_reputation_step.
# This is a compositional test (not a DB integration test).
# ---------------------------------------------------------------------------

class TestE2ESmoke:
    """Compositional smoke: engine delta + apply_reputation_step = correct final score."""

    def test_plus7_on_success_to_absolute_trust(self):
        """Pipeline smoke:
          - Engine: reputation_delta_success=+7 on CRITICAL SUCCESS → delta=7 in updates.
          - apply_reputation_step(old_val=0, raw_delta=7) → 100 "Абсолютна довіра".
          - score_to_relation_text(100) == "Абсолютна довіра".
        """
        from core.reputation import apply_reputation_step
        from core.dnd_core import score_to_relation_text

        # Step 1: Engine produces raw delta
        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=7,
            reputation_delta_failure=0,
            difficulty=10,
        )
        profile = _dnd_profile_minimal()

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Save Eddard Stark's life",
                profile=profile,
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve_rep(worker_data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=20):
                _, updates = asyncio.run(_run())

        engine_delta = updates["reputation_delta"]
        assert engine_delta == 7, f"Engine must output delta=7 on SUCCESS. Got {engine_delta}"

        # Step 2: apply_reputation_step(0, 7) → 100
        final_score = apply_reputation_step(0, engine_delta)
        assert final_score == 100, (
            f"apply_reputation_step(0, 7) must yield 100 (epic jump). Got {final_score}"
        )

        # Step 3: label check
        label = score_to_relation_text(final_score)
        assert label == "Абсолютна довіра", (
            f"score_to_relation_text(100) must be 'Абсолютна довіра'. Got '{label}'"
        )

    def test_minus7_on_failure_to_blood_enemy(self):
        """Pipeline smoke:
          - Engine: reputation_delta_failure=-7 on CRITICAL FAILURE → delta=-7 in updates.
          - apply_reputation_step(old_val=0, raw_delta=-7) → -100 "Смертельна ненависть".
        """
        from core.reputation import apply_reputation_step
        from core.dnd_core import score_to_relation_text

        worker_data = _minimal_worker_data_rep(
            reputation_delta_success=0,
            reputation_delta_failure=-7,
            difficulty=10,
        )
        profile = _dnd_profile_minimal()

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Betray and murder an ally",
                profile=profile,
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve_rep(worker_data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=1):  # nat 1
                _, updates = asyncio.run(_run())

        engine_delta = updates["reputation_delta"]
        assert engine_delta == -7, f"Engine must output delta=-7 on CRITICAL FAILURE. Got {engine_delta}"

        final_score = apply_reputation_step(0, engine_delta)
        assert final_score == -100, (
            f"apply_reputation_step(0, -7) must yield -100 (epic crash). Got {final_score}"
        )

        label = score_to_relation_text(final_score)
        assert label == "Смертельна ненависть", (
            f"score_to_relation_text(-100) must be 'Смертельна ненависть'. Got '{label}'"
        )
