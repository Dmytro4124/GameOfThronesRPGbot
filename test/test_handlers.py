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
    fake_engine.commit_narration_to_history = AsyncMock()
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



# ===========================================================================
# Narrator A/B (blind vote) handlers
# ===========================================================================

import asyncio as _asyncio
import json as _json
import time as _time

import core.narrator_ab as _na

_USER = 5551            # not in ADMIN_TELEGRAM_IDS
_ADMIN = 494157543      # in ADMIN_TELEGRAM_IDS (config.py)
_mid_counter = [10_000]


@pytest.fixture
def ab(handlers_module, tmp_path, monkeypatch):
    """Isolated A/B state: temp JSONL log, clean pending, stubbed send helpers."""
    hm = handlers_module
    log = tmp_path / "ab.jsonl"
    monkeypatch.setattr(_na, "NARRATOR_AB_LOG_PATH", str(log))
    monkeypatch.setattr(hm, "NARRATOR_AB_LOG_PATH", str(log))
    _na._pending.clear()
    _na._last_turn_id.clear()
    _na._log_lock = _asyncio.Lock()
    hm.user_sessions.clear()
    hm.active_processing.discard(_USER)
    commit = AsyncMock()
    monkeypatch.setattr(sys.modules["core.engine"], "commit_narration_to_history", commit)
    send_game = AsyncMock()
    monkeypatch.setattr(hm, "send_game_response", send_game)
    monkeypatch.setattr(hm, "_check_and_trigger_asi", AsyncMock())
    monkeypatch.setattr(hm, "keep_typing", AsyncMock())
    ns = types.SimpleNamespace(hm=hm, log=log, commit=commit, send_game=send_game)
    yield ns
    _na._pending.clear()
    _na._last_turn_id.clear()
    hm.user_sessions.clear()
    hm.active_processing.discard(_USER)


def _results(first="gemma"):
    other = "flash_lite" if first == "gemma" else "gemma"
    return [_na.NarrationResult(model_key=first, text=f"TEXT-{first}", total_ms=10),
            _na.NarrationResult(model_key=other, text=f"TEXT-{other}", total_ms=20)]


def _mk_pending(chat_id=_USER, turn_id="tid00001", created=None, order=None, variant_ids=None):
    order = order or _results("gemma")
    rec = _na.build_log_record(
        turn_id=turn_id, user_id=chat_id, chat_id=chat_id, mode="NORMAL", narrator_prompt="P",
        mechanics={}, results=order, shown_order=[r.model_key for r in order])
    p = _na.PendingChoice(
        turn_id=turn_id, chat_id=chat_id, user_id=chat_id,
        created=_time.time() if created is None else created, order=order,
        change_log="\n\n📊 log", suggested_actions=["a", "b", "c", "d"],
        deferred_history={"user_input": "inspect", "mech_updates": {"m": 1}}, log_record=rec,
        variant_message_ids=list(variant_ids or []))
    _na.set_pending(p)
    return p


def _mk_message(chat_id=_USER, text="do something", user_id=None):
    _mid_counter[0] += 1
    m = MagicMock()
    m.chat.id = chat_id
    m.message_id = _mid_counter[0]
    m.text = text
    m.from_user.id = user_id or chat_id
    m.answer = AsyncMock()
    temp = MagicMock()
    temp.delete = AsyncMock()
    temp.edit_text = AsyncMock()
    m.reply = AsyncMock(return_value=temp)
    m.temp = temp
    return m


def _mk_cq(data, chat_id=_USER):
    cq = MagicMock()
    cq.from_user.id = chat_id
    cq.message = None  # inaccessible message: handler must not rely on cq.message
    cq.data = data
    cq.answer = AsyncMock()
    return cq


def _mk_bot():
    bot = MagicMock()
    bot.delete_message = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    return bot


