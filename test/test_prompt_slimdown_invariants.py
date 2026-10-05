"""Stage 3 (prompt slim-down for Flash-Lite): invariants of rendered prompts.

No live LLM calls. Prompts are rendered as plain strings; resolve_normal_action
is tested with mocked model_worker / prompt builder.
"""
import asyncio
import re
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from core.prompts import (
    build_combat_round_prompt,
    build_gm_logic_prompt,
    build_normal_resolve_prompt,
    build_validate_action_prompt,
)

WORKER_LEN_BARRIER = 30000

SCENES_BLOCK = "- Тронна зала\n- Двір\n- Покої лорда"


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _profile(with_features: bool = True) -> dict:
    p = {
        "Ім'я": "Джон Сноу", "Дім": "Старк", "level": 1, "class": "Bastard",
        "heritage": "Valyrian Descent",
        "hp_current": 12, "hp_max": 12, "ac": 14, "proficiency_bonus": 2,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "skill_profs": ["Athletics", "Intimidation"],
        "conditions": [], "Особисте Золото": 120,
        "Інвентар": "Меч, плащ, фляга", "Годинники": {"Scene_Tension": "0/4"},
        "equipped_weapon": {"name": "Довгий меч", "damage_dice": "1d8",
                            "damage_type": "slashing", "properties": ["versatile"]},
    }
    if not with_features:
        p["heritage"] = ""  # heritage traits alone also trigger the features block
    if with_features:
        p["features"] = [
            {"name": "Шляхетне поводження", "source": "Bastard",
             "desc": "Перевага на соціальні перевірки проти цілей рівного або нижчого статусу."},
            {"name": "Срібний язик", "source": "Bastard", "desc": "1/день: переконання без кидка."},
        ]
    return p


def _worker(scenes_block_str=None, with_features=True, **kw) -> str:
    args = dict(
        user_input="Я переконую слугу Маріка показати лист",
        profile=_profile(with_features),
        current_scene="Тронна зала",
        npcs_in_scene=[{"Name": "Слуга Марік", "Relation_Player": "Нейтральний"}],
        last_turn_summary="Гравець увійшов до зали.",
        current_location="Вінтерфелл",
        npc_reputation_context={"Слуга Марік": 0},
        clocks_info={"Scene_Tension": "0/4"},
        nearby_canonical_locs=["Вінтерфелл", "Білу Гавань"],
        all_canonical_locs_grouped="Північ: Вінтерфелл, Білу Гавань",
    )
    args.update(kw)
    if scenes_block_str is not None:
        args["scenes_block_str"] = scenes_block_str
    return build_normal_resolve_prompt(**args)


def _gm(puppet_mode=False, mode="NORMAL", **kw) -> str:
    args = dict(
        hero_name="Джон Сноу", hero_house="Старк", profile_json="{}",
        context_knowledge="", event_injection="", burst_injection="",
        current_time_str="День 1, ранок", curr_region="Північ", curr_loc="Вінтерфелл",
        is_traveling=False, loc_hint="", curr_scene="Тронна зала",
        valid_locs_str="Вінтерфелл, Біла Гавань", valid_regions_str="Північ",
        region_locs_str="Вінтерфелл", npc_context_text="Слуга Марік (Active)",
        tension_label="Спокійна", mechanics_verdict="SUCCESS", impact_narrative_hints="",
        history_text="", user_input="Я розмовляю зі слугою",
        action_slots=["A", "B", "C", "D"], puppet_mode=puppet_mode,
        scenes_block_str=SCENES_BLOCK, mode=mode,
    )
    args.update(kw)
    return build_gm_logic_prompt(**args)


def _censor() -> str:
    return build_validate_action_prompt("Джон", "Я іду до воріт", ["Меч"], gold=10)


# ---------------------------------------------------------------------------
# 1. Worker prompt invariants
# ---------------------------------------------------------------------------

