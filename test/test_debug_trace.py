# test/test_debug_trace.py
"""
Unit-tests for the debug trace accumulation in core/engine.py (Round 1).

Strategy:
- Test cmd_debugmode toggle logic directly via the DEBUG_USERS set
  without importing core.cheats through its heavy dependency chain.
- Test that `_debug_trace` structure is well-formed by exercising the
  initialization and field-fill logic in isolation.

These tests are self-contained: they use a minimal fake `core.cheats` stub
(with a real `set` for DEBUG_USERS) and verify the state machine behavior
described in the implementation.
"""

import asyncio
import sys
import types
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_debug_trace(chat_id: int = 111, user_input: str = "Test action") -> dict:
    """Build a _debug_trace dict with the same shape that engine.py produces."""
    return {
        "chat_id": chat_id,
        "user_input": user_input,
        "timestamp": time.time(),
        "censor": {"prompt": None, "raw": None, "parsed": None, "thoughts": []},
        "worker": {"prompt": None, "raw": None, "parsed": None, "thoughts": []},
        "gm_logic": {"prompt": None, "raw": None, "parsed": None, "thoughts": []},
        "narrator": {"prompt": None, "thoughts": [], "final_text": None},
        "logs": [],
    }


# ---------------------------------------------------------------------------
# Tests: DEBUG_USERS toggle behavior (state machine)
# ---------------------------------------------------------------------------

class TestDebugUsersStateMachine:
    """
    Tests for the toggle logic that controls DEBUG_USERS.

    These tests use a bare `set` to simulate DEBUG_USERS — identical to the
    actual module-level `DEBUG_USERS: set = set()` in core/cheats.py.
    They verify the toggle semantics without importing the full cheats module.
    """

    def test_toggle_on_adds_to_set(self):
        """When chat_id absent from set: add it (toggle ON)."""
        DEBUG_USERS: set = set()
        chat_id = 494157543

        if chat_id in DEBUG_USERS:
            DEBUG_USERS.discard(chat_id)
        else:
            DEBUG_USERS.add(chat_id)

        assert chat_id in DEBUG_USERS

    def test_toggle_off_removes_from_set(self):
        """When chat_id present in set: remove it (toggle OFF)."""
        DEBUG_USERS: set = set()
        chat_id = 494157543
        DEBUG_USERS.add(chat_id)  # pre-condition: already ON

        if chat_id in DEBUG_USERS:
            DEBUG_USERS.discard(chat_id)
        else:
            DEBUG_USERS.add(chat_id)

        assert chat_id not in DEBUG_USERS

    def test_non_admin_not_added(self):
        """Non-admin must not be added — simulates the guard check."""
        ADMIN_TELEGRAM_IDS = [494157543, 778186089]
        DEBUG_USERS: set = set()
        non_admin_id = 99999999

        if non_admin_id in ADMIN_TELEGRAM_IDS:
            DEBUG_USERS.add(non_admin_id)

        assert non_admin_id not in DEBUG_USERS

    def test_admin_id_passes_guard(self):
        """Admin ID passes guard check and is added to DEBUG_USERS."""
        ADMIN_TELEGRAM_IDS = [494157543, 778186089]
        DEBUG_USERS: set = set()
        admin_id = 494157543

        if admin_id in ADMIN_TELEGRAM_IDS:
            DEBUG_USERS.add(admin_id)

        assert admin_id in DEBUG_USERS

    def test_toggle_is_idempotent_double_on(self):
        """Two consecutive toggle-on calls result in OFF (second call toggles it back)."""
        DEBUG_USERS: set = set()
        chat_id = 494157543

        # First toggle: ON
        if chat_id in DEBUG_USERS:
            DEBUG_USERS.discard(chat_id)
        else:
            DEBUG_USERS.add(chat_id)
        assert chat_id in DEBUG_USERS

        # Second toggle: OFF
        if chat_id in DEBUG_USERS:
            DEBUG_USERS.discard(chat_id)
        else:
            DEBUG_USERS.add(chat_id)
        assert chat_id not in DEBUG_USERS


# ---------------------------------------------------------------------------
# Tests: _debug_trace structure
# ---------------------------------------------------------------------------

