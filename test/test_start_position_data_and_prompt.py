"""Дані та промпт для стартової позиції героя (A2/A3 audit + prompt rule 1).

- build_initial_stats_prompt: правило 1, валідний few-shot, antiexample.
- Аудит canon NPC: (Location, Scene) мають бути валідними за LOCATION_SCENES; відомі невідповідності
  зафіксовані як xfail(strict=True) -- окрема задача по даних (при виправленні strict-xfail впаде -> прибрати з _KNOWN_BAD).
Без мережі/Sheets.
"""
import re

import pytest

from core.prompts import build_initial_stats_prompt
from core.world_constants import (
    LOCATION_SCENES, VALID_LOCATIONS_ORDERED, format_scenes_for_prompt,
    get_scenes_for_location, is_valid_location, is_valid_scene,
)
from database.canon_npc import get_canon_npcs_copy


def _prompt(region="Ессос"):
    locs = ", ".join(f'"{l}"' for l in VALID_LOCATIONS_ORDERED)
    return build_initial_stats_prompt("Тест", "Дім", region, locs,
                                      scenes_block_str=format_scenes_for_prompt(region))


# ------------------------------ prompt ------------------------------

def test_rule1_present():
    p = _prompt()
    assert "2 рівні, Location → Scene" in p
    assert "ДОСЛІВНО одна зі сцен САМЕ ОБРАНОЇ локації" in p
    assert "Перевага публічним/напівпублічним" in p


def test_antiexample_about_foreign_scene_present():
    assert "Поточна сцена зі списку ІНШОЇ локації або вигадана" in _prompt()


def _fewshot_pair(p):
    block = p.split("<few_shot_example>", 1)[1].split("</few_shot_example>", 1)[0]
    loc = re.search(r'"Поточне місцезнаходження":\s*"([^"]+)"', block).group(1)
    scene = re.search(r'"Поточна сцена":\s*"([^"]+)"', block).group(1)
    return loc, scene, block


def test_fewshot_pair_valid_per_location_scenes():
    loc, scene, _ = _fewshot_pair(_prompt())
    assert loc in LOCATION_SCENES
    assert scene in get_scenes_for_location(loc)
    assert is_valid_scene(loc, scene)


def test_fewshot_pair_is_kvartal_magistriv_taverna():
    loc, scene, block = _fewshot_pair(_prompt())
    assert (loc, scene) == ("Квартал Магістрів", "Таверна Купців")
    assert "Таверна Купців" in block  # CoT згадує ту саму сцену


def test_fewshot_scene_is_public_not_private():
    loc, scene, _ = _fewshot_pair(_prompt())
    assert scene in LOCATION_SCENES[loc]["hub"] + LOCATION_SCENES[loc].get("semi_public", [])


def test_all_quoted_scene_loc_pairs_in_prompt_text_valid():
    """Кожна пара 'Локація'/'сцена' із CoT few-shot: згадана у лапках сцена належить згаданій локації."""
    p = _prompt()
    block = p.split("<few_shot_example>", 1)[1].split("</few_shot_example>", 1)[0]
    cot = re.search(r'"thought_process":\s*"(.*?)",\s*\n\s*"narrative_intro"', block, re.S).group(1)
    locs = re.findall(r"'([^']+)'", cot)
    known_locs = [x for x in locs if x in LOCATION_SCENES]
    assert known_locs, "CoT має згадувати локацію з LOCATION_SCENES"
    for loc in known_locs:
        scenes_in_cot = [x for x in locs if x in get_scenes_for_location(loc)]
        assert scenes_in_cot, f"CoT не містить сцени локації {loc}"


# ------------------------------ canon audit ------------------------------

_KNOWN_BAD = {
    "Русе Болтон": "Дредфорт: сцена 'Зала Лордів' відсутня у LOCATION_SCENES['Дредфорт']",
    "Рамсі Сноу": "Дредфорт: сцена 'Тренувальний Двір' відсутня",
    "Манс Розбійник": "'За Стіною' не є валідною локацією",
    "Тормунд Велетозгуба": "'За Стіною' не є валідною локацією",
    "Ігрітт": "'За Стіною' не є валідною локацією",
    "Еурон Грейджой": "'Залізні Острови' не є валідною локацією (це регіон)",
    "Віктаріон Грейджой": "'Залізні Острови' не є валідною локацією (це регіон)",
    "Ейєрон Мокроголовий": "'Залізні Острови' не є валідною локацією (це регіон)",
    "Міррі Маз Дуур": "Лхазар: сцена 'Намет Лікувальниці' відсутня",
    "Ширен Баратеон": "Драконів Камінь: сцена 'Дитячі Покої' відсутня",
}

_NPCS = get_canon_npcs_copy()


def _params():
    out = []
    for n in _NPCS:
        marks = []
        if n["Name"] in _KNOWN_BAD:
            marks.append(pytest.mark.xfail(strict=True, reason="відома невідповідність canon-даних: " + _KNOWN_BAD[n["Name"]]))
        out.append(pytest.param(n, marks=marks, id=n["Name"]))
    return out


@pytest.mark.parametrize("npc", _params())
def test_canon_npc_location_scene_valid(npc):
    assert is_valid_location(npc["Location"]), npc["Location"]
    assert is_valid_scene(npc["Location"], npc["Scene"]), (npc["Location"], npc["Scene"])


def test_known_bad_names_exist_in_canon():
    names = {n["Name"] for n in _NPCS}
    assert set(_KNOWN_BAD) <= names


@pytest.mark.parametrize("name,loc,scene", [
    ("Візеріс Таргарієн", "Квартал Магістрів", "Маєток Ілліріо"),
    ("Еддард Старк", "Вінтерфел", "Великий Зал"),
])
def test_anchoring_candidates_have_expected_valid_position(name, loc, scene):
    npc = next(n for n in _NPCS if n["Name"] == name)
    assert (npc["Location"], npc["Scene"]) == (loc, scene)
    assert is_valid_scene(loc, scene)


# ------------------------------ локації у прикладах промпту ------------------------------

def test_all_example_locations_in_initial_stats_prompt_valid():
    p = _prompt()
    locs = re.findall(r'"Поточне місцезнаходження":\s*"([^"<]+)"', p)
    locs += re.findall(r'Поточне місцезнаходження="[^"]*"[^✅\n]*✅\s*"([^"]+)"', p)
    assert locs, "у промпті мають бути приклади локацій"
    for loc in locs:
        assert is_valid_location(loc), f"невалідна локація у прикладі промпту: {loc!r}"
    assert "Вінтерфел" in locs
