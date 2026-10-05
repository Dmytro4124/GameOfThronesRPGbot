"""Prompt-optimization baseline runner (harness only, NOT runtime).

Wraps qa_auto_test.run_test with non-invasive hooks (no edits to core/):
  * class-level patch of core.ai_client.AIWrapper.generate_content  -> per-call role/latency/parse/usage
  * instance patch of the SDK client's models.generate_content       -> raw SDK calls (retries) + usage_metadata
  * logging handler                                                   -> fallback markers
  * patch of RPGTesterAdapter.process                                 -> per-turn latency/DC/actions/mode

Usage (from repo root):
  .venv/Scripts/python.exe scripts/prompt_metrics_runner.py --label baseline --profiles standard,combat_stress_tester --turns 15
  (--no-eval disables LLM evaluator; --table logs/prompt_metrics_<label>.json re-prints the table)
Output: logs/prompt_metrics_<label>.json
"""
import argparse
import asyncio
import json
import logging
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime

for _s in (sys.stdout, sys.stderr):  # works in plain PowerShell without PYTHONUTF8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

LEGAL_DCS = {2, 5, 10, 12, 15, 17, 20, 22}

FALLBACK_PATTERNS = {
    "worker_auto_success": "falling back to AUTO_SUCCESS",
    "gm_logic_minimal": "[GM_Logic] using minimal fallback",
    "narrator_fail": "[NARRATOR_FAIL]",
    "schema_rejected": "response_schema rejected",
    "worker_unparseable_retry": "empty/unparseable Worker response",
    "worker_llm_call_failed": "[DND_ENGINE] LLM call failed",
}

# [CACHE] events from core/ai_client.py: most are print() (stdout), some logger.warning -> both captured.
CACHE_PATTERNS = {
    "cache_created": ": created ",
    "cache_inline_fallback": "inline fallback",
    "cache_upgrade_failed": "upgrade failed, inline",
    "cache_invalid_retry": "cached content invalid/expired",
}

_tls = threading.local()
_lock = threading.Lock()


class Collector:
    def __init__(self):
        self.calls = []        # one dict per AIWrapper.generate_content call
        self.fallbacks = []    # {turn, kind, msg}
        self.cache_events = []  # {turn, kind, msg} from [CACHE] lines
        self.turns = []
        self.cur_turn = 0

    def add_cache_line(self, line):
        if "[CACHE]" not in line:
            return
        for kind, pat in CACHE_PATTERNS.items():
            if pat in line:
                with _lock:
                    self.cache_events.append({"turn": self.cur_turn, "kind": kind, "msg": line.strip()[:200]})
                return

    def add_call(self, rec):
        with _lock:
            rec["turn"] = self.cur_turn
            self.calls.append(rec)


COL = Collector()


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(s[k], 3)


def _usage(raw):
    u = getattr(raw, "usage_metadata", None)
    if u is None:
        return None
    g = lambda n: getattr(u, n, None) or 0
    return {
        "prompt": g("prompt_token_count"),
        "output": g("candidates_token_count"),
        "thoughts": g("thoughts_token_count"),
        "cached": g("cached_content_token_count"),
        "total": g("total_token_count"),
    }


def _system_chars(cfg, wrapper):
    """Length of system_instruction (config-level, falls back to wrapper-level); 0 if none/non-str."""
    si = getattr(cfg, "system_instruction", None) if cfg is not None else getattr(wrapper, "system_instruction", None)
    return len(si) if isinstance(si, str) else 0


