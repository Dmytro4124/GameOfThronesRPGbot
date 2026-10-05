"""Stage 2: native response_schema constants in core/prompts.py.

Covers: SDK validity, structural sanity, CLAUDE.md §5.3 contract <-> schema, enums vs code,
Scene_Tension-as-string handling in apply_system_impacts, wiring (schema -> call site).
No network, no Sheets.
"""
import ast
import json
import pathlib

import pytest
from google.genai import types

from core import prompts
from core.prompts import (
    RESPONSE_SCHEMAS, CENSOR_SCHEMA, WORKER_NORMAL_SCHEMA, WORKER_COMBAT_SCHEMA,
    GM_LOGIC_SCHEMA, TRAINING_REQUEST_SCHEMA, INITIAL_STATS_SCHEMA,
    NPC_COMBAT_ACTION_SCHEMA, NPC_REGEN_SCHEMA,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
ABILITIES = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}


def _enum(schema, *path):
    node = schema
    for p in path:
        node = node["properties"][p] if "properties" in node else node["items"]["properties"][p]
    return node["enum"]


def _props(schema):
    return schema["properties"]


def _walk(node, path="$"):
    """Yield (path, dict) for every schema node."""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.get("properties", {}).items():
            yield from _walk(v, f"{path}.{k}")
        if "items" in node:
            yield from _walk(node["items"], f"{path}[]")


# ── registry ────────────────────────────────────────────────────────────────

def test_registry_has_exactly_the_eight_schemas():
    assert set(RESPONSE_SCHEMAS) == {
        "censor", "worker_normal", "worker_combat", "gm_logic",
        "training", "initial_stats", "npc_combat_action", "npc_regen",
    }
    assert RESPONSE_SCHEMAS["censor"] is CENSOR_SCHEMA
    assert RESPONSE_SCHEMAS["worker_normal"] is WORKER_NORMAL_SCHEMA
    assert RESPONSE_SCHEMAS["worker_combat"] is WORKER_COMBAT_SCHEMA
    assert RESPONSE_SCHEMAS["gm_logic"] is GM_LOGIC_SCHEMA
    assert RESPONSE_SCHEMAS["training"] is TRAINING_REQUEST_SCHEMA
    assert RESPONSE_SCHEMAS["initial_stats"] is INITIAL_STATS_SCHEMA
    assert RESPONSE_SCHEMAS["npc_combat_action"] is NPC_COMBAT_ACTION_SCHEMA
    assert RESPONSE_SCHEMAS["npc_regen"] is NPC_REGEN_SCHEMA


# ── SDK validity ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_schema_passes_sdk_model_validate(name):
    types.Schema.model_validate(RESPONSE_SCHEMAS[name])


@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_schema_accepted_by_generate_content_config(name):
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=RESPONSE_SCHEMAS[name])
    assert cfg.response_schema is not None


@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_schema_is_json_serialisable(name):
    json.dumps(RESPONSE_SCHEMAS[name])


@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_sdk_does_not_silently_drop_constraint_keys(name):
    """required / propertyOrdering / enum / minItems / maxItems survive Schema round-trip."""
    schema = RESPONSE_SCHEMAS[name]
    dumped = types.Schema.model_validate(schema).model_dump(by_alias=True, exclude_none=True, mode="json")
    for (path, src), (_, got) in zip(_walk(schema), _walk(dumped)):
        for key in ("required", "propertyOrdering", "enum", "minItems", "maxItems", "nullable"):
            if key in src:
                assert key in got, f"{name}{path}: SDK dropped '{key}'"
                assert got[key] == src[key] or key == "enum" and list(got[key]) == list(src[key])


# ── structural sanity ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_required_and_ordering_are_consistent_with_properties(name):
    for path, node in _walk(RESPONSE_SCHEMAS[name]):
        if node.get("type") == "object" and "properties" in node:
            props = set(node["properties"])
            assert set(node.get("required", [])) <= props, f"{name}{path}: required not in properties"
            assert node["propertyOrdering"], f"{name}{path}: empty propertyOrdering"
            assert list(node["propertyOrdering"]) == list(node["properties"]) or \
                set(node["propertyOrdering"]) == props, f"{name}{path}: ordering != properties"


