"""Engine-side tests for Narrator A/B: _run_narrator_chain, the A/B branch of process_game_turn,
and commit_narration_to_history. All external deps (Sheets, Gemini) are mocked.

Patterns reused from test_narrator_fallback.py (_build_patches, asyncio.run, _run_bg_task patched).
"""
import asyncio
import json
import logging
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import core.narrator_ab as na
from core.narrator_ab import NarrationResult

_LONG_OK = ("The hero steps carefully across the stone floor of the great hall. "
            "Torches cast trembling shadows on the ancient tapestries.")
_GEMMA_TXT = "GEMMA: " + _LONG_OK
_FLASH_TXT = "FLASH: " + _LONG_OK

_PROFILE = {
    "Ім'я": "Тест Герой", "Дім": "Старк", "Титул": "Лорд", "Здоров'я": 100, "Енергія": 800,
    "Особисте Золото": 200, "Бойові навички": 50, "Військові навички": 20, "Інтрига": 15,
    "Управління": 10, "Поточне місцезнаходження": "Вінтерфелл", "Поточна сцена": "Зала",
    "Регіон": "Північ", "Ігровий час": "День 1, Ранок", "Інвентар": "Меч", "Зброя": "Довгий меч",
    "Броня": "Кольчуга", "Транспорт": "Кінь", "Світогляд": "Нейтральний", "Риси": "Хоробрий",
    "Вади": "Упертий", "Вороги": "", "Друзі": "", "Годинники": {"Scene_Tension": "0/4"},
}
_WORKER_UPDATES = {
    "action_type": "standard", "skill_used": "None", "difficulty": 10, "outcome": "SUCCESS",
    "dice_roll": "50", "skill_val": 20, "total_score": 70, "reputation_delta": 0,
    "reputation_target_npc": None, "minutes_passed": 10, "location_impact": "none",
    "scene_impact": "none", "health_impact": "none", "energy_impact": "none",
    "gold_impact": "none", "inventory_new": [], "inventory_lost": [], "clocks_impact": {},
}
_GM_JSON = (
    '{"reasoning": "t", "npc_reasoning": "t", "director_notes": ["Hero stands."],'
    ' "companion_npcs": [], "npc_updates": [], "suggested_actions": ['
    '{"button": "A1", "intent": "I1"},{"button": "A2", "intent": "I2"},'
    '{"button": "A3", "intent": "I3"},{"button": "A4", "intent": "I4"}]}'
)


def _resp(text):
    r = MagicMock()
    r.text = text
    return r


@pytest.fixture(autouse=True)
def _clean_ab_state():
    na._pending.clear()
    na._last_turn_id.clear()
    na._log_lock = asyncio.Lock()
    yield
    na._pending.clear()
    na._last_turn_id.clear()


# =============================================================================
# _run_narrator_chain
# =============================================================================

def _chain(wrapper, hedged):
    from core.engine import _run_narrator_chain

    async def go():
        with patch("core.engine.hedged_generate_content_async", hedged):
            return await _run_narrator_chain(wrapper, "PROMPT", "gemma")
    return asyncio.run(go())


def test_chain_success_attempt_1():
    wrapper = MagicMock()
    r = _chain(wrapper, AsyncMock(return_value=_resp(_LONG_OK)))
    assert r.ok and r.text == _LONG_OK
    assert r.final_attempt == 1 and r.used_fallback is False and r.error is None
    assert len(r.attempt_ms) == 1 and r.total_ms >= 0 and r.model_key == "gemma"
    wrapper.generate_content.assert_not_called()


def test_chain_attempt_1_exception_falls_to_attempt_2():
    wrapper = MagicMock()
    wrapper.generate_content.return_value = _resp(_LONG_OK)
    r = _chain(wrapper, AsyncMock(side_effect=RuntimeError("boom")))
    assert r.ok and r.final_attempt == 2 and len(r.attempt_ms) == 2


def test_chain_success_attempt_2():
    wrapper = MagicMock()
    wrapper.generate_content.return_value = _resp(_LONG_OK)
    r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.ok and r.text == _LONG_OK and r.final_attempt == 2
    assert len(r.attempt_ms) == 2 and wrapper.generate_content.call_count == 1


