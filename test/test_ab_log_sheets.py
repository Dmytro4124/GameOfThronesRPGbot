"""AB_Log Sheets sink: database.operations, core.narrator_ab sinks, config normalization,
scripts/ab_report --from-sheets. Everything is mocked; no real Sheets/network."""
import asyncio
import importlib.util
import json
import pathlib
import time
from unittest.mock import MagicMock, AsyncMock, patch

import gspread
import pytest

import database.operations as ops
import core.narrator_ab as na

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _turn(**kw):
    rec = {
        "type": "turn", "ts": "2026-01-01T00:00:00", "turn_id": "t1", "user_id": 42,
        "mode": "NORMAL", "vote": "gemma", "vote_raw": "1", "vote_ms": 1234,
        "reason": None, "shown_order": ["gemma", "flash_lite"],
        "mechanics": {"outcome": "success", "difficulty": 12, "natural_roll": 15},
        "narrator_prompt": "PROMPT",
        "results": {
            "gemma": {"text": "G text", "len": 6, "total_ms": 900, "final_attempt": 1, "used_fallback": False},
            "flash_lite": {"text": "F text", "len": 6, "total_ms": 400, "final_attempt": 2, "used_fallback": True},
        },
    }
    rec.update(kw)
    return rec


class FakeWS:
    def __init__(self):
        self.rows = []
        self.fail_with = None

    def append_row(self, row, value_input_option=None):
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc
        self.rows.append(list(row))

    def get_all_values(self):
        return [list(r) for r in self.rows]


def _wsnf():
    return gspread.exceptions.WorksheetNotFound("AB_Log")


@pytest.fixture
def sheet(monkeypatch):
    """Fake spreadsheet with no AB_Log tab yet; resets module caches."""
    ss = MagicMock(name="spreadsheet")
    ws = FakeWS()
    ss.worksheet.side_effect = _wsnf()
    ss.add_worksheet.return_value = ws
    fake_db = MagicMock(name="db")
    fake_db.spreadsheet = ss
    monkeypatch.setattr(ops, "db", fake_db)
    monkeypatch.setattr(ops, "_ab_sheet_ready", False)
    monkeypatch.setattr(ops, "_ab_ws", None)
    monkeypatch.setattr(ops, "_ab_log_lock", asyncio.Lock())
    return type("S", (), {"ss": ss, "ws": ws})


def col(row, name):
    return row[ops.AB_LOG_HEADERS.index(name)]


# ---------------- header / mapping ----------------

def test_headers_are_25_columns():
    assert len(ops.AB_LOG_HEADERS) == 25 and ops.AB_LOG_HEADERS[-1] == "record_json"


def test_header_created_once(sheet):
    assert run(ops.append_ab_log_row(_turn())) is True
    assert run(ops.append_ab_log_row(_turn(turn_id="t2"))) is True
    sheet.ss.add_worksheet.assert_called_once()
    assert sheet.ws.rows[0] == ops.AB_LOG_HEADERS
    assert len(sheet.ws.rows) == 3  # header + 2 data rows


def test_existing_sheet_no_header_added(sheet):
    sheet.ss.worksheet.side_effect = None
    sheet.ss.worksheet.return_value = sheet.ws
    run(ops.append_ab_log_row(_turn()))
    sheet.ss.add_worksheet.assert_not_called()
    assert len(sheet.ws.rows) == 1


def test_turn_row_column_mapping(sheet):
    run(ops.append_ab_log_row(_turn()))
    row = sheet.ws.rows[1]
    assert len(row) == 25
    exp = {
        "ts": "2026-01-01T00:00:00", "type": "turn", "turn_id": "t1", "user_id": "42",
        "mode": "NORMAL", "vote": "gemma", "vote_raw": "1", "vote_ms": "1234", "reason": "",
        "shown_order": '["gemma", "flash_lite"]', "gemma_ms": "900", "flash_lite_ms": "400",
        "gemma_attempt": "1", "flash_lite_attempt": "2", "gemma_fallback": "False",
        "flash_lite_fallback": "True", "gemma_len": "6", "flash_lite_len": "6",
        "outcome": "success", "difficulty": "12", "natural_roll": "15", "note": "",
        "gemma_text": "G text", "flash_lite_text": "F text",
    }
    for k, v in exp.items():
        assert col(row, k) == v, k
    assert json.loads(col(row, "record_json"))["turn_id"] == "t1"