@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_enum_only_on_string_types_no_integer_enums(name):
    for path, node in _walk(RESPONSE_SCHEMAS[name]):
        if "enum" in node:
            assert node["type"] == "string", f"{name}{path}: enum on non-string"
            assert all(isinstance(v, str) for v in node["enum"])


@pytest.mark.parametrize("name", sorted(RESPONSE_SCHEMAS))
def test_top_level_is_object(name):
    assert RESPONSE_SCHEMAS[name]["type"] == "object"


# ── reasoning-first (CoT before the answer) ─────────────────────────────────

@pytest.mark.parametrize("schema,first", [
    (WORKER_NORMAL_SCHEMA, ["skill_check_reasoning", "difficulty_reasoning", "gold_reasoning"]),
    (WORKER_COMBAT_SCHEMA, ["reasoning"]),
    (GM_LOGIC_SCHEMA, ["reasoning", "npc_reasoning"]),
    (NPC_REGEN_SCHEMA, ["reasoning"]),
    (INITIAL_STATS_SCHEMA, ["thought_process"]),
])
def test_reasoning_fields_come_first_in_property_ordering(schema, first):
    assert schema["propertyOrdering"][:len(first)] == first


def test_worker_normal_reasoning_precedes_dependent_decisions():
    order = WORKER_NORMAL_SCHEMA["propertyOrdering"]
    assert order.index("skill_check_reasoning") < order.index("skill_used")
    assert order.index("difficulty_reasoning") < order.index("difficulty")
    assert order.index("gold_reasoning") < order.index("updates")
    assert order.index("reputation_reasoning") < order.index("reputation_delta_success")


# ── §5.3 contract: Censor ───────────────────────────────────────────────────

def test_censor_contract():
    assert set(CENSOR_SCHEMA["required"]) == {"is_valid", "refusal_reason"}
    assert _props(CENSOR_SCHEMA)["is_valid"]["type"] == "boolean"
    assert _props(CENSOR_SCHEMA)["refusal_reason"]["type"] == "string"


# ── §5.3 contract: Worker NORMAL ────────────────────────────────────────────

WORKER_REQUIRED = {
    "skill_check_reasoning", "difficulty_reasoning", "gold_reasoning",
    "ability_used", "skill_used", "difficulty", "advantage_reason", "disadvantage_reason",
    "combat_imminent", "verdict_text", "xp_award",
    "reputation_delta_success", "reputation_delta_failure", "reputation_target_npc", "updates",
}
WORKER_OPTIONAL_READ_BY_CODE = {
    "save_used", "save_dc", "rest_type", "action_type", "action_severity", "reputation_reasoning",
}
UPDATES_KEYS = {
    "minutes_passed", "location_impact", "scene_impact", "hp_damage_dice", "hp_damage_type",
    "hp_heal_dice", "gold_impact", "inventory_new", "inventory_lost", "clocks_impact",
    "condition_apply", "condition_remove",
}
UPDATES_REQUIRED_IMPLEMENTED = {
    "minutes_passed", "location_impact", "scene_impact", "gold_impact",
    "inventory_new", "inventory_lost",
}


def test_worker_normal_required_contains_5_3_keys():
    assert WORKER_REQUIRED <= set(WORKER_NORMAL_SCHEMA["required"])


def test_worker_normal_properties_contain_all_read_keys():
    assert (WORKER_REQUIRED | WORKER_OPTIONAL_READ_BY_CODE) <= set(_props(WORKER_NORMAL_SCHEMA))


def test_worker_normal_updates_properties_cover_all_keys():
    updates = _props(WORKER_NORMAL_SCHEMA)["updates"]
    assert UPDATES_KEYS <= set(updates["properties"])


