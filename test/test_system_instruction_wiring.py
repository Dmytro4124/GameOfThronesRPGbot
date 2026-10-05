"""Stage 4 wiring: engine paths pass the STATIC prompt part as system_instruction and the DYNAMIC part
as contents, with the proper response schema; every Narrator path (blocking chain attempts 1/2/3,
streaming main/retry/degraded, A/B alt) receives the IDENTICAL system_instruction.

No network, no Sheets: Gemini wrappers' generate_content(_stream) are mocked.
"""
import asyncio
import hashlib
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.narrator_ab as na
from core import prompts as P
from core.ai_client import (
    AIWrapper, build_strict_config, model_gm_logic, model_narrator, model_narrator_alt, model_worker,
)

_LONG_OK = ("The hero steps carefully across the stone floor of the great hall. "
            "Torches cast trembling shadows on the ancient tapestries.")


def _resp(text):
    r = MagicMock()
    r.text = text
    return r


def _cfg_of(call):
    """config of a generate_content call (kwarg 'config' or 3rd positional as in hedged helper)."""
    if "config" in call.kwargs:
        return call.kwargs["config"]
    return call.args[2]


def _prompt_of(call):
    return call.args[0] if call.args else call.kwargs["prompt"]


@pytest.fixture(autouse=True)
def _clean_ab_state():
    na._pending.clear()
    na._last_turn_id.clear()
    na._log_lock = asyncio.Lock()
    yield
    na._pending.clear()
    na._last_turn_id.clear()


# ===========================================================================
# Worker NORMAL
# ===========================================================================

def _run_worker(user_input="Огляджу залу ZXQ", debug_trace=None):
    from core.dnd_engine import resolve_normal_action
    from test.test_dnd_engine import _dnd_profile, _minimal_worker_data, _make_llm_response
    data = _minimal_worker_data()
    gen = MagicMock(return_value=_make_llm_response(data))
    with patch("core.dnd_engine.model_worker.generate_content", gen), \
            patch("core.dnd_engine.clean_and_parse_json", return_value=data):
        asyncio.run(resolve_normal_action(user_input, _dnd_profile(), debug_trace=debug_trace))
    return gen


def test_worker_gets_static_as_system_instruction_and_dynamic_as_contents():
    gen = _run_worker()
    assert gen.call_count >= 1
    call = gen.call_args_list[0]
    cfg = call.kwargs["config"]
    prompt = _prompt_of(call)
    assert cfg.system_instruction == P.WORKER_NORMAL_SYSTEM == P.SYSTEM_PROMPTS["worker_normal"]
    assert cfg.cached_content is None
    assert "Огляджу залу ZXQ" in prompt
    assert "[GATE" not in prompt
    assert P.WORKER_NORMAL_SYSTEM not in prompt
    assert prompt != P.WORKER_NORMAL_SYSTEM


def test_worker_config_has_worker_schema_and_json_mime():
    cfg = _cfg_of(_run_worker().call_args_list[0])
    expected = build_strict_config(model_worker, schema=P.WORKER_NORMAL_SCHEMA).response_schema
    assert cfg.response_schema == expected
    assert cfg.response_mime_type == "application/json"


def test_worker_dynamic_contents_equals_parts_dynamic():
    """contents sent to the model is exactly the dynamic half of build_normal_resolve_parts."""
    from core.dnd_engine import resolve_normal_action
    from test.test_dnd_engine import _dnd_profile, _minimal_worker_data, _make_llm_response
    data = _minimal_worker_data()
    parts = MagicMock(return_value=("STATIC_X", "DYNAMIC_Y"))
    gen = MagicMock(return_value=_make_llm_response(data))
    with patch("core.dnd_engine.build_normal_resolve_parts", parts), \
            patch("core.dnd_engine.model_worker.generate_content", gen), \
            patch("core.dnd_engine.clean_and_parse_json", return_value=data):
        asyncio.run(resolve_normal_action("x", _dnd_profile()))
    for call in gen.call_args_list:
        assert _prompt_of(call) == "DYNAMIC_Y"
        assert _cfg_of(call).system_instruction == "STATIC_X"


