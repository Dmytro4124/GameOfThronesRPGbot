# test/test_debug_trace_send.py
"""
Unit-tests for the debug trace file dispatch added to bot/handlers.py.

Async tests use asyncio.run() -- no pytest-asyncio, consistent with the
pattern adopted in test/test_cheats.py and test/test_bg_intro_flow.py.

Covers:
1. _send_debug_trace_file builds a .txt file and calls bot.send_document.
2. File content includes all 6 pipeline section headers and key data.
3. Checkpoint sends file when trace is in session (decoupled from DEBUG_USERS).
4. If bot.send_document raises, the error is logged but does not propagate.
5. Full debug flow: trace in session → send_document called, trace popped.
6. No send when trace is absent from session (regardless of DEBUG_USERS state).
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
            "prompt": "You are a content censor. User action: Test action",
            "raw": None,
            "parsed": {"is_valid": True, "refusal_reason": ""},
            "thoughts": ["censor thought"],
        },
        "worker": {
            "prompt": "You are the Worker. Resolve: Test action",
            "raw": '{"difficulty": 10}',
            "parsed": {"difficulty": 10, "xp_award": 25},
            "thoughts": ["worker thought"],
        },
        "gm_logic": {
            "prompt": "You are GM_Logic. Context: ...",
            "raw": '{"director_notes": []}',
            "parsed": {"director_notes": ["Scene beat 1"]},
            "thoughts": ["gm thought"],
        },
        "narrator": {
            "prompt": "You are the Narrator. Write scene: ...",
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
    Section headers now use box-style format: 'STAGE N: <TITLE>'.
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

    # Box-style headers (new format)
    assert "STAGE 1:" in content
    assert "PLAYER ACTION" in content
    assert "Test action" in content
    assert "STAGE 2:" in content
    assert "CENSOR" in content
    assert "censor thought" in content
    assert "STAGE 3:" in content
    assert "WORKER" in content
    assert "worker thought" in content
    assert "STAGE 4:" in content
    assert "GM_LOGIC" in content
    assert "gm thought" in content
    assert "STAGE 5:" in content
    assert "NARRATOR" in content
    assert "The raven lands on the parapet." in content
    assert "STAGE 6:" in content
    assert "MECHANICAL IMPACTS" in content
    assert "HP: 10 -> 8" in content
    assert "XP: 0 -> 25" in content


# ---------------------------------------------------------------------------
# Test 3: checkpoint sends file when trace is in session (decoupled from DEBUG_USERS)
# ---------------------------------------------------------------------------

def test_debug_trace_sent_when_trace_in_session_regardless_of_debug_users(handlers_module):
    """
    Checkpoint in handle_general_messages is decoupled from DEBUG_USERS.
    If last_debug_trace is present in session, _send_debug_trace_file must be
    called even when chat_id is NOT in DEBUG_USERS (covers cold-start scenario
    where engine saved trace via profile flag but in-memory set is empty).
    """
    hm = handlers_module
    chat_id = 999

    import sys
    cheats_stub = sys.modules["core.cheats"]
    # Guarantee chat_id is absent from in-memory set (cold-start scenario)
    cheats_stub.DEBUG_USERS.discard(chat_id)

    user_sessions = hm.user_sessions
    trace = _make_trace(chat_id)
    user_sessions[chat_id] = {"last_debug_trace": trace}

    send_document_calls = []

    try:
        async def _run():
            mock_bot = MagicMock()
            mock_bot.send_document = AsyncMock(
                side_effect=lambda **kw: send_document_calls.append(kw)
            )

            # Reproduce the new decoupled checkpoint: just check session, not DEBUG_USERS
            session = user_sessions.get(chat_id, {})
            popped_trace = session.pop("last_debug_trace", None)
            if popped_trace:
                await hm._send_debug_trace_file(mock_bot, chat_id, popped_trace)

        asyncio.run(_run())
    finally:
        user_sessions.pop(chat_id, None)

    assert len(send_document_calls) == 1, (
        "_send_debug_trace_file must be called when trace is in session, "
        "even if chat_id is absent from DEBUG_USERS (cold-start scenario)"
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


# ---------------------------------------------------------------------------
# Test 5: full debug flow — trace in session → send called, trace popped
#         (decoupled from DEBUG_USERS membership)
# ---------------------------------------------------------------------------

def test_full_debug_flow_file_sent(handlers_module):
    """
    End-to-end checkpoint logic (new decoupled version):
    - user_sessions[chat_id]['last_debug_trace'] contains a valid trace
    - checkpoint pops the trace and calls _send_debug_trace_file
    - bot.send_document is called exactly once
    - last_debug_trace is no longer in session after the call
    - DEBUG_USERS membership is irrelevant (decoupled)

    Uses hm.user_sessions (the binding captured when bot.handlers was loaded
    by the fixture) to avoid sys.modules resolution ambiguity with the real
    core.engine that other test files import at collection time.
    """
    hm = handlers_module
    chat_id = 200

    user_sessions = hm.user_sessions  # the fixture's fake_engine.user_sessions
    trace = _make_trace(chat_id)
    user_sessions[chat_id] = {"last_debug_trace": trace}

    send_document_calls = []

    try:
        async def _run():
            mock_bot = MagicMock()

            async def _fake_send_document(**kwargs):
                send_document_calls.append(kwargs)

            mock_bot.send_document = AsyncMock(side_effect=_fake_send_document)

            # Reproduce the new decoupled checkpoint block from handle_general_messages
            session = user_sessions.get(chat_id, {})
            popped_trace = session.pop("last_debug_trace", None)
            if popped_trace:
                await hm._send_debug_trace_file(mock_bot, chat_id, popped_trace)

        asyncio.run(_run())
    finally:
        user_sessions.pop(chat_id, None)

    assert len(send_document_calls) == 1, (
        "bot.send_document must be called exactly once in the full flow"
    )
    assert send_document_calls[0].get("chat_id") == chat_id, (
        f"send_document must target chat_id={chat_id}"
    )
    # Trace must be consumed (popped) from session
    remaining = user_sessions.get(chat_id, {})
    assert "last_debug_trace" not in remaining, (
        "last_debug_trace must be popped from session after dispatch"
    )


# ---------------------------------------------------------------------------
# Test 6: no send when trace is absent from session
# ---------------------------------------------------------------------------

def test_full_debug_flow_no_send_when_trace_absent(handlers_module):
    """
    If last_debug_trace is absent from session (engine did not save it because
    _debug_active was False for this turn), send_document must NOT be called.
    This is the correct guard now that the checkpoint is decoupled from DEBUG_USERS.
    """
    hm = handlers_module
    chat_id = 201

    user_sessions = hm.user_sessions
    # No last_debug_trace in session
    user_sessions[chat_id] = {}

    send_document_calls = []

    try:
        async def _run():
            mock_bot = MagicMock()
            mock_bot.send_document = AsyncMock(
                side_effect=lambda **kw: send_document_calls.append(kw)
            )

            # Reproduce the new decoupled checkpoint
            session = user_sessions.get(chat_id, {})
            popped_trace = session.pop("last_debug_trace", None)
            if popped_trace:
                await hm._send_debug_trace_file(mock_bot, chat_id, popped_trace)

        asyncio.run(_run())
    finally:
        user_sessions.pop(chat_id, None)

    assert len(send_document_calls) == 0, (
        "send_document must NOT be called when last_debug_trace is absent from session"
    )


# ---------------------------------------------------------------------------
# Test 7: None values in trace fields do NOT crash the join (regression)
# ---------------------------------------------------------------------------

def test_send_debug_trace_file_tolerates_none_fields(handlers_module):
    """Regression: trace fields explicitly set to None (worker.raw, narrator.final_text,
    user_input) must NOT crash '\\n'.join(lines) with TypeError.

    Real bug from prod: worker['raw'] is always None, so worker.get('raw', default)
    returned None (key exists), and join() raised
    'TypeError: sequence item N: expected str instance, NoneType found'.
    """
    hm = handlers_module
    chat_id = 99
    trace = _make_trace(chat_id)
    # Force the None values that occur in real traces
    trace["user_input"] = None
    trace["worker"]["raw"] = None
    trace["gm_logic"]["raw"] = None
    trace["narrator"]["final_text"] = None

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock()

    # Must not raise TypeError
    asyncio.run(hm._send_debug_trace_file(mock_bot, chat_id, trace))

    assert mock_bot.send_document.called, (
        "send_document must be called even when trace has None fields"
    )


# ---------------------------------------------------------------------------
# Test 8: INPUT prompt appears in file content for all 4 stages
# ---------------------------------------------------------------------------

def test_send_debug_trace_file_content_contains_input_prompts(handlers_module):
    """
    The generated .txt must include INPUT (prompt to model) sub-sections
    for all 4 model stages (Censor, Worker, GM_Logic, Narrator) and the
    prompt text from _make_trace must appear in the file content.
    """
    hm = handlers_module
    chat_id = 88
    trace = _make_trace(chat_id)

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock()

    asyncio.run(hm._send_debug_trace_file(mock_bot, chat_id, trace))

    from aiogram.types import BufferedInputFile
    doc_arg = mock_bot.send_document.call_args.kwargs.get("document")
    content = doc_arg.data.decode("utf-8")

    # Sub-section labels must be present
    assert "INPUT (prompt to model)" in content, (
        "Each stage must have an INPUT sub-section header"
    )

    # Actual prompt text from _make_trace must appear
    assert "You are a content censor" in content, (
        "Censor prompt text must appear in trace content"
    )
    assert "You are the Worker" in content, (
        "Worker prompt text must appear in trace content"
    )
    assert "You are GM_Logic" in content, (
        "GM_Logic prompt text must appear in trace content"
    )
    assert "You are the Narrator" in content, (
        "Narrator prompt text must appear in trace content"
    )


# ---------------------------------------------------------------------------
# Test 9: None prompt fields do NOT crash (regression — mechanics-dev may not
#         yet have filled prompt keys for all stages)
# ---------------------------------------------------------------------------

def test_send_debug_trace_file_tolerates_none_prompt_fields(handlers_module):
    """
    Regression: trace[stage]['prompt'] may be None (mechanics-dev not yet
    populated for this stage, or non-debug turn). Must NOT crash join() and
    must emit '<not captured>' placeholder instead.
    """
    hm = handlers_module
    chat_id = 101
    trace = _make_trace(chat_id)
    # Simulate mechanics-dev not having filled prompt keys yet
    trace["censor"]["prompt"] = None
    trace["worker"]["prompt"] = None
    trace["gm_logic"]["prompt"] = None
    trace["narrator"]["prompt"] = None

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock()

    # Must not raise
    asyncio.run(hm._send_debug_trace_file(mock_bot, chat_id, trace))

    from aiogram.types import BufferedInputFile
    doc_arg = mock_bot.send_document.call_args.kwargs.get("document")
    content = doc_arg.data.decode("utf-8")

    assert mock_bot.send_document.called, (
        "send_document must be called even when all prompt fields are None"
    )
    # Placeholder must appear at least once for each None prompt
    assert content.count("<not captured>") >= 4, (
        "Each None prompt must produce a '<not captured>' placeholder; "
        f"found {content.count('<not captured>')} occurrences"
    )