def test_worker_normal_updates_required_core_keys():
    updates = _props(WORKER_NORMAL_SCHEMA)["updates"]
    assert UPDATES_REQUIRED_IMPLEMENTED <= set(updates["required"])


def test_worker_normal_updates_required_covers_full_5_3_list():
    updates = _props(WORKER_NORMAL_SCHEMA)["updates"]
    assert {"hp_damage_dice", "hp_heal_dice", "clocks_impact",
            "condition_apply", "condition_remove"} <= set(updates["required"])


def test_worker_normal_ability_and_skill_enums_match_code():
    from core.dnd_skills import SKILLS
    assert set(_enum(WORKER_NORMAL_SCHEMA, "ability_used")) == ABILITIES | {"None"}
    assert set(_enum(WORKER_NORMAL_SCHEMA, "skill_used")) == set(SKILLS) | {"None"}
    assert len(SKILLS) == 18


def test_worker_normal_save_and_rest_enums():
    assert set(_enum(WORKER_NORMAL_SCHEMA, "save_used")) == ABILITIES | {"None"}
    assert set(_enum(WORKER_NORMAL_SCHEMA, "rest_type")) == {"none", "short", "long"}


def test_worker_normal_hp_damage_type_enum_covers_5_3_types():
    types_ = set(_enum(WORKER_NORMAL_SCHEMA, "updates", "hp_damage_type"))
    assert {"physical", "fire", "cold", "poison", "acid"} <= types_


def test_worker_normal_scene_tension_is_string():
    updates = _props(WORKER_NORMAL_SCHEMA)["updates"]
    tension = updates["properties"]["clocks_impact"]["properties"]["Scene_Tension"]
    assert tension["type"] == "string"


def test_worker_normal_integer_fields_are_integers_not_enums():
    p = _props(WORKER_NORMAL_SCHEMA)
    for k in ("difficulty", "xp_award", "reputation_delta_success",
              "reputation_delta_failure", "save_dc"):
        assert p[k]["type"] == "integer" and "enum" not in p[k]


def test_worker_normal_dc_description_lists_exactly_legal_dcs():
    from core.dnd_core import LEGAL_DCS
    desc = _props(WORKER_NORMAL_SCHEMA)["difficulty"]["description"]
    for dc in LEGAL_DCS:
        assert str(dc) in desc
    for bad in (25, 28, 30):
        assert str(bad) not in desc


def test_worker_normal_combat_imminent_is_boolean():
    assert _props(WORKER_NORMAL_SCHEMA)["combat_imminent"]["type"] == "boolean"


# ── §5.3 contract: Worker COMBAT ────────────────────────────────────────────

def test_worker_combat_required_and_enums():
    from core.dnd_combat_engine import _VALID_INTENTS
    assert {"intent", "target_npc", "weapon", "spell_or_ability", "tactic",
            "move_to", "verdict_text", "reasoning"} <= set(WORKER_COMBAT_SCHEMA["required"])
    assert set(_enum(WORKER_COMBAT_SCHEMA, "intent")) == set(_VALID_INTENTS)
    assert set(_enum(WORKER_COMBAT_SCHEMA, "tactic")) == {"reckless", "normal", "cautious"}


@pytest.mark.parametrize("k", ["target_npc", "weapon", "spell_or_ability", "move_to"])
def test_worker_combat_nullable_fields(k):
    assert _props(WORKER_COMBAT_SCHEMA)[k].get("nullable") is True


# ── §5.3 contract: GM_Logic ─────────────────────────────────────────────────

NPC_FIELDS = {"Name", "Location", "Scene", "Memory_Anchor", "Relation_NPCs",
              "Inventory", "Status", "hp_current", "conditions"}


def _npc_item():
    return _props(GM_LOGIC_SCHEMA)["npc_updates"]["items"]


def test_gm_logic_required_keys():
    assert {"reasoning", "npc_reasoning", "director_notes", "companion_npcs",
            "npc_updates", "suggested_actions"} <= set(GM_LOGIC_SCHEMA["required"])