def test_note_row_mapping(sheet):
    run(ops.append_ab_log_row({"type": "note", "ts": "T", "turn_id": "x", "user_id": 7, "note": "too purple"}))
    row = sheet.ws.rows[1]
    assert col(row, "type") == "note" and col(row, "note") == "too purple"
    assert col(row, "user_id") == "7"
    assert col(row, "gemma_text") == "" and col(row, "vote") == ""


# ---------------- truncation ----------------

def test_cell_truncation_45000(sheet):
    big = "я" * 100000
    rec = _turn()
    rec["results"]["gemma"]["text"] = big
    run(ops.append_ab_log_row(rec))
    row = sheet.ws.rows[1]
    assert all(len(c) <= ops.AB_CELL_LIMIT for c in row)
    assert len(col(row, "gemma_text")) == ops.AB_CELL_LIMIT


def test_record_json_small_untouched():
    rec = _turn()
    assert json.loads(ops._ab_record_json(rec)) == rec


def test_record_json_huge_prompt_truncated_but_valid():
    rec = _turn(narrator_prompt="p" * 200000)
    out = ops._ab_record_json(rec)
    assert len(out) <= ops.AB_CELL_LIMIT
    back = json.loads(out)
    assert back["results"]["gemma"]["text"] == "G text"
    assert "truncated" in back["narrator_prompt"]
    assert back["turn_id"] == "t1"


def test_record_json_huge_texts_truncated_and_valid():
    rec = _turn(narrator_prompt="p" * 200000)
    rec["results"]["gemma"]["text"] = "g" * 60000
    rec["results"]["flash_lite"]["text"] = "f" * 60000
    out = ops._ab_record_json(rec)
    assert len(out) <= ops.AB_CELL_LIMIT
    back = json.loads(out)
    assert "truncated" in back["results"]["gemma"]["text"]
    assert back["results"]["flash_lite"]["text"].startswith("f")


def test_record_json_huge_texts_without_prompt_still_bounded():
    rec = _turn()
    rec.pop("narrator_prompt")
    rec["results"]["gemma"]["text"] = "g" * 60000
    rec["results"]["flash_lite"]["text"] = "f" * 60000
    out = ops._ab_record_json(rec)
    assert len(out) <= ops.AB_CELL_LIMIT
    assert json.loads(out)["results"]["gemma"]["text"].startswith("g")


def test_record_json_does_not_mutate_input():
    rec = _turn(narrator_prompt="p" * 200000)
    ops._ab_record_json(rec)
    assert len(rec["narrator_prompt"]) == 200000


# ---------------- failures / recovery ----------------

def test_append_api_error_returns_false(sheet):
    sheet.ss.worksheet.side_effect = None
    sheet.ss.worksheet.return_value = sheet.ws
    sheet.ws.fail_with = RuntimeError("APIError-like boom")
    assert run(ops.append_ab_log_row(_turn())) is False


def test_append_generic_exception_returns_false(sheet):
    sheet.ss.worksheet.side_effect = RuntimeError("down")
    sheet.ss.add_worksheet.side_effect = RuntimeError("down")
    assert run(ops.append_ab_log_row(_turn())) is False


def test_append_unserializable_record_no_raise(sheet):
    assert run(ops.append_ab_log_row({"type": "turn", "x": object()})) in (True, False)


def test_worksheet_not_found_recovery(sheet):
    assert run(ops.append_ab_log_row(_turn(turn_id="a"))) is True
    assert sheet.ss.add_worksheet.call_count == 1
    # sheet deleted by hand: cached ws raises WorksheetNotFound on append
    sheet.ws.fail_with = _wsnf()
    assert run(ops.append_ab_log_row(_turn(turn_id="b"))) is True
    assert sheet.ss.add_worksheet.call_count == 2
    assert json.loads(col(sheet.ws.rows[-1], "record_json"))["turn_id"] == "b"


# ---------------- read ----------------

def test_read_missing_sheet_returns_empty(sheet):
    assert run(ops.read_ab_log_records()) == []
    sheet.ss.add_worksheet.assert_not_called()


def test_read_exception_returns_empty(sheet):
    sheet.ss.worksheet.side_effect = RuntimeError("x")
    assert run(ops.read_ab_log_records()) == []


def test_read_round_trip(sheet):
    recs = [_turn(turn_id="a"), {"type": "note", "turn_id": "a", "note": "n", "user_id": 1}]
    for r in recs:
        run(ops.append_ab_log_row(r))
    assert run(ops.read_ab_log_records()) == recs


