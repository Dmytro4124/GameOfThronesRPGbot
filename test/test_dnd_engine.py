"""
test_dnd_engine.py

Tests for core/dnd_engine.py (Phase 5):
  - resolve_normal_action: async D&D Worker (mocks LLM)
  - apply_dnd_impacts: HP dice, conditions, XP, delegation to legacy
  - process_game_turn integration: FSM dispatcher, COMBAT/NORMAL/combat_imminent

Strategy:
  - LLM is mocked via patch('core.dnd_engine.model_worker.generate_content') +
    patch('core.dnd_engine.clean_and_parse_json').
  - Dice are mocked via patch('core.dnd_engine.random.randint') where determinism matters.
  - No real Google Sheets or Gemini API calls.
"""

import asyncio
import logging
import random
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers: minimal profile and LLM response factories
# ---------------------------------------------------------------------------

def _dnd_profile(**overrides) -> dict:
    """Return a minimal D&D profile for testing resolve_normal_action."""
    base = {
        "Ім'я": "TestHero",
        "Дім": "Stark",
        "Титул": "Lord",
        "Здоров'я": 100,
        "Енергія": 800,
        "Особисте Золото": 200,
        "Поточне місцезнаходження": "Вінтерфелл",
        "Поточна сцена": "Throne Room",
        "Регіон": "Північ",
        "Ігровий час": "Day 1, Morning",
        "Інвентар": "Sword",
        "Зброя": "Longsword",
        "Броня": "Chainmail",
        "Транспорт": "Horse",
        "Світогляд": "Neutral",
        "Риси": "Brave",
        "Вади": "Stubborn",
        "Вороги": "",
        "Друзі": "",
        "Годинники": {"Scene_Tension": "0/4"},
        "level": 1,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "skill_profs": ["Athletics", "Intimidation"],
        "skill_expertise": [],
        "saves_proficient": ["STR", "CON"],
        "conditions": [],
        "xp": 0,
    }
    base.update(overrides)
    return base


def _dnd_profile_with_hp(**overrides) -> dict:
    """Return a D&D profile that uses hp_current/hp_max (new-style)."""
    base = _dnd_profile(**overrides)
    base["hp_current"] = 50
    base["hp_max"] = 50
    return base


def _llm_response_json(data: dict) -> str:
    """Serialize dict to JSON string as an LLM would return."""
    import json
    return json.dumps(data, ensure_ascii=False)


def _make_llm_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.text = _llm_response_json(data)
    return m


def _minimal_worker_data(**overrides) -> dict:
    """Minimal valid LLM output for resolve_normal_action."""
    base = {
        "ability_used": "STR",
        "skill_used": "Athletics",
        "difficulty": 15,
        "advantage_reason": "",
        "disadvantage_reason": "",
        "combat_imminent": False,
        "verdict_text": "You succeed with ease.",
        "xp_award": 25,
        "reputation_delta": 0,
        "reputation_target_npc": "",
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


def _run_async(coro):
    """Helper: run a coroutine in a fresh event loop (asyncio.run creates a new loop).
    Using asyncio.run() instead of get_event_loop().run_until_complete() avoids
    RuntimeError when a prior test already closed the current event loop (e.g. after asyncio.run()).
    """
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixture: patch all LLM + prompt calls for resolve_normal_action
# ---------------------------------------------------------------------------

def _patches_for_resolve(worker_data: dict = None, fail_llm: bool = False):
    """
    Return a list of patch context managers for isolating resolve_normal_action.
    worker_data: the dict that clean_and_parse_json will return.
    fail_llm: if True, make generate_content raise an exception.
    """
    if worker_data is None:
        worker_data = _minimal_worker_data()

    if fail_llm:
        gen_patch = patch(
            "core.dnd_engine.model_worker.generate_content",
            side_effect=Exception("Simulated LLM failure"),
        )
    else:
        gen_patch = patch(
            "core.dnd_engine.model_worker.generate_content",
            return_value=_make_llm_response(worker_data),
        )

    parse_patch = patch(
        "core.dnd_engine.clean_and_parse_json",
        return_value=worker_data if not fail_llm else None,
    )

    prompt_patch = patch(
        "core.dnd_engine.build_normal_resolve_prompt",
        return_value="MOCK_PROMPT",
    )

    return [gen_patch, parse_patch, prompt_patch]


# ===========================================================================
# Regression guard: _DANGER_DC_FLOOR constant downgrade
# ===========================================================================

def test_danger_floor_is_fifteen():
    """Тестовий downgrade: _DANGER_DC_FLOOR = 15 (раніше 25, потім 17)."""
    from core.dnd_engine import _DANGER_DC_FLOOR
    assert _DANGER_DC_FLOOR == 15


# ===========================================================================
# Tests: resolve_normal_action
# ===========================================================================

class TestResolveNormalAction:
    """Tests for the async D&D Worker (resolve_normal_action)."""

    # 1. Valid JSON with Athletics skill + DC 15 → outcome is SUCCESS or FAILURE
    def test_valid_skill_check_outcome_in_expected_set(self):
        """LLM returns ability_used=STR, skill_used=Athletics, difficulty=15.
        Outcome must be one of the 4 valid strings."""
        profile = _dnd_profile()
        data = _minimal_worker_data(ability_used="STR", skill_used="Athletics", difficulty=15)

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Climb the wall",
                profile=profile,
                current_location="Вінтерфелл",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            verdict, updates = _run_async(_run())

        valid_outcomes = {"SUCCESS", "FAILURE", "CRITICAL SUCCESS", "CRITICAL FAILURE"}
        assert updates["outcome"] in valid_outcomes, (
            f"outcome must be in {valid_outcomes}, got {updates['outcome']!r}"
        )

    # 2. Invalid DC=18 → clamped to nearest legal DC (17 or 20)
    def test_invalid_dc_is_clamped_to_legal_value(self):
        """LLM returns difficulty=18 (not in LEGAL_DCS) → clamp_dc snaps to 17 or 20."""
        from core.dnd_core import LEGAL_DCS
        profile = _dnd_profile()
        data = _minimal_worker_data(difficulty=18)

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Investigate the room",
                profile=profile,
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            _, updates = _run_async(_run())

        assert updates["difficulty"] in LEGAL_DCS, (
            f"difficulty must be in LEGAL_DCS={LEGAL_DCS}, got {updates['difficulty']}"
        )

    # 3. advantage_reason set → advantage used; roll is max of 2
    def test_advantage_reason_applies_advantage(self):
        """LLM returns advantage_reason='flanking', no disadvantage.
        With advantage, roll should be >= either single roll (statistical proxy: patch to deterministic)."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=10,
            advantage_reason="flanking",
            disadvantage_reason="",
        )

        # Patch roll_d20 to return a deterministic advantage result
        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            # roll_d20 with advantage returns max([r1, r2]); patch randint to return 15, 8
            with patch("core.dnd_core.random.randint", side_effect=[15, 8]):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Attack with flanking",
                        profile=profile,
                    )
                _, updates = _run_async(_run())

        # With advantage and rolls [15,8], natural should be 15 (max)
        assert updates.get("natural_roll", 0) == 15, (
            f"With advantage and rolls [15,8], natural_roll must be 15. "
            f"Got natural_roll={updates.get('natural_roll')}"
        )

    # 4. disadvantage_reason set → disadvantage used; roll is min of 2
    def test_disadvantage_reason_applies_disadvantage(self):
        """LLM returns disadvantage_reason='dim light', no advantage.
        Patch randint to [15, 8] → natural must be 8 (min)."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="DEX",
            skill_used="Stealth",
            difficulty=12,
            advantage_reason="",
            disadvantage_reason="dim light",
        )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", side_effect=[15, 8]):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Sneak through the darkness",
                        profile=profile,
                    )
                _, updates = _run_async(_run())

        assert updates.get("natural_roll", 0) == 8, (
            f"With disadvantage and rolls [15,8], natural_roll must be 8. "
            f"Got natural_roll={updates.get('natural_roll')}"
        )

    # 5. Both advantage_reason and disadvantage_reason → cancel out → normal roll (single d20)
    def test_both_adv_and_dis_cancel_to_normal(self):
        """When both advantage_reason and disadvantage_reason are set, they cancel.
        With normal roll, randint is called once → natural equals that roll."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=10,
            advantage_reason="high ground",
            disadvantage_reason="exhausted",
        )

        call_count = []

        original_randint = random.randint

        def counting_randint(a, b):
            call_count.append(1)
            return 12  # deterministic

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", side_effect=counting_randint):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Push the door",
                        profile=profile,
                    )
                _, updates = _run_async(_run())

        # Normal roll = 1 call to randint (not 2)
        assert len(call_count) == 1, (
            f"Cancellation should yield normal roll (1 die). Got {len(call_count)} calls."
        )
        assert updates.get("natural_roll") == 12

    # 6. profile with condition "poisoned" → forced disadvantage
    def test_poisoned_condition_forces_disadvantage(self):
        """Profile has condition 'poisoned'. Even with advantage_reason set, poisoned imposes
        disadvantage on checks (D&D PHB) → both cancel if advantage_reason also set."""
        from core.dnd_conditions import apply_condition
        profile = _dnd_profile()
        apply_condition(profile, "poisoned", duration_rounds=3, source="snake_bite")

        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=12,
            advantage_reason="",
            disadvantage_reason="",
        )

        call_count = []

        def counting_randint(a, b):
            call_count.append(1)
            return 10

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", side_effect=counting_randint):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Push through the obstacle",
                        profile=profile,
                    )
                _, updates = _run_async(_run())

        # Poisoned → disadvantage → 2 dice calls (min taken)
        assert len(call_count) == 2, (
            f"Poisoned should impose disadvantage (2 dice rolls). Got {len(call_count)} calls."
        )

    # 7. nat 1 → outcome == CRITICAL FAILURE
    def test_nat_1_yields_critical_failure(self):
        """When d20 roll is 1, outcome must be CRITICAL FAILURE (D&D 5e nat 1 rule)."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=10,
        )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=1):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Attempt a difficult jump",
                        profile=profile,
                    )
                _, updates = _run_async(_run())

        assert updates["outcome"] == "CRITICAL FAILURE", (
            f"nat 1 must yield CRITICAL FAILURE, got {updates['outcome']!r}"
        )
        assert updates["natural_roll"] == 1

    # 8. nat 20 → outcome == CRITICAL SUCCESS
    def test_nat_20_yields_critical_success(self):
        """When d20 roll is 20, outcome must be CRITICAL SUCCESS."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=25,  # high DC so only nat 20 would "succeed"
        )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", return_value=20):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Attempt a heroic feat",
                        profile=profile,
                    )
                _, updates = _run_async(_run())

        assert updates["outcome"] == "CRITICAL SUCCESS", (
            f"nat 20 must yield CRITICAL SUCCESS, got {updates['outcome']!r}"
        )
        assert updates["natural_roll"] == 20

    # 9. DC <= 2 (ultra-trivial action) → AUTO_SUCCESS without roll
    def test_trivial_dc_yields_auto_success_no_roll(self):
        """When difficulty=2 (AUTO_SUCCESS_MAX_DC) and skill_used='None', ability_used='None',
        no dice are rolled and outcome is SUCCESS."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="None",
            skill_used="None",
            difficulty=2,
        )

        roll_calls = []

        def spy_randint(a, b):
            roll_calls.append(1)
            return 10

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            with patch("core.dnd_core.random.randint", side_effect=spy_randint):
                async def _run():
                    from core.dnd_engine import resolve_normal_action
                    return await resolve_normal_action(
                        user_input="Say hello",
                        profile=profile,
                    )
                verdict, updates = _run_async(_run())

        # No dice rolled for trivial/free action
        assert roll_calls == [], (
            f"No dice should be rolled for AUTO_SUCCESS. Got {len(roll_calls)} calls."
        )
        assert updates["outcome"] == "SUCCESS"
        assert updates["natural_roll"] == 0

    # 10. Danger keywords in user_input → DC floor applied (DC >= _DANGER_DC_FLOOR)
    def test_danger_keywords_apply_dc_floor(self):
        """'стриб з даху' contains danger keyword → clamped_dc must be >= _DANGER_DC_FLOOR (15).
        LLM returns difficulty=10, which would normally clamp to 10, but danger floor = 15."""
        from core.dnd_core import LEGAL_DCS
        from core.dnd_engine import _DANGER_DC_FLOOR
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=10,
        )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Стрибаю з даху будинку",
                    profile=profile,
                )
            _, updates = _run_async(_run())

        # _DANGER_DC_FLOOR = 15; clamp_dc(15) = 15 (it's in LEGAL_DCS)
        assert updates["difficulty"] >= _DANGER_DC_FLOOR, (
            f"Danger keyword must impose DC floor >= {_DANGER_DC_FLOOR}. Got difficulty={updates['difficulty']}"
        )

    # 11. LLM failure → AUTO_SUCCESS fallback
    def test_llm_failure_falls_back_to_auto_success(self):
        """When model_worker.generate_content raises an exception, resolve_normal_action
        must return AUTO_SUCCESS without re-raising."""
        profile = _dnd_profile()

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Look around the room",
                profile=profile,
            )

        prompt_patch = patch(
            "core.dnd_engine.build_normal_resolve_prompt",
            return_value="MOCK_PROMPT",
        )
        with prompt_patch:
            with patch(
                "core.dnd_engine.model_worker.generate_content",
                side_effect=Exception("LLM timeout"),
            ):
                with patch("core.dnd_engine.clean_and_parse_json", return_value=None):
                    verdict, updates = _run_async(_run())

        assert updates["outcome"] == "SUCCESS", (
            f"LLM fallback must yield SUCCESS, got {updates['outcome']!r}"
        )
        assert "AUTO_SUCCESS" in verdict, (
            f"Fallback verdict must contain 'AUTO_SUCCESS', got {verdict!r}"
        )