class _StdoutTee:
    """Pass-through stdout wrapper that scans complete lines for [CACHE] events (ai_client uses print)."""

    def __init__(self, inner):
        self._inner = inner
        self._buf = ""

    def write(self, s):
        n = self._inner.write(s)
        try:
            self._buf += s
            if "\n" in self._buf:
                *lines, self._buf = self._buf.split("\n")
                for ln in lines:
                    COL.add_cache_line(ln)
        except Exception:
            pass
        return n

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _classify(wrapper, ai):
    if wrapper is ai.model_gm_logic:
        return "GM_Logic"
    if wrapper is ai.model_narrator:
        return "Narrator"
    if wrapper is ai.model_narrator_alt:
        return "Narrator_alt"
    if wrapper is ai.model:
        return "Main"
    # model_worker: distinguish by calling core frame
    f = sys._getframe(2)
    while f is not None:
        fn = os.path.basename(f.f_code.co_filename)
        qn = getattr(f.f_code, "co_qualname", f.f_code.co_name)
        if os.path.dirname(f.f_code.co_filename).endswith("core") and fn != "ai_client.py":
            if fn == "mechanics.py":
                return "Censor" if "validate_action" in qn else "Worker_training"
            if fn == "dnd_engine.py":
                return "Worker"
            if fn == "dnd_combat_engine.py":
                return "Worker_COMBAT_npc" if "npc" in qn.lower() else "Worker_COMBAT"
            if fn == "engine.py":
                return "Summarizer"
            return f"Worker_other:{fn}:{qn}"
        f = f.f_back
    return "Worker_unknown"


JSON_ROLES = {"Censor", "Worker", "GM_Logic", "Worker_COMBAT", "Worker_COMBAT_npc", "Worker_training"}


def install_hooks():
    import core.ai_client as ai

    orig_gc = ai.AIWrapper.generate_content

    def patched(self, prompt, *a, **kw):
        role = _classify(self, ai)
        rec = {"role": role, "model": self.model_name, "raw_calls": 0, "usage": [],
               "parse_ok": None, "exception": None, "prompt_chars": len(ai._normalize_prompt(prompt)) if prompt else 0,
               "system_instruction_chars": _system_chars(kw.get("config", a[1] if len(a) > 1 else None), self)}
        prev = getattr(_tls, "rec", None)
        _tls.rec = rec
        t0 = time.perf_counter()
        try:
            resp = orig_gc(self, prompt, *a, **kw)
            rec["latency"] = time.perf_counter() - t0
            text = getattr(resp, "text", None)
            rec["out_chars"] = len(text or "")
            if role in JSON_ROLES:
                try:
                    parsed = ai.clean_and_parse_json(text) if text else None
                except Exception:
                    parsed = None
                rec["parse_ok"] = bool(parsed)
                if parsed and isinstance(parsed, dict):
                    if role == "Worker":
                        rec["difficulty"] = parsed.get("difficulty")
                        rec["worker_keys"] = sorted(parsed.keys())
                    if role == "GM_Logic":
                        sa = parsed.get("suggested_actions")
                        rec["suggested_actions_len"] = len(sa) if isinstance(sa, list) else None
                        rec["mode_transition"] = parsed.get("mode_transition")
            else:
                rec["empty_text"] = not (text or "").strip()
            return resp
        except Exception as e:
            rec["latency"] = time.perf_counter() - t0
            rec["exception"] = f"{type(e).__name__}: {str(e)[:160]}"
            raise
        finally:
            _tls.rec = prev
            COL.add_call(rec)

    ai.AIWrapper.generate_content = patched

    # Narrator streaming path (engine -> AIWrapper.generate_content_stream): not covered by the raw hook.
    # Recorded as separate role "<role>_stream" so existing role rows stay comparable with baseline.
    orig_stream = ai.AIWrapper.generate_content_stream

    def patched_stream(self, prompt, config=None):
        role = _classify(self, ai) + "_stream"
        rec = {"role": role, "model": self.model_name, "raw_calls": 1, "usage": [], "parse_ok": None,
               "exception": None, "stream": True,
               "prompt_chars": len(ai._normalize_prompt(prompt)) if prompt else 0,
               "system_instruction_chars": _system_chars(config, self)}
        t0 = time.perf_counter()
        try:
            it = orig_stream(self, prompt, config=config)
        except Exception as e:
            rec["latency"] = time.perf_counter() - t0
            rec["exception"] = f"{type(e).__name__}: {str(e)[:160]}"
            COL.add_call(rec)
            raise

        def _wrap():
            last_u = None
            chars = 0
            try:
                for chunk in it:
                    u = _usage(chunk)
                    if u:
                        last_u = u  # usage_metadata is cumulative; the last chunk is final
                    chars += len(getattr(chunk, "text", None) or "")
                    yield chunk
            except Exception as e:
                rec["exception"] = f"{type(e).__name__}: {str(e)[:160]}"
                raise
            finally:
                rec["latency"] = time.perf_counter() - t0
                rec["out_chars"] = chars
                rec["empty_text"] = chars == 0
                if last_u:
                    rec["usage"].append(last_u)
                COL.add_call(rec)

        return _wrap()

    ai.AIWrapper.generate_content_stream = patched_stream

    # raw SDK hook (retries + usage_metadata)
    models = ai.client.models
    orig_raw = models.generate_content

    def raw_patched(*a, **kw):
        rec = getattr(_tls, "rec", None)
        if rec is not None:
            rec["raw_calls"] += 1
        raw = orig_raw(*a, **kw)
        if rec is not None:
            u = _usage(raw)
            if u:
                rec["usage"].append(u)
        return raw

    models.generate_content = raw_patched

    class H(logging.Handler):
        def emit(self, record):
            try:
                msg = record.getMessage()
            except Exception:
                return
            for kind, pat in FALLBACK_PATTERNS.items():
                if pat in msg:
                    COL.fallbacks.append({"turn": COL.cur_turn, "kind": kind, "msg": msg[:200]})
            COL.add_cache_line(msg)

    logging.getLogger().addHandler(H())
    sys.stdout = _StdoutTee(sys.stdout)