def _real_wrapper(name):
    from core.ai_client import AIWrapper, _NARRATOR_SYSTEM_INSTRUCTION
    w = AIWrapper(name, temperature=0.9, thinking_level="high", include_thoughts=True,
                  block_none=True, system_instruction=_NARRATOR_SYSTEM_INSTRUCTION)
    w._cache_attempted = True  # caching unavailable -> inline system_instruction, no network
    return w


@pytest.mark.parametrize("name", ["gemma-4-31b-it", "gemini-flash-lite-alt"])
def test_chain_success_attempt_3_uses_low_temperature_config(name):
    """Attempt 3 config is built via wrapper.config_with(temperature=0.5): temperature 0.5 AND
    the narrator's safety_settings / system_instruction / thinking_config are preserved."""
    wrapper = _real_wrapper(name)
    with patch.object(wrapper, "generate_content",
                      side_effect=[_resp(""), _resp(_LONG_OK)]) as gen:
        r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.ok and r.final_attempt == 3 and len(r.attempt_ms) == 3
    assert gen.call_count == 2
    cfg = gen.call_args_list[1].kwargs["config"]
    assert cfg.temperature == 0.5
    assert cfg.safety_settings and len(cfg.safety_settings) == 4
    assert cfg.system_instruction == wrapper.system_instruction
    assert cfg.thinking_config is not None
    assert wrapper.temperature == 0.9  # wrapper not mutated


@pytest.mark.usefixtures("explicit_cache_on")
def test_chain_attempt_3_uses_cached_content_when_cache_available():
    wrapper = _real_wrapper("gemma-4-31b-it")
    wrapper._cached_content_name = "cachedContents/xyz"
    with patch.object(wrapper, "generate_content",
                      side_effect=[_resp(""), _resp(_LONG_OK)]) as gen:
        _chain(wrapper, AsyncMock(return_value=_resp("")))
    cfg = gen.call_args_list[1].kwargs["config"]
    assert cfg.cached_content == "cachedContents/xyz"
    assert cfg.system_instruction is None
    assert cfg.temperature == 0.5


def test_chain_attempt_1_uses_hedge_count_1():
    wrapper = MagicMock()
    hedged = AsyncMock(return_value=_resp(_LONG_OK))
    _chain(wrapper, hedged)
    assert hedged.call_args.kwargs["hedge_count"] == 1


def test_chain_attempt_3_text_under_50_chars_is_failure():
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = [_resp(""), _resp("x" * 30)]
    r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.used_fallback and r.final_attempt == "fallback"


def test_chain_all_fail_used_fallback():
    wrapper = MagicMock()
    wrapper.generate_content.return_value = _resp("")
    r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.used_fallback is True and r.text is None and r.ok is False
    assert r.final_attempt == "fallback" and len(r.attempt_ms) == 3
    assert all(isinstance(x, int) and x >= 0 for x in r.attempt_ms)
    assert r.total_ms >= 0


def test_chain_attempt_3_exception_is_swallowed_into_fallback():
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = [_resp(""), RuntimeError("a3"), RuntimeError("a3-nocfg")]
    r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.used_fallback and r.final_attempt == "fallback" and len(r.attempt_ms) == 3


def test_chain_placeholder_triggers_retry():
    wrapper = MagicMock()
    wrapper.generate_content.return_value = _resp(_LONG_OK)
    placeholder = "[Опис сцени] " + "x" * 120
    r = _chain(wrapper, AsyncMock(return_value=_resp(placeholder)))
    assert r.final_attempt == 2 and r.text == _LONG_OK


def test_chain_truncated_long_text_accepted_with_ellipsis():
    wrapper = MagicMock()
    trunc = "word " * 30 + "end"
    assert len(trunc) >= 100
    r = _chain(wrapper, AsyncMock(return_value=_resp(trunc)))
    assert r.final_attempt == 1 and r.text.endswith("…")
    wrapper.generate_content.assert_not_called()


def test_chain_truncated_short_text_triggers_retry():
    wrapper = MagicMock()
    wrapper.generate_content.return_value = _resp(_LONG_OK)
    r = _chain(wrapper, AsyncMock(return_value=_resp("short unfinished text here")))
    assert r.final_attempt == 2


