"""Lord-sanitizer removal, Narrator dialogue rules, player_identity (first name). No network."""
import pytest

from core import prompts as P
from core.engine import _sanitize_story
from core.prompts import build_narrator_parts, build_gm_logic_parts


@pytest.mark.parametrize("text", [
    "лорд Старк кивнув.", "Лорде Ланністер, прошу.", "леді Таллі всміхнулась.",
    "Лорд Тайвін Ланністер мовчав.", "Леді Старк увійшла.", "лорду Баратеону віддали листа.",
])
def test_sanitize_keeps_lord_lady_house(text):
    out = _sanitize_story(text)
    assert out == text
    assert "місцев" not in out


def test_sanitize_still_cleans_technical_and_spaces():
    out = _sanitize_story("Сцена  Scene_Tension  E: 500 кінець.")
    assert "Scene_Tension" not in out
    assert "E: 500" not in out and "  " not in out


def test_sanitize_strips_whitespace():
    assert _sanitize_story("  лорд Старк  ") == "лорд Старк"


def _np(name="Орвелл Тестовий", house="Тестерлі", **kw):
    a = dict(user_input="дія", director_notes=["Факт"], npc_context_text="x",
             player_name=name, player_house=house, current_scene="S",
             current_location="L", impact_narrative_hints="", active_roster=["Н"])
    a.update(kw)
    return build_narrator_parts(**a)


@pytest.mark.parametrize("s", [P.NARRATOR_SYSTEM, P.NARRATOR_SYSTEM_COMBAT], ids=["normal", "combat"])
@pytest.mark.parametrize("needle", [
    "місцевий лорд", "Не вживай безособові", "2-4 репліки на сцену", "1-2 речення на NPC",
    "жодних нових обіцянок", "а не переказом", "[SECRET/GM ONLY]",
    # старі обов'язкові
    "пряма репліка", "Не вживай заїжджені звороти", "ЗАКРИТИЙ РОСТЕР",
])
def test_narrator_static_rules(s, needle):
    assert needle in s


def test_narrator_static_length_and_inheritance():
    assert len(P.NARRATOR_SYSTEM) < 7500
    assert len(P.NARRATOR_SYSTEM_COMBAT) < 9000
    assert P.NARRATOR_SYSTEM_COMBAT.startswith(P.NARRATOR_SYSTEM)


def test_narrator_static_identical_across_players():
    assert _np()[0] == _np("Ґвінет Вигадана", "Інших")[0] == P.NARRATOR_SYSTEM


def test_narrator_dynamic_dialogue_hint_normal_only():
    hint = "Словесні реакції NPC з director_notes передай прямою мовою"
    assert hint in _np()[1]
    assert hint not in _np(combat_log=["Удар."])[1]


@pytest.mark.parametrize("name,first", [("Джон Сноу", "Джон"), ("Орвелл", "Орвелл"), ("Арья Старк Тестова", "Арья")])
def test_narrator_identity_first_name(name, first):
    d = _np(name, "Старк")[1]
    ident = d[d.index("<player_identity>"):d.index("</player_identity>")]
    assert f'особисте ім\'я "{first}"' in ident
    assert "ТІЛЬКИ як" not in ident
    assert "лорд/леді Старк" in ident
    assert "Не вигадуй спорідненість" in ident
    assert "прізвищем іншого дому" in ident


@pytest.mark.parametrize("name", ["", None])
def test_narrator_identity_empty_name_fallback(name):
    d = _np(name or "", "Старк")[1]
    ident = d[d.index("<player_identity>"):d.index("</player_identity>")]
    assert 'особисте ім\'я "Герой"' in ident
    assert "прізвищем іншого дому" in ident


def _gm(hero_name, mode="NORMAL"):
    return build_gm_logic_parts(
        hero_name=hero_name, hero_house="Старк", profile_json="{}", context_knowledge="",
        event_injection="", burst_injection="", current_time_str="t", curr_region="r",
        curr_loc="l", is_traveling=False, loc_hint="", curr_scene="s", valid_locs_str="",
        valid_regions_str="", region_locs_str="", npc_context_text="", tension_label="",
        mechanics_verdict="", impact_narrative_hints="", history_text="", user_input="x",
        action_slots=["a", "b", "c", "d"], mode=mode)


@pytest.mark.parametrize("name,first", [("Джон Сноу", "Джон"), ("Орвелл", "Орвелл"), ("", "Герой"), (None, "Герой")])
def test_gm_identity(name, first):
    d = _gm(name)[1]
    ident = d[d.index("<player_identity>"):d.index("</player_identity>")]
    assert f'особисте ім\'я "{first}"' in ident
    assert "лише як" not in ident
    assert "лорд/леді Старк" in ident
    assert "Не вигадуй спорідненість" in ident
    assert "НІКОЛИ не називай героя прізвищем іншого дому" in ident


def test_gm_static_speech_fact_rule_and_purity():
    s = _gm("Зхqв Тестовий")[0]
    assert "[NORMAL]" in s and "мовленнєвий факт" in s
    assert "Зхqв" not in s
    assert _gm("Джон Сноу")[0] == _gm("Інша Особа")[0] == P.GM_LOGIC_SYSTEM