def _log_rows(path):
    if not path.exists():
        return []
    return [_json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _arun(coro):
    return _asyncio.run(coro)


# --- gate -------------------------------------------------------------------

def test_ab_gate_blocks_new_turn_while_pending(ab):
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": []}
    _mk_pending()
    msg = _mk_message()
    with patch.object(hm, "process_game_turn", AsyncMock()) as pgt:
        _arun(hm.handle_general_messages(msg, _mk_bot()))
    pgt.assert_not_awaited()
    msg.answer.assert_awaited_once()
    assert "Спершу обери варіант" in msg.answer.await_args.args[0]
    assert _USER not in hm.active_processing
    assert _na.get_pending(_USER) is not None
    ab.commit.assert_not_awaited()


def test_ab_gate_expired_pending_autocommits_and_turn_proceeds(ab):
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": []}
    _mk_pending(created=_time.time() - _na.NARRATOR_AB_CHOICE_TTL - 60)
    msg = _mk_message()
    with patch.object(hm, "process_game_turn", AsyncMock(return_value=("narr", ["x"]))) as pgt:
        _arun(hm.handle_general_messages(msg, _mk_bot()))
    ab.commit.assert_awaited_once()
    assert ab.commit.await_args.args[1] == "inspect"
    assert ab.commit.await_args.args[2] in ("TEXT-gemma", "TEXT-flash_lite")
    pgt.assert_awaited_once()
    rows = _log_rows(ab.log)
    assert len(rows) == 1 and rows[0]["reason"] == "ttl_expired"
    assert rows[0]["vote"] is None and rows[0]["vote_raw"] is None
    assert _na.get_pending(_USER) is None
    ab.send_game.assert_awaited_once()
    assert _USER not in hm.active_processing


def test_ab_no_pending_flow_unchanged_and_display_intent_shown(ab):
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": [],
                               "action_intents": {"Button": "Full intent text"}}
    msg = _mk_message(text="👉 Button")
    with patch.object(hm, "process_game_turn", AsyncMock(return_value=("narr", []))):
        _arun(hm.handle_general_messages(msg, _mk_bot()))
    sent = ab.send_game.await_args.args[2]
    assert sent.startswith("🗣️ _Full intent text_") and sent.endswith("narr")


# --- pair mode display ------------------------------------------------------

def test_ab_pair_sends_two_variants_and_vote_buttons(ab):
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": []}
    bot = _mk_bot()
    ids = iter([[101], [102]])

    async def fake_pgt(chat_id, text, **kw):
        _mk_pending(chat_id=chat_id, turn_id="tid_pair")
        return "\n\n📊 log", ["a", "b", "c", "d"]

    msg = _mk_message()
    with patch.object(hm, "process_game_turn", side_effect=fake_pgt), \
            patch.object(hm, "send_safe_message", AsyncMock(side_effect=lambda *a, **k: next(ids))) as ssm:
        _arun(hm.handle_general_messages(msg, bot))
    assert ssm.await_count == 2
    t1, t2 = ssm.await_args_list[0].args[2], ssm.await_args_list[1].args[2]
    assert "Варіант 1" in t1 and "Варіант 2" in t2
    p = _na.get_pending(_USER)
    assert p.variant_message_ids == [101, 102, 999]
    kb = bot.send_message.await_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == ["ab_tid_pair_1", "ab_tid_pair_2", "ab_tid_pair_tie"]
    ab.send_game.assert_not_awaited()
    msg.temp.delete.assert_awaited()
    assert _USER not in hm.active_processing


def test_ab_pair_variant_headers_do_not_reveal_model(ab):
    hm = ab.hm
    p = _mk_pending()
    with patch.object(hm, "send_safe_message", AsyncMock(return_value=[1])) as ssm:
        _arun(hm._send_ab_variants(_mk_bot(), _USER, p))
    for c in ssm.await_args_list:
        header = c.args[2].split("\n\n")[0].lower()
        assert "gemma" not in header and "flash" not in header


def test_ab_pair_send_failure_autocommits_and_unblocks(ab):
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": []}

    async def fake_pgt(chat_id, text, **kw):
        _mk_pending(chat_id=chat_id, turn_id="tid_fail")
        return "\n\n📊 log", ["a", "b", "c", "d"]

    msg = _mk_message()
    with patch.object(hm, "process_game_turn", side_effect=fake_pgt), \
            patch.object(hm, "send_safe_message", AsyncMock(side_effect=RuntimeError("tg down"))):
        _arun(hm.handle_general_messages(msg, _mk_bot()))
    assert _na.get_pending(_USER) is None
    ab.commit.assert_awaited_once()
    ab.send_game.assert_awaited_once()
    assert _log_rows(ab.log)[0]["reason"] == "send_failed"


