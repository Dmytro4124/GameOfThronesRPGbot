"""Debug-trace мета-інфраструктура в core/engine.py: _meta_start/_meta_stop/_ml, _debug_meta_cleanup,
_roster_diag_lines (лише in-memory), _fmt_call, _trace_finalize (header з Narrator experiment),
поведінка process_game_turn у debug/не-debug (meta_label, start/stop). Без мережі/Sheets."""
import asyncio
import contextvars
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import core.engine as eng
from core import ai_client as aic


@pytest.fixture(autouse=True)
def _reset_debug_state():
    eng._debug_meta_var.set(False)
    aic.stop_call_meta_capture()
    yield
    eng._debug_meta_var.set(False)
    aic.stop_call_meta_capture()


def _in_ctx(fn, *a, **k):
    """Виконує fn в окремому копійованому контексті (ContextVar-зміни не витікають у тест)."""
    return contextvars.copy_context().run(fn, *a, **k)


# ============================ _meta_start / _meta_stop / _ml ============================

def test_meta_start_activates_capture_and_flag():
    def body():
        eng._meta_start()
        return eng._debug_meta_var.get(), aic.get_call_meta(), aic._call_meta_var.get()
    flag, recs, raw = _in_ctx(body)
    assert flag is True and recs == [] and raw == []


def test_meta_stop_deactivates_capture_and_flag():
    def body():
        eng._meta_start()
        eng._meta_stop()
        return eng._debug_meta_var.get(), aic._call_meta_var.get()
    flag, raw = _in_ctx(body)
    assert flag is False and raw is None


def test_meta_stop_is_noop_without_start():
    with patch.object(aic, "stop_call_meta_capture") as stop:
        _in_ctx(eng._meta_stop)
    stop.assert_not_called()


def test_meta_stop_idempotent():
    def body():
        eng._meta_start()
        eng._meta_stop()
        eng._meta_stop()
        return eng._debug_meta_var.get()
    assert _in_ctx(body) is False


def test_meta_start_swallows_api_failure():
    with patch.object(aic, "start_call_meta_capture", side_effect=RuntimeError("x")):
        flag = _in_ctx(lambda: (eng._meta_start(), eng._debug_meta_var.get())[1])
    assert flag is False


def test_meta_start_missing_api_is_noop():
    with patch.object(eng, "_aic", SimpleNamespace()):
        flag = _in_ctx(lambda: (eng._meta_start(), eng._debug_meta_var.get())[1])
    assert flag is False


def test_ml_empty_when_not_debug():
    def f(a, meta_label=None):
        pass
    assert eng._ml("x", f) == {}


def test_ml_label_in_debug_when_callee_accepts():
    def f(a, meta_label=None):
        pass
    assert _in_ctx(lambda: (eng._meta_start(), eng._ml("censor", f))[1]) == {"meta_label": "censor"}


def test_ml_empty_in_debug_when_callee_lacks_param():
    def f(a, b):
        pass
    assert _in_ctx(lambda: (eng._meta_start(), eng._ml("censor", f))[1]) == {}


def test_ml_label_in_debug_when_callee_has_var_kwargs():
    def f(*a, **k):
        pass
    assert _in_ctx(lambda: (eng._meta_start(), eng._ml("w", f))[1]) == {"meta_label": "w"}


def test_ml_without_fn_in_debug_returns_label():
    assert _in_ctx(lambda: (eng._meta_start(), eng._ml("w"))[1]) == {"meta_label": "w"}


def test_ml_uninspectable_callee_returns_empty():
    with patch.object(eng._inspect, "signature", side_effect=ValueError):
        assert _in_ctx(lambda: (eng._meta_start(), eng._ml("w", len))[1]) == {}


def test_meta_mark_zero_outside_debug():
    assert eng._meta_mark() == 0
    assert eng._meta_records() == []


# ============================ _debug_meta_cleanup ============================

def test_cleanup_decorator_preserves_name_and_wrapped():
    assert eng.process_game_turn.__name__ == "process_game_turn"
    assert hasattr(eng.process_game_turn, "__wrapped__")
    assert asyncio.iscoroutinefunction(eng.process_game_turn)


def test_cleanup_decorator_calls_stop_on_normal_return():
    @eng._debug_meta_cleanup
    async def f():
        return 5
    with patch.object(eng, "_meta_stop") as stop:
        assert asyncio.run(f()) == 5
    stop.assert_called_once()