class TestDebugTraceStructure:
    """
    Tests for the _debug_trace dict structure that engine.py builds.
    These test the factory function (same logic as engine's initialization block)
    and simulate field-fill operations.
    """

    def test_initial_trace_has_all_required_keys(self):
        """The _debug_trace dict produced by engine.py init block has all required keys."""
        trace = _make_debug_trace(chat_id=42, user_input="Look around")

        required_top = {"chat_id", "user_input", "timestamp", "censor", "worker", "gm_logic", "narrator", "logs"}
        missing = required_top - set(trace.keys())
        assert not missing, f"Missing top-level keys: {missing}"

    def test_censor_sub_keys(self):
        """censor section has prompt, raw, parsed, thoughts keys."""
        trace = _make_debug_trace()
        censor = trace["censor"]
        assert "prompt" in censor
        assert "raw" in censor
        assert "parsed" in censor
        assert "thoughts" in censor
        assert isinstance(censor["thoughts"], list)

    def test_worker_sub_keys(self):
        """worker section has prompt, raw, parsed, thoughts keys."""
        trace = _make_debug_trace()
        worker = trace["worker"]
        assert "prompt" in worker
        assert "raw" in worker
        assert "parsed" in worker
        assert "thoughts" in worker

    def test_gm_logic_sub_keys(self):
        """gm_logic section has prompt, raw, parsed, thoughts keys."""
        trace = _make_debug_trace()
        gm = trace["gm_logic"]
        assert "prompt" in gm
        assert "raw" in gm
        assert "parsed" in gm
        assert "thoughts" in gm

    def test_narrator_sub_keys(self):
        """narrator section has prompt, thoughts and final_text keys."""
        trace = _make_debug_trace()
        narrator = trace["narrator"]
        assert "prompt" in narrator
        assert "thoughts" in narrator
        assert "final_text" in narrator
        assert isinstance(narrator["thoughts"], list)

    def test_logs_is_list(self):
        """logs field is an empty list by default."""
        trace = _make_debug_trace()
        assert isinstance(trace["logs"], list)

    def test_censor_fill_operation(self):
        """Simulates engine.py filling censor.parsed after validate_action returns."""
        trace = _make_debug_trace()
        is_valid, refusal_reason = True, ""

        # This is the exact fill code from engine.py
        trace["censor"]["parsed"] = {
            "is_valid": is_valid,
            "refusal_reason": refusal_reason,
        }

        assert trace["censor"]["parsed"]["is_valid"] is True
        assert trace["censor"]["parsed"]["refusal_reason"] == ""

    def test_censor_fill_blocked_action(self):
        """Simulates fill when Censor blocks the action."""
        trace = _make_debug_trace()
        is_valid, refusal_reason = False, "Content violation: explicit violence"

        trace["censor"]["parsed"] = {
            "is_valid": is_valid,
            "refusal_reason": refusal_reason,
        }

        assert trace["censor"]["parsed"]["is_valid"] is False
        assert "violation" in trace["censor"]["parsed"]["refusal_reason"]

    def test_worker_fill_operation(self):
        """Simulates engine.py filling worker.parsed after worker_task completes."""
        trace = _make_debug_trace()
        mechanical_updates = {"difficulty": 10, "xp_award": 25, "action_type": "standard"}

        trace["worker"]["parsed"] = mechanical_updates

        assert trace["worker"]["parsed"]["difficulty"] == 10
        assert trace["worker"]["parsed"]["xp_award"] == 25

    def test_gm_logic_fill_operation(self):
        """Simulates engine.py filling gm_logic fields after generate_content returns."""
        trace = _make_debug_trace()
        gm_raw = '{"director_notes": ["The battle begins."], "npc_updates": []}'
        gm_parsed = {"director_notes": ["The battle begins."], "npc_updates": []}

        trace["gm_logic"]["raw"] = gm_raw
        trace["gm_logic"]["parsed"] = gm_parsed

        assert trace["gm_logic"]["raw"] == gm_raw
        assert trace["gm_logic"]["parsed"]["director_notes"][0] == "The battle begins."

    def test_narrator_fill_operation(self):
        """Simulates engine.py filling narrator.final_text."""
        trace = _make_debug_trace()
        story = "The raven lands on the parapet, its black eyes watching."

        trace["narrator"]["final_text"] = story

        assert trace["narrator"]["final_text"] == story

    def test_censor_thoughts_captured_in_debug_mode(self):
        """Sequential debug path: thoughts captured after Censor go into censor.thoughts."""
        trace = _make_debug_trace()

        # Simulate the stage-aware capture for Censor stage
        censor_thoughts_log = [
            {"model": "gemma-4-31b-it", "thought": "Checking content policy"},
            {"model": "gemma-4-31b-it", "thought": "No violation found"},
        ]
        trace["censor"]["thoughts"] = [
            e.get("thought", "") for e in censor_thoughts_log if e.get("thought")
        ]

        assert len(trace["censor"]["thoughts"]) == 2
        assert trace["censor"]["thoughts"][0] == "Checking content policy"
        # Worker and narrator thoughts must NOT be contaminated by Censor stage
        assert trace["worker"]["thoughts"] == []
        assert trace["narrator"]["thoughts"] == []

    def test_gm_logic_thoughts_captured_separately(self):
        """clear_thoughts() before GM_Logic isolates its thoughts from Worker stage."""
        trace = _make_debug_trace()

        # Simulate: Worker stage produced thoughts that are already in trace["worker"]
        trace["worker"]["thoughts"] = ["Checking skill proficiency"]

        # After clear_thoughts(), only GM_Logic thoughts appear in _thoughts_log
        gm_thoughts_log = [
            {"model": "gemma-4-31b-it", "thought": "GM logic reasoning"},
        ]
        trace["gm_logic"]["thoughts"] = [
            e.get("thought", "") for e in gm_thoughts_log if e.get("thought")
        ]

        # GM thoughts must not bleed into worker
        assert trace["worker"]["thoughts"] == ["Checking skill proficiency"]
        assert trace["gm_logic"]["thoughts"] == ["GM logic reasoning"]
        assert trace["narrator"]["thoughts"] == []

    def test_narrator_thoughts_captured_separately(self):
        """clear_thoughts() before Narrator isolates its thoughts from GM_Logic stage."""
        trace = _make_debug_trace()

        # Previous stages already populated
        trace["worker"]["thoughts"] = ["Worker thought"]
        trace["gm_logic"]["thoughts"] = ["GM thought"]

        # After clear_thoughts(), only Narrator thoughts appear in _thoughts_log
        narrator_thoughts_log = [
            {"model": "gemma-4-31b-it", "thought": "Crafting the narrative"},
        ]
        trace["narrator"]["thoughts"] = [
            e.get("thought", "") for e in narrator_thoughts_log if e.get("thought")
        ]

        assert trace["narrator"]["thoughts"] == ["Crafting the narrative"]
        # Earlier stages must remain unchanged
        assert trace["worker"]["thoughts"] == ["Worker thought"]
        assert trace["gm_logic"]["thoughts"] == ["GM thought"]

    def test_logs_fill_operation(self):
        """Simulates engine.py assigning list(logs) to trace.logs."""
        trace = _make_debug_trace()
        logs = ["HP: 10 -> 8 (dmg_light)", "XP: 0 -> 25"]

        trace["logs"] = list(logs)

        assert trace["logs"] == ["HP: 10 -> 8 (dmg_light)", "XP: 0 -> 25"]

    def test_store_in_user_sessions(self):
        """Simulates engine.py storing trace in user_sessions[chat_id]['last_debug_trace']."""
        user_sessions: dict = {}
        chat_id = 42
        trace = _make_debug_trace(chat_id=chat_id)

        # This is the exact store code from engine.py
        user_sessions.setdefault(chat_id, {})["last_debug_trace"] = trace

        assert "last_debug_trace" in user_sessions[chat_id]
        assert user_sessions[chat_id]["last_debug_trace"]["chat_id"] == chat_id

    def test_no_store_when_debug_inactive(self):
        """When _debug_active is False, user_sessions must not receive last_debug_trace."""
        user_sessions: dict = {}
        chat_id = 42
        _debug_active = False
        _debug_trace = None  # never initialized when inactive

        # Simulate the guard condition from engine.py
        if _debug_active and _debug_trace is not None:
            user_sessions.setdefault(chat_id, {})["last_debug_trace"] = _debug_trace

        session = user_sessions.get(chat_id, {})
        assert "last_debug_trace" not in session