def test_read_skips_blank_rows_and_header(sheet):
    run(ops.append_ab_log_row(_turn()))
    sheet.ws.rows.append([""] * 25)
    sheet.ws.rows.append([])
    assert len(run(ops.read_ab_log_records())) == 1


def test_read_corrupt_record_json_rebuilds(sheet):
    run(ops.append_ab_log_row(_turn()))
    sheet.ws.rows[1][ops.AB_LOG_HEADERS.index("record_json")] = "{not json"
    rec = run(ops.read_ab_log_records())[0]
    assert rec["type"] == "turn" and rec["turn_id"] == "t1"
    assert rec["vote"] == "gemma" and rec["vote_raw"] == "1"
    assert rec["vote_ms"] == 1234 and isinstance(rec["vote_ms"], int)
    assert rec["shown_order"] == ["gemma", "flash_lite"]
    g, f = rec["results"]["gemma"], rec["results"]["flash_lite"]
    assert g["text"] == "G text" and g["total_ms"] == 900 and g["final_attempt"] == 1
    assert g["used_fallback"] is False and f["used_fallback"] is True and f["final_attempt"] == 2


def test_read_empty_record_json_rebuilds(sheet):
    run(ops.append_ab_log_row(_turn()))
    sheet.ws.rows[1][-1] = ""
    assert run(ops.read_ab_log_records())[0]["shown_order"] == ["gemma", "flash_lite"]


def test_read_record_json_not_dict_rebuilds(sheet):
    run(ops.append_ab_log_row(_turn()))
    sheet.ws.rows[1][-1] = "[1,2]"
    assert run(ops.read_ab_log_records())[0]["turn_id"] == "t1"


def test_rebuild_bad_values():
    rec = ops._ab_rebuild_record({"vote_ms": "abc", "shown_order": "{bad", "note": "hi"})
    assert rec["vote_ms"] is None and rec["shown_order"] is None
    assert rec["note"] == "hi" and rec["results"] == {} and rec["type"] == "turn"


def test_read_short_row_padded(sheet):
    sheet.ss.worksheet.side_effect = None
    sheet.ss.worksheet.return_value = sheet.ws
    sheet.ws.rows = [ops.AB_LOG_HEADERS, ["ts1", "note", "tid", "u"]]
    rec = run(ops.read_ab_log_records())[0]
    assert rec["type"] == "note" and rec["turn_id"] == "tid"


# ---------------- narrator_ab.append_log sinks ----------------

@pytest.fixture
def ab_env(tmp_path, monkeypatch):
    log = tmp_path / "ab.jsonl"
    monkeypatch.setattr(na, "NARRATOR_AB_LOG_PATH", str(log))
    monkeypatch.setattr(na, "_log_lock", asyncio.Lock())
    sink = AsyncMock(return_value=True)
    monkeypatch.setattr(ops, "append_ab_log_row", sink)
    return type("E", (), {"log": log, "sink": sink})


async def _append_drain(rec):
    await na.append_log(rec)
    await na.drain_pending_sheet_writes()


async def _note_drain():
    ok = await na.append_note(9, 9, "meh")
    await na.drain_pending_sheet_writes()
    return ok


@pytest.mark.parametrize("sink,file_,sheets", [
    ("file", True, False), ("sheets", False, True), ("both", True, True),
])
def test_append_log_sinks(ab_env, monkeypatch, sink, file_, sheets):
    monkeypatch.setattr(na, "NARRATOR_AB_SINK", sink)
    rec = {"type": "turn", "turn_id": "z"}
    run(_append_drain(rec))
    assert ab_env.log.exists() is file_
    if file_:
        assert json.loads(ab_env.log.read_text(encoding="utf-8").strip()) == rec
    if sheets:
        ab_env.sink.assert_awaited_once_with(rec)
    else:
        ab_env.sink.assert_not_awaited()


@pytest.mark.parametrize("exc_kind", ["raise", "false"])
def test_sheets_failure_keeps_file(ab_env, monkeypatch, exc_kind):
    monkeypatch.setattr(na, "NARRATOR_AB_SINK", "both")
    if exc_kind == "raise":
        ab_env.sink.side_effect = RuntimeError("sheets down")
    else:
        ab_env.sink.return_value = False
    run(_append_drain({"type": "turn", "turn_id": "k"}))
    assert json.loads(ab_env.log.read_text(encoding="utf-8").strip())["turn_id"] == "k"


