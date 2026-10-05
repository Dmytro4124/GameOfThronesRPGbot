"""Stage 1 tests: AIWrapper.config_with, build_strict_config (inherits full wrapper config),
and removal of the "Schema rejected" no-config fallback in GM_Logic / world initial stats.
No network: client.caches is patched / _cache_attempted pre-set."""
import asyncio
import sys
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

import pytest

import core.ai_client as ai
from core.ai_client import AIWrapper, build_strict_config

_SI = "S" * 300  # >= 200 chars -> eligible for caching


def _wrapper(**kw):
    d = dict(model_name="test-model", temperature=0.9, max_output_tokens=777,
             thinking_level="high", include_thoughts=True, block_none=True,
             system_instruction=_SI)
    d.update(kw)
    w = AIWrapper(**d)
    return w


def _no_cache(w):
    with patch("core.ai_client.client.caches.create", side_effect=Exception("unsupported")):
        w._ensure_cache()
    return w


# ---- 1. config_with ---------------------------------------------------------

def test_config_with_does_not_mutate_wrapper():
    w = _no_cache(_wrapper())
    before = w._build_config().model_dump()
    w.config_with(temperature=0.1, response_mime_type="application/json")
    w.config_with(temperature=0.2)
    assert w._build_config().model_dump() == before
    assert w.temperature == 0.9 and w.response_mime_type is None


def test_config_with_returns_independent_copies():
    w = _no_cache(_wrapper())
    a = w.config_with(temperature=0.5)
    b = w.config_with(temperature=0.5)
    assert a is not b
    assert a.model_dump() == b.model_dump()


def test_config_with_applies_temperature_override():
    w = _no_cache(_wrapper())
    assert w.config_with(temperature=0.5).temperature == 0.5
    assert w.config_with().temperature == 0.9


def test_config_with_preserves_thinking_safety_max_tokens():
    cfg = _no_cache(_wrapper()).config_with(temperature=0.5)
    assert cfg.max_output_tokens == 777
    assert cfg.thinking_config is not None and cfg.thinking_config.include_thoughts is True
    assert len(cfg.safety_settings) == 4
    assert all(str(s.threshold).endswith("BLOCK_NONE") for s in cfg.safety_settings)


def test_config_with_inline_system_instruction_when_cache_unavailable():
    cfg = _no_cache(_wrapper()).config_with(temperature=0.5)
    assert cfg.system_instruction == _SI
    assert cfg.cached_content is None


@pytest.mark.usefixtures("explicit_cache_on")
def test_config_with_cached_content_when_cache_available_xor_system_instruction():
    w = _wrapper()
    fake = MagicMock()
    fake.name = "cachedContents/abc"
    with patch("core.ai_client.client.caches.create", return_value=fake):
        cfg = w.config_with(temperature=0.5)
    assert cfg.cached_content == "cachedContents/abc"
    assert cfg.system_instruction is None


def test_config_with_no_system_instruction_wrapper():
    cfg = _wrapper(system_instruction=None).config_with(temperature=0.3)
    assert cfg.system_instruction is None and cfg.cached_content is None


# ---- 2. build_strict_config -------------------------------------------------

def test_build_strict_config_worker_inherits_everything():
    cfg = build_strict_config(ai.model_worker)
    assert cfg.response_mime_type == "application/json"
    assert str(cfg.thinking_config.thinking_level).upper().endswith("MINIMAL")
    assert len(cfg.safety_settings) == 4
    assert cfg.temperature == ai.model_worker.temperature


def test_build_strict_config_temperature_override():
    assert build_strict_config(ai.model_worker, temperature=0.123).temperature == 0.123


def test_build_strict_config_schema_is_set():
    cfg = build_strict_config(ai.model_worker, schema={"type": "object"})
    assert cfg.response_schema is not None
    assert build_strict_config(ai.model_worker).response_schema is None
    # all other fields are inherited unchanged
    a = cfg.model_dump(exclude={"response_schema"})
    b = build_strict_config(ai.model_worker).model_dump(exclude={"response_schema"})
    assert a == b


def test_build_strict_config_does_not_mutate_wrapper():
    w = _no_cache(_wrapper())
    build_strict_config(w, temperature=0.1)
    assert w.response_mime_type is None and w.temperature == 0.9


def test_build_strict_config_keeps_max_tokens_and_system_instruction():
    w = _no_cache(_wrapper())
    cfg = build_strict_config(w)
    assert cfg.max_output_tokens == 777 and cfg.system_instruction == _SI


# ---- 4a. GM_Logic: no free-JSON fallback ------------------------------------

def test_gm_logic_invalid_argument_no_config_less_retry():
    from test.test_narrator_fallback import _build_patches, _make_narrator_response
    configs = []

    def _gm(prompt, max_retries=6, config=None):
        configs.append(config)
        raise Exception("400 INVALID_ARGUMENT: schema not supported")

    narrator = MagicMock(return_value=_make_narrator_response(
        "The hero stands in the hall and watches the torches flicker over ancient stone walls."))

    async def _run():
        from core.engine import process_game_turn
        try:
            return await process_game_turn(chat_id=9101, user_input="Look around", narrator_queue=None)
        except Exception as exc:  # existing error handling may surface; contract is about calls
            return exc

    with ExitStack() as stack:
        for p in _build_patches(narrator):
            stack.enter_context(p)
        stack.enter_context(patch("core.engine.model_gm_logic.generate_content", side_effect=_gm))
        asyncio.run(_run())

    assert configs, "GM_Logic must have been called"
    assert all(c is not None for c in configs), "no free-JSON (config-less) retry after INVALID_ARGUMENT"
    assert len(configs) <= 2, "at most the existing 1 initial + 1 retry"


def test_gm_logic_none_text_single_retry_with_config():
    from test.test_narrator_fallback import _build_patches, _make_narrator_response, _make_gm_response
    calls = []
    empty = MagicMock()
    empty.text = None
    good = _make_gm_response()

    def _gm(prompt, max_retries=6, config=None):
        calls.append(config)
        return empty if len(calls) == 1 else good

    narrator = MagicMock(return_value=_make_narrator_response(
        "The hero stands in the hall and watches the torches flicker over ancient stone walls."))

    async def _run():
        from core.engine import process_game_turn
        return await process_game_turn(chat_id=9102, user_input="Look around", narrator_queue=None)

    with ExitStack() as stack:
        for p in _build_patches(narrator):
            stack.enter_context(p)
        stack.enter_context(patch("core.engine.model_gm_logic.generate_content", side_effect=_gm))
        asyncio.run(_run())

    assert len(calls) == 2 and all(c is not None for c in calls)


# ---- 4b. world.generate_initial_stats ---------------------------------------

def _world():
    from test import test_world_fallback  # noqa: F401  (installs sys.modules stubs)
    import core.world as w
    return w


def test_initial_stats_invalid_argument_goes_to_deterministic_fallback_without_retry():
    w = _world()
    configs = []

    def _gen(prompt, max_retries=6, config=None):
        configs.append(config)
        raise Exception("400 INVALID_ARGUMENT: schema not supported")

    with patch.object(w, "build_initial_stats_prompt", return_value="P"), \
         patch.object(w.model_gm_logic, "generate_content", side_effect=_gen):
        profile = asyncio.run(w.generate_initial_stats("Test", "Stark", {"Регіон": "Північ"}))

    assert len(configs) == 1 and configs[0] is not None
    assert profile["class"] == "Hedge Knight"  # deterministic fallback