def test_ab_pair_display_intent_prefix_shown(ab):
    """Expected: like the normal path, the player sees the intent they chose (button text)."""
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": [],
                               "action_intents": {"Button": "Full intent text"}}

    async def fake_pgt(chat_id, text, **kw):
        _mk_pending(chat_id=chat_id, turn_id="tid_di")
        return "\n\n📊 log", []

    msg = _mk_message(text="👉 Button")
    bot = _mk_bot()
    with patch.object(hm, "process_game_turn", side_effect=fake_pgt), \
            patch.object(hm, "send_safe_message", AsyncMock(return_value=[1])) as ssm:
        _arun(hm.handle_general_messages(msg, bot))
    shown = " ".join(c.args[2] for c in ssm.await_args_list)
    assert "Full intent text" in shown


# --- vote callback ----------------------------------------------------------

@pytest.mark.parametrize("pick,expected_text,expected_vote", [
    ("1", "TEXT-gemma", "gemma"),
    ("2", "TEXT-flash_lite", "flash_lite"),
])
def test_ab_callback_pick(ab, pick, expected_text, expected_vote):
    hm = ab.hm
    _mk_pending(variant_ids=[11, 12, 13])
    bot = _mk_bot()
    cq = _mk_cq(f"ab_tid00001_{pick}")
    _arun(hm.callback_ab_vote(cq, bot))
    ab.commit.assert_awaited_once_with(_USER, "inspect", expected_text, {"m": 1})
    assert [c.args for c in bot.delete_message.await_args_list] == [(_USER, 11), (_USER, 12), (_USER, 13)]
    ab.send_game.assert_awaited_once_with(bot, _USER, expected_text + "\n\n📊 log", ["a", "b", "c", "d"])
    (row,) = _log_rows(ab.log)
    assert row["vote"] == expected_vote and row["vote_raw"] == pick
    assert isinstance(row["vote_ms"], int) and row["vote_ms"] >= 0
    assert _na.get_pending(_USER) is None
    cq.answer.assert_awaited()


def test_ab_callback_tie(ab):
    hm = ab.hm
    _mk_pending(variant_ids=[11])
    bot = _mk_bot()
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_tie"), bot))
    ab.commit.assert_awaited_once()
    assert ab.commit.await_args.args[2] in ("TEXT-gemma", "TEXT-flash_lite")
    (row,) = _log_rows(ab.log)
    assert row["vote"] == "tie" and row["vote_raw"] == "tie"
    ab.send_game.assert_awaited_once()
    assert ab.send_game.await_args.args[2].startswith(ab.commit.await_args.args[2])


def test_ab_callback_shuffle_order_respected(ab):
    hm = ab.hm
    _mk_pending(order=_results("flash_lite"))  # variant 1 is flash_lite
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_1"), _mk_bot()))
    assert ab.commit.await_args.args[2] == "TEXT-flash_lite"
    assert _log_rows(ab.log)[0]["vote"] == "flash_lite"


def test_ab_callback_double_click_is_stale(ab):
    hm = ab.hm
    _mk_pending()
    bot = _mk_bot()
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_1"), bot))
    cq2 = _mk_cq("ab_tid00001_2")
    _arun(hm.callback_ab_vote(cq2, bot))
    assert ab.commit.await_count == 1 and ab.send_game.await_count == 1
    assert len(_log_rows(ab.log)) == 1
    assert "застарів" in cq2.answer.await_args.args[0]


def test_ab_callback_wrong_turn_id_is_stale_and_keeps_pending(ab):
    hm = ab.hm
    _mk_pending()
    cq = _mk_cq("ab_OTHER_1")
    _arun(hm.callback_ab_vote(cq, _mk_bot()))
    assert "застарів" in cq.answer.await_args.args[0]
    assert _na.get_pending(_USER) is not None
    ab.commit.assert_not_awaited()


@pytest.mark.parametrize("data", ["ab_", "ab_x", "ab_tid_9", "ab_a_b_c_1"])
def test_ab_callback_malformed_data(ab, data):
    cq = _mk_cq(data)
    _arun(ab.hm.callback_ab_vote(cq, _mk_bot()))
    assert "застарів" in cq.answer.await_args.args[0]
    ab.commit.assert_not_awaited()