WORKER_REQUIRED_KEYS = [
    "skill_check_reasoning", "difficulty_reasoning", "gold_reasoning",
    "ability_used", "skill_used", "difficulty", "advantage_reason", "disadvantage_reason",
    "combat_imminent", "verdict_text", "xp_award", "reputation_target_npc",
    "reputation_delta_success", "reputation_delta_failure", "updates",
    "minutes_passed", "location_impact", "scene_impact", "hp_damage_dice", "hp_heal_dice",
    "gold_impact", "inventory_new", "inventory_lost", "clocks_impact",
    "condition_apply", "condition_remove", "hp_damage_type",
    "save_used", "save_dc", "rest_type",
]


@pytest.fixture(scope="module")
def worker_prompt():
    return _worker(scenes_block_str=SCENES_BLOCK)


@pytest.mark.parametrize("key", WORKER_REQUIRED_KEYS)
def test_worker_schema_contains_contract_key(worker_prompt, key):
    schema = worker_prompt.split("<output_schema>\n")[-1].split("</output_schema>")[0]
    assert key in schema, f"Key {key!r} missing from Worker <output_schema>"


@pytest.mark.parametrize("key", WORKER_REQUIRED_KEYS)
def test_worker_output_format_example_contains_contract_key(worker_prompt, key):
    fmt = worker_prompt.split("OUTPUT FORMAT (JSON):")[1]
    assert f'"{key}"' in fmt, f"Key {key!r} missing from Worker OUTPUT FORMAT example"


def test_worker_dc_enum_exact(worker_prompt):
    assert "{2|5|10|12|15|17|20|22}" in worker_prompt


def test_worker_has_no_legacy_dc_in_enum(worker_prompt):
    schema = worker_prompt.split("<output_schema>\n")[-1].split("OUTPUT FORMAT")[0]
    dc_lines = [l for l in schema.splitlines() if l.startswith("difficulty") or "save_dc" in l]
    assert dc_lines
    for line in dc_lines:
        assert not re.search(r"\b(25|28|30)\b", line), f"legacy DC in: {line}"
    assert "25|28|30" not in worker_prompt


def test_worker_output_format_example_dc_is_legal(worker_prompt):
    fmt = worker_prompt.split("OUTPUT FORMAT (JSON):")[1]
    m = re.search(r'"difficulty":\s*(\d+)', fmt)
    assert m and int(m.group(1)) in {2, 5, 10, 12, 15, 17, 20, 22}


@pytest.mark.parametrize("marker", ["[GATE S", "[GATE R", "[GATE W", "[GATE 6"])
def test_worker_contains_gate(worker_prompt, marker):
    assert marker in worker_prompt


def test_worker_friend_foe_combat_imminent_rule(worker_prompt):
    # combat_imminent tied to a target NPC from the list; physical attack -> true
    assert "combat_imminent=true" in worker_prompt
    assert "reputation_target_npc обов'язково непорожній" in worker_prompt


def test_worker_reputation_rules(worker_prompt):
    assert "-7..+7" in worker_prompt
    assert "reputation_delta_success" in worker_prompt and "reputation_delta_failure" in worker_prompt
    assert "= 0 в обох полях" in worker_prompt  # trivial actions = 0


def test_worker_reputation_target_names_only_from_list(worker_prompt):
    assert "ТОЧНЕ ім'я" in worker_prompt
    assert "Лише ці імена легальні" in worker_prompt
    assert 'reputation_target_npc="Торговець"' in worker_prompt  # antiexample


def test_worker_example_m_purchase(worker_prompt):
    assert "EXAMPLE M" in worker_prompt
    seg = worker_prompt.split("EXAMPLE M")[1].split("</few_shot_examples>")[0]
    assert "spend_" in seg
    assert "inventory_new" in seg


def test_worker_few_shot_count_is_six(worker_prompt):
    seg = worker_prompt.split("<few_shot_examples>")[1].split("</few_shot_examples>")[0]
    assert re.findall(r"^EXAMPLE ([A-Z])", seg, flags=re.M) == ["A", "C", "F", "G", "H", "M"]


@pytest.mark.parametrize("with_features", [True, False])
def test_worker_length_below_barrier(with_features):
    p = _worker(scenes_block_str=SCENES_BLOCK, with_features=with_features)
    assert len(p) < WORKER_LEN_BARRIER, f"Worker prompt grew to {len(p)} chars"