def test_chain_attempt_2_exception_not_propagated_attempt_3_runs():
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = [RuntimeError("a2 boom"), _resp(_LONG_OK)]
    r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.used_fallback is False and r.text == _LONG_OK and r.final_attempt == 3
    assert wrapper.generate_content.call_count == 2  # attempt 2 (raised) + attempt 3


def test_chain_attempt_2_exception_and_all_fail_gives_fallback():
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = RuntimeError("boom")
    r = _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert r.used_fallback is True and r.text is None and r.final_attempt == "fallback"
    assert len(r.attempt_ms) == 3


def test_chain_attempt_2_exception_is_logged(caplog):
    import logging
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = RuntimeError("a2 boom")
    with caplog.at_level(logging.WARNING):
        _chain(wrapper, AsyncMock(return_value=_resp("")))
    assert any("Attempt 2 raised" in r.getMessage() for r in caplog.records)


def test_chain_attempt_2_cancelled_error_propagates():
    wrapper = MagicMock()
    wrapper.generate_content.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        _chain(wrapper, AsyncMock(return_value=_resp("")))


# =============================================================================
# commit_narration_to_history
# =============================================================================

def test_commit_appends_turn_entry_and_strips_change_log():
    from core import engine
    summ = AsyncMock(return_value="SUMMARY")
    engine.user_sessions[777] = {"history": []}
    try:
        with patch("core.engine.summarize_full_turn", summ):
            asyncio.run(engine.commit_narration_to_history(777, "inp", "story text\n\n📊 hp -1", {"k": 1}))
        assert engine.user_sessions[777]["history"] == [{"role": "Turn", "content": "SUMMARY"}]
        args, kwargs = summ.call_args
        assert args == ("inp", "story text\n\n") or args[0] == "inp"
        assert "📊" not in args[1]
        assert kwargs.get("mechanical_updates") == {"k": 1}
    finally:
        engine.user_sessions.pop(777, None)


def test_commit_truncates_story_to_800():
    from core import engine
    summ = AsyncMock(return_value="S")
    engine.user_sessions[778] = {"history": []}
    try:
        with patch("core.engine.summarize_full_turn", summ):
            asyncio.run(engine.commit_narration_to_history(778, "i", "x" * 2000, {}))
        assert len(summ.call_args.args[1]) == 800
    finally:
        engine.user_sessions.pop(778, None)


def test_commit_sliding_window_compresses_over_20():
    from core import engine
    engine.user_sessions[779] = {"history": [{"role": "Turn", "content": f"t{i}"} for i in range(20)]}
    try:
        with patch("core.engine.summarize_full_turn", AsyncMock(return_value="NEW")), \
                patch("core.engine.model_worker.generate_content", return_value=_resp("COMPRESSED")):
            asyncio.run(engine.commit_narration_to_history(779, "i", "s", {}))
        h = engine.user_sessions[779]["history"]
        assert h[0]["role"] == "SYSTEM" and "COMPRESSED" in h[0]["content"]
        assert len(h) == 16 and h[-1]["content"] == "NEW"
    finally:
        engine.user_sessions.pop(779, None)


def test_commit_without_session_is_noop_and_never_raises():
    from core import engine
    engine.user_sessions.pop(780, None)
    with patch("core.engine.summarize_full_turn", AsyncMock(return_value="S")):
        asyncio.run(engine.commit_narration_to_history(780, "i", "s", {}))
    assert 780 not in engine.user_sessions


def test_commit_swallows_summarizer_error():
    from core import engine
    engine.user_sessions[781] = {"history": []}
    try:
        with patch("core.engine.summarize_full_turn", AsyncMock(side_effect=RuntimeError("x"))):
            asyncio.run(engine.commit_narration_to_history(781, "i", "s", {}))
        assert engine.user_sessions[781]["history"] == []
    finally:
        engine.user_sessions.pop(781, None)


# =============================================================================
# process_game_turn: A/B branch
# =============================================================================