def test_ab_callback_commit_failure_still_logs_and_sends(ab):
    hm = ab.hm
    _mk_pending()
    ab.commit.side_effect = RuntimeError("summarizer down")
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_1"), _mk_bot()))
    (row,) = _log_rows(ab.log)
    assert row["vote"] == "gemma" and row["reason"].startswith("commit_error")
    ab.send_game.assert_awaited_once()


def test_ab_callback_delete_message_errors_do_not_block(ab):
    hm = ab.hm
    _mk_pending(variant_ids=[1, 2])
    bot = _mk_bot()
    from aiogram.exceptions import TelegramBadRequest
    bot.delete_message.side_effect = [TelegramBadRequest(method=MagicMock(), message="gone"), RuntimeError("x")]
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_1"), bot))
    ab.send_game.assert_awaited_once()


def test_ab_callback_other_chat_cannot_vote_on_foreign_pending(ab):
    hm = ab.hm
    _mk_pending(chat_id=_USER)
    cq = _mk_cq("ab_tid00001_1", chat_id=777)
    _arun(hm.callback_ab_vote(cq, _mk_bot()))
    assert _na.get_pending(_USER) is not None
    ab.commit.assert_not_awaited()


def test_ab_callback_while_processing_keeps_pending_and_asks_wait(ab):
    hm = ab.hm
    _mk_pending()
    hm.active_processing.add(_USER)
    cq = _mk_cq("ab_tid00001_1")
    _arun(hm.callback_ab_vote(cq, _mk_bot()))
    assert _na.get_pending(_USER) is not None
    ab.commit.assert_not_awaited()
    cq.answer.assert_awaited_once_with("Зачекай, хід ще обробляється")
    assert _USER in hm.active_processing  # foreign lock is not released


def test_ab_callback_releases_lock_even_if_commit_raises(ab):
    hm = ab.hm
    _mk_pending()
    ab.commit.side_effect = RuntimeError("boom")
    ab.send_game.side_effect = RuntimeError("send boom")
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_1"), _mk_bot()))
    assert _USER not in hm.active_processing
    assert _na.get_pending(_USER) is None


def test_ab_callback_holds_lock_during_commit_and_send(ab):
    hm = ab.hm
    _mk_pending()
    seen = []

    async def _commit(*a, **k):
        seen.append(("commit", _USER in hm.active_processing))

    async def _send(*a, **k):
        seen.append(("send", _USER in hm.active_processing))
    ab.commit.side_effect = _commit
    ab.send_game.side_effect = _send
    _arun(hm.callback_ab_vote(_mk_cq("ab_tid00001_1"), _mk_bot()))
    assert seen == [("commit", True), ("send", True)]
    assert _USER not in hm.active_processing


def test_ab_callback_stale_releases_lock(ab):
    hm = ab.hm
    cq = _mk_cq("ab_nope_1")
    _arun(hm.callback_ab_vote(cq, _mk_bot()))
    assert _USER not in hm.active_processing
    cq.answer.assert_awaited_once_with("Вибір вже зроблено або застарів")


def test_ab_expired_commit_happens_before_process_game_turn_under_lock(ab):
    hm = ab.hm
    hm.user_sessions[_USER] = {"state": "GAME_ACTIVE", "history": []}
    _mk_pending(created=_time.time() - _na.NARRATOR_AB_CHOICE_TTL - 60)
    order = []

    async def _commit(*a, **k):
        order.append(("commit", _USER in hm.active_processing))

    async def _pgt(*a, **k):
        order.append(("turn", _USER in hm.active_processing))
        return ("narr", [])
    ab.commit.side_effect = _commit
    with patch.object(hm, "process_game_turn", AsyncMock(side_effect=_pgt)):
        _arun(hm.handle_general_messages(_mk_message(), _mk_bot()))
    assert order == [("commit", True), ("turn", True)]
    assert _USER not in hm.active_processing


# --- restart / abandon ------------------------------------------------------

