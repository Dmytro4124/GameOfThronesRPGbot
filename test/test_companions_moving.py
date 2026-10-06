"""Feature "NPC рухаються разом з гравцем": prompts/schema, _build_updates,
database.operations.move_npcs_with_player, update_npcs_in_db(locked_names=...).

Без мережі: Sheets (database.operations.db) замокано; async через asyncio.run().
Канонічна назва локації в проєкті -- "Вінтерфел" (одна "л"), саме її приймає is_valid_location.
"""
import asyncio
import logging
import re
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from google.genai import types

import database.operations as ops
from core.prompts import (
    WORKER_NORMAL_SCHEMA,
    build_gm_logic_parts,
    build_normal_resolve_parts,
)

CID = 555001
LOC = "Вінтерфел"
OLD_SCENE = "Жіночі Покої"
NEW_SCENE = "Покої Лорда"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema
# ─────────────────────────────────────────────────────────────────────────────

def _updates_schema():
    return WORKER_NORMAL_SCHEMA["properties"]["updates"]


def test_schema_companions_moving_is_array_of_strings():
    cm = _updates_schema()["properties"]["companions_moving"]
    assert cm["type"] == "array"
    assert cm["items"]["type"] == "string"


def test_schema_companions_moving_not_required():
    assert "companions_moving" not in _updates_schema().get("required", [])
    assert "companions_moving" not in WORKER_NORMAL_SCHEMA.get("required", [])


def test_schema_companions_moving_in_property_ordering():
    assert "companions_moving" in _updates_schema()["propertyOrdering"]