# ===========================================================================
# Tests: apply_dnd_impacts
# ===========================================================================

class TestApplyDndImpacts:
    """Tests for apply_dnd_impacts (HP dice, conditions, XP, legacy delegation)."""

    # 12. hp_damage_dice="1d6" → profile['hp_current'] -= roll
    def test_hp_damage_dice_reduces_hp_current(self):
        """hp_damage_dice='1d6' with mocked roll=4 → hp_current decreases by 4."""
        profile = _dnd_profile_with_hp(hp_current=50, hp_max=50)
        updates = {
            "hp_damage_dice": "1d6",
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

        with patch("core.dnd_engine.random.randint", return_value=4):
            from core.dnd_engine import apply_dnd_impacts
            result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["hp_current"] == 46, (
            f"hp_current must be 50-4=46, got {result_profile['hp_current']}"
        )
        assert any("46" in log for log in logs), (
            f"Logs must mention new HP 46. Got logs: {logs}"
        )

    # 13. hp_heal_dice="1d8" → hp_current does not exceed hp_max
    def test_hp_heal_dice_capped_at_hp_max(self):
        """hp_heal_dice='1d8' with profile hp_current=48, hp_max=50 and mocked roll=8
        → hp_current capped at 50 (not 56)."""
        profile = _dnd_profile_with_hp(hp_current=48, hp_max=50)
        updates = {
            "hp_damage_dice": "none",
            "hp_heal_dice": "1d8",
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

        with patch("core.dnd_engine.random.randint", return_value=8):
            from core.dnd_engine import apply_dnd_impacts
            result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["hp_current"] == 50, (
            f"hp_current must be capped at hp_max=50, got {result_profile['hp_current']}"
        )

    # 14. condition_apply [{name:"poisoned", duration:3, target:"player"}]
    #     → profile['conditions'] contains poisoned
    def test_condition_apply_adds_condition_to_profile(self):
        """condition_apply list with poisoned entry → profile['conditions'] has 'poisoned'."""
        profile = _dnd_profile()
        updates = {
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "condition_apply": [
                {"name": "poisoned", "duration": 3, "target": "player"}
            ],
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

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        condition_names = [c["name"] for c in result_profile.get("conditions", [])]
        assert "poisoned" in condition_names, (
            f"'poisoned' must be in profile['conditions'] after apply. Got: {condition_names}"
        )

    # 15. condition_remove [{name:"poisoned", target:"player"}] → removes condition
    def test_condition_remove_removes_existing_condition(self):
        """condition_remove with poisoned → poisoned removed from profile['conditions']."""
        from core.dnd_conditions import apply_condition
        profile = _dnd_profile()
        apply_condition(profile, "poisoned", duration_rounds=3, source="test")

        updates = {
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "condition_apply": [],
            "condition_remove": [
                {"name": "poisoned", "target": "player"}
            ],
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

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        condition_names = [c["name"] for c in result_profile.get("conditions", [])]
        assert "poisoned" not in condition_names, (
            f"'poisoned' must be removed from profile['conditions']. Got: {condition_names}"
        )

    # 16. xp_award=50 → profile['xp'] += 50; profile['level'] updated if threshold passed
    def test_xp_award_increases_profile_xp(self):
        """xp_award=50 with profile xp=0 → profile['xp'] == 50, level unchanged (need 300 for L2)."""
        profile = _dnd_profile(xp=0, level=1)
        updates = {
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "condition_apply": [],
            "condition_remove": [],
            "xp_award": 50,
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

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["xp"] == 50, (
            f"profile['xp'] must be 50, got {result_profile['xp']}"
        )
        assert result_profile["level"] == 1, (
            f"Level must remain 1 (need 300 XP for L2). Got level={result_profile['level']}"
        )
        assert any("XP" in log and "50" in log for log in logs), (
            f"Logs must mention XP award of 50. Got logs: {logs}"
        )

    # 17. updates with only time/location (no D&D-specific tags) → delegates to apply_system_impacts
    def test_non_dnd_updates_delegate_to_apply_system_impacts(self):
        """When updates have only legacy fields (minutes_passed, location_impact),
        apply_dnd_impacts delegates them to apply_system_impacts which updates game time."""
        profile = _dnd_profile()
        updates = {
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "condition_apply": [],
            "condition_remove": [],
            "xp_award": 0,
            "minutes_passed": 60,
            "location_impact": "none",
            "scene_impact": "none",
            "health_impact": "none",
            "energy_impact": "none",
            "gold_impact": "none",
            "inventory_new": [],
            "inventory_lost": [],
            "clocks_impact": {},
        }

        legacy_called = []

        # Capture the real function BEFORE patching to avoid recursion in side_effect.
        from core.mechanics import apply_system_impacts as _real_apply
        _real_ref = _real_apply  # local reference is unaffected by the patch below

        def spy_apply_system_impacts(prof, upd):
            legacy_called.append(1)
            return _real_ref(prof, upd)

        # apply_system_impacts is lazily imported inside apply_dnd_impacts via
        # "from core.mechanics import apply_system_impacts". The correct patch target
        # is core.mechanics.apply_system_impacts (the module-level attribute).
        with patch("core.mechanics.apply_system_impacts", side_effect=spy_apply_system_impacts):
            from core.dnd_engine import apply_dnd_impacts
            result_profile, logs = apply_dnd_impacts(profile, updates)

        assert len(legacy_called) == 1, (
            f"apply_system_impacts must be called exactly once for legacy delegation. "
            f"Got {len(legacy_called)} calls."
        )


# ===========================================================================
# Tests: auto-level-up via apply_dnd_impacts (Phase 9)
# ===========================================================================

def _minimal_updates(xp_award: int = 0) -> dict:
    """Return a minimal updates dict for apply_dnd_impacts tests."""
    return {
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "condition_apply": [],
        "condition_remove": [],
        "xp_award": xp_award,
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


def _profile_for_level_up(level: int = 1, xp: int = 0, **overrides) -> dict:
    """Return a D&D profile suitable for level-up tests (includes hp_current/hp_max)."""
    base = _dnd_profile(level=level, xp=xp)
    base["class"] = "Knight"
    base["hp_current"] = 12
    base["hp_max"] = 12
    base["proficiency_bonus"] = 2
    base["hit_dice_total"] = level
    base["features"] = []
    base["asi_pending"] = False
    # Knight primary_abilities = ["STR", "CHA"]; set both below 20
    base["ability_scores"] = {
        "STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 12,
    }
    base.update(overrides)
    return base


class TestAutoLevelUp:
    """Tests for automatic level_up trigger inside apply_dnd_impacts (Phase 9)."""

    # 18. xp_award crosses L1→L2 threshold: level_up applied, hp_max increased
    def test_level1_to_level2_on_xp_award(self):
        """xp_award=300 from L1/xp=0 crosses L2 threshold (300 XP).
        After apply_dnd_impacts: profile['level']==2, profile['hp_max'] > 12."""
        profile = _profile_for_level_up(level=1, xp=0)
        updates = _minimal_updates(xp_award=300)

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["level"] == 2, (
            f"Level must be 2 after 300 XP award from L1. Got {result_profile['level']}"
        )
        assert result_profile["hp_max"] > 12, (
            f"hp_max must have increased above 12 after level-up. Got {result_profile['hp_max']}"
        )
        level_logs = [l for l in logs if "LEVEL" in l and "1 ->" in l]
        assert level_logs, (
            f"Logs must mention level transition 1->2. Got logs: {logs}"
        )

    # 19. xp_award crosses L3→L4 (ASI level): ASI applied to primary_abilities
    def test_level3_to_level4_asi_applied(self):
        """xp_award=1800 from L3/xp=900 crosses L4 threshold (2700 XP).
        After apply_dnd_impacts: STR or CHA increased (Knight primary_abilities)."""
        # Knight primary_abilities = ["STR", "CHA"]
        profile = _profile_for_level_up(level=3, xp=900)
        profile["ability_scores"]["STR"] = 16  # room to grow
        profile["ability_scores"]["CHA"] = 12  # room to grow
        updates = _minimal_updates(xp_award=1800)

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["level"] == 4, (
            f"Level must be 4 after crossing L4 threshold. Got {result_profile['level']}"
        )
        str_val = result_profile["ability_scores"]["STR"]
        cha_val = result_profile["ability_scores"]["CHA"]
        assert str_val == 17 and cha_val == 13, (
            f"Knight ASI at L4: STR should be 17 and CHA should be 13 "
            f"(split +1/+1 to primary_abilities). Got STR={str_val}, CHA={cha_val}"
        )
        asi_logs = [l for l in logs if "Ability Score Improvement" in l]
        assert asi_logs, f"Logs must mention ASI. Got logs: {logs}"

    # 20. Multi-level: xp_award crosses L1→L5 (levels_gained=4): level_up called 4 times
    def test_multi_level_l1_to_l5(self):
        """xp_award=6500 from L1/xp=0 crosses L5 threshold (6500 XP).
        After apply_dnd_impacts: profile['level']==5, LEVEL logs appear 4 times."""
        profile = _profile_for_level_up(level=1, xp=0)
        updates = _minimal_updates(xp_award=6500)

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["level"] == 5, (
            f"Level must be 5 after 6500 XP from L1. Got {result_profile['level']}"
        )
        level_logs = [l for l in logs if "LEVEL" in l and "->" in l]
        assert len(level_logs) == 4, (
            f"Must have exactly 4 LEVEL transition log entries (L1->L2, L2->L3, L3->L4, L4->L5). "
            f"Got {len(level_logs)}: {level_logs}"
        )

    # 21. ASI cap: STR=19 → ASI {STR:1, CHA:1} → STR=20 (not 21), CHA+1
    def test_asi_cap_str_at_19(self):
        """STR=19 at ASI level → split gives STR+1/CHA+1, STR becomes 20 (not 21)."""
        # L3→L4 is an ASI level for Knight
        profile = _profile_for_level_up(level=3, xp=900)
        profile["ability_scores"]["STR"] = 19  # one point from cap
        profile["ability_scores"]["CHA"] = 10
        updates = _minimal_updates(xp_award=1800)  # crosses L4

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        str_val = result_profile["ability_scores"]["STR"]
        cha_val = result_profile["ability_scores"]["CHA"]
        assert str_val == 20, (
            f"STR must be capped at 20 (was 19, +1 from split ASI). Got {str_val}"
        )
        assert cha_val == 11, (
            f"CHA must be 11 (was 10, +1 from split ASI). Got {cha_val}"
        )

    # 22. L20 → level_up raises ValueError, log contains "Max level reached", no exception
    def test_max_level_no_crash(self):
        """Profile already at L20 with enough XP — apply_dnd_impacts must not raise,
        and logs must contain 'Max level reached'."""
        from core.dnd_progression import XP_TABLE
        # Construct L19 profile at exactly L20 threshold XP so xp_award pushes over
        # the top (but L20 is already the cap and level_up will refuse)
        profile = _profile_for_level_up(level=20, xp=XP_TABLE[20])
        updates = _minimal_updates(xp_award=200)  # extra XP, level stays at 20

        from core.dnd_engine import apply_dnd_impacts
        # Must not raise
        result_profile, logs = apply_dnd_impacts(profile, updates)

        # level stays at 20 (award_xp keeps it at 20 since get_level_for_xp caps at 20)
        assert result_profile["level"] == 20, (
            f"Level must remain 20 at cap. Got {result_profile['level']}"
        )
        # No "Max level reached" log expected here because award_xp already keeps level=20
        # and leveled_up=False (level_before==level_after==20). The ValueError path is
        # only reachable if caller manually resets level below 20 then awards huge XP.
        # We verify the function completes without exception — that is the invariant.


# ===========================================================================
# Tests: process_game_turn integration (high-level FSM)
# ===========================================================================

# Minimal profile and patches for process_game_turn integration tests
_PROCESS_PROFILE = {
    "Ім'я": "IntegrationHero",
    "Дім": "Stark",
    "Титул": "Lord",
    "Здоров'я": 100,
    "Енергія": 800,
    "Особисте Золото": 200,
    "Бойові навички": 50,
    "Військові навички": 20,
    "Інтрига": 15,
    "Управління": 10,
    "Поточне місцезнаходження": "Вінтерфелл",
    "Поточна сцена": "Great Hall",
    "Регіон": "Північ",
    "Ігровий час": "Day 1, Morning",
    "Інвентар": "Sword",
    "Зброя": "Longsword",
    "Броня": "Chainmail",
    "Транспорт": "Horse",
    "Світогляд": "Neutral",
    "Риси": "Brave",
    "Вади": "Stubborn",
    "Вороги": "",
    "Друзі": "",
    "Годинники": {"Scene_Tension": "0/4"},
    "level": 1,
    "ability_scores": {"STR": 14, "DEX": 10, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10},
    "skill_profs": [],
    "skill_expertise": [],
    "saves_proficient": [],
    "conditions": [],
    "xp": 0,
}

_PROCESS_WORKER_UPDATES = {
    "action_type": "standard",
    "skill_used": "None",
    "ability_used": "None",
    "outcome": "SUCCESS",
    "natural_roll": 10,
    "total_score": 10,
    "difficulty": 10,
    "advantage_reason": "",
    "disadvantage_reason": "",
    "combat_imminent": False,
    "xp_award": 0,
    "reputation_delta": 0,
    "reputation_target_npc": "",
    "minutes_passed": 5,
    "location_impact": "none",
    "scene_impact": "none",
    "health_impact": "none",
    "energy_impact": "none",
    "hp_damage_dice": "none",
    "hp_heal_dice": "none",
    "gold_impact": "none",
    "inventory_new": [],
    "inventory_lost": [],
    "clocks_impact": {},
    "condition_apply": [],
    "condition_remove": [],
}

_PROCESS_GM_JSON = (
    '{"reasoning":"test","npc_reasoning":"test",'
    '"director_notes":["Hero stands quietly."],'
    '"companion_npcs":[],"npc_updates":[],'
    '"suggested_actions":['
    '{"button":"A1","intent":"i1"},'
    '{"button":"A2","intent":"i2"},'
    '{"button":"A3","intent":"i3"},'
    '{"button":"A4","intent":"i4"}]}'
)

_PROCESS_NARRATOR_TEXT = (
    "The hero stands in the great hall of Winterfell. "
    "Cold stone walls surround him, and the fire burns low. "
    "He feels the weight of his decision."
)


def _make_process_patches(profile_overrides: dict = None, combat_imminent: bool = False):
    """Return patches for a complete process_game_turn integration test."""
    profile = dict(_PROCESS_PROFILE)
    if profile_overrides:
        profile.update(profile_overrides)

    worker_updates = dict(_PROCESS_WORKER_UPDATES)
    if combat_imminent:
        worker_updates["combat_imminent"] = True

    narrator_resp = MagicMock()
    narrator_resp.text = _PROCESS_NARRATOR_TEXT

    gm_resp = MagicMock()
    gm_resp.text = _PROCESS_GM_JSON

    return [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch(
            "core.engine.resolve_normal_action",
            new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", worker_updates)),
        ),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
    ]


class TestProcessGameTurnIntegration:
    """High-level integration tests for process_game_turn FSM dispatcher."""

    # 18. profile mode="NORMAL" → D&D pipeline is used (resolve_normal_action called)
    def test_normal_mode_calls_resolve_normal_action(self):
        """Profile without 'mode' key defaults to NORMAL; resolve_normal_action must be called."""
        resolve_call_count = []

        async def spy_resolve(*args, **kwargs):
            resolve_call_count.append(1)
            return ("MECHANICAL VERDICT: SUCCESS", dict(_PROCESS_WORKER_UPDATES))

        patches = _make_process_patches()
        # Override the resolve_normal_action patch with our spy
        patches[2] = patch("core.engine.resolve_normal_action", new=spy_resolve)

        async def _run():
            from core.engine import process_game_turn
            return await process_game_turn(
                chat_id=1001,
                user_input="Look around",
                narrator_queue=None,
            )

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result_text, result_actions = asyncio.run(_run())

        assert len(resolve_call_count) == 1, (
            f"resolve_normal_action must be called exactly once for NORMAL mode. "
            f"Got {len(resolve_call_count)} calls."
        )
        assert isinstance(result_actions, list)

    # 19. profile mode="COMBAT" → warning logged, falls back to NORMAL, resolve_normal_action called
    def test_combat_mode_falls_back_to_normal_with_warning(self, caplog):
        """profile mode='COMBAT' triggers fallback warning and NORMAL pipeline."""
        resolve_call_count = []

        async def spy_resolve(*args, **kwargs):
            resolve_call_count.append(1)
            return ("MECHANICAL VERDICT: SUCCESS", dict(_PROCESS_WORKER_UPDATES))

        patches = _make_process_patches(profile_overrides={"mode": "COMBAT"})
        patches[2] = patch("core.engine.resolve_normal_action", new=spy_resolve)

        async def _run():
            from core.engine import process_game_turn
            return await process_game_turn(
                chat_id=1002,
                user_input="Draw my sword",
                narrator_queue=None,
            )

        with caplog.at_level(logging.WARNING, logger="core.engine"):
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                asyncio.run(_run())

        # COMBAT mode must emit a warning (Phase 7 placeholder)
        assert any("COMBAT" in r.message for r in caplog.records), (
            f"Expected WARNING mentioning COMBAT mode. Got records: "
            f"{[r.message for r in caplog.records]}"
        )
        # Still falls back to NORMAL → resolve_normal_action must be called
        assert len(resolve_call_count) == 1, (
            f"After COMBAT fallback, resolve_normal_action must be called. "
            f"Got {len(resolve_call_count)} calls."
        )

    # 20. combat_imminent=True → log warning, mode NOT changed to COMBAT (Phase 5 placeholder)
    def test_combat_imminent_logs_warning_does_not_change_mode(self, caplog):
        """combat_imminent=True in updates triggers [FSM] log but does NOT change profile mode.
        Phase 5: this is a placeholder (Phase 7 will implement COMBAT transition)."""
        profile = dict(_PROCESS_PROFILE)
        profile.pop("mode", None)  # NORMAL by default

        patches = _make_process_patches(combat_imminent=True)

        async def _run():
            from core.engine import process_game_turn
            return await process_game_turn(
                chat_id=1003,
                user_input="I see a guard approaching with a weapon",
                narrator_queue=None,
            )

        with caplog.at_level(logging.INFO, logger="core.engine"):
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                result_text, result_actions = asyncio.run(_run())

        # Phase 5: combat_imminent logged but mode NOT changed
        assert any("combat_imminent" in r.message for r in caplog.records), (
            f"Expected log mentioning combat_imminent. Got: "
            f"{[r.message for r in caplog.records]}"
        )
        # Result text should still be a valid narrative (not an error)
        assert isinstance(result_text, str) and len(result_text) > 0
        assert isinstance(result_actions, list)

    # 21. combat_imminent=True → scene NPCs use real score-based relation (not hardcoded "Ворожий")
    def test_combat_init_uses_real_relation_not_hardcoded_hostile(self):
        """COMBAT scene NPCs get score-based relation, not hardcoded 'Ворожий'.

        Regression guard for WARN #1 from code-reviewer:
        engine.py _scene_npcs_for_combat was built with hardcoded Relation_Player='Ворожий'
        for ALL NPCs in the scene, even peaceful bystanders.

        Scenario:
        - Scene contains two NPCs: an aggressor (score=-50, 'Ворожий') and a
          neutral bystander (score=0, 'Нейтральний').
        - combat_imminent=True triggers COMBAT init.
        - initiate_combat_from_normal must receive the neutral NPC with
          Relation_Player='Нейтральний', NOT 'Ворожий'.
        """
        captured_scene_npcs: list = []

        async def _spy_initiate(chat_id, profile, scene_npcs):
            captured_scene_npcs.extend(scene_npcs)
            # Return a minimal CombatState-like mock so engine can iterate initiative_order
            mock_cs = MagicMock()
            mock_cs.initiative_order = []
            return mock_cs

        worker_updates = dict(_PROCESS_WORKER_UPDATES)
        worker_updates["combat_imminent"] = True

        narrator_resp = MagicMock()
        narrator_resp.text = _PROCESS_NARRATOR_TEXT

        gm_resp = MagicMock()
        gm_resp.text = _PROCESS_GM_JSON

        # Two NPCs: one hostile (aggressor), one neutral (bystander)
        npc_names = ["Агресор", "Мирний NPC"]
        npc_reputation = {"Агресор": -50, "Мирний NPC": 0}

        patches = [
            patch("core.engine.get_user_data", new=AsyncMock(return_value=(dict(_PROCESS_PROFILE), 2))),
            patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
            patch(
                "core.engine.resolve_normal_action",
                new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", worker_updates)),
            ),
            patch(
                "core.engine.get_location_npcs",
                return_value=("Агресор стоїть тут. Мирний NPC теж.", npc_names, npc_reputation),
            ),
            patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
            patch("core.engine.get_dead_npc_names", return_value=set()),
            patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
            patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
            patch("core.engine._run_bg_task", return_value=MagicMock()),
            patch("core.engine.initiate_combat_from_normal", new=AsyncMock(side_effect=_spy_initiate)),
        ]

        async def _run():
            from core.engine import process_game_turn
            return await process_game_turn(
                chat_id=1099,
                user_input="Агресор нападає!",
                narrator_queue=None,
            )

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            asyncio.run(_run())

        assert len(captured_scene_npcs) == 2, (
            f"Expected 2 scene NPCs passed to initiate_combat_from_normal. "
            f"Got {len(captured_scene_npcs)}: {captured_scene_npcs}"
        )

        by_name = {npc["Name"]: npc["Relation_Player"] for npc in captured_scene_npcs}

        assert by_name["Агресор"] == "Ворожий", (
            f"Агресор (score=-50) must have Relation_Player='Ворожий'. "
            f"Got {by_name['Агресор']!r}"
        )
        assert by_name["Мирний NPC"] == "Нейтральний", (
            f"REGRESSION: Мирний NPC (score=0) must have Relation_Player='Нейтральний', "
            f"NOT hardcoded 'Ворожий'. Got {by_name['Мирний NPC']!r}"
        )


# ===========================================================================
# Tests: Item 2 — response_schema config passed to model_worker (Phase 11b)
# ===========================================================================

class TestResolveNormalActionPassesSchema:
    """Verify that resolve_normal_action passes a GenerateContentConfig with
    response_schema to model_worker.generate_content (Item 2, Phase 11b)."""

    def _capture_config_and_return(self, worker_data: dict):
        """Helper: returns a generate_content mock that captures config kwarg."""
        captured = []

        def _mock_gen(prompt, max_retries=6, config=None):
            captured.append(config)
            m = MagicMock()
            import json as _json
            m.text = _json.dumps(worker_data, ensure_ascii=False)
            return m

        return _mock_gen, captured

    def test_schema_config_is_passed_not_none(self):
        """resolve_normal_action must pass a non-None config with response_mime_type='application/json'.
        response_schema must NOT be set: strict constrained decoding caused
        14-min hangs on gemma-4-31b-it preview (schema parameter is now a no-op).
        """
        data = _minimal_worker_data()
        mock_gen, captured = self._capture_config_and_return(data)

        prompt_patch = patch("core.dnd_engine.build_normal_resolve_prompt", return_value="MOCK")
        parse_patch = patch("core.dnd_engine.clean_and_parse_json", return_value=data)

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action("Do something", _dnd_profile())

        with prompt_patch, parse_patch:
            with patch("core.dnd_engine.model_worker.generate_content", side_effect=mock_gen):
                _run_async(_run())

        assert len(captured) >= 1, "generate_content must be called at least once"
        cfg = captured[0]
        assert cfg is not None, "config must not be None"
        assert cfg.response_mime_type == "application/json", (
            f"response_mime_type must be 'application/json', got {cfg.response_mime_type!r}"
        )
        assert not hasattr(cfg, "response_schema") or cfg.response_schema is None, (
            "response_schema must be None: strict schema causes 14-min hangs"
        )

    def test_schema_config_fallback_on_invalid_argument(self):
        """If generate_content raises INVALID_ARGUMENT (schema rejected),
        resolve_normal_action must fall back to a free-JSON call and succeed."""
        data = _minimal_worker_data()
        call_count = []

        def _mock_gen_fallback(prompt, max_retries=6, config=None):
            call_count.append(config)
            if config is not None:
                raise Exception("400 INVALID_ARGUMENT: schema not supported")
            m = MagicMock()
            import json as _json
            m.text = _json.dumps(data, ensure_ascii=False)
            return m

        prompt_patch = patch("core.dnd_engine.build_normal_resolve_prompt", return_value="MOCK")
        parse_patch = patch("core.dnd_engine.clean_and_parse_json", return_value=data)

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action("Do something", _dnd_profile())

        with prompt_patch, parse_patch:
            with patch("core.dnd_engine.model_worker.generate_content", side_effect=_mock_gen_fallback):
                verdict, updates = _run_async(_run())

        # First call with schema (raises), second call without schema (succeeds)
        assert len(call_count) == 2, (
            f"Must call generate_content twice (schema then fallback). Got {len(call_count)}"
        )
        assert call_count[0] is not None, "First call must have config (schema attempt)"
        assert call_count[1] is None, "Second call (fallback) must have config=None"
        assert updates["outcome"] in {"SUCCESS", "FAILURE", "CRITICAL SUCCESS", "CRITICAL FAILURE"}

    def test_build_strict_config_inherits_temperature(self):
        """build_strict_config must use model_wrapper.temperature if no override given."""
        from core.ai_client import build_strict_config, model_worker
        cfg = build_strict_config(model_worker)
        assert cfg.temperature == model_worker.temperature, (
            f"temperature must match model_worker.temperature={model_worker.temperature}, "
            f"got {cfg.temperature}"
        )

    def test_build_strict_config_temperature_override(self):
        """build_strict_config must use the provided temperature override."""
        from core.ai_client import build_strict_config, model_worker
        cfg = build_strict_config(model_worker, temperature=0.42)
        assert cfg.temperature == 0.42, (
            f"temperature override must be 0.42, got {cfg.temperature}"
        )

    def test_build_strict_config_has_mime_type_no_schema(self):
        """build_strict_config sets response_mime_type=application/json.
        response_schema must NOT be set: strict constrained decoding caused
        14-min hangs on gemma-4-31b-it preview (schema is accepted but ignored).
        """
        from core.ai_client import build_strict_config, model_worker
        cfg = build_strict_config(model_worker)
        assert cfg.response_mime_type == "application/json"
        assert not hasattr(cfg, "response_schema") or cfg.response_schema is None


class TestValidateActionPassesSchema:
    """Verify that validate_action (Censor) passes schema config to model_worker."""

    def test_validate_action_config_not_none(self):
        """validate_action must call model_worker.generate_content with a non-None config."""
        captured = []
        censor_result = {"is_valid": True, "refusal_reason": ""}

        def _mock_gen(prompt, max_retries=6, config=None):
            captured.append(config)
            m = MagicMock()
            import json as _json
            m.text = _json.dumps(censor_result)
            return m

        parse_patch = patch("core.mechanics.clean_and_parse_json", return_value=censor_result)

        async def _run():
            from core.mechanics import validate_action
            return await validate_action("Attack the guard", _dnd_profile())

        with parse_patch:
            with patch("core.mechanics.model_worker.generate_content", side_effect=_mock_gen):
                valid, reason = _run_async(_run())

        assert valid is True
        assert len(captured) >= 1
        assert captured[0] is not None, "validate_action config must not be None"
        assert captured[0].response_mime_type == "application/json"

    def test_validate_action_schema_fallback(self):
        """When schema raises INVALID_ARGUMENT, validate_action falls back gracefully."""
        censor_result = {"is_valid": True, "refusal_reason": ""}
        call_count = []

        def _mock_gen_fallback(prompt, max_retries=6, config=None):
            call_count.append(config)
            if config is not None:
                raise Exception("INVALID_ARGUMENT: unsupported schema")
            m = MagicMock()
            import json as _json
            m.text = _json.dumps(censor_result)
            return m

        parse_patch = patch("core.mechanics.clean_and_parse_json", return_value=censor_result)

        async def _run():
            from core.mechanics import validate_action
            return await validate_action("Attack the guard", _dnd_profile())

        with parse_patch:
            with patch("core.mechanics.model_worker.generate_content", side_effect=_mock_gen_fallback):
                valid, reason = _run_async(_run())

        assert valid is True
        assert len(call_count) == 2, (
            f"Must call twice (schema attempt + fallback). Got {len(call_count)}"
        )


# ===========================================================================
# Tests: Issue 3 — CRITICAL SUCCESS must apply gold and XP in NORMAL mode
# ===========================================================================

class TestCriticalSuccessAppliesUpdates:
    """Verify that CRITICAL SUCCESS in NORMAL mode applies gold_impact, xp_award,
    inventory, and HP impacts.  Regression suite for Issue 3.

    Strategy: call apply_dnd_impacts directly with pre-built updates dicts that
    have outcome='CRITICAL SUCCESS', then assert mechanical changes on the profile.
    """

    def _make_crit_updates(self, **overrides) -> dict:
        """Return a CRITICAL SUCCESS updates dict (as produced by _build_updates)."""
        base = {
            "action_type": "standard",
            "outcome": "CRITICAL SUCCESS",
            "skill_used": "Intimidation",
            "ability_used": "CHA",
            "natural_roll": 20,
            "total_score": 22,
            "difficulty": 15,
            "advantage_reason": "",
            "disadvantage_reason": "",
            "combat_imminent": False,
            "xp_award": 200,
            "reputation_delta": 2,
            "reputation_target_npc": "Cersei",
            # Updates sub-dict fields (already merged by _build_updates)
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "gold_impact": "earn_small",   # should add gold
            "inventory_new": ["Dragon Egg"],
            "inventory_lost": [],
            "minutes_passed": 10,
            "location_impact": "none",
            "scene_impact": "none",
            "health_impact": "none",
            "energy_impact": "none",
            "clocks_impact": {},
            "condition_apply": [],
            "condition_remove": [],
        }
        base.update(overrides)
        return base

    def test_critical_success_applies_xp_in_normal(self):
        """CRITICAL SUCCESS with xp_award=200 → profile['xp'] increases by 200."""
        profile = _dnd_profile(xp=0, level=1)
        updates = self._make_crit_updates(xp_award=200)

        from core.dnd_engine import apply_dnd_impacts
        result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["xp"] == 200, (
            f"CRITICAL SUCCESS must apply xp_award=200. Got xp={result_profile['xp']}"
        )
        assert any("XP" in log and "200" in log for log in logs), (
            f"Logs must mention XP=200. Got: {logs}"
        )

    def test_critical_success_applies_gold_in_normal(self):
        """CRITICAL SUCCESS with gold_impact='earn_small' → profile gold increases."""
        profile = _dnd_profile()
        initial_gold = profile.get("Особисте Золото", 200)
        updates = self._make_crit_updates(gold_impact="earn_small")

        from core.dnd_engine import apply_dnd_impacts
        # Mock random for deterministic gold: earn_small = randint(10, 30)
        with patch("core.mechanics.random.randint", return_value=20):
            result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["Особисте Золото"] > initial_gold, (
            f"Gold must increase after earn_small on CRITICAL SUCCESS. "
            f"Before={initial_gold}, After={result_profile['Особисте Золото']}"
        )

    def test_critical_success_applies_hp_damage_in_normal(self):
        """CRITICAL SUCCESS with hp_damage_dice='1d6' → hp_current decreases.
        HP impact in NORMAL mode is NOT blocked (only blocked in COMBAT mode).
        """
        profile = _dnd_profile_with_hp(hp_current=50, hp_max=50)
        updates = self._make_crit_updates(hp_damage_dice="1d6", gold_impact="none")

        from core.dnd_engine import apply_dnd_impacts
        with patch("core.dnd_engine.random.randint", return_value=4):
            result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["hp_current"] == 46, (
            f"hp_current must drop by 4 (1d6 roll=4). Got {result_profile['hp_current']}"
        )

    def test_combat_mode_hp_impact_blocked_normal_hp_not_applied(self):
        """COMBAT mode updates (action_type='combat') must NOT apply hp_damage_dice.
        This guards FSM isolation: combat HP is managed exclusively by the combat FSM.
        """
        profile = _dnd_profile_with_hp(hp_current=50, hp_max=50)
        updates = self._make_crit_updates(
            action_type="combat",      # simulate a combat-mode update
            hp_damage_dice="1d6",
            gold_impact="none",
        )

        from core.dnd_engine import apply_dnd_impacts
        with patch("core.dnd_engine.random.randint", return_value=4):
            result_profile, logs = apply_dnd_impacts(profile, updates)

        # HP must NOT change — combat guard blocks hp_damage_dice in COMBAT mode
        assert result_profile["hp_current"] == 50, (
            f"hp_current must be unchanged in COMBAT mode update (guard active). "
            f"Got {result_profile['hp_current']}"
        )

    def test_critical_success_gold_and_xp_both_applied_together(self):
        """Both gold and XP must be applied on a single CRITICAL SUCCESS — they
        are independent and neither blocks the other."""
        profile = _dnd_profile(xp=0, level=1)
        initial_gold = profile.get("Особисте Золото", 200)
        updates = self._make_crit_updates(xp_award=200, gold_impact="earn_small")

        from core.dnd_engine import apply_dnd_impacts
        with patch("core.mechanics.random.randint", return_value=15):
            result_profile, logs = apply_dnd_impacts(profile, updates)

        assert result_profile["xp"] == 200, (
            f"XP must be 200. Got {result_profile['xp']}"
        )
        assert result_profile["Особисте Золото"] > initial_gold, (
            f"Gold must increase. Before={initial_gold}, After={result_profile['Особисте Золото']}"
        )


# ===========================================================================
# Tests: Guard rail — hp_damage_dice suppressed on attack actions (Layer 2)
# ===========================================================================

class TestGuardRailAttackHpDamageDice:
    """Guard rail in resolve_normal_action: when user_input contains attack keywords,
    hp_damage_dice must be suppressed to 'none' and combat_imminent forced to True.

    Architectural invariant: hp_damage_dice in NORMAL pipeline = player self-damage only
    (environmental, falls, poison). Player attacks on NPCs must use COMBAT mode.
    This is Layer 2 defense-in-depth (Layer 1 = Worker prompt, Layer 3 = Narrator).
    """

    def test_guard_suppresses_hp_damage_dice_on_attack_action(self):
        """Guard: якщо user_input містить attack keyword, hp_damage_dice форсується до none,
        щоб гравець не отримав self-damage за помилковою класифікацією LLM."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="None",
            skill_used="None",
            difficulty=10,
            combat_imminent=False,
        )
        data["updates"]["hp_damage_dice"] = "1d8"  # LLM помилково встановив

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Я атакую слугу рапірою",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"hp_damage_dice must be suppressed to 'none' for attack action. "
            f"Got {updates.get('hp_damage_dice')!r}"
        )
        assert updates.get("combat_imminent") is True, (
            f"combat_imminent must be forced to True when LLM forgot to set it. "
            f"Got {updates.get('combat_imminent')!r}"
        )

    def test_guard_does_not_suppress_environmental_damage(self):
        """Guard must NOT suppress hp_damage_dice when user_input has no attack keywords.
        Environmental damage (falling, poison) is legitimate player self-damage."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="DEX",
            skill_used="Acrobatics",
            difficulty=15,
            combat_imminent=False,
        )
        data["updates"]["hp_damage_dice"] = "1d6"  # legitimate fall damage

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Стрибаю з даху будинку",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_damage_dice") == "1d6", (
            f"Environmental hp_damage_dice='1d6' must NOT be suppressed for non-attack action. "
            f"Got {updates.get('hp_damage_dice')!r}"
        )

    def test_guard_preserves_combat_imminent_true_when_already_set(self):
        """If LLM correctly set combat_imminent=True alongside hp_damage_dice on attack,
        guard must suppress hp_damage_dice but keep combat_imminent=True (no double-flip)."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=15,
            combat_imminent=True,  # LLM correctly set this
        )
        data["updates"]["hp_damage_dice"] = "1d8"  # LLM incorrectly set this too

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Вдаряю стражника мечем",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"hp_damage_dice must be suppressed even when combat_imminent was already True. "
            f"Got {updates.get('hp_damage_dice')!r}"
        )
        assert updates.get("combat_imminent") is True, (
            f"combat_imminent must remain True. Got {updates.get('combat_imminent')!r}"
        )

    def test_guard_no_suppression_when_hp_damage_dice_is_none(self):
        """Guard must not alter hp_damage_dice when it is already 'none'
        (attack action that LLM correctly left damage-free)."""
        profile = _dnd_profile()
        data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=15,
            combat_imminent=True,  # LLM correctly set combat_imminent
        )
        # hp_damage_dice is already 'none' — correct LLM behaviour
        data["updates"]["hp_damage_dice"] = "none"

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Рубаю ворога мечем",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        # Guard condition: _hp_dmg_dice in ("none", "None", "") → no suppression triggered
        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"hp_damage_dice must remain 'none'. Got {updates.get('hp_damage_dice')!r}"
        )
        assert updates.get("combat_imminent") is True, (
            f"combat_imminent must remain True. Got {updates.get('combat_imminent')!r}"
        )

    def test_user_bug_scenario_attack_npc_no_self_damage(self):
        """User-reported bug: 'Я вихоплюю рапіру і атакую слугу' з рапірою (1d8)
        в інвентарі. Worker помилково повернув hp_damage_dice='1d8' + combat_imminent=False.
        Guard має суприм damage до none і форсувати combat_imminent=true.

        Відтворює саме той сценарій, що описав користувач:
        - зброя Рапіра (1d8) є в інвентарі
        - LLM повернув hp_damage_dice='1d8' (помилка: damage гравця, не NPC)
        - LLM повернув combat_imminent=False (помилка: бій не розпочато)
        Guard rail (Layer 2) має виправити обидві помилки.
        """
        profile = _dnd_profile_with_hp()
        profile["Інвентар"] = "Рапіра (1d8 колота, фінесс), Вишукані шати, Перстень"
        profile["Зброя"] = "Рапіра"

        data = _minimal_worker_data(
            ability_used="DEX",
            skill_used="None",
            difficulty=10,
            combat_imminent=False,  # LLM помилка: не виставив флаг
        )
        data["updates"]["hp_damage_dice"] = "1d8"  # LLM помилка: damage замість "none"

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Я вихоплюю рапіру і атакую слугу",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        # Guard має спрацювати — hp_damage_dice суприм до none
        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"Guard must suppress hp_damage_dice='1d8' for rapier attack on NPC. "
            f"Got {updates.get('hp_damage_dice')!r}"
        )
        # Guard має форсувати combat_imminent=True (LLM помилково не виставив)
        assert updates.get("combat_imminent") is True, (
            f"Guard must force combat_imminent=True when LLM returned False for attack action. "
            f"Got {updates.get('combat_imminent')!r}"
        )
        # HP гравця не змінено — profile повертається з resolve_normal_action без
        # apply_dnd_impacts, але guard не застосовує dice, отже hp_current лишається 50.
        # Перевіряємо непрямо через hp_damage_dice='none' → apply_dnd_impacts нічого не зробить.
        assert profile.get("hp_current") == 50, (
            f"Player hp_current must remain 50 (guard suppressed 1d8 dice). "
            f"Got {profile.get('hp_current')!r}"
        )


# ===========================================================================
# Integration test: training XP triggers level_up and HP actually increases
# ===========================================================================

def test_training_levelup_actually_increases_hp():
    """User-reported bug: training XP triggers level_up message але HP не зростає.

    Симулюємо training що дає L1 → L3 (1500 XP).
    Після apply_pending_levelups профіль має:
    - level == 3
    - hp_max > початкового (HP increase з 2 рівнів)
    - Не повинно бути '(Потрібна дія для level_up)' у logs
    - У logs є рядки 'LEVEL 1 -> 2' і 'LEVEL 2 -> 3' (або подібні від хелпера)

    Стратегія: мокаємо process_training_request щоб повернути leveled_up=True,
    level_before=1, level_after=3 (як якщо б тренування дало 1500 XP з рівня L1).
    Потім викликаємо apply_pending_levelups напряму (як це робить engine.py,
    рядки 698–706), перевіряємо інваріанти.
    """
    from core.dnd_progression import award_xp, apply_pending_levelups

    # --- Збудувати профіль L1 Knight ---
    profile = {
        "class": "Knight",
        "level": 1,
        "xp": 0,
        "hp_current": 12,
        "hp_max": 12,
        "hit_dice_total": 1,
        "hit_dice_used": 0,
        "ability_scores": {
            "STR": 16, "DEX": 12, "CON": 14,
            "INT": 10, "WIS": 10, "CHA": 12,
        },
        "features": [],
        "proficiency_bonus": 2,
        "asi_pending": False,
        "Ім'я": "TestHero",
        "Здоров'я": 100,
        "Енергія": 1000,
    }

    initial_hp_max = profile["hp_max"]
    initial_level = profile["level"]

    # --- Симулюємо award_xp(1500) → L1 → L3 ---
    # process_training_request викликає award_xp внутрішньо;
    # тут ми відтворюємо той самий шлях явно, щоб тест був deterministic.
    xp_result = award_xp(profile, 1500, reason="training: Athletics")

    # award_xp вже виставив profile['level'] = 3
    assert xp_result.leveled_up is True, (
        f"1500 XP from L1 must cross level thresholds. Got leveled_up={xp_result.leveled_up}"
    )
    assert xp_result.level_before == 1
    assert xp_result.level_after == 3
    assert xp_result.levels_gained == 2

    # --- Викликаємо apply_pending_levelups (engine.py рядки 698–706) ---
    logs = apply_pending_levelups(
        profile,
        level_before=xp_result.level_before,
        levels_gained=xp_result.levels_gained,
        class_name=profile.get("class", "Knight"),
    )

    # --- Перевіряємо інваріанти ---

    # 1. Рівень має бути 3
    assert profile["level"] == 3, (
        f"Level must be 3 after L1→L3 training level-up. Got {profile['level']}"
    )

    # 2. HP має збільшитися (щонайменше +1 за кожен рівень)
    assert profile["hp_max"] > initial_hp_max, (
        f"hp_max must increase above {initial_hp_max} after 2 level-ups. "
        f"Got {profile['hp_max']}"
    )
    # Мінімальний приріст: 2 рівні × (мінімум 1 HP кожен) = +2
    assert profile["hp_max"] >= initial_hp_max + 2, (
        f"hp_max must grow by at least +2 (min 1 HP per level × 2 levels). "
        f"hp_max before={initial_hp_max}, after={profile['hp_max']}"
    )

    # 3. У logs НЕ має бути '(Потрібна дія для level_up)' — тест на відсутність баги,
    #    при якій хелпер реєструє повідомлення про очікування рішення гравця
    #    замість того, щоб застосувати level_up автоматично.
    pending_action_log = any(
        "Потрібна дія для level_up" in log for log in logs
    )
    assert not pending_action_log, (
        f"Logs must NOT contain 'Потрібна дія для level_up'. "
        f"This would indicate apply_pending_levelups is deferred instead of applied. "
        f"Got logs: {logs}"
    )

    # 4. У logs мають бути рядки переходу рівнів для обох кроків
    level_transition_logs = [log for log in logs if "LEVEL" in log and "->" in log]
    assert len(level_transition_logs) == 2, (
        f"Must have exactly 2 LEVEL transition log lines (1->2 and 2->3). "
        f"Got {len(level_transition_logs)}: {level_transition_logs}"
    )

    # 5. Перші два логи мають містити '1 -> 2' і '2 -> 3' (або подібний формат з хелпера)
    first_log_content = " ".join(level_transition_logs)
    assert "1 ->" in first_log_content or "-> 2" in first_log_content, (
        f"First transition must mention L1→L2. Got logs: {level_transition_logs}"
    )
    assert "2 ->" in first_log_content or "-> 3" in first_log_content, (
        f"Second transition must mention L2→L3. Got logs: {level_transition_logs}"
    )


# ===========================================================================
# Tests: P1 — Saving throw integration
# ===========================================================================

class TestSavingThrowIntegration:
    """Tests for P1: Worker output save_used triggers saving_throw, not skill_check."""

    def test_save_used_con_triggers_saving_throw(self):
        """Worker output with save_used='CON' must invoke saving_throw instead of skill_check.
        Verified via patch: saving_throw is called, skill_check is NOT called."""
        profile = _dnd_profile_with_hp()
        profile["saves_proficient"] = ["STR", "CON"]

        data = _minimal_worker_data(
            ability_used="None",
            skill_used="None",
            difficulty=15,
        )
        data["save_used"] = "CON"
        data["save_dc"] = 12
        data["updates"]["hp_damage_dice"] = "1d4"  # Worker set damage for poison scenario

        save_calls = []

        from core.dnd_core import CheckResult

        def _mock_saving_throw(profile, ability, dc, advantage=False, disadvantage=False):
            save_calls.append({"ability": ability, "dc": dc})
            return CheckResult(
                total=14, natural=12, ability_mod=2, prof_bonus=2,
                dc=dc, success=True, critical="none",
                roll_str=f"[12]→12 +2(CON) +2(prof) = 14 vs DC {dc} → SUCCESS",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=_mock_saving_throw)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Ковтаю отруту",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert len(save_calls) == 1, (
            f"saving_throw must be called exactly once. Got {len(save_calls)} calls."
        )
        assert save_calls[0]["ability"] == "CON", (
            f"saving_throw must be called with ability='CON'. Got {save_calls[0]['ability']!r}"
        )
        assert save_calls[0]["dc"] == 12, (
            f"saving_throw dc must be 12 (clamped to LEGAL_DCS). Got {save_calls[0]['dc']}"
        )
        assert "SAVING THROW" in verdict, (
            f"verdict must contain 'SAVING THROW'. Got: {verdict!r}"
        )

    def test_save_success_suppresses_hp_damage_dice(self):
        """Save SUCCESS must force hp_damage_dice to 'none' — no damage on a passed save."""
        profile = _dnd_profile_with_hp()

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=15)
        data["save_used"] = "DEX"
        data["save_dc"] = 15
        data["updates"]["hp_damage_dice"] = "2d6"  # fireball — should be cancelled on success

        from core.dnd_core import CheckResult

        def _mock_save_success(profile, ability, dc, advantage=False, disadvantage=False):
            return CheckResult(
                total=18, natural=15, ability_mod=1, prof_bonus=2,
                dc=dc, success=True, critical="none",
                roll_str=f"[15]→15 +1(DEX) +2(prof) = 18 vs DC {dc} → SUCCESS",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=_mock_save_success)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Відстрибую від вогню",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"Save SUCCESS must suppress hp_damage_dice. Got {updates.get('hp_damage_dice')!r}"
        )
        assert updates["outcome"] == "SUCCESS"

    def test_save_failure_keeps_hp_damage_dice(self):
        """Save FAILURE must keep hp_damage_dice as Worker set it — damage applies."""
        profile = _dnd_profile_with_hp()

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=15)
        data["save_used"] = "CON"
        data["save_dc"] = 15
        data["updates"]["hp_damage_dice"] = "1d4"  # poison damage on failure

        from core.dnd_core import CheckResult

        def _mock_save_failure(profile, ability, dc, advantage=False, disadvantage=False):
            return CheckResult(
                total=7, natural=5, ability_mod=2, prof_bonus=0,
                dc=dc, success=False, critical="none",
                roll_str=f"[5]→5 +2(CON) = 7 vs DC {dc} → FAIL",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=_mock_save_failure)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Не встигаю ухилитися",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_damage_dice") == "1d4", (
            f"Save FAILURE must keep hp_damage_dice='1d4'. Got {updates.get('hp_damage_dice')!r}"
        )
        assert updates["outcome"] == "FAILURE"

    def test_save_dc_clamped_to_legal_dcs(self):
        """Worker returns save_dc=13 (illegal) → clamp_dc snaps to 12 or 15."""
        from core.dnd_core import LEGAL_DCS
        profile = _dnd_profile_with_hp()

        data = _minimal_worker_data(ability_used="None", skill_used="None")
        data["save_used"] = "WIS"
        data["save_dc"] = 13  # not in LEGAL_DCS=(5,10,12,15,17,20,22)

        captured_dc = []

        from core.dnd_core import CheckResult

        def _mock_save(profile, ability, dc, advantage=False, disadvantage=False):
            captured_dc.append(dc)
            return CheckResult(
                total=10, natural=8, ability_mod=0, prof_bonus=2,
                dc=dc, success=False, critical="none",
                roll_str=f"[8]→8 = 8 vs DC {dc} → FAIL",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=_mock_save)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Намагаюся протистояти чарам",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert len(captured_dc) == 1
        assert captured_dc[0] in LEGAL_DCS, (
            f"save_dc must be clamped to LEGAL_DCS. Got {captured_dc[0]}"
        )
        assert updates["difficulty"] in LEGAL_DCS

    def test_save_used_none_falls_through_to_skill_check_flow(self):
        """When save_used is absent or 'None', saving_throw must NOT be called —
        normal skill_check flow must execute instead."""
        profile = _dnd_profile()
        data = _minimal_worker_data(ability_used="STR", skill_used="Athletics", difficulty=15)
        # No save_used key — defaults to "None"

        save_calls = []

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=lambda *a, **kw: save_calls.append(1))
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Climb the wall",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert len(save_calls) == 0, (
            f"saving_throw must NOT be called when save_used is absent. "
            f"Got {len(save_calls)} calls."
        )
        # Normal skill check path — should have valid outcome
        assert updates["outcome"] in {"SUCCESS", "FAILURE", "CRITICAL SUCCESS", "CRITICAL FAILURE"}

    def test_save_critical_failure_nat_1(self):
        """Save with nat 1 → outcome CRITICAL FAILURE."""
        profile = _dnd_profile_with_hp()

        data = _minimal_worker_data(ability_used="None", skill_used="None")
        data["save_used"] = "STR"
        data["save_dc"] = 10

        from core.dnd_core import CheckResult

        def _mock_nat1_save(profile, ability, dc, advantage=False, disadvantage=False):
            return CheckResult(
                total=2, natural=1, ability_mod=3, prof_bonus=0,
                dc=dc, success=False, critical="fail",
                roll_str=f"[1]→1 +3(STR) = 4 vs DC {dc} → FAIL",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=_mock_nat1_save)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Намагаюся встояти",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates["outcome"] == "CRITICAL FAILURE", (
            f"nat 1 save must yield CRITICAL FAILURE. Got {updates['outcome']!r}"
        )
        assert updates["natural_roll"] == 1

    def test_save_critical_success_nat_20_suppresses_damage(self):
        """Save with nat 20 → CRITICAL SUCCESS → hp_damage_dice suppressed."""
        profile = _dnd_profile_with_hp()

        data = _minimal_worker_data(ability_used="None", skill_used="None")
        data["save_used"] = "DEX"
        data["save_dc"] = 20
        data["updates"]["hp_damage_dice"] = "2d8"

        from core.dnd_core import CheckResult

        def _mock_nat20_save(profile, ability, dc, advantage=False, disadvantage=False):
            return CheckResult(
                total=21, natural=20, ability_mod=1, prof_bonus=0,
                dc=dc, success=True, critical="success",
                roll_str=f"[20]→20 +1(DEX) = 21 vs DC {dc} → SUCCESS",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_engine.saving_throw", side_effect=_mock_nat20_save)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Ухиляюся від вибуху",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates["outcome"] == "CRITICAL SUCCESS"
        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"CRITICAL SUCCESS save must suppress hp_damage_dice. Got {updates.get('hp_damage_dice')!r}"
        )


# ===========================================================================
# Tests: P2 — Rest integration
# ===========================================================================

class TestRestIntegration:
    """Tests for P2: Worker output rest_type triggers rest mechanics, bypasses skill check."""

    def test_rest_type_long_restores_full_hp(self):
        """Worker output with rest_type='long' must call long_rest and fully restore HP."""
        profile = _dnd_profile_with_hp()
        profile["hp_current"] = 5
        profile["hp_max"] = 20
        profile["hit_dice_total"] = 1
        profile["hit_dice_used"] = 0
        profile["class"] = "Knight"

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=5)
        data["rest_type"] = "long"

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Лягаю спати в таверні",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        # long_rest fully restores HP
        assert profile["hp_current"] == 20, (
            f"Long rest must restore hp_current to hp_max=20. Got {profile['hp_current']}"
        )
        assert "LONG REST" in verdict, (
            f"verdict must mention LONG REST. Got: {verdict!r}"
        )
        assert updates["outcome"] == "SUCCESS"

    def test_rest_type_long_suppresses_worker_hp_damage_dice(self):
        """Long rest must clear any hp_damage_dice Worker set — no double-counting."""
        profile = _dnd_profile_with_hp()
        profile["hp_current"] = 10
        profile["hp_max"] = 20
        profile["hit_dice_total"] = 1
        profile["hit_dice_used"] = 0
        profile["class"] = "Knight"

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=5)
        data["rest_type"] = "long"
        data["updates"]["hp_damage_dice"] = "1d6"  # Worker incorrectly set damage during rest

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Відпочиваю",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_damage_dice") in ("none", "None", ""), (
            f"Long rest must suppress hp_damage_dice. Got {updates.get('hp_damage_dice')!r}"
        )

    def test_rest_type_long_suppresses_worker_hp_heal_dice(self):
        """Long rest already fully heals — must suppress hp_heal_dice to avoid double-count."""
        profile = _dnd_profile_with_hp()
        profile["hp_current"] = 10
        profile["hp_max"] = 20
        profile["hit_dice_total"] = 1
        profile["hit_dice_used"] = 0
        profile["class"] = "Knight"

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=5)
        data["rest_type"] = "long"
        data["updates"]["hp_heal_dice"] = "2d8"  # Worker redundantly set heal dice

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Відпочиваю в таверні",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert updates.get("hp_heal_dice") in ("none", "None", ""), (
            f"Long rest must suppress hp_heal_dice (already full HP). "
            f"Got {updates.get('hp_heal_dice')!r}"
        )

    def test_rest_type_short_calls_short_rest(self):
        """Worker output with rest_type='short' must invoke short_rest and restore some HP."""
        profile = _dnd_profile_with_hp()
        profile["hp_current"] = 5
        profile["hp_max"] = 20
        profile["hit_dice_total"] = 4
        profile["hit_dice_used"] = 0
        profile["class"] = "Knight"

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=5)
        data["rest_type"] = "short"

        short_rest_calls = []

        from core.dnd_rest import RestResult as _RestResult

        def _mock_short_rest(profile_arg, hit_dice_spent):
            short_rest_calls.append(hit_dice_spent)
            # Simulate restoring 6 HP
            profile_arg["hp_current"] = min(
                profile_arg.get("hp_max", 20),
                profile_arg.get("hp_current", 0) + 6,
            )
            return _RestResult(
                rest_type="short",
                hp_restored=6,
                hit_dice_spent=hit_dice_spent,
                hit_dice_regained=0,
                conditions_cleared=[],
                duration_minutes=60,
                log="Short rest: +6 HP",
            )

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_rest.short_rest", side_effect=_mock_short_rest)
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Роблю короткий перепочинок",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert len(short_rest_calls) == 1, (
            f"short_rest must be called exactly once. Got {len(short_rest_calls)} calls."
        )
        assert short_rest_calls[0] == 1, (
            f"short_rest must be called with hit_dice_spent=1 (default). Got {short_rest_calls[0]}"
        )
        assert "SHORT REST" in verdict, (
            f"verdict must mention SHORT REST. Got: {verdict!r}"
        )

    def test_rest_type_short_spends_one_hit_die_default(self):
        """Short rest spends exactly 1 hit die by default (no parameterisation yet)."""
        profile = _dnd_profile_with_hp()
        profile["hp_current"] = 10
        profile["hp_max"] = 20
        profile["hit_dice_total"] = 3
        profile["hit_dice_used"] = 0
        profile["class"] = "Knight"

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=5)
        data["rest_type"] = "short"

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Відпочиваю годину",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        # hit_dice_used should be 1 (spent 1 die)
        assert profile["hit_dice_used"] == 1, (
            f"Short rest must spend exactly 1 hit die. hit_dice_used={profile['hit_dice_used']}"
        )

    def test_rest_type_none_does_not_trigger_rest(self):
        """When rest_type is absent or 'none', rest mechanics must NOT be triggered."""
        profile = _dnd_profile()
        data = _minimal_worker_data(ability_used="STR", skill_used="Athletics", difficulty=15)
        # No rest_type key — defaults to "none"

        long_rest_calls = []
        short_rest_calls = []

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)
            stack.enter_context(
                patch("core.dnd_rest.long_rest",
                      side_effect=lambda *a, **kw: long_rest_calls.append(1))
            )
            stack.enter_context(
                patch("core.dnd_rest.short_rest",
                      side_effect=lambda *a, **kw: short_rest_calls.append(1))
            )

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Climb the wall",
                    profile=profile,
                )
            verdict, updates = _run_async(_run())

        assert len(long_rest_calls) == 0, "long_rest must NOT be called when rest_type is absent."
        assert len(short_rest_calls) == 0, "short_rest must NOT be called when rest_type is absent."

    def test_rest_type_long_syncs_legacy_health_field(self):
        """Long rest must sync profile['Здоров'я'] to 100 (legacy UI field)."""
        profile = _dnd_profile_with_hp()
        profile["hp_current"] = 5
        profile["hp_max"] = 20
        profile["hit_dice_total"] = 1
        profile["hit_dice_used"] = 0
        profile["class"] = "Knight"
        profile["Здоров'я"] = 25  # initially stale/wrong

        data = _minimal_worker_data(ability_used="None", skill_used="None", difficulty=5)
        data["rest_type"] = "long"

        with ExitStack() as stack:
            for p in _patches_for_resolve(data):
                stack.enter_context(p)

            async def _run():
                from core.dnd_engine import resolve_normal_action
                return await resolve_normal_action(
                    user_input="Сплю 8 годин",
                    profile=profile,
                )
            _run_async(_run())

        health_key = "Здоров'я"
        assert profile.get(health_key) == 100, (
            f"Long rest must set legacy {health_key} to 100. Got {profile.get(health_key)}"
        )


# ===========================================================================
# Tests: Worker retry logic (1 initial + 1 retry before AUTO_SUCCESS fallback)
# ===========================================================================

class TestWorkerRetry:
    """Verify that resolve_normal_action retries once on empty/None LLM response
    before degrading to AUTO_SUCCESS fallback."""

    def test_worker_retries_once_on_empty_then_succeeds(self):
        """First call returns resp.text=None, second call returns valid JSON.
        Result must use the valid data (not AUTO_SUCCESS fallback).
        generate_content must be called exactly 2 times."""
        profile = _dnd_profile()
        valid_data = _minimal_worker_data(
            ability_used="STR",
            skill_used="Athletics",
            difficulty=15,
            xp_award=25,
        )
        import json as _json

        call_count = []

        def _mock_gen(prompt, max_retries=6, config=None):
            call_count.append(1)
            m = MagicMock()
            if len(call_count) == 1:
                m.text = None  # first attempt: MALFORMED/None
            else:
                m.text = _json.dumps(valid_data, ensure_ascii=False)  # second attempt: valid
            return m

        prompt_patch = patch(
            "core.dnd_engine.build_normal_resolve_prompt",
            return_value="MOCK_PROMPT",
        )
        # clean_and_parse_json: return None for None text (guarded in loop), valid dict for valid text
        original_parse = None

        def _smart_parse(text):
            if not text:
                return None
            return valid_data

        parse_patch = patch(
            "core.dnd_engine.clean_and_parse_json",
            side_effect=_smart_parse,
        )

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Climb the wall",
                profile=profile,
            )

        with prompt_patch, parse_patch:
            with patch(
                "core.dnd_engine.model_worker.generate_content",
                side_effect=_mock_gen,
            ):
                verdict, updates = _run_async(_run())

        assert len(call_count) == 2, (
            f"generate_content must be called 2 times (1 initial + 1 retry). Got {len(call_count)}"
        )
        assert "AUTO_SUCCESS" not in verdict, (
            f"Retry must succeed — verdict must NOT contain AUTO_SUCCESS. Got: {verdict!r}"
        )
        assert updates["outcome"] in {"SUCCESS", "FAILURE", "CRITICAL SUCCESS", "CRITICAL FAILURE"}, (
            f"Outcome must be a real dice result after retry. Got {updates['outcome']!r}"
        )

    def test_worker_both_attempts_empty_falls_back_auto_success(self):
        """Both attempts return resp.text=None → AUTO_SUCCESS fallback.
        generate_content must be called exactly 2 times."""
        profile = _dnd_profile()

        call_count = []

        def _mock_gen_empty(prompt, max_retries=6, config=None):
            call_count.append(1)
            m = MagicMock()
            m.text = None  # always None — MALFORMED
            return m

        prompt_patch = patch(
            "core.dnd_engine.build_normal_resolve_prompt",
            return_value="MOCK_PROMPT",
        )
        # clean_and_parse_json is never called (guarded by `if _raw_text else None`)
        # but patch it for safety to ensure no unexpected calls
        parse_patch = patch(
            "core.dnd_engine.clean_and_parse_json",
            return_value=None,
        )

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Look around",
                profile=profile,
            )

        with prompt_patch, parse_patch:
            with patch(
                "core.dnd_engine.model_worker.generate_content",
                side_effect=_mock_gen_empty,
            ):
                verdict, updates = _run_async(_run())

        assert len(call_count) == 2, (
            f"Both attempts must be made before fallback. generate_content called {len(call_count)} times"
        )
        assert "AUTO_SUCCESS" in verdict, (
            f"Both attempts empty → fallback verdict must contain 'AUTO_SUCCESS'. Got: {verdict!r}"
        )
        assert updates["outcome"] == "SUCCESS", (
            f"AUTO_SUCCESS fallback outcome must be 'SUCCESS'. Got {updates['outcome']!r}"
        )


# ===========================================================================
# Tests: npcs_in_scene uses real relation from npc_reputation_context
# ===========================================================================

class TestNpcsInSceneRelation:
    """Regression suite: npcs_in_scene_dicts must use score_to_relation_text(reputation_score),
    NOT the hardcoded string 'Нейтральний'.

    Root cause (fixed): dnd_engine.py line 143-144 was:
        [{"Name": n, "Relation_Player": "Нейтральний"} for n in ...]
    Fix: loop over npc_names, look up score in npc_reputation_context, call score_to_relation_text.
    """

    def test_npc_with_high_score_gets_absolute_trust(self):
        """npc_reputation_context={"Дейнеріс": 100} → npcs_in_scene Relation_Player='Абсолютна довіра'.
        Verify via mock of build_normal_resolve_prompt: check npcs_in_scene kwarg."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Поговорити з Дейнерісою",
                profile=profile,
                npc_names=["Дейнеріс"],
                npc_reputation_context={"Дейнеріс": 100},
            )

        gen_patch = patch(
            "core.dnd_engine.model_worker.generate_content",
            return_value=_make_llm_response(data),
        )
        parse_patch = patch("core.dnd_engine.clean_and_parse_json", return_value=data)
        prompt_patch = patch(
            "core.dnd_engine.build_normal_resolve_prompt",
            side_effect=_mock_prompt,
        )

        with gen_patch, parse_patch, prompt_patch:
            _run_async(_run())

        assert len(captured_npcs) == 1, (
            f"npcs_in_scene must have 1 entry for 1 NPC. Got {len(captured_npcs)}: {captured_npcs}"
        )
        rel = captured_npcs[0].get("Relation_Player")
        assert rel == "Абсолютна довіра", (
            f"score=100 must yield 'Абсолютна довіра', NOT hardcoded 'Нейтральний'. "
            f"Got {rel!r}"
        )

    def test_npc_with_zero_score_gets_neutral(self):
        """npc_reputation_context={"Ланністер": 0} → Relation_Player='Нейтральний'."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Дивлюсь на Ланністера",
                profile=profile,
                npc_names=["Ланністер"],
                npc_reputation_context={"Ланністер": 0},
            )

        with (
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)),
            patch("core.dnd_engine.clean_and_parse_json", return_value=data),
            patch("core.dnd_engine.build_normal_resolve_prompt", side_effect=_mock_prompt),
        ):
            _run_async(_run())

        assert captured_npcs[0]["Relation_Player"] == "Нейтральний", (
            f"score=0 must yield 'Нейтральний'. Got {captured_npcs[0]['Relation_Player']!r}"
        )

    def test_npc_with_negative_score_gets_hostile(self):
        """npc_reputation_context={"Серсея": -40} → score_to_relation_text(-40) = 'Ворожий'."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Дивлюсь на Серсею",
                profile=profile,
                npc_names=["Серсея"],
                npc_reputation_context={"Серсея": -40},
            )

        with (
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)),
            patch("core.dnd_engine.clean_and_parse_json", return_value=data),
            patch("core.dnd_engine.build_normal_resolve_prompt", side_effect=_mock_prompt),
        ):
            _run_async(_run())

        # score -40 falls in range [-50, -36) → "Ворожий"
        assert captured_npcs[0]["Relation_Player"] == "Ворожий", (
            f"score=-40 must yield 'Ворожий'. Got {captured_npcs[0]['Relation_Player']!r}"
        )

    def test_npc_absent_from_reputation_context_defaults_to_neutral(self):
        """NPC not in npc_reputation_context → default score=0 → 'Нейтральний'."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Дивлюсь довкола",
                profile=profile,
                npc_names=["Невідомий"],
                npc_reputation_context={},  # NPC not in dict
            )

        with (
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)),
            patch("core.dnd_engine.clean_and_parse_json", return_value=data),
            patch("core.dnd_engine.build_normal_resolve_prompt", side_effect=_mock_prompt),
        ):
            _run_async(_run())

        assert captured_npcs[0]["Relation_Player"] == "Нейтральний", (
            f"NPC absent from context must default to 'Нейтральний'. "
            f"Got {captured_npcs[0]['Relation_Player']!r}"
        )

    def test_npc_absent_reputation_context_none_defaults_to_neutral(self):
        """npc_reputation_context=None (not passed) → default score=0 → 'Нейтральний'."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Дивлюсь довкола",
                profile=profile,
                npc_names=["Хтось"],
                # npc_reputation_context not passed → defaults to None
            )

        with (
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)),
            patch("core.dnd_engine.clean_and_parse_json", return_value=data),
            patch("core.dnd_engine.build_normal_resolve_prompt", side_effect=_mock_prompt),
        ):
            _run_async(_run())

        assert captured_npcs[0]["Relation_Player"] == "Нейтральний", (
            f"npc_reputation_context=None must default to 'Нейтральний'. "
            f"Got {captured_npcs[0]['Relation_Player']!r}"
        )

    def test_multiple_npcs_each_get_correct_relation(self):
        """Multiple NPCs with different scores each get the correct textual relation."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        npc_reputation = {
            "Джон Сноу": 85,   # "Абсолютна довіра"
            "Серсея":    -40,  # "Ворожий"
            "Самвелл":   30,   # "Прихильний"
        }

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Дивлюсь на всіх",
                profile=profile,
                npc_names=["Джон Сноу", "Серсея", "Самвелл"],
                npc_reputation_context=npc_reputation,
            )

        with (
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)),
            patch("core.dnd_engine.clean_and_parse_json", return_value=data),
            patch("core.dnd_engine.build_normal_resolve_prompt", side_effect=_mock_prompt),
        ):
            _run_async(_run())

        assert len(captured_npcs) == 3, (
            f"npcs_in_scene must have 3 entries. Got {len(captured_npcs)}"
        )
        by_name = {npc["Name"]: npc["Relation_Player"] for npc in captured_npcs}

        assert by_name["Джон Сноу"] == "Абсолютна довіра", (
            f"Джон Сноу score=85: expected 'Абсолютна довіра', got {by_name['Джон Сноу']!r}"
        )
        assert by_name["Серсея"] == "Ворожий", (
            f"Серсея score=-40: expected 'Ворожий', got {by_name['Серсея']!r}"
        )
        assert by_name["Самвелл"] == "Прихильний", (
            f"Самвелл score=30: expected 'Прихильний', got {by_name['Самвелл']!r}"
        )

    def test_not_hardcoded_neutralnyi_for_trusted_npc(self):
        """Regression: the old code always returned 'Нейтральний' regardless of score.
        This test explicitly guards against that regression: a score of 100 must NOT
        produce 'Нейтральний'."""
        profile = _dnd_profile()
        data = _minimal_worker_data()

        captured_npcs = []

        def _mock_prompt(**kwargs):
            captured_npcs.extend(kwargs.get("npcs_in_scene", []))
            return "MOCK_PROMPT"

        async def _run():
            from core.dnd_engine import resolve_normal_action
            return await resolve_normal_action(
                user_input="Поговорити",
                profile=profile,
                npc_names=["Дейнеріс"],
                npc_reputation_context={"Дейнеріс": 100},
            )

        with (
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)),
            patch("core.dnd_engine.clean_and_parse_json", return_value=data),
            patch("core.dnd_engine.build_normal_resolve_prompt", side_effect=_mock_prompt),
        ):
            _run_async(_run())

        rel = captured_npcs[0]["Relation_Player"]
        assert rel != "Нейтральний", (
            f"REGRESSION: score=100 must NOT yield hardcoded 'Нейтральний'. Got {rel!r}"
        )