def summarize(calls, fallbacks):
    roles = {}
    for r in sorted({c["role"] for c in calls}):
        cs = [c for c in calls if c["role"] == r]
        lat = [c["latency"] for c in cs if "latency" in c]
        us = [u for c in cs for u in c["usage"]]
        roles[r] = {
            "calls": len(cs),
            "json_parse_failures": sum(1 for c in cs if c["parse_ok"] is False),
            "empty_text": sum(1 for c in cs if c.get("empty_text")),
            "exceptions": sum(1 for c in cs if c["exception"]),
            "sdk_retries": sum(max(0, c["raw_calls"] - 1) for c in cs),
            "latency_median_s": round(statistics.median(lat), 3) if lat else None,
            "latency_p90_s": _pct(lat, 0.9),
            "tokens_in_total": sum(u["prompt"] for u in us),
            "tokens_out_total": sum(u["output"] for u in us),
            "tokens_thoughts_total": sum(u["thoughts"] for u in us),
            "tokens_cached_total": sum(u["cached"] for u in us),
            "tokens_cache_hit_calls": sum(1 for u in us if u["cached"] > 0),
            "calls_with_system_instruction": sum(1 for c in cs if c.get("system_instruction_chars")),
            "prompt_chars_avg": round(sum(c.get("prompt_chars", 0) for c in cs) / len(cs), 1),
            "system_instruction_chars_avg": round(sum(c.get("system_instruction_chars", 0) for c in cs) / len(cs), 1),
            "tokens_in_avg": round(sum(u["prompt"] for u in us) / len(us), 1) if us else None,
            "tokens_out_avg": round(sum(u["output"] for u in us) / len(us), 1) if us else None,
            "usage_samples": len(us),
        }
    fb = {}
    for f in fallbacks:
        fb[f["kind"]] = fb.get(f["kind"], 0) + 1
    for r in roles:
        pass
    return roles, fb