def test_schema_with_companions_moving_passes_sdk_validation():
    types.Schema.model_validate(WORKER_NORMAL_SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Worker static: rule + EXAMPLE P
# ─────────────────────────────────────────────────────────────────────────────

def _worker_parts():
    return build_normal_resolve_parts(
        user_input="Я веду Кейтлін у Покої Лорда",
        profile={
            "Ім'я": "Джон", "Дім": "Старк", "level": 1, "class": "Bastard", "heritage": "",
            "hp_current": 12, "hp_max": 12, "ac": 14, "proficiency_bonus": 2,
            "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
            "skill_profs": [], "conditions": [], "Особисте Золото": 10, "Інвентар": "Меч",
            "Годинники": {"Scene_Tension": "0/4"},
        },
        current_scene=OLD_SCENE,
        npcs_in_scene=[{"Name": "Кейтлін Старк", "Relation_Player": "Нейтральний"},
                       {"Name": "Санса Старк", "Relation_Player": "Нейтральний"}],
        last_turn_summary="x", current_location=LOC,
        npc_reputation_context={"Кейтлін Старк": 0, "Санса Старк": 0},
        clocks_info={"Scene_Tension": "0/4"}, nearby_canonical_locs=[LOC],
        all_canonical_locs_grouped="Північ: Вінтерфел",
    )


def test_worker_static_has_companions_rule_and_example_p():
    static, dynamic = _worker_parts()
    assert "companions_moving" in static
    assert "EXAMPLE P" in static
    seg = static.split("EXAMPLE P")[1].split("EXAMPLE M")[0]
    assert 'companions_moving: ["Кейтлін Старк"]' in seg
    assert "companions_moving: []" in seg  # negative example: "лишайся"


def test_worker_static_companions_rule_in_gate5():
    static, _ = _worker_parts()
    gate5 = static.split("[GATE 5")[1].split("[GATE 6")[0]
    assert "companions_moving" in gate5


def test_worker_static_has_no_concrete_npc_names_from_dynamic_roster():
    """Статика (кешована) не має залежати від ростеру: імена з npcs_in_scene -- лише в динаміці."""
    static, dynamic = _worker_parts()
    assert "Санса Старк" in dynamic
    # у EXAMPLE P імена приклад-ні, тому перевіряємо поза few_shot блоком
    outside = static.split("<few_shot_examples>")[0] + static.split("</few_shot_examples>")[-1]
    assert "Санса Старк" not in outside


# ─────────────────────────────────────────────────────────────────────────────
# 3. GM_Logic: <moved_with_player>
# ─────────────────────────────────────────────────────────────────────────────

def _gm_parts(**kw):
    args = dict(
        hero_name="Джон", hero_house="Старк", profile_json="{}",
        context_knowledge="", event_injection="", burst_injection="",
        current_time_str="День 1", curr_region="Північ", curr_loc=LOC,
        is_traveling=False, loc_hint="", curr_scene=NEW_SCENE,
        valid_locs_str=LOC, valid_regions_str="Північ", region_locs_str=LOC,
        npc_context_text="Кейтлін Старк (Active)", tension_label="Спокійна",
        mechanics_verdict="SUCCESS", impact_narrative_hints="", history_text="",
        user_input="Веду Кейтлін", action_slots=["A", "B", "C", "D"], puppet_mode=False,
        scenes_block_str="- Покої Лорда", mode="NORMAL",
    )
    args.update(kw)
    return build_gm_logic_parts(**args)


def test_gm_dynamic_has_moved_with_player_block_with_names():
    _, dyn = _gm_parts(moved_companions=["Кейтлін Старк", "Санса Старк"])
    assert "<moved_with_player>\n" in dyn and "</moved_with_player>" in dyn
    block = dyn.split("<moved_with_player>\n")[1].split("</moved_with_player>")[0]
    assert "Кейтлін Старк" in block and "Санса Старк" in block


@pytest.mark.parametrize("val", [None, []])
def test_gm_dynamic_no_block_without_moved_companions(val):
    _, dyn = _gm_parts(moved_companions=val)
    assert "<moved_with_player>" not in dyn
    assert "moved_with_player" not in dyn


def test_gm_dynamic_no_block_when_param_omitted():
    _, dyn = _gm_parts()
    assert "<moved_with_player>" not in dyn


def test_gm_static_has_no_names_and_no_block_tag_data():
    static, _ = _gm_parts(moved_companions=["Кейтлін Старк"])
    assert "Кейтлін Старк" not in static
    assert "<moved_with_player>\n" not in static  # лише згадка в правилах, не блок даних


@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_gm_static_identical_with_and_without_moved_companions(mode):
    s1, _ = _gm_parts(mode=mode, moved_companions=["Кейтлін Старк"])
    s2, _ = _gm_parts(mode=mode)
    s3, _ = _gm_parts(mode=mode, moved_companions=[])
    assert s1 == s2 == s3


def test_gm_absent_npcs_block_mentions_moved_exception():
    _, dyn = _gm_parts(absent_npcs=["Санса Старк"], moved_companions=["Кейтлін Старк"])
    absent = dyn.split("<absent_npcs>")[1].split("</absent_npcs>")[0]
    assert "Санса Старк" in absent
    assert "moved_with_player" in absent


# ─────────────────────────────────────────────────────────────────────────────
# 4. _build_updates normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _bu(raw):
    from core.dnd_engine import _build_updates
    return _build_updates(
        raw, action_type="standard", skill_used="None", ability_used="None",
        outcome="SUCCESS", natural_roll=10, total_score=12, difficulty=10,
        advantage_reason="", disadvantage_reason="", combat_imminent=False,
        xp_award=0, reputation_delta=0, reputation_target_npc="",
    )


@pytest.mark.parametrize("raw,expected", [
    ({}, []),                                              # відсутній ключ
    ({"companions_moving": None}, []),
    ({"companions_moving": "Кейтлін Старк"}, []),          # рядок, не список
    ({"companions_moving": {"a": 1}}, []),
    ({"companions_moving": 5}, []),
    ({"companions_moving": []}, []),
    ({"companions_moving": ["Кейтлін Старк"]}, ["Кейтлін Старк"]),
    ({"companions_moving": ["  Кейтлін Старк  "]}, ["Кейтлін Старк"]),   # strip
    ({"companions_moving": ["", "   ", "Санса Старк"]}, ["Санса Старк"]),  # порожні
    ({"companions_moving": [None, 5, {"n": 1}, ["x"], "Нед Старк"]}, ["Нед Старк"]),  # не-рядки
])
def test_build_updates_companions_moving_normalised(raw, expected):
    assert _bu(raw)["companions_moving"] == expected


def test_build_updates_does_not_mutate_input_and_keeps_other_keys():
    raw = {"companions_moving": "x", "minutes_passed": 5, "scene_impact": "Кузня"}
    out = _bu(raw)
    assert raw["companions_moving"] == "x"  # shallow copy: вхід не змінено
    assert out["minutes_passed"] == 5 and out["scene_impact"] == "Кузня"


# ─────────────────────────────────────────────────────────────────────────────
# 5. move_npcs_with_player
# ─────────────────────────────────────────────────────────────────────────────

def _npc(name, scene="Невідомо"):
    return {"name": name, "scene": scene, "card": f"> **{name}**\n",
            "reputation_score": 0, "region": "Північ"}


def _make_cache():
    return {
        LOC: [_npc("Кейтлін Старк", OLD_SCENE), _npc("Санса Старк", OLD_SCENE),
              _npc("Ар'я Старк", OLD_SCENE)],
        "Королівська Гавань": [_npc("Тиріон Ланністер", "Тронна Зала")],
        "GLOBAL": [_npc("Мандрівник", "Global")],
    }


HEADERS = ["Location", "Scene", "Name", "Status"]


def _ws(rows=None):
    ws = MagicMock()
    ws.get_all_values.return_value = [HEADERS] + (rows if rows is not None else [
        [LOC, OLD_SCENE, "Кейтлін Старк", "Active"],
        [LOC, OLD_SCENE, "Санса Старк", "Active"],
        [LOC, OLD_SCENE, "Ар'я Старк", "Active"],
    ])
    return ws


@pytest.fixture(autouse=True)
def _session():
    from core.engine import user_sessions
    user_sessions.pop(CID, None)
    ops._pending_move_tasks.pop(CID, None)
    user_sessions[CID] = {"npc_cache": _make_cache(), "dead_npc_names": {"Тайвін Ланністер"}}
    yield
    user_sessions.pop(CID, None)
    ops._pending_move_tasks.pop(CID, None)


def _cache():
    from core.engine import user_sessions
    return user_sessions[CID]["npc_cache"]


def _names(loc):
    return [n["name"] for n in _cache().get(loc, [])]


def _move(names, location=LOC, scene=NEW_SCENE, ws=None):
    """Виклик move_npcs_with_player + очікування фонового запису. Повертає (moved, ws)."""
    ws = ws or _ws()

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            moved = await ops.move_npcs_with_player(CID, names, location, scene)
            await ops._await_pending_moves(CID)
            return moved

    return asyncio.run(_run()), ws


def test_move_updates_cache_scene_same_location():
    moved, _ = _move(["Кейтлін Старк"])
    assert moved == ["Кейтлін Старк"]
    entry = next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")
    assert entry["scene"] == NEW_SCENE
    # інші не зачеплені
    assert next(n for n in _cache()[LOC] if n["name"] == "Санса Старк")["scene"] == OLD_SCENE


def test_move_removes_from_old_location_and_adds_to_new():
    moved, _ = _move(["Кейтлін Старк"], location="Королівська Гавань", scene="Тронна Зала")
    assert moved == ["Кейтлін Старк"]
    assert "Кейтлін Старк" not in _names(LOC)
    assert "Кейтлін Старк" in _names("Королівська Гавань")
    assert _names("Королівська Гавань").count("Кейтлін Старк") == 1
    entry = next(n for n in _cache()["Королівська Гавань"] if n["name"] == "Кейтлін Старк")
    assert entry["scene"] == "Тронна Зала"
    # решта не змінилась
    assert "Санса Старк" in _names(LOC) and "Тиріон Ланністер" in _names("Королівська Гавань")


def test_move_moves_from_global_bucket_too():
    moved, _ = _move(["Мандрівник"])
    assert moved == ["Мандрівник"]
    assert "Мандрівник" not in _names("GLOBAL")
    assert "Мандрівник" in _names(LOC)


def test_move_skips_unknown_names():
    moved, _ = _move(["Кейтлін Старк", "Невідомий Лицар"])
    assert moved == ["Кейтлін Старк"]


def test_move_skips_dead_names_even_if_in_cache():
    _cache()[LOC].append(_npc("Тайвін Ланністер", OLD_SCENE))
    moved, _ = _move(["Тайвін Ланністер"])
    assert moved == []
    assert next(n for n in _cache()[LOC] if n["name"] == "Тайвін Ланністер")["scene"] == OLD_SCENE


def test_move_dead_check_is_normalised():
    _cache()[LOC].append(_npc("Тайвін Ланністер", OLD_SCENE))
    moved, _ = _move(["ТАЙВІН ЛАННІСТЕР"])
    assert moved == []


@pytest.mark.parametrize("raw", ["ар'я старк", "АР'Я СТАРК", "Ар’я Старк", "  Ар`я Старк "])
def test_move_name_normalisation_returns_canonical(raw):
    moved, _ = _move([raw])
    assert moved == ["Ар'я Старк"]


def test_move_is_exact_match_not_fuzzy():
    moved, _ = _move(["Кейтлін"])  # лише ім'я -- не точний збіг
    assert moved == []


def test_move_dedups_duplicate_names():
    moved, _ = _move(["Кейтлін Старк", "кейтлін старк"])
    assert moved == ["Кейтлін Старк"]
    assert _names(LOC).count("Кейтлін Старк") == 1


def test_move_empty_names_returns_empty():
    moved, ws = _move([])
    assert moved == []
    ws.update_cells.assert_not_called()


@pytest.mark.parametrize("loc", ["В дорозі", "", "   ", "Мордор (вигадка)", "none"])
def test_move_invalid_location_returns_empty_and_cache_untouched(loc):
    import copy
    before = copy.deepcopy(_cache())
    moved, ws = _move(["Кейтлін Старк"], location=loc)
    assert moved == []
    assert _cache() == before
    ws.update_cells.assert_not_called()


def test_move_no_session_returns_empty():
    from core.engine import user_sessions
    user_sessions.pop(CID, None)
    moved, _ = _move(["Кейтлін Старк"])
    assert moved == []


def test_move_empty_scene_falls_back_to_nevidomo():
    moved, _ = _move(["Кейтлін Старк"], scene="")
    assert moved == ["Кейтлін Старк"]
    assert next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")["scene"] == "невідомо"


def test_move_cache_updated_synchronously_before_background_write():
    """Кеш змінено до того, як фонова таска встигла виконатись (немає await між читанням і записом)."""
    ws = _ws()

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            moved = await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, NEW_SCENE)
            snapshot = next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")["scene"]
            written_yet = ws.update_cells.called
            await ops._await_pending_moves(CID)
            return moved, snapshot, written_yet

    moved, snapshot, written_yet = asyncio.run(_run())
    assert snapshot == NEW_SCENE
    assert written_yet is False  # Sheets ще не торкались


