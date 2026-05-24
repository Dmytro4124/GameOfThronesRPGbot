"""Unit tests for core/combat_state.py (Phase 2 QA).

Covers: get/set/clear CRUD, is_in_combat, get_or_create_lock, cleanup_lock,
get_all_active_combats, and isolation between multiple chats.
"""

import asyncio
import pytest

import core.combat_state as cs
from core.combat_state import (
    clear_combat_state,
    cleanup_lock,
    get_all_active_combats,
    get_combat_state,
    get_or_create_lock,
    is_in_combat,
    set_combat_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub_state(chat_id: int):
    """Build a minimal CombatState-like placeholder without importing combat code."""
    from core.dnd_combat import CombatState, CombatantRef

    player_snap = {"Ім'я": "Arya", "hp_current": 20, "ability_scores": {"DEX": 14}, "conditions": []}
    ref = CombatantRef(kind="player", name="Arya", init_roll=12)
    return CombatState(
        chat_id=chat_id,
        round=1,
        initiative_order=[ref],
        current_actor_idx=0,
        npcs={},
        player_snapshot=player_snap,
    )


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset module-global registries before and after each test."""
    cs._combat_states.clear()
    cs._state_locks.clear()
    yield
    cs._combat_states.clear()
    cs._state_locks.clear()


# ---------------------------------------------------------------------------
# 1. set / get / clear roundtrip
# ---------------------------------------------------------------------------

def test_set_get_roundtrip():
    state = _make_stub_state(chat_id=100)
    set_combat_state(100, state)
    retrieved = get_combat_state(100)
    assert retrieved is state


def test_get_returns_none_when_absent():
    result = get_combat_state(999)
    assert result is None


def test_clear_removes_state_and_returns_it():
    state = _make_stub_state(chat_id=200)
    set_combat_state(200, state)
    removed = clear_combat_state(200)
    assert removed is state
    assert get_combat_state(200) is None


def test_clear_returns_none_when_not_present():
    result = clear_combat_state(999)
    assert result is None


# ---------------------------------------------------------------------------
# 2. is_in_combat
# ---------------------------------------------------------------------------

def test_is_in_combat_true_after_set():
    state = _make_stub_state(chat_id=300)
    set_combat_state(300, state)
    assert is_in_combat(300) is True


def test_is_in_combat_false_before_set():
    assert is_in_combat(400) is False


def test_is_in_combat_false_after_clear():
    state = _make_stub_state(chat_id=500)
    set_combat_state(500, state)
    clear_combat_state(500)
    assert is_in_combat(500) is False


# ---------------------------------------------------------------------------
# 3. get_or_create_lock — same lock returned twice
# ---------------------------------------------------------------------------

def test_get_or_create_lock_same_object_for_same_chat():
    lock1 = get_or_create_lock(600)
    lock2 = get_or_create_lock(600)
    assert lock1 is lock2


def test_get_or_create_lock_different_objects_for_different_chats():
    lock_a = get_or_create_lock(700)
    lock_b = get_or_create_lock(800)
    assert lock_a is not lock_b


def test_get_or_create_lock_returns_asyncio_lock():
    lock = get_or_create_lock(900)
    assert isinstance(lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# 4. cleanup_lock
# ---------------------------------------------------------------------------

def test_cleanup_lock_removes_lock():
    get_or_create_lock(1000)
    cleanup_lock(1000)
    # After cleanup, creating again returns a NEW lock object
    new_lock = get_or_create_lock(1000)
    # The registry now has a new entry — just verify no error and it's a Lock
    assert isinstance(new_lock, asyncio.Lock)


def test_cleanup_lock_nonexistent_does_not_raise():
    try:
        cleanup_lock(9999)
    except Exception as exc:
        pytest.fail(f"cleanup_lock raised unexpectedly: {exc}")


def test_cleanup_lock_lock_gone_from_registry():
    get_or_create_lock(1100)
    cleanup_lock(1100)
    assert 1100 not in cs._state_locks


# ---------------------------------------------------------------------------
# 5. get_all_active_combats
# ---------------------------------------------------------------------------

def test_get_all_active_combats_contains_chat_id_after_set():
    state = _make_stub_state(chat_id=1200)
    set_combat_state(1200, state)
    assert 1200 in get_all_active_combats()


def test_get_all_active_combats_does_not_contain_id_after_clear():
    state = _make_stub_state(chat_id=1300)
    set_combat_state(1300, state)
    clear_combat_state(1300)
    assert 1300 not in get_all_active_combats()


def test_get_all_active_combats_empty_when_no_combats():
    assert get_all_active_combats() == []


# ---------------------------------------------------------------------------
# 6. Multiple chats — isolation
# ---------------------------------------------------------------------------

def test_multiple_chats_are_isolated():
    state1 = _make_stub_state(chat_id=2000)
    state2 = _make_stub_state(chat_id=2001)
    set_combat_state(2000, state1)
    set_combat_state(2001, state2)

    # Clear only chat 2001
    clear_combat_state(2001)

    # Chat 2000 must still be active
    assert is_in_combat(2000) is True
    assert get_combat_state(2000) is state1

    # Chat 2001 must be gone
    assert is_in_combat(2001) is False


def test_multiple_chats_all_appear_in_active_combats():
    set_combat_state(3000, _make_stub_state(chat_id=3000))
    set_combat_state(3001, _make_stub_state(chat_id=3001))
    active = get_all_active_combats()
    assert 3000 in active
    assert 3001 in active
