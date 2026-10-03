"""Офлайн-звіт по Narrator A/B (Gemma vs Flash-Lite). НЕ runtime.

УВАГА: звіт РОЗСЛІПЛЮЄ моделі (показує назви gemma / flash_lite). Запускати лише адміну
ПІСЛЯ завершення тесту, не показувати тестувальникам до кінця.

Запуск:
    python scripts/ab_report.py [--log logs/narrator_ab.jsonl] [--cliches scripts/ab_cliches.txt]

Markdown іде в stdout. Залежності: лише stdlib. LLM-викликів немає.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys

MODELS = ("gemma", "flash_lite")
ALPHA = 0.05
DEFAULT_LOG = "logs/narrator_ab.jsonl"
DEFAULT_CLICHES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab_cliches.txt")

_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_LATIN_RE = re.compile(r"[A-Za-z]")
_RU_RE = re.compile(r"[ыэъёЫЭЪЁ]")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


# ---------- stats helpers ----------

def sign_test_one_sided(wins: int, n: int) -> float:
    """P(X >= wins) для X ~ Bin(n, 0.5)."""
    if n <= 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / (2 ** n)


def binom_two_sided(k: int, n: int) -> float:
    """Точний двосторонній біноміальний p проти 0.5 (симетрична гіпотеза)."""
    if n <= 0:
        return 1.0
    lo = min(k, n - k)
    p = 2 * sum(math.comb(n, i) for i in range(0, lo + 1)) / (2 ** n)
    return min(1.0, p)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return s[idx]


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def fmt(x, digits: int = 1) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


# ---------- loading ----------

def load_log(path: str) -> tuple[list[dict], int, int]:
    """Повертає (turns, notes_count, bad_lines). Відсутній файл = порожньо."""
    turns: list[dict] = []
    notes = 0
    bad = 0
    if not os.path.isfile(path):
        return turns, notes, bad
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(row, dict):
                bad += 1
                continue
            t = row.get("type", "turn")
            if t == "note":
                notes += 1
            elif t == "turn":
                turns.append(row)
    return turns, notes, bad


def load_cliches(path: str) -> list[str]:
    if not path or not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip().lower()
            if s and not s.startswith("#"):
                out.append(s)
    return out


# ---------- text metrics ----------

def count_cliches(text: str, cliches: list[str]) -> int:
    low = text.lower()
    return sum(low.count(c) for c in cliches)


def latin_share(text: str) -> float | None:
    letters = _LETTER_RE.findall(text)
    if not letters:
        return None
    return len(_LATIN_RE.findall(text)) / len(letters)


def has_ru_letters(text: str) -> bool:
    return bool(_RU_RE.search(text))


def _texts_for(turns: list[dict], model: str) -> list[tuple[dict, dict, str]]:
    """(turn, result, text) для ходів, де є непорожній текст моделі."""
    out = []
    for t in turns:
        r = (t.get("results") or {}).get(model)
        if isinstance(r, dict) and r.get("text"):
            out.append((t, r, r["text"]))
    return out


# ---------- sections ----------

def section_votes(turns: list[dict]) -> list[str]:
    L = ["## Голоси", ""]
    voted = [t for t in turns if t.get("vote") in (*MODELS, "tie")]
    ties = [t for t in voted if t["vote"] == "tie"]
    decided = [t for t in voted if t["vote"] in MODELS]
    nulls = [t for t in turns if t.get("vote") is None]
    L.append(f"- Ходів у логу: {len(turns)}")
    L.append(f"- Голосів: {len(voted)} (вирішальних: {len(decided)}, нічиїх: {len(ties)})")
    L.append(f"- vote=null: {len(nulls)}")
    if nulls:
        by_reason: dict[str, int] = {}
        for t in nulls:
            by_reason[str(t.get("reason"))] = by_reason.get(str(t.get("reason")), 0) + 1
        for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            L.append(f"  - reason={k}: {v}")
    L.append("")

    n = len(decided)
    wins = {m: sum(1 for t in decided if t["vote"] == m) for m in MODELS}
    L.append("### Частка перемог (без нічиїх)")
    L.append("")
    L.append("| Модель | Перемог | Частка |")
    L.append("|---|---|---|")
    for m in MODELS:
        share = f"{100 * wins[m] / n:.1f}%" if n else "-"
        L.append(f"| {m} | {wins[m]} | {share} |")
    L.append("")

    L.append("### Sign test (односторонній, H0: p=0.5)")
    L.append("")
    p_fl = sign_test_one_sided(wins["flash_lite"], n)
    p_gm = sign_test_one_sided(wins["gemma"], n)
    L.append(f"- n={n}; p(flash_lite кращий)={p_fl:.4f}; p(gemma кращий)={p_gm:.4f}; alpha={ALPHA}")
    L.append(f"- **Вердикт:** {verdict(wins['flash_lite'], wins['gemma'], len(ties), p_fl, p_gm)}")
    L.append("")

    L.append("### Позиційне зміщення")
    L.append("")
    raw = [t for t in voted if t.get("vote_raw") in ("1", "2", "tie")]
    raw12 = [t for t in raw if t["vote_raw"] in ("1", "2")]
    k1 = sum(1 for t in raw12 if t["vote_raw"] == "1")
    if raw12:
        L.append(f"- «Варіант 1» обрано {k1} з {len(raw12)} ({100 * k1 / len(raw12):.1f}%); "
                 f"двосторонній біноміальний p={binom_two_sided(k1, len(raw12)):.4f}")
    else:
        L.append("- Немає даних vote_raw")
    L.append("")

    L.append("### NORMAL vs COMBAT")
    L.append("")
    L.append("| Режим | Рішень | gemma | flash_lite | нічиї |")
    L.append("|---|---|---|---|---|")
    for mode in ("NORMAL", "COMBAT"):
        sub = [t for t in voted if t.get("mode") == mode]
        g = sum(1 for t in sub if t["vote"] == "gemma")
        f = sum(1 for t in sub if t["vote"] == "flash_lite")
        tt = sum(1 for t in sub if t["vote"] == "tie")
        L.append(f"| {mode} | {len(sub)} | {g} | {f} | {tt} |")
    L.append("")
    return L


def verdict(fl_wins: int, gm_wins: int, ties: int, p_fl: float, p_gm: float) -> str:
    n = fl_wins + gm_wins
    if n == 0:
        return "недостатньо даних (немає вирішальних голосів)"
    if p_fl < ALPHA:
        return "Flash-Lite значуще виграє -> ПЕРЕВОДИТИ Narrator на Flash-Lite"
    if p_gm < ALPHA:
        return "Gemma значуще виграє -> ЗАЛИШИТИ Gemma"
    if fl_wins >= gm_wins:
        return ("різниця незначуща, Flash-Lite не гірший (нічия/перевага) -> можна ПЕРЕВОДИТИ "
                "(швидше/дешевше), за умови що швидкість/мова/кліше нижче не гірші")
    return "різниця незначуща, Gemma трохи попереду -> ЗАЛИШИТИ Gemma (або збільшити вибірку)"


def section_speed(turns: list[dict]) -> list[str]:
    L = ["## Швидкість", "",
         "| Модель | n | median total_ms | p90 total_ms | median attempt-1 ms | fallback % | error % | final_attempt |",
         "|---|---|---|---|---|---|---|---|"]
    for m in MODELS:
        rs = [t["results"][m] for t in turns
              if isinstance(t.get("results"), dict) and isinstance(t["results"].get(m), dict)]
        totals = [r["total_ms"] for r in rs if isinstance(r.get("total_ms"), (int, float))]
        a1 = [r["attempt_ms"][0] for r in rs if r.get("attempt_ms")]
        fb = sum(1 for r in rs if r.get("used_fallback"))
        er = sum(1 for r in rs if r.get("error"))
        dist: dict[str, int] = {}
        for r in rs:
            k = str(r.get("final_attempt"))
            dist[k] = dist.get(k, 0) + 1
        dist_s = ", ".join(f"{k}:{v}" for k, v in sorted(dist.items())) or "-"
        n = len(rs)
        L.append(f"| {m} | {n} | {fmt(median(totals), 0)} | {fmt(percentile(totals, 0.9), 0)} | "
                 f"{fmt(median(a1), 0)} | {fmt(100 * fb / n if n else None)} | "
                 f"{fmt(100 * er / n if n else None)} | {dist_s} |")
    L.append("")
    return L


def section_length(turns: list[dict]) -> list[str]:
    L = ["## Довжина", "",
         "| Модель | Метрика | mean | median | min | max |",
         "|---|---|---|---|---|---|"]
    for m in MODELS:
        items = _texts_for(turns, m)
        chars = [len(txt) for _, _, txt in items]
        words = [len(_WORD_RE.findall(txt)) for _, _, txt in items]
        for name, vals in (("символи", chars), ("слова", words)):
            L.append(f"| {m} | {name} | {fmt(mean(vals))} | {fmt(median(vals))} | "
                     f"{fmt(min(vals) if vals else None)} | {fmt(max(vals) if vals else None)} |")
    L.append("")
    longer_won = 0
    cmp_n = 0
    for t in turns:
        if t.get("vote") not in MODELS:
            continue
        res = t.get("results") or {}
        try:
            la = len(res["gemma"]["text"] or "")
            lb = len(res["flash_lite"]["text"] or "")
        except (KeyError, TypeError):
            continue
        if la == lb:
            continue
        cmp_n += 1
        longer = "gemma" if la > lb else "flash_lite"
        if t["vote"] == longer:
            longer_won += 1
    if cmp_n:
        L.append(f"- Довший текст переміг у {longer_won} з {cmp_n} вирішальних голосів "
                 f"({100 * longer_won / cmp_n:.1f}%); двосторонній p={binom_two_sided(longer_won, cmp_n):.4f}")
    else:
        L.append("- Немає вирішальних голосів для перевірки тяжіння до довжини")
    L.append("")
    return L


def section_cliches(turns: list[dict], cliches: list[str]) -> list[str]:
    L = ["## Кліше (на 1000 символів)", ""]
    if not cliches:
        L += ["Стоп-список кліше порожній або не знайдений.", ""]
        return L
    L += ["| Модель | mean | найгірший хід (turn_id: значення) |", "|---|---|---|"]
    for m in MODELS:
        vals = []
        for t, _, txt in _texts_for(turns, m):
            vals.append((count_cliches(txt, cliches) * 1000 / max(len(txt), 1), t.get("turn_id")))
        if vals:
            worst = max(vals, key=lambda v: v[0])
            L.append(f"| {m} | {mean([v[0] for v in vals]):.2f} | {worst[1]}: {worst[0]:.2f} |")
        else:
            L.append(f"| {m} | - | - |")
    L.append("")
    return L


def section_language(turns: list[dict]) -> list[str]:
    L = ["## Мова", "",
         "| Модель | Латиниця (частка літер), mean | worst | Ходи з ы/э/ъ/ё | worst turn (ы/э/ъ/ё) |",
         "|---|---|---|---|---|"]
    for m in MODELS:
        items = _texts_for(turns, m)
        lat = [(latin_share(txt), t.get("turn_id")) for t, _, txt in items]
        lat = [(v, tid) for v, tid in lat if v is not None]
        ru = [(len(_RU_RE.findall(txt)), t.get("turn_id")) for t, _, txt in items]
        ru_share = (100 * sum(1 for c, _ in ru if c > 0) / len(ru)) if ru else None
        lat_mean = f"{100 * mean([v for v, _ in lat]):.2f}%" if lat else "-"
        if lat:
            w = max(lat, key=lambda v: v[0])
            lat_worst = f"{w[1]}: {100 * w[0]:.2f}%"
        else:
            lat_worst = "-"
        ru_worst = "-"
        if ru:
            w = max(ru, key=lambda v: v[0])
            ru_worst = f"{w[1]}: {w[0]} симв." if w[0] > 0 else "-"
        L.append(f"| {m} | {lat_mean} | {lat_worst} | "
                 f"{fmt(ru_share)}% | {ru_worst} |" if ru_share is not None else
                 f"| {m} | {lat_mean} | {lat_worst} | - | - |")
    L.append("")
    return L


def build_report(turns: list[dict], notes: int, bad: int, cliches: list[str], log_path: str = "") -> str:
    L = ["# Narrator A/B report", "",
         "> Звіт розсліплює моделі (gemma / flash_lite). Лише для адміна, після завершення тесту.", ""]
    if log_path:
        L.append(f"- Лог: `{log_path}`")
    L.append(f"- Нотаток (`type:note`): {notes}")
    if bad:
        L.append(f"- Пошкоджених рядків пропущено: {bad}")
    L.append("")
    if not turns:
        L += ["Лог порожній або відсутній: даних для звіту немає.", ""]
        return "\n".join(L)
    L += section_votes(turns)
    L += section_speed(turns)
    L += section_length(turns)
    L += section_cliches(turns, cliches)
    L += section_language(turns)
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Narrator A/B report (unblinds models; admin-only)")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--cliches", default=DEFAULT_CLICHES)
    args = ap.parse_args(argv)
    turns, notes, bad = load_log(args.log)
    cliches = load_cliches(args.cliches)
    out = build_report(turns, notes, bad, cliches, args.log)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