def test_background_write_uses_correct_columns_and_values():
    moved, ws = _move(["Кейтлін Старк", "Ар'я Старк"])
    ws.update_cells.assert_called_once()
    cells = ws.update_cells.call_args[0][0]
    got = {(c.row, c.col): c.value for c in cells}
    # HEADERS: Location=1, Scene=2, Name=3; рядки даних з 2
    assert got == {
        (2, 1): LOC, (2, 2): NEW_SCENE,   # Кейтлін
        (4, 1): LOC, (4, 2): NEW_SCENE,   # Ар'я
    }


def test_background_write_matches_names_normalised():
    ws = _ws([[LOC, OLD_SCENE, "АР’Я СТАРК", "Active"]])
    _move(["Ар'я Старк"], ws=ws)
    cells = ws.update_cells.call_args[0][0]
    assert {(c.row, c.col) for c in cells} == {(2, 1), (2, 2)}


def test_background_write_skips_missing_columns(capsys):
    ws = MagicMock()
    ws.get_all_values.return_value = [["Name", "Status"], ["Кейтлін Старк", "Active"]]
    moved, _ = _move(["Кейтлін Старк"], ws=ws)
    assert moved == ["Кейтлін Старк"]
    ws.update_cells.assert_not_called()


def test_background_write_failure_logs_warning_and_does_not_roll_back_cache(caplog, monkeypatch):
    monkeypatch.setattr(ops, "NPC_MOVE_RETRY_DELAY_SEC", 0)
    ws = _ws()
    ws.update_cells.side_effect = RuntimeError("sheets down")
    with caplog.at_level(logging.WARNING, logger=ops._logger.name):
        moved, _ = _move(["Кейтлін Старк"], ws=ws)
    assert moved == ["Кейтлін Старк"]
    assert "sheets down" in caplog.text and "Кейтлін Старк" in caplog.text and LOC in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")["scene"] == NEW_SCENE


