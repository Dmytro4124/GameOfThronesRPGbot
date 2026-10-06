"""Engine: застосування рух компаньйонів (updates.companions_moving) у process_game_turn.

Сценарій Кейтлін: гравець у "Жіночі Покої" з Кейтлін і Сансою, Worker повертає scene_impact
"Покої Лорда" + companions_moving ["Кейтлін Старк"]. Без мережі: LLM/Sheets/ростер замокано.
Канонічна назва локації -- "Вінтерфел" (одна "л").
"""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.engine as eng

CID = 556001
LOC = "Вінтерфел"
OLD_SCENE = "Жіночі Покої"
NEW_SCENE = "Покої Лорда"
CAT = "Кейтлін Старк"
SANSA = "Санса Старк"

_PROFILE = {
    "Ім'я": "Тест Герой", "Дім": "Старк", "Титул": "Лорд", "Здоров'я": 100, "Енергія": 800,
    "Особисте Золото": 200, "Бойові навички": 50, "Військові навички": 20, "Інтрига": 15,
    "Управління": 10, "Поточне місцезнаходження": LOC, "Поточна сцена": OLD_SCENE,
    "Регіон": "Північ", "Ігровий час": "День 1, Ранок", "Інвентар": "Меч", "Зброя": "Довгий меч",
    "Броня": "Кольчуга", "Транспорт": "Кінь", "Світогляд": "Нейтральний", "Риси": "Хоробрий",
    "Вади": "Упертий", "Вороги": "", "Друзі": "", "Годинники": {"Scene_Tension": "0/4"},
}

_GM_JSON = (
    '{"reasoning": "t", "npc_reasoning": "t", "director_notes": ["Hero walks."],'
    ' "companion_npcs": [],'
    ' "npc_updates": [{"Name": "Кейтлін Старк", "Location": "Королівська Гавань",'
    ' "Scene": "Тронна Зала", "Memory_Anchor": "Пішла з героєм", "Status": "Active"}],'
    ' "suggested_actions": [{"button": "A", "intent": "a"}, {"button": "B", "intent": "b"},'
    ' {"button": "C", "intent": "c"}, {"button": "D", "intent": "d"}]}'
)


def _worker_updates(**over):
    d = {
        "action_type": "standard", "skill_used": "None", "difficulty": 5, "circumstance": "NORMAL",
        "outcome": "SUCCESS", "dice_roll": "15", "skill_val": 2, "total_score": 17,
        "reputation_delta": 0, "reputation_target_npc": None, "minutes_passed": 10,
        "location_impact": "none", "scene_impact": NEW_SCENE, "health_impact": "none",
        "energy_impact": "none", "gold_impact": "none", "inventory_new": [], "inventory_lost": [],
        "clocks_impact": {}, "companions_moving": [CAT],
    }
    d.update(over)
    return d


