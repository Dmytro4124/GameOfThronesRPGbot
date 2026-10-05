"""Stage 4: explicit context cache keyed by system_instruction TEXT (get_or_create_text_cache)
and the cache-fallback paths of AIWrapper.generate_content / generate_content_stream.

No network: client.caches.create, client.models.generate_content(_stream) and time.sleep are mocked.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.ai_client as ai
from core.ai_client import (
    AIWrapper, get_or_create_text_cache, invalidate_text_cache, build_strict_config,
    _CACHE_REGISTRY, _CACHE_DENY, _CIRCUIT_STATE, _REJECTED_SCHEMAS, _cache_key,
)
from core.prompts import CENSOR_SCHEMA

MODEL = "text-cache-model"
LONG = "L" * 5000      # 5000 / 3.5 ~ 1428 tokens >= 1024
SHORT = "S" * 3000     # 3000 / 3.5 ~ 857 tokens < 1024
pytestmark = pytest.mark.usefixtures("explicit_cache_on")
CREATE = "core.ai_client.client.caches.create"
GEN = "core.ai_client.client.models.generate_content"
STREAM = "core.ai_client.client.models.generate_content_stream"
SLEEP = "core.ai_client.time.sleep"
CACHE_ERR = "404 NOT_FOUND. CachedContent not found (or permission denied)"


class _ApiError(Exception):
    def __init__(self, code, status, extra=""):
        super().__init__(f"{code} {status}. {extra}")
        self.code = code


def _counter_create():
    """create mock returning cachedContents/N with N incrementing."""
    n = {"i": 0}

    def _create(model, config):
        n["i"] += 1
        return SimpleNamespace(name=f"cachedContents/{n['i']}")

    return MagicMock(side_effect=_create)


def _wrapper(name=MODEL):
    return AIWrapper(model_name=name, temperature=0.5, include_thoughts=False)


def _ok(text="{}"):
    r = MagicMock()
    r.text = text
    return r


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        _CACHE_REGISTRY.clear()
        _CACHE_DENY.clear()
        ai._CACHE_KEY_LOCKS.clear()
        _REJECTED_SCHEMAS.clear()
        _CIRCUIT_STATE.pop(MODEL, None)
    wipe()
    yield
    wipe()


@pytest.fixture
def clock(monkeypatch):
    """Controllable time.time(): clock.advance(seconds)."""
    real = time.time
    state = {"off": 0.0}
    monkeypatch.setattr(ai.time, "time", lambda: real() + state["off"])

    class _C:
        @staticmethod
        def advance(sec):
            state["off"] += sec
    return _C


# ---------------------------------------------------------------------------
# get_or_create_text_cache
# ---------------------------------------------------------------------------

def test_short_text_no_create_call():
    with patch(CREATE) as create:
        assert get_or_create_text_cache(MODEL, SHORT) is None
        assert get_or_create_text_cache(MODEL, "") is None
    create.assert_not_called()
    assert not _CACHE_DENY and not _CACHE_REGISTRY


def test_long_text_creates_once_then_reuses():
    create = _counter_create()
    with patch(CREATE, create):
        n1 = get_or_create_text_cache(MODEL, LONG)
        n2 = get_or_create_text_cache(MODEL, LONG)
        n3 = get_or_create_text_cache(MODEL, LONG)
    assert n1 == n2 == n3 == "cachedContents/1"
    assert create.call_count == 1
    kw = create.call_args.kwargs
    assert kw["model"] == MODEL
    assert kw["config"].system_instruction == LONG
    assert kw["config"].ttl == "3600s"


def test_different_text_or_model_get_different_caches():
    create = _counter_create()
    with patch(CREATE, create):
        a = get_or_create_text_cache(MODEL, LONG)
        b = get_or_create_text_cache(MODEL, LONG + "x")
        c = get_or_create_text_cache("other-model", LONG)
    assert len({a, b, c}) == 3 and create.call_count == 3


def test_parallel_threads_create_exactly_once():
    started = threading.Event()

    def slow_create(model, config):
        started.set()
        time.sleep(0.15)
        return SimpleNamespace(name="cachedContents/only")

    create = MagicMock(side_effect=slow_create)
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        return get_or_create_text_cache(MODEL, LONG)

    with patch(CREATE, create), ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda _: worker(), range(8)))
    assert create.call_count == 1
    assert results == ["cachedContents/only"] * 8


def test_parallel_threads_failed_create_called_once_all_get_none():
    def slow_fail(model, config):
        time.sleep(0.1)
        raise _ApiError(400, "INVALID_ARGUMENT", "caching not supported")

    create = MagicMock(side_effect=slow_fail)
    barrier = threading.Barrier(6)

    def worker(_):
        barrier.wait()
        return get_or_create_text_cache(MODEL, LONG)

    with patch(CREATE, create), ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(worker, range(6)))
    assert create.call_count == 1 and results == [None] * 6


def test_create_400_denies_until_restart_and_goes_inline(clock):
    create = MagicMock(side_effect=_ApiError(400, "INVALID_ARGUMENT", "Cached content is too small"))
    with patch(CREATE, create):
        assert get_or_create_text_cache(MODEL, LONG) is None
        key = _cache_key(MODEL, LONG)
        assert _CACHE_DENY[key] == float("inf")
        clock.advance(10 * 24 * 3600)  # days later still denied
        assert get_or_create_text_cache(MODEL, LONG) is None
    assert create.call_count == 1


@pytest.mark.parametrize("err", [
    _ApiError(403, "PERMISSION_DENIED"),
    _ApiError(404, "NOT_FOUND"),
    _ApiError(429, "RESOURCE_EXHAUSTED", "Quota exceeded: limit: 0, free tier"),
])
def test_permanent_failures_deny_until_restart(err):
    create = MagicMock(side_effect=err)
    with patch(CREATE, create):
        assert get_or_create_text_cache(MODEL, LONG) is None
        assert get_or_create_text_cache(MODEL, LONG) is None
    assert create.call_count == 1
    assert _CACHE_DENY[_cache_key(MODEL, LONG)] == float("inf")


@pytest.mark.parametrize("err", [
    _ApiError(429, "RESOURCE_EXHAUSTED", "rate"),
    _ApiError(503, "UNAVAILABLE"),
    _ApiError(500, "INTERNAL"),
    TimeoutError("network timeout"),
    ConnectionError("reset"),
])
def test_transient_failures_deny_temporarily_ttl_via_clock(err, clock):
    create = MagicMock(side_effect=[err, SimpleNamespace(name="cachedContents/late")])
    with patch(CREATE, create):
        assert get_or_create_text_cache(MODEL, LONG) is None
        until = _CACHE_DENY[_cache_key(MODEL, LONG)]
        assert until != float("inf")
        assert 0 < until - time.time() <= ai._CACHE_DENY_TRANSIENT_SECONDS + 1
        # Within the deny window: no new create call.
        clock.advance(ai._CACHE_DENY_TRANSIENT_SECONDS - 10)
        assert get_or_create_text_cache(MODEL, LONG) is None
        assert create.call_count == 1
        # After the window: retried and succeeds.
        clock.advance(20)
        assert get_or_create_text_cache(MODEL, LONG) == "cachedContents/late"
    assert create.call_count == 2
    assert _cache_key(MODEL, LONG) not in _CACHE_DENY


def test_refresh_before_ttl_expiry(clock):
    create = _counter_create()
    ttl, margin = ai._CACHE_TTL_SECONDS, ai._CACHE_RENEW_MARGIN
    with patch(CREATE, create):
        assert get_or_create_text_cache(MODEL, LONG) == "cachedContents/1"
        clock.advance(ttl - margin - 30)                 # still fresh
        assert get_or_create_text_cache(MODEL, LONG) == "cachedContents/1"
        assert create.call_count == 1
        clock.advance(60)                                # inside the renew margin, before real TTL
        assert ttl - margin - 30 + 60 < ttl
        assert get_or_create_text_cache(MODEL, LONG) == "cachedContents/2"
    assert create.call_count == 2


def test_invalidate_text_cache_forces_recreate():
    create = _counter_create()
    with patch(CREATE, create):
        get_or_create_text_cache(MODEL, LONG)
        invalidate_text_cache(MODEL, LONG)
        assert get_or_create_text_cache(MODEL, LONG) == "cachedContents/2"
    assert create.call_count == 2


def test_get_or_create_never_raises_on_weird_exception():
    with patch(CREATE, side_effect=ValueError("weird")):
        assert get_or_create_text_cache(MODEL, LONG) is None


# ---------------------------------------------------------------------------
# generate_content integration
# ---------------------------------------------------------------------------

def test_generate_short_si_inline_no_create():
    w = _wrapper()
    with patch(CREATE) as create, patch(GEN, return_value=_ok()) as gen:
        w.generate_content("p", config=w.config_with(system_instruction=SHORT))
    create.assert_not_called()
    cfg = gen.call_args.kwargs["config"]
    assert cfg.system_instruction == SHORT and cfg.cached_content is None


def test_generate_long_si_one_create_then_cached_content():
    w = _wrapper()
    create = _counter_create()
    with patch(CREATE, create), patch(GEN, return_value=_ok()) as gen:
        for _ in range(3):
            w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert create.call_count == 1
    cfgs = [c.kwargs["config"] for c in gen.call_args_list]
    assert all(c.cached_content == "cachedContents/1" and c.system_instruction is None for c in cfgs)


def test_generate_create_400_inline_and_no_more_create_attempts():
    w = _wrapper()
    create = MagicMock(side_effect=_ApiError(400, "INVALID_ARGUMENT", "unsupported"))
    with patch(CREATE, create), patch(GEN, return_value=_ok()) as gen:
        for _ in range(3):
            w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert create.call_count == 1
    cfgs = [c.kwargs["config"] for c in gen.call_args_list]
    assert all(c.system_instruction == LONG and c.cached_content is None for c in cfgs)


def test_generate_parallel_threads_share_single_create():
    w = _wrapper()

    def slow_create(model, config):
        time.sleep(0.1)
        return SimpleNamespace(name="cachedContents/shared")

    create = MagicMock(side_effect=slow_create)
    barrier = threading.Barrier(6)

    def call(_):
        barrier.wait()
        return w.generate_content("p", config=w.config_with(system_instruction=LONG))

    with patch(CREATE, create), patch(GEN, return_value=_ok()) as gen, ThreadPoolExecutor(6) as ex:
        list(ex.map(call, range(6)))
    assert create.call_count == 1
    assert {c.kwargs["config"].cached_content for c in gen.call_args_list} == {"cachedContents/shared"}


# ---- cache-fallback ---------------------------------------------------------

def test_cache_error_retries_once_inline_and_invalidates_registry():
    w = _wrapper()
    create = _counter_create()
    ok = _ok("RESULT")
    with patch(CREATE, create), patch(GEN, side_effect=[Exception(CACHE_ERR), ok]) as gen, patch(SLEEP) as sl:
        res = w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert res is ok
    assert gen.call_count == 2
    c1, c2 = (c.kwargs["config"] for c in gen.call_args_list)
    assert c1.cached_content == "cachedContents/1" and c1.system_instruction is None
    assert c2.system_instruction == LONG and c2.cached_content is None
    assert _cache_key(MODEL, LONG) not in _CACHE_REGISTRY
    sl.assert_not_called()


def test_cache_error_does_not_consume_attempt_budget():
    """max_retries=1: the cache-fallback retry is free, so the inline retry still happens."""
    w = _wrapper()
    ok = _ok()
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=[Exception(CACHE_ERR), ok]) as gen, \
            patch(SLEEP):
        assert w.generate_content("p", max_retries=1, config=w.config_with(system_instruction=LONG)) is ok
    assert gen.call_count == 2


def test_cache_error_does_not_touch_circuit_breaker():
    w = _wrapper()
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=[Exception(CACHE_ERR), _ok()]), patch(SLEEP):
        w.generate_content("p", config=w.config_with(system_instruction=LONG))
    cb = _CIRCUIT_STATE[MODEL]
    assert cb["consecutive_failures"] == 0 and cb["cooldown_until"] == 0.0


def test_cache_error_then_real_failure_counts_breaker_once():
    w = _wrapper()
    with patch(CREATE, _counter_create()), \
            patch(GEN, side_effect=[Exception(CACHE_ERR), _ApiError(503, "UNAVAILABLE")]) as gen, patch(SLEEP):
        with pytest.raises(_ApiError):
            w.generate_content("p", max_retries=1, config=w.config_with(system_instruction=LONG))
    assert gen.call_count == 2
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 1  # only the real failure, not the cache error


def test_cache_error_fallback_happens_only_once_no_loop():
    w = _wrapper()
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=Exception(CACHE_ERR)) as gen, patch(SLEEP):
        with pytest.raises(Exception, match="CachedContent"):
            w.generate_content("p", max_retries=3, config=w.config_with(system_instruction=LONG))
    assert gen.call_count == 2  # cached + one inline; the inline permanent (404) error is raised


def test_cache_error_denies_recreate_then_recreates_after_window(clock):
    """After a cache-fallback the key is denied for _CACHE_DENY_TRANSIENT_SECONDS (anti-churn)."""
    w = _wrapper()
    create = _counter_create()
    with patch(CREATE, create), patch(GEN, side_effect=[Exception(CACHE_ERR), _ok(), _ok(), _ok()]) as gen, patch(SLEEP):
        w.generate_content("p", config=w.config_with(system_instruction=LONG))
        w.generate_content("p", config=w.config_with(system_instruction=LONG))   # inside deny window
        assert create.call_count == 1
        assert gen.call_args_list[2].kwargs["config"].system_instruction == LONG
        assert gen.call_args_list[2].kwargs["config"].cached_content is None
        clock.advance(ai._CACHE_DENY_TRANSIENT_SECONDS + 1)
        w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert create.call_count == 2
    assert gen.call_args_list[3].kwargs["config"].cached_content == "cachedContents/2"


def test_persistent_cache_error_does_not_create_on_every_call():
    w = _wrapper()
    create = _counter_create()
    side = [Exception(CACHE_ERR)] + [_ok() for _ in range(5)]
    with patch(CREATE, create), patch(GEN, side_effect=side) as gen, patch(SLEEP):
        for _ in range(5):
            w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert create.call_count == 1
    assert gen.call_count == 6  # 1 failed cached + 1 inline retry, then 4 inline-only calls


def test_drop_cache_logs_only_via_logger_warning(caplog, capsys):
    w = _wrapper()
    cfg = SimpleNamespace(cached_content="cachedContents/x")
    with caplog.at_level("WARNING", logger="core.ai_client"):
        w._drop_cache(cfg, LONG)
    assert any("cached content invalid/expired" in r.getMessage() and r.levelname == "WARNING"
               for r in caplog.records)
    assert "invalid/expired" not in capsys.readouterr().out


def test_drop_cache_sets_registry_deny_and_wrapper_deny(clock):
    w = AIWrapper(MODEL, temperature=0.5, include_thoughts=False, system_instruction="P" * 300)
    w._cached_content_name = "cachedContents/w"
    w._cache_attempted = True
    cfg = SimpleNamespace(cached_content="cachedContents/w")
    ai._CACHE_REGISTRY[_cache_key(MODEL, LONG)] = ("cachedContents/r", time.time() + 1000)
    w._drop_cache(cfg, LONG)
    key = _cache_key(MODEL, LONG)
    assert key not in _CACHE_REGISTRY
    assert 0 < _CACHE_DENY[key] - time.time() <= ai._CACHE_DENY_TRANSIENT_SECONDS + 1
    assert w._cached_content_name is None and w._cache_attempted is False
    assert w._cache_deny_until > time.time()
    create = _counter_create()
    with patch(CREATE, create):
        assert w._ensure_cache() is None           # inside deny window: no create
        create.assert_not_called()
        clock.advance(ai._CACHE_DENY_TRANSIENT_SECONDS + 1)
        assert w._ensure_cache() == "cachedContents/1"


@pytest.mark.parametrize("msg", [
    "404 NOT_FOUND. CachedContent not found",
    "403 PERMISSION_DENIED. cached_content does not exist or caller has no access",
    "400 INVALID_ARGUMENT. Cached content is expired",
])
def test_cache_error_message_variants_trigger_fallback(msg):
    w = _wrapper()
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=[Exception(msg), _ok()]) as gen, patch(SLEEP):
        w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert gen.call_count == 2 and gen.call_args_list[1].kwargs["config"].system_instruction == LONG


@pytest.mark.parametrize("msg", ["503 UNAVAILABLE. CachedContent service busy", "429 RESOURCE_EXHAUSTED cached content"])
def test_transient_errors_mentioning_cache_do_not_trigger_cache_fallback(msg):
    assert ai._is_cache_error(Exception(msg)) is False


def test_non_cache_error_with_cached_config_is_not_cache_fallback():
    w = _wrapper()
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=_ApiError(403, "PERMISSION_DENIED", "bad key")) as gen:
        with pytest.raises(_ApiError):
            w.generate_content("p", config=w.config_with(system_instruction=LONG))
    assert gen.call_count == 1
    assert _cache_key(MODEL, LONG) in _CACHE_REGISTRY  # not invalidated


def test_cache_fallback_and_schema_fallback_cache_first():
    """cached+schema -> cache error -> inline+schema -> 400 -> inline, no schema -> ok (3 calls, 2 free retries)."""
    w = _wrapper()
    cfg = build_strict_config(w, schema=CENSOR_SCHEMA, system_instruction=LONG)
    ok = _ok("RESULT")
    with patch(CREATE, _counter_create()), \
            patch(GEN, side_effect=[Exception(CACHE_ERR), _ApiError(400, "INVALID_ARGUMENT"), ok]) as gen, \
            patch(SLEEP) as sl:
        res = w.generate_content("p", max_retries=1, config=cfg)
    assert res is ok and gen.call_count == 3
    c1, c2, c3 = (c.kwargs["config"] for c in gen.call_args_list)
    assert c1.cached_content and c1.response_schema is not None
    assert c2.cached_content is None and c2.system_instruction == LONG and c2.response_schema is not None
    assert c3.cached_content is None and c3.system_instruction == LONG and c3.response_schema is None
    sl.assert_not_called()
    assert (MODEL, ai._schema_fingerprint(CENSOR_SCHEMA)) in _REJECTED_SCHEMAS
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 0


def test_cache_fallback_and_schema_fallback_schema_first():
    """cached+schema -> 400 -> cached, no schema -> cache error -> inline, no schema -> ok."""
    w = _wrapper()
    cfg = build_strict_config(w, schema=CENSOR_SCHEMA, system_instruction=LONG)
    ok = _ok("RESULT")
    with patch(CREATE, _counter_create()), \
            patch(GEN, side_effect=[_ApiError(400, "INVALID_ARGUMENT"), Exception(CACHE_ERR), ok]) as gen, \
            patch(SLEEP) as sl:
        res = w.generate_content("p", max_retries=1, config=cfg)
    assert res is ok and gen.call_count == 3
    c1, c2, c3 = (c.kwargs["config"] for c in gen.call_args_list)
    assert c1.cached_content and c1.response_schema is not None
    assert c2.cached_content and c2.response_schema is None
    assert c3.cached_content is None and c3.system_instruction == LONG and c3.response_schema is None
    sl.assert_not_called()
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 0


def test_cache_and_schema_fallback_both_failing_terminates():
    w = _wrapper()
    cfg = build_strict_config(w, schema=CENSOR_SCHEMA, system_instruction=LONG)
    errs = [Exception(CACHE_ERR), _ApiError(400, "INVALID_ARGUMENT"), _ApiError(400, "INVALID_ARGUMENT"),
            Exception("unexpected extra call")]
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=errs) as gen, patch(SLEEP):
        with pytest.raises(_ApiError):
            w.generate_content("p", max_retries=3, config=cfg)
    assert gen.call_count == 3
    assert not _REJECTED_SCHEMAS  # schema not remembered when the schema-less retry failed too


def test_cache_inline_text_for_narrator_includes_preamble():
    w = AIWrapper(MODEL, temperature=0.5, include_thoughts=False, system_instruction="P" * 300)
    w._cache_attempted = True
    with patch(CREATE, _counter_create()), patch(GEN, side_effect=[Exception(CACHE_ERR), _ok()]) as gen, patch(SLEEP):
        w.generate_content("p", config=w.config_with(system_instruction=LONG))
    inline = gen.call_args_list[1].kwargs["config"]
    assert inline.system_instruction == "P" * 300 + "\n\n" + LONG and inline.cached_content is None


# ---------------------------------------------------------------------------
# generate_content_stream
# ---------------------------------------------------------------------------

def _chunks(*texts):
    return [SimpleNamespace(text=t) for t in texts]


def _failing_gen(exc):
    def g():
        raise exc
        yield  # pragma: no cover
    return g()


def test_stream_cache_error_before_first_chunk_retries_inline():
    w = _wrapper()
    good = _chunks("a", "b")
    with patch(CREATE, _counter_create()), patch(STREAM, side_effect=[_failing_gen(Exception(CACHE_ERR)), iter(good)]) as st:
        out = list(w.generate_content_stream("p", config=w.config_with(system_instruction=LONG)))
    assert out == good
    assert st.call_count == 2
    c1, c2 = (c.kwargs["config"] for c in st.call_args_list)
    assert c1.cached_content and c1.system_instruction is None
    assert c2.cached_content is None and c2.system_instruction == LONG
    assert _cache_key(MODEL, LONG) not in _CACHE_REGISTRY


def test_stream_cache_error_raised_by_call_itself_retries_inline():
    w = _wrapper()
    good = _chunks("x")
    with patch(CREATE, _counter_create()), patch(STREAM, side_effect=[Exception(CACHE_ERR), iter(good)]) as st:
        out = list(w.generate_content_stream("p", config=w.config_with(system_instruction=LONG)))
    assert out == good and st.call_count == 2


def test_stream_error_after_first_chunk_is_not_retried():
    w = _wrapper()

    def gen():
        yield SimpleNamespace(text="a")
        raise Exception(CACHE_ERR)

    with patch(CREATE, _counter_create()), patch(STREAM, side_effect=[gen()]) as st:
        it = w.generate_content_stream("p", config=w.config_with(system_instruction=LONG))
        assert next(it).text == "a"
        with pytest.raises(Exception, match="CachedContent"):
            next(it)
    assert st.call_count == 1


def test_stream_non_cache_error_before_first_chunk_propagates():
    w = _wrapper()
    with patch(CREATE, _counter_create()), patch(STREAM, side_effect=[_failing_gen(_ApiError(500, "INTERNAL"))]) as st:
        with pytest.raises(_ApiError):
            list(w.generate_content_stream("p", config=w.config_with(system_instruction=LONG)))
    assert st.call_count == 1


def test_stream_short_si_inline_returns_client_stream_directly():
    w = _wrapper()
    sentinel = iter(_chunks("q"))
    with patch(CREATE) as create, patch(STREAM, return_value=sentinel) as st:
        res = w.generate_content_stream("p", config=w.config_with(system_instruction=SHORT))
    create.assert_not_called()
    assert res is sentinel
    cfg = st.call_args.kwargs["config"]
    assert cfg.system_instruction == SHORT and cfg.cached_content is None


def test_stream_long_si_uses_cached_content():
    w = _wrapper()
    with patch(CREATE, _counter_create()) as create, patch(STREAM, side_effect=lambda **k: iter(_chunks("z"))) as st:
        out = list(w.generate_content_stream("p", config=w.config_with(system_instruction=LONG)))
        list(w.generate_content_stream("p", config=w.config_with(system_instruction=LONG)))
    assert len(out) == 1 and create.call_count == 1
    cfg = st.call_args_list[0].kwargs["config"]
    assert cfg.cached_content == "cachedContents/1" and cfg.system_instruction is None


def test_stream_config_none_uses_wrapper_default():
    w = _wrapper()
    sentinel = iter(_chunks("q"))
    with patch(STREAM, return_value=sentinel) as st:
        assert w.generate_content_stream("p") is sentinel
    assert st.call_args.kwargs["config"].temperature == 0.5
