"""Unit tests for core/narrator_ab.py (pure state + JSONL logging). No network, no Sheets."""
import asyncio
import json
import re
import time

import pytest

import core.narrator_ab as na


@pytest.fixture(autouse=True)
def _clean_state():
    na._pending.clear()
    na._last_turn_id.clear()
    na._log_lock = asyncio.Lock()  # lock binds to the first loop under contention; reset per test
    yield
    na._pending.clear()
    na._last_turn_id.clear()


def _res(key, text="text", **kw):
    return na.NarrationResult(model_key=key, text=text, total_ms=kw.pop("total_ms", 100), **kw)


def _pending(chat_id=1, turn_id="abcd1234", created=None, order=None):
    order = order or [_res("gemma", "G"), _res("flash_lite", "F")]
    return na.PendingChoice(
        turn_id=turn_id, chat_id=chat_id, user_id=chat_id,
        created=time.time() if created is None else created,
        order=order, change_log="\n\n📊 x", suggested_actions=["a", "b", "c", "d"],
        deferred_history={"user_input": "hi", "mech_updates": {}},
        log_record={"type": "turn", "turn_id": turn_id, "vote": None},
    )


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


# ---------------- turn id / pending state ----------------

def test_new_turn_id_format():
    for _ in range(50):
        assert re.fullmatch(r"[0-9a-f]{8}", na.new_turn_id())


def test_new_turn_id_unique():
    assert len({na.new_turn_id() for _ in range(200)}) == 200


def test_set_get_pending_and_last_turn_id():
    p = _pending(chat_id=7, turn_id="t1")
    na.set_pending(p)
    assert na.get_pending(7) is p
    assert na.last_turn_id(7) == "t1"


def test_get_pending_missing_is_none():
    assert na.get_pending(999) is None
    assert na.last_turn_id(999) is None


def test_pop_pending_matching_turn_id():
    p = _pending(turn_id="t1")
    na.set_pending(p)
    assert na.pop_pending(1, "t1") is p
    assert na.get_pending(1) is None


def test_pop_pending_mismatched_turn_id_does_not_pop():
    p = _pending(turn_id="t1")
    na.set_pending(p)
    assert na.pop_pending(1, "OTHER") is None
    assert na.get_pending(1) is p


def test_pop_pending_without_turn_id_pops():
    p = _pending()
    na.set_pending(p)
    assert na.pop_pending(1) is p
    assert na.pop_pending(1) is None


def test_last_turn_id_survives_pop():
    na.set_pending(_pending(turn_id="t1"))
    na.pop_pending(1)
    assert na.last_turn_id(1) == "t1"


def test_pending_isolated_per_chat():
    a, b = _pending(chat_id=1, turn_id="a"), _pending(chat_id=2, turn_id="b")
    na.set_pending(a)
    na.set_pending(b)
    assert na.pop_pending(1) is a
    assert na.get_pending(2) is b


# ---------------- TTL ----------------

def test_is_expired_false_within_ttl():
    p = _pending(created=1000.0)
    assert na.is_expired(p, now=1000.0 + na.NARRATOR_AB_CHOICE_TTL - 1) is False


def test_is_expired_true_after_ttl():
    p = _pending(created=1000.0)
    assert na.is_expired(p, now=1000.0 + na.NARRATOR_AB_CHOICE_TTL + 1) is True


def test_is_expired_boundary_is_not_expired():
    p = _pending(created=1000.0)
    assert na.is_expired(p, now=1000.0 + na.NARRATOR_AB_CHOICE_TTL) is False


def test_is_expired_uses_wall_clock_by_default():
    assert na.is_expired(_pending()) is False
    assert na.is_expired(_pending(created=time.time() - na.NARRATOR_AB_CHOICE_TTL - 5)) is True


# ---------------- shuffle / resolve ----------------

def test_shuffle_variants_returns_both():
    a, b = _res("gemma"), _res("flash_lite")
    out = na.shuffle_variants(a, b)
    assert len(out) == 2 and set(map(id, out)) == {id(a), id(b)}


def test_shuffle_variants_produces_both_orders():
    a, b = _res("gemma"), _res("flash_lite")
    firsts = {na.shuffle_variants(a, b)[0].model_key for _ in range(200)}
    assert firsts == {"gemma", "flash_lite"}


def test_resolve_pick_1():
    p = _pending(order=[_res("flash_lite", "F"), _res("gemma", "G")])
    w, vote = na.resolve_pick(p, "1")
    assert (w.model_key, vote) == ("flash_lite", "flash_lite")


def test_resolve_pick_2():
    p = _pending(order=[_res("flash_lite", "F"), _res("gemma", "G")])
    w, vote = na.resolve_pick(p, "2")
    assert (w.model_key, vote) == ("gemma", "gemma")


def test_resolve_pick_tie_returns_member_and_tie_vote():
    p = _pending()
    seen = set()
    for _ in range(100):
        w, vote = na.resolve_pick(p, "tie")
        assert vote == "tie" and w in p.order
        seen.add(w.model_key)
    assert seen == {"gemma", "flash_lite"}


@pytest.mark.parametrize("bad", ["3", "", "TIE", None, "0"])
def test_resolve_pick_invalid_raises(bad):
    with pytest.raises(ValueError):
        na.resolve_pick(_pending(), bad)


# ---------------- NarrationResult.ok ----------------

