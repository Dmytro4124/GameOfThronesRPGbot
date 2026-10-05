"""Debug call-meta capture у core/ai_client.py: start/stop/get/mark, записи generate/stream/hedged,
mask_secrets, ізоляція між Task-ами, split_thoughts на parts=None. Без мережі (client мокається)."""
import asyncio
import threading
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import core.ai_client as ai
from core.ai_client import (
    AIWrapper, mask_secrets, start_call_meta_capture, stop_call_meta_capture, get_call_meta,
    call_meta_mark, split_thoughts, hedged_generate_content_async, _CIRCUIT_STATE,
)

MODEL = "meta-test-model"


@pytest.fixture(autouse=True)
def _clean_state():
    _CIRCUIT_STATE.pop(MODEL, None)
    stop_call_meta_capture()
    yield
    stop_call_meta_capture()
    _CIRCUIT_STATE.pop(MODEL, None)


def _wrapper(**kw):
    d = {"model_name": MODEL, "temperature": 0.5, "include_thoughts": False}
    d.update(kw)
    return AIWrapper(**d)


class _Fin(Enum):
    STOP = 1
    SAFETY = 2


def _resp(text="ok", finish=_Fin.STOP, block=None, usage=True, thought=False):
    parts = [SimpleNamespace(text=text, thought=False)]
    if thought:
        parts.insert(0, SimpleNamespace(text="thinking", thought=True))
    cand = SimpleNamespace(
        finish_reason=finish, content=SimpleNamespace(parts=parts),
        safety_ratings=[SimpleNamespace(category=SimpleNamespace(name="HARM_X"),
                                        probability=SimpleNamespace(name="LOW"), blocked=False)],
    )
    r = SimpleNamespace(
        text=text, candidates=[cand],
        prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name=block) if block else None),
    )
    if usage:
        r.usage_metadata = SimpleNamespace(prompt_token_count=10, candidates_token_count=5,
                                           thoughts_token_count=3, cached_content_token_count=0,
                                           total_token_count=18)
    return r