def test_background_write_get_sheet_exception_does_not_propagate(caplog, monkeypatch):
    monkeypatch.setattr(ops, "NPC_MOVE_RETRY_DELAY_SEC", 0)

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.side_effect = RuntimeError("boom")
            moved = await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, NEW_SCENE)
            await ops._await_pending_moves(CID)  # не має кидати
            return moved

    with caplog.at_level(logging.WARNING, logger=ops._logger.name):
        assert asyncio.run(_run()) == ["Кейтлін Старк"]
    assert "boom" in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_pending_tasks_are_released_after_completion():
    _move(["Кейтлін Старк"])
    assert CID not in ops._pending_move_tasks


def test_await_pending_moves_noop_without_tasks():
    asyncio.run(ops._await_pending_moves(CID))  # не кидає


# ── серіалізація, retry, timeout, region, GLOBAL ──────────────────────────────

def test_sequential_moves_same_npc_last_write_is_second_scene():
    """Друга таска чекає першу: навіть якщо перший запис повільний, фінал у Sheets = друга сцена."""
    state = {"scene": OLD_SCENE}
    order = []

    def _update_cells(cells):
        vals = {c.col: c.value for c in cells}
        if vals[2] == NEW_SCENE:
            time.sleep(0.3)  # перший запис повільний
        state["scene"] = vals[2]
        order.append(vals[2])

    ws = _ws()
    ws.update_cells.side_effect = _update_cells
    second = "Тронна Зала"

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, NEW_SCENE)
            await asyncio.sleep(0)  # перший запис стартує
            await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, second)
            await ops._await_pending_moves(CID)

    asyncio.run(_run())
    assert order == [NEW_SCENE, second]
    assert state["scene"] == second