def _run_turn(worker_updates, *, profile_over=None, in_combat=False, move_result=None,
              start_roster=(CAT, SANSA), debug=False):
    """Повертає dict: move (AsyncMock), gm_kwargs, update_kwargs, session, ctx."""
    profile = dict(_PROFILE)
    profile.update(profile_over or {})
    if debug:
        profile["_debug_mode"] = True

    state = {"moved": False}
    gm_calls = []
    real_gm_parts = eng.build_gm_logic_parts
    bg = []

    def _gln(chat_id, loc, scene, current_region=None):
        if scene == OLD_SCENE:
            names = [n for n in start_roster if not (state["moved"] and n == CAT)]
            return ("\n".join(f"> **{n}**" for n in names), names, {n: 0 for n in names})
        return (f"> **{CAT}**", [CAT], {CAT: 0})

    async def _move(chat_id, names, location, scene):
        state["moved"] = True
        return list(names) if move_result is None else list(move_result)

    move_mock = AsyncMock(side_effect=_move)

    def _gm_spy(*a, **k):
        gm_calls.append(k)
        return real_gm_parts(*a, **k)

    update_mock = AsyncMock()
    narr = MagicMock(return_value=MagicMock(text="The hero steps into the lord's chambers. " * 5))
    gm_resp = MagicMock(text=_GM_JSON)

    async def _go():
        eng.user_sessions[CID] = {
            "state": "GAME_ACTIVE", "history": [],
            "npc_cache": {LOC: [{"name": CAT, "scene": OLD_SCENE, "card": "c",
                                 "reputation_score": 0, "region": "Північ"}]},
            "dead_npc_names": set(), "prev_legal_npc_names": list(start_roster),
        }
        with ExitStack() as st:
            st.enter_context(patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))))
            st.enter_context(patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))))
            st.enter_context(patch("core.engine.resolve_normal_action",
                                   new=AsyncMock(return_value=("MECHANICAL VERDICT: SUCCESS", worker_updates))))
            st.enter_context(patch("core.engine.get_location_npcs", side_effect=_gln))
            st.enter_context(patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")))
            st.enter_context(patch("core.engine.get_dead_npc_names", return_value=set()))
            st.enter_context(patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp))
            st.enter_context(patch("core.engine.model_narrator.generate_content", narr))
            st.enter_context(patch("core.engine._run_bg_task", side_effect=lambda c: bg.append(c) or MagicMock()))
            st.enter_context(patch("core.engine.move_npcs_with_player", move_mock))
            st.enter_context(patch("core.engine.build_gm_logic_parts", side_effect=_gm_spy))
            st.enter_context(patch("core.engine.update_npcs_in_db", update_mock))
            st.enter_context(patch("core.engine.commit_narration_to_history", new=AsyncMock()))
            if in_combat:
                st.enter_context(patch("core.engine._is_in_combat_for_engine", return_value=True))
            await eng.process_game_turn(chat_id=CID, user_input="Веду Кейтлін в Покої Лорда",
                                        narrator_queue=None)
            # виконуємо захоплену background_task, щоб побачити виклик update_npcs_in_db
            for c in bg:
                if asyncio.iscoroutine(c) and c.cr_code.co_name == "background_task":
                    await c
                elif asyncio.iscoroutine(c):
                    c.close()
            return dict(eng.user_sessions.get(CID, {}))

    try:
        session = asyncio.run(_go())
    finally:
        eng.user_sessions.pop(CID, None)

    return {"move": move_mock, "gm": gm_calls[-1] if gm_calls else None,
            "update": update_mock, "session": session}


@pytest.fixture(autouse=True)
def _clean():
    eng.user_sessions.pop(CID, None)
    yield
    eng.user_sessions.pop(CID, None)


# ─────────────────────────────────────────────────────────────────────────────
# Сценарій Кейтлін (happy path)
# ─────────────────────────────────────────────────────────────────────────────

def test_caitlyn_move_called_with_start_roster_names_and_new_place():
    r = _run_turn(_worker_updates())
    r["move"].assert_awaited_once_with(CID, [CAT], LOC, NEW_SCENE)


def test_caitlyn_gm_receives_moved_companions():
    r = _run_turn(_worker_updates())
    assert r["gm"]["moved_companions"] == [CAT]


def test_caitlyn_not_absent_and_not_departing_sansa_departing():
    r = _run_turn(_worker_updates())
    gm = r["gm"]
    assert CAT not in gm["absent_npcs"]
    assert CAT not in gm["departing_roster_text"]
    assert SANSA in gm["departing_roster_text"]


def test_caitlyn_gm_prompt_renders_moved_with_player_block():
    r = _run_turn(_worker_updates())
    gm = r["gm"]
    from core.prompts import build_gm_logic_parts as real
    # реальне відображення блоку з тими самими kwargs
    _, dyn = real(**gm)
    assert "<moved_with_player>\n" in dyn  # саме блок даних (у absent-блоці є лише згадка)
    assert CAT in dyn.split("<moved_with_player>\n")[1].split("</moved_with_player>")[0]
    if gm["absent_npcs"]:
        assert CAT not in dyn.split("<absent_npcs>")[1].split("</absent_npcs>")[0]


def test_caitlyn_locked_names_passed_to_update_npcs_in_db():
    r = _run_turn(_worker_updates())
    r["update"].assert_awaited_once()
    assert r["update"].await_args.kwargs["locked_names"] == [CAT]