def test_cleanup_decorator_calls_stop_on_exception():
    @eng._debug_meta_cleanup
    async def f():
        raise ValueError("boom")
    with patch.object(eng, "_meta_stop") as stop:
        with pytest.raises(ValueError):
            asyncio.run(f())
    stop.assert_called_once()


def test_cleanup_decorator_calls_stop_on_cancellation():
    @eng._debug_meta_cleanup
    async def f():
        raise asyncio.CancelledError()
    with patch.object(eng, "_meta_stop") as stop:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(f())
    stop.assert_called_once()


def test_cleanup_decorator_really_resets_state():
    @eng._debug_meta_cleanup
    async def f():
        eng._meta_start()
        raise ValueError("boom")

    async def go():
        with pytest.raises(ValueError):
            await f()
        return eng._debug_meta_var.get(), aic.get_call_meta()

    assert asyncio.run(go()) == (False, [])


# ============================ process_game_turn: debug vs non-debug ============================

_PROFILE = {
    "Ім'я": "Тест Герой", "Дім": "Старк", "Титул": "Лорд", "Здоров'я": 100, "Енергія": 800,
    "Особисте Золото": 200, "Бойові навички": 50, "Військові навички": 20, "Інтрига": 15,
    "Управління": 10, "Поточне місцезнаходження": "Вінтерфелл", "Поточна сцена": "Зала",
    "Регіон": "Північ", "Ігровий час": "День 1, Ранок", "Інвентар": "Меч", "Зброя": "Довгий меч",
    "Броня": "Кольчуга", "Транспорт": "Кінь", "Світогляд": "Нейтральний", "Риси": "Хоробрий",
    "Вади": "Упертий", "Вороги": "", "Друзі": "", "Годинники": {"Scene_Tension": "0/4"},
}
_UPD = {
    "action_type": "standard", "skill_used": "None", "difficulty": 10, "outcome": "SUCCESS",
    "dice_roll": "50", "skill_val": 20, "total_score": 70, "reputation_delta": 0,
    "reputation_target_npc": None, "minutes_passed": 10, "location_impact": "none",
    "scene_impact": "none", "health_impact": "none", "energy_impact": "none",
    "gold_impact": "none", "inventory_new": [], "inventory_lost": [], "clocks_impact": {},
}
_GOOD = "Герой обережно ступає кам'яною підлогою великої зали. Смолоскипи кидають тремтливі тіні."


def _gm_resp():
    r = MagicMock()
    r.text = json.dumps({
        "reasoning": "t", "npc_reasoning": "t", "director_notes": ["Двері відчинилися зі скрипом у залі."],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [{"button": f"A{i}", "intent": f"I{i}"} for i in range(1, 5)],
    }, ensure_ascii=False)
    return r


def _resp(text="", finish="STOP"):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)],
                           prompt_feedback=SimpleNamespace(block_reason=None))


def _patches(profile, extra=()):
    return [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(dict(profile), 2))),
        patch("core.engine.save_user_data", new=AsyncMock(return_value=True)),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.resolve_normal_action",
              new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", dict(_UPD)))),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=_gm_resp()),
        patch("core.engine.append_log", new=AsyncMock()),
        patch("core.engine._run_bg_task", side_effect=lambda c: (c.close(), MagicMock())[1]),
        patch("core.engine.NARRATOR_AB_ENABLED", False),
        *extra,
    ]


def _run_turn(chat_id, patches, after=None):
    """Виконує хід; повертає (result, session_snapshot, after()). Сесію чистить."""
    out = {}

    async def go():
        res = await eng.process_game_turn(chat_id, "Оглянути зал", narrator_queue=None)
        out["flag_after"] = eng._debug_meta_var.get()
        out["meta_after"] = aic.get_call_meta()
        return res

    try:
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            res = asyncio.run(go())
            out["session"] = dict(eng.user_sessions.get(chat_id, {}))
            return res, out
    finally:
        eng.user_sessions.pop(chat_id, None)