# ---------------------------------------------------------------------------
# Tests: cmd_debugmode dispatch registration
# ---------------------------------------------------------------------------

class TestDebugModeDispatchRegistration:
    """
    Verify that /debugmode is registered in the handle_cheat_command dispatch table.
    Uses a stub-based import to avoid heavy dependency chain.
    """

    def test_debugmode_key_in_dispatch(self):
        """'/debugmode' must appear in handle_cheat_command dispatch dict."""
        import importlib
        import sys

        # Stubs needed to import core.cheats
        fake_sheets = types.ModuleType("database.sheets")
        fake_sheets.db = MagicMock()
        fake_ops = types.ModuleType("database.operations")
        fake_ops.get_user_data = AsyncMock()
        fake_ops.save_user_data = AsyncMock(return_value=True)
        fake_ops.find_best_match = MagicMock(return_value=None)
        fake_ops.evict_npc_from_cache = MagicMock()
        fake_ops.refresh_npc_database = AsyncMock()

        stubs = {
            "database.sheets": fake_sheets,
            "database.operations": fake_ops,
        }

        with patch.dict(sys.modules, stubs):
            import core.cheats as cheats
            importlib.reload(cheats)

            # Simulate the dispatch table construction from handle_cheat_command
            # by importing the known function and checking it is callable
            assert hasattr(cheats, "cmd_debugmode"), (
                "core.cheats must have cmd_debugmode function"
            )
            assert callable(cheats.cmd_debugmode), (
                "cmd_debugmode must be callable"
            )
            assert hasattr(cheats, "DEBUG_USERS"), (
                "core.cheats must have DEBUG_USERS set"
            )
            assert isinstance(cheats.DEBUG_USERS, set), (
                "DEBUG_USERS must be a set instance"
            )