class _Err(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        if code is not None:
            self.code = code


# ============================ capture off ============================

def test_without_start_get_call_meta_is_empty_list():
    assert get_call_meta() == []
    assert call_meta_mark() == 0


def test_without_start_generate_records_nothing():
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp()
        w.generate_content("p")
    assert get_call_meta() == []


def test_without_start_stream_records_nothing_and_returns_raw_iterator():
    w = _wrapper()
    raw = iter([_resp()])
    with patch.object(ai, "client") as c:
        c.models.generate_content_stream.return_value = raw
        out = w.generate_content_stream("p")
    assert out is raw
    assert get_call_meta() == []


def test_stop_clears_records():
    start_call_meta_capture()
    stop_call_meta_capture()
    assert get_call_meta() == []


def test_start_resets_previous_records():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp()
        w.generate_content("p")
    assert len(get_call_meta()) == 1
    start_call_meta_capture()
    assert get_call_meta() == []


# ============================ generate ============================

def test_generate_success_record_fields():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp("hello world")
        w.generate_content("p", meta_label="censor")
    recs = get_call_meta()
    assert len(recs) == 1
    r = recs[0]
    assert r["model"] == MODEL and r["path"] == "generate" and r["label"] == "censor"
    assert r["finish_reason"] == "STOP"
    assert r["text_len"] == len("hello world")
    assert r["usage"] == {"prompt": 10, "candidates": 5, "thoughts": 3, "cached": 0, "total": 18}
    assert r["safety"] == ["HARM_X:LOW"]
    assert r["exception"] is None and r["attempts"] == []
    assert isinstance(r["elapsed_s"], float)
    assert "_sink" not in r and "_t0" not in r


def test_generate_text_len_excludes_thought_parts():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp("abcde", thought=True)
        w.generate_content("p")
    assert get_call_meta()[0]["text_len"] == 5


def test_generate_block_reason_recorded():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp("", finish=_Fin.SAFETY, block="SAFETY")
        w.generate_content("p")
    r = get_call_meta()[0]
    assert r["finish_reason"] == "SAFETY" and r["block_reason"] == "SAFETY"


def test_generate_retry_503_records_attempt_with_sleep_s():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c, patch("core.ai_client.time.sleep"), \
            patch("core.ai_client.random.random", return_value=0.5):
        c.models.generate_content.side_effect = [_Err("503 UNAVAILABLE overloaded", 503), _resp()]
        w.generate_content("p", max_retries=3)
    r = get_call_meta()[0]
    assert len(r["attempts"]) == 1
    a = r["attempts"][0]
    assert a["n"] == 1 and a["error_code"] == 503
    assert a["sleep_s"] == 5.0  # base 5s, jitter 0 при random()==0.5
    assert r["exception"] is None
    assert r["finish_reason"] == "STOP"


def test_generate_permanent_error_records_exception_and_reraises():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.side_effect = _Err("400 INVALID_ARGUMENT bad", 400)
        with pytest.raises(_Err):
            w.generate_content("p")
    r = get_call_meta()[0]
    assert r["exception"]["type"] == "_Err"
    assert "INVALID_ARGUMENT" in r["exception"]["msg"]
    assert len(r["attempts"]) == 1 and r["attempts"][0]["sleep_s"] is None


def test_generate_exhausted_retries_records_exception():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c, patch("core.ai_client.time.sleep"):
        c.models.generate_content.side_effect = _Err("503 UNAVAILABLE", 503)
        with pytest.raises(_Err):
            w.generate_content("p", max_retries=2)
    r = get_call_meta()[0]
    assert len(r["attempts"]) == 2
    assert r["attempts"][0]["sleep_s"] is not None and r["attempts"][1]["sleep_s"] is None
    assert r["exception"]["type"] == "_Err"


def test_generate_breaker_open_recorded():
    import time
    _CIRCUIT_STATE[MODEL] = {"consecutive_failures": 5, "cooldown_until": time.time() + 100}
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        with pytest.raises(RuntimeError):
            w.generate_content("p")
        c.models.generate_content.assert_not_called()
    r = get_call_meta()[0]
    assert r["breaker_open"] is True and r["exception"]["type"] == "RuntimeError"


def test_generate_exception_message_masks_secret():
    start_call_meta_capture()
    w = _wrapper()
    key = "AIzaSyA1234567890123456789012345678901234"
    with patch.object(ai, "client") as c:
        c.models.generate_content.side_effect = _Err(f"400 INVALID_ARGUMENT url?key={key}", 400)
        with pytest.raises(_Err):
            w.generate_content("p")
    r = get_call_meta()[0]
    assert key not in r["exception"]["msg"]
    assert key not in r["attempts"][0]["error"]


def test_generate_label_default_none():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp()
        w.generate_content("p")
    assert get_call_meta()[0]["label"] is None


def test_call_meta_mark_counts_records():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp()
        assert call_meta_mark() == 0
        w.generate_content("p")
        assert call_meta_mark() == 1
        w.generate_content("p")
        assert call_meta_mark() == 2


def test_weird_response_object_does_not_break_call():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = object()
        out = w.generate_content("p")
    assert out is not None
    assert len(get_call_meta()) == 1


def test_absorb_response_swallows_internal_errors():
    """Збір не ламає виклик, якщо всередині збору виняток (відповідь із властивістю, що кидає)."""
    class Evil:
        @property
        def candidates(self):
            raise RuntimeError("boom")
    meta = {"text_len": 0, "usage": {}}
    ai._meta_absorb_response(meta, Evil())  # не кидає


def test_new_call_meta_never_raises_when_sink_broken():
    with patch.object(ai, "_call_meta_var") as v:
        v.get.side_effect = RuntimeError("boom")
        assert ai._new_call_meta("m", "generate") is None


def test_broken_commit_does_not_break_generate():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content.return_value = _resp("fine")
        with patch.object(ai.time, "monotonic", side_effect=RuntimeError("clock")):
            out = w.generate_content("p")
    assert out.text == "fine"


# ============================ stream ============================

def test_stream_exhausted_commits_record_with_accumulated_text():
    start_call_meta_capture()
    w = _wrapper()
    chunks = [_resp("ab", finish=None), _resp("cde", finish=_Fin.STOP)]
    with patch.object(ai, "client") as c:
        c.models.generate_content_stream.return_value = iter(chunks)
        it = w.generate_content_stream("p", meta_label="narr.stream")
        assert get_call_meta() == []  # запис додається лише по завершенню стріму
        got = list(it)
    assert len(got) == 2
    r = get_call_meta()[0]
    assert r["path"] == "stream" and r["label"] == "narr.stream"
    assert r["text_len"] == 5
    assert r["finish_reason"] == "STOP"
    assert r["exception"] is None


def test_stream_abandoned_with_break_still_commits_on_close():
    start_call_meta_capture()
    w = _wrapper()
    chunks = [_resp("ab", finish=None), _resp("cd", finish=None), _resp("ef", finish=_Fin.STOP)]
    with patch.object(ai, "client") as c:
        c.models.generate_content_stream.return_value = iter(chunks)
        it = w.generate_content_stream("p")
        for _ch in it:
            break
        it.close()
    recs = get_call_meta()
    assert len(recs) == 1
    assert recs[0]["text_len"] == 2 and recs[0]["exception"] is None


def test_stream_failure_midway_records_exception_and_reraises():
    start_call_meta_capture()
    w = _wrapper()

    def gen():
        yield _resp("ab", finish=None)
        raise _Err("503 mid-stream")

    with patch.object(ai, "client") as c:
        c.models.generate_content_stream.return_value = gen()
        it = w.generate_content_stream("p")
        with pytest.raises(_Err):
            list(it)
    r = get_call_meta()[0]
    assert r["exception"]["type"] == "_Err" and r["text_len"] == 2


def test_stream_creation_failure_records_exception():
    start_call_meta_capture()
    w = _wrapper()
    with patch.object(ai, "client") as c:
        c.models.generate_content_stream.side_effect = _Err("400 bad")
        with pytest.raises(_Err):
            w.generate_content_stream("p")
    r = get_call_meta()[0]
    assert r["path"] == "stream" and r["exception"]["type"] == "_Err"


# ============================ hedged ============================

def _run(coro):
    return asyncio.run(coro)


def test_hedged_two_records_with_hedge_idx_and_single_winner():
    w = _wrapper()
    barrier = threading.Barrier(2, timeout=5)

    def slow(*a, **k):
        barrier.wait()
        return _resp("ok")

    async def go():
        start_call_meta_capture()
        with patch.object(ai, "client") as c:
            c.models.generate_content.side_effect = slow
            res = await hedged_generate_content_async(w, "p", hedge_count=2, max_retries=1, meta_label="narr.h")
            await asyncio.sleep(0.3)  # дати другому hedge дописати запис
        return res, list(get_call_meta())

    res, recs = _run(go())
    assert res is not None
    assert len(recs) == 2
    assert sorted(r["hedge_idx"] for r in recs) == [0, 1]
    assert all(r["label"] == "narr.h" for r in recs)
    assert sum(1 for r in recs if r["winner"] is True) == 1


def test_hedged_single_hedge_marks_winner():
    w = _wrapper()

    async def go():
        start_call_meta_capture()
        with patch.object(ai, "client") as c:
            c.models.generate_content.return_value = _resp("ok")
            await hedged_generate_content_async(w, "p", hedge_count=1, max_retries=1)
        return list(get_call_meta())

    recs = _run(go())
    assert len(recs) == 1 and recs[0]["hedge_idx"] == 0 and recs[0]["winner"] is True


def test_hedged_without_capture_adds_no_records():
    w = _wrapper()

    async def go():
        with patch.object(ai, "client") as c:
            c.models.generate_content.return_value = _resp("ok")
            await hedged_generate_content_async(w, "p", hedge_count=1, max_retries=1)
        return get_call_meta()

    assert _run(go()) == []


def test_hedged_does_not_change_generate_content_signature_for_mocks():
    """Моки generate_content(prompt, max_retries, config) без meta_label продовжують працювати."""
    calls = []

    class W:
        def generate_content(self, prompt, max_retries, config):
            calls.append((prompt, max_retries, config))
            return "r"

    async def go():
        return await hedged_generate_content_async(W(), "p", hedge_count=1, max_retries=2, meta_label="x")

    assert _run(go()) == "r"
    assert calls == [("p", 2, None)]


# ============================ isolation between tasks ============================

def test_parallel_tasks_do_not_mix_records():
    w = _wrapper()

    async def turn(label, n_calls):
        start_call_meta_capture()
        for i in range(n_calls):
            await asyncio.to_thread(w.generate_content, "p", 1, None, f"{label}{i}")
            await asyncio.sleep(0)
        return [r["label"] for r in get_call_meta()]

    async def go():
        with patch.object(ai, "client") as c:
            c.models.generate_content.return_value = _resp("ok")
            return await asyncio.gather(turn("A", 3), turn("B", 2))

    a, b = _run(go())
    assert a == ["A0", "A1", "A2"]
    assert b == ["B0", "B1"]


def test_parallel_task_without_start_sees_nothing_while_other_captures():
    w = _wrapper()

    async def debug_turn():
        start_call_meta_capture()
        await asyncio.to_thread(w.generate_content, "p")
        return len(get_call_meta())

    async def normal_turn():
        await asyncio.to_thread(w.generate_content, "p")
        return len(get_call_meta())

    async def go():
        with patch.object(ai, "client") as c:
            c.models.generate_content.return_value = _resp("ok")
            return await asyncio.gather(debug_turn(), normal_turn())

    assert _run(go()) == [1, 0]


# ============================ mask_secrets ============================

def test_mask_aiza_key():
    out = mask_secrets("key AIzaSyA1234567890123456789012345678901234 end")
    assert "AIzaSyA1234" not in out and "AIza***" in out


def test_mask_telegram_bot_token():
    tok = "123456789:AAEhBP0av18Kdl2Z9_xYz-1234567890abcdef"
    out = mask_secrets(f"url https://api.telegram.org/bot{tok}/getMe")
    assert tok not in out and "AAEhBP0" not in out


@pytest.mark.parametrize("raw,leak", [
    ("https://x.y/z?key=SECRETVALUE123&a=1", "SECRETVALUE123"),
    ("https://x.y/z?token=SECRETVALUE123", "SECRETVALUE123"),
    ("?api_key=SECRETVALUE123", "SECRETVALUE123"),
    ("?access_token=SECRETVALUE123&x=2", "SECRETVALUE123"),
    ("Authorization: Bearer abc.DEF-123_xyz~+/=", "abc.DEF-123_xyz"),
])
def test_mask_key_token_bearer(raw, leak):
    assert leak not in mask_secrets(raw)


def test_mask_keeps_other_query_params():
    out = mask_secrets("https://x.y/z?key=S3CRET&a=1")
    assert "a=1" in out and "key=***" in out


def test_mask_limit_truncates_with_ellipsis():
    out = mask_secrets("x" * 500, limit=200)
    assert len(out) == 201 and out.endswith("…")


def test_mask_limit_none_does_not_truncate():
    assert len(mask_secrets("x" * 500, limit=None)) == 500


def test_mask_default_limit_is_200():
    assert len(mask_secrets("y" * 300)) == 201


def test_mask_none_and_non_string():
    assert mask_secrets(None) == ""
    assert mask_secrets(12345) == "12345"


def test_mask_plain_text_unchanged():
    assert mask_secrets("Звичайний текст помилки 503") == "Звичайний текст помилки 503"


def test_mask_applied_before_truncation():
    key = "AIza" + "a" * 40
    out = mask_secrets("z" * 190 + key, limit=200)
    assert "AIzaaaaa" not in out


# ============================ split_thoughts parts=None ============================

def test_split_thoughts_parts_none_returns_empty_pair():
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=None))])
    assert split_thoughts(resp) == ("", "")