def test_file_failure_does_not_block_sheets(ab_env, monkeypatch):
    monkeypatch.setattr(na, "NARRATOR_AB_SINK", "both")
    monkeypatch.setattr(na, "_write_line", MagicMock(side_effect=OSError("disk")))
    run(_append_drain({"type": "turn"}))
    ab_env.sink.assert_awaited_once()


def test_append_note_goes_to_sheets_sink(ab_env, monkeypatch):
    monkeypatch.setattr(na, "NARRATOR_AB_SINK", "sheets")
    na._last_turn_id.clear()
    na._last_turn_id[9] = "tid9"
    try:
        assert run(_note_drain()) is True
        rec = ab_env.sink.await_args.args[0]
        assert rec["type"] == "note" and rec["note"] == "meh"
    finally:
        na._last_turn_id.clear()


def test_slow_sheets_append_does_not_delay_append_log(ab_env, monkeypatch):
    monkeypatch.setattr(na, "NARRATOR_AB_SINK", "sheets")

    async def slow(_rec):
        await asyncio.sleep(5)
        return True

    ab_env.sink.side_effect = slow

    async def scenario():
        t0 = time.monotonic()
        await na.append_log({"type": "turn", "turn_id": "slow"})
        elapsed = time.monotonic() - t0
        assert na._pending_sheet_tasks  # strong ref held
        await na.drain_pending_sheet_writes(timeout=0.05)  # swallows timeout
        for t in list(na._pending_sheet_tasks):
            t.cancel()
        await asyncio.gather(*list(na._pending_sheet_tasks), return_exceptions=True)
        return elapsed

    assert run(scenario()) < 0.5


# ---------------- config sink normalization ----------------

def _load_cfg():
    spec = importlib.util.spec_from_file_location("_config_sink_under_test", ROOT / "config.py")
    m = importlib.util.module_from_spec(spec)
    with patch("dotenv.load_dotenv", lambda *a, **k: False):
        spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("env,expected", [
    (None, "both"), ("file", "file"), ("sheets", "sheets"), ("both", "both"),
    ("FILE", "file"), ("  Sheets ", "sheets"), ("bogus", "both"), ("", "both"),
])
def test_config_sink_normalization(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("NARRATOR_AB_SINK", raising=False)
    else:
        monkeypatch.setenv("NARRATOR_AB_SINK", env)
    assert _load_cfg().NARRATOR_AB_SINK == expected


# ---------------- scripts/ab_report ----------------

_spec = importlib.util.spec_from_file_location("_ab_report_sheets_test", ROOT / "scripts" / "ab_report.py")
rep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rep)


def test_split_records():
    turns, notes = rep.split_records([_turn(), {"type": "note"}, {"type": "note"}, {"no_type": 1},
                                      "junk", {"type": "other"}])
    assert notes == 2 and len(turns) == 2  # missing type defaults to turn


def test_main_from_sheets_uses_loader_not_file(capsys, tmp_path):
    with patch.object(rep, "load_from_sheets", return_value=[_turn(), {"type": "note"}]) as lf, \
            patch.object(rep, "load_log", side_effect=AssertionError("file must not be read")):
        rc = rep.main(["--from-sheets", "--log", str(tmp_path / "nope.jsonl")])
    assert rc == 0
    lf.assert_called_once()
    assert "Sheets:AB_Log" in capsys.readouterr().out


def test_main_without_flag_reads_file(capsys, tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps(_turn()) + "\n", encoding="utf-8")
    with patch.object(rep, "load_from_sheets", side_effect=AssertionError("no sheets")):
        assert rep.main(["--log", str(p)]) == 0
    assert capsys.readouterr().out


def test_main_from_sheets_empty_exits_1(capsys):
    with patch.object(rep, "load_from_sheets", return_value=[]):
        rc = rep.main(["--from-sheets"])
    cap = capsys.readouterr()
    assert rc == 1
    assert "AB_Log порожній або не вдалося прочитати" in cap.err
    assert "Narrator A/B report" not in cap.out


def test_load_from_sheets_failure_is_systemexit():
    with patch.object(ops, "read_ab_log_records", AsyncMock(side_effect=RuntimeError("no creds"))):
        with pytest.raises(SystemExit) as ei:
            rep.load_from_sheets()
    assert "AB_Log" in str(ei.value)


def test_load_from_sheets_returns_records():
    with patch.object(ops, "read_ab_log_records", AsyncMock(return_value=[{"type": "turn"}])):
        assert rep.load_from_sheets() == [{"type": "turn"}]