def test_worker_gate0_class_conditional_in_static_features_in_dynamic():
    """Stage 4: GATE 0-CLASS is always in the static part (conditional on the
    class_features block); the <class_features> block lives in dynamic only with features."""
    from core.prompts import build_normal_resolve_parts

    def _parts(with_features):
        return build_normal_resolve_parts(
            user_input="Я оглядаюсь", profile=_profile(with_features), current_scene="Тронна зала",
            npcs_in_scene=[], last_turn_summary="x", current_location="Вінтерфелл",
            npc_reputation_context={}, clocks_info={}, nearby_canonical_locs=[],
            all_canonical_locs_grouped="",
        )

    st_f, dyn_f = _parts(True)
    st_n, dyn_n = _parts(False)
    assert st_f == st_n
    assert "[GATE 0-CLASS" in st_f
    assert "є блок class_features" in st_f
    assert "<class_features>" in dyn_f
    assert "<class_features>" not in dyn_n
    assert "GATE 0-META" in st_f
    assert "{next_gate}" not in st_f


def test_worker_no_unrendered_placeholders(worker_prompt):
    assert "{scene_rule}" not in worker_prompt
    assert "{locs_nearby}" not in worker_prompt


# ---------------------------------------------------------------------------
# 2. scenes_block_str
# ---------------------------------------------------------------------------

def test_scenes_block_passed_renders_block_and_literal_rule():
    p = _worker(scenes_block_str=SCENES_BLOCK)
    loc_rules = p.split("<location_rules>")[1].split("</location_rules>")[0]
    assert SCENES_BLOCK in loc_rules
    assert "ДОСЛІВНО" in loc_rules
    assert "1–3 слова" not in loc_rules


def test_scenes_block_empty_uses_fallback_rule():
    p = _worker(scenes_block_str="")
    loc_rules = p.split("<location_rules>")[1].split("</location_rules>")[0]
    assert "1–3 слова" in loc_rules
    assert "ДОСЛІВНО" not in loc_rules
    assert "Канонічні сцени поточної локації" not in p


def test_scenes_block_default_equals_empty():
    assert _worker() == _worker(scenes_block_str="")


# ---------------------------------------------------------------------------
# 3. resolve_normal_action -> scenes_block_str
# ---------------------------------------------------------------------------

def _run_resolve(current_location, format_patch):
    from core.dnd_engine import resolve_normal_action

    resp = MagicMock()
    resp.text = "{}"
    data = {
        "ability_used": "None", "skill_used": "None", "difficulty": 2,
        "advantage_reason": "", "disadvantage_reason": "", "combat_imminent": False,
        "verdict_text": "ok", "xp_award": 0, "reputation_target_npc": "",
        "updates": {"minutes_passed": 1, "location_impact": "none", "scene_impact": "none"},
    }
    builder = MagicMock(return_value=("MOCK_STATIC", "MOCK_PROMPT"))
    with ExitStack() as st:
        st.enter_context(patch("core.dnd_engine.model_worker.generate_content", return_value=resp))
        st.enter_context(patch("core.dnd_engine.clean_and_parse_json", return_value=data))
        st.enter_context(patch("core.dnd_engine.build_normal_resolve_parts", builder))
        st.enter_context(format_patch)
        asyncio.run(resolve_normal_action(
            user_input="Я оглядаюсь",
            profile={"level": 1, "ability_scores": {"STR": 10}, "skill_profs": [], "conditions": []},
            current_location=current_location,
        ))
    return builder


def test_resolve_passes_scenes_from_formatter():
    fmt = patch("core.dnd_engine.format_scene_names_for_prompt", return_value="SCENES_X")
    builder = _run_resolve("Вінтерфелл", fmt)
    assert builder.call_args.kwargs["scenes_block_str"] == "SCENES_X"


def test_resolve_calls_formatter_with_current_location():
    m2 = MagicMock(return_value="S")
    _run_resolve("Вінтерфелл", patch("core.dnd_engine.format_scene_names_for_prompt", m2))
    m2.assert_called_once_with("Вінтерфелл")


