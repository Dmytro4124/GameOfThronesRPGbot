"""In-memory combat state registry (Фаза 2 standalone — not yet wired to engine.py).

Architecture note (CLAUDE.md §5.4):
  _combat_states is a module-global dict WITHOUT thread-safety by design —
  aiogram runs in a single asyncio event loop, so there is no true parallelism.
  However, _state_locks provides per-chat asyncio.Lock objects so that callers
  can guard initiate/cleanup sequences with try/finally to prevent a crashed
  handler from leaving the state in an inconsistent state.

Caller contract:
  Whenever you acquire a lock via get_or_create_lock(chat_id) you MUST use
  try/finally to guarantee cleanup_lock() is called, e.g.:

      lock = get_or_create_lock(chat_id)
      async with lock:
          try:
              state = initiate_combat(...)
              set_combat_state(chat_id, state)
              # ... do work ...
          finally:
              if not is_in_combat(chat_id):
                  cleanup_lock(chat_id)

  This mirrors the active_processing pattern in bot/handlers.py.
"""

import asyncio
from typing import Optional

from core.dnd_combat import CombatState

# ---------------------------------------------------------------------------
# Module-global registry — NOT thread-safe (single-process aiogram by design)
# ---------------------------------------------------------------------------

_combat_states: dict[int, CombatState] = {}

# Per-chat asyncio.Lock for atomic init/cleanup sequences
_state_locks: dict[int, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_combat_state(chat_id: int) -> Optional[CombatState]:
    """Return CombatState if chat is in active combat, else None."""
    return _combat_states.get(chat_id)


def set_combat_state(chat_id: int, state: CombatState) -> None:
    """Store state for chat_id. Replaces existing state without checking."""
    _combat_states[chat_id] = state


def clear_combat_state(chat_id: int) -> Optional[CombatState]:
    """Remove and return CombatState for chat_id (or None if not present)."""
    return _combat_states.pop(chat_id, None)


def is_in_combat(chat_id: int) -> bool:
    """Return True if chat_id currently has an active CombatState."""
    return chat_id in _combat_states


def get_or_create_lock(chat_id: int) -> asyncio.Lock:
    """Return existing or create new asyncio.Lock for chat_id.

    Used by engine/handlers for try/finally guard around combat init and cleanup.
    The lock is NOT automatically removed — call cleanup_lock() in the finally block
    after clear_combat_state() to avoid unbounded lock accumulation.
    """
    if chat_id not in _state_locks:
        _state_locks[chat_id] = asyncio.Lock()
    return _state_locks[chat_id]


def cleanup_lock(chat_id: int) -> None:
    """Remove the asyncio.Lock for chat_id. Call after clear_combat_state()."""
    _state_locks.pop(chat_id, None)


def get_all_active_combats() -> list[int]:
    """Return list of chat_ids currently in combat (for admin/debug)."""
    return list(_combat_states.keys())
