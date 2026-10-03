"""Tests for scripts/ab_report.py (offline Narrator A/B report). Synthetic JSONL only."""
import importlib.util
import json
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "ab_report.py"
_spec = importlib.util.spec_from_file_location("_ab_report_under_test", _PATH)
rep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rep)


def _turn(i, vote, vote_raw=None, g="Гарний текст.", f="Інший текст.", mode="NORMAL",
          g_ms=1000, f_ms=500, reason=None, **res_extra):
    return {
        "type": "turn", "turn_id": f"t{i}", "mode": mode, "vote": vote, "vote_raw": vote_raw,
        "reason": reason, "shown_order": ["gemma", "flash_lite"],
        "results": {
            "gemma": {"text": g, "len": len(g), "total_ms": g_ms, "attempt_ms": [g_ms],
                      "final_attempt": 1, "used_fallback": False, "error": None, **res_extra},
            "flash_lite": {"text": f, "len": len(f), "total_ms": f_ms, "attempt_ms": [f_ms],
                           "final_attempt": 1, "used_fallback": False, "error": None},
        },
    }


def _write(path, rows, raw_extra=()):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        for x in raw_extra:
            fh.write(x + "\n")


# ---------------- stats ----------------

def test_sign_test_20_of_30():
    assert rep.sign_test_one_sided(20, 30) == pytest.approx(0.0494, abs=5e-4)


def test_sign_test_edges():
    assert rep.sign_test_one_sided(0, 10) == 1.0
    assert rep.sign_test_one_sided(10, 10) == pytest.approx(1 / 1024)
    assert rep.sign_test_one_sided(0, 0) == 1.0


def test_binom_two_sided_symmetric_and_capped():
    assert rep.binom_two_sided(15, 30) == 1.0
    assert rep.binom_two_sided(3, 20) == pytest.approx(rep.binom_two_sided(17, 20))
    assert rep.binom_two_sided(0, 0) == 1.0


def test_p90():
    assert rep.percentile(list(range(1, 11)), 0.9) == 9
    assert rep.percentile([5], 0.9) == 5
    assert rep.percentile([], 0.9) is None


def test_median_mean_empty():
    assert rep.median([]) is None and rep.mean([]) is None


# ---------------- text metrics ----------------

def test_latin_share():
    assert rep.latin_share("abcd") == 1.0
    assert rep.latin_share("абвг") == 0.0
    assert rep.latin_share("аб cd") == pytest.approx(0.5)
    assert rep.latin_share("123 !!") is None


@pytest.mark.parametrize("t,exp", [
    ("привіт світе", False), ("это текст", True), ("ёж", True), ("объект", True), ("Ыы", True),
    ("їжак є", False),
])
def test_has_ru_letters(t, exp):
    assert rep.has_ru_letters(t) is exp


def test_count_cliches_case_insensitive():
    assert rep.count_cliches("Серце закалатало. СЕРЦЕ ЗАКАЛАТАЛО", ["серце закалатало"]) == 2