def print_table(rep):
    print(f"\nlabel={rep['label']} sim_model={rep.get('qa_simulator_model')} eval={rep.get('qa_evaluator_enabled')} "
          f"turns={rep['aggregate']['turns']}")
    print(f"{'role':<18}{'calls':>6}{'parseFail':>10}{'retries':>8}{'exc':>5}{'med_s':>8}{'p90_s':>8}{'tok_in':>9}{'tok_out':>9}{'tok_cached':>11}{'cacheHit':>9}")
    for r, v in rep["roles_total"].items():
        print(f"{r:<18}{v['calls']:>6}{v['json_parse_failures']:>10}{v['sdk_retries']:>8}{v['exceptions']:>5}"
              f"{str(v['latency_median_s']):>8}{str(v['latency_p90_s']):>8}{v['tokens_in_total']:>9}{v['tokens_out_total']:>9}"
              f"{v.get('tokens_cached_total', 0):>11}{v.get('tokens_cache_hit_calls', 0):>9}")
    print("fallbacks:", rep["fallbacks_total"] or "none")
    print("cache events:", rep.get("cache_events_total") or "none")
    print("aggregate:", {k: v for k, v in rep["aggregate"].items() if k != "mode_transitions"})


def git_hash():
    try:
        h = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).decode().strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).decode().strip()
        return h, bool(dirty)
    except Exception:
        return "unknown", None


