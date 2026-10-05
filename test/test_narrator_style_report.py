"""scripts/narrator_style_report.py: offline metrics + CLI. Synthetic data only."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import narrator_style_report as R  # noqa: E402
import ab_report as ab  # noqa: E402

CL = ab.load_cliches(ab.DEFAULT_CLICHES)


def _words(n):
    return " ".join("слово" for _ in range(n))


def _item(text, mode="NORMAL", i="1"):
    return {"id": i, "model": "m", "mode": mode, "text": text}


def _report(items):
    return "\n".join(R.group_report("t", items, CL))


def test_cliche_file_loaded():
    assert "тиша повисла" in CL


@pytest.mark.parametrize("text", [
    "Повітря густішає навколо.", "Повітря стає важким.", "Тиша затягується.", "Тиша повисла.",
    "Напруга гусне.", "Крижаний погляд.", "Холодні очі.", "Відчуваючи вагу рішення.",
    "Мов перед бурею.", "Хірургічна точність.",
])
def test_extra_patterns_match(text):
    assert R.extra_cliche_count(text) >= 1


def test_extra_patterns_clean_text_zero():
    assert R.extra_cliche_count("Двері риплять. Хтось кашляє за стіною.") == 0


def test_ab_cliche_counted_in_report():
    out = _report([_item("Серце закалатало. " + _words(160))])
    assert "ходів із >=1 кліше: 100%" in out


def test_clean_text_zero_cliches():
    out = _report([_item("Двері риплять. " + _words(160))])
    assert "ходів із >=1 кліше: 0%" in out


def test_words_and_sentences():
    assert len(R.words("раз два три")) == 3
    assert R.sentences("Раз. Два! Три? Чотири…") == ["Раз.", "Два!", "Три?", "Чотири…"]
    assert R.sentences("") == []


def test_opening_closing():
    assert R.opening("Ключ повертається в замку") == "ключ повертається в"
    assert R.closing("Перше. Двері зачинились назавжди.") == "двері зачинились назавжди"
    assert R.closing("") == ""


def test_normal_length_bounds():
    items = [_item(_words(n) + ".", i=str(n)) for n in (100, 200, 300)]
    out = _report(items)
    assert "поза 150-250: 67%" in out and "<150: 33%" in out
    assert "min/median/max = 100/200/300" in out


def test_combat_sentence_bounds():
    out = _report([_item("А. Б. В.", "COMBAT", "1"), _item("А. Б. В. Г. Д.", "COMBAT", "2")])
    assert "COMBAT (2)" in out and "поза 4-6: 50%" in out
    assert "NORMAL (" not in out


def test_mode_split_case_insensitive():
    out = _report([_item("А. Б. В. Г.", "combat", "1"), _item(_words(200) + ".", "NORMAL", "2")])
    assert "COMBAT (1)" in out and "NORMAL (1)" in out


def test_final_question_metrics():
    out = _report([_item("Він мовчить. Що ви зробите?", i="1"), _item("Він мовчить. Ти хто?", i="2"),
                   _item("Він мовчить.", i="3")])
    assert "закінчується '?': 67%" in out
    assert "фінал-питання до героя (шаблон): 33%" in out


def test_repeated_trigrams_counts_documents_not_occurrences():
    assert R.repeated_trigrams(["а б в а б в а б в", "г д е", "ж з и"], min_count=2) == []
    assert ("а б в", 3) in R.repeated_trigrams(["а б в", "а б в г", "а б в д"], min_count=3)


def test_pct():
    assert R.pct(1, 4) == "25%" and R.pct(0, 0) == "-"


def test_group_report_empty():
    assert "Немає текстів" in _report([])


def test_repeated_openings_reported():
    out = _report([_item("Двері риплять рано.", i=str(i)) for i in range(3)])
    assert "3x «двері риплять рано»" in out


# ---------- loading / CLI ----------

def _ab_row(i, mode="NORMAL"):
    return {"type": "turn", "turn_id": f"t{i}", "mode": mode, "results": {
        "gemma": {"text": _words(180) + ".", "len": 1},
        "flash_lite": {"text": "Тиша повисла. " + _words(180) + "."}}}


def _write_jsonl(p, rows):
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def test_load_ab_jsonl_all_and_filter(tmp_path):
    p = tmp_path / "ab.jsonl"
    _write_jsonl(p, [_ab_row(1), _ab_row(2, "COMBAT")])
    items = R.load_items(str(p), "all", "NORMAL")
    assert len([i for i in items if i["model"] == "gemma"]) == 2
    assert {"NORMAL", "COMBAT"} <= {i["mode"] for i in items}
    only = R.load_items(str(p), "flash_lite", "NORMAL")
    assert {i["model"] for i in only} == {"flash_lite"} and len(only) == 2


def test_cli_ab_jsonl(tmp_path, capsys):
    p = tmp_path / "ab.jsonl"
    _write_jsonl(p, [_ab_row(1), _ab_row(2)])
    assert R.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "Модель gemma" in out and "Модель flash_lite" in out
    assert "# Narrator style report" in out


def test_cli_model_filter_single_group(tmp_path, capsys):
    p = tmp_path / "ab.jsonl"
    _write_jsonl(p, [_ab_row(1)])
    assert R.main([str(p), "--model", "flash_lite"]) == 0
    out = capsys.readouterr().out
    assert "Усі тексти" in out and "ходів із >=1 кліше: 100%" in out


@pytest.mark.parametrize("key", ["text", "narrative", "narrator_text", "ui_text"])
def test_load_json_list_text_keys(tmp_path, key):
    p = tmp_path / "x.json"
    p.write_text(json.dumps([{key: "Двері риплять.", "mode": "COMBAT"}, {"other": "no text"}, "junk"]),
                 encoding="utf-8")
    items = R.load_items(str(p), "all", "NORMAL")
    assert len(items) == 1 and items[0]["mode"] == "COMBAT"


def test_load_prompt_metrics_style_turns(tmp_path):
    p = tmp_path / "pm.json"
    p.write_text(json.dumps({"turns": [{"turn": 1, "ui_text": "Двері риплять."},
                                       {"turn": 2, "ui_text": "Хтось кашляє."}]}), encoding="utf-8")
    items = R.load_items(str(p), "all", "NORMAL")
    assert [i["id"] for i in items] == ["1", "2"]
    assert all(i["mode"] == "NORMAL" for i in items)


def test_cli_json_with_default_mode(tmp_path, capsys):
    p = tmp_path / "pm.json"
    p.write_text(json.dumps([{"ui_text": "А. Б. В. Г."}]), encoding="utf-8")
    assert R.main([str(p), "--mode", "combat"]) == 0
    assert "COMBAT (1)" in capsys.readouterr().out


def test_load_qa_log(tmp_path):
    p = tmp_path / "qa.log"
    p.write_text("шапка\nСИСТЕМА (UI):\nПерший текст.\n=====\nСИСТЕМА (UI):\nДругий текст.\n", encoding="utf-8")
    items = R.load_items(str(p), "all", "NORMAL")
    assert [i["text"] for i in items] == ["Перший текст.", "Другий текст."]


@pytest.mark.parametrize("content", ["", "[]", "[1, 2]", "[not json"])
def test_cli_empty_or_bad_input_does_not_crash(tmp_path, capsys, content):
    p = tmp_path / "e.json"
    p.write_text(content, encoding="utf-8")
    assert R.main([str(p)]) == 1
    assert "не знайдено" in capsys.readouterr().out


def test_cli_missing_file(tmp_path, capsys):
    assert R.main([str(tmp_path / "nope.jsonl")]) == 1
    assert "Файл не знайдено" in capsys.readouterr().err


# ---------- prompt_metrics pretty-printed JSON ----------

def _pm_doc():
    return {"label": "x", "profile_runs": [
        {"profile": "knight", "turn_records": [
            {"profile": "knight", "turn": 1, "mode_before": "COMBAT", "mode_after": "NORMAL", "ui_text": "А. Б. В. Г."},
            {"profile": "knight", "turn": 2, "mode_before": "NORMAL", "ui_text": "Двері риплять."},
            {"turn": 3, "mode": "COMBAT", "ui_text": "Удар."},
            {"turn": 4, "mode_after": "COMBAT", "ui_text": "Ще удар."},
            {"turn": 5, "ui_text": ""},
        ]},
        {"profile": "maester", "turn_records": [{"turn": 1, "ui_text": "Хтось кашляє."}]},
        None,
    ]}


def test_load_prompt_metrics_profile_runs(tmp_path):
    p = tmp_path / "prompt_metrics_x.json"
    p.write_text(json.dumps(_pm_doc(), ensure_ascii=False, indent=2), encoding="utf-8")
    items = R.load_items(str(p), "all", "NORMAL")
    by_id = {i["id"]: i for i in items}
    assert by_id["knight:1"]["mode"] == "COMBAT"
    assert by_id["knight:2"]["mode"] == "NORMAL"
    assert by_id["knight:3"]["mode"] == "COMBAT"
    assert by_id["knight:4"]["mode"] == "COMBAT"
    assert by_id["maester:1"]["mode"] == "NORMAL"
    assert "knight:5" not in by_id and len(items) == 5


def test_cli_prompt_metrics_pretty_json(tmp_path, capsys):
    p = tmp_path / "prompt_metrics_x.json"
    p.write_text(json.dumps(_pm_doc(), ensure_ascii=False, indent=2), encoding="utf-8")
    assert R.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "текстів: 5" in out and "COMBAT (3)" in out and "NORMAL (2)" in out


def test_pretty_printed_ab_object_and_jsonl_ab(tmp_path):
    row = _ab_row(1)
    p1 = tmp_path / "one.json"
    p1.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(R.load_items(str(p1), "all", "NORMAL")) == 2
    p2 = tmp_path / "two.jsonl"
    _write_jsonl(p2, [_ab_row(1), _ab_row(2, "COMBAT")])
    items = R.load_items(str(p2), "all", "NORMAL")
    assert len(items) == 4 and {i["mode"] for i in items} == {"NORMAL", "COMBAT"}