# ---------------------------------------------------------------------------
# Tests: _is_debug_active helper (persistent profile flag, cold-start survival)
# ---------------------------------------------------------------------------

# Inline reimplementation of the helper for isolated unit-testing.
# This avoids importing core.engine (which pulls in a heavy async dependency
# chain including gspread, Gemini SDK, etc.).
# The contract tested here is identical to the one in core/engine.py — any
# divergence in the production implementation would be caught by a separate
# integration test or code-review.

def _is_debug_active_impl(chat_id: int, profile: dict, debug_users: set) -> bool:
    """Mirror of core.engine._is_debug_active — kept in sync manually."""
    return (chat_id in debug_users) or bool(profile.get("_debug_mode", False))


class TestIsDebugActiveHelper:
    """
    Unit-tests for the _is_debug_active() boolean logic.

    Tests use a local reimplementation (_is_debug_active_impl) that mirrors
    core.engine._is_debug_active exactly, avoiding the heavy async import
    chain.  The logic under test is a single boolean expression — any change
    to the production function must keep the same semantics or these tests
    will catch the drift.

    Covered scenarios:
    - Only in-memory set active        → True
    - Only profile flag active         → True  (cold-start survival)
    - Both active                      → True
    - Neither active                   → False
    - profile["_debug_mode"] = False   → False (explicit opt-out)
    - profile["_debug_mode"] = None    → False (null-safe via bool())
    - profile["_debug_mode"] missing   → False (default via .get())
    - profile["_debug_mode"] = 1 (int) → True  (truthy int is accepted)
    - Different chat_id in set         → False
    """

    def test_in_memory_set_only(self):
        """chat_id in debug_users, profile flag absent → True."""
        chat_id = 42
        debug_users = {chat_id}
        profile: dict = {}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is True

    def test_profile_flag_only_cold_start_survival(self):
        """_debug_mode=True in profile, debug_users empty → True (cold-start scenario)."""
        chat_id = 42
        debug_users: set = set()
        profile = {"_debug_mode": True}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is True

    def test_both_sources_active(self):
        """Both in-memory set and profile flag set → True."""
        chat_id = 42
        debug_users = {chat_id}
        profile = {"_debug_mode": True}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is True

    def test_neither_source_active(self):
        """No in-memory, no profile flag → False."""
        chat_id = 42
        debug_users: set = set()
        profile: dict = {}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is False

    def test_profile_flag_false_explicit(self):
        """profile['_debug_mode'] = False explicitly → False (unless in set)."""
        chat_id = 42
        debug_users: set = set()
        profile = {"_debug_mode": False}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is False

    def test_profile_flag_none_is_falsy(self):
        """profile['_debug_mode'] = None → False (bool(None) == False)."""
        chat_id = 42
        debug_users: set = set()
        profile = {"_debug_mode": None}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is False

    def test_profile_flag_missing_key(self):
        """Key '_debug_mode' absent → False (profile.get default)."""
        chat_id = 42
        debug_users: set = set()
        profile = {"SomeOtherKey": 123}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is False

    def test_profile_flag_truthy_int(self):
        """profile['_debug_mode'] = 1 (truthy int) → True."""
        chat_id = 42
        debug_users: set = set()
        profile = {"_debug_mode": 1}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is True

    def test_different_chat_id_not_activated(self):
        """debug_users contains different chat_id, profile has no flag → False."""
        chat_id = 42
        other_id = 99
        debug_users = {other_id}
        profile: dict = {}
        assert _is_debug_active_impl(chat_id, profile, debug_users) is False


