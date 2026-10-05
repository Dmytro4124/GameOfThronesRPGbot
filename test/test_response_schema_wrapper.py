"""Stage 2: AIWrapper.generate_content — centralised response_schema fallback.

400 INVALID_ARGUMENT with an active response_schema -> one retry WITHOUT schema
(attempt budget not consumed, no sleep, circuit breaker untouched); the rejection is
memoised in _REJECTED_SCHEMAS by (model_name, schema fingerprint).
No network: client.models.generate_content and time.sleep are patched.
"""
from unittest.mock import MagicMock, patch

import pytest

import core.ai_client as ai
from core.ai_client import (
    AIWrapper, build_strict_config, _CIRCUIT_STATE, _REJECTED_SCHEMAS,
    _schema_fingerprint, _is_bad_request, get_circuit_breaker_status,
)
from core.prompts import CENSOR_SCHEMA, WORKER_COMBAT_SCHEMA

MODEL = "schema-test-model"
GEN = "core.ai_client.client.models.generate_content"
SLEEP = "core.ai_client.time.sleep"


class _ApiError(Exception):
    """Mimics google.genai.errors.ClientError/ServerError (has .code)."""
    def __init__(self, code, status):
        super().__init__(f"{code} {status}. {{'error': {{'code': {code}, 'status': '{status}'}}}}")
        self.code = code


def _bad_request():
    return _ApiError(400, "INVALID_ARGUMENT")


def _ok(text="{}"):
    r = MagicMock()
    r.text = text
    return r


def _wrapper():
    return AIWrapper(model_name=MODEL, temperature=0.5, include_thoughts=False)


def _cfg(schema=CENSOR_SCHEMA):
    return build_strict_config(_wrapper(), schema=schema)


def _cfg_schemas(mock_gen):
    return [c.kwargs["config"].response_schema for c in mock_gen.call_args_list]


@pytest.fixture(autouse=True)
def _clean_state():
    _REJECTED_SCHEMAS.clear()
    _CIRCUIT_STATE.pop(MODEL, None)
    yield
    _REJECTED_SCHEMAS.clear()
    _CIRCUIT_STATE.pop(MODEL, None)


# (a) 400 with schema -> second call without schema ---------------------------

def test_400_with_schema_retries_without_schema_and_returns_result():
    ok = _ok("RESULT")
    with patch(GEN, side_effect=[_bad_request(), ok]) as gen, patch(SLEEP) as sl:
        res = _wrapper().generate_content("p", config=_cfg())
    assert res is ok
    assert gen.call_count == 2
    schemas = _cfg_schemas(gen)
    assert schemas[0] is not None
    assert schemas[1] is None
    sl.assert_not_called()


def test_400_with_schema_keeps_other_config_fields_on_retry():
    with patch(GEN, side_effect=[_bad_request(), _ok()]) as gen, patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    first, second = (c.kwargs["config"] for c in gen.call_args_list)
    assert second.response_mime_type == first.response_mime_type == "application/json"
    assert second.temperature == first.temperature


def test_400_with_schema_does_not_touch_circuit_breaker():
    _CIRCUIT_STATE[MODEL] = {"consecutive_failures": 2, "cooldown_until": 0.0}
    # success resets the counter normally; check the state *between* the 400 and success
    seen = {}

    def _side(*a, **kw):
        if kw["config"].response_schema is not None:
            raise _bad_request()
        seen["failures_after_400"] = _CIRCUIT_STATE[MODEL]["consecutive_failures"]
        return _ok()

    with patch(GEN, side_effect=_side), patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    assert seen["failures_after_400"] == 2  # not bumped by the 400
    assert get_circuit_breaker_status(MODEL)["is_open"] is False


def test_400_with_schema_adds_fingerprint_to_rejected():
    with patch(GEN, side_effect=[_bad_request(), _ok()]), patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    assert (MODEL, _schema_fingerprint(CENSOR_SCHEMA)) in _REJECTED_SCHEMAS


def test_400_text_only_error_without_code_attr_also_triggers_fallback():
    err = Exception("400 INVALID_ARGUMENT: response_schema unsupported")
    with patch(GEN, side_effect=[err, _ok()]) as gen, patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    assert gen.call_count == 2


# (b) memoised rejection -> subsequent call goes straight without schema -------

def test_subsequent_call_with_same_schema_goes_without_schema_single_sdk_call():
    with patch(GEN, side_effect=[_bad_request(), _ok()]), patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    with patch(GEN, return_value=_ok()) as gen, patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    assert gen.call_count == 1
    assert gen.call_args.kwargs["config"].response_schema is None


