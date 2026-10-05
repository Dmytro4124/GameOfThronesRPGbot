"""Stage 4: static/dynamic split of role prompts (build_*_parts -> (static, dynamic)).

Invariants: the static part is byte-identical across players/turns (required for the
text-keyed explicit cache), equals the constant in SYSTEM_PROMPTS, and never carries
player data; the dynamic part carries the data and none of the big static rules.
Backward-compatible builders return static + "\\n\\n" + dynamic. No network.
"""
import pytest

from core import prompts as P
from core.prompts import (
    SYSTEM_PROMPTS, build_normal_resolve_parts, build_normal_resolve_prompt,
    build_gm_logic_parts, build_gm_logic_prompt, build_validate_action_parts,
    build_validate_action_prompt, build_narrator_parts, build_narrator_prompt,
)

# ---------------------------------------------------------------------------
# Builders with two very different "players/turns"
# ---------------------------------------------------------------------------

_PLAYERS = [
    dict(name="Орвелл Тестовий", house="Тестерлі", gold=120, inv="Меч ZXQ, плащ", action="Виконую дію ZXQ",
         loc="Локація ZXQ", scene="Сцена ZXQ", npc="Тестовий Слуга ZXQ"),
    dict(name="Ґвінет Вигадана", house="Вигаданих", gold=98765, inv="Артефакт QWV", action="Роблю дію QWV",
         loc="Локація QWV", scene="Сцена QWV", npc="Вигаданий Радник QWV"),
]


def _profile(pl, features):
    p = {
        "Ім'я": pl["name"], "Дім": pl["house"], "level": 1, "class": "Bastard",
        "heritage": "Valyrian Descent", "hp_current": 12, "hp_max": 12, "ac": 14,
        "proficiency_bonus": 2,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "skill_profs": ["Athletics"], "conditions": [], "Особисте Золото": pl["gold"],
        "Інвентар": pl["inv"], "Годинники": {"Scene_Tension": "0/4"},
    }
    if not features:
        p["heritage"] = ""  # heritage traits alone also produce a <class_features> block
    if features:
        p["features"] = [{"name": "Срібний язик", "source": "Bastard", "desc": "1/день: переконання."}]
    return p


def _worker(pl, features=True):
    return build_normal_resolve_parts(
        user_input=pl["action"], profile=_profile(pl, features), current_scene=pl["scene"],
        npcs_in_scene=[{"Name": pl["npc"], "Relation_Player": "Нейтральний"}],
        last_turn_summary="Попередній хід " + pl["name"], current_location=pl["loc"],
        npc_reputation_context={pl["npc"]: 0}, clocks_info={"Scene_Tension": "0/4"},
        nearby_canonical_locs=[pl["loc"]], all_canonical_locs_grouped=f"Регіон: {pl['loc']}",
        scenes_block_str="- " + pl["scene"],
    )


def _gm_args(pl, **kw):
    a = dict(
        hero_name=pl["name"], hero_house=pl["house"], profile_json="{}", context_knowledge="",
        event_injection="", burst_injection="", current_time_str="День 1, ранок",
        curr_region="Північ", curr_loc=pl["loc"], is_traveling=False, loc_hint="",
        curr_scene=pl["scene"], valid_locs_str=pl["loc"], valid_regions_str="Північ",
        region_locs_str=pl["loc"], npc_context_text=f"{pl['npc']} (Active)", tension_label="Спокійна",
        mechanics_verdict="SUCCESS", impact_narrative_hints="", history_text="",
        user_input=pl["action"], action_slots=["A", "B", "C", "D"], scenes_block_str="- " + pl["scene"],
    )
    a.update(kw)
    return a


def _narrator(pl, **kw):
    a = dict(
        user_input=pl["action"], director_notes=["Факт про " + pl["npc"]], npc_context_text=pl["npc"],
        player_name=pl["name"], player_house=pl["house"], current_scene=pl["scene"],
        current_location=pl["loc"], impact_narrative_hints="", active_roster=[pl["npc"]],
    )
    a.update(kw)
    return build_narrator_parts(**a)


# ---------------------------------------------------------------------------
# Static identity across players/turns and vs SYSTEM_PROMPTS
# ---------------------------------------------------------------------------

def test_worker_static_identical_across_players_and_equals_constant():
    s1, _ = _worker(_PLAYERS[0])
    s2, _ = _worker(_PLAYERS[1])
    assert s1 == s2 == SYSTEM_PROMPTS["worker_normal"] == P.WORKER_NORMAL_SYSTEM