def test_non_debug_turn_does_not_start_capture_and_passes_no_meta_label():
    hedged = AsyncMock(return_value=_resp(""))      # attempt 1 порожній -> йдемо в attempt 2
    narr = MagicMock(return_value=_resp(_GOOD))
    start = MagicMock()
    extra = [
        patch("core.engine.hedged_generate_content_async", hedged),
        patch("core.engine.model_narrator.generate_content", narr),
        patch("core.engine._meta_start", start),
    ]
    with patch.object(aic, "start_call_meta_capture") as aic_start:
        res, out = _run_turn(7001, _patches(_PROFILE, extra))
    start.assert_not_called()
    aic_start.assert_not_called()
    assert hedged.await_count >= 1
    assert all("meta_label" not in c.kwargs for c in hedged.await_args_list)
    assert narr.call_count >= 1
    assert all("meta_label" not in c.kwargs for c in narr.call_args_list)
    assert "last_debug_trace" not in out["session"]
    assert out["flag_after"] is False


def test_debug_turn_starts_capture_and_passes_meta_labels():
    hedged = AsyncMock(return_value=_resp(""))
    narr = MagicMock(return_value=_resp(_GOOD))
    prof = dict(_PROFILE, _debug_mode=True)
    extra = [
        patch("core.engine.hedged_generate_content_async", hedged),
        patch("core.engine.model_narrator.generate_content", narr),
    ]
    with patch.object(eng, "_meta_start", wraps=eng._meta_start) as start:
        res, out = _run_turn(7002, _patches(prof, extra))
    start.assert_called_once()
    assert all(c.kwargs.get("meta_label") for c in hedged.await_args_list)
    assert all(c.kwargs.get("meta_label") for c in narr.call_args_list)
    assert out["flag_after"] is False and out["meta_after"] == []   # stop у finally
    assert "last_debug_trace" in out["session"]


def test_debug_turn_trace_has_header_with_narrator_experiment_and_rosters():
    narr = MagicMock(return_value=_resp(_GOOD))
    prof = dict(_PROFILE, _debug_mode=True)
    extra = [
        patch("core.engine.hedged_generate_content_async", AsyncMock(return_value=_resp(_GOOD))),
        patch("core.engine.model_narrator.generate_content", narr),
    ]
    res, out = _run_turn(7003, _patches(prof, extra))
    trace = out["session"]["last_debug_trace"]
    header = "\n".join(trace["header"])
    assert "Narrator experiment: model=" in header
    assert "thinking_level=" in header and "preamble_variant=" in header
    assert "Turn total time:" in header and "BOT_VERSION" in header
    assert trace["worker"]["roster"] and trace["gm_logic"]["roster"]
    for st in ("censor", "worker", "gm_logic", "narrator"):
        assert trace[st]["metrics"]
    assert any("content_blocked=" in x for x in trace["narrator_diag"])
    assert "_narr_summary" not in trace


def test_debug_turn_stop_called_even_if_pipeline_raises():
    prof = dict(_PROFILE, _debug_mode=True)
    boom = patch("core.engine.validate_action", new=AsyncMock(side_effect=asyncio.CancelledError()))
    ps = _patches(prof)
    ps.append(boom)  # пізніший patch перекриває validate_action
    stop = MagicMock(wraps=eng._meta_stop)
    with patch.object(eng, "_meta_stop", stop):
        with pytest.raises(asyncio.CancelledError):
            _run_turn(7005, ps)
    stop.assert_called_once()


def test_non_debug_turn_still_calls_stop_harmlessly():
    stop = MagicMock(wraps=eng._meta_stop)
    with patch.object(eng, "_meta_stop", stop):
        _run_turn(7006, _patches(_PROFILE, [
            patch("core.engine.hedged_generate_content_async", AsyncMock(return_value=_resp(_GOOD))),
        ]))
    stop.assert_called_once()


# ============================ _roster_diag_lines ============================

def _session(chat_id, cache, dead=()):
    eng.user_sessions[chat_id] = {"npc_cache": cache, "dead_npc_names": set(dead)}


@pytest.fixture
def _no_sheets():
    """Будь-який виклик до Sheets-обгорток engine має провалити тест."""
    names = [n for n in ("get_location_npcs", "get_dead_npc_names", "get_user_data", "save_user_data",
                         "get_relevant_context") if hasattr(eng, n)]
    with ExitStack() as st:
        mocks = [st.enter_context(patch.object(eng, n, MagicMock(side_effect=AssertionError(n)))) for n in names]
        yield mocks