def test_move_happens_before_roster_rebuild_arriving_contains_companion():
    r = _run_turn(_worker_updates())
    assert CAT in r["gm"]["arriving_roster_text"]


# ─────────────────────────────────────────────────────────────────────────────
# Варіанти, коли рух НЕ застосовується
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("outcome", ["FAILURE", "CRITICAL FAILURE"])
def test_failure_outcome_does_not_move(outcome):
    r = _run_turn(_worker_updates(outcome=outcome))
    r["move"].assert_not_awaited()
    assert not r["gm"]["moved_companions"]


@pytest.mark.parametrize("outcome", ["SUCCESS", "CRITICAL SUCCESS"])
def test_success_outcomes_move(outcome):
    r = _run_turn(_worker_updates(outcome=outcome))
    r["move"].assert_awaited_once()


def test_name_not_in_start_roster_is_rejected():
    r = _run_turn(_worker_updates(companions_moving=["Роб Старк"]))
    r["move"].assert_not_awaited()


def test_only_roster_names_forwarded_to_move():
    r = _run_turn(_worker_updates(companions_moving=[CAT, "Роб Старк"]))
    r["move"].assert_awaited_once_with(CID, [CAT], LOC, NEW_SCENE)


def test_duplicate_names_deduped_before_move():
    r = _run_turn(_worker_updates(companions_moving=[CAT, CAT]))
    r["move"].assert_awaited_once_with(CID, [CAT], LOC, NEW_SCENE)


def test_scene_unchanged_does_not_move():
    r = _run_turn(_worker_updates(scene_impact="none"))
    r["move"].assert_not_awaited()


def test_empty_companions_does_not_move():
    r = _run_turn(_worker_updates(companions_moving=[]))
    r["move"].assert_not_awaited()
    assert not r["gm"]["moved_companions"]


def test_missing_companions_key_does_not_move():
    w = _worker_updates()
    w.pop("companions_moving")
    r = _run_turn(w)
    r["move"].assert_not_awaited()


def test_non_list_companions_does_not_move_and_does_not_crash():
    r = _run_turn(_worker_updates(companions_moving="Кейтлін Старк"))
    r["move"].assert_not_awaited()


def test_combat_does_not_move():
    r = _run_turn(_worker_updates(), in_combat=True)
    r["move"].assert_not_awaited()


def test_travel_location_does_not_move():
    r = _run_turn(_worker_updates(location_impact="В дорозі", scene_impact="none"))
    r["move"].assert_not_awaited()


def test_move_returning_nothing_means_no_moved_companions_and_no_lock():
    r = _run_turn(_worker_updates(), move_result=[])
    assert not r["gm"]["moved_companions"]
    assert r["update"].await_args.kwargs["locked_names"] is None


