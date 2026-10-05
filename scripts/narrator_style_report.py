"""Офлайн-метрика стилю Narrator (до/після змін промпту). НЕ runtime, без LLM-викликів.

Запуск:
    python scripts/narrator_style_report.py logs/narrator_ab_YYYYMMDD_HHMM.jsonl [--model flash_lite|gemma|all]
    python scripts/narrator_style_report.py qa_logs/run_standard_*.log          # текстовий лог QA-харнесу
    python scripts/narrator_style_report.py some.json [--mode NORMAL|COMBAT]    # JSON/JSONL зі списком записів

Підтримувані входи (визначається автоматично):
  1. A/B JSONL (core/narrator_ab): `type: turn`, тексти у `results.<model>.text`, режим у `mode`.
  2. Довільний JSON/JSONL: записи з полем `text` / `narrative` / `narrator_text` / `ui_text` (+ опційно `mode`).
  3. Текстовий лог QA-харнесу: блоки після `СИСТЕМА (UI):` (best-effort, режим = --mode або NORMAL).

Метрики: частка кліше (ab_cliches.txt + додаткові патерни нижче), розподіл довжини і вихід за межі
(NORMAL 150-250 слів; COMBAT 4-6 речень), шаблонні відкриття/фінали, частка фіналів-питань до героя,
повтори триграм між ходами, латиниця/ы-э-ъ-ё, числа в тексті.
Логіку кліше/довжини/мови перевикористано з scripts/ab_report.py.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ab_report as ab  # noqa: E402

NORMAL_WORDS = (150, 250)
COMBAT_SENTENCES = (4, 6)

# Доповнення до scripts/ab_cliches.txt: звороти, виявлені в A/B 2026-10-05 (регулярні вирази, нижній регістр).
EXTRA_CLICHE_PATTERNS = [
    r"повітря (?:густі\w+|стає важк\w+|став\w* важк\w+)",
    r"тиша (?:затягу\w+|повисла|стає)",
    r"напруга (?:гусне|в залі|в повітрі|зростає)",
    r"(?:крижан\w+|холодн\w+) (?:погляд\w*|очі|спокій|гідніст\w+|зарозумілі\w+)",
    r"відчуваючи вагу",
    r"мов перед (?:кривавою )?бурею",
    r"хірургічн\w+",
]
_EXTRA_RE = [re.compile(p) for p in EXTRA_CLICHE_PATTERNS]

# Прямі питання до героя наприкінці (кліше-кінцівка).
_QUESTION_END_RE = re.compile(r"(що (?:ви|ти) (?:зробите|скажете|відповісте|робите)|ваш наступний крок|чи (?:наважитесь|зробите|готові)[^?]*\?)", re.I)
_DIGIT_RE = re.compile(r"\d")
# Характерні слова мікро-прикладу зі статики Narrator (виявлення копіювання на E2E).
_EXAMPLE_WORDS_RE = re.compile(r"ключ\w*|відр\w+|сало|салом|салі|поріг\w*", re.I)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_TEXT_KEYS = ("text", "narrative", "narrator_text", "ui_text")


# ---------- loading ----------

def _from_ab_turn(row: dict, model_filter: str) -> list[dict]:
    out = []
    for m, res in (row.get("results") or {}).items():
        if model_filter != "all" and m != model_filter:
            continue
        if isinstance(res, dict) and res.get("text"):
            out.append({"id": f"{row.get('turn_id')}:{m}", "model": m, "mode": row.get("mode") or "NORMAL",
                        "text": res["text"]})
    return out


def _from_generic(row: dict, default_mode: str, idx: int) -> list[dict]:
    for k in _TEXT_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return [{"id": str(row.get("turn_id") or row.get("turn") or idx), "model": row.get("model") or "-",
                     "mode": row.get("mode") or default_mode, "text": v}]
    return []


def _from_qa_log(raw: str, default_mode: str) -> list[dict]:
    blocks = re.split(r"СИСТЕМА \(UI\):\s*", raw)[1:]
    out = []
    for i, b in enumerate(blocks):
        # блок закінчується на наступному рядку з таймстемпом/розділювачем
        m = re.search(r"\n(?:\d{4}-\d{2}-\d{2}|={5,}|📜|⚔️|🤖)", b)
        txt = (b[:m.start()] if m else b).strip()
        if txt:
            out.append({"id": str(i + 1), "model": "-", "mode": default_mode, "text": txt})
    return out


def load_items(path: str, model_filter: str, default_mode: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    items: list[dict] = []
    records: list = []
    stripped = raw.lstrip()
    if stripped.startswith("["):
        try:
            records = json.loads(raw)
        except json.JSONDecodeError:
            records = []
    elif stripped.startswith("{"):
        obj = None
        try:
            obj = json.loads(raw)  # один JSON-об'єкт (напр. logs/prompt_metrics_*.json)
        except json.JSONDecodeError:
            pass
        if isinstance(obj, dict):
            if isinstance(obj.get("profile_runs"), list):
                for run in obj["profile_runs"]:
                    for rec in (run or {}).get("turn_records") or []:
                        if isinstance(rec, dict):
                            rec = dict(rec)
                            rec["turn_id"] = f"{rec.get('profile') or (run or {}).get('profile')}:{rec.get('turn')}"
                            rec["mode"] = rec.get("mode_before") or rec.get("mode") or rec.get("mode_after")
                            records.append(rec)
            else:
                records = obj.get("turns", [obj])
        else:  # JSONL (по рядку)
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    else:
        return _from_qa_log(raw, default_mode)
    for i, row in enumerate(records):
        if not isinstance(row, dict) or row.get("type") == "note":
            continue
        if isinstance(row.get("results"), dict) and row.get("type", "turn") == "turn":
            items += _from_ab_turn(row, model_filter)
        else:
            items += _from_generic(row, default_mode, i)
    return items


# ---------- metrics ----------

def words(text: str) -> list[str]:
    return ab._WORD_RE.findall(text)


def sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT_RE.split(text.strip()) if s.strip()]


def opening(text: str, n: int = 3) -> str:
    w = [x.lower() for x in words(text)[:n]]
    return " ".join(w)


def closing(text: str, n: int = 3) -> str:
    s = sentences(text)
    return opening(s[-1], n) if s else ""


def extra_cliche_count(text: str) -> int:
    low = text.lower()
    return sum(len(r.findall(low)) for r in _EXTRA_RE)


def repeated_trigrams(texts: list[str], min_count: int = 3, top: int = 8) -> list[tuple[str, int]]:
    """Триграми, що трапляються у >= min_count РІЗНИХ текстах (повтори між ходами)."""
    docs = collections.Counter()
    for t in texts:
        w = [x.lower() for x in words(t)]
        docs.update({" ".join(w[i:i + 3]) for i in range(len(w) - 2)})
    return [(g, c) for g, c in docs.most_common(60) if c >= min_count][:top]


def pct(a: int, n: int) -> str:
    return f"{100 * a / n:.0f}%" if n else "-"


def group_report(label: str, items: list[dict], cliches: list[str]) -> list[str]:
    n = len(items)
    L = [f"## {label} (n={n})", ""]
    if not n:
        return L + ["Немає текстів.", ""]
    texts = [it["text"] for it in items]
    normal = [it for it in items if str(it["mode"]).upper() != "COMBAT"]
    combat = [it for it in items if str(it["mode"]).upper() == "COMBAT"]

    cl_total = [ab.count_cliches(t, cliches) + extra_cliche_count(t) for t in texts]
    per1k = [c * 1000 / max(len(t), 1) for c, t in zip(cl_total, texts)]
    L.append(f"- Кліше на 1000 символів: mean {ab.fmt(ab.mean(per1k), 2)}; ходів із >=1 кліше: "
             f"{pct(sum(1 for c in cl_total if c), n)}")

    L.append(f"- Числа в тексті: {pct(sum(1 for t in texts if _DIGIT_RE.search(t)), n)}; "
             f"латиниця mean {100 * (ab.mean([v for v in (ab.latin_share(t) for t in texts) if v is not None]) or 0):.2f}%; "
             f"ы/э/ъ/ё: {pct(sum(1 for t in texts if ab.has_ru_letters(t)), n)}")

    if normal:
        wc = [len(words(it["text"])) for it in normal]
        out = sum(1 for x in wc if not (NORMAL_WORDS[0] <= x <= NORMAL_WORDS[1]))
        below = sum(1 for x in wc if x < NORMAL_WORDS[0])
        L.append(f"- NORMAL ({len(normal)}): слів min/median/max = {min(wc)}/{ab.fmt(ab.median(wc), 0)}/{max(wc)}; "
                 f"поза {NORMAL_WORDS[0]}-{NORMAL_WORDS[1]}: {pct(out, len(wc))} (з них <{NORMAL_WORDS[0]}: {pct(below, len(wc))})")
    if combat:
        sc = [len(sentences(it["text"])) for it in combat]
        out = sum(1 for x in sc if not (COMBAT_SENTENCES[0] <= x <= COMBAT_SENTENCES[1]))
        L.append(f"- COMBAT ({len(combat)}): речень min/median/max = {min(sc)}/{ab.fmt(ab.median(sc), 0)}/{max(sc)}; "
                 f"поза {COMBAT_SENTENCES[0]}-{COMBAT_SENTENCES[1]}: {pct(out, len(sc))}")

    ex = sum(1 for t in texts if _EXAMPLE_WORDS_RE.search(t))
    L.append(f"- Лексика мікро-прикладу (ключ/відро/сало/поріг): {ex} з {n} текстів ({pct(ex, n)})")

    q_mark = sum(1 for t in texts if t.strip().endswith("?"))
    q_hero = sum(1 for t in texts if _QUESTION_END_RE.search(sentences(t)[-1] if sentences(t) else ""))
    L.append(f"- Фінал закінчується '?': {pct(q_mark, n)}; фінал-питання до героя (шаблон): {pct(q_hero, n)}")

    sent_len = [len(words(s)) for t in texts for s in sentences(t)]
    if sent_len:
        short = sum(1 for x in sent_len if x <= 6)
        L.append(f"- Ритм: середня довжина речення {ab.fmt(ab.mean(sent_len))} слів; коротких (<=6) {pct(short, len(sent_len))}")

    L += ["", "Топ шаблонних відкриттів (перші 3 слова):"]
    for g, c in collections.Counter(opening(t) for t in texts).most_common(5):
        if c > 1 or n <= 6:
            L.append(f"  - {c}x «{g}»")
    L += ["Топ шаблонних фіналів (перші 3 слова останнього речення):"]
    for g, c in collections.Counter(closing(t) for t in texts).most_common(5):
        if c > 1 or n <= 6:
            L.append(f"  - {c}x «{g}»")
    rep = repeated_trigrams(texts)
    L += ["Триграми, що повторюються у >=3 різних текстах:"]
    L += [f"  - {c}x «{g}»" for g, c in rep] or ["  - немає"]
    L.append("")
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Narrator style metrics (offline, no LLM calls)")
    ap.add_argument("log", nargs="+", help="A/B JSONL, JSON/JSONL із текстами або текстовий QA-лог (підтримує glob)")
    ap.add_argument("--model", default="all", help="для A/B JSONL: gemma | flash_lite | all (за замовч. all, звіт по кожній)")
    ap.add_argument("--mode", default="NORMAL", help="режим за замовч., якщо запис його не містить")
    ap.add_argument("--cliches", default=ab.DEFAULT_CLICHES)
    args = ap.parse_args(argv)

    paths = [p for pat in args.log for p in (glob.glob(pat) or [pat])]
    cliches = ab.load_cliches(args.cliches)
    items: list[dict] = []
    for p in paths:
        if not os.path.isfile(p):
            print(f"Файл не знайдено: {p}", file=sys.stderr)
            continue
        items += load_items(p, args.model, args.mode.upper())
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print("# Narrator style report\n")
    print(f"- Джерела: {', '.join(paths)}; текстів: {len(items)}; кліше у списку: {len(cliches)} + {len(_EXTRA_RE)} патернів\n")
    if not items:
        print("Текстів для аналізу не знайдено.")
        return 1
    by_model = collections.OrderedDict()
    for it in items:
        by_model.setdefault(it["model"], []).append(it)
    if len(by_model) > 1:
        for m, its in by_model.items():
            print("\n".join(group_report(f"Модель {m}", its, cliches)))
    else:
        print("\n".join(group_report("Усі тексти", items, cliches)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