def test_rejection_is_per_schema_other_schema_still_sent():
    with patch(GEN, side_effect=[_bad_request(), _ok()]), patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg(CENSOR_SCHEMA))
    with patch(GEN, return_value=_ok()) as gen, patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg(WORKER_COMBAT_SCHEMA))
    assert gen.call_args.kwargs["config"].response_schema is not None


def test_rejection_is_per_model():
    with patch(GEN, side_effect=[_bad_request(), _ok()]), patch(SLEEP):
        _wrapper().generate_content("p", config=_cfg())
    other = AIWrapper(model_name=MODEL + "-other", temperature=0.5, include_thoughts=False)
    try:
        with patch(GEN, return_value=_ok()) as gen:
            other.generate_content("p", config=build_strict_config(other, schema=CENSOR_SCHEMA))
        assert gen.call_args.kwargs["config"].response_schema is not None
    finally:
        _CIRCUIT_STATE.pop(MODEL + "-other", None)


# (c) 400 without schema -> permanent raise as before --------------------------

def test_400_without_schema_raises_immediately_no_retry():
    cfg = build_strict_config(_wrapper())  # schema=None
    with patch(GEN, side_effect=_bad_request()) as gen, patch(SLEEP) as sl:
        with pytest.raises(Exception, match="INVALID_ARGUMENT"):
            _wrapper().generate_content("p", config=cfg)
    assert gen.call_count == 1
    sl.assert_not_called()
    assert not _REJECTED_SCHEMAS


# (d) 429 / 5xx with schema must NOT memoise the schema ------------------------

@pytest.mark.parametrize("err", [
    _ApiError(429, "RESOURCE_EXHAUSTED"),
    _ApiError(500, "INTERNAL"),
    _ApiError(503, "UNAVAILABLE"),
])
def test_transient_and_rate_limit_do_not_reject_schema(err):
    ok = _ok()
    with patch(GEN, side_effect=[err, ok]) as gen, patch(SLEEP):
        res = _wrapper().generate_content("p", config=_cfg())
    assert res is ok
    assert not _REJECTED_SCHEMAS
    # the retry keeps the schema
    assert all(s is not None for s in _cfg_schemas(gen))


# (e) 400 on the schema-less retry -> raise, no infinite loop ------------------

def test_400_on_retry_without_schema_raises_after_two_calls():
    with patch(GEN, side_effect=_bad_request()) as gen, patch(SLEEP) as sl:
        with pytest.raises(Exception, match="INVALID_ARGUMENT"):
            _wrapper().generate_content("p", config=_cfg())
    assert gen.call_count == 2
    sl.assert_not_called()
    assert _cfg_schemas(gen)[1] is None


def test_400_on_retry_does_not_bump_circuit_breaker():
    with patch(GEN, side_effect=_bad_request()), patch(SLEEP):
        with pytest.raises(Exception):
            _wrapper().generate_content("p", config=_cfg())
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 0


# (f) retry budget regression (for -> while) ----------------------------------

@pytest.mark.parametrize("max_retries", [1, 2, 3, 5])
def test_transient_errors_use_exactly_max_retries_attempts(max_retries):
    with patch(GEN, side_effect=_ApiError(503, "UNAVAILABLE")) as gen, patch(SLEEP) as sl, \
            patch("core.ai_client.random.random", return_value=0.5):
        with pytest.raises(Exception, match="UNAVAILABLE"):
            _wrapper().generate_content("p", max_retries=max_retries,
                                        config=build_strict_config(_wrapper()))
    assert gen.call_count == max_retries
    assert sl.call_count == max_retries - 1
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 1


def test_default_budget_is_three_attempts():
    with patch(GEN, side_effect=_ApiError(500, "INTERNAL")) as gen, patch(SLEEP):
        with pytest.raises(Exception):
            _wrapper().generate_content("p")
    assert gen.call_count == ai.DEFAULT_MAX_RETRIES == 3


def test_schema_400_does_not_consume_retry_budget():
    # 400 (schema) + max_retries transient failures -> max_retries + 1 SDK calls
    side = [_bad_request()] + [_ApiError(503, "UNAVAILABLE")] * 3
    with patch(GEN, side_effect=side) as gen, patch(SLEEP):
        with pytest.raises(Exception, match="UNAVAILABLE"):
            _wrapper().generate_content("p", max_retries=3, config=_cfg())
    assert gen.call_count == 4
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 1