def test_gm_logic_properties_contain_mode_transition():
    assert "mode_transition" in _props(GM_LOGIC_SCHEMA)
    assert set(_enum(GM_LOGIC_SCHEMA, "mode_transition")) == {"TO_COMBAT", "TO_NORMAL"}
    assert _props(GM_LOGIC_SCHEMA)["mode_transition"].get("nullable") is True


def test_gm_logic_requires_mode_transition():
    assert "mode_transition" in GM_LOGIC_SCHEMA["required"]


def test_gm_logic_suggested_actions_exactly_four_button_intent():
    sa = _props(GM_LOGIC_SCHEMA)["suggested_actions"]
    assert sa["type"] == "array"
    assert sa["minItems"] == sa["maxItems"] == 4
    assert set(sa["items"]["required"]) == {"button", "intent"}
    assert set(sa["items"]["properties"]) == {"button", "intent"}


def test_gm_logic_director_notes_3_to_7_strings():
    dn = _props(GM_LOGIC_SCHEMA)["director_notes"]
    assert dn["minItems"] == 3 and dn["maxItems"] == 7
    assert dn["items"]["type"] == "string"


def test_gm_logic_companion_npcs_is_string_array():
    cn = _props(GM_LOGIC_SCHEMA)["companion_npcs"]
    assert cn["type"] == "array" and cn["items"]["type"] == "string"
    assert "minItems" not in cn  # may be empty


def test_gm_logic_npc_update_fields_and_required():
    assert NPC_FIELDS <= set(_npc_item()["properties"])
    assert {"Name", "Status"} <= set(_npc_item()["required"])


def test_gm_logic_npc_update_never_contains_attitude_to_player():
    props = set(_npc_item()["properties"])
    assert not {"Attitude to Player", "Attitude_to_Player", "Relation_Player"} & props


def test_gm_logic_npc_status_enum():
    assert set(_npc_item()["properties"]["Status"]["enum"]) == {"Active", "Dead", "Fled", "Unconscious"}


def test_gm_logic_npc_hp_current_integer_conditions_array():
    assert _npc_item()["properties"]["hp_current"]["type"] == "integer"
    assert _npc_item()["properties"]["conditions"]["type"] == "array"


@pytest.mark.parametrize("frozen", ["Description", "Character", "Goal", "Secrets"])
def test_gm_logic_frozen_fields_are_never_required(frozen):
    assert frozen not in _npc_item()["required"]
    if frozen in _npc_item()["properties"]:
        assert "FROZEN" in _npc_item()["properties"][frozen]["description"]


# ── Training / Initial stats / NPC actions / NPC regen ──────────────────────

def test_training_contract_and_skill_enum():
    from core.mechanics import _VALID_DND_TRAINING_SKILLS
    assert {"is_training", "is_possible", "skill", "method", "reason_if_failed"} <= \
        set(TRAINING_REQUEST_SCHEMA["required"])
    assert set(_enum(TRAINING_REQUEST_SCHEMA, "skill")) == set(_VALID_DND_TRAINING_SKILLS)
    assert set(_enum(TRAINING_REQUEST_SCHEMA, "method")) == {"solo", "mentor"}


def test_initial_stats_contract():
    p = _props(INITIAL_STATS_SCHEMA)
    assert {"suggested_class", "suggested_heritage", "ability_scores", "thought_process"} <= \
        set(INITIAL_STATS_SCHEMA["required"])
    assert "languages" in p and p["languages"]["type"] == "array"
    assert "languages" not in INITIAL_STATS_SCHEMA["required"]  # optional, defaulted by code
    assert set(p["ability_scores"]["properties"]) == ABILITIES
    assert set(p["ability_scores"]["required"]) == ABILITIES


def test_initial_stats_class_and_heritage_enums_match_code():
    from core.dnd_classes import GOT_CLASSES
    from core.dnd_heritages import HERITAGES
    assert set(_enum(INITIAL_STATS_SCHEMA, "suggested_class")) == set(GOT_CLASSES)
    assert set(_enum(INITIAL_STATS_SCHEMA, "suggested_heritage")) == set(HERITAGES)
    assert len(GOT_CLASSES) == 9 and len(HERITAGES) == 6