async def run_profile(key, turns, qa):
    from qa_profiles import TEST_PROFILES
    from database.operations import load_lore_data, refresh_npc_database, get_user_data
    import core.ai_client as ai
    from config import GEMINI_API_KEYS_TEST
    from google import genai

    selected = TEST_PROFILES[key]
    idx = list(TEST_PROFILES.keys()).index(key)
    # engine uses a test key (same as qa_auto_test main)
    new_client = genai.Client(api_key=GEMINI_API_KEYS_TEST[idx % len(GEMINI_API_KEYS_TEST)])
    ai.client = new_client
    # re-install raw hook on the new client
    orig_raw = new_client.models.generate_content

    def raw_patched(*a, **kw):
        rec = getattr(_tls, "rec", None)
        if rec is not None:
            rec["raw_calls"] += 1
        raw = orig_raw(*a, **kw)
        if rec is not None:
            u = _usage(raw)
            if u:
                rec["usage"].append(u)
        return raw

    new_client.models.generate_content = raw_patched

    await load_lore_data()
    await refresh_npc_database(selected["user_id"])

    n_calls_start = len(COL.calls)
    n_fb_start = len(COL.fallbacks)
    turn_records = []
    orig_process = qa.RPGTesterAdapter.process
    state = {"turn": 0}

    async def process_patched(self, action):
        if action.startswith("/"):
            return await orig_process(self, action)
        state["turn"] += 1
        COL.cur_turn = f"{key}:{state['turn']}"
        prof_before, _ = await get_user_data(self.chat_id)
        mode_before = (prof_before or {}).get("mode", "NORMAL")
        c0 = len(COL.calls)
        f0 = len(COL.fallbacks)
        t0 = time.perf_counter()
        res = await orig_process(self, action)
        dt = time.perf_counter() - t0
        prof_after, _ = await get_user_data(self.chat_id)
        mode_after = (prof_after or {}).get("mode", "NORMAL")
        ui_text, logs, sugg, ai_data = res
        turn_calls = COL.calls[c0:]
        diffs = [c.get("difficulty") for c in turn_calls if c["role"] == "Worker" and "difficulty" in c]
        dc = None
        for d in diffs:
            dc = d
        try:
            dc_int = int(dc) if dc is not None else None
        except (TypeError, ValueError):
            dc_int = None
        gm_sa = [c.get("suggested_actions_len") for c in turn_calls if c["role"] == "GM_Logic"]
        turn_records.append({
            "profile": key,
            "turn": state["turn"],
            "action": action[:200],
            "ui_text": ui_text,
            "turn_latency_s": round(dt, 3),
            "worker_dc_raw": dc,
            "dc_valid": (dc_int in LEGAL_DCS) if dc_int is not None else None,
            "suggested_actions_count": len(sugg or []),
            "suggested_actions_is_4": len(sugg or []) == 4,
            "gm_logic_suggested_len": gm_sa,
            "mode_before": mode_before,
            "mode_after": mode_after,
            "mode_transition": f"{mode_before}->{mode_after}" if mode_before != mode_after else None,
            "roles_called": sorted({c["role"] for c in turn_calls}),
            "fallbacks": [f["kind"] for f in COL.fallbacks[f0:]],
            "censor_blocked": "Цензором" in (logs or ""),
        })
        r = turn_records[-1]
        n_err = sum(1 for c in turn_calls if c["exception"])
        print(f"[METRICS] {key} turn {state['turn']}/{turns} | {r['turn_latency_s']:.1f}s | DC={r['worker_dc_raw']}"
              f"{'' if r['dc_valid'] in (True, None) else ' INVALID'} | mode={mode_before}->{mode_after}"
              f" | actions={r['suggested_actions_count']} | calls={len(turn_calls)} errs={n_err}"
              f" | fallbacks={r['fallbacks'] or '-'}", flush=True)
        return res

    print(f"[METRICS] profile {key}: setup (world/NPC generation, ~1 min) ...", flush=True)
    qa.RPGTesterAdapter.process = process_patched

    captured = {}
    orig_report = qa._print_report

    def report_patched(results, profile_key="test", turn_states=None, action_log=None):
        captured["results"] = results
        return orig_report(results, profile_key=profile_key, turn_states=turn_states, action_log=action_log)

    qa._print_report = report_patched

    err = None
    try:
        await qa.run_test(max_turns=turns, profile=selected, profile_key=key, profile_index=idx)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        qa.RPGTesterAdapter.process = orig_process
        qa._print_report = orig_report

    results = captured.get("results", [])
    return {
        "profile": key,
        "char_name": selected["char_name"],
        "user_id": selected["user_id"],
        "turns_requested": turns,
        "turns_played": state["turn"],
        "error": err,
        "evaluator": {
            "pass": sum(1 for r in results if r["status"] == "PASS"),
            "fail": sum(1 for r in results if r["status"] == "FAIL"),
            "skip": sum(1 for r in results if r["status"] == "SKIP"),
            "fail_reasons": [{"turn": r["turn"], "reason": r["reason"][:200]} for r in results if r["status"] == "FAIL"],
            "ux_scores": [r["ux_score"] for r in results],
        },
        "turn_records": turn_records,
        "_call_slice": (n_calls_start, len(COL.calls)),
        "_fb_slice": (n_fb_start, len(COL.fallbacks)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--profiles", default="standard,combat_stress_tester")
    ap.add_argument("--turns", type=int, default=15)
    ap.add_argument("--no-eval", action="store_true", help="disable LLM evaluator (metrics are mechanical)")
    ap.add_argument("--table", help="print summary table from an existing metrics JSON and exit")
    args = ap.parse_args()
    if args.table:
        with open(args.table, encoding="utf-8") as f:
            print_table(json.load(f))
        return
    if args.no_eval:
        os.environ["QA_EVAL_ENABLED"] = "0"
    t_run0 = time.perf_counter()

    import qa_auto_test as qa
    qa._setup_logging(f"metrics_{args.label}")
    # console: only [METRICS] progress lines; full detail stays in qa_logs/run_metrics_<label>_*.log
    for _h in list(logging.getLogger().handlers):
        if type(_h) is logging.StreamHandler:
            logging.getLogger().removeHandler(_h)
    install_hooks()
    import config as cfg

    keys = [k.strip() for k in args.profiles.split(",") if k.strip()]
    started = datetime.now().isoformat(timespec="seconds")
    h, dirty = git_hash()

    async def go():
        out = []
        # Sequential on purpose: harness patches class-level RPGTesterAdapter.process, global ai.client,
        # COL.cur_turn and user_sessions; concurrency would interleave attribution.
        for k in keys:
            tp = time.perf_counter()
            r = await run_profile(k, args.turns, qa)
            r["wall_clock_s"] = round(time.perf_counter() - tp, 1)
            r["engine_turn_time_total_s"] = round(sum(t["turn_latency_s"] for t in r["turn_records"]), 1)
            r["harness_overhead_s"] = round(r["wall_clock_s"] - r["engine_turn_time_total_s"], 1)
            r["harness_overhead_per_turn_s"] = round(r["harness_overhead_s"] / max(1, r["turns_played"]), 1)
            print(f"[METRICS] profile {k} done: {r['turns_played']} turns, wall {r['wall_clock_s']}s, error={r['error']}", flush=True)
            out.append(r)
        return out

    profile_runs = asyncio.run(go())

    for pr in profile_runs:
        a, b = pr.pop("_call_slice")
        fa, fb_ = pr.pop("_fb_slice")
        roles, fbs = summarize(COL.calls[a:b], COL.fallbacks[fa:fb_])
        pr["roles"] = roles
        pr["fallbacks"] = fbs
    roles_all, fb_all = summarize(COL.calls, COL.fallbacks)
    cache_total = {}
    for ev in COL.cache_events:
        cache_total[ev["kind"]] = cache_total.get(ev["kind"], 0) + 1
    turns_all = [t for pr in profile_runs for t in pr["turn_records"]]
    lat = [t["turn_latency_s"] for t in turns_all]
    dcs = [t for t in turns_all if t["dc_valid"] is not None]
    agg = {
        "turns": len(turns_all),
        "turn_latency_median_s": round(statistics.median(lat), 3) if lat else None,
        "turn_latency_p90_s": _pct(lat, 0.9),
        "dc_checked": len(dcs),
        "dc_invalid": sum(1 for t in dcs if not t["dc_valid"]),
        "suggested_actions_not_4": sum(1 for t in turns_all if not t["suggested_actions_is_4"]),
        "mode_transitions": [f'{t["profile"]}:{t["turn"]}:{t["mode_transition"]}' for t in turns_all if t["mode_transition"]],
        "turns_in_combat": sum(1 for t in turns_all if t["mode_after"] == "COMBAT"),
        "censor_blocked_turns": sum(1 for t in turns_all if t["censor_blocked"]),
        "evaluator_pass": sum(pr["evaluator"]["pass"] for pr in profile_runs),
        "evaluator_fail": sum(pr["evaluator"]["fail"] for pr in profile_runs),
        "evaluator_skip": sum(pr["evaluator"]["skip"] for pr in profile_runs),
        "cache_created": cache_total.get("cache_created", 0),
        "cache_inline_fallback": cache_total.get("cache_inline_fallback", 0),
        "cache_hit_calls": sum(v["tokens_cache_hit_calls"] for v in roles_all.values()),
        "tokens_cached_total": sum(v["tokens_cached_total"] for v in roles_all.values()),
    }
    report = {
        "label": args.label,
        "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "git_commit": h,
        "git_dirty": dirty,
        "models": {
            "main": cfg.MODEL_MAIN_NAME, "worker": cfg.MODEL_WORKER_NAME,
            "gm_logic": cfg.MODEL_GM_LOGIC_NAME, "narrator": cfg.MODEL_NARRATOR_NAME,
            "narrator_alt": cfg.MODEL_NARRATOR_ALT_NAME,
        },
        "qa_simulator_model": qa.QA_MODEL_NAME,
        "qa_evaluator_enabled": qa.QA_EVAL_ENABLED,
        "qa_thinking_budget": qa.QA_THINKING_BUDGET,
        "total_wall_clock_s": round(time.perf_counter() - t_run0, 1),
        "profiles": keys,
        "turns_per_profile": args.turns,
        "aggregate": agg,
        "roles_total": roles_all,
        "fallbacks_total": fb_all,
        "cache_events_total": cache_total,
        "cache_events": COL.cache_events,
        "profile_runs": profile_runs,
        "all_calls": COL.calls,
        "fallback_events": COL.fallbacks,
    }
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", f"prompt_metrics_{args.label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[METRICS] WRITTEN: {path}  (total wall {report['total_wall_clock_s']}s)")
    print_table(report)
    print(f"[METRICS] style report: python scripts/narrator_style_report.py {path}")


if __name__ == "__main__":
    main()
