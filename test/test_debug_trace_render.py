"""Рендер debug-trace у bot/handlers.py::_send_debug_trace_file з новими ключами (header, metrics,
roster, narrator_diag, total_s). bot.send_document замокано; handlers імпортується зі стабами
(як у test_debug_trace_send.py)."""
import asyncio
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(scope="module")
def handlers_module():
    _stub_keys = [
        "database.sheets", "database.operations", "core.world", "core.intro_cache",
        "bot.help_text", "bot.handlers", "core.engine", "core.cheats",
    ]
    _orig = {k: sys.modules.get(k) for k in _stub_keys}

    fake_sheets = types.ModuleType("database.sheets")
    fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = fake_sheets

    fake_ops = types.ModuleType("database.operations")
    for name in (
        "get_user_data", "save_user_data", "delete_user_data",
        "refresh_npc_database", "update_npcs_in_db", "update_npc_reputation",
        "append_memory_anchor", "ensure_user_npc_sheet", "delete_user_npc_sheet",
        "get_relevant_context",
    ):
        setattr(fake_ops, name, AsyncMock())
    for name in (
        "get_unique_regions", "get_houses_by_region", "get_house_stats_data",
        "clear_npc_cache", "get_location_npcs", "get_dead_npc_names",
        "find_best_match", "_npc_tab_name",
    ):
        setattr(fake_ops, name, MagicMock())
    sys.modules["database.operations"] = fake_ops

    fake_world = types.ModuleType("core.world")
    for name in (
        "get_canon_characters", "generate_initial_stats", "get_narrative_intro",
        "background_canon_generation", "populate_contextual_npcs", "build_fallback_intro",
    ):
        setattr(fake_world, name, MagicMock())
    sys.modules["core.world"] = fake_world

    fake_intro_cache = types.ModuleType("core.intro_cache")
    fake_intro_cache.get_cached_intro = MagicMock(return_value=None)
    fake_intro_cache.set_cached_intro = MagicMock()
    sys.modules["core.intro_cache"] = fake_intro_cache

    fake_help = types.ModuleType("bot.help_text")
    fake_help.HELP_TEXT = "STUB"
    fake_help.ADMIN_HELP_TEXT = "STUB_ADMIN"
    sys.modules["bot.help_text"] = fake_help

    fake_engine = types.ModuleType("core.engine")
    fake_engine.process_game_turn = AsyncMock(return_value=("narrative text", []))
    fake_engine.user_sessions = {}
    sys.modules["core.engine"] = fake_engine

    fake_cheats = types.ModuleType("core.cheats")
    fake_cheats.DEBUG_USERS = set()
    sys.modules["core.cheats"] = fake_cheats

    sys.modules.pop("bot.handlers", None)
    import bot.handlers as hm

    yield hm

    for key, val in _orig.items():
        if val is not None:
            sys.modules[key] = val
        else:
            sys.modules.pop(key, None)


SECRET = "AIzaSyA1234567890123456789012345678901234"


def _base_trace(chat_id=111):
    return {
        "chat_id": chat_id, "user_input": "Test action", "timestamp": time.time(),
        "censor": {"prompt": "cp", "raw": None, "parsed": {"is_valid": True, "refusal_reason": ""}, "thoughts": []},
        "worker": {"prompt": "wp", "raw": '{"difficulty": 10}', "parsed": {"difficulty": 10}, "thoughts": []},
        "gm_logic": {"prompt": "gp", "raw": "{}", "parsed": {"director_notes": []}, "thoughts": []},
        "narrator": {"prompt": "np", "thoughts": [], "final_text": "FINAL_NARRATIVE_TEXT"},
        "logs": ["LOG_LINE_ONE"],
    }


def _full_trace():
    t = _base_trace()
    t["header"] = ["  BOT_VERSION: 9.9.9",
                   "  Narrator experiment: model=m thinking_level=high preamble_variant=explicit",
                   "  Turn total time: 12.34s"]
    t["total_s"] = 12.34
    t["censor"]["metrics"] = ["  CENSOR_METRIC_ROW"]
    t["worker"]["metrics"] = ["  WORKER_METRIC_ROW"]
    t["worker"]["roster"] = ["  WORKER_ROSTER_ROW"]
    t["gm_logic"]["metrics"] = ["  GM_METRIC_ROW"]
    t["gm_logic"]["roster"] = ["  GM_ROSTER_ROW"]
    t["narrator"]["metrics"] = ["  NARR_METRIC_ROW"]
    t["narrator_diag"] = ["  NARR_DIAG_ROW"]
    return t


def _render(hm, trace):
    bot = MagicMock()
    bot.send_document = AsyncMock()
    asyncio.run(hm._send_debug_trace_file(bot, trace["chat_id"], trace))
    return bot.send_document.call_args.kwargs["document"].data.decode("utf-8")


