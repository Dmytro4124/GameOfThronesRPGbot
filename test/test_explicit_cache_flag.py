"""Stage 4 follow-ups: GEMINI_EXPLICIT_CACHE flag (default OFF), error classification helpers,
and mode-priority rules in static prompts. No network."""
import importlib
from unittest.mock import MagicMock, patch

import pytest

import core.ai_client as ai
from core.ai_client import AIWrapper, _error_code, _is_transient_error, _is_cache_error
from core import prompts as P

CREATE = "core.ai_client.client.caches.create"
LONG = "L" * 5000


class _Api(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg or f"{code} STATUS")
        self.code = code


@pytest.fixture(autouse=True)
def _clean():
    for d in (ai._CACHE_REGISTRY, ai._CACHE_DENY, ai._CACHE_KEY_LOCKS):
        d.clear()
    yield
    for d in (ai._CACHE_REGISTRY, ai._CACHE_DENY, ai._CACHE_KEY_LOCKS):
        d.clear()


# ---------------------------------------------------------------------------
# config.py flag parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env,expected", [
    (None, False), ("0", False), ("", False), ("true", False), ("1", True), (" 1 ", True),
])
def test_config_flag_parsing(monkeypatch, env, expected):
    import config
    if env is None:
        monkeypatch.delenv("GEMINI_EXPLICIT_CACHE", raising=False)
    else:
        monkeypatch.setenv("GEMINI_EXPLICIT_CACHE", env)
    try:
        importlib.reload(config)
        assert config.GEMINI_EXPLICIT_CACHE_ENABLED is expected
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_flag_default_off_under_test_env():
    assert ai.GEMINI_EXPLICIT_CACHE_ENABLED is False


# ---------------------------------------------------------------------------
# Flag OFF: zero caches.create calls, SI inline
# ---------------------------------------------------------------------------

def test_off_get_or_create_text_cache_returns_none_no_create():
    with patch(CREATE) as create:
        assert ai.get_or_create_text_cache("m", LONG) is None
    create.assert_not_called()
    assert not ai._CACHE_DENY and not ai._CACHE_REGISTRY


def test_off_per_call_si_stays_inline_and_no_create():
    w = AIWrapper("flag-off-model", temperature=0.5, include_thoughts=False)
    seen = []
    resp = MagicMock()
    resp.text = "{}"
    with patch(CREATE) as create, patch("core.ai_client.client.models.generate_content",
                                        side_effect=lambda **k: (seen.append(k["config"]), resp)[1]):
        w.generate_content("p", config=w.config_with(system_instruction=LONG))
    create.assert_not_called()
    assert seen[0].system_instruction == LONG and seen[0].cached_content is None


def test_off_wrapper_level_ensure_cache_no_create_narrator():
    n = AIWrapper("flag-off-narr", temperature=0.5, system_instruction="P" * 400)
    with patch(CREATE) as create:
        assert n._ensure_cache() is None
        cfg = n._build_config()
        n.config_with(temperature=0.3)
    create.assert_not_called()
    assert cfg.system_instruction == "P" * 400 and cfg.cached_content is None


def test_off_stream_inline_no_create():
    w = AIWrapper("flag-off-stream", temperature=0.5, include_thoughts=False)
    sentinel = iter([])
    with patch(CREATE) as create, patch("core.ai_client.client.models.generate_content_stream",
                                        return_value=sentinel) as st:
        assert w.generate_content_stream("p", config=w.config_with(system_instruction=LONG)) is sentinel
    create.assert_not_called()
    assert st.call_args.kwargs["config"].system_instruction == LONG


def test_off_module_wrappers_have_no_cached_content():
    from core.ai_client import model_narrator, model_narrator_alt
    with patch(CREATE) as create:
        for w in (model_narrator, model_narrator_alt):
            cfg = w.config_with(system_instruction=LONG)
            assert cfg.cached_content is None
    create.assert_not_called()