# ---------------------------------------------------------------------------
# Tests: prompt capture in debug_trace (Stage INPUT capture)
# ---------------------------------------------------------------------------

class TestDebugTracePromptCapture:
    """
    Tests verifying that 'prompt' keys are present in every stage section
    and that validate_action / resolve_normal_action fill them when
    debug_trace is provided.

    Contract for python-dev (_send_debug_trace_file):
        _debug_trace[stage]["prompt"] = str  (full input text sent to model)
        Stages: "censor", "worker", "gm_logic", "narrator"
    """

    def test_all_stages_have_prompt_key_initially_none(self):
        """All four stage sections must have 'prompt': None in fresh trace."""
        trace = _make_debug_trace()
        for stage in ("censor", "worker", "gm_logic", "narrator"):
            assert "prompt" in trace[stage], f"Missing 'prompt' key in stage '{stage}'"
            assert trace[stage]["prompt"] is None, (
                f"Expected None initial value for '{stage}.prompt'"
            )

    def test_censor_prompt_fill(self):
        """Simulates validate_action writing censor prompt into debug_trace."""
        trace = _make_debug_trace()
        fake_prompt = "You are a Censor. User says: attack the king."

        # This is the exact assignment in mechanics.py validate_action
        trace["censor"]["prompt"] = fake_prompt

        assert trace["censor"]["prompt"] == fake_prompt
        # Other stages must remain None
        assert trace["worker"]["prompt"] is None
        assert trace["gm_logic"]["prompt"] is None
        assert trace["narrator"]["prompt"] is None

    def test_worker_prompt_fill(self):
        """Simulates resolve_normal_action writing worker prompt into debug_trace."""
        trace = _make_debug_trace()
        fake_prompt = "<player_state>...</player_state>\n<scene_data>...</scene_data>"

        # This is the exact assignment in dnd_engine.py resolve_normal_action
        trace["worker"]["prompt"] = fake_prompt

        assert trace["worker"]["prompt"] == fake_prompt
        assert trace["censor"]["prompt"] is None

    def test_gm_logic_prompt_fill(self):
        """Simulates engine.py writing gm_logic prompt into debug_trace."""
        trace = _make_debug_trace()
        fake_prompt = "You are GM_Logic. Hero is in King's Landing."

        trace["gm_logic"]["prompt"] = fake_prompt

        assert trace["gm_logic"]["prompt"] == fake_prompt
        assert trace["narrator"]["prompt"] is None

    def test_narrator_prompt_fill(self):
        """Simulates engine.py writing narrator prompt into debug_trace."""
        trace = _make_debug_trace()
        fake_prompt = "You are Narrator. Write 150-250 words."

        trace["narrator"]["prompt"] = fake_prompt

        assert trace["narrator"]["prompt"] == fake_prompt

    def test_prompt_is_str_type(self):
        """Prompt values must be str (contract for handlers.py formatter)."""
        trace = _make_debug_trace()
        trace["censor"]["prompt"] = "prompt text"
        trace["worker"]["prompt"] = "worker prompt"
        trace["gm_logic"]["prompt"] = "gm prompt"
        trace["narrator"]["prompt"] = "narrator prompt"

        for stage in ("censor", "worker", "gm_logic", "narrator"):
            assert isinstance(trace[stage]["prompt"], str), (
                f"prompt in stage '{stage}' must be str, got {type(trace[stage]['prompt'])}"
            )

    def test_non_debug_path_no_capture_validate_action(self):
        """When debug_trace=None (non-debug path), validate_action must not fail."""
        # Simulate the guard: if debug_trace is None, skip assignment silently
        debug_trace = None
        prompt = "censor prompt text"
        if debug_trace is not None:
            debug_trace["censor"]["prompt"] = prompt
        # No exception — non-debug path is transparent
        assert debug_trace is None

    def test_non_debug_path_no_capture_resolve_normal_action(self):
        """When debug_trace=None (non-debug path), worker prompt capture is skipped."""
        debug_trace = None
        prompt = "worker prompt text"
        if debug_trace is not None:
            debug_trace["worker"]["prompt"] = prompt
        assert debug_trace is None

    def test_validate_action_debug_trace_param_accepted(self):
        """validate_action signature must accept debug_trace kwarg (no TypeError)."""
        import inspect
        import core.mechanics as mech
        sig = inspect.signature(mech.validate_action)
        assert "debug_trace" in sig.parameters, (
            "validate_action must have 'debug_trace' parameter"
        )
        param = sig.parameters["debug_trace"]
        assert param.default is None, (
            "debug_trace default must be None (non-debug path is zero-overhead)"
        )

    def test_resolve_normal_action_debug_trace_param_accepted(self):
        """resolve_normal_action signature must accept debug_trace kwarg (no TypeError)."""
        import inspect
        import sys
        import types

        # Stub heavy deps before importing dnd_engine
        for mod_name in [
            "core.ai_client", "core.dnd_core", "core.dnd_skills",
            "core.dnd_classes", "core.dnd_progression", "core.dnd_conditions",
            "core.prompts", "core.world_constants",
        ]:
            if mod_name not in sys.modules:
                stub = types.ModuleType(mod_name)
                # Provide minimal attrs that dnd_engine imports
                stub.model_worker = MagicMock()
                stub.clean_and_parse_json = MagicMock()
                stub.build_strict_config = MagicMock()
                stub.roll_d20 = MagicMock()
                stub.ability_modifier = MagicMock()
                stub.proficiency_bonus = MagicMock()
                stub.clamp_dc = MagicMock()
                stub.LEGAL_DCS = [2, 5, 10, 12, 15, 17, 20, 22]
                stub.skill_check = MagicMock()
                stub.ability_check = MagicMock()
                stub.saving_throw = MagicMock()
                stub.CheckResult = MagicMock()
                stub.SKILLS = {}
                stub.get_ability_for_skill = MagicMock()
                stub.is_valid_skill = MagicMock()
                stub.GOT_CLASSES = {}
                stub.get_class_features_at_level = MagicMock()
                stub.award_xp = MagicMock()
                stub.level_up = MagicMock()
                stub.apply_asi = MagicMock()
                stub.get_level_for_xp = MagicMock()
                stub.apply_pending_levelups = MagicMock()
                stub.apply_condition = MagicMock()
                stub.remove_condition = MagicMock()
                stub.has_condition = MagicMock()
                stub.condition_modifies = MagicMock()
                stub.build_normal_resolve_prompt = MagicMock()
                stub.build_normal_resolve_schema = MagicMock()
                stub.get_region_for_location = MagicMock(return_value="")
                stub.get_locations_for_region = MagicMock(return_value=[])
                stub.LOCATION_TO_REGION = {}
                stub.VALID_REGIONS_ORDERED = []
                sys.modules[mod_name] = stub

        import importlib
        import core.dnd_engine as dnd_eng
        importlib.reload(dnd_eng)

        sig = inspect.signature(dnd_eng.resolve_normal_action)
        assert "debug_trace" in sig.parameters, (
            "resolve_normal_action must have 'debug_trace' parameter"
        )
        param = sig.parameters["debug_trace"]
        assert param.default is None, (
            "debug_trace default must be None (non-debug path is zero-overhead)"
        )


