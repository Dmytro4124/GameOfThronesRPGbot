# test/test_outskirts_scenes.py
"""
Tests for the `outskirts` scene category added across all 88 locations in
core/world_constants.py.

Covers:
  - Structural invariants (SCENE_DEFINITIONS, LOCATION_SCENES coverage)
  - Validation pipeline (is_valid_scene, get_scenes_for_location, format_scenes_for_prompt)
  - Regression (pre-existing hub scenes untouched, PLAYER_MAP_CATEGORIES, fallback path)
  - TODO marker count

No mocks needed: world_constants.py has zero external imports.
"""

import re
import pytest

from core.world_constants import (
    LOCATION_TO_REGION,
    LOCATION_SCENES,
    SCENE_DEFINITIONS,
    PLAYER_MAP_CATEGORIES,
    _FALLBACK_SCENES,
    get_scenes_for_location,
    is_valid_scene,
    format_scenes_for_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Ukrainian prepositions / conjunctions that are expected to stay lowercase
# when they appear as non-first words in a scene name.
_LOWERCASE_ALLOWED = frozenset({
    "до", "від", "за", "над", "біля", "під", "на", "у", "в",
    "з", "із", "зі", "по", "при", "про", "через", "між",
    "серед", "без", "для", "і", "та", "або", "а", "але", "й",
})

ALL_LOCATIONS = list(LOCATION_TO_REGION.keys())


# ---------------------------------------------------------------------------
# 1. Category exists in SCENE_DEFINITIONS with the required keys
# ---------------------------------------------------------------------------

def test_outskirts_in_scene_definitions():
    """outskirts key must be present in SCENE_DEFINITIONS."""
    assert "outskirts" in SCENE_DEFINITIONS, (
        "SCENE_DEFINITIONS is missing the 'outskirts' key"
    )


def test_outskirts_scene_definition_has_label():
    assert "label" in SCENE_DEFINITIONS["outskirts"], (
        "SCENE_DEFINITIONS['outskirts'] must have 'label'"
    )
    assert SCENE_DEFINITIONS["outskirts"]["label"], "label must be non-empty"


def test_outskirts_scene_definition_has_npc_hint():
    assert "npc_hint" in SCENE_DEFINITIONS["outskirts"], (
        "SCENE_DEFINITIONS['outskirts'] must have 'npc_hint'"
    )


def test_outskirts_scene_definition_has_forbidden_hint():
    assert "forbidden_hint" in SCENE_DEFINITIONS["outskirts"], (
        "SCENE_DEFINITIONS['outskirts'] must have 'forbidden_hint'"
    )


# ---------------------------------------------------------------------------
# 2. All 88 locations have outskirts in LOCATION_SCENES
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_location_in_location_scenes(loc):
    """Every LOCATION_TO_REGION entry must appear in LOCATION_SCENES."""
    assert loc in LOCATION_SCENES, (
        f"Location '{loc}' is in LOCATION_TO_REGION but missing from LOCATION_SCENES"
    )


@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_outskirts_key_present(loc):
    """Every location entry in LOCATION_SCENES must have an 'outskirts' key."""
    assert "outskirts" in LOCATION_SCENES[loc], (
        f"Location '{loc}' is missing the 'outskirts' category in LOCATION_SCENES"
    )


# ---------------------------------------------------------------------------
# 3. Scene count per location: 2-3 outskirts scenes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_outskirts_scene_count(loc):
    """Each location must have 2 to 3 outskirts scenes (inclusive)."""
    scenes = LOCATION_SCENES[loc]["outskirts"]
    count = len(scenes)
    assert 2 <= count <= 3, (
        f"Location '{loc}' has {count} outskirts scenes; expected 2 or 3. scenes={scenes}"
    )


# ---------------------------------------------------------------------------
# 4. Word limit per scene: <= 3 words (mechanics.py line 354 truncates at 3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_outskirts_word_limit(loc):
    """Every outskirts scene name must be at most 3 words."""
    violations = [
        (s, len(s.split()))
        for s in LOCATION_SCENES[loc]["outskirts"]
        if len(s.split()) > 3
    ]
    assert not violations, (
        f"Location '{loc}' has outskirts scene(s) exceeding 3 words: {violations}"
    )


# ---------------------------------------------------------------------------
# 5. Non-empty strings — no empty names, no None values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_outskirts_no_empty_or_none(loc):
    """Outskirts scene names must be non-empty strings."""
    scenes = LOCATION_SCENES[loc]["outskirts"]
    for scene in scenes:
        assert scene is not None, f"Location '{loc}' has None in outskirts"
        assert isinstance(scene, str), (
            f"Location '{loc}' has non-string entry in outskirts: {scene!r}"
        )
        assert scene.strip(), (
            f"Location '{loc}' has an empty/whitespace scene name in outskirts"
        )


# ---------------------------------------------------------------------------
# 6. Title case — first word uppercase; non-first words allowed lowercase only
#    if they are recognized Ukrainian prepositions/conjunctions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_outskirts_title_case(loc):
    """
    Each word in an outskirts scene name must start uppercase, UNLESS it is a
    recognized Ukrainian preposition/conjunction appearing after the first word.
    """
    violations = []
    for scene in LOCATION_SCENES[loc]["outskirts"]:
        words = scene.split()
        for i, word in enumerate(words):
            if not word:
                continue
            first_char = word[0]
            if first_char.isalpha() and first_char.islower():
                # First word must always be capitalized
                if i == 0:
                    violations.append((scene, f"first word '{word}' is lowercase"))
                # Non-first word lowercase is acceptable only if it's a preposition
                elif word.lower() not in _LOWERCASE_ALLOWED:
                    violations.append(
                        (scene, f"word[{i}] '{word}' is lowercase and not a preposition")
                    )
    assert not violations, (
        f"Location '{loc}' has title-case violations in outskirts: {violations}"
    )


# ---------------------------------------------------------------------------
# 7. is_valid_scene picks up outskirts scenes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_is_valid_scene_for_outskirts(loc):
    """is_valid_scene must return True for every outskirts scene of every location."""
    for scene in LOCATION_SCENES[loc]["outskirts"]:
        assert is_valid_scene(loc, scene), (
            f"is_valid_scene('{loc}', '{scene}') returned False"
        )


# ---------------------------------------------------------------------------
# 8. get_scenes_for_location includes all outskirts entries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_get_scenes_includes_outskirts(loc):
    """get_scenes_for_location must contain all outskirts scenes for every location."""
    all_scenes = get_scenes_for_location(loc)
    for scene in LOCATION_SCENES[loc]["outskirts"]:
        assert scene in all_scenes, (
            f"get_scenes_for_location('{loc}') does not contain outskirts scene '{scene}'"
        )


# ---------------------------------------------------------------------------
# 9. format_scenes_for_prompt includes outskirts block for representative locations
# ---------------------------------------------------------------------------

_REPRESENTATIVE_LOCATIONS = [
    ("Вінтерфел",          "Богоріч Вінтерфелу"),
    ("Королівська Гавань", "Передмістя Гавані"),
    ("Сонячний Спис",      "Піщана Пустеля"),
]


@pytest.mark.parametrize("loc,expected_scene", _REPRESENTATIVE_LOCATIONS)
def test_format_scenes_includes_outskirts_label(loc, expected_scene):
    """format_scenes_for_prompt must contain the SCENE_DEFINITIONS outskirts label."""
    formatted = format_scenes_for_prompt(loc)
    label = SCENE_DEFINITIONS["outskirts"]["label"]
    assert label in formatted, (
        f"format_scenes_for_prompt('{loc}') does not contain the outskirts label '{label}'"
    )


@pytest.mark.parametrize("loc,expected_scene", _REPRESENTATIVE_LOCATIONS)
def test_format_scenes_includes_outskirts_scene_name(loc, expected_scene):
    """format_scenes_for_prompt must contain at least one expected outskirts scene name."""
    formatted = format_scenes_for_prompt(loc)
    assert expected_scene in formatted, (
        f"format_scenes_for_prompt('{loc}') does not contain outskirts scene '{expected_scene}'"
    )


# ---------------------------------------------------------------------------
# 10. Regression — pre-existing hub entries untouched
# ---------------------------------------------------------------------------

_HUB_REGRESSION = [
    ("Вінтерфел",          ["Замкові Ворота", "Внутрішній Двір", "Замкове Подвір'я"]),
    ("Королівська Гавань", ["Вулиці Міста", "Ринкова Площа", "Міські Ворота"]),
    ("Кастерлі Рок",       ["Замкові Ворота", "Внутрішній Двір"]),
    ("Стіна",              ["Чорний Замок", "Внутрішній Двір Варти"]),
    ("Сонячний Спис",      ["Замкові Ворота", "Внутрішній Двір"]),
]


@pytest.mark.parametrize("loc,expected_hub_scenes", _HUB_REGRESSION)
def test_hub_scenes_untouched(loc, expected_hub_scenes):
    """Adding outskirts must not remove or rename pre-existing hub scenes."""
    hub = LOCATION_SCENES[loc].get("hub", [])
    for scene in expected_hub_scenes:
        assert scene in hub, (
            f"Pre-existing hub scene '{scene}' is missing from '{loc}' hub={hub}"
        )


# ---------------------------------------------------------------------------
# 11. PLAYER_MAP_CATEGORIES has outskirts with emoji "🌿"
# ---------------------------------------------------------------------------

def test_player_map_categories_has_outskirts():
    """PLAYER_MAP_CATEGORIES must include the 'outskirts' key."""
    assert "outskirts" in PLAYER_MAP_CATEGORIES, (
        "PLAYER_MAP_CATEGORIES is missing the 'outskirts' key"
    )


def test_player_map_categories_outskirts_emoji():
    """PLAYER_MAP_CATEGORIES['outskirts'] emoji must be the plant leaf (U+1F33F)."""
    expected_emoji = "\U0001F33F"  # 🌿 HERB
    emoji, label = PLAYER_MAP_CATEGORIES["outskirts"]
    assert emoji == expected_emoji, (
        "Expected herb emoji (U+1F33F) for outskirts, got " + repr(emoji)
    )


def test_player_map_categories_outskirts_label_nonempty():
    """PLAYER_MAP_CATEGORIES['outskirts'] label must be non-empty."""
    emoji, label = PLAYER_MAP_CATEGORIES["outskirts"]
    assert label and isinstance(label, str), (
        f"outskirts label must be a non-empty string, got {label!r}"
    )


# ---------------------------------------------------------------------------
# 12. Fallback path — unknown location returns _FALLBACK_SCENES
# ---------------------------------------------------------------------------

def test_unknown_location_returns_fallback():
    """get_scenes_for_location with an unknown location must return _FALLBACK_SCENES."""
    result = get_scenes_for_location("Несуществуючий замок")
    assert result == _FALLBACK_SCENES, (
        f"Expected _FALLBACK_SCENES for unknown location, got {result}"
    )


def test_fallback_is_nonempty():
    """_FALLBACK_SCENES must not be empty (sanity guard)."""
    assert len(_FALLBACK_SCENES) > 0, "_FALLBACK_SCENES is empty — fallback is broken"


# ---------------------------------------------------------------------------
# 13. TODO marker count in source file
# ---------------------------------------------------------------------------

def test_todo_outskirts_marker_count():
    """
    mechanics-dev reported 4 TODO outskirts markers in world_constants.py.
    Assert the exact count so regressions (forgotten TODOs or unannounced additions)
    are caught.
    """
    import pathlib
    source = pathlib.Path(__file__).parent.parent / "core" / "world_constants.py"
    text = source.read_text(encoding="utf-8")
    count = text.count("# TODO outskirts:")
    assert count == 4, (
        f"Expected 4 'TODO outskirts:' markers in world_constants.py, found {count}. "
        "Update this test if the count changes intentionally."
    )


# ---------------------------------------------------------------------------
# Additional: no duplicate outskirts scenes within a single location
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_no_duplicate_outskirts_scenes(loc):
    """Outskirts scene list must not contain duplicate entries for a location."""
    scenes = LOCATION_SCENES[loc]["outskirts"]
    assert len(scenes) == len(set(scenes)), (
        f"Location '{loc}' has duplicate outskirts scenes: "
        f"{[s for s in scenes if scenes.count(s) > 1]}"
    )
