"""
Unit-тести для AIWrapper.generate_content з circuit breaker та retry-логікою.
Усі тести мокають:
  - core.ai_client.client.models.generate_content  (Gemini SDK call)
  - core.ai_client.time.sleep                       (щоб тести бігли миттєво)
  - core.ai_client.random.random                    (для детермінованого jitter)
"""
import time
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