def test_ab_abandon_pending_logs_reason_without_commit(ab):
    hm = ab.hm
    _mk_pending()
    _arun(hm._abandon_pending(_USER))
    assert _na.get_pending(_USER) is None
    ab.commit.assert_not_awaited()
    (row,) = _log_rows(ab.log)
    assert row["reason"] == "abandoned" and row["vote"] is None


def test_ab_abandon_without_pending_is_noop(ab):
    _arun(ab.hm._abandon_pending(_USER))
    assert _log_rows(ab.log) == []


def test_ab_restart_confirm_abandons_pending(ab):
    hm = ab.hm
    _mk_pending()
    cq = MagicMock()
    cq.message.chat.id = _USER
    cq.from_user.id = _USER
    cq.message.edit_reply_markup = AsyncMock()
    cq.message.answer = AsyncMock()
    with patch.object(hm, "delete_user_data", AsyncMock()), \
            patch.object(hm, "delete_user_npc_sheet", AsyncMock()), \
            patch.object(hm, "clear_npc_cache", MagicMock()), \
            patch.object(hm, "get_unique_regions", AsyncMock(return_value=["Північ"])):
        _arun(hm.callback_restart_confirm_handler(cq))
    assert _na.get_pending(_USER) is None
    ab.commit.assert_not_awaited()
    assert _log_rows(ab.log)[0]["reason"] == "abandoned"


# --- /ab_note ---------------------------------------------------------------

def test_ab_note_without_text_shows_usage(ab):
    msg = _mk_message()
    _arun(ab.hm.cmd_ab_note(msg, MagicMock(args=None)))
    assert "Використання" in msg.answer.await_args.args[0]
    assert _log_rows(ab.log) == []


def test_ab_note_without_last_turn(ab):
    msg = _mk_message()
    _arun(ab.hm.cmd_ab_note(msg, MagicMock(args="nice")))
    assert "Немає A/B-ходу" in msg.answer.await_args.args[0]
    assert _log_rows(ab.log) == []


def test_ab_note_with_last_turn_writes_note(ab):
    _mk_pending(turn_id="tid_note")
    msg = _mk_message()
    _arun(ab.hm.cmd_ab_note(msg, MagicMock(args="  too many cliches  ")))
    assert "збережено" in msg.answer.await_args.args[0]
    (row,) = _log_rows(ab.log)
    assert row["type"] == "note" and row["turn_id"] == "tid_note" and row["note"] == "too many cliches"


# --- /ab_stats --------------------------------------------------------------

def _seed_stats(path):
    rows = [
        {"type": "turn", "shown_order": ["gemma", "flash_lite"], "vote": "gemma", "vote_raw": "1"},
        {"type": "turn", "shown_order": ["flash_lite", "gemma"], "vote": "gemma", "vote_raw": "2"},
        {"type": "turn", "shown_order": ["gemma", "flash_lite"], "vote": "flash_lite", "vote_raw": "2"},
        {"type": "turn", "shown_order": ["gemma", "flash_lite"], "vote": "tie", "vote_raw": "tie"},
        {"type": "turn", "shown_order": ["gemma", "flash_lite"], "vote": None, "vote_raw": None, "reason": "ttl_expired"},
        {"type": "turn", "shown_order": None, "vote": None, "reason": "single_variant"},
        {"type": "note", "note": "x"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(_json.dumps(r) + "\n")
        f.write("{broken\n")


def test_ab_stats_non_admin_ignored(ab):
    _seed_stats(ab.log)
    msg = _mk_message(chat_id=_USER)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args=None)))
    msg.answer.assert_not_awaited()


def test_ab_stats_admin_hides_model_wins_by_default(ab):
    _seed_stats(ab.log)
    msg = _mk_message(chat_id=_ADMIN)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args=None)))
    out = msg.answer.await_args.args[0]
    assert "ходів з парою 5" in out and "Нічиї: 1" in out and "Без голосу: 1" in out
    assert "Варіант 1" in out and "(1/3)" in out
    assert "gemma" not in out.lower() and "flash" not in out.lower()


def test_ab_stats_admin_reveal_shows_model_wins(ab):
    _seed_stats(ab.log)
    msg = _mk_message(chat_id=_ADMIN)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args="reveal")))
    out = msg.answer.await_args.args[0]
    assert "Перемоги gemma: 2" in out and "Перемоги flash_lite: 1" in out


