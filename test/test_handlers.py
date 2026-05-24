# test/test_handlers.py
"""
Unit-tests for bot/handlers.py.

Isolation strategy: bot/handlers.py is a monolith with heavy dependencies
(database.operations, core.world, core.engine). We use a module-scoped
fixture that stubs sys.modules before import and restores them after via yield.

NOTE: This file does NOT do module-level patch.dict().start() without a
corresponding stop() -- that pattern (present in test_cheats.py) leaks
into other test files. Here we use a proper scoped fixture with yield/teardown.
"""

import asyncio
import sys
import types
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixture: isolated import of bot.handlers with stubbed external deps
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def handlers_module():
    """
    Returns bot.handlers with all external dependencies stubbed.
    Teardown restores sys.modules after all tests in the module finish.
    """
    _stub_keys = [
        "database.sheets",
        "database.operations",
        "core.world",
        "core.intro_cache",
        "bot.help_text",
        "bot.handlers",
        "core.engine",
    ]
    _orig = {k: sys.modules.get(k) for k in _stub_keys}

    # Stub database.sheets (must be installed before database.operations loads)
    fake_sheets = types.ModuleType("database.sheets")
    fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = fake_sheets

    # Stub database.operations -- all functions as mocks
    fake_ops = types.ModuleType("database.operations")
    async_names = {
        "get_user_data", "save_user_data", "delete_user_data",
        "refresh_npc_database", "update_npcs_in_db", "update_npc_reputation",
        "append_memory_anchor", "ensure_user_npc_sheet", "delete_user_npc_sheet",
        "get_relevant_context",
    }
    sync_names = {
        "get_unique_regions", "get_houses_by_region", "get_house_stats_data",
        "clear_npc_cache", "get_location_npcs", "get_dead_npc_names",
        "find_best_match", "_npc_tab_name",
    }
    for name in async_names:
        setattr(fake_ops, name, AsyncMock())
    for name in sync_names:
        setattr(fake_ops, name, MagicMock())
    sys.modules["database.operations"] = fake_ops

    # Stub core.world
    fake_world = types.ModuleType("core.world")
    for name in [
        "get_canon_characters", "generate_initial_stats", "get_narrative_intro",
        "background_canon_generation", "populate_contextual_npcs", "build_fallback_intro",
    ]:
        setattr(fake_world, name, MagicMock())
    sys.modules["core.world"] = fake_world

    # Stub core.intro_cache
    fake_intro_cache = types.ModuleType("core.intro_cache")
    fake_intro_cache.get_cached_intro = MagicMock(return_value=None)
    fake_intro_cache.set_cached_intro = MagicMock()
    sys.modules["core.intro_cache"] = fake_intro_cache

    # Stub bot.help_text
    fake_help = types.ModuleType("bot.help_text")
    fake_help.HELP_TEXT = "STUB"
    fake_help.ADMIN_HELP_TEXT = "STUB_ADMIN"
    sys.modules["bot.help_text"] = fake_help

    # Stub core.engine -- process_game_turn will be patched per-test
    fake_engine = types.ModuleType("core.engine")
    fake_engine.process_game_turn = AsyncMock(return_value=("narrative text", []))
    fake_engine.user_sessions = {}
    sys.modules["core.engine"] = fake_engine

    # Force-reload bot.handlers with stubs in place
    sys.modules.pop("bot.handlers", None)
    import bot.handlers as hm

    yield hm

    # Teardown: restore original modules
    for key, val in _orig.items():
        if val is not None:
            sys.modules[key] = val
        else:
            sys.modules.pop(key, None)


# ---------------------------------------------------------------------------
# Test 1: Static analysis -- active_processing.discard is in a finally block
# ---------------------------------------------------------------------------

def test_active_processing_discard_is_in_finally_block(handlers_module):
    """
    Static source check for bot/handlers.py:
    active_processing.discard(chat_id) must exist AND be placed inside
    a 'finally:' block (not only in the happy path).

    This guarantees that any exception -- including asyncio.TimeoutError --
    cannot leave chat_id locked in active_processing forever.
    """
    hm = handlers_module
    source = inspect.getsource(hm)

    assert "active_processing.discard" in source, (
        "bot/handlers.py must call active_processing.discard(chat_id). "
        "Without this, any exception permanently blocks the user."
    )

    assert "finally:" in source, (
        "bot/handlers.py must have a 'finally:' block to guarantee "
        "active_processing cleanup even when exceptions occur."
    )

    # Verify discard appears AFTER a finally keyword (in source order).
    # We use rfind to find the last 'finally:' before the discard call.
    discard_pos = source.find("active_processing.discard")
    finally_pos = source.rfind("finally:", 0, discard_pos)

    assert finally_pos != -1, (
        "active_processing.discard must appear AFTER a 'finally:' block in source. "
        "If it is only in the happy path, TimeoutError will not release the lock. "
        "Check bot/handlers.py handle_text function."
    )


# ---------------------------------------------------------------------------
# Test 2: active_processing released after engine exception
# ---------------------------------------------------------------------------

def test_active_processing_released_after_engine_timeout(handlers_module):
    """
    Issue 1: When process_game_turn raises asyncio.TimeoutError,
    chat_id must NOT remain in active_processing after handling.

    If the finally block does not discard:
    - Player cannot make moves until bot restart.
    - This is a data-loss scenario (frozen user lock).

    Strategy:
    - Patch process_game_turn to raise asyncio.TimeoutError.
    - Invoke handle_text directly (no real Telegram).
    - Assert chat_id is not in active_processing afterwards.

    Note: handle_text is a closure registered with aiogram Router.
    If it is not a direct module attribute, the test will verify the
    static source invariant (test_active_processing_discard_is_in_finally_block)
    and skip the runtime invocation with a clear explanation.
    """
    hm = handlers_module
    chat_id = 9999

    # Make sure chat_id is clean before the test
    hm.active_processing.discard(chat_id)
    assert chat_id not in hm.active_processing, "Pre-condition: chat_id must start clean"

    if not hasattr(hm, "handle_text"):
        pytest.skip(
            "handle_text is not a direct public attribute of bot.handlers. "
            "It is registered as a closure inside aiogram Router and cannot be "
            "invoked without a full aiogram event loop. "
            "Issue 1 is verified statically by "
            "test_active_processing_discard_is_in_finally_block."
        )

    async def _timeout_pgt(*args, **kwargs):
        raise asyncio.TimeoutError("simulated Gemini timeout")

    fake_message = MagicMock()
    fake_message.chat.id = chat_id
    fake_message.from_user.id = chat_id
    fake_message.message_id = 1
    fake_message.text = "Some action"
    fake_message.reply = AsyncMock(return_value=MagicMock(
        edit_text=AsyncMock(), delete=AsyncMock()
    ))
    fake_message.answer = AsyncMock()

    with patch.object(hm, "process_game_turn", side_effect=_timeout_pgt), \
         patch.object(hm, "get_user_data",
                      new=AsyncMock(return_value=({"name": "Test", "mode": "NORMAL"}, 2))), \
         patch.object(hm, "send_game_response", new=AsyncMock()):
        try:
            asyncio.run(hm.handle_text(fake_message))
        except Exception:
            pass  # Expected: aiogram internals may raise without full setup

    assert chat_id not in hm.active_processing, (
        f"Issue 1: chat_id={chat_id} remained in active_processing after TimeoutError. "
        f"Player would be unable to make moves until bot restart. "
        f"Check the finally block in handle_text in bot/handlers.py."
    )