def test_split_thoughts_content_none_returns_empty_pair():
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=None)])
    assert split_thoughts(resp) == ("", "")


def test_split_thoughts_regular_still_works():
    parts = [SimpleNamespace(text="think", thought=True), SimpleNamespace(text="answer", thought=False)]
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))])
    t, c = split_thoughts(resp)
    assert "think" in t and "answer" in c


# ============================ hedge groups ============================

def test_two_parallel_hedged_calls_do_not_overwrite_each_others_winner():
    w = _wrapper()

    async def go():
        start_call_meta_capture()  # один спільний sink на обидва виклики
        with patch.object(ai, "client") as c:
            c.models.generate_content.return_value = _resp("ok")
            await asyncio.gather(
                hedged_generate_content_async(w, "pA", hedge_count=2, max_retries=1, meta_label="A"),
                hedged_generate_content_async(w, "pB", hedge_count=2, max_retries=1, meta_label="B"),
            )
            await asyncio.sleep(0.3)  # дати програшним hedge дописати запис
        return list(get_call_meta())

    recs = _run(go())
    assert len(recs) == 4
    for label in ("A", "B"):
        grp = [r for r in recs if r["label"] == label]
        assert len(grp) == 2
        assert sorted(r["hedge_idx"] for r in grp) == [0, 1]
        assert len({r["hedge_group"] for r in grp}) == 1
        assert sum(1 for r in grp if r["winner"] is True) == 1
        assert sum(1 for r in grp if r["winner"] is False) == 1  # пізній програшний -> False, не None
    assert len({r["hedge_group"] for r in recs}) == 2
    assert all("_gstate" not in r for r in recs)