def test_roster_warns_about_npc_in_cache_but_not_in_roster(_no_sheets):
    _session(8001, {"Вінтерфелл": [{"name": "Тіріон Ланістер", "scene": "Зала"}, {"name": "Арья Старк"}]})
    try:
        lines = eng._roster_diag_lines(8001, ["Арья Старк"], {"director_notes": "Тіріон усміхнувся і підняв кубок."})
    finally:
        eng.user_sessions.pop(8001, None)
    joined = "\n".join(lines)
    assert "legal_npc_names" in joined and "Арья Старк" in joined
    assert "⚠" in joined and "Тіріон Ланістер" in joined and "director_notes" in joined
    assert "Вінтерфелл" in joined and "Зала" in joined
    for m in _no_sheets:
        m.assert_not_called()


def test_roster_no_warning_when_mentioned_npc_is_in_roster():
    _session(8002, {"Вінтерфелл": [{"name": "Тіріон Ланістер"}]})
    try:
        lines = eng._roster_diag_lines(8002, ["Тіріон Ланістер"], {"user_input": "Тіріон Ланістер, привіт"})
    finally:
        eng.user_sessions.pop(8002, None)
    assert not any("⚠" in l for l in lines)
    assert any("no out-of-roster" in l for l in lines)


def test_roster_skipped_without_cache(_no_sheets):
    eng.user_sessions.pop(8003, None)
    lines = eng._roster_diag_lines(8003, ["X"], {"user_input": "Тіріон Ланістер"})
    assert any("cache not loaded" in l or "skipped" in l for l in lines)
    assert not any("⚠" in l for l in lines)
    for m in _no_sheets:
        m.assert_not_called()


def test_roster_skipped_with_empty_cache():
    _session(8004, {})
    try:
        lines = eng._roster_diag_lines(8004, [], {"user_input": "Тіріон"})
    finally:
        eng.user_sessions.pop(8004, None)
    assert any("skipped" in l for l in lines)


def test_roster_ignores_dead_npcs():
    _session(8005, {"Вінтерфелл": [{"name": "Тіріон Ланістер"}]}, dead={"Тіріон Ланістер"})
    try:
        lines = eng._roster_diag_lines(8005, [], {"user_input": "Тіріон Ланістер"})
    finally:
        eng.user_sessions.pop(8005, None)
    assert not any("⚠" in l for l in lines)


def test_roster_short_first_name_token_not_matched_alone():
    _session(8006, {"Вінтерфелл": [{"name": "Арья Старк"}]})
    try:
        lines = eng._roster_diag_lines(8006, [], {"user_input": "арья посміхнулась"})
    finally:
        eng.user_sessions.pop(8006, None)
    assert not any("⚠" in l for l in lines)   # токен < 5 символів


def test_roster_ambiguous_first_name_not_matched_by_token():
    _session(8007, {"Л": [{"name": "Тіріон Ланістер"}, {"name": "Тіріон Фрей"}]})
    try:
        lines = eng._roster_diag_lines(8007, [], {"user_input": "Тіріон усміхнувся"})
    finally:
        eng.user_sessions.pop(8007, None)
    assert not any("⚠" in l for l in lines)


def test_roster_warnings_capped_at_eight():
    names = [f"Персонаж{chr(0x410 + i)}{chr(0x410 + i)} Номер" for i in range(eng._ROSTER_WARN_LIMIT + 4)]
    _session(8008, {"Л": [{"name": n} for n in names]})
    try:
        lines = eng._roster_diag_lines(8008, [], {"user_input": " ".join(names)})
    finally:
        eng.user_sessions.pop(8008, None)
    assert sum(1 for l in lines if "⚠" in l) == eng._ROSTER_WARN_LIMIT
    assert any(f"ще 4 попереджень" in l for l in lines)


def test_roster_survives_malformed_cache():
    eng.user_sessions[8009] = {"npc_cache": {"Л": [None, 5, {"x": 1}]}}
    try:
        lines = eng._roster_diag_lines(8009, [], {"user_input": "Тіріон"})
    finally:
        eng.user_sessions.pop(8009, None)
    assert lines  # не кидає


# ============================ _fmt_call / _mask ============================

def test_fmt_call_basic_and_hedge():
    rec = {"label": "narr.attempt1", "path": "generate", "model": "m", "elapsed_s": 1.5, "hedge_idx": 1,
           "usage": {"prompt": 1, "candidates": 2, "thoughts": 3, "cached": 0, "total": 6},
           "attempts": [{"n": 1, "error_code": 503, "error": "x", "sleep_s": 5.0}], "exception": None}
    out = "\n".join(eng._fmt_call(rec))
    assert "narr.attempt1" in out and "hedge#1" in out and "1/2/3/0/6" in out and "codes=[503]" in out