def test_worker_retry_uses_same_static():
    from core.dnd_engine import resolve_normal_action
    from test.test_dnd_engine import _dnd_profile
    empty = MagicMock()
    empty.text = None
    gen = MagicMock(return_value=empty)
    with patch("core.dnd_engine.model_worker.generate_content", gen), \
            patch("core.dnd_engine.clean_and_parse_json", return_value=None):
        asyncio.run(resolve_normal_action("x", _dnd_profile()))
    assert gen.call_count == 2
    assert {_cfg_of(c).system_instruction for c in gen.call_args_list} == {P.WORKER_NORMAL_SYSTEM}


def test_worker_debug_trace_has_static_tag_not_full_static():
    trace = {"worker": {"prompt": None, "raw": None, "parsed": None, "thoughts": []}}
    _run_worker(debug_trace=trace)
    p = trace["worker"]["prompt"]
    md5 = hashlib.md5(P.WORKER_NORMAL_SYSTEM.encode("utf-8")).hexdigest()[:8]
    assert p.startswith(f"[SYSTEM_INSTRUCTION static len={len(P.WORKER_NORMAL_SYSTEM)} md5={md5}]")
    assert "Огляджу залу ZXQ" in p
    assert P.WORKER_NORMAL_SYSTEM not in p


# ===========================================================================
# Censor
# ===========================================================================

def _run_censor(debug_trace=None):
    from core.mechanics import validate_action
    gen = MagicMock(return_value=_resp(json.dumps({"is_valid": True, "refusal_reason": ""})))
    profile = {"Ім'я": "Тестовий ZXQ", "Здоров'я": 100, "Особисте Золото": 77, "Інвентар": []}
    with patch("core.mechanics.model_worker.generate_content", gen):
        asyncio.run(validate_action("Я іду до воріт ZXQ", profile, debug_trace=debug_trace))
    return gen


def test_censor_gets_static_as_system_instruction_and_dynamic_as_contents():
    gen = _run_censor()
    call = gen.call_args_list[0]
    cfg, prompt = _cfg_of(call), _prompt_of(call)
    assert cfg.system_instruction == P.CENSOR_SYSTEM == P.SYSTEM_PROMPTS["censor"]
    assert cfg.cached_content is None
    assert "Я іду до воріт ZXQ" in prompt and "Тестовий ZXQ" in prompt and "77" in prompt
    assert P.CENSOR_SYSTEM not in prompt
    assert len(prompt) < len(P.CENSOR_SYSTEM)


def test_censor_config_has_censor_schema():
    cfg = _cfg_of(_run_censor().call_args_list[0])
    expected = build_strict_config(model_worker, schema=P.CENSOR_SCHEMA).response_schema
    assert cfg.response_schema == expected
    assert cfg.response_mime_type == "application/json"


def test_censor_debug_trace_tag():
    trace = {"censor": {"prompt": None, "raw": None}}
    _run_censor(debug_trace=trace)
    p = trace["censor"]["prompt"]
    md5 = hashlib.md5(P.CENSOR_SYSTEM.encode("utf-8")).hexdigest()[:8]
    assert p.startswith(f"[SYSTEM_INSTRUCTION static len={len(P.CENSOR_SYSTEM)} md5={md5}]")
    assert P.CENSOR_SYSTEM not in p


# ===========================================================================
# process_game_turn harness: GM_Logic + Narrator
# ===========================================================================

_PROFILE = {
    "Ім'я": "Тест Герой", "Дім": "Старк", "Титул": "Лорд", "Здоров'я": 100, "Енергія": 800,
    "Особисте Золото": 200, "Бойові навички": 50, "Військові навички": 20, "Інтрига": 15,
    "Управління": 10, "Поточне місцезнаходження": "Вінтерфелл", "Поточна сцена": "Зала",
    "Регіон": "Північ", "Ігровий час": "День 1, Ранок", "Інвентар": "Меч", "Зброя": "Довгий меч",
    "Броня": "Кольчуга", "Транспорт": "Кінь", "Світогляд": "Нейтральний", "Риси": "Хоробрий",
    "Вади": "Упертий", "Вороги": "", "Друзі": "", "Годинники": {"Scene_Tension": "0/4"},
}
_UPDATES = {
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
_USER_INPUT = "Inspect the hall ZXQ"
_EXPECTED_NARR_SI = model_narrator.system_instruction + "\n\n" + P.NARRATOR_SYSTEM


def _chunk(text):
    part = SimpleNamespace(thought=False, text=text)
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason=None)
    return SimpleNamespace(candidates=[cand], text=text)