def test_late_losing_hedge_record_gets_winner_false():
    w = _wrapper()
    release = threading.Event()
    entered = threading.Event()
    calls = {"n": 0}
    lock = threading.Lock()

    def fn(*a, **k):
        with lock:
            calls["n"] += 1
            me = calls["n"]
        if me == 2:
            entered.set()
            release.wait(5)
        else:
            entered.wait(5)  # переможець завершується лише коли програшний уже стартував
        return _resp("ok")

    async def go():
        start_call_meta_capture()
        with patch.object(ai, "client") as c:
            c.models.generate_content.side_effect = fn
            await hedged_generate_content_async(w, "p", hedge_count=2, max_retries=1)
            assert len(get_call_meta()) == 1
            release.set()
            await asyncio.sleep(0.3)
        return list(get_call_meta())

    recs = _run(go())
    assert len(recs) == 2
    assert sorted(r["winner"] for r in recs) == [False, True]


# ============================ mask_secrets: Basic / PEM / k=v ============================

@pytest.mark.parametrize("raw,leak", [
    ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1", "dXNlcjpwYXNzd29yZDEyMzQ1"),
    ("authorization=Basic dXNlcjpwYXNzd29yZDEyMzQ1", "dXNlcjpwYXNzd29yZDEyMzQ1"),
    ("header Basic dXNlcjpwYXNzd29yZDEyMzQ1 end", "dXNlcjpwYXNzd29yZDEyMzQ1"),
])
def test_mask_basic_auth(raw, leak):
    assert leak not in mask_secrets(raw, limit=None)