def test_move_exception_is_swallowed_and_turn_continues():
    profile = dict(_PROFILE)

    async def _boom(*a, **k):
        raise RuntimeError("cache exploded")

    async def _go():
        eng.user_sessions[CID] = {"state": "GAME_ACTIVE", "history": [],
                                  "npc_cache": {LOC: [{"name": CAT, "scene": OLD_SCENE, "card": "c",
                                                       "reputation_score": 0, "region": "Північ"}]},
                                  "dead_npc_names": set()}
        with ExitStack() as st:
            st.enter_context(patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))))
            st.enter_context(patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))))
            st.enter_context(patch("core.engine.resolve_normal_action",
                                   new=AsyncMock(return_value=("V", _worker_updates()))))
            st.enter_context(patch("core.engine.get_location_npcs",
                                   return_value=("> **Кейтлін Старк**", [CAT], {CAT: 0})))
            st.enter_context(patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")))
            st.enter_context(patch("core.engine.get_dead_npc_names", return_value=set()))
            st.enter_context(patch("core.engine.model_gm_logic.generate_content",
                                   return_value=MagicMock(text=_GM_JSON)))
            st.enter_context(patch("core.engine.model_narrator.generate_content",
                                   MagicMock(return_value=MagicMock(text="Hero walks on. " * 10))))
            st.enter_context(patch("core.engine._run_bg_task",
                                   side_effect=lambda c: (c.close(), MagicMock())[1]))
            st.enter_context(patch("core.engine.move_npcs_with_player", AsyncMock(side_effect=_boom)))
            return await eng.process_game_turn(chat_id=CID, user_input="Веду", narrator_queue=None)

    try:
        result = asyncio.run(_go())
    finally:
        eng.user_sessions.pop(CID, None)
    assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# Debug trace
# ─────────────────────────────────────────────────────────────────────────────

def _trace_roster(r):
    trace = r["session"].get("last_debug_trace")
    assert trace is not None, "debug trace не збережено в сесії"
    return trace["worker"]["roster"]


def test_debug_line_for_moved_companions():
    r = _run_turn(_worker_updates(), debug=True)
    lines = _trace_roster(r)
    assert f"  companions_moving: raw=['{CAT}'] matched=['{CAT}'] → moved: ['{CAT}']" in lines


@pytest.mark.parametrize("raw", ["кейтлін старк", "КЕЙТЛІН СТАРК", "  кейтлін старк "])
def test_case_variant_matched_to_canonical_name(raw):
    r = _run_turn(_worker_updates(companions_moving=[raw]))
    r["move"].assert_awaited_once_with(CID, [CAT], LOC, NEW_SCENE)


def test_apostrophe_variant_matched_to_canonical_name():
    arya = "Ар'я Старк"
    r = _run_turn(_worker_updates(companions_moving=["ар’я старк"]), start_roster=(CAT, arya))
    r["move"].assert_awaited_once_with(CID, [arya], LOC, NEW_SCENE)


def test_case_and_canonical_duplicates_collapse_to_one():
    r = _run_turn(_worker_updates(companions_moving=["кейтлін старк", CAT]))
    r["move"].assert_awaited_once_with(CID, [CAT], LOC, NEW_SCENE)


def test_name_outside_roster_rejected_even_with_case_variant():
    r = _run_turn(_worker_updates(companions_moving=["роб старк"]))
    r["move"].assert_not_awaited()


def test_debug_line_shows_raw_and_matched_for_case_variant():
    r = _run_turn(_worker_updates(companions_moving=["кейтлін старк"]), debug=True)
    lines = _trace_roster(r)
    assert f"  companions_moving: raw=['кейтлін старк'] matched=['{CAT}'] → moved: ['{CAT}']" in lines


def test_debug_worker_roster_uses_start_roster():
    r = _run_turn(_worker_updates(), debug=True)
    first = _trace_roster(r)[0]
    assert CAT in first and SANSA in first  # стартовий ростер, не arriving


def test_debug_line_failure_reason():
    r = _run_turn(_worker_updates(outcome="FAILURE"), debug=True)
    line = [l for l in _trace_roster(r) if "companions_moving" in l][0]
    assert "moved: []" in line and "outcome FAILURE" in line and f"skipped: ['{CAT}']" in line


def test_debug_line_travel_reason():
    r = _run_turn(_worker_updates(location_impact="В дорозі", scene_impact="none"), debug=True)
    line = [l for l in _trace_roster(r) if "companions_moving" in l][0]
    assert "travel mode" in line


def test_debug_line_place_unchanged_reason():
    r = _run_turn(_worker_updates(scene_impact="none"), debug=True)
    line = [l for l in _trace_roster(r) if "companions_moving" in l][0]
    assert "player place unchanged" in line


def test_debug_line_combat_reason():
    r = _run_turn(_worker_updates(), in_combat=True, debug=True)
    line = [l for l in _trace_roster(r) if "companions_moving" in l][0]
    assert "combat" in line


def test_debug_line_not_in_roster_reason():
    r = _run_turn(_worker_updates(companions_moving=["Роб Старк"]), debug=True)
    line = [l for l in _trace_roster(r) if "companions_moving" in l][0]
    assert "not in start roster/dead" in line


def test_debug_line_default_when_no_companions():
    r = _run_turn(_worker_updates(companions_moving=[]), debug=True)
    assert "  companions_moving: [] → moved: []" in _trace_roster(r)