def _turn(*, narrator_gen=None, alt_gen=None, stream=None, queue=False, ab=False, chat_id=4401):
    """Runs process_game_turn with all externals mocked.
    Returns SimpleNamespace(gm, narr, alt, stream, result)."""
    from core.engine import process_game_turn
    gm = MagicMock(return_value=_resp(_GM_JSON))
    narr = narrator_gen if narrator_gen is not None else MagicMock(return_value=_resp(_LONG_OK))
    alt = alt_gen if alt_gen is not None else MagicMock(return_value=_resp(_LONG_OK))
    strm = stream if stream is not None else MagicMock(side_effect=lambda *a, **k: iter([_chunk(_LONG_OK)]))

    async def go():
        q = asyncio.Queue() if queue else None
        return await process_game_turn(chat_id, _USER_INPUT, narrator_queue=q)

    with ExitStack() as st:
        for p in (
            patch("core.engine.get_user_data", new=AsyncMock(return_value=(dict(_PROFILE), 2))),
            patch("core.engine.save_user_data", new=AsyncMock(return_value=True)),
            patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
            patch("core.engine.resolve_normal_action",
                  new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", dict(_UPDATES)))),
            patch("core.engine.get_location_npcs", return_value=("", [], {})),
            patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
            patch("core.engine.get_dead_npc_names", return_value=set()),
            patch("core.engine.append_log", new=AsyncMock()),
            patch("core.engine.NARRATOR_AB_ENABLED", ab),
            patch("core.engine._run_bg_task", side_effect=lambda c: (c.close(), MagicMock())[1]),
            patch("core.engine.model_gm_logic.generate_content", gm),
            patch("core.engine.model_narrator.generate_content", narr),
            patch("core.engine.model_narrator_alt.generate_content", alt),
            patch("core.engine.model_narrator.generate_content_stream", strm),
        ):
            st.enter_context(p)
        result = asyncio.run(go())
    return SimpleNamespace(gm=gm, narr=narr, alt=alt, stream=strm, result=result)


# ---------------------------------------------------------------------------
# GM_Logic
# ---------------------------------------------------------------------------

def test_gm_logic_gets_static_as_system_instruction_and_dynamic_as_contents():
    t = _turn()
    assert t.gm.call_count >= 1
    call = t.gm.call_args_list[0]
    cfg, prompt = _cfg_of(call), _prompt_of(call)
    assert cfg.system_instruction == P.GM_LOGIC_SYSTEM == P.SYSTEM_PROMPTS["gm_logic"]
    assert cfg.cached_content is None
    assert _USER_INPUT in prompt
    assert P.GM_LOGIC_SYSTEM not in prompt
    assert len(prompt) < len(P.GM_LOGIC_SYSTEM)


def test_gm_logic_config_has_gm_schema():
    cfg = _cfg_of(_turn().gm.call_args_list[0])
    expected = build_strict_config(model_gm_logic, schema=P.GM_LOGIC_SCHEMA).response_schema
    assert cfg.response_schema == expected
    assert cfg.response_mime_type == "application/json"


# ---------------------------------------------------------------------------
# Narrator: _build_narrator_prompt returns (static, dynamic)
# ---------------------------------------------------------------------------

def _nargs(**kw):
    a = dict(user_input="u", director_notes=["n"], npc_context_text="", player_name="Джон Сноу",
             player_house="Старк", current_scene="S", current_location="L", impact_narrative_hints="")
    a.update(kw)
    return a


def test_build_narrator_prompt_returns_tuple_normal():
    from core.engine import _build_narrator_prompt
    res = _build_narrator_prompt(**_nargs())
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] == P.NARRATOR_SYSTEM and res[1] and res[1] != res[0]