def test_worker_static_identical_with_and_without_features():
    assert _worker(_PLAYERS[0], True)[0] == _worker(_PLAYERS[0], False)[0]


def test_censor_static_identical_and_equals_constant():
    s1, _ = build_validate_action_parts("Джон", "дія 1", "Меч", 10)
    s2, _ = build_validate_action_parts("Дейнерис", "дія 2", "порожній", 99999)
    assert s1 == s2 == SYSTEM_PROMPTS["censor"] == P.CENSOR_SYSTEM


@pytest.mark.parametrize("mode,key", [("NORMAL", "gm_logic"), ("COMBAT", "gm_logic_combat")])
def test_gm_static_identical_across_players_and_equals_constant(mode, key):
    s1, _ = build_gm_logic_parts(**_gm_args(_PLAYERS[0], mode=mode))
    s2, _ = build_gm_logic_parts(**_gm_args(_PLAYERS[1], mode=mode))
    assert s1 == s2 == SYSTEM_PROMPTS[key]


def test_gm_normal_and_combat_statics_differ():
    sn, _ = build_gm_logic_parts(**_gm_args(_PLAYERS[0], mode="NORMAL"))
    sc, _ = build_gm_logic_parts(**_gm_args(_PLAYERS[0], mode="COMBAT"))
    assert sn != sc
    assert sn == P.GM_LOGIC_SYSTEM and sc == P.GM_LOGIC_SYSTEM_COMBAT


def test_gm_combat_snapshot_stays_out_of_static():
    snap = {"Тестовий Воїн ZXQ": {"hp_current": 142, "hp_max": 150, "ac": 18, "conditions": []}}
    s_snap, d_snap = build_gm_logic_parts(**_gm_args(_PLAYERS[0], mode="COMBAT", npc_hp_snapshot=snap))
    s_none, _ = build_gm_logic_parts(**_gm_args(_PLAYERS[0], mode="COMBAT"))
    assert s_snap == s_none
    assert "Тестовий Воїн ZXQ" not in s_snap and "Тестовий Воїн ZXQ" in d_snap


def test_narrator_static_identical_across_players_normal():
    s1, _ = _narrator(_PLAYERS[0])
    s2, _ = _narrator(_PLAYERS[1], puppet_mode=True, erotic_mode=True, dead_npcs=["Нед Старк"])
    assert s1 == s2 == SYSTEM_PROMPTS["narrator"] == P.NARRATOR_SYSTEM


def test_narrator_combat_vs_normal_static():
    sn, dn = _narrator(_PLAYERS[0])
    sc, dc = _narrator(_PLAYERS[0], combat_log=["ЛОГ ZXQ"])
    assert sn == P.NARRATOR_SYSTEM and sc == P.NARRATOR_SYSTEM_COMBAT
    assert sn != sc
    assert sc.startswith(sn)
    assert "<combat_log>" + chr(10) + "ЛОГ ZXQ" in dc
    assert "ЛОГ ZXQ" not in sc and "<combat_log>" + chr(10) not in dn


def test_narrator_empty_combat_log_list_is_combat_mode():
    s, d = _narrator(_PLAYERS[0], combat_log=[])
    assert s == P.NARRATOR_SYSTEM_COMBAT and "<combat_log>" in d


def test_system_prompts_registry_complete_and_distinct():
    assert set(SYSTEM_PROMPTS) == {"worker_normal", "gm_logic", "gm_logic_combat", "censor",
                                   "narrator", "narrator_combat"}
    assert all(isinstance(v, str) and v for v in SYSTEM_PROMPTS.values())
    assert len(set(SYSTEM_PROMPTS.values())) == len(SYSTEM_PROMPTS)


# ---------------------------------------------------------------------------
# Static carries no player data; dynamic carries data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pl", _PLAYERS, ids=["snow", "dany"])
def test_worker_static_has_no_player_data(pl):
    static, dyn = _worker(pl)
    for needle in (pl["name"], pl["house"], str(pl["gold"]), pl["inv"], pl["action"], pl["npc"], pl["scene"]):
        assert needle not in static, f"player data {needle!r} leaked into Worker static"
    for needle in (pl["action"], pl["npc"], pl["scene"]):
        assert needle in dyn, f"{needle!r} missing from Worker dynamic"
    assert "<player_state>" in dyn