def test_move_does_not_block_on_previous_task():
    """Хід не блокується: move_npcs_with_player повертається, поки перший запис ще триває."""
    ws = _ws()
    ws.update_cells.side_effect = lambda cells: time.sleep(0.3)

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, NEW_SCENE)
            t0 = time.monotonic()
            await ops.move_npcs_with_player(CID, ["Санса Старк"], LOC, "Кузня")
            elapsed = time.monotonic() - t0
            await ops._await_pending_moves(CID)
            return elapsed

    assert asyncio.run(_run()) < 0.2


def test_retry_first_attempt_fails_second_succeeds(caplog, monkeypatch):
    monkeypatch.setattr(ops, "NPC_MOVE_RETRY_DELAY_SEC", 0)
    ws = _ws()
    ws.update_cells.side_effect = [RuntimeError("flaky"), None]
    with caplog.at_level(logging.WARNING, logger=ops._logger.name):
        moved, _ = _move(["Кейтлін Старк"], ws=ws)
    assert moved == ["Кейтлін Старк"]
    assert ws.update_cells.call_count == 2
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_retry_both_fail_warning_and_cache_kept(caplog, monkeypatch):
    monkeypatch.setattr(ops, "NPC_MOVE_RETRY_DELAY_SEC", 0)
    ws = _ws()
    ws.update_cells.side_effect = RuntimeError("down")
    with caplog.at_level(logging.WARNING, logger=ops._logger.name):
        _move(["Кейтлін Старк"], ws=ws)
    assert ws.update_cells.call_count == 2
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    assert next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")["scene"] == NEW_SCENE


def test_await_pending_moves_timeout_warns_and_does_not_cancel(caplog, monkeypatch):
    monkeypatch.setattr(ops, "PENDING_MOVES_TIMEOUT_SEC", 0.05)
    ws = _ws()
    ws.update_cells.side_effect = lambda cells: time.sleep(0.4)

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, NEW_SCENE)
            tasks = list(ops._pending_move_tasks[CID])
            t0 = time.monotonic()
            await ops._await_pending_moves(CID)
            waited = time.monotonic() - t0
            not_done_after_wait = not tasks[0].done()
            cancelled = tasks[0].cancelled()
            await asyncio.gather(*tasks, return_exceptions=True)
            return waited, not_done_after_wait, cancelled, tasks[0]

    with caplog.at_level(logging.WARNING, logger=ops._logger.name):
        waited, not_done, cancelled, task = asyncio.run(_run())
    assert waited < 0.3
    assert not_done and not cancelled
    assert task.done() and not task.cancelled()
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def _region_ws(with_region_col):
    headers = ["Location", "Scene", "Name", "Status"] + (["REGION"] if with_region_col else [])
    row = [LOC, OLD_SCENE, "Кейтлін Старк", "Active"] + (["Північ"] if with_region_col else [])
    ws = MagicMock()
    ws.get_all_values.return_value = [headers, row]
    return ws