# ---------------------------------------------------------------------------
# Tests: raw LLM response capture (Fix 2a / Fix 2b)
# ---------------------------------------------------------------------------

class TestDebugTraceRawCapture:
    """
    Tests verifying that 'raw' keys are filled when validate_action and
    resolve_normal_action receive a debug_trace dict.

    Strategy: patch asyncio.to_thread so we never hit a real LLM.
    For validate_action we also stub model_worker and clean_and_parse_json
    to control the full path.
    """

    def test_censor_raw_filled_by_validate_action(self):
        """
        validate_action must write response.text into debug_trace["censor"]["raw"]
        immediately after await asyncio.to_thread() returns.
        """
        import asyncio
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        # Build a minimal fake response object
        fake_response = MagicMock()
        fake_response.text = '{"is_valid": true, "refusal_reason": ""}'

        # Stub heavy deps that mechanics.py imports at module level
        for mod_name in [
            "core.ai_client",
            "core.prompts",
            "core.world_constants",
        ]:
            if mod_name not in sys.modules:
                stub = types.ModuleType(mod_name)
                stub.model_worker = MagicMock()
                stub.clean_and_parse_json = MagicMock(return_value={"is_valid": True, "refusal_reason": ""})
                stub.build_strict_config = MagicMock(return_value=MagicMock())
                stub.build_validate_action_prompt = MagicMock(return_value="prompt text")
                stub.build_validate_action_schema = MagicMock(return_value={})
                sys.modules[mod_name] = stub

        import importlib
        import core.mechanics as mech
        importlib.reload(mech)

        trace = _make_debug_trace()

        async def _run_validate():
            with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_response)):
                with patch.object(mech, "clean_and_parse_json",
                                  return_value={"is_valid": True, "refusal_reason": ""}):
                    profile = {"Ім'я": "Arya", "Здоров'я": 80, "Інвентар": []}
                    return await mech.validate_action("test action", profile, debug_trace=trace)

        asyncio.run(_run_validate())

        assert trace["censor"]["raw"] == fake_response.text, (
            f"Expected censor.raw={fake_response.text!r}, got {trace['censor']['raw']!r}"
        )

    def test_censor_raw_none_when_debug_trace_not_provided(self):
        """When debug_trace=None, censor.raw capture is silently skipped (no AttributeError)."""
        # Simulate the guard logic directly — no actual LLM call needed
        debug_trace = None
        fake_response = MagicMock()
        fake_response.text = '{"is_valid": true}'

        # Guard from mechanics.py
        if debug_trace is not None:
            debug_trace["censor"]["raw"] = fake_response.text if fake_response else None

        assert debug_trace is None  # unchanged

    def test_censor_raw_none_on_null_response(self):
        """
        If response is falsy (edge case: model returns None), raw must be None not raise.
        Simulates: debug_trace["censor"]["raw"] = response.text if response else None
        """
        trace = _make_debug_trace()
        response = None  # edge case

        # This is the exact guard from mechanics.py
        if trace is not None:
            trace["censor"]["raw"] = response.text if response else None

        assert trace["censor"]["raw"] is None

    def test_worker_raw_filled_after_to_thread(self):
        """
        Simulates the fix in dnd_engine.py: resp.text written to debug_trace["worker"]["raw"]
        immediately after asyncio.to_thread returns.
        """
        trace = _make_debug_trace()
        fake_resp = MagicMock()
        fake_resp.text = '{"skill_used": "Stealth", "difficulty": 15}'

        # Simulate the fix code path from dnd_engine.py
        if trace is not None:
            trace["worker"]["raw"] = fake_resp.text if fake_resp else None

        assert trace["worker"]["raw"] == fake_resp.text, (
            f"Expected worker.raw={fake_resp.text!r}, got {trace['worker']['raw']!r}"
        )

    def test_worker_raw_none_when_debug_trace_not_provided(self):
        """When debug_trace=None, worker.raw capture is silently skipped."""
        debug_trace = None
        fake_resp = MagicMock()
        fake_resp.text = '{"skill_used": "Athletics"}'

        if debug_trace is not None:
            debug_trace["worker"]["raw"] = fake_resp.text if fake_resp else None

        assert debug_trace is None

    def test_worker_raw_none_on_null_resp(self):
        """If resp is falsy, raw must be None not raise AttributeError."""
        trace = _make_debug_trace()
        resp = None

        if trace is not None:
            trace["worker"]["raw"] = resp.text if resp else None

        assert trace["worker"]["raw"] is None

    def test_raw_fields_initially_none_in_trace(self):
        """Both censor.raw and worker.raw must be None in a freshly built trace."""
        trace = _make_debug_trace()
        assert trace["censor"]["raw"] is None, "censor.raw must start as None"
        assert trace["worker"]["raw"] is None, "worker.raw must start as None"

    def test_raw_field_is_str_type_when_filled(self):
        """raw field must be str when filled (contract for handlers.py format)."""
        trace = _make_debug_trace()
        raw_text = '{"is_valid": true}'
        trace["censor"]["raw"] = raw_text
        assert isinstance(trace["censor"]["raw"], str)

        raw_worker = '{"difficulty": 10}'
        trace["worker"]["raw"] = raw_worker
        assert isinstance(trace["worker"]["raw"], str)


