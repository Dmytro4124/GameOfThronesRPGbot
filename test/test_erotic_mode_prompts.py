"""Erotic mode: Narrator/GM_Logic prompt blocks (dynamic only) + engine flag propagation. No network."""
from unittest.mock import patch

import pytest

import config
from core import prompts as P
from core.prompts import build_gm_logic_parts, build_narrator_parts
from test.test_companions_moving_engine import _run_turn, _worker_updates, CID


def _narr(pl_name="Орвелл Тестовий", **kw):
    a = dict(user_input="дія", director_notes=["Факт"], npc_context_text="npc",
             player_name=pl_name, player_house="Тестерлі", current_scene="Сцена",
             current_location="Локація", impact_narrative_hints="", active_roster=["Тест"])
    a.update(kw)
    return build_narrator_parts(**a)


def _gm(pl_name="Орвелл Тестовий", **kw):
    a = dict(
        hero_name=pl_name, hero_house="Тестерлі", profile_json="{}", context_knowledge="",
        event_injection="", burst_injection="", current_time_str="День 1", curr_region="Північ",
        curr_loc="Вінтерфел", is_traveling=False, loc_hint="", curr_scene="Сцена",
        valid_locs_str="", valid_regions_str="", region_locs_str="", npc_context_text="",
        tension_label="0/4", mechanics_verdict="", impact_narrative_hints="", history_text="",
        user_input="дія", action_slots=[],
    )
    a.update(kw)
    return build_gm_logic_parts(**a)


# ---- Narrator ----

def test_narrator_erotic_block_in_dynamic_only():
    static, dyn = _narr(erotic_mode=True)
    assert "<EROTIC_MODE" in dyn
    assert "<EROTIC_MODE " not in static and "18+" not in static.split("ПРІОРИТЕТ РЕЖИМІВ")[0]


def test_narrator_erotic_adult_rule_present_and_first():
    _, dyn = _narr(erotic_mode=True)
    block = dyn[dyn.index("<EROTIC_MODE"):dyn.index("</EROTIC_MODE>")]
    assert "18+" in block
    assert block.index("18+") < block.index("ПРИВАТНІСТЬ")
    assert "неповнолітн" in block


def test_narrator_erotic_length_exception():
    _, dyn = _narr(erotic_mode=True)
    assert "300-450" in dyn


def test_narrator_no_erotic_tokens_when_off():
    _, dyn = _narr(erotic_mode=False)
    assert "<EROTIC_MODE" not in dyn and P.EROTIC_SCENE_WORDS not in dyn
    assert "150-250" in dyn.splitlines()[-1]


def test_narrator_combat_has_no_300_450_even_if_erotic():
    _, dyn = _narr(erotic_mode=True, combat_log=["лог"])
    assert "300-450" not in dyn.split("</EROTIC_MODE>")[-1]


def test_narrator_static_unchanged_by_erotic_and_player():
    assert _narr(erotic_mode=True)[0] == _narr(erotic_mode=False, pl_name="Інша Особа")[0] == P.NARRATOR_SYSTEM


def test_narrator_static_priority_clarifies_18plus():
    s = P.NARRATOR_SYSTEM
    line = [ln for ln in s.splitlines() if ln.startswith("ПРІОРИТЕТ РЕЖИМІВ")][0]
    assert "18+" in line
    assert "вище за будь-який режим" in line
    assert len(s) < 7500


def test_narrator_erotic_18plus_top_priority_over_modes_and_hero():
    _, dyn = _narr(erotic_mode=True)
    block = dyn[dyn.index("<EROTIC_MODE"):dyn.index("</EROTIC_MODE>")]
    for tok in ("найвищий пріоритет", "CRITICAL_OVERRIDE", "puppet", "godmode", "включно з героєм"):
        assert tok in block


def test_narrator_puppet_minor_caveat_independent_of_erotic():
    _, dyn = _narr(puppet_mode=True, erotic_mode=False)
    assert "<CRITICAL_OVERRIDE" in dyn and "неповнолітніх" in dyn and "18+" in dyn
    assert "<EROTIC_MODE" not in dyn


def test_narrator_no_puppet_no_caveat():
    _, dyn = _narr(puppet_mode=False, erotic_mode=False)
    assert "<CRITICAL_OVERRIDE" not in dyn and "неповнолітніх" not in dyn


def test_narrator_puppet_plus_erotic_both_blocks_and_rule():
    _, dyn = _narr(puppet_mode=True, erotic_mode=True)
    assert "<CRITICAL_OVERRIDE" in dyn and "<EROTIC_MODE" in dyn
    assert "18+" in dyn[dyn.index("<EROTIC_MODE"):dyn.index("</EROTIC_MODE>")]


def test_erotic_scene_words_constant_used_in_both_places():
    marker = "ДОВЖИНА: якщо сцена інтимна"
    _, dyn = _narr(erotic_mode=True)
    assert f"{marker} (і лише тоді) — {P.EROTIC_SCENE_WORDS} слів" in dyn
    assert f"довжина {P.EROTIC_SCENE_WORDS} слів" in dyn
    assert dyn.count(P.EROTIC_SCENE_WORDS) == 2


# ---- GM_Logic ----

def test_gm_erotic_block_in_dynamic_with_adult_rule_and_3_7():
    static, dyn = _gm(erotic_mode=True)
    assert "<erotic_mode>" in dyn and "<erotic_mode>" not in static
    block = dyn[dyn.index("<erotic_mode>"):dyn.index("</erotic_mode>")]
    assert "18+" in block and "3-7" in block


def test_gm_erotic_18plus_top_priority_over_modes_and_hero():
    _, dyn = _gm(erotic_mode=True)
    block = dyn[dyn.index("<erotic_mode>"):dyn.index("</erotic_mode>")]
    for tok in ("найвищий пріоритет", "puppet", "godmode", "включно з героєм"):
        assert tok in block


def test_gm_puppet_minor_caveat_independent_of_erotic():
    _, dyn = _gm(puppet_mode=True, erotic_mode=False)
    assert "<puppet_mode" in dyn and "неповнолітніх" in dyn and "18+" in dyn
    assert "<erotic_mode>" not in dyn
    _, dyn_off = _gm(puppet_mode=False, erotic_mode=False)
    assert "<puppet_mode" not in dyn_off and "неповнолітніх" not in dyn_off


def test_gm_puppet_plus_erotic_both_blocks():
    _, dyn = _gm(puppet_mode=True, erotic_mode=True)
    assert "<puppet_mode" in dyn and "<erotic_mode>" in dyn


def test_gm_no_erotic_block_when_off():
    _, dyn = _gm(erotic_mode=False)
    assert "<erotic_mode>" not in dyn
    _, dyn_default = _gm()
    assert "<erotic_mode>" not in dyn_default


def test_gm_static_identical_across_erotic_and_players():
    s1 = _gm(erotic_mode=True)[0]
    s2 = _gm(erotic_mode=False, pl_name="Інша Особа")[0]
    assert s1 == s2 == P.GM_LOGIC_SYSTEM
    assert "Орвелл" not in s1


# ---- Engine propagation ----

@pytest.mark.parametrize("flag", [True, False])
def test_engine_passes_same_erotic_flag_to_gm_and_narrator(flag):
    users = {CID} if flag else set()
    with patch.object(config, "EROTIC_USERS", users):
        r = _run_turn(_worker_updates())
    assert r["gm"]["erotic_mode"] is flag
    assert r["narr"] and r["narr"][-1].get("erotic_mode") is flag