@pytest.mark.parametrize("pl", _PLAYERS, ids=["snow", "dany"])
def test_censor_static_has_no_player_data(pl):
    static, dyn = build_validate_action_parts(pl["name"], pl["action"], pl["inv"], pl["gold"])
    for needle in (pl["name"], pl["action"], pl["inv"], str(pl["gold"])):
        assert needle not in static
        assert needle in dyn


@pytest.mark.parametrize("pl", _PLAYERS, ids=["snow", "dany"])
@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_gm_static_has_no_player_data(pl, mode):
    static, dyn = build_gm_logic_parts(**_gm_args(pl, mode=mode))
    for needle in (pl["name"], pl["house"], pl["action"], pl["npc"], pl["scene"]):
        assert needle not in static
    for needle in (pl["action"], pl["npc"]):
        assert needle in dyn


@pytest.mark.parametrize("pl", _PLAYERS, ids=["snow", "dany"])
@pytest.mark.parametrize("combat", [False, True])
def test_narrator_static_has_no_player_data(pl, combat):
    static, dyn = _narrator(pl, combat_log=["лог"] if combat else None)
    for needle in (pl["name"], pl["house"], pl["action"], pl["npc"], pl["scene"], pl["loc"]):
        assert needle not in static
    for needle in (pl["name"], pl["action"], pl["npc"]):
        assert needle in dyn


# ---------------------------------------------------------------------------
# Dynamic has no big static rules (gates, schema, rule banners)
# ---------------------------------------------------------------------------

def test_worker_dynamic_has_no_gates_or_output_schema():
    static, dyn = _worker(_PLAYERS[0])
    assert "[GATE" in static
    assert "[GATE" not in dyn
    assert "GATE 0-CLASS" not in dyn
    assert len(dyn) < len(static) / 2


def test_worker_gate0_class_conditional_always_static():
    s_f, d_f = _worker(_PLAYERS[0], True)
    s_n, d_n = _worker(_PLAYERS[0], False)
    assert "[GATE 0-CLASS" in s_f and "[GATE 0-CLASS" in s_n
    assert "Active class/heritage features" in d_f and "Active class/heritage features" not in d_n
    assert "Active class/heritage features" not in s_f


def test_censor_dynamic_is_short_and_ruleless():
    static, dyn = build_validate_action_parts("Джон", "Я іду", "Меч", 5)
    assert len(dyn) < 600 and len(dyn) < len(static) / 3
    assert "OUTPUT FORMAT" in static and "OUTPUT FORMAT" not in dyn


@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_gm_dynamic_has_no_static_rule_blocks(mode):
    static, dyn = build_gm_logic_parts(**_gm_args(_PLAYERS[0], mode=mode))
    assert "LANGUAGE INVARIANT" in static and "LANGUAGE INVARIANT" not in dyn
    assert "suggested_actions" in static


def test_narrator_dynamic_has_no_style_rules():
    static, dyn = _narrator(_PLAYERS[0])
    assert "<system>" in static and "<system>" not in dyn
    assert len(dyn) < len(static)


# ---------------------------------------------------------------------------
# Backward-compatible builders: static + "\n\n" + dynamic
# ---------------------------------------------------------------------------

def test_legacy_worker_builder_concatenates_parts():
    pl = _PLAYERS[0]
    kwargs = dict(
        user_input=pl["action"], profile=_profile(pl, True), current_scene=pl["scene"],
        npcs_in_scene=[], last_turn_summary="x", current_location=pl["loc"],
        npc_reputation_context={}, clocks_info={}, nearby_canonical_locs=[], all_canonical_locs_grouped="",
    )
    s, d = build_normal_resolve_parts(**kwargs)
    assert build_normal_resolve_prompt(**kwargs) == s + "\n\n" + d


def test_legacy_censor_builder_concatenates_parts():
    s, d = build_validate_action_parts("Джон", "дія", "Меч", 7)
    assert build_validate_action_prompt("Джон", "дія", "Меч", 7) == s + "\n\n" + d


@pytest.mark.parametrize("mode", ["NORMAL", "COMBAT"])
def test_legacy_gm_builder_concatenates_parts(mode):
    a = _gm_args(_PLAYERS[0], mode=mode)
    s, d = build_gm_logic_parts(**a)
    assert build_gm_logic_prompt(**a) == s + "\n\n" + d


def test_legacy_narrator_builder_concatenates_parts():
    a = dict(user_input="x", director_notes=["n"], npc_context_text="", player_name="Джон Сноу",
             player_house="Старк", current_scene="S", current_location="L", impact_narrative_hints="")
    s, d = build_narrator_parts(**a)
    assert build_narrator_prompt(**a) == s + "\n\n" + d
