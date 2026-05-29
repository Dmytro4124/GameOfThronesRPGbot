"""test_combat_package1.py

Unit / integration tests for Package 1 combat-engine fixes:

  1. Reinit-guard  — initiate_combat_from_normal returns EXISTING state on second call.
  2. Friend/foe single-target enrolment — only one NPC enters combat per initiation.
  3. Cleanup-on-error — clear_combat_state + cleanup_lock called on COMBAT pipeline crash.

Mocking strategy:
  - core.combat_state functions used directly (real module, isolated via fixture).
  - core.dnd_combat.initiate_combat patched where initiative randomness is unwanted.
  - asyncio.to_thread / Google Sheets / LLM never touched.

Each test covers exactly one assertion / behaviour boundary.
"""

import asyncio
import copy
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.combat_state as _cs_module
from core.combat_state import (
    clear_combat_state,
    cleanup_lock,
    get_combat_state,
    is_in_combat,
    set_combat_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset combat-state and lock registries around every test."""
    _cs_module._combat_states.clear()
    _cs_module._state_locks.clear()
    yield
    _cs_module._combat_states.clear()
    _cs_module._state_locks.clear()


def _run(coro):
    """Run an async coroutine synchronously in a fresh event loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers (minimal objects — no DB, no LLM)
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> dict:
    base = {
        "Ім'я": "TestPlayer",
        "Здоров'я": 100,
        "Енергія": 800,
        "Особисте Золото": 50,
        "Поточне місцезнаходження": "Вінтерфелл",
        "Поточна сцена": "Courtyard",
        "Регіон": "Північ",
        "Ігровий час": "Day 1, Morning",
        "Інвентар": "Longsword",
        "Зброя": "Longsword",
        "Броня": "Chainmail",
        "Транспорт": "",
        "Світогляд": "Neutral",
        "Риси": "Brave",
        "Вади": "Stubborn",
        "Вороги": "",
        "Друзі": "",
        "Годинники": {"Scene_Tension": "0/4"},
        "level": 3,
        "hp_current": 40,
        "hp_max": 40,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "conditions": [],
        "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        "mode": "NORMAL",
    }
    base.update(overrides)
    return base


def _make_single_npc(name: str = "Guard", hp: int = 15) -> dict:
    return {
        "Name": name,
        "hp_current": hp,
        "hp_max": hp,
        "ac": 14,
        "Status": "Active",
        "conditions": [],
        "attacks": [{"name": "Spear", "to_hit": 3, "dmg": "1d6+1 piercing", "range": 5}],
        "ability_scores": {"STR": 12, "DEX": 10, "CON": 12, "INT": 10, "WIS": 10, "CHA": 8},
    }


def _make_real_combat_state(chat_id: int, profile: dict, npc: dict):
    """Build a real CombatState using initiate_combat (with mocked randomness)."""
    from core.dnd_combat import initiate_combat
    with patch("core.dnd_core.random.randint", return_value=10):
        return initiate_combat(
            player_profile=copy.deepcopy(profile),
            npc_list=[npc],
            chat_id=chat_id,
        )


# ===========================================================================
# PART 1 — Reinit-guard
# ===========================================================================

def test_reinit_guard_returns_existing_state_object():
    """Second call to initiate_combat_from_normal for same chat_id
    must return the IDENTICAL state object (is, not ==)."""
    from core.dnd_combat_engine import initiate_combat_from_normal

    chat_id = 10001
    profile = _make_profile()
    npc1 = _make_single_npc("Guard", hp=15)
    npc2 = _make_single_npc("Assassin", hp=20)

    with patch("core.dnd_core.random.randint", return_value=12):
        state_first = _run(initiate_combat_from_normal(chat_id, profile, [npc1]))

    assert is_in_combat(chat_id), "Combat must be active after first call."

    # Second call with a DIFFERENT npc list
    with patch("core.dnd_core.random.randint", return_value=18):
        state_second = _run(initiate_combat_from_normal(chat_id, profile, [npc2]))

    assert state_second is state_first, (
        "Reinit-guard failed: second call returned a NEW state instead of the existing one. "
        f"state_first id={id(state_first)}, state_second id={id(state_second)}"
    )

    clear_combat_state(chat_id)


def test_reinit_guard_preserves_initiative_order():
    """After a second initiate_combat_from_normal call, the initiative_order
    from the FIRST call is unchanged (no re-rolling)."""
    from core.dnd_combat_engine import initiate_combat_from_normal

    chat_id = 10002
    profile = _make_profile()
    npc = _make_single_npc("Guard")

    with patch("core.dnd_core.random.randint", return_value=10):
        state_first = _run(initiate_combat_from_normal(chat_id, profile, [npc]))

    original_order = [ref.name for ref in state_first.initiative_order]

    # Second call with different NPC list — should be ignored
    npc2 = _make_single_npc("Assassin", hp=99)
    with patch("core.dnd_core.random.randint", return_value=18):
        state_second = _run(initiate_combat_from_normal(chat_id, profile, [npc2]))

    new_order = [ref.name for ref in state_second.initiative_order]
    assert new_order == original_order, (
        f"Reinit-guard must not re-roll initiative. "
        f"Original: {original_order}, after second call: {new_order}"
    )

    clear_combat_state(chat_id)


def test_reinit_guard_new_npcs_not_added():
    """The NPC from the second call ('Assassin') must NOT appear in the
    CombatState.npcs dict returned from the second call."""
    from core.dnd_combat_engine import initiate_combat_from_normal

    chat_id = 10003
    profile = _make_profile()
    npc = _make_single_npc("Guard")

    with patch("core.dnd_core.random.randint", return_value=10):
        _run(initiate_combat_from_normal(chat_id, profile, [npc]))

    npc2 = _make_single_npc("Assassin", hp=99)
    with patch("core.dnd_core.random.randint", return_value=18):
        state_second = _run(initiate_combat_from_normal(chat_id, profile, [npc2]))

    assert "Assassin" not in state_second.npcs, (
        "Reinit-guard: second call must not add 'Assassin' to existing CombatState.npcs. "
        f"npcs keys: {list(state_second.npcs.keys())}"
    )

    clear_combat_state(chat_id)


def test_reinit_guard_first_call_creates_new_state():
    """When no combat is active, initiate_combat_from_normal creates a new state
    (is_in_combat becomes True, state.npcs contains the supplied NPC)."""
    from core.dnd_combat_engine import initiate_combat_from_normal

    chat_id = 10004
    profile = _make_profile()
    npc = _make_single_npc("Bandit")

    assert not is_in_combat(chat_id), "Precondition: no combat before call."

    with patch("core.dnd_core.random.randint", return_value=10):
        state = _run(initiate_combat_from_normal(chat_id, profile, [npc]))

    assert is_in_combat(chat_id), "After first call, is_in_combat must be True."
    assert "Bandit" in state.npcs, f"Bandit must be in CombatState.npcs. Got: {list(state.npcs.keys())}"

    clear_combat_state(chat_id)


# ===========================================================================
# PART 2 — Friend/foe single-target enrolment (initiate_combat)
# ===========================================================================

def test_initiate_combat_single_npc_only_that_npc_in_state():
    """initiate_combat with a single-NPC list: only that NPC in CombatState.npcs."""
    from core.dnd_combat import initiate_combat

    profile = _make_profile()
    single_enemy = _make_single_npc("Enemy")

    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(profile, [single_enemy], chat_id=20001)

    assert list(state.npcs.keys()) == ["Enemy"], (
        f"With one NPC in npc_list, only that NPC must appear in CombatState.npcs. "
        f"Got: {list(state.npcs.keys())}"
    )


def test_initiate_combat_single_npc_only_in_initiative():
    """initiate_combat with a single-NPC list: initiative_order has exactly 2 entries
    (1 player + 1 NPC), not more."""
    from core.dnd_combat import initiate_combat

    profile = _make_profile()
    single_enemy = _make_single_npc("Enemy")

    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(profile, [single_enemy], chat_id=20002)

    assert len(state.initiative_order) == 2, (
        f"1 player + 1 NPC → 2 combatants. Got {len(state.initiative_order)}: "
        f"{[r.name for r in state.initiative_order]}"
    )

    kinds = {ref.kind for ref in state.initiative_order}
    assert kinds == {"player", "npc"}, (
        f"Exactly one 'player' and one 'npc' in initiative_order. Got kinds: {kinds}"
    )


def test_initiate_combat_bystanders_excluded_when_not_passed():
    """Bystanders / allies passed in a *different* list must never auto-appear
    in a CombatState created with only the enemy in npc_list.

    This tests the invariant that initiate_combat does NOT pull NPCs from any
    global roster — only the supplied npc_list is enrolled."""
    from core.dnd_combat import initiate_combat

    profile = _make_profile()
    enemy = _make_single_npc("Enemy")
    # These two are scene NPCs but intentionally NOT passed to initiate_combat
    bystander_a = _make_single_npc("Bystander A")
    bystander_b = _make_single_npc("Bystander B")

    with patch("core.dnd_core.random.randint", return_value=10):
        state = initiate_combat(profile, [enemy], chat_id=20003)

    for bystander_name in ("Bystander A", "Bystander B"):
        assert bystander_name not in state.npcs, (
            f"'{bystander_name}' was not passed to initiate_combat but appears in npcs. "
            f"npcs keys: {list(state.npcs.keys())}"
        )
        initiative_names = [ref.name for ref in state.initiative_order]
        assert bystander_name not in initiative_names, (
            f"'{bystander_name}' must not be in initiative_order. Got: {initiative_names}"
        )


def test_friend_foe_resolution_primary_path_named_target_in_legal():
    """Resolution path 1 (primary): reputation_target_npc is in legal_npc_names
    → only THAT NPC is enrolled in combat.

    Tests the friend/foe rule directly by calling initiate_combat_from_normal
    with the pre-filtered single-NPC list (as engine.py would build it).
    """
    from core.dnd_combat_engine import initiate_combat_from_normal

    chat_id = 20010
    profile = _make_profile()
    # Engine pre-resolves _target_npc_name and passes only that NPC
    target_npc = {"Name": "Hostile Guard", "Relation_Player": "Ворожість"}

    with patch("core.dnd_core.random.randint", return_value=10):
        state = _run(initiate_combat_from_normal(chat_id, profile, [target_npc]))

    assert "Hostile Guard" in state.npcs, (
        f"Target NPC must be in CombatState.npcs. Got: {list(state.npcs.keys())}"
    )
    assert len(state.npcs) == 1, (
        f"Exactly 1 NPC must be enrolled (friend/foe single-target). "
        f"Got {len(state.npcs)}: {list(state.npcs.keys())}"
    )

    clear_combat_state(chat_id)


def test_friend_foe_resolution_fallback_most_hostile():
    """Resolution path 2 (fallback): reputation_target_npc is None/absent,
    but there's one NPC with negative reputation score.

    We bypass engine.py's full pipeline and test the targeting logic directly:
    build the legal_npc_names + npc_reputation_context as the engine would,
    then assert that the most-hostile NPC is selected and only it is enrolled.

    The selection rule (replicating engine.py FSM block):
      _hostile_candidates = [(n, score) for n in legal_npc_names if score < 0]
      _target = min(_hostile_candidates, key=score)
    """
    # This test unit-validates the SELECTION LOGIC — not the full engine pipeline.
    # The rule is: when reputation_target_npc is empty/absent, pick the most-hostile
    # (lowest score < 0) NPC from legal_npc_names.

    legal_npc_names = ["Friendly Septa", "Neutral Guard", "Hostile Outlaw"]
    npc_reputation_context = {
        "Friendly Septa": 30,
        "Neutral Guard": 0,
        "Hostile Outlaw": -45,  # most hostile
    }
    raw_target = ""  # Worker did not name a target

    # --- Replicate engine.py selection logic ---
    _hostile_candidates = [
        (n, npc_reputation_context.get(n, 0))
        for n in legal_npc_names
        if npc_reputation_context.get(n, 0) < 0
    ]
    _target_npc_name = min(_hostile_candidates, key=lambda x: x[1])[0] if _hostile_candidates else None

    assert _target_npc_name == "Hostile Outlaw", (
        f"Most-hostile fallback must select 'Hostile Outlaw' (score=-45). "
        f"Got: {_target_npc_name!r}. Candidates: {_hostile_candidates}"
    )

    # Now verify that only this NPC gets enrolled
    from core.dnd_combat_engine import initiate_combat_from_normal

    chat_id = 20011
    profile = _make_profile()
    scene_npcs_for_combat = [{"Name": _target_npc_name, "Relation_Player": "Ворожість"}]

    with patch("core.dnd_core.random.randint", return_value=10):
        state = _run(initiate_combat_from_normal(chat_id, profile, scene_npcs_for_combat))

    assert list(state.npcs.keys()) == ["Hostile Outlaw"], (
        f"Only the hostile NPC must be enrolled. Got: {list(state.npcs.keys())}"
    )

    # Allies / neutrals must NOT be in combat
    for excluded in ("Friendly Septa", "Neutral Guard"):
        assert excluded not in state.npcs, (
            f"'{excluded}' must NOT be enrolled when only Hostile Outlaw is targeted."
        )

    clear_combat_state(chat_id)


def test_friend_foe_resolution_llm_mismatch_no_hostile_no_combat():
    """NEW behavior (tier-3 removed): Worker named a non-legal NPC
    ('Unknown Bandit' not in legal_npc_names) and no hostile candidates exist.
    Engine must resolve _target_npc_name to None → combat NOT initiated.

    Previously this was a "best-effort fallback" that enrolled legal_npc_names[0].
    That tier-3 was intentionally removed to prevent accidental ally enrolment.

    We test the selection rule directly (no full engine pipeline needed).
    """
    legal_npc_names = ["City Guard", "Market Merchant"]
    npc_reputation_context = {}  # all neutral — no score < 0
    raw_target = "Unknown Bandit"  # LLM hallucinated a name not in legal list

    # Replicate NEW engine.py 2-tier logic (no tier-3)
    _hostile_candidates = [
        (n, npc_reputation_context.get(n, 0))
        for n in legal_npc_names
        if npc_reputation_context.get(n, 0) < 0
    ]

    _target_npc_name: str | None = None
    if raw_target and raw_target in legal_npc_names:
        _target_npc_name = raw_target
    elif _hostile_candidates:
        _target_npc_name = min(_hostile_candidates, key=lambda x: x[1])[0]
    # No tier-3: if Worker named an invalid NPC and no hostile fallback exists,
    # _target_npc_name stays None → combat is not initiated.

    assert _target_npc_name is None, (
        f"With Worker naming a non-legal NPC and no hostile NPCs, "
        f"tier-3 fallback is removed — _target_npc_name must be None (no combat). "
        f"Got: {_target_npc_name!r}"
    )


def test_friend_foe_no_target_when_no_legal_npcs_and_no_hostile():
    """Edge case: legal_npc_names is empty AND reputation_target_npc is absent
    → _target_npc_name stays None → combat is NOT initiated."""
    legal_npc_names: list = []
    npc_reputation_context: dict = {}
    raw_target = ""

    # Replicate engine.py selection logic
    _hostile_candidates = [
        (n, npc_reputation_context.get(n, 0))
        for n in legal_npc_names
        if npc_reputation_context.get(n, 0) < 0
    ]
    _target_npc_name = None
    if raw_target and raw_target in legal_npc_names:
        _target_npc_name = raw_target
    elif _hostile_candidates:
        _target_npc_name = min(_hostile_candidates, key=lambda x: x[1])[0]

    assert _target_npc_name is None, (
        "With no legal NPCs and no named target, combat must NOT be initiated. "
        f"Got _target_npc_name={_target_npc_name!r}"
    )


# ===========================================================================
# PART 3 — Cleanup-on-error: registry helpers + engine crash path
# ===========================================================================

def test_clear_combat_state_makes_is_in_combat_false():
    """After set_combat_state then clear_combat_state, is_in_combat is False."""
    from core.dnd_combat import CombatState, CombatantRef

    chat_id = 30001
    player_snap = {"Ім'я": "X", "hp_current": 20, "ability_scores": {"DEX": 10}, "conditions": []}
    ref = CombatantRef(kind="player", name="X", init_roll=10)
    state = CombatState(
        chat_id=chat_id, round=1, initiative_order=[ref],
        current_actor_idx=0, npcs={}, player_snapshot=player_snap,
    )

    set_combat_state(chat_id, state)
    assert is_in_combat(chat_id) is True, "Precondition: combat active after set."

    clear_combat_state(chat_id)
    assert is_in_combat(chat_id) is False, (
        "After clear_combat_state, is_in_combat must be False."
    )


def test_clear_combat_state_on_absent_chat_id_is_idempotent():
    """clear_combat_state on a chat_id that has no entry must not raise."""
    absent_id = 30002
    assert not is_in_combat(absent_id), "Precondition: no combat for absent_id."

    try:
        clear_combat_state(absent_id)
    except Exception as exc:
        pytest.fail(
            f"clear_combat_state on absent chat_id must be idempotent (no raise). "
            f"Raised: {exc!r}"
        )

    # Still no combat after the call
    assert not is_in_combat(absent_id)


def test_clear_combat_state_repeated_call_is_idempotent():
    """Calling clear_combat_state twice on the same chat_id must not raise."""
    from core.dnd_combat import CombatState, CombatantRef

    chat_id = 30003
    ref = CombatantRef(kind="player", name="Y", init_roll=10)
    state = CombatState(
        chat_id=chat_id, round=1, initiative_order=[ref],
        current_actor_idx=0, npcs={},
        player_snapshot={"Ім'я": "Y", "hp_current": 10, "ability_scores": {"DEX": 10}, "conditions": []},
    )

    set_combat_state(chat_id, state)
    clear_combat_state(chat_id)

    try:
        result = clear_combat_state(chat_id)  # second call — must not raise
    except Exception as exc:
        pytest.fail(f"Second clear_combat_state raised: {exc!r}")

    assert result is None, "Second clear on absent entry must return None."


def test_cleanup_lock_on_absent_chat_id_does_not_raise():
    """cleanup_lock for a chat_id that never had a lock must not raise."""
    try:
        cleanup_lock(30004)
    except Exception as exc:
        pytest.fail(f"cleanup_lock on absent chat_id raised: {exc!r}")


def test_engine_combat_crash_clears_state_and_lock():
    """When COMBAT pipeline crashes, engine calls both cleanup helpers:
      - _clear_combat_state_for_engine is called INSIDE the except block
        (while the lock is still held by async with combat_lock).
      - _cleanup_lock_for_engine is called AFTER async with exits
        (gated by _combat_crashed flag), so the lock is fully released
        before removal from the registry (CLAUDE.md §5.4 deadlock-safe pattern).

    We test by patching execute_combat_round to raise and then asserting
    both mock helpers were called exactly once with the correct chat_id.
    """
    from core.combat_state import set_combat_state as _set

    chat_id = 30010
    profile = _make_profile(mode="COMBAT")

    # Pre-populate combat state so the engine enters the COMBAT branch
    from core.dnd_combat import CombatState, CombatantRef
    ref = CombatantRef(kind="player", name="TestPlayer", init_roll=10)
    npc_ref = CombatantRef(kind="npc", name="Goblin", init_roll=15)
    state = CombatState(
        chat_id=chat_id, round=1,
        initiative_order=[npc_ref, ref],
        current_actor_idx=0,
        npcs={"Goblin": {"hp_current": 10, "hp_max": 10, "ac": 12, "Status": "Active", "conditions": []}},
        player_snapshot={
            "Ім'я": "TestPlayer", "hp_current": 40, "hp_max": 40,
            "ac": 16, "conditions": [], "level": 3,
            "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        },
    )
    _set(chat_id, state)

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["crash test"],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "A", "intent": "a"},
            {"button": "B", "intent": "b"},
            {"button": "C", "intent": "c"},
            {"button": "D", "intent": "d"},
        ],
        "mode_transition": None,
    }
    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json)
    narrator_resp = MagicMock()
    narrator_resp.text = "The battle is interrupted."

    # COMBAT resolve returns a normal-action result so the fallback NORMAL path runs
    normal_updates = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 10,
        "outcome": "SUCCESS",
        "reputation_target_npc": None,
        "reputation_delta_success": 0,
        "reputation_delta_failure": 0,
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
        "combat_imminent": False,
        "xp_award": 0,
        "ability_used": "None",
        "advantage_reason": "",
        "disadvantage_reason": "",
        "verdict_text": "fallback",
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "condition_apply": [],
        "condition_remove": [],
        "save_used": "None",
        "save_dc": 5,
        "rest_type": "none",
    }

    mock_clear = MagicMock(wraps=clear_combat_state)
    mock_cleanup = MagicMock(wraps=cleanup_lock)

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.get_location_npcs", return_value=("Goblin lurks here.", ["Goblin"], {"Goblin": -50})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        # COMBAT pipeline CRASHES
        patch("core.engine.execute_combat_round", new=AsyncMock(side_effect=RuntimeError("COMBAT_CRASH_TEST"))),
        # GM and Narrator must work for the post-crash NORMAL path
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        patch("core.engine.resolve_normal_action", new=AsyncMock(return_value=("FALLBACK_VERDICT", normal_updates))),
        # Spy on the actual cleanup helpers via the engine's namespace
        patch("core.engine._clear_combat_state_for_engine", new=mock_clear),
        patch("core.engine._cleanup_lock_for_engine", new=mock_cleanup),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=chat_id, user_input="Атакую гобліна!", narrator_queue=None
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        try:
            _run(_run_engine())
        except Exception:
            pass  # engine may propagate; we care only about the cleanup calls

    mock_clear.assert_called_once_with(chat_id)
    mock_cleanup.assert_called_once_with(chat_id)

    # cleanup (idempotent — state may already be gone from the spy)
    clear_combat_state(chat_id)