def test_fmt_call_detail_includes_retry_and_exception_masked():
    rec = {"label": "x", "path": "generate", "model": "m", "safety": ["HARM:LOW"],
           "attempts": [{"n": 1, "error_code": 503, "error": "key=AIzaSyA1234567890123456789012345678901234", "sleep_s": 5}],
           "exception": {"type": "E", "msg": "token=SECRET123"}}
    out = "\n".join(eng._fmt_call(rec, detail=True))
    assert "retry #1" in out and "HARM:LOW" in out
    assert "AIzaSyA1234" not in out and "SECRET123" not in out


def test_fmt_call_non_dict_does_not_raise():
    assert eng._fmt_call("garbage")


def test_fmt_calls_empty():
    assert "no LLM calls" in eng._fmt_calls([])[0]


def test_mask_truncates_and_flattens_newlines():
    out = eng._mask("a\n" * 300, 50)
    assert "\n" not in out and len(out) <= 51


# ============================ _trace_finalize ============================

def _bare_trace():
    return {"censor": {}, "worker": {}, "gm_logic": {}, "narrator": {}}


def test_trace_finalize_none_is_noop():
    eng._trace_finalize(None, 1, 0.0, {})


def test_trace_finalize_header_lines():
    t = _bare_trace()
    import time
    eng._trace_finalize(t, 1, time.time() - 2, {})
    h = "\n".join(t["header"])
    assert "BOT_VERSION" in h and "Flags:" in h
    line = [x for x in t["header"] if "Narrator experiment:" in x][0]
    assert f"model={eng.model_narrator.model_name}" in line
    assert f"thinking_level={eng.model_narrator.thinking_level}" in line
    assert f"preamble_variant={eng.model_narrator.preamble_variant}" in line
    assert "| alt:" not in line
    assert t["header"][-1].strip().startswith("Turn total time:")
    assert t["total_s"] >= 2


def test_trace_finalize_header_includes_alt_when_ab_enabled():
    t = _bare_trace()
    with patch.object(eng, "NARRATOR_AB_ENABLED", True):
        eng._trace_finalize(t, 1, 0.0, {})
    line = [x for x in t["header"] if "Narrator experiment:" in x][0]
    assert "| alt: model=" in line


def test_trace_finalize_non_debug_gives_unavailable_metrics_placeholder():
    t = _bare_trace()
    eng._trace_finalize(t, 1, 0.0, {})
    for st in ("censor", "worker", "gm_logic", "narrator"):
        assert "unavailable" in t[st]["metrics"][0]
    assert "narrator_diag" not in t


def test_trace_finalize_debug_slices_records_per_stage():
    recs = [{"label": f"r{i}", "path": "generate", "model": "m"} for i in range(5)]
    t = _bare_trace()
    t["_narr_summary"] = {"content_blocked": True, "branch": "blocked_notes", "elapsed_s": 1.2,
                          "errors": ["attempt1: X: boom key=AIzaSyA1234567890123456789012345678901234"]}
    marks = {"c0": 0, "c1": 1, "w1": 2, "g1": 3, "n1": 5}

    def body():
        eng._debug_meta_var.set(True)
        with patch.object(aic, "get_call_meta", return_value=recs):
            eng._trace_finalize(t, 1, 0.0, marks)
    contextvars.copy_context().run(body)
    assert "r0" in "\n".join(t["censor"]["metrics"]) and "r1" not in "\n".join(t["censor"]["metrics"])
    assert "r1" in "\n".join(t["worker"]["metrics"])
    assert "r2" in "\n".join(t["gm_logic"]["metrics"])
    nm = "\n".join(t["narrator"]["metrics"])
    assert "r3" in nm and "r4" in nm
    diag = "\n".join(t["narrator_diag"])
    assert "content_blocked=True" in diag and "blocked_notes" in diag
    assert "AIzaSyA1234" not in diag
    assert "_narr_summary" not in t


def test_trace_finalize_never_raises_on_broken_trace():
    eng._trace_finalize({}, 1, 0.0, {})   # немає ключів censor/... -> KeyError ковтається
    eng._trace_finalize("not a dict", 1, 0.0, {})