def test_load_cliches_skips_comments_and_blank(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("# comment\n\nОдин Два\nтри\n", encoding="utf-8")
    assert rep.load_cliches(str(p)) == ["один два", "три"]
    assert rep.load_cliches(str(tmp_path / "missing")) == []


def test_shipped_cliche_list_loads():
    assert len(rep.load_cliches(rep.DEFAULT_CLICHES)) > 5


# ---------------- loading ----------------

def test_load_log_missing_file(tmp_path):
    assert rep.load_log(str(tmp_path / "nope.jsonl")) == ([], 0, 0)


def test_load_log_empty_file(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("")
    assert rep.load_log(str(p)) == ([], 0, 0)


def test_load_log_skips_broken_lines_and_counts_notes(tmp_path):
    p = tmp_path / "l.jsonl"
    _write(p, [_turn(1, "gemma", "1"), {"type": "note", "note": "x", "turn_id": "t1"}],
           raw_extra=["{broken json", "[1,2,3]", ""])
    turns, notes, bad = rep.load_log(str(p))
    assert len(turns) == 1 and notes == 1 and bad == 2


def test_notes_do_not_affect_votes():
    base = [_turn(i, "gemma", "1") for i in range(3)]
    r1 = rep.build_report(base, 0, 0, [])
    r2 = rep.build_report(base, 5, 0, [])
    assert "Ходів у логу: 3" in r1 and "Ходів у логу: 3" in r2
    assert "Нотаток (`type:note`): 5" in r2


# ---------------- report content ----------------

def test_report_empty():
    out = rep.build_report([], 0, 0, [])
    assert "даних для звіту немає" in out


def test_report_sign_test_20_of_30_in_output():
    turns = [_turn(i, "flash_lite", "1") for i in range(20)] + [_turn(100 + i, "gemma", "2") for i in range(10)]
    out = rep.build_report(turns, 0, 0, [])
    assert "n=30" in out
    assert "p(flash_lite кращий)=0.0494" in out
    assert "ПЕРЕВОДИТИ Narrator на Flash-Lite" in out


def test_report_gemma_significant_verdict():
    turns = [_turn(i, "gemma", "1") for i in range(15)]
    assert "ЗАЛИШИТИ Gemma" in rep.build_report(turns, 0, 0, [])


def test_report_ties_excluded_from_decisive():
    turns = [_turn(i, "tie", "tie") for i in range(5)] + [_turn(10, "gemma", "1")]
    out = rep.build_report(turns, 0, 0, [])
    assert "вирішальних: 1" in out and "нічиїх: 5" in out


def test_report_vote_null_reasons():
    turns = [_turn(1, None, None, reason="ttl_expired"), _turn(2, None, None, reason="ttl_expired"),
             _turn(3, None, None, reason="abandoned")]
    out = rep.build_report(turns, 0, 0, [])
    assert "vote=null: 3" in out
    assert "reason=ttl_expired: 2" in out and "reason=abandoned: 1" in out


def test_report_position_bias_share():
    turns = [_turn(i, "gemma", "1") for i in range(8)] + [_turn(50 + i, "gemma", "2") for i in range(2)]
    out = rep.build_report(turns, 0, 0, [])
    assert "«Варіант 1» обрано 8 з 10 (80.0%)" in out


def test_report_position_bias_no_data():
    out = rep.build_report([_turn(1, None, None)], 0, 0, [])
    assert "Немає даних vote_raw" in out


def test_report_mode_split():
    turns = [_turn(1, "gemma", "1", mode="COMBAT"), _turn(2, "flash_lite", "1", mode="NORMAL"),
             _turn(3, "tie", "tie", mode="NORMAL")]
    out = rep.build_report(turns, 0, 0, [])
    assert "| NORMAL | 2 | 0 | 1 | 1 |" in out
    assert "| COMBAT | 1 | 1 | 0 | 0 |" in out


def test_report_speed_median_and_p90():
    turns = [_turn(i, "gemma", "1", g_ms=(i + 1) * 100, f_ms=50) for i in range(10)]
    out = rep.build_report(turns, 0, 0, [])
    row = next(l for l in out.splitlines() if l.startswith("| gemma | 10 |") and "1:10" in l)
    cells = [c.strip() for c in row.split("|")]
    assert cells[3] == "550" and cells[4] == "900"  # median of 100..1000, p90


def test_report_speed_fallback_and_error_percent():
    t = _turn(1, "gemma", "1")
    t["results"]["flash_lite"].update(used_fallback=True, error="boom", final_attempt="fallback")
    out = rep.build_report([t, _turn(2, "gemma", "1")], 0, 0, [])
    row = next(l for l in out.splitlines() if l.startswith("| flash_lite | 2 |") and "fallback:1" in l)
    assert "50.0" in row and "fallback:1" in row


def test_report_length_stats():
    turns = [_turn(1, "gemma", "1", g="x" * 100, f="y" * 40), _turn(2, "gemma", "1", g="x" * 200, f="y" * 60)]
    out = rep.build_report(turns, 0, 0, [])
    row = next(l for l in out.splitlines() if l.startswith("| gemma | символи |"))
    assert "150.0" in row and "100" in row and "200" in row


def test_report_longer_text_bias():
    turns = [_turn(i, "gemma", "1", g="x" * 200, f="y" * 10) for i in range(4)]
    out = rep.build_report(turns, 0, 0, [])
    assert "Довший текст переміг у 4 з 4" in out


def test_report_cliches_per_1000_chars():
    txt = "серце закалатало" + "а" * 984  # 1000 chars, 1 cliche
    assert len(txt) == 1000
    out = rep.build_report([_turn(1, "gemma", "1", g=txt)], 0, 0, ["серце закалатало"])
    row = next(l for l in out.splitlines() if l.startswith("| gemma | 1.00"))
    assert "t1: 1.00" in row


def test_report_cliches_empty_list_message():
    assert "Стоп-список кліше порожній" in rep.build_report([_turn(1, "gemma", "1")], 0, 0, [])


def test_report_language_latin_and_ru():
    turns = [_turn(1, "gemma", "1", g="abcd абвг", f="чистий український текст"),
             _turn(2, "gemma", "1", g="это текст", f="український")]
    out = rep.build_report(turns, 0, 0, [])
    g_row = next(l for l in out.splitlines() if l.startswith("| gemma |") and "%" in l and "симв" in l)
    assert "t1: 50.00%" in g_row           # worst latin share = 4/8
    assert "50.0%" in g_row                # 1 of 2 turns with ы/э/ъ/ё
    assert "t2:" in g_row and "симв." in g_row
    lang = out.split("## Мова")[1]
    f_row = next(l for l in lang.splitlines() if l.startswith("| flash_lite |"))
    assert "0.00%" in f_row and "0.0%" in f_row


def test_report_survives_missing_results_keys():
    t = {"type": "turn", "turn_id": "x", "vote": "gemma", "vote_raw": "1", "results": {}}
    assert "Narrator A/B report" in rep.build_report([t], 0, 0, [])


def test_main_end_to_end(tmp_path, capsys):
    log = tmp_path / "l.jsonl"
    _write(log, [_turn(i, "gemma", "1") for i in range(3)], raw_extra=["garbage"])
    rc = rep.main(["--log", str(log), "--cliches", str(tmp_path / "none.txt")])
    out = capsys.readouterr().out
    assert rc == 0 and "Narrator A/B report" in out and "Пошкоджених рядків пропущено: 1" in out


def test_main_missing_log(tmp_path, capsys):
    assert rep.main(["--log", str(tmp_path / "missing.jsonl")]) == 0
    assert "даних для звіту немає" in capsys.readouterr().out