def test_all_new_blocks_rendered(handlers_module):
    c = _render(handlers_module, _full_trace())
    for marker in ("BOT_VERSION: 9.9.9", "Narrator experiment:", "CENSOR_METRIC_ROW", "WORKER_METRIC_ROW",
                   "WORKER_ROSTER_ROW", "GM_METRIC_ROW", "GM_ROSTER_ROW", "NARR_METRIC_ROW", "NARR_DIAG_ROW"):
        assert marker in c, marker
    assert c.count("┌─ METRICS ") == 4
    assert c.count("┌─ ROSTER ") == 2


def test_section_order_and_5b_between_5_and_6(handlers_module):
    c = _render(handlers_module, _full_trace())
    idx = [c.index(f"STAGE {n}:") for n in ("1", "2", "3", "4", "5", "5b", "6")]
    assert idx == sorted(idx)
    assert "STAGE 5b: NARRATOR — DIAGNOSTICS" in c
    assert c.index("FINAL_NARRATIVE_TEXT") < c.index("STAGE 5b:") < c.index("NARR_DIAG_ROW") < c.index("STAGE 6:")


def test_header_is_under_title_before_stage1(handlers_module):
    c = _render(handlers_module, _full_trace())
    assert c.index("DEBUG TRACE") < c.index("BOT_VERSION: 9.9.9") < c.index("STAGE 1:")


def test_metrics_and_roster_positions_within_stages(handlers_module):
    c = _render(handlers_module, _full_trace())
    assert c.index("STAGE 2:") < c.index("CENSOR_METRIC_ROW") < c.index("STAGE 3:")
    assert c.index("STAGE 3:") < c.index("WORKER_ROSTER_ROW") < c.index("WORKER_METRIC_ROW") < c.index("STAGE 4:")
    assert c.index("STAGE 4:") < c.index("GM_ROSTER_ROW") < c.index("GM_METRIC_ROW") < c.index("STAGE 5:")
    assert c.index("STAGE 5:") < c.index("NARR_METRIC_ROW") < c.index("STAGE 5b:")


def test_empty_keys_render_no_frames(handlers_module):
    t = _full_trace()
    for st in ("censor", "worker", "gm_logic", "narrator"):
        t[st]["metrics"] = []
    t["worker"]["roster"] = []
    t["gm_logic"]["roster"] = None
    t["narrator_diag"] = []
    t["header"] = []
    c = _render(handlers_module, t)
    assert "┌─ METRICS" not in c and "┌─ ROSTER" not in c
    assert "STAGE 5b" not in c
    assert "BOT_VERSION" not in c


def test_blank_and_none_rows_skipped_so_no_empty_frame(handlers_module):
    t = _base_trace()
    t["worker"]["metrics"] = [None, ""]
    c = _render(handlers_module, t)
    assert "┌─ METRICS" not in c


def test_secret_masked_in_metrics_row(handlers_module):
    t = _full_trace()
    t["worker"]["metrics"] = [f"  exception=E: url?key={SECRET}"]
    t["narrator_diag"] = [f"  caught error: token=SUPERSECRETTOKEN {SECRET}"]
    t["header"] = [f"  hdr {SECRET}"]
    c = _render(handlers_module, t)
    assert SECRET not in c and "SUPERSECRETTOKEN" not in c


def test_long_rows_not_truncated(handlers_module):
    t = _full_trace()
    t["worker"]["metrics"] = ["  " + "x" * 1000]
    c = _render(handlers_module, t)
    assert "x" * 1000 in c


def test_total_time_not_duplicated_when_header_has_it(handlers_module):
    c = _render(handlers_module, _full_trace())
    assert c.lower().count("turn total time") == 1


def test_total_time_rendered_when_header_missing(handlers_module):
    t = _base_trace()
    t["total_s"] = 3.5
    c = _render(handlers_module, t)
    assert c.lower().count("turn total time") == 1 and "3.50s" in c


def test_total_time_absent_when_no_total_s_and_no_header(handlers_module):
    assert "turn total time" not in _render(handlers_module, _base_trace()).lower()


def test_old_trace_without_new_keys_renders_as_before(handlers_module):
    c = _render(handlers_module, _base_trace())
    for n in ("1", "2", "3", "4", "5", "6"):
        assert f"STAGE {n}:" in c
    assert "STAGE 5b" not in c
    assert "FINAL_NARRATIVE_TEXT" in c and "LOG_LINE_ONE" in c
    assert "END OF TRACE" in c


def test_send_document_called_once_with_txt(handlers_module):
    hm = handlers_module
    bot = MagicMock()
    bot.send_document = AsyncMock()
    asyncio.run(hm._send_debug_trace_file(bot, 5, _full_trace()))
    bot.send_document.assert_awaited_once()
    assert bot.send_document.call_args.kwargs["document"].filename.endswith(".txt")


def test_section_header_accepts_string_number(handlers_module):
    assert "STAGE 5b: X" in handlers_module._section_header("5b", "X")
    assert "STAGE 3: X" in handlers_module._section_header(3, "X")