@pytest.mark.parametrize("kw,expected", [
    ({"text": "x"}, True),
    ({"text": None}, False),
    ({"text": ""}, False),
    ({"text": "x", "used_fallback": True}, False),
    ({"text": "x", "error": "boom"}, False),
])
def test_narration_result_ok(kw, expected):
    r = na.NarrationResult(model_key="gemma", total_ms=1, **kw)
    assert r.ok is expected


# ---------------- log record schema ----------------

_PLAN_FIELDS = {"type", "turn_id", "ts", "user_id", "chat_id", "mode", "narrator_prompt",
                "mechanics", "shown_order", "results", "vote", "vote_raw", "vote_ms", "reason"}
_RESULT_FIELDS = {"text", "len", "total_ms", "attempt_ms", "final_attempt", "used_fallback", "error"}


def _record(reason=None):
    return na.build_log_record(
        turn_id="t1", user_id=5, chat_id=5, mode="NORMAL", narrator_prompt="PROMPT",
        mechanics={"outcome": "SUCCESS"},
        results=[_res("gemma", "abc", attempt_ms=[10], final_attempt=1),
                 _res("flash_lite", None, used_fallback=True, final_attempt="fallback", error="E")],
        shown_order=["flash_lite", "gemma"], reason=reason)


def test_build_log_record_has_all_plan_fields():
    assert set(_record()) == _PLAN_FIELDS


def test_build_log_record_values():
    r = _record()
    assert r["type"] == "turn" and r["turn_id"] == "t1" and r["mode"] == "NORMAL"
    assert r["shown_order"] == ["flash_lite", "gemma"]
    assert r["vote"] is None and r["vote_raw"] is None and r["vote_ms"] is None
    assert r["narrator_prompt"] == "PROMPT"
    assert r["mechanics"] == {"outcome": "SUCCESS"}


def test_build_log_record_result_schema_and_len():
    r = _record()["results"]
    assert set(r) == {"gemma", "flash_lite"}
    for v in r.values():
        assert set(v) == _RESULT_FIELDS
    assert r["gemma"]["len"] == 3
    assert r["flash_lite"]["len"] == 0 and r["flash_lite"]["text"] is None
    assert r["flash_lite"]["used_fallback"] is True and r["flash_lite"]["final_attempt"] == "fallback"


def test_build_log_record_ts_iso_utc():
    from datetime import datetime
    ts = datetime.fromisoformat(_record()["ts"])
    assert ts.tzinfo is not None


def test_build_log_record_is_json_serializable():
    json.dumps(_record())


def test_finalize_record_sets_vote_fields():
    rec = na.finalize_record(_record(), vote="gemma", vote_raw="2", vote_ms=1234)
    assert (rec["vote"], rec["vote_raw"], rec["vote_ms"]) == ("gemma", "2", 1234)
    assert rec["reason"] is None


def test_finalize_record_reason_only_overwritten_when_given():
    rec = na.finalize_record(_record(reason="keep"), vote=None, vote_raw=None, vote_ms=None)
    assert rec["reason"] == "keep"
    rec = na.finalize_record(rec, vote=None, vote_raw=None, vote_ms=None, reason="ttl_expired")
    assert rec["reason"] == "ttl_expired"


def test_finalize_record_returns_same_object():
    r = _record()
    assert na.finalize_record(r, vote="tie", vote_raw="tie", vote_ms=1) is r


# ---------------- append_log ----------------

def test_append_log_writes_valid_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(path))

    async def go():
        await na.append_log({"a": 1, "u": "Привіт"})
        await na.append_log({"a": 2})

    asyncio.run(go())
    assert _read(path) == [{"a": 1, "u": "Привіт"}, {"a": 2}]
    assert "Привіт" in path.read_text(encoding="utf-8")  # ensure_ascii=False


def test_append_log_concurrent_does_not_interleave(tmp_path, monkeypatch):
    path = tmp_path / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(path))
    big = "x" * 20000

    async def go():
        await asyncio.gather(*(na.append_log({"i": i, "pad": big}) for i in range(40)))

    asyncio.run(go())
    rows = _read(path)  # any interleaving would break json parsing
    assert sorted(r["i"] for r in rows) == list(range(40))


def test_append_log_unwritable_path_does_not_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(blocker / "child" / "ab.jsonl"))
    asyncio.run(na.append_log({"a": 1}))  # must not raise


def test_append_log_unserializable_record_does_not_raise(tmp_path, monkeypatch):
    path = tmp_path / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(path))
    asyncio.run(na.append_log({"obj": object(), "s": {1, 2}}))  # default=str covers it
    assert len(_read(path)) == 1


# ---------------- append_note ----------------

def test_append_note_without_last_turn_returns_false_and_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(path))
    assert asyncio.run(na.append_note(1, 1, "hello")) is False
    assert not path.exists()


def test_append_note_with_last_turn_writes_note(tmp_path, monkeypatch):
    path = tmp_path / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(path))
    na.set_pending(_pending(chat_id=3, turn_id="tt"))
    assert asyncio.run(na.append_note(3, 33, "nice prose")) is True
    (row,) = _read(path)
    assert row["type"] == "note" and row["turn_id"] == "tt" and row["note"] == "nice prose"
    assert row["chat_id"] == 3 and row["user_id"] == 33 and "ts" in row


def test_append_note_works_after_pending_popped(tmp_path, monkeypatch):
    path = tmp_path / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(path))
    na.set_pending(_pending(chat_id=3, turn_id="tt"))
    na.pop_pending(3)
    assert asyncio.run(na.append_note(3, 3, "after vote")) is True