def test_resolve_formatter_exception_does_not_crash_turn():
    fmt = patch("core.dnd_engine.format_scene_names_for_prompt", side_effect=RuntimeError("boom"))
    builder = _run_resolve("Вінтерфелл", fmt)
    assert builder.call_args.kwargs["scenes_block_str"] == ""


@pytest.mark.parametrize("loc", [None, ""])
def test_resolve_no_location_gives_empty_and_skips_formatter(loc):
    m = MagicMock(return_value="SHOULD_NOT_BE_USED")
    builder = _run_resolve(loc, patch("core.dnd_engine.format_scene_names_for_prompt", m))
    assert builder.call_args.kwargs["scenes_block_str"] == ""
    m.assert_not_called()


# ---------------------------------------------------------------------------
# 4. GM_Logic
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gm_prompt():
    return _gm()


def test_gm_button_plain_text_rule(gm_prompt):
    assert "простий текст без HTML/Markdown" in gm_prompt


def test_gm_button_div_antiexample(gm_prompt):
    assert "<div>Напасти</div>" in gm_prompt


def test_gm_acting_npcs_must_be_in_npc_updates(gm_prompt):
    assert "Кожен NPC з ростеру сцени (присутній у сцені), що говорив, діяв, постраждав" in gm_prompt
    assert "має бути в npc_updates" in gm_prompt


def test_gm_attitude_to_player_forbidden(gm_prompt):
    assert "Attitude to Player" in gm_prompt
    assert "у npc_updates не включай" in gm_prompt


def test_gm_attitude_to_player_not_in_output_example(gm_prompt):
    fmt = gm_prompt.split("ФОРМАТ ВІДПОВІДІ (JSON):")[1]
    assert "Attitude to Player" not in fmt


def test_gm_companion_npcs_whitelist(gm_prompt):
    assert "companion_npcs" in gm_prompt
    assert '"companion_npcs": []' in gm_prompt
    assert "заповни companion_npcs точними іменами" in gm_prompt


def test_gm_suggested_actions_exactly_four(gm_prompt):
    assert "suggested_actions — рівно 4" in gm_prompt
    for i in (1, 2, 3, 4):
        assert f"Дія {i}" in gm_prompt


def test_gm_frozen_fields(gm_prompt):
    for f in ("Description", "Character", "Goal", "Secrets"):
        assert f in gm_prompt
    assert "frozen_fields_change_reason" in gm_prompt


def test_gm_language_invariant(gm_prompt):
    assert "LANGUAGE INVARIANT" in gm_prompt


def test_gm_scene_literal_from_scene_list(gm_prompt):
    assert "дослівно одна назва зі списку" in gm_prompt


@pytest.mark.parametrize("key", [
    "reasoning", "npc_reasoning", "director_notes", "companion_npcs", "npc_updates",
    "mode_transition", "suggested_actions",
])
def test_gm_output_has_required_keys(gm_prompt, key):
    assert f'"{key}"' in gm_prompt.split("ФОРМАТ ВІДПОВІДІ (JSON):")[1]


@pytest.mark.parametrize("field", ["Name", "Location", "Scene", "Memory_Anchor",
                                   "Relation_NPCs", "Inventory", "Status"])
def test_gm_npc_update_fixed_fields(gm_prompt, field):
    assert f'"{field}"' in gm_prompt.split("ФОРМАТ ВІДПОВІДІ (JSON):")[1]


def test_gm_puppet_tag_renamed():
    p = _gm(puppet_mode=True)
    assert '<puppet_mode priority="highest">' in p
    assert "</puppet_mode>" in p
    assert "CRITICAL_OVERRIDE" not in p


def test_gm_no_puppet_block_when_disabled(gm_prompt):
    assert "<puppet_mode" not in gm_prompt


def test_gm_combat_mode_still_four_slots_and_no_hp_current():
    p = _gm(mode="COMBAT")
    assert "[ATTACK]" in p and "[DEFEND]" in p and "[FLEE]" in p and "[SPECIAL]" in p
    assert "не включай hp_current" in p


# ---------------------------------------------------------------------------
# 5. Censor: five blocking rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [
    "1. OUTCOME CONTROL", "2. NPC PUPPETING", "3. ITEM FRAUD",
    "4. ANACHRONISMS", "5. META-GAMING",
])
def test_censor_five_blocking_rules(rule):
    assert rule in _censor()