def test_ab_stats_missing_log_does_not_crash(ab):
    msg = _mk_message(chat_id=_ADMIN)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args=None)))
    assert "ходів з парою 0" in msg.answer.await_args.args[0]


# --- /ab_stats source + /ab_export (Sheets sink) ------------------------------

def _set_sheet_records(monkeypatch, value=None, exc=None):
    ops_mod = sys.modules["database.operations"]
    fn = AsyncMock(side_effect=exc) if exc is not None else AsyncMock(return_value=value)
    monkeypatch.setattr(ops_mod, "read_ab_log_records", fn, raising=False)
    return fn


_SHEET_RECS = [
    {"type": "turn", "shown_order": ["gemma", "flash_lite"], "vote": "gemma", "vote_raw": "1"},
    {"type": "turn", "shown_order": ["gemma", "flash_lite"], "vote": "tie", "vote_raw": "tie"},
    {"type": "note", "note": "n"},
]


def _export_ctx():
    bot = MagicMock()
    bot.send_document = AsyncMock()
    return bot


def test_ab_stats_uses_sheets_source(ab, monkeypatch):
    _set_sheet_records(monkeypatch, _SHEET_RECS)
    msg = _mk_message(chat_id=_ADMIN)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args=None)))
    out = msg.answer.await_args.args[0]
    assert "ходів з парою 2" in out and "Нічиї: 1" in out and "джерело: Sheets" in out


def test_ab_stats_falls_back_to_file_on_empty_sheet(ab, monkeypatch):
    _seed_stats(ab.log)
    _set_sheet_records(monkeypatch, [])
    msg = _mk_message(chat_id=_ADMIN)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args=None)))
    out = msg.answer.await_args.args[0]
    assert "ходів з парою 5" in out and "джерело: файл" in out


def test_ab_stats_falls_back_to_file_on_exception(ab, monkeypatch):
    _seed_stats(ab.log)
    _set_sheet_records(monkeypatch, exc=RuntimeError("sheets down"))
    msg = _mk_message(chat_id=_ADMIN)
    _arun(ab.hm.cmd_ab_stats(msg, MagicMock(args=None)))
    out = msg.answer.await_args.args[0]
    assert "ходів з парою 5" in out and "джерело: файл" in out


def test_ab_export_non_admin_ignored(ab, monkeypatch):
    _set_sheet_records(monkeypatch, _SHEET_RECS)
    msg, bot = _mk_message(chat_id=_USER), _export_ctx()
    _arun(ab.hm.cmd_ab_export(msg, bot))
    bot.send_document.assert_not_awaited()
    msg.answer.assert_not_awaited()


def test_ab_export_empty_log(ab, monkeypatch):
    _set_sheet_records(monkeypatch, [])
    msg, bot = _mk_message(chat_id=_ADMIN), _export_ctx()
    _arun(ab.hm.cmd_ab_export(msg, bot))
    bot.send_document.assert_not_awaited()
    assert "Лог порожній" in msg.answer.await_args.args[0]


def test_ab_export_admin_sends_valid_jsonl(ab, monkeypatch):
    recs = _SHEET_RECS + [{"type": "turn", "turn_id": "ukr", "results": {"gemma": {"text": "Привіт\nсвіте"}}}]
    _set_sheet_records(monkeypatch, recs)
    msg, bot = _mk_message(chat_id=_ADMIN), _export_ctx()
    _arun(ab.hm.cmd_ab_export(msg, bot))
    bot.send_document.assert_awaited_once()
    args = bot.send_document.await_args.args
    assert args[0] == _ADMIN
    doc = args[1]
    assert doc.filename.startswith("narrator_ab_") and doc.filename.endswith(".jsonl")
    lines = doc.data.decode("utf-8").splitlines()
    assert [_json.loads(l) for l in lines] == recs


def test_ab_export_file_fallback(ab, monkeypatch):
    _seed_stats(ab.log)
    _set_sheet_records(monkeypatch, [])
    msg, bot = _mk_message(chat_id=_ADMIN), _export_ctx()
    _arun(ab.hm.cmd_ab_export(msg, bot))
    lines = bot.send_document.await_args.args[1].data.decode("utf-8").splitlines()
    assert len(lines) == 7  # broken line skipped