def test_engine_combat_crash_resets_mode_to_normal():
    """After COMBAT pipeline crash, engine resets profile['mode'] to 'NORMAL'."""
    from core.combat_state import set_combat_state as _set

    chat_id = 30011
    profile = _make_profile(mode="COMBAT")

    from core.dnd_combat import CombatState, CombatantRef
    ref = CombatantRef(kind="player", name="TestPlayer", init_roll=10)
    npc_ref = CombatantRef(kind="npc", name="Enemy", init_roll=15)
    state = CombatState(
        chat_id=chat_id, round=1,
        initiative_order=[npc_ref, ref],
        current_actor_idx=0,
        npcs={"Enemy": {"hp_current": 10, "hp_max": 10, "ac": 12, "Status": "Active", "conditions": []}},
        player_snapshot={
            "Ім'я": "TestPlayer", "hp_current": 40, "hp_max": 40,
            "ac": 16, "conditions": [], "level": 3,
            "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        },
    )
    _set(chat_id, state)

    _gm_json = {
        "reasoning": "r", "npc_reasoning": "r",
        "director_notes": ["fallback"],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "A", "intent": "a"}, {"button": "B", "intent": "b"},
            {"button": "C", "intent": "c"}, {"button": "D", "intent": "d"},
        ],
        "mode_transition": None,
    }
    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json)
    narrator_resp = MagicMock()
    narrator_resp.text = "After the chaos."

    normal_updates = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 5,
        "outcome": "SUCCESS",
        "reputation_target_npc": None,
        "reputation_delta_success": 0,
        "reputation_delta_failure": 0,
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
        "combat_imminent": False,
        "xp_award": 0,
        "ability_used": "None",
        "advantage_reason": "",
        "disadvantage_reason": "",
        "verdict_text": "v",
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "condition_apply": [],
        "condition_remove": [],
        "save_used": "None",
        "save_dc": 5,
        "rest_type": "none",
    }

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.get_location_npcs", return_value=("Enemy is here.", ["Enemy"], {"Enemy": -50})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.execute_combat_round", new=AsyncMock(side_effect=RuntimeError("COMBAT_CRASH"))),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        patch("core.engine.resolve_normal_action", new=AsyncMock(return_value=("VERDICT", normal_updates))),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=chat_id, user_input="Атакую!", narrator_queue=None
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        try:
            _run(_run_engine())
        except Exception:
            pass

    assert profile["mode"] == "NORMAL", (
        f"After COMBAT crash, profile['mode'] must be reset to 'NORMAL'. "
        f"Got: {profile['mode']!r}"
    )

    clear_combat_state(chat_id)