def test_region_updated_in_cache_and_sheet_with_region_column():
    from core.world_constants import get_region_for_location
    dest = "Королівська Гавань"
    region = get_region_for_location(dest)
    assert region and region != "Північ"
    ws = _region_ws(True)
    moved, _ = _move(["Кейтлін Старк"], location=dest, scene="Тронна Зала", ws=ws)
    assert moved == ["Кейтлін Старк"]
    entry = next(n for n in _cache()[dest] if n["name"] == "Кейтлін Старк")
    assert entry["region"] == region
    cells = ws.update_cells.call_args[0][0]
    got = {(c.row, c.col): c.value for c in cells}
    assert got == {(2, 1): dest, (2, 2): "Тронна Зала", (2, 5): region}


def test_region_cache_updated_but_sheet_has_no_region_column():
    from core.world_constants import get_region_for_location
    dest = "Королівська Гавань"
    ws = _region_ws(False)
    _move(["Кейтлін Старк"], location=dest, scene="Тронна Зала", ws=ws)
    entry = next(n for n in _cache()[dest] if n["name"] == "Кейтлін Старк")
    assert entry["region"] == get_region_for_location(dest)
    cells = ws.update_cells.call_args[0][0]
    assert {(c.row, c.col) for c in cells} == {(2, 1), (2, 2)}


@pytest.mark.parametrize("loc", ["В дорозі", "Мордор (вигадка)"])
def test_travel_or_unknown_location_does_not_change_region(loc):
    ws = _region_ws(True)
    moved, _ = _move(["Кейтлін Старк"], location=loc, ws=ws)
    assert moved == []
    entry = next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")
    assert entry["region"] == "Північ"
    ws.update_cells.assert_not_called()


def test_global_location_rejected_with_warning(caplog):
    import copy
    before = copy.deepcopy(_cache())
    with caplog.at_level(logging.WARNING, logger=ops._logger.name):
        moved, ws = _move(["Кейтлін Старк"], location="GLOBAL")
    assert moved == []
    assert _cache() == before
    ws.update_cells.assert_not_called()
    assert any(r.levelno == logging.WARNING and "GLOBAL" in r.getMessage() for r in caplog.records)


# ── refresh_npc_database не дає старим даним Sheets перезаписати переміщення ───

def _slow_sheet():
    """Sheets-стан зі "повільним" записом: update_cells застосовується лише через 0.3s."""
    state = {"Кейтлін Старк": (LOC, OLD_SCENE)}

    def _records():
        loc, scene = state["Кейтлін Старк"]
        return [{"Location": loc, "Scene": scene, "Name": "Кейтлін Старк", "Status": "Active",
                 "Is_Canon": "TRUE", "Reputation_Score": "0", "Region": "Північ"}]

    def _update_cells(cells):
        time.sleep(0.3)
        vals = {c.col: c.value for c in cells}
        state["Кейтлін Старк"] = (vals[1], vals[2])

    ws = MagicMock()
    ws.get_all_values.return_value = [HEADERS, [LOC, OLD_SCENE, "Кейтлін Старк", "Active"]]
    ws.get_all_records.side_effect = _records
    ws.update_cells.side_effect = _update_cells
    return ws, state


def test_refresh_waits_for_pending_move_write():
    ws, state = _slow_sheet()

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            await ops.move_npcs_with_player(CID, ["Кейтлін Старк"], LOC, NEW_SCENE)
            await asyncio.sleep(0)  # дати фоновій таскі стартувати (запис ще "в польоті")
            return await ops.refresh_npc_database(CID)

    assert asyncio.run(_run()) is True
    cached = next(n for n in _cache()[LOC] if n["name"] == "Кейтлін Старк")
    assert cached["scene"] == NEW_SCENE, "refresh перезаписав переміщення старими даними Sheets"


def test_refresh_without_pending_move_loads_sheet_state():
    """Контроль: без фонового запису refresh читає те, що в Sheets (довів би, що тест вище чутливий)."""
    ws, state = _slow_sheet()

    async def _run():
        with patch.object(ops, "db") as mdb:
            mdb.get_sheet.return_value = ws
            return await ops.refresh_npc_database(CID)

    assert asyncio.run(_run()) is True
    assert next(n for n in _cache()[LOC])["scene"] == OLD_SCENE


# ─────────────────────────────────────────────────────────────────────────────
# 6. update_npcs_in_db(locked_names=...)
# ─────────────────────────────────────────────────────────────────────────────

UHEADERS = ["Location", "Scene", "Name", "Description", "Character", "Goal", "Secrets",
            "Relation_Player", "Memory_Anchor", "Relation_NPCs", "Status", "Is_Canon",
            "Inventory", "Reputation_Score", "Region"]