def test_initial_stats_keys_read_by_world_present():
    p = set(_props(INITIAL_STATS_SCHEMA))
    assert {"Ім'я", "Дім", "Поточне місцезнаходження", "Поточна сцена", "background",
            "personality_traits", "bond", "flaw"} <= p


def test_npc_combat_action_contract():
    actions = _props(NPC_COMBAT_ACTION_SCHEMA)["actions"]
    assert NPC_COMBAT_ACTION_SCHEMA["required"] == ["actions"]
    assert actions["type"] == "array"
    item = actions["items"]
    assert {"npc_name", "action", "target", "reason"} <= set(item["required"])
    assert {"npc_name", "action", "target", "weapon", "reason"} == set(item["properties"])
    assert set(item["properties"]["action"]["enum"]) == {"attack", "dodge", "flee", "help", "cast", "none"}


def test_npc_regen_contract():
    p = _props(NPC_REGEN_SCHEMA)
    assert {"reasoning", "cr", "ability_scores", "hp_max", "ac", "speed", "attacks"} <= \
        set(NPC_REGEN_SCHEMA["required"])
    assert set(p["ability_scores"]["properties"]) == ABILITIES
    assert p["attacks"]["minItems"] == 1
    assert {"name", "to_hit", "dmg"} <= set(p["attacks"]["items"]["required"])
    assert {"saves", "skills", "conditions", "tags"} <= set(p)
    from core.dnd_skills import SKILLS
    assert set(p["skills"]["properties"]) == set(SKILLS)


# ── Scene_Tension as string -> apply_system_impacts ─────────────────────────

def _apply(clocks, current=None):
    from core.mechanics import apply_system_impacts
    profile = {"Годинники": dict(current or {})}
    updated, logs = apply_system_impacts(profile, {"clocks_impact": clocks})
    return updated["Годинники"], logs


@pytest.mark.parametrize("value,start,expected", [
    ("1", None, "1/4"),
    ("-1", {"Scene_Tension": "2/4"}, "1/4"),
    ("1", {"Scene_Tension": "1/4"}, "2/4"),
    (1, None, "1/4"),                       # backward compat: int
    (-1, {"Scene_Tension": "3/4"}, "2/4"),
    ("2", {"Scene_Tension": "3/4"}, "4/4"),  # capped at max
    ("+1", None, "1/4"),
])
def test_scene_tension_numeric_string_and_int(value, start, expected):
    clocks, _ = _apply({"Scene_Tension": value}, start)
    assert clocks["Scene_Tension"] == expected


@pytest.mark.parametrize("value", ["-1", -1])
def test_scene_tension_negative_clamps_not_below_zero(value):
    clocks, _ = _apply({"Scene_Tension": value}, None)
    assert int(clocks["Scene_Tension"].split("/")[0]) >= 0


@pytest.mark.parametrize("start,expected", [
    ({"Scene_Tension": "2/4"}, None),        # <=2 -> removed
    ({"Scene_Tension": "3/4"}, "1/4"),
    ({"Scene_Tension": "4/4"}, "2/4"),
])
def test_scene_tension_clear(start, expected):
    clocks, _ = _apply({"Scene_Tension": "clear"}, start)
    assert clocks.get("Scene_Tension") == expected


def test_scene_tension_garbage_string_is_noop_not_crash():
    clocks, _ = _apply({"Scene_Tension": "banana"}, {"Scene_Tension": "2/4"})
    assert clocks["Scene_Tension"] == "2/4"


# ── Wiring: every build_strict_config(schema=...) call site ─────────────────