def test_censor_output_contract():
    p = _censor()
    assert '"is_valid"' in p and '"refusal_reason"' in p


def test_censor_has_no_sixth_rule():
    assert "6. " not in _censor().split("The only 5 rules")[1].split("=== refusal_reason")[0]


# ---------------------------------------------------------------------------
# 6. Removed CAPS markers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("builder", [
    lambda: _worker(scenes_block_str=SCENES_BLOCK),
    lambda: _worker(scenes_block_str=""),
    lambda: _gm(),
    lambda: _gm(puppet_mode=True),
    lambda: _gm(mode="COMBAT"),
    _censor,
    lambda: build_combat_round_prompt("Я б'ю", _profile(False), {"npcs": [], "weapons": []}),
], ids=["worker", "worker_noscenes", "gm", "gm_puppet", "gm_combat", "censor", "combat_round"])
@pytest.mark.parametrize("marker", ["NOT. EVER.", "CRITICAL OVERRIDE", "CRITICAL_OVERRIDE"])
def test_no_legacy_caps_markers(builder, marker):
    assert marker not in builder()


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------

def test_worker_example_m_not_dc2(worker_prompt):
    seg = worker_prompt.split("EXAMPLE M")[1].split("</few_shot_examples>")[0]
    assert "difficulty: 5" in seg
    assert "difficulty: 2" not in seg
    assert 'action_severity: "TRIVIAL"' in seg


def test_worker_gate6_self_damage_mini_example(worker_prompt):
    seg = worker_prompt.split("[GATE 6")[1].split("reputation_target_npc —")[0]
    assert "Приклад самошкоди" in seg
    assert 'hp_damage_type="fire"' in seg
    assert 'hp_damage_dice="1d6"' in seg
    assert 'ability_used="None"' in seg and "difficulty=5" in seg


def test_gm_combat_prompt_has_no_double_braces():
    assert "{{" not in _gm(mode="COMBAT")
    assert "}}" not in _gm(mode="COMBAT")


def test_gm_puppet_mandatory_and_no_reputation_delta():
    p = _gm(puppet_mode=True)
    blk = p.split('<puppet_mode priority="highest">')[1].split("</puppet_mode>")[0]
    assert "обов'язково" in blk
    assert "reputation_delta" not in blk
    assert "+7" not in blk


# ---------------------------------------------------------------------------
# format_scene_names_for_prompt
# ---------------------------------------------------------------------------

from core import world_constants as wc  # noqa: E402

_SAMPLE_LOCS = ["Королівська Гавань", "Вінтерфелл", "Харренхол"]


def _known_locs():
    return [l for l in _SAMPLE_LOCS if l in wc.LOCATION_SCENES] or list(wc.LOCATION_SCENES)[:3]


@pytest.mark.parametrize("loc", ["", None, wc.TRAVEL_LOCATION, "Неіснуюча локація"])
def test_scene_names_empty_for_travel_unknown_empty(loc):
    assert wc.format_scene_names_for_prompt(loc) == ""


def test_scene_names_format_dash_lines_only():
    for loc in _known_locs():
        out = wc.format_scene_names_for_prompt(loc)
        assert out
        assert all(line.startswith("- ") and len(line) > 2 for line in out.splitlines())


def test_scene_names_no_hints():
    for loc in _known_locs():
        out = wc.format_scene_names_for_prompt(loc)
        for bad in ("NPC-пул", "ЗАБОРОНЕНО", "->", "["):
            assert bad not in out


def test_scene_names_no_duplicates():
    for loc in wc.LOCATION_SCENES:
        lines = wc.format_scene_names_for_prompt(loc).splitlines()
        assert len(lines) == len(set(lines))


def test_scene_names_all_pass_is_valid_scene_and_cover_all():
    for loc in wc.LOCATION_SCENES:
        names = [l[2:] for l in wc.format_scene_names_for_prompt(loc).splitlines()]
        assert all(wc.is_valid_scene(loc, n) for n in names)
        assert set(names) == set(wc.get_scenes_for_location(loc))