UCOL = {h.lower(): i + 1 for i, h in enumerate(UHEADERS)}


def _urow(name, loc=LOC, scene=OLD_SCENE):
    return [loc, scene, name, "d", "c", "g", "s", "Нейтральний", "-", "-", "Active", "TRUE",
            "Кинджал", "0", "Північ"]


def _update(updates, **kw):
    ws = MagicMock()
    ws.get_all_values.return_value = [UHEADERS, _urow("Кейтлін Старк"), _urow("Санса Старк")]
    ws.get_all_records.return_value = []

    async def _noop(*a, **k):
        return True

    async def _run():
        with patch.object(ops, "db") as mdb, \
                patch.object(ops, "refresh_npc_database", _noop), \
                patch.object(ops, "append_memory_anchor", _noop):
            mdb.get_sheet.return_value = ws
            await ops.update_npcs_in_db(CID, updates, **kw)

    asyncio.run(_run())
    cells = ws.update_cells.call_args[0][0] if ws.update_cells.called else []
    return {(c.row, c.col): c.value for c in cells}


def test_locked_name_location_and_scene_from_gm_ignored():
    got = _update(
        [{"Name": "Кейтлін Старк", "Location": "Королівська Гавань", "Scene": "Тронна Зала"}],
        locked_names=["Кейтлін Старк"],
    )
    assert got == {}


def test_locked_name_other_fields_still_updated():
    got = _update(
        [{"Name": "Кейтлін Старк", "Location": "Королівська Гавань", "Scene": "Тронна Зала",
          "Inventory": "Меч, плащ"}],
        locked_names=["Кейтлін Старк"],
    )
    assert got == {(2, UCOL["inventory"]): "Меч, плащ"}


def test_locked_name_match_is_normalised():
    got = _update(
        [{"Name": "Кейтлін Старк", "Scene": "Покої Лорда", "Inventory": "Свічка"}],
        locked_names=["КЕЙТЛІН СТАРК"],
    )
    assert (2, UCOL["scene"]) not in got
    assert got[(2, UCOL["inventory"])] == "Свічка"


def test_not_locked_npc_location_still_applied_when_locked_list_has_others():
    got = _update(
        [{"Name": "Санса Старк", "Location": "Королівська Гавань"}],
        locked_names=["Кейтлін Старк"],
    )
    assert got == {(3, UCOL["location"]): "Королівська Гавань"}


def test_without_locked_names_behaviour_unchanged():
    got = _update([{"Name": "Кейтлін Старк", "Location": "Королівська Гавань"}])
    assert got == {(2, UCOL["location"]): "Королівська Гавань"}


def test_teleport_guard_still_blocks_non_locked_npc_to_player_location():
    got = _update(
        [{"Name": "Санса Старк", "Location": "Королівська Гавань"}],
        player_new_location="Королівська Гавань", player_location_changed=True,
        locked_names=["Кейтлін Старк"],
    )
    assert got == {}


def test_scene_drag_guard_still_blocks_non_locked_npc_to_player_scene():
    got = _update(
        [{"Name": "Санса Старк", "Scene": NEW_SCENE}],
        player_new_scene=NEW_SCENE, player_scene_changed=True,
        locked_names=["Кейтлін Старк"],
    )
    assert got == {}


def test_update_waits_for_pending_move_writes():
    """update_npcs_in_db чекає фонові записи переміщення перед власним записом у Sheets."""
    order = []
    ws = MagicMock()
    ws.get_all_values.return_value = [UHEADERS, _urow("Кейтлін Старк")]
    ws.get_all_records.return_value = []

    async def _noop(*a, **k):
        return True

    async def _fake_pending(uid):
        order.append("await_pending")

    async def _run():
        with patch.object(ops, "db") as mdb, \
                patch.object(ops, "refresh_npc_database", _noop), \
                patch.object(ops, "_await_pending_moves", _fake_pending):
            mdb.get_sheet.return_value = ws
            ws.get_all_values.side_effect = lambda: (order.append("read_sheet"), [UHEADERS, _urow("Кейтлін Старк")])[1]
            await ops.update_npcs_in_db(CID, [{"Name": "Кейтлін Старк", "Inventory": "Лист"}])

    asyncio.run(_run())
    assert order[:2] == ["await_pending", "read_sheet"]
