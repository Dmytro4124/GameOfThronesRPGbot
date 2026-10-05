"""Stage 4: per-call system_instruction in AIWrapper.config_with / build_strict_config / _upgrade_config.

Invariants:
  * config_with(system_instruction=X) -> inline system_instruction, never cached_content;
  * wrapper-level preamble (model_narrator) is prepended: preamble + "\\n\\n" + X;
  * system_instruction and cached_content are NEVER set together (also after _upgrade_config);
  * CLAUDE.md 5.1: config_with / build_strict_config make no network call (caches.create is
    reserved for worker threads), even when invoked from inside a running event loop.
No network: client.caches.create is mocked.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.ai_client as ai
from core.ai_client import AIWrapper, build_strict_config

pytestmark = pytest.mark.usefixtures("explicit_cache_on")
CREATE = "core.ai_client.client.caches.create"
PREAMBLE = "P" * 300
STATIC = "S" * 6000          # > 1024 tokens * 3.5 chars -> eligible for explicit cache
SHORT = "short static"


def _plain(**kw):
    d = dict(model_name="cfg-test-plain", temperature=0.4, thinking_level="minimal",
             include_thoughts=True, response_mime_type="application/json", block_none=True)
    d.update(kw)
    return AIWrapper(**d)


def _narr(**kw):
    return _plain(model_name="cfg-test-narr", system_instruction=PREAMBLE,
                  response_mime_type=None, **kw)


@pytest.fixture(autouse=True)
def _clean():
    for d in (ai._CACHE_REGISTRY, ai._CACHE_DENY, ai._CACHE_KEY_LOCKS):
        d.clear()
    yield
    for d in (ai._CACHE_REGISTRY, ai._CACHE_DENY, ai._CACHE_KEY_LOCKS):
        d.clear()


def _both(cfg):
    return bool(cfg.system_instruction) and bool(cfg.cached_content)


# ---------------------------------------------------------------------------
# config_with
# ---------------------------------------------------------------------------

def test_config_with_inline_system_instruction_no_cached_content():
    with patch(CREATE) as create:
        cfg = _plain().config_with(system_instruction=STATIC)
    assert cfg.system_instruction == STATIC
    assert cfg.cached_content is None
    create.assert_not_called()


def test_config_with_preserves_other_wrapper_fields():
    cfg = _plain().config_with(system_instruction=SHORT)
    assert cfg.temperature == 0.4
    assert cfg.response_mime_type == "application/json"
    assert cfg.thinking_config is not None and cfg.thinking_config.include_thoughts is True
    assert cfg.safety_settings and len(cfg.safety_settings) == 4


def test_config_with_narrator_preamble_plus_static():
    with patch(CREATE) as create:
        cfg = _narr().config_with(system_instruction=STATIC)
    assert cfg.system_instruction == PREAMBLE + "\n\n" + STATIC
    assert cfg.cached_content is None
    create.assert_not_called()


def test_config_with_preamble_not_duplicated_on_repeat_calls():
    w = _narr()
    a = w.config_with(system_instruction=STATIC)
    b = w.config_with(system_instruction=STATIC)
    assert a.system_instruction == b.system_instruction == PREAMBLE + "\n\n" + STATIC
    assert w.system_instruction == PREAMBLE


def test_config_with_per_call_si_wins_over_existing_wrapper_cache():
    """Wrapper already holds a cached_content for its preamble; per-call SI must not mix with it."""
    w = _narr()
    w._cache_attempted = True
    w._cached_content_name = "cachedContents/wrapper-level"
    cfg = w.config_with(system_instruction=STATIC)
    assert cfg.cached_content is None
    assert cfg.system_instruction == PREAMBLE + "\n\n" + STATIC
    assert not _both(cfg)


def test_config_with_without_si_keeps_wrapper_level_cache_behavior():
    w = _narr()
    w._cache_attempted = True
    w._cached_content_name = "cachedContents/wrapper-level"
    cfg = w.config_with(temperature=0.5)
    assert cfg.cached_content == "cachedContents/wrapper-level"
    assert cfg.system_instruction is None
    assert cfg.temperature == 0.5


def test_config_with_without_si_inline_when_no_cache():
    w = _narr()
    w._cache_attempted = True
    cfg = w.config_with()
    assert cfg.system_instruction == PREAMBLE and cfg.cached_content is None


def test_config_with_other_overrides_with_si():
    cfg = _narr().config_with(system_instruction=SHORT, temperature=0.5)
    assert cfg.temperature == 0.5
    assert cfg.system_instruction == PREAMBLE + "\n\n" + SHORT


@pytest.mark.parametrize("empty", [None, ""])
def test_config_with_empty_si_is_wrapper_level(empty):
    w = _narr()
    w._cache_attempted = True
    cfg = w.config_with(system_instruction=empty)
    assert cfg.system_instruction == PREAMBLE


# ---------------------------------------------------------------------------
# build_strict_config
# ---------------------------------------------------------------------------

def test_build_strict_config_passes_system_instruction():
    w = _plain()
    schema = {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}}
    cfg = build_strict_config(w, schema=schema, system_instruction=STATIC)
    assert cfg.system_instruction == STATIC
    assert cfg.cached_content is None
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is not None


def test_build_strict_config_none_si_is_backward_compatible():
    w = _plain()
    cfg = build_strict_config(w, schema=None)
    assert cfg.system_instruction is None and cfg.cached_content is None


def test_build_strict_config_narrator_preamble_plus_static():
    cfg = build_strict_config(_narr(), system_instruction=STATIC)
    assert cfg.system_instruction == PREAMBLE + "\n\n" + STATIC


def test_build_strict_config_temperature_with_si():
    cfg = build_strict_config(_plain(), temperature=0.1, system_instruction=STATIC)
    assert cfg.temperature == 0.1 and cfg.system_instruction == STATIC


# ---------------------------------------------------------------------------
# _upgrade_config: never both fields
# ---------------------------------------------------------------------------

def test_upgrade_long_si_swaps_to_cached_content_only():
    w = _plain()
    cfg = w.config_with(system_instruction=STATIC)
    with patch("core.ai_client.get_or_create_text_cache", return_value="cachedContents/x") as g:
        up, inline = w._upgrade_config(cfg)
    g.assert_called_once_with(w.model_name, STATIC)
    assert up.cached_content == "cachedContents/x"
    assert up.system_instruction is None
    assert inline == STATIC
    assert not _both(up)
    assert cfg.system_instruction == STATIC and cfg.cached_content is None  # original untouched


def test_upgrade_narrator_caches_preamble_plus_static_text():
    w = _narr()
    cfg = w.config_with(system_instruction=STATIC)
    with patch("core.ai_client.get_or_create_text_cache", return_value="cachedContents/n") as g:
        up, inline = w._upgrade_config(cfg)
    g.assert_called_once_with(w.model_name, PREAMBLE + "\n\n" + STATIC)
    assert inline == PREAMBLE + "\n\n" + STATIC
    assert up.system_instruction is None and up.cached_content == "cachedContents/n"


def test_upgrade_cache_unavailable_stays_inline():
    w = _plain()
    cfg = w.config_with(system_instruction=STATIC)
    with patch("core.ai_client.get_or_create_text_cache", return_value=None):
        up, inline = w._upgrade_config(cfg)
    assert inline is None
    assert up.system_instruction == STATIC and up.cached_content is None


def test_upgrade_short_si_never_hits_api():
    w = _plain()
    cfg = w.config_with(system_instruction=SHORT)
    with patch(CREATE) as create:
        up, inline = w._upgrade_config(cfg)
    create.assert_not_called()
    assert inline is None and up.system_instruction == SHORT and up.cached_content is None


def test_upgrade_config_already_cached_is_untouched():
    w = _plain()
    cfg = w.config_with().model_copy(update={"cached_content": "cachedContents/pre"})
    with patch("core.ai_client.get_or_create_text_cache") as g:
        up, inline = w._upgrade_config(cfg)
    g.assert_not_called()
    assert up is cfg and inline is None


def test_upgrade_config_without_si_is_noop():
    w = _plain()
    cfg = w.config_with()
    with patch("core.ai_client.get_or_create_text_cache") as g:
        up, inline = w._upgrade_config(cfg)
    g.assert_not_called()
    assert up is cfg and inline is None


def test_upgrade_config_never_raises_on_helper_failure():
    w = _plain()
    cfg = w.config_with(system_instruction=STATIC)
    with patch("core.ai_client.get_or_create_text_cache", side_effect=RuntimeError("boom")):
        up, inline = w._upgrade_config(cfg)
    assert inline is None and up.system_instruction == STATIC and not _both(up)


def test_generate_content_sends_exactly_one_of_si_or_cached(monkeypatch):
    """End to end through generate_content: whichever way the cache goes, never both fields."""
    w = _plain(model_name="cfg-test-e2e")
    resp = MagicMock()
    resp.text = "{}"
    seen = []

    def gen(model, contents, config):
        seen.append(config)
        return resp

    for create_side in (SimpleNamespace(name="cachedContents/ok"), Exception("400 INVALID_ARGUMENT")):
        ai._CACHE_REGISTRY.clear()
        ai._CACHE_DENY.clear()
        create = MagicMock(side_effect=create_side) if isinstance(create_side, Exception) \
            else MagicMock(return_value=create_side)
        with patch(CREATE, create), patch("core.ai_client.client.models.generate_content", side_effect=gen):
            w.generate_content("p", config=w.config_with(system_instruction=STATIC))
    assert len(seen) == 2
    assert seen[0].cached_content and not seen[0].system_instruction
    assert seen[1].system_instruction == STATIC and not seen[1].cached_content
    assert not any(_both(c) for c in seen)


# ---------------------------------------------------------------------------
# CLAUDE.md 5.1: no network from config_with / build_strict_config
# ---------------------------------------------------------------------------

def test_config_builders_do_not_call_caches_create_from_event_loop():
    w = _plain(model_name="cfg-test-loop")
    n = _narr()

    async def go():
        with patch(CREATE) as create:
            w.config_with(system_instruction=STATIC)
            n.config_with(system_instruction=STATIC)
            n.config_with(system_instruction=STATIC, temperature=0.5)
            build_strict_config(w, schema=None, system_instruction=STATIC)
            build_strict_config(n, system_instruction=STATIC)
            return create.call_count

    assert asyncio.run(go()) == 0


def test_config_builders_do_not_call_get_or_create_text_cache():
    w = _plain()
    with patch("core.ai_client.get_or_create_text_cache") as g, patch(CREATE) as create:
        w.config_with(system_instruction=STATIC)
        build_strict_config(w, system_instruction=STATIC)
    g.assert_not_called()
    create.assert_not_called()


def test_config_with_si_does_not_trigger_wrapper_ensure_cache():
    n = _narr()
    with patch.object(AIWrapper, "_ensure_cache") as ec:
        n.config_with(system_instruction=STATIC)
    ec.assert_not_called()


def test_config_with_runs_in_event_loop_thread_no_blocking_sleep():
    """Sanity: building configs is instantaneous (no network wait) even with a slow create mock."""
    def slow_create(*a, **k):
        time.sleep(1.0)
        return SimpleNamespace(name="x")

    w = _plain()
    t0 = time.perf_counter()
    with patch(CREATE, side_effect=slow_create):
        for _ in range(20):
            w.config_with(system_instruction=STATIC)
    assert time.perf_counter() - t0 < 0.5