def test_mask_pem_block_with_end():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nabcdef\n-----END PRIVATE KEY----- tail"
    out = mask_secrets("x " + pem, limit=None)
    assert "MIIEvQ" not in out and "abcdef" not in out and "BEGIN" not in out
    assert out.endswith("tail")


def test_mask_pem_block_without_end_masks_to_end_of_text():
    out = mask_secrets("a -----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkq\nzzzzz", limit=None)
    assert "MIIEvQ" not in out and "zzzzz" not in out and out.startswith("a ")


@pytest.mark.parametrize("name", ["client_secret", "private_key", "refresh_token"])
@pytest.mark.parametrize("fmt", ['{n}=SECRETVAL123', '{n}: SECRETVAL123', '"{n}": "SECRETVAL123"',
                                 "'{n}': 'SECRETVAL123'", '{n} = SECRETVAL123'])
def test_mask_named_secret_forms(name, fmt):
    out = mask_secrets("ctx " + fmt.format(n=name) + " rest", limit=None)
    assert "SECRETVAL123" not in out


def test_mask_named_secret_quoted_value_with_spaces():
    out = mask_secrets('{"client_secret": "abc def ghi", "x": 1}', limit=None)
    assert "abc def" not in out and '"x": 1' in out


@pytest.mark.parametrize("phrase", [
    "He has a basic understanding of the rules",
    "basic knowledge of swordplay is required",
    "Basic rules apply here",
])
def test_mask_does_not_touch_ordinary_basic_phrases(phrase):
    assert mask_secrets(phrase, limit=None) == phrase