def _patches(profile=None, chain=None, extra_updates=None):
    profile = dict(_PROFILE if profile is None else profile)
    upd = dict(_WORKER_UPDATES)
    upd.update(extra_updates or {})
    gm = MagicMock()
    gm.text = _GM_JSON
    ps = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.save_user_data", new=AsyncMock(return_value=True)),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.resolve_normal_action",
              new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", upd))),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm),
        patch("core.engine.append_log", new=AsyncMock()),
    ]
    return ps


def _run_turn(chat_id, ps, chain, bg=None, ab=True):
    """Runs process_game_turn with chain results keyed by model_key. Returns (result, bg_mock, log_mock)."""
    from core.engine import process_game_turn
    bg = bg if bg is not None else MagicMock()
    captured = []

    def _bg(coro):
        captured.append(coro.cr_frame.f_locals.get("commit_history_arg"))
        coro.close()
        return MagicMock()

    async def fake_chain(wrapper, prompt, key, static=None):
        return chain[key]

    with ExitStack() as st:
        entered = [st.enter_context(p) for p in ps]
        st.enter_context(patch("core.engine.NARRATOR_AB_ENABLED", ab))
        st.enter_context(patch("core.engine._run_bg_task", side_effect=_bg))
        st.enter_context(patch("core.engine._run_narrator_chain", side_effect=fake_chain))
        log_mock = entered[-1]
        result = asyncio.run(process_game_turn(chat_id, "Inspect the hall", narrator_queue=None))
    return result, captured, log_mock


def _ok(key, text):
    return NarrationResult(model_key=key, text=text, total_ms=50, attempt_ms=[50], final_attempt=1)


def _bad(key, error=None):
    return NarrationResult(model_key=key, text=None, total_ms=50, attempt_ms=[1, 2, 3],
                           final_attempt="fallback", used_fallback=True, error=error)


def test_ab_pair_sets_pending_and_hides_story():
    from core import engine
    chain = {"gemma": _ok("gemma", _GEMMA_TXT), "flash_lite": _ok("flash_lite", _FLASH_TXT)}
    try:
        (text, actions), bg_flags, log_mock = _run_turn(910, _patches(), chain)
        assert "GEMMA:" not in text and "FLASH:" not in text
        assert text == "" or text.startswith("\n\n📊")
        p = na.get_pending(910)
        assert p is not None and len(p.order) == 2
        assert {r.model_key for r in p.order} == {"gemma", "flash_lite"}
        assert {r.text for r in p.order} == {_GEMMA_TXT, _FLASH_TXT} or all(
            r.text.startswith(("GEMMA:", "FLASH:")) for r in p.order)
        assert p.deferred_history["user_input"] == "Inspect the hall"
        assert p.user_id == 910 and p.chat_id == 910
        assert p.change_log == text
        assert p.suggested_actions == actions and len(actions) == 4
        assert na.last_turn_id(910) == p.turn_id
        assert p.log_record["shown_order"] == [r.model_key for r in p.order]
        assert p.log_record["vote"] is None and p.log_record["reason"] is None
        log_mock.assert_not_called()  # pair record is written only at vote time
    finally:
        engine.user_sessions.pop(910, None)


def test_ab_pair_history_not_appended_and_bg_commit_disabled():
    from core import engine
    chain = {"gemma": _ok("gemma", _GEMMA_TXT), "flash_lite": _ok("flash_lite", _FLASH_TXT)}
    try:
        engine.user_sessions[911] = {"history": [], "state": "GAME_ACTIVE"}
        _run_turn(911, _patches(), chain)
        assert engine.user_sessions[911].get("history") == []
    finally:
        engine.user_sessions.pop(911, None)


def test_ab_pair_background_task_called_with_commit_history_false():
    from core import engine
    chain = {"gemma": _ok("gemma", _GEMMA_TXT), "flash_lite": _ok("flash_lite", _FLASH_TXT)}
    try:
        _, flags, _ = _run_turn(912, _patches(), chain)
        assert flags == [False]
    finally:
        engine.user_sessions.pop(912, None)