def test_success_after_transient_returns_and_resets_breaker():
    ok = _ok()
    with patch(GEN, side_effect=[_ApiError(503, "UNAVAILABLE"), ok]), patch(SLEEP):
        assert _wrapper().generate_content("p", config=_cfg()) is ok
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 0


def test_max_retries_zero_does_not_call_sdk_and_raises():
    # edge: while-loop with max_retries=0 must terminate (raise last_error=None -> TypeError)
    with patch(GEN) as gen:
        with pytest.raises(BaseException):
            _wrapper().generate_content("p", max_retries=0)
    assert gen.call_count == 0


# helpers ---------------------------------------------------------------------

@pytest.mark.parametrize("exc,expected", [
    (_ApiError(400, "INVALID_ARGUMENT"), True),
    (Exception("400 INVALID_ARGUMENT x"), True),
    (_ApiError(429, "RESOURCE_EXHAUSTED"), False),
    (_ApiError(503, "UNAVAILABLE"), False),
    (Exception("boom"), False),
    (Exception("INVALID_ARGUMENT without code"), False),
])
def test_is_bad_request(exc, expected):
    assert _is_bad_request(exc) is expected


def test_schema_fingerprint_stable_and_distinct():
    assert _schema_fingerprint(CENSOR_SCHEMA) == _schema_fingerprint(dict(CENSOR_SCHEMA))
    assert _schema_fingerprint(CENSOR_SCHEMA) != _schema_fingerprint(WORKER_COMBAT_SCHEMA)


# ── Review follow-up: memoise only after a SUCCESSFUL schema-less retry ─────

def test_rejected_and_logged_only_after_successful_retry(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="core.ai_client"):
        with patch(GEN, side_effect=[_bad_request(), _ok()]), patch(SLEEP):
            _wrapper().generate_content("p", config=_cfg())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("response_schema rejected (400)" in m and "retrying without schema" in m for m in msgs)
    assert any("schema remembered as rejected" in m for m in msgs)
    assert (MODEL, _schema_fingerprint(CENSOR_SCHEMA)) in _REJECTED_SCHEMAS


def test_not_remembered_when_retry_also_fails_with_400(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="core.ai_client"):
        with patch(GEN, side_effect=_bad_request()), patch(SLEEP):
            with pytest.raises(Exception, match="INVALID_ARGUMENT"):
                _wrapper().generate_content("p", config=_cfg())
    assert not _REJECTED_SCHEMAS
    msgs = [r.getMessage() for r in caplog.records]
    assert any("response_schema rejected (400)" in m for m in msgs)
    assert not any("schema remembered as rejected" in m for m in msgs)


def test_not_remembered_when_retry_fails_with_transient_exhaustion():
    side = [_bad_request()] + [_ApiError(503, "UNAVAILABLE")] * 3
    with patch(GEN, side_effect=side), patch(SLEEP):
        with pytest.raises(Exception, match="UNAVAILABLE"):
            _wrapper().generate_content("p", max_retries=3, config=_cfg())
    assert not _REJECTED_SCHEMAS


def test_next_call_after_failed_retry_sends_schema_again():
    with patch(GEN, side_effect=_bad_request()), patch(SLEEP):
        with pytest.raises(Exception):
            _wrapper().generate_content("p", config=_cfg())
    ok = _ok()
    with patch(GEN, return_value=ok) as gen, patch(SLEEP):
        assert _wrapper().generate_content("p", config=_cfg()) is ok
    assert gen.call_count == 1
    assert gen.call_args.kwargs["config"].response_schema is not None


def test_failed_retry_leaves_breaker_and_attempts_untouched():
    _CIRCUIT_STATE[MODEL] = {"consecutive_failures": 3, "cooldown_until": 0.0}
    with patch(GEN, side_effect=_bad_request()) as gen, patch(SLEEP) as sl:
        with pytest.raises(Exception):
            _wrapper().generate_content("p", max_retries=1, config=_cfg())
    # max_retries=1: the schema 400 did not consume the single attempt -> 2 SDK calls
    assert gen.call_count == 2
    sl.assert_not_called()
    assert _CIRCUIT_STATE[MODEL]["consecutive_failures"] == 3


def test_successful_retry_does_not_consume_attempt_and_no_sleep():
    ok = _ok()
    with patch(GEN, side_effect=[_bad_request(), ok]) as gen, patch(SLEEP) as sl:
        assert _wrapper().generate_content("p", max_retries=1, config=_cfg()) is ok
    assert gen.call_count == 2
    sl.assert_not_called()