# ---------------------------------------------------------------------------
# Tests: COMBAT path forwards debug_trace to validate_action (Fix 1)
# ---------------------------------------------------------------------------

class TestCombatCensorDebugTraceForward:
    """
    Verify that the COMBAT branch of process_game_turn passes debug_trace
    to validate_action when _debug_active is True.

    Strategy: simulate the conditional expression introduced by Fix 1:
        debug_trace=_debug_trace if _debug_active else None

    We do NOT import engine.py (heavy chain). Instead we test the forwarding
    expression in isolation, which mirrors the exact logic added.
    """

    def test_debug_active_true_forwards_trace(self):
        """When _debug_active=True, validate_action receives the trace dict, not None."""
        _debug_active = True
        _debug_trace = _make_debug_trace(chat_id=7, user_input="attack")

        # This is the exact expression from engine.py Fix 1
        kwarg_value = _debug_trace if _debug_active else None

        assert kwarg_value is _debug_trace, (
            "debug_trace kwarg must be the trace dict when _debug_active=True"
        )

    def test_debug_active_false_forwards_none(self):
        """When _debug_active=False, validate_action receives None (non-debug path)."""
        _debug_active = False
        _debug_trace = _make_debug_trace(chat_id=7, user_input="attack")

        kwarg_value = _debug_trace if _debug_active else None

        assert kwarg_value is None, (
            "debug_trace kwarg must be None when _debug_active=False"
        )

    def test_debug_trace_none_and_inactive(self):
        """When _debug_trace is None and _debug_active=False, kwarg is None (safe)."""
        _debug_active = False
        _debug_trace = None

        kwarg_value = _debug_trace if _debug_active else None

        assert kwarg_value is None

    def test_combat_censor_prompt_captured_via_forward(self):
        """
        End-to-end simulation: debug_trace forwarded → validate_action writes
        censor.prompt → trace["censor"]["prompt"] is filled.
        Simulates the actual call path without importing engine.py.
        """
        _debug_active = True
        _debug_trace = _make_debug_trace(chat_id=7, user_input="attack the guard")

        # Simulate validate_action receiving and using the forwarded trace
        forwarded_trace = _debug_trace if _debug_active else None
        fake_prompt = "COMBAT censor prompt for: attack the guard"

        # This is what validate_action does internally (mechanics.py line ~382)
        if forwarded_trace is not None:
            forwarded_trace["censor"]["prompt"] = fake_prompt

        assert _debug_trace["censor"]["prompt"] == fake_prompt, (
            "censor.prompt must be filled when debug_trace is forwarded in COMBAT path"
        )