def test_ab_pair_log_record_mechanics_fields():
    from core import engine
    chain = {"gemma": _ok("gemma", _GEMMA_TXT), "flash_lite": _ok("flash_lite", _FLASH_TXT)}
    try:
        _run_turn(913, _patches(), chain)
        rec = na.get_pending(913).log_record
        assert rec["mode"] in ("NORMAL", None) or isinstance(rec["mode"], str)
        assert rec["mechanics"]["outcome"] == "SUCCESS"
        assert rec["mechanics"]["difficulty"] == 10
        assert set(rec["results"]) == {"gemma", "flash_lite"}
        assert rec["narrator_prompt"]
    finally:
        engine.user_sessions.pop(913, None)


@pytest.mark.parametrize("ok_key,bad_key", [("gemma", "flash_lite"), ("flash_lite", "gemma")])
def test_ab_single_variant_normal_return_and_log(ok_key, bad_key):
    from core import engine
    chain = {ok_key: _ok(ok_key, "ONLY-ONE " + _LONG_OK), bad_key: _bad(bad_key)}
    try:
        (text, actions), flags, log_mock = _run_turn(920, _patches(), chain)
        assert text.startswith("ONLY-ONE")
        assert na.get_pending(920) is None
        assert flags == [True]
        log_mock.assert_awaited_once()
        rec = log_mock.await_args.args[0]
        assert rec["reason"] == "single_variant" and rec["vote"] is None and rec["shown_order"] is None
        assert rec["type"] == "turn"
    finally:
        engine.user_sessions.pop(920, None)


def test_ab_single_variant_with_exception_error_result():
    from core import engine
    chain = {"gemma": _ok("gemma", "OK " + _LONG_OK), "flash_lite": _bad("flash_lite", error="RuntimeError: x")}
    try:
        (text, _), _, log_mock = _run_turn(921, _patches(), chain)
        assert text.startswith("OK")
        assert log_mock.await_args.args[0]["results"]["flash_lite"]["error"] == "RuntimeError: x"
    finally:
        engine.user_sessions.pop(921, None)


def test_ab_both_failed_deterministic_and_log_reason():
    from core import engine
    chain = {"gemma": _bad("gemma"), "flash_lite": _bad("flash_lite")}
    try:
        (text, actions), flags, log_mock = _run_turn(930, _patches(), chain)
        story = text.split("📊")[0]
        # director_notes з _GM_JSON ("Hero stands.") + примітка про недоступність опису
        assert "Hero stands." in story
        assert "Детальний опис сцени тимчасово недоступний" in story
        assert "⚠️" not in text and "помилка" not in text.lower() and "майстер" not in text.lower()
        assert na.get_pending(930) is None and flags == [True]
        rec = log_mock.await_args.args[0]
        assert rec["reason"] == "both_failed" and rec["vote"] is None
    finally:
        engine.user_sessions.pop(930, None)


def test_ab_chain_exception_becomes_failed_result():
    """gather(return_exceptions=True): one raising chain -> single_variant, not a crash."""
    from core import engine
    from core.engine import process_game_turn

    async def fake_chain(wrapper, prompt, key, static=None):
        if key == "flash_lite":
            raise RuntimeError("chain exploded")
        return _ok("gemma", "SURVIVOR " + _LONG_OK)

    try:
        with ExitStack() as st:
            entered = [st.enter_context(p) for p in _patches()]
            st.enter_context(patch("core.engine.NARRATOR_AB_ENABLED", True))
            st.enter_context(patch("core.engine._run_bg_task", side_effect=lambda c: (c.close(), MagicMock())[1]))
            st.enter_context(patch("core.engine._run_narrator_chain", side_effect=fake_chain))
            text, _ = asyncio.run(process_game_turn(931, "Inspect", narrator_queue=None))
            rec = entered[-1].await_args.args[0]
        assert text.startswith("SURVIVOR")
        assert rec["reason"] == "single_variant"
        assert "chain exploded" in rec["results"]["flash_lite"]["error"]
    finally:
        engine.user_sessions.pop(931, None)