WIRING = [
    ("core/mechanics.py", "validate_action", "CENSOR_SCHEMA"),
    ("core/mechanics.py", "process_training_request", "TRAINING_REQUEST_SCHEMA"),
    ("core/dnd_engine.py", "resolve_normal_action", "WORKER_NORMAL_SCHEMA"),
    ("core/dnd_combat_engine.py", "parse_player_combat_intent", "WORKER_COMBAT_SCHEMA"),
    ("core/dnd_combat_engine.py", "execute_npc_actions", "NPC_COMBAT_ACTION_SCHEMA"),
    ("core/engine.py", "process_game_turn", "GM_LOGIC_SCHEMA"),
    ("core/world.py", "generate_initial_stats", "INITIAL_STATS_SCHEMA"),
    ("core/dnd_migration.py", "regenerate_one_npc", "NPC_REGEN_SCHEMA"),
]


def _schema_names_in_function(path, func):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            out = []
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "build_strict_config":
                    for kw in call.keywords:
                        if kw.arg == "schema":
                            out.append(getattr(kw.value, "id", ast.dump(kw.value)))
            return out
    raise AssertionError(f"{func} not found in {path}")


@pytest.mark.parametrize("path,func,schema_name", WIRING)
def test_call_site_passes_its_own_schema(path, func, schema_name):
    assert _schema_names_in_function(path, func) == [schema_name]


@pytest.mark.parametrize("path,func,schema_name", WIRING)
def test_call_site_schema_name_is_the_registry_object(path, func, schema_name):
    assert schema_name in {n for n in dir(prompts) if n.endswith("_SCHEMA")}
    assert getattr(prompts, schema_name) in RESPONSE_SCHEMAS.values()


def test_training_request_passes_training_schema_at_runtime():
    import asyncio
    from unittest.mock import MagicMock, patch
    from core.ai_client import build_strict_config, model_worker
    from core.mechanics import process_training_request

    captured = []

    def _gen(prompt, max_retries=6, config=None):
        captured.append(config)
        m = MagicMock()
        m.text = "{}"
        return m

    profile = {"Ім'я": "T", "level": 1, "Особисте Золото": 0}
    with patch("core.mechanics.model_worker.generate_content", side_effect=_gen), \
            patch("core.mechanics.clean_and_parse_json", return_value={"is_training": False}):
        try:
            asyncio.run(process_training_request("тренуюсь", profile))
        except Exception:
            pass  # post-LLM handling is out of scope; only the call config matters
    assert captured, "model_worker.generate_content was not called"
    expected = build_strict_config(model_worker, schema=TRAINING_REQUEST_SCHEMA).response_schema
    assert captured[0].response_schema == expected


# ── Empty values for the newly-required updates keys are true no-ops ────────

def test_empty_required_updates_values_yield_no_damage_heal_or_conditions():
    import asyncio
    from unittest.mock import patch
    from core.dnd_engine import resolve_normal_action, apply_dnd_impacts
    from test.test_dnd_engine import _dnd_profile_with_hp, _minimal_worker_data, _make_llm_response

    data = _minimal_worker_data()
    data["updates"].update({
        "hp_damage_dice": "none", "hp_heal_dice": "none",
        "clocks_impact": {}, "condition_apply": [], "condition_remove": [],
    })
    profile = _dnd_profile_with_hp()
    with patch("core.dnd_engine.build_normal_resolve_parts", return_value=("MOCK_STATIC", "MOCK")), \
            patch("core.dnd_engine.model_worker.generate_content",
                  return_value=_make_llm_response(data)):
        _, updates = asyncio.run(resolve_normal_action("Оглядаюсь", profile))

    before = dict(profile)
    updated, _ = apply_dnd_impacts(profile, updates)
    assert updated["hp_current"] == before["hp_current"] == 50
    assert not updated.get("conditions")
    assert updated.get("Годинники") == before["Годинники"]


def test_worker_normal_action_severity_is_required():
    assert "action_severity" in WORKER_NORMAL_SCHEMA["required"]
    assert set(_enum(WORKER_NORMAL_SCHEMA, "action_severity")) == {"TRIVIAL", "NORMAL", "HARD", "HORRIBLE"}