# ===========================================================================
# PART 4 — Update existing test: combat_imminent now requires valid target
# (replaces test_process_game_turn_combat_imminent_triggers_initiation
#  which failed because it had reputation_target_npc=None + no hostile NPCs)
# ===========================================================================

def test_process_game_turn_combat_imminent_triggers_initiation_with_named_target():
    """combat_imminent=True + reputation_target_npc='Guard' in legal_npc_names
    → initiate_combat_from_normal is called (primary resolution path).

    This is the UPDATED version of the old test that failed because it used
    reputation_target_npc=None and an empty reputation context — neither of which
    satisfies the new friend/foe resolution logic.
    """
    from core.combat_state import clear_combat_state

    chat_id = 40001
    profile = _make_profile(mode="NORMAL")

    worker_updates = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 10,
        "circumstance": "NORMAL",
        "outcome": "SUCCESS",
        "reputation_target_npc": "Guard",  # <-- primary path: named + in legal list
        "reputation_delta_success": 0,
        "reputation_delta_failure": 0,
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
        "combat_imminent": True,
        "xp_award": 0,
        "ability_used": "None",
        "advantage_reason": "",
        "disadvantage_reason": "",
        "verdict_text": "Провокує охоронця до бою.",
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "condition_apply": [],
        "condition_remove": [],
        "save_used": "None",
        "save_dc": 5,
        "rest_type": "none",
    }

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["An enemy approaches."],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "Attack", "intent": "Attack"},
            {"button": "Flee",   "intent": "Flee"},
            {"button": "Talk",   "intent": "Talk"},
            {"button": "Wait",   "intent": "Wait"},
        ],
        "mode_transition": None,
    }
    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json, ensure_ascii=False)
    narrator_resp = MagicMock()
    narrator_resp.text = "An enemy emerges. Combat is imminent."

    initiate_mock = AsyncMock(return_value=MagicMock(
        initiative_order=[
            MagicMock(name="TestPlayer", init_roll=12),
            MagicMock(name="Guard", init_roll=8),
        ]
    ))

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.resolve_normal_action", new=AsyncMock(return_value=("VERDICT", worker_updates))),
        # 'Guard' in legal_npc_names; npc_reputation_context neutral (path 1 fires via named target)
        patch("core.engine.get_location_npcs", return_value=("Guard is here.", ["Guard"], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        patch("core.engine.initiate_combat_from_normal", new=initiate_mock),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=chat_id,
            user_input="Провокую охоронця!",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        _run(_run_engine())

    initiate_mock.assert_called_once()

    clear_combat_state(chat_id)


def test_process_game_turn_combat_imminent_no_valid_target_stays_normal():
    """combat_imminent=True + reputation_target_npc=None + no hostile NPCs
    → initiate_combat_from_normal is NOT called; profile stays NORMAL.

    This locks in the new behaviour: if the engine can't resolve a valid target,
    it deliberately does NOT start combat (no silent crash).
    """
    from core.combat_state import clear_combat_state

    chat_id = 40002
    profile = _make_profile(mode="NORMAL")

    worker_updates = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 10,
        "outcome": "SUCCESS",
        "reputation_target_npc": None,   # no named target
        "reputation_delta_success": 0,
        "reputation_delta_failure": 0,
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
        "combat_imminent": True,
        "xp_award": 0,
        "ability_used": "None",
        "advantage_reason": "",
        "disadvantage_reason": "",
        "verdict_text": "v",
        "hp_damage_dice": "none",
        "hp_heal_dice": "none",
        "condition_apply": [],
        "condition_remove": [],
        "save_used": "None",
        "save_dc": 5,
        "rest_type": "none",
    }

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["calm"],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "A", "intent": "a"}, {"button": "B", "intent": "b"},
            {"button": "C", "intent": "c"}, {"button": "D", "intent": "d"},
        ],
        "mode_transition": None,
    }
    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json)
    narrator_resp = MagicMock()
    narrator_resp.text = "Nothing happens."

    initiate_mock = AsyncMock()

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.resolve_normal_action", new=AsyncMock(return_value=("VERDICT", worker_updates))),
        # neutral NPC, score=0 (not < 0) → no hostile candidates
        patch("core.engine.get_location_npcs", return_value=("Guard is here.", ["Guard"], {"Guard": 0})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        patch("core.engine.initiate_combat_from_normal", new=initiate_mock),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(
            chat_id=chat_id,
            user_input="щось",
            narrator_queue=None,
        )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        _run(_run_engine())

    initiate_mock.assert_not_called()
    assert profile["mode"] == "NORMAL", (
        f"Without a valid target, mode must stay NORMAL. Got: {profile['mode']!r}"
    )

    clear_combat_state(chat_id)
