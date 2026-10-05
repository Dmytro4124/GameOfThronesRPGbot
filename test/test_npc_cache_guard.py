"""A1: lazy NPC-cache guard у core/engine.py::process_game_turn (+ A2-prompt, A2/A3-аудит даних).

Без мережі/Sheets: get_user_data і refresh_npc_database замоковані; хід навмисно обривається одразу
після guard (eng.safe_int кидає) -- перевіряємо лише поведінку guard, не весь pipeline.
"""
import asyncio
import re
from unittest.mock import patch, AsyncMock

import pytest

import core.engine as eng

CID = 987001


class _Abort(Exception):
    pass


def _turn(refresh, session=None, now=1000.0):
    """Один виклик process_game_turn; повертає (результат, user_sessions[CID])."""
    if session is not None:
        eng.user_sessions[CID] = session
    else:
        eng.user_sessions.pop(CID, None)

    async def _run():
        with patch.object(eng, "get_user_data", AsyncMock(return_value=({"Ім'я": "Т"}, 1))), \
                patch.object(eng, "refresh_npc_database", refresh), \
                patch.object(eng, "safe_int", side_effect=_Abort("stop after guard")), \
                patch.object(eng.time, "time", return_value=now_holder["t"]):
            try:
                return await eng.process_game_turn(CID, "привіт")
            except _Abort:
                return None

    now_holder = {"t": now}
    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _clean():
    eng.user_sessions.pop(CID, None)
    yield
    eng.user_sessions.pop(CID, None)


def test_empty_cache_triggers_one_refresh():
    refresh = AsyncMock(return_value=True)
    _turn(refresh)
    assert refresh.await_count == 1
    refresh.assert_awaited_with(CID)


def test_filled_cache_is_noop():
    refresh = AsyncMock()
    _turn(refresh, session={"state": "GAME_ACTIVE", "history": [], "npc_cache": {"A": {}}, "dead_npc_names": set()})
    assert refresh.await_count == 0


def test_second_turn_within_60s_throttled_when_still_empty():
    refresh = AsyncMock(return_value=False)
    _turn(refresh, now=1000.0)
    _turn(refresh, session=eng.user_sessions[CID], now=1030.0)
    assert refresh.await_count == 1


def test_turn_after_60s_retries():
    refresh = AsyncMock(return_value=False)
    _turn(refresh, now=1000.0)
    _turn(refresh, session=eng.user_sessions[CID], now=1061.0)
    assert refresh.await_count == 2


def test_timestamp_set_before_await():
    seen = {}

    async def _refresh(chat_id):
        seen["ts"] = eng.user_sessions[chat_id].get("_npc_cache_attempt_ts")
        return True

    _turn(AsyncMock(side_effect=_refresh), now=1000.0)
    assert seen["ts"] == 1000.0


def test_refresh_exception_does_not_crash_turn_and_warns(caplog):
    refresh = AsyncMock(side_effect=RuntimeError("sheets down"))
    with caplog.at_level("WARNING"):
        _turn(refresh)  # _Abort (а не RuntimeError) доходить до кінця -> guard проковтнув виняток
    assert refresh.await_count == 1
    assert any("NPC CACHE GUARD" in r.getMessage() for r in caplog.records)


def test_handlers_attempted_flag_does_not_block_engine_guard():
    refresh = AsyncMock(return_value=True)
    _turn(refresh, session={"state": "GAME_ACTIVE", "history": [], "npc_cache": {},
                            "dead_npc_names": set(), "_npc_cache_attempted": True})
    assert refresh.await_count == 1


def test_parallel_turns_same_chat_single_refresh():
    gate = asyncio.Event()
    calls = {"n": 0}

    async def _slow(chat_id):
        calls["n"] += 1
        await gate.wait()
        return True

    async def _run():
        eng.user_sessions.pop(CID, None)
        with patch.object(eng, "get_user_data", AsyncMock(return_value=({"Ім'я": "Т"}, 1))), \
                patch.object(eng, "refresh_npc_database", _slow), \
                patch.object(eng, "safe_int", side_effect=_Abort("stop")):
            async def one():
                try:
                    await eng.process_game_turn(CID, "x")
                except _Abort:
                    pass
            t1 = asyncio.create_task(one())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            t2 = asyncio.create_task(one())
            await asyncio.sleep(0.01)
            gate.set()
            await asyncio.gather(t1, t2)

    asyncio.run(_run())
    assert calls["n"] == 1


# ============================ backoff: 60 с (збій) vs 600 с (порожній ростер) ============================

def test_constants_backoff():
    assert eng._NPC_CACHE_RETRY_FAIL_S == 60
    assert eng._NPC_CACHE_RETRY_EMPTY_S == 600


def test_refresh_true_empty_cache_sets_600_backoff():
    refresh = AsyncMock(return_value=True)
    _turn(refresh, now=1000.0)
    assert eng.user_sessions[CID]["_npc_cache_retry_after_s"] == 600


def test_refresh_true_empty_then_61s_no_call():
    refresh = AsyncMock(return_value=True)
    _turn(refresh, now=1000.0)
    _turn(refresh, session=eng.user_sessions[CID], now=1061.0)
    assert refresh.await_count == 1


def test_refresh_true_empty_then_601s_calls_again():
    refresh = AsyncMock(return_value=True)
    _turn(refresh, now=1000.0)
    _turn(refresh, session=eng.user_sessions[CID], now=1601.0)
    assert refresh.await_count == 2


def test_refresh_false_then_61s_calls_again():
    refresh = AsyncMock(return_value=False)
    _turn(refresh, now=1000.0)
    assert eng.user_sessions[CID]["_npc_cache_retry_after_s"] == 60
    _turn(refresh, session=eng.user_sessions[CID], now=1061.0)
    assert refresh.await_count == 2


def test_refresh_exception_backoff_60():
    refresh = AsyncMock(side_effect=RuntimeError("x"))
    _turn(refresh, now=1000.0)
    assert eng.user_sessions[CID]["_npc_cache_retry_after_s"] == 60
    _turn(refresh, session=eng.user_sessions[CID], now=1061.0)
    assert refresh.await_count == 2


def test_refresh_true_with_filled_cache_keeps_60():
    async def _fill(cid):
        eng.user_sessions[cid]["npc_cache"] = {"A": {}}
        return True
    _turn(AsyncMock(side_effect=_fill), now=1000.0)
    assert eng.user_sessions[CID]["_npc_cache_retry_after_s"] == 60