# ---------------------------------------------------------------------------
# _error_code / _is_transient_error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [429, 408, 500, 502, 503, 504, 599])
def test_transient_by_code(code):
    assert _is_transient_error(_Api(code)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_by_code_even_if_text_looks_transient(code):
    assert _is_transient_error(_Api(code, "503 UNAVAILABLE RESOURCE_EXHAUSTED")) is False


def test_error_code_extraction():
    assert _error_code(_Api(429)) == 429
    assert _error_code(Exception("x")) is None
    e = Exception("x")
    e.code = True
    assert _error_code(e) is None
    e.code = "500"
    assert _error_code(e) is None


@pytest.mark.parametrize("msg", [
    "503 UNAVAILABLE. overloaded", "429 RESOURCE_EXHAUSTED", "Error 500 internal", "RESOURCE_EXHAUSTED",
])
def test_transient_by_text_fallback_without_code(msg):
    assert _is_transient_error(Exception(msg)) is True


@pytest.mark.parametrize("msg", [
    "cachedContents/5001 not found", "cachedContents/500", "model v2.503 missing", "id-429-abc",
    "request too small: 5000 chars",
])
def test_word_boundaries_not_transient(msg):
    assert _is_transient_error(Exception(msg)) is False


def test_500_tokens_is_not_transient_text():
    """Coordinator requirement: '500 tokens' must not be classified as transient."""
    assert _is_transient_error(Exception("Cached content is too small: 500 tokens, min 1024")) is False


def test_network_exceptions_transient():
    assert _is_transient_error(TimeoutError("t")) and _is_transient_error(ConnectionError("c"))


def test_is_cache_error_uses_code_first():
    assert _is_cache_error(_Api(404, "CachedContent not found")) is True
    assert _is_cache_error(_Api(503, "CachedContent unavailable")) is False
    assert _is_cache_error(Exception("cachedContents/5001 not found")) is True


@pytest.mark.parametrize("exc,permanent", [
    (_Api(400, "INVALID_ARGUMENT Cached content is too small"), True),
    (_Api(403, "PERMISSION_DENIED"), True),
    (_Api(404, "NOT_FOUND"), True),
    (_Api(429, "RESOURCE_EXHAUSTED"), False),
    (_Api(503, "UNAVAILABLE"), False),
])
def test_create_failure_deny_kind_by_code(monkeypatch, exc, permanent):
    monkeypatch.setattr(ai, "GEMINI_EXPLICIT_CACHE_ENABLED", True)
    with patch(CREATE, side_effect=exc):
        assert ai.get_or_create_text_cache("code-model", LONG) is None
    until = ai._CACHE_DENY[ai._cache_key("code-model", LONG)]
    assert (until == float("inf")) is permanent


def test_create_429_free_tier_limit_0_permanent(monkeypatch):
    monkeypatch.setattr(ai, "GEMINI_EXPLICIT_CACHE_ENABLED", True)
    with patch(CREATE, side_effect=_Api(429, "429 RESOURCE_EXHAUSTED quota limit: 0")):
        ai.get_or_create_text_cache("code-model", LONG)
    assert ai._CACHE_DENY[ai._cache_key("code-model", LONG)] == float("inf")


# ---------------------------------------------------------------------------
# Mode-priority rules in static prompts
# ---------------------------------------------------------------------------

def _gm_args(**kw):
    a = dict(
        hero_name="H", hero_house="Старк", profile_json="{}", context_knowledge="", event_injection="",
        burst_injection="", current_time_str="t", curr_region="r", curr_loc="l", is_traveling=False,
        loc_hint="", curr_scene="s", valid_locs_str="l", valid_regions_str="r", region_locs_str="l",
        npc_context_text="", tension_label="x", mechanics_verdict="SUCCESS", impact_narrative_hints="",
        history_text="", user_input="u", action_slots=["A", "B", "C", "D"],
    )
    a.update(kw)
    return a


@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_gm_static_has_mode_priority_rule(mode):
    static, dyn = P.build_gm_logic_parts(**_gm_args(mode=mode))
    assert "<mode_priority_rule>" in static and "</mode_priority_rule>" in static
    assert "puppet_mode" in static.split("<mode_priority_rule>")[1].split("</mode_priority_rule>")[0]
    assert "<mode_priority_rule>" not in dyn


@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_gm_static_mentions_puppet_without_angle_brackets(mode):
    """The static rule must not contain a literal <puppet_mode tag (only the dynamic prefix may)."""
    static, _ = P.build_gm_logic_parts(**_gm_args(mode=mode))
    assert "<puppet_mode" not in static


def test_gm_puppet_block_only_in_dynamic_when_enabled_and_absent_otherwise():
    s_on, d_on = P.build_gm_logic_parts(**_gm_args(puppet_mode=True))
    s_off, d_off = P.build_gm_logic_parts(**_gm_args(puppet_mode=False))
    assert s_on == s_off
    assert "<puppet_mode" in d_on
    assert "<puppet_mode" not in d_off and "<puppet_mode" not in s_off


@pytest.mark.parametrize("combat", [False, True])
def test_narrator_static_has_mode_priority(combat):
    a = dict(user_input="u", director_notes=["n"], npc_context_text="", player_name="Джон",
             player_house="Старк", current_scene="S", current_location="L", impact_narrative_hints="",
             combat_log=["x"] if combat else None)
    static, dyn = P.build_narrator_parts(**a)
    assert "ПРІОРИТЕТ РЕЖИМІВ" in static
    line = [l for l in static.splitlines() if "ПРІОРИТЕТ РЕЖИМІВ" in l][0]
    assert "CRITICAL_OVERRIDE" in line and "EROTIC_MODE" in line
    assert "ПРІОРИТЕТ РЕЖИМІВ" not in dyn


def test_narrator_constants_contain_priority():
    assert "ПРІОРИТЕТ РЕЖИМІВ" in P.NARRATOR_SYSTEM and "ПРІОРИТЕТ РЕЖИМІВ" in P.NARRATOR_SYSTEM_COMBAT


@pytest.mark.parametrize("_i", range(3))
def test_gm_no_puppet_block_stable_independent_of_env_and_order(_i, monkeypatch):
    monkeypatch.setenv("GEMINI_EXPLICIT_CACHE", "1")
    full = P.build_gm_logic_prompt(**_gm_args(puppet_mode=False))
    assert "<puppet_mode" not in full