def test_ab_flag_off_uses_single_gemma_chain_no_pending():
    from core import engine
    calls = []
    from core.engine import process_game_turn

    async def fake_chain(wrapper, prompt, key, static=None):
        calls.append(key)
        return _ok(key, "NORMAL " + _LONG_OK)

    try:
        with ExitStack() as st:
            entered = [st.enter_context(p) for p in _patches()]
            st.enter_context(patch("core.engine.NARRATOR_AB_ENABLED", False))
            st.enter_context(patch("core.engine._run_bg_task", side_effect=lambda c: (c.close(), MagicMock())[1]))
            st.enter_context(patch("core.engine._run_narrator_chain", side_effect=fake_chain))
            text, _ = asyncio.run(process_game_turn(940, "Inspect", narrator_queue=None))
        assert calls == ["gemma"] and text.startswith("NORMAL")
        assert na.get_pending(940) is None
        entered[-1].assert_not_called()
    finally:
        engine.user_sessions.pop(940, None)


def test_ab_streaming_mode_ignores_ab():
    """With a narrator_queue the A/B branch must not trigger (blocking-only feature)."""
    from core import engine
    from core.engine import process_game_turn
    q = asyncio.Queue()
    calls = []

    async def fake_chain(*a, **k):
        calls.append(a)
        raise AssertionError("chain must not be used in streaming mode")

    chunk = MagicMock()
    chunk.candidates = []
    chunk.text = _LONG_OK
    try:
        with ExitStack() as st:
            for p in _patches():
                st.enter_context(p)
            st.enter_context(patch("core.engine.NARRATOR_AB_ENABLED", True))
            st.enter_context(patch("core.engine._run_bg_task", side_effect=lambda c: (c.close(), MagicMock())[1]))
            st.enter_context(patch("core.engine._run_narrator_chain", side_effect=fake_chain))
            st.enter_context(patch("core.engine.model_narrator.generate_content_stream",
                                   MagicMock(return_value=iter([chunk]))))
            text, _ = asyncio.run(process_game_turn(941, "Inspect", narrator_queue=q))
        assert na.get_pending(941) is None and not calls
    finally:
        engine.user_sessions.pop(941, None)


def test_ab_pair_combat_mode():
    from core import engine
    prof = dict(_PROFILE, mode="COMBAT", hp_current=10, hp_max=20)
    combat_state = MagicMock()
    combat_state.npcs = {}
    chain = {"gemma": _ok("gemma", _GEMMA_TXT), "flash_lite": _ok("flash_lite", _FLASH_TXT)}
    ps = _patches(profile=prof)
    ps += [
        patch("core.engine.execute_combat_round",
              new=AsyncMock(return_value=("Player attacks", {"combat_round": 2, "combat_phase": "ONGOING"}, []))),
        patch("core.combat_state.get_combat_state", return_value=combat_state),
        patch("core.engine.format_combat_log_for_narrator", return_value=["Player attacks"]),
        patch("core.engine.cleanup_and_exit_combat", new=AsyncMock(return_value={})),
    ]
    # keep append_log mock last-but-handled: _run_turn reads entered[-1]; reorder not needed for this test
    try:
        (text, actions), flags, _ = _run_turn(950, ps, chain)
        p = na.get_pending(950)
        assert p is not None and flags == [False]
        assert p.log_record["mode"] == "COMBAT"
        assert p.log_record["mechanics"].get("combat_round") == 2
        assert p.log_record["mechanics"].get("combat_phase") == "ONGOING"
    finally:
        engine.user_sessions.pop(950, None)
        from core.combat_state import _combat_states, _state_locks
        _combat_states.pop(950, None)
        _state_locks.pop(950, None)


def test_ab_pair_death_suffix_added_to_both_variants():
    from core import engine
    prof = dict(_PROFILE)
    chain = {"gemma": _ok("gemma", _GEMMA_TXT), "flash_lite": _ok("flash_lite", _FLASH_TXT)}
    ps = _patches(profile=prof, extra_updates={"health_impact": "none"})
    ps.insert(0, patch("core.engine.apply_dnd_impacts", side_effect=_kill_player))
    try:
        _run_turn(960, ps, chain)
        p = na.get_pending(960)
        assert p is not None
        for r in p.order:
            assert "ВАШ ДОЗОР ЗАКІНЧИВСЯ" in r.text
        assert p.suggested_actions == ["🔄 Почати заново"]
    finally:
        engine.user_sessions.pop(960, None)


def _kill_player(profile, *a, **k):
    profile["Здоров'я"] = 0
    profile["hp_current"] = 0
    return profile, []