def test_build_narrator_prompt_combat_static_differs():
    from core.engine import _build_narrator_prompt
    static, dyn = _build_narrator_prompt(**_nargs(combat_log=["Удар ZXQ"]))
    assert static == P.NARRATOR_SYSTEM_COMBAT
    assert "Удар ZXQ" in dyn and "Удар ZXQ" not in static


# ---------------------------------------------------------------------------
# Narrator: blocking chain, attempts 1 / 2 / 3 (via process_game_turn)
# ---------------------------------------------------------------------------

def test_narrator_blocking_attempt1_gets_static_si_and_dynamic_contents():
    t = _turn()
    assert t.narr.call_count == 1
    call = t.narr.call_args_list[0]
    assert _cfg_of(call).system_instruction == _EXPECTED_NARR_SI
    assert _cfg_of(call).cached_content is None
    assert _USER_INPUT in _prompt_of(call)
    assert P.NARRATOR_SYSTEM not in _prompt_of(call)


def test_narrator_blocking_all_three_attempts_identical_si():
    narr = MagicMock(side_effect=[_resp(""), _resp(""), _resp(_LONG_OK)])
    t = _turn(narrator_gen=narr)
    assert narr.call_count == 3
    cfgs = [_cfg_of(c) for c in narr.call_args_list]
    assert {c.system_instruction for c in cfgs} == {_EXPECTED_NARR_SI}
    assert all(c.cached_content is None for c in cfgs)
    assert cfgs[0].temperature == cfgs[1].temperature != 0.5
    assert cfgs[2].temperature == 0.5
    prompts = {_prompt_of(c) for c in narr.call_args_list}
    assert len(prompts) == 1  # identical dynamic contents for all attempts
    assert t.result[0]


def test_narrator_blocking_attempt3_cfg_fallback_still_same_si():
    """If the temperature=0.5 config raises, the base config (same SI) is used."""
    narr = MagicMock(side_effect=[_resp(""), _resp(""), RuntimeError("t05 boom"), _resp(_LONG_OK)])
    _turn(narrator_gen=narr)
    cfgs = [_cfg_of(c) for c in narr.call_args_list]
    assert len(cfgs) == 4
    assert {c.system_instruction for c in cfgs} == {_EXPECTED_NARR_SI}


# ---------------------------------------------------------------------------
# _run_narrator_chain directly
# ---------------------------------------------------------------------------

def _chain(wrapper, static):
    from core.engine import _run_narrator_chain

    async def go():
        with patch("core.engine.hedged_generate_content_async",
                   AsyncMock(return_value=_resp(""))) as hedged:
            res = await _run_narrator_chain(wrapper, "DYN", "gemma", static)
        return res, hedged
    return asyncio.run(go())


def test_chain_hedged_attempt1_receives_static_cfg():
    w = AIWrapper("chain-model", temperature=0.9, include_thoughts=True, block_none=True,
                  system_instruction="P" * 300)
    with patch.object(w, "generate_content", side_effect=[_resp(""), _resp(_LONG_OK)]):
        res, hedged = _chain(w, "STATIC_ZXQ")
    cfg = hedged.call_args.kwargs["config"]
    assert cfg.system_instruction == "P" * 300 + "\n\n" + "STATIC_ZXQ"
    assert hedged.call_args.args[1] == "DYN"
    assert res.ok


def test_chain_without_static_keeps_wrapper_config():
    w = AIWrapper("chain-model", temperature=0.9, include_thoughts=True, block_none=True)
    with patch.object(w, "generate_content", side_effect=[_resp(_LONG_OK)]) as gen:
        _, hedged = _chain(w, None)
    assert hedged.call_args.kwargs["config"] is None
    assert gen.call_args.kwargs["config"] is None


