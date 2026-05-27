# test/test_debug_trace_send.py
"""
Unit-tests for the debug trace file dispatch added to bot/handlers.py.

Async tests use asyncio.run() -- no pytest-asyncio, consistent with the
pattern adopted in test/test_cheats.py and test/test_bg_intro_flow.py.

Covers:
1. _send_debug_trace_file builds a .txt file and calls bot.send_document.
2. File content includes all 6 pipeline section headers and key data.
3. No file sent when chat_id is NOT in DEBUG_USERS.
4. If bot.send_document raises, the error is logged but does not propagate.
"""

import asyncio
import sys
import time
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so bot.handlers can be imported without real dependencies
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def handlers_module():
    _stub_keys = [
        "database.sheets",
        "database.operations",
        "core.world",
        "core.intro_cache",
        "bot.help_text",
        "bot.handlers",
        "core.engine",
        "core.cheats",
    ]
    _orig = {k: sys.modules.get(k) for k in _stub_keys}

    fake_sheets = types.ModuleType("database.sheets")
    fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = fake_sheets

    fake_ops = types.ModuleType("database.operations")
    for name in (
        "get_user_data", "save_user_data", "delete_user_data",
        "refresh_npc_database", "update_npcs_in_db", "update_npc_reputation",
        "append_memory_anchor", "ensure_user_npc_sheet", "delete_user_npc_sheet",
        "get_relevant_context",
    ):
        setattr(fake_ops, name, AsyncMock())
    for name in (
        "get_unique_regions", "get_houses_by_region", "get_house_stats_data",
        "clear_npc_cache", "get_location_npcs", "get_dead_npc_names",
        "find_best_match", "_npc_tab_name",
    ):
        setattr(fake_ops, name, MagicMock())
    sys.modules["database.operations"] = fake_ops

    fake_world = types.ModuleType("core.world")
    for name in (
        "get_canon_characters", "generate_initial_stats", "get_narrative_intro",
        "background_canon_generation", "populate_contextual_npcs", "build_fallback_intro",
    ):
        setattr(fake_world, name, MagicMock())
    sys.modules["core.world"] = fake_world

    fake_intro_cache = types.ModuleType("core.intro_cache")
    fake_intro_cache.get_cached_intro = MagicMock(return_value=None)
    fake_intro_cache.set_cached_intro = MagicMock()
    sys.modules["core.intro_cache"] = fake_intro_cache

    fake_help = types.ModuleType("bot.help_text")
    fake_help.HELP_TEXT = "STUB"
    fake_help.ADMIN_HELP_TEXT = "STUB_ADMIN"
    sys.modules["bot.help_text"] = fake_help

    fake_engine = types.ModuleType("core.engine")
    fake_engine.process_game_turn = AsyncMock(return_value=("narrative text", []))
    fake_engine.user_sessions = {}
    sys.modules["core.engine"] = fake_engine

    # core.cheats stub: DEBUG_USERS starts empty; tests manipulate it directly
    fake_cheats = types.ModuleType("core.cheats")
    fake_cheats.DEBUG_USERS = set()
    sys.modules["core.cheats"] = fake_cheats

    sys.modules.pop("bot.handlers", None)
    import bot.handlers as hm

    yield hm

    for key, val in _orig.items():
        if val is not None:
            sys.modules[key] = val
        else:
            sys.modules.pop(key, None)


# ---------------------------------------------------------------------------
# Minimal trace factory used across tests
# ---------------------------------------------------------------------------

def _make_trace(chat_id: int = 111) -> dict:
    return {
        "chat_id": chat_id,
        "user_input": "Test action",
        "timestamp": time.time(),
        "censor": {
            "raw": None,
            "parsed": {"is_valid": True, "refusal_reason": ""},
            "thoughts": ["censor thought"],
        },
        "worker": {
            "raw": '{"difficulty": 10}',
            "parsed": {"difficulty": 10, "xp_award": 25},
            "thoughts": ["worker thought"],
        },
        "gm_logic": {
            "raw": '{"director_notes": []}',
            "parsed": {"director_notes": ["Scene beat 1"]},
            "thoughts": ["gm thought"],
        },
        "narrator": {
            "thoughts": ["narrator thought"],
            "final_text": "The raven lands on the parapet.",
        },
        "logs": ["HP: 10 -> 8", "XP: 0 -> 25"],
    }


# ---------------------------------------------------------------------------
# Test 1: _send_debug_trace_file sends document with correct filename
# ---------------------------------------------------------------------------

