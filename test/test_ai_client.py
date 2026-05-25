"""
Unit-тести для AIWrapper.generate_content з circuit breaker та retry-логікою.
Усі тести мокають:
  - core.ai_client.client.models.generate_content  (Gemini SDK call)
  - core.ai_client.time.sleep                       (щоб тести бігли миттєво)
  - core.ai_client.random.random                    (для детермінованого jitter)
"""
import time
import asyncio
import pytest
from unittest.mock import patch, MagicMock, call

import core.ai_client as ai_module
from core.ai_client import (
    AIWrapper,
    _CIRCUIT_STATE,
    DEFAULT_MAX_RETRIES,
    CIRCUIT_BREAKER_THRESHOLD,
    CIRCUIT_BREAKER_COOLDOWN,
    get_circuit_breaker_status,
    _normalize_prompt,
    hedged_generate_content_async,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

MODEL = "gemma-4-31b-it"


def _make_wrapper(**kwargs):
    """Повертає AIWrapper з тестовою моделлю (include_thoughts=False за замовчуванням)."""
    defaults = {"model_name": MODEL, "temperature": 0.7, "include_thoughts": False}
    defaults.update(kwargs)
    return AIWrapper(**defaults)


def _reset_cb(model_name=MODEL):
    """Скидає стан circuit breaker між тестами."""
    _CIRCUIT_STATE.pop(model_name, None)


def _fake_response(text="ok"):
    resp = MagicMock()
    resp.text = text
    return resp


def _error(code_or_text: str):
    """Повертає виняток, рядок якого містить вказаний код або текст."""
    return Exception(f"API error {code_or_text} something went wrong")


# ─── Тест 1: Success on first try — no retry, circuit breaker reset ───────────

def test_success_first_try_no_retry():
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _fake_response("hello")

    with patch("core.ai_client.client.models.generate_content", return_value=fake) as mock_gen, \
         patch("core.ai_client.time.sleep") as mock_sleep:
        result = wrapper.generate_content("prompt")

    assert result is fake
    assert mock_gen.call_count == 1
    mock_sleep.assert_not_called()

    # Circuit breaker must be reset (consecutive_failures == 0)
    cb = _CIRCUIT_STATE.get(MODEL, {})
    assert cb.get("consecutive_failures", 0) == 0
    assert cb.get("cooldown_until", 0.0) <= time.time()


# ─── Тест 2: Transient 500, then success — retried once, returns OK ───────────

def test_transient_500_then_success():
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _fake_response("ok")

    side_effects = [_error("500"), fake]

    with patch("core.ai_client.client.models.generate_content", side_effect=side_effects) as mock_gen, \
         patch("core.ai_client.time.sleep") as mock_sleep, \
         patch("core.ai_client.random.random", return_value=0.5):  # jitter = 0
        result = wrapper.generate_content("prompt")

    assert result is fake
    assert mock_gen.call_count == 2
    # One sleep between attempt 1 and attempt 2
    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    # base_delay for transient attempt 1 = min(5*2^0, 60) = 5; jitter=0 when random=0.5
    assert abs(sleep_arg - 5.0) < 0.1


# ─── Тест 3: Persistent 500 × max_retries — raises, consecutive_failures ↑ ───

def test_persistent_500_raises_and_bumps_failures():
    _reset_cb()
    wrapper = _make_wrapper()
    retries = 4  # менше порогу, щоб не відкривати breaker

    with patch("core.ai_client.client.models.generate_content", side_effect=_error("500")), \
         patch("core.ai_client.time.sleep"), \
         patch("core.ai_client.random.random", return_value=0.5):
        with pytest.raises(Exception, match="500"):
            wrapper.generate_content("prompt", max_retries=retries)

    cb = _CIRCUIT_STATE.get(MODEL, {})
    assert cb["consecutive_failures"] == 1


# ─── Тест 4: 5 consecutive full-retry failures → circuit breaker opens ────────

def test_circuit_breaker_opens_after_threshold():
    _reset_cb()
    wrapper = _make_wrapper()

    # Each call exhausts max_retries=2 (2 attempts each) before hitting threshold
    # We need CIRCUIT_BREAKER_THRESHOLD full call groups to open the breaker
    for _ in range(CIRCUIT_BREAKER_THRESHOLD):
        with patch("core.ai_client.client.models.generate_content", side_effect=_error("500")), \
             patch("core.ai_client.time.sleep"), \
             patch("core.ai_client.random.random", return_value=0.5):
            with pytest.raises(Exception):
                wrapper.generate_content("prompt", max_retries=2)

    cb = _CIRCUIT_STATE.get(MODEL, {})
    assert cb["consecutive_failures"] >= CIRCUIT_BREAKER_THRESHOLD
    assert cb["cooldown_until"] > time.time()

    # Next call must fast-fail immediately (circuit open)
    with patch("core.ai_client.client.models.generate_content") as mock_gen, \
         patch("core.ai_client.time.sleep"):
        with pytest.raises(RuntimeError, match="circuit breaker open"):
            wrapper.generate_content("prompt")

    mock_gen.assert_not_called()


# ─── Тест 5: Cooldown expires → next call retries normally ───────────────────

def test_circuit_breaker_resets_after_cooldown():
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _fake_response("ok after cooldown")

    # Manually place the circuit into open state with an expired cooldown
    _CIRCUIT_STATE[MODEL] = {
        "consecutive_failures": CIRCUIT_BREAKER_THRESHOLD,
        "cooldown_until": time.time() - 1.0,  # cooldown already expired
    }

    with patch("core.ai_client.client.models.generate_content", return_value=fake), \
         patch("core.ai_client.time.sleep"):
        result = wrapper.generate_content("prompt")

    assert result is fake
    # consecutive_failures must be reset to 0 on success
    cb = _CIRCUIT_STATE.get(MODEL, {})
    assert cb["consecutive_failures"] == 0


# ─── Тест 6: 429 RESOURCE_EXHAUSTED → uses longer rate-limit backoff ─────────

def test_rate_limit_429_uses_long_backoff():
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _fake_response("ok")

    side_effects = [_error("429"), fake]

    with patch("core.ai_client.client.models.generate_content", side_effect=side_effects), \
         patch("core.ai_client.time.sleep") as mock_sleep, \
         patch("core.ai_client.random.random", return_value=0.5):  # jitter = 0
        result = wrapper.generate_content("prompt")

    assert result is fake
    sleep_arg = mock_sleep.call_args[0][0]
    # base_delay for rate limit attempt 1 = min(30*2^0, 120) = 30
    assert abs(sleep_arg - 30.0) < 0.1


def test_resource_exhausted_uses_long_backoff():
    """RESOURCE_EXHAUSTED (без 429 коду) теж є rate limit."""
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _fake_response("ok")

    side_effects = [Exception("RESOURCE_EXHAUSTED quota exceeded"), fake]

    with patch("core.ai_client.client.models.generate_content", side_effect=side_effects), \
         patch("core.ai_client.time.sleep") as mock_sleep, \
         patch("core.ai_client.random.random", return_value=0.5):
        result = wrapper.generate_content("prompt")

    assert result is fake
    sleep_arg = mock_sleep.call_args[0][0]
    assert sleep_arg >= 25.0  # rate-limit path, not transient path (5s)


# ─── Тест 7: 400 INVALID_ARGUMENT → no retry, raises immediately ─────────────

def test_permanent_400_no_retry():
    _reset_cb()
    wrapper = _make_wrapper()

    with patch("core.ai_client.client.models.generate_content", side_effect=_error("400")) as mock_gen, \
         patch("core.ai_client.time.sleep") as mock_sleep:
        with pytest.raises(Exception, match="400"):
            wrapper.generate_content("prompt")

    assert mock_gen.call_count == 1
    mock_sleep.assert_not_called()


def test_invalid_argument_no_retry():
    """INVALID_ARGUMENT (без 400 коду) теж permanent."""
    _reset_cb()
    wrapper = _make_wrapper()

    with patch("core.ai_client.client.models.generate_content",
               side_effect=Exception("INVALID_ARGUMENT bad request")) as mock_gen, \
         patch("core.ai_client.time.sleep") as mock_sleep:
        with pytest.raises(Exception, match="INVALID_ARGUMENT"):
            wrapper.generate_content("prompt")

    assert mock_gen.call_count == 1
    mock_sleep.assert_not_called()


# ─── Тест 8: Jitter is applied — sleep value differs from base ───────────────

def test_jitter_applied_to_sleep():
    """Перевіряє, що jitter реально впливає на sleep-значення.
    random.random() = 0.0 → jitter = -20% від base.
    random.random() = 1.0 → jitter = +20% від base.
    """
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _fake_response("ok")

    # attempt 1 fails with transient 500, attempt 2 succeeds
    side_effects = [_error("500"), fake]

    # Test with random=0.0 → jitter = base * 0.2 * (2*0 - 1) = -20%
    with patch("core.ai_client.client.models.generate_content", side_effect=list(side_effects)), \
         patch("core.ai_client.time.sleep") as mock_sleep_low, \
         patch("core.ai_client.random.random", return_value=0.0):
        wrapper.generate_content("prompt")
    sleep_low = mock_sleep_low.call_args[0][0]

    _reset_cb()
    # Test with random=1.0 → jitter = base * 0.2 * (2*1 - 1) = +20%
    with patch("core.ai_client.client.models.generate_content", side_effect=list(side_effects)), \
         patch("core.ai_client.time.sleep") as mock_sleep_high, \
         patch("core.ai_client.random.random", return_value=1.0):
        wrapper.generate_content("prompt")
    sleep_high = mock_sleep_high.call_args[0][0]

    # base = 5.0 for transient attempt 1; low = 4.0, high = 6.0
    assert abs(sleep_low - 4.0) < 0.01
    assert abs(sleep_high - 6.0) < 0.01
    assert sleep_high > sleep_low


# ─── Тест 9: get_circuit_breaker_status — правильно відображає стан ──────────

def test_get_circuit_breaker_status_closed():
    _reset_cb()
    status = get_circuit_breaker_status(MODEL)
    assert status["model"] == MODEL
    assert status["consecutive_failures"] == 0
    assert status["cooldown_remaining"] == 0.0
    assert status["is_open"] is False


def test_get_circuit_breaker_status_open():
    future_time = time.time() + 50.0
    _CIRCUIT_STATE[MODEL] = {"consecutive_failures": 5, "cooldown_until": future_time}
    status = get_circuit_breaker_status(MODEL)
    assert status["is_open"] is True
    assert status["consecutive_failures"] == 5
    assert status["cooldown_remaining"] > 40.0
    _reset_cb()


# ─── Тест 10: include_thoughts=True path — _AIResponse повертається ──────────

def test_include_thoughts_returns_ai_response():
    """Перевіряє, що при include_thoughts=True повертається _AIResponse з content."""
    _reset_cb()
    wrapper = _make_wrapper(include_thoughts=True)

    # Мокаємо split_thoughts — повертає ("thought text", "content text")
    fake_raw = MagicMock()

    with patch("core.ai_client.client.models.generate_content", return_value=fake_raw), \
         patch("core.ai_client.split_thoughts", return_value=("a thought", "content text")) as mock_split, \
         patch("core.ai_client.time.sleep"):
        result = wrapper.generate_content("prompt")

    from core.ai_client import _AIResponse
    assert isinstance(result, _AIResponse)
    assert result.text == "content text"
    mock_split.assert_called_once_with(fake_raw)


# ─── Item 7: Unicode normalize ────────────────────────────────────────────────

def test_normalize_prompt_nfc():
    """Decomposed and composed forms of the same character produce identical NFC output."""
    composed = "é"              # é as single codepoint (NFC)
    decomposed = "é"          # e + combining acute accent (NFD)
    assert composed != decomposed, "Pre-condition: inputs must differ"
    assert _normalize_prompt(composed) == _normalize_prompt(decomposed)


def test_normalize_handles_emoji_zwj():
    """Emoji with ZWJ sequences normalise without raising an exception."""
    zwj_emoji = "\U0001F468‍\U0001F469‍\U0001F467"  # family ZWJ sequence
    result = _normalize_prompt(zwj_emoji)
    assert isinstance(result, str)


def test_normalize_handles_non_string():
    """Non-string values (int, None) are returned as-is without crashing."""
    assert _normalize_prompt(42) == 42
    assert _normalize_prompt(None) is None


# ─── Item 3: Hedging ─────────────────────────────────────────────────────────

def _make_fake_resp(text="ok"):
    resp = MagicMock()
    resp.text = text
    return resp


def test_hedged_first_success():
    """When both hedges succeed, one of the results is returned."""
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _make_fake_resp("hedged-ok")

    # generate_content is synchronous; asyncio.to_thread will call it in a thread.
    # Patching generate_content directly and letting real asyncio.to_thread work.
    with patch("core.ai_client.client.models.generate_content", return_value=fake), \
         patch("core.ai_client.time.sleep"):
        result = asyncio.run(
            hedged_generate_content_async(wrapper, "prompt", hedge_count=2, max_retries=1)
        )

    assert result is fake
    assert result.text == "hedged-ok"


def test_hedged_first_fails_second_succeeds():
    """When both tasks finish together (same wait cycle) and one raised, the
    successful result from the other task is returned.

    Both coroutines complete before asyncio.wait returns (no 'pending'
    tasks), so the hedge loop inspects all 'done' tasks and picks the
    first non-exception result.
    """
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _make_fake_resp("second-ok")

    async def _run():
        # Both complete instantly — asyncio.wait will return both in 'done'.
        async def _fail():
            raise Exception("500 first fails")

        async def _succeed():
            return fake

        # Deterministic: task 0 → fail, task 1 → succeed
        coro_queue = [_fail(), _succeed()]
        coro_idx = {"n": 0}

        async def _mock_to_thread(fn, *args, **kwargs):
            c = coro_queue[coro_idx["n"]]
            coro_idx["n"] += 1
            return await c

        with patch("core.ai_client.asyncio.to_thread", side_effect=_mock_to_thread):
            return await hedged_generate_content_async(
                wrapper, "prompt", hedge_count=2, max_retries=2
            )

    result = asyncio.run(_run())
    assert result is fake


def test_hedged_both_fail():
    """When all hedges raise, the function propagates an exception."""
    _reset_cb()
    wrapper = _make_wrapper()

    with patch("core.ai_client.client.models.generate_content",
               side_effect=Exception("500 INTERNAL both fail")), \
         patch("core.ai_client.time.sleep"):
        with pytest.raises(Exception):
            asyncio.run(
                hedged_generate_content_async(wrapper, "prompt", hedge_count=2, max_retries=1)
            )


def test_hedged_cancels_pending():
    """Once first task succeeds, pending tasks are cancelled before returning."""
    _reset_cb()
    wrapper = _make_wrapper()
    fake = _make_fake_resp("fast-ok")

    fast_done = False
    slow_cancelled = False

    async def _run():
        nonlocal fast_done, slow_cancelled

        async def _fast_task():
            nonlocal fast_done
            fast_done = True
            return fake

        async def _slow_task():
            nonlocal slow_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                slow_cancelled = True
                raise
            return _make_fake_resp("slow")

        coro_queue = [_fast_task(), _slow_task()]
        coro_idx = {"n": 0}

        async def _mock_to_thread(fn, *args, **kwargs):
            c = coro_queue[coro_idx["n"]]
            coro_idx["n"] += 1
            return await c

        with patch("core.ai_client.asyncio.to_thread", side_effect=_mock_to_thread):
            result = await hedged_generate_content_async(
                wrapper, "prompt", hedge_count=2, max_retries=2
            )
        await asyncio.sleep(0)  # let CancelledError propagate
        return result

    result = asyncio.run(_run())
    assert result is fake
    assert fast_done is True
    assert slow_cancelled is True


# ─── Item 4: Prompt caching ───────────────────────────────────────────────────

def test_cache_skipped_when_not_supported():
    """When client.caches.create raises, _ensure_cache returns None and system_instruction is used inline."""
    long_instruction = "x" * 1001  # > 1000 chars threshold
    wrapper = _make_wrapper(system_instruction=long_instruction)

    with patch("core.ai_client.client.caches.create", side_effect=Exception("caching not supported")):
        cache_name = wrapper._ensure_cache()

    assert cache_name is None
    assert wrapper._cached_content_name is None
    assert wrapper._cache_attempted is True

    # _build_config must fall back to inline system_instruction
    with patch("core.ai_client.client.caches.create", side_effect=Exception("not supported")):
        wrapper2 = _make_wrapper(system_instruction=long_instruction)
        cfg = wrapper2._build_config()

    # system_instruction should be present in config (cache path failed)
    assert cfg.system_instruction == long_instruction


def test_cache_skipped_for_short_instruction():
    """system_instruction shorter than 1000 chars is never sent to caches.create."""
    wrapper = _make_wrapper(system_instruction="short")

    with patch("core.ai_client.client.caches.create") as mock_create:
        cache_name = wrapper._ensure_cache()

    mock_create.assert_not_called()
    assert cache_name is None


def test_cache_idempotent():
    """_ensure_cache is idempotent: caches.create called only once even if invoked multiple times."""
    long_instruction = "y" * 1001
    wrapper = _make_wrapper(system_instruction=long_instruction)
    fake_cache = MagicMock()
    fake_cache.name = "cachedContents/abc123"

    with patch("core.ai_client.client.caches.create", return_value=fake_cache) as mock_create:
        name1 = wrapper._ensure_cache()
        name2 = wrapper._ensure_cache()
        name3 = wrapper._ensure_cache()

    mock_create.assert_called_once()
    assert name1 == name2 == name3 == "cachedContents/abc123"


# ─── Issue 5: model_worker та model_gm_logic мають block_none=True ──────────

def test_worker_model_has_block_none():
    """
    Issue 5: model_worker та model_gm_logic повинні мати block_none=True.

    Без цього Gemini може блокувати grimdark-контент (насильство, сексуальний
    підтекст, жорстокість) і повертати порожні відповіді замість JSON.
    Це зламає парсер і весь pipeline.

    Перевіряємо атрибут block_none на module-level instances.
    """
    from core.ai_client import model_worker, model_gm_logic

    assert model_worker.block_none is True, (
        "model_worker must have block_none=True. "
        "Without BLOCK_NONE safety settings, Gemini blocks grimdark RPG content "
        "and returns empty responses instead of JSON (Issue 5)."
    )
    assert model_gm_logic.block_none is True, (
        "model_gm_logic must have block_none=True. "
        "Without BLOCK_NONE safety settings, GM_Logic stage fails on violent content (Issue 5)."
    )


def test_worker_model_block_none_produces_four_safety_settings():
    """
    Коли block_none=True, _build_config повинен включати safety_settings
    рівно для 4 категорій: SEXUALLY_EXPLICIT, DANGEROUS_CONTENT,
    HATE_SPEECH, HARASSMENT — всі зі значенням BLOCK_NONE.
    """
    from core.ai_client import AIWrapper
    from google.genai import types

    wrapper = AIWrapper(
        model_name="test-model",
        temperature=0.7,
        block_none=True,
    )

    with patch("core.ai_client.client.caches.create", side_effect=Exception("no cache")):
        config = wrapper._build_config()

    assert config.safety_settings is not None, (
        "_build_config with block_none=True must produce safety_settings"
    )
    assert len(config.safety_settings) == 4, (
        f"Expected 4 safety settings (one per harm category), "
        f"got {len(config.safety_settings)}"
    )

    # Перевіряємо, що всі налаштування мають threshold BLOCK_NONE
    for setting in config.safety_settings:
        threshold_str = str(setting.threshold)
        assert "BLOCK_NONE" in threshold_str, (
            f"All safety settings must have BLOCK_NONE threshold. "
            f"Got: {threshold_str!r} for category {setting.category}"
        )


def test_model_without_block_none_has_no_safety_settings():
    """
    Контрольний тест: AIWrapper без block_none=True
    НЕ додає safety_settings до config.
    """
    from core.ai_client import AIWrapper

    wrapper = AIWrapper(
        model_name="test-model",
        temperature=0.7,
        block_none=False,
    )

    with patch("core.ai_client.client.caches.create", side_effect=Exception("no cache")):
        config = wrapper._build_config()

    # safety_settings має бути None або не встановлено
    # (default у GenerateContentConfig)
    assert not config.safety_settings, (
        f"AIWrapper without block_none must NOT add safety_settings. "
        f"Got: {config.safety_settings!r}"
    )


# ─── Тест 1 (нові): build_strict_config ignores schema ──────────────────────

def test_build_strict_config_ignores_schema():
    """build_strict_config приймає schema-аргумент, але НЕ встановлює response_schema.

    Строгий constrained decoding (response_schema) спричиняв 14-хвилинні зависання
    на gemma-4-31b-it preview. Параметр schema — no-op для сумісності сигнатури.
    Перевіряємо:
    - response_schema is None (або відсутній)
    - response_mime_type == "application/json"
    - safety_settings присутні (model_worker має block_none=True)
    - temperature встановлено
    """
    from core.ai_client import build_strict_config, model_worker

    # Передаємо довільну схему — вона має ігноруватись
    dummy_schema = {"type": "object", "properties": {"key": {"type": "string"}}}

    cfg = build_strict_config(model_worker, schema=dummy_schema, temperature=0.7)

    # response_schema must NOT be set
    assert not hasattr(cfg, "response_schema") or cfg.response_schema is None, (
        "build_strict_config must NOT set response_schema: "
        "strict constrained decoding causes 14-min hangs on gemma-4-31b-it preview"
    )
    # JSON mode via MIME type must still be active
    assert cfg.response_mime_type == "application/json", (
        f"response_mime_type must be 'application/json', got {cfg.response_mime_type!r}"
    )
    # model_worker has block_none=True → safety_settings must be present
    assert cfg.safety_settings, (
        "build_strict_config must inherit safety_settings from model_wrapper.block_none"
    )
    assert len(cfg.safety_settings) == 4
    # temperature must be set
    assert cfg.temperature == 0.7, (
        f"temperature must be 0.7, got {cfg.temperature!r}"
    )


# ─── Тест 2 (нові): DEFAULT_MAX_RETRIES regression ──────────────────────────

def test_default_max_retries_is_three():
    """Регресійний тест: DEFAULT_MAX_RETRIES повернуто з 6 назад до 3.

    Значення 6 спричиняло надто довгі retry-цикли при MALFORMED_RESPONSE
    від preview-моделі. Цей тест гарантує, що ніхто випадково не поверне 6.
    """
    from core.ai_client import DEFAULT_MAX_RETRIES
    assert DEFAULT_MAX_RETRIES == 3, (
        f"DEFAULT_MAX_RETRIES must be 3 (reverted from 6). Got: {DEFAULT_MAX_RETRIES}"
    )


# ─── Тест 3 (нові): split_thoughts failure → raw returned, no retry ─────────

def test_split_thoughts_failure_returns_raw(caplog):
    """Коли split_thoughts кидає TypeError (MALFORMED_RESPONSE),
    generate_content повертає raw без retry.

    Перевіряємо:
    - client.models.generate_content викликано РІВНО 1 раз (не 3)
    - повернуто raw (той самий об'єкт, що повернув mock)
    - logger.warning записав повідомлення з 'split_thoughts failed'
    """
    import logging
    _reset_cb()
    wrapper = _make_wrapper(include_thoughts=True)
    fake_raw = MagicMock()
    fake_raw.text = '{"key": "value"}'

    with patch("core.ai_client.client.models.generate_content", return_value=fake_raw) as mock_gen, \
         patch("core.ai_client.split_thoughts",
               side_effect=TypeError("'NoneType' object is not iterable")), \
         patch("core.ai_client.time.sleep"), \
         caplog.at_level(logging.WARNING, logger="core.ai_client"):
        result = wrapper.generate_content("prompt")

    # Повинен повернути raw (без retry і без crash)
    assert result is fake_raw, (
        "generate_content must return raw response when split_thoughts raises TypeError"
    )
    # Рівно 1 виклик — без retry-циклу
    assert mock_gen.call_count == 1, (
        f"generate_content must be called exactly once (no retry on split_thoughts failure). "
        f"Got: {mock_gen.call_count}"
    )
    # warning має бути залогований
    assert "split_thoughts failed" in caplog.text, (
        f"logger.warning must contain 'split_thoughts failed'. "
        f"caplog.text: {caplog.text!r}"
    )


# ─── Тест 4 (нові): split_thoughts success → _AIResponse повернуто ──────────

def test_split_thoughts_success_returns_ai_response():
    """Happy-path regression: коли split_thoughts повертає (thoughts, content) нормально,
    generate_content повертає _AIResponse(content).

    Перевіряємо:
    - результат є екземпляром _AIResponse
    - result.text == content_part (без thoughts)
    - client.models.generate_content викликано 1 раз
    """
    from core.ai_client import _AIResponse
    _reset_cb()
    wrapper = _make_wrapper(include_thoughts=True)
    fake_raw = MagicMock()
    expected_content = '{"verdict": "success"}'

    with patch("core.ai_client.client.models.generate_content", return_value=fake_raw) as mock_gen, \
         patch("core.ai_client.split_thoughts",
               return_value=("some internal thought", expected_content)), \
         patch("core.ai_client.time.sleep"):
        result = wrapper.generate_content("prompt")

    assert isinstance(result, _AIResponse), (
        f"generate_content must return _AIResponse on successful split_thoughts. "
        f"Got: {type(result).__name__}"
    )
    assert result.text == expected_content, (
        f"_AIResponse.text must equal the content part from split_thoughts. "
        f"Got: {result.text!r}"
    )
    assert mock_gen.call_count == 1, (
        f"generate_content must be called exactly once. Got: {mock_gen.call_count}"
    )