def test_chain_attempts_2_and_3_same_static_si():
    w = AIWrapper("chain-model", temperature=0.9, include_thoughts=True, block_none=True,
                  system_instruction="P" * 300)
    with patch.object(w, "generate_content", side_effect=[_resp(""), _resp(_LONG_OK)]) as gen:
        res, hedged = _chain(w, "STATIC_ZXQ")
    cfg2 = gen.call_args_list[0].kwargs["config"]
    cfg3 = gen.call_args_list[1].kwargs["config"]
    cfg1 = hedged.call_args.kwargs["config"]
    assert cfg1.system_instruction == cfg2.system_instruction == cfg3.system_instruction
    assert cfg3.temperature == 0.5 and cfg2.temperature == 0.9


# ---------------------------------------------------------------------------
# Narrator: streaming main / retry / degraded
# ---------------------------------------------------------------------------

def test_streaming_main_path_si_and_contents():
    t = _turn(queue=True)
    assert t.stream.call_count == 1
    call = t.stream.call_args_list[0]
    assert _cfg_of(call).system_instruction == _EXPECTED_NARR_SI
    assert _cfg_of(call).cached_content is None
    assert _USER_INPUT in _prompt_of(call) and P.NARRATOR_SYSTEM not in _prompt_of(call)
    t.narr.assert_not_called()


def test_streaming_retry_identical_si():
    strm = MagicMock(side_effect=[iter([]), iter([_chunk(_LONG_OK)])])
    t = _turn(queue=True, stream=strm)
    assert strm.call_count == 2
    cfgs = [_cfg_of(c) for c in strm.call_args_list]
    assert {c.system_instruction for c in cfgs} == {_EXPECTED_NARR_SI}
    assert _prompt_of(strm.call_args_list[0]) == _prompt_of(strm.call_args_list[1])
    t.narr.assert_not_called()


def test_streaming_degraded_third_attempt_identical_si_temp_05():
    strm = MagicMock(side_effect=[iter([]), iter([])])
    narr = MagicMock(return_value=_resp(_LONG_OK))
    _turn(queue=True, stream=strm, narrator_gen=narr)
    assert strm.call_count == 2
    assert narr.call_count >= 1
    third = _cfg_of(narr.call_args_list[0])
    assert third.temperature == 0.5
    cfgs = [_cfg_of(c) for c in strm.call_args_list] + [third]
    assert {c.system_instruction for c in cfgs} == {_EXPECTED_NARR_SI}
    assert all(c.cached_content is None for c in cfgs)
    assert narr.call_args_list[0].kwargs.get("max_retries") == 2


# ---------------------------------------------------------------------------
# Narrator: A/B alt
# ---------------------------------------------------------------------------

def test_ab_main_and_alt_receive_identical_si_and_contents():
    narr = MagicMock(return_value=_resp("GEMMA: " + _LONG_OK))
    alt = MagicMock(return_value=_resp("FLASH: " + _LONG_OK))
    t = _turn(ab=True, narrator_gen=narr, alt_gen=alt, chat_id=4402)
    assert narr.call_count >= 1 and alt.call_count >= 1
    c_main, c_alt = _cfg_of(narr.call_args_list[0]), _cfg_of(alt.call_args_list[0])
    assert c_main.system_instruction == c_alt.system_instruction == _EXPECTED_NARR_SI
    assert c_main.cached_content is None and c_alt.cached_content is None
    assert _prompt_of(narr.call_args_list[0]) == _prompt_of(alt.call_args_list[0])
    assert model_narrator.system_instruction == model_narrator_alt.system_instruction


def test_ab_alt_chain_retries_keep_same_si():
    narr = MagicMock(return_value=_resp("GEMMA: " + _LONG_OK))
    alt = MagicMock(side_effect=[_resp(""), _resp(""), _resp("FLASH: " + _LONG_OK)])
    _turn(ab=True, narrator_gen=narr, alt_gen=alt, chat_id=4403)
    cfgs = [_cfg_of(c) for c in alt.call_args_list] + [_cfg_of(c) for c in narr.call_args_list]
    assert len(alt.call_args_list) == 3
    assert {c.system_instruction for c in cfgs} == {_EXPECTED_NARR_SI}