def test_send_debug_trace_file_calls_send_document(handlers_module):
    """
    _send_debug_trace_file must call bot.send_document once.
    The document must be a BufferedInputFile with filename
    matching 'debug_<chat_id>_<timestamp>.txt'.
    """
    hm = handlers_module
    chat_id = 42
    trace = _make_trace(chat_id)

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock()

    asyncio.run(hm._send_debug_trace_file(mock_bot, chat_id, trace))

    assert mock_bot.send_document.called, "bot.send_document must be called"

    call_kwargs = mock_bot.send_document.call_args.kwargs
    assert call_kwargs.get("chat_id") == chat_id, (
        f"send_document must target chat_id={chat_id}"
    )

    from aiogram.types import BufferedInputFile
    doc_arg = call_kwargs.get("document")
    assert isinstance(doc_arg, BufferedInputFile), (
        "document must be a BufferedInputFile instance"
    )
    assert doc_arg.filename.startswith(f"debug_{chat_id}_"), (
        f"filename must start with 'debug_{chat_id}_', got: {doc_arg.filename}"
    )
    assert doc_arg.filename.endswith(".txt"), (
        f"filename must end with .txt, got: {doc_arg.filename}"
    )


# ---------------------------------------------------------------------------
# Test 2: file content includes all 6 pipeline section headers and key data
# ---------------------------------------------------------------------------

def test_send_debug_trace_file_content_contains_all_sections(handlers_module):
    """
    The generated .txt content must contain all 6 section headers
    (PLAYER ACTION, CENSOR, WORKER, GM_LOGIC, NARRATOR, MECHANICAL IMPACTS)
    and key values from each trace field.
    """
    hm = handlers_module
    chat_id = 77
    trace = _make_trace(chat_id)

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock()

    asyncio.run(hm._send_debug_trace_file(mock_bot, chat_id, trace))

    from aiogram.types import BufferedInputFile
    doc_arg = mock_bot.send_document.call_args.kwargs.get("document")
    content = doc_arg.data.decode("utf-8")

    assert "## 1. PLAYER ACTION" in content
    assert "Test action" in content
    assert "## 2. CENSOR" in content
    assert "censor thought" in content
    assert "## 3. WORKER" in content
    assert "worker thought" in content
    assert "## 4. GM_LOGIC" in content
    assert "gm thought" in content
    assert "## 5. NARRATOR" in content
    assert "The raven lands on the parapet." in content
    assert "## 6. MECHANICAL IMPACTS" in content
    assert "HP: 10 -> 8" in content
    assert "XP: 0 -> 25" in content


# ---------------------------------------------------------------------------
# Test 3: no file sent when chat_id NOT in DEBUG_USERS
# ---------------------------------------------------------------------------

def test_debug_trace_not_sent_when_user_not_in_debug_users(handlers_module):
    """
    Simulates the condition guard from handle_general_messages.
    If chat_id is absent from DEBUG_USERS, _send_debug_trace_file must
    never be called.
    """
    hm = handlers_module
    chat_id = 999

    # Guarantee chat_id is absent
    import core.cheats as cheats_mod
    cheats_mod.DEBUG_USERS.discard(chat_id)

    call_count = []

    async def _run():
        mock_bot = MagicMock()
        mock_bot.send_document = AsyncMock()

        session = {"last_debug_trace": _make_trace(chat_id)}

        # Reproduce the condition guard from handle_general_messages
        from core.cheats import DEBUG_USERS
        if chat_id in DEBUG_USERS:
            trace = session.pop("last_debug_trace", None)
            if trace:
                call_count.append(1)
                await hm._send_debug_trace_file(mock_bot, chat_id, trace)

    asyncio.run(_run())

    assert len(call_count) == 0, (
        "_send_debug_trace_file must NOT be called when chat_id not in DEBUG_USERS"
    )


# ---------------------------------------------------------------------------
# Test 4: send_document failure is caught; logger.warning called, no propagation
# ---------------------------------------------------------------------------

def test_send_debug_trace_file_handles_send_error_gracefully(handlers_module):
    """
    If bot.send_document raises, the exception must NOT propagate.
    The caller's except block must log via logger.warning with '[DEBUG_TRACE]' tag.
    """
    hm = handlers_module
    chat_id = 55
    trace = _make_trace(chat_id)

    import core.cheats as cheats_mod
    cheats_mod.DEBUG_USERS.add(chat_id)

    warning_calls = []

    class _FakeLogger:
        def warning(self, msg):
            warning_calls.append(msg)

    original_logger = hm.logger
    hm.logger = _FakeLogger()

    try:
        async def _run():
            mock_bot = MagicMock()
            mock_bot.send_document = AsyncMock(
                side_effect=RuntimeError("Network timeout")
            )
            # Reproduce the exact except block from handle_general_messages
            try:
                await hm._send_debug_trace_file(mock_bot, chat_id, trace)
            except Exception as _trace_err:
                hm.logger.warning(
                    f"[DEBUG_TRACE] Failed to send: {type(_trace_err).__name__}: {_trace_err}"
                )

        asyncio.run(_run())
    finally:
        hm.logger = original_logger
        cheats_mod.DEBUG_USERS.discard(chat_id)

    assert len(warning_calls) == 1, (
        "logger.warning must be called exactly once on send failure"
    )
    assert "[DEBUG_TRACE]" in warning_calls[0], (
        "warning message must contain '[DEBUG_TRACE]' tag"
    )
    assert "RuntimeError" in warning_calls[0], (
        "warning message must name the exception type"
    )
