# test/test_tavern_inn_coverage.py
"""
Tests for the tavern + inn + private-room expansion across LOCATION_SCENES
in core/world_constants.py.

Implemented: tavern scene in semi_public, inn scene in semi_public, and a
rented/private room in private for 81 of 88 locations.
7 locations are EXEMPTED (nomadic / beyond-the-Wall) and carry # TODO tavern: inline markers.

Coverage:
  A. Coverage invariants (parametrized over non-exempted locations)
  B. Format invariants (3-word limit, in-location uniqueness)
  C. Pipeline integration (is_valid_scene, format_scenes_for_prompt)
  D. Spot-check canon (Vinterfel, Wall flavour, Essos merchant flavour, Dothraki no-tavern)
  E. Regression (outskirts intact, SCENE_DEFINITIONS key count == 11, PLAYER_MAP_CATEGORIES order)

No mocks needed: world_constants.py has zero external imports.
"""

import re
import pytest

from core.world_constants import (
    LOCATION_TO_REGION,
    LOCATION_SCENES,
    SCENE_DEFINITIONS,
    PLAYER_MAP_CATEGORIES,
    get_scenes_for_location,
    is_valid_scene,
    format_scenes_for_prompt,
)

# ---------------------------------------------------------------------------
# Module-level constants (test plan §A)
# ---------------------------------------------------------------------------

TAVERN_KEYWORDS = ("таверн", "корчм")
INN_KEYWORDS = ("заїж", "постоял")
# ROOM_KEYWORDS: match against private scene names, case-insensitive substring.
# Covers: "Кімната у Таверні", "Кімната у Дворі", "Кімната у ..."
#         "Орендована Кімната", "знімана кімната", "спальня для гостей"
ROOM_KEYWORDS = ("кімната у", "орендована кімната", "знімана кімната", "спальня для гостей")

EXEMPTED: frozenset = frozenset({
    "Серце Степу",
    "Ваес Дотрак",
    "Ваес Орехас",
    "Ваес Аресак",
    "Замок Крастера",
    "Суворий Дім",
    "Кулак Перших Людей",
})

# Non-exempted locations = all canonical locations minus exempted set.
ALL_LOCATIONS = list(LOCATION_TO_REGION.keys())
NON_EXEMPTED = [loc for loc in ALL_LOCATIONS if loc not in EXEMPTED]
EXEMPTED_LIST = sorted(EXEMPTED)

# Ukrainian prepositions / conjunctions allowed lowercase inside scene names.
_LOWERCASE_ALLOWED = frozenset({
    "до", "від", "за", "над", "біля", "під", "на", "у", "в",
    "з", "із", "зі", "по", "при", "про", "через", "між",
    "серед", "без", "для", "і", "та", "або", "а", "але", "й",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_keyword(scene: str, keywords: tuple) -> bool:
    """Case-insensitive substring match of any keyword in scene name."""
    lower = scene.lower()
    return any(kw in lower for kw in keywords)


def _find_matching_scenes(loc: str, category: str, keywords: tuple) -> list:
    """Return scenes in a given category that match any keyword."""
    scenes = LOCATION_SCENES[loc].get(category, [])
    return [s for s in scenes if _has_keyword(s, keywords)]


# ===========================================================================
# A. Coverage invariants — parametrized over non-exempted locations
# ===========================================================================

# A-1: Tavern present in semi_public (non-exempted)
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_tavern_present_in_semi_public(loc):
    """Every non-exempted location must have at least one tavern scene in semi_public."""
    matches = _find_matching_scenes(loc, "semi_public", TAVERN_KEYWORDS)
    assert matches, (
        f"Location '{loc}' has no tavern scene in semi_public. "
        f"Keywords checked: {TAVERN_KEYWORDS}. "
        f"semi_public={LOCATION_SCENES[loc].get('semi_public', [])}"
    )


# A-2: Inn present in semi_public (non-exempted)
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_inn_present_in_semi_public(loc):
    """Every non-exempted location must have at least one inn scene in semi_public."""
    matches = _find_matching_scenes(loc, "semi_public", INN_KEYWORDS)
    assert matches, (
        f"Location '{loc}' has no inn scene in semi_public. "
        f"Keywords checked: {INN_KEYWORDS}. "
        f"semi_public={LOCATION_SCENES[loc].get('semi_public', [])}"
    )


# A-3: Private room present in private (non-exempted)
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_private_room_present_in_private(loc):
    """Every non-exempted location must have at least one rented/inn room scene in private."""
    matches = _find_matching_scenes(loc, "private", ROOM_KEYWORDS)
    assert matches, (
        f"Location '{loc}' has no private room scene matching ROOM_KEYWORDS. "
        f"Keywords checked: {ROOM_KEYWORDS}. "
        f"private={LOCATION_SCENES[loc].get('private', [])}"
    )


# A-4: Exempted locations have TODO marker in source
@pytest.mark.parametrize("loc", EXEMPTED_LIST)
def test_exempted_location_has_todo_marker(loc):
    """Each exempted location must have a '# TODO tavern:' comment in world_constants.py."""
    source_path = "core/world_constants.py"
    import os
    # Resolve relative to project root (two levels up from test/)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base, source_path)
    with open(full_path, encoding="utf-8") as f:
        source = f.read()

    # Find the location name in source, then check the next ~10 lines for TODO marker.
    # We search for the quoted location name followed by a colon (dict key syntax).
    pattern = re.compile(
        r'"' + re.escape(loc) + r'"\s*:\s*\{',
        re.MULTILINE,
    )
    match = pattern.search(source)
    assert match, f"Location '{loc}' not found in {source_path} with dict-key syntax"

    # Get the text in a window starting from the match position (up to 600 chars).
    window = source[match.start():match.start() + 600]
    assert "# TODO tavern:" in window, (
        f"Location '{loc}' is in EXEMPTED set but no '# TODO tavern:' comment found "
        f"within 600 chars of its dict entry. Window:\n{window[:300]}"
    )


# A-5: Exempted set entries actually exist in LOCATION_TO_REGION (no typos)
@pytest.mark.parametrize("loc", EXEMPTED_LIST)
def test_exempted_location_exists_in_location_to_region(loc):
    """Each name in EXEMPTED must exist in LOCATION_TO_REGION (catches future renames)."""
    assert loc in LOCATION_TO_REGION, (
        f"Exempted location '{loc}' is NOT in LOCATION_TO_REGION. "
        f"Possible rename? EXEMPTED set needs updating."
    )


# A-6: Tavern and inn are DISTINCT scenes (non-exempted)
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_tavern_and_inn_are_distinct_scenes(loc):
    """
    The matched tavern scene and matched inn scene must be different strings.
    No single scene may satisfy both keyword families at once.
    (Guards against a scene like 'Таверна-Постоялий Двір' satisfying both.)
    """
    tavern_matches = _find_matching_scenes(loc, "semi_public", TAVERN_KEYWORDS)
    inn_matches = _find_matching_scenes(loc, "semi_public", INN_KEYWORDS)

    # We require at least one from each family — already covered by A-1 and A-2,
    # but we need them to be distinct sets.
    assert tavern_matches and inn_matches, (
        f"Location '{loc}': need at least one tavern AND one inn scene to compare distinctness. "
        f"tavern_matches={tavern_matches}, inn_matches={inn_matches}"
    )

    # The intersection of both sets must be empty:
    # no scene should be both a tavern AND an inn.
    both = set(tavern_matches) & set(inn_matches)
    assert not both, (
        f"Location '{loc}' has scene(s) matching BOTH tavern and inn keywords: {both}. "
        f"There must be at least 2 distinct scenes in semi_public — one tavern, one inn."
    )


# ===========================================================================
# B. Format invariants
# ===========================================================================

# B-7: 3-word limit for tavern/inn/room scenes (non-exempted)
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_tavern_scene_max_three_words(loc):
    """Every matched tavern scene must be at most 3 words."""
    matches = _find_matching_scenes(loc, "semi_public", TAVERN_KEYWORDS)
    violations = [(s, len(s.split())) for s in matches if len(s.split()) > 3]
    assert not violations, (
        f"Location '{loc}' has tavern scene(s) exceeding 3 words: {violations}"
    )


@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_inn_scene_max_three_words(loc):
    """Every matched inn scene must be at most 3 words."""
    matches = _find_matching_scenes(loc, "semi_public", INN_KEYWORDS)
    violations = [(s, len(s.split())) for s in matches if len(s.split()) > 3]
    assert not violations, (
        f"Location '{loc}' has inn scene(s) exceeding 3 words: {violations}"
    )


@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_room_scene_max_three_words(loc):
    """Every matched private room scene must be at most 3 words."""
    matches = _find_matching_scenes(loc, "private", ROOM_KEYWORDS)
    violations = [(s, len(s.split())) for s in matches if len(s.split()) > 3]
    assert not violations, (
        f"Location '{loc}' has private room scene(s) exceeding 3 words: {violations}"
    )


# B-8: In-location uniqueness — tavern/inn/room scenes not duplicated across categories
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_tavern_inn_room_not_duplicated_across_categories(loc):
    """
    The tavern, inn, and room scenes must each appear in exactly one category.
    None of these scenes should be in two different categories of the same location.
    """
    tavern_matches = _find_matching_scenes(loc, "semi_public", TAVERN_KEYWORDS)
    inn_matches = _find_matching_scenes(loc, "semi_public", INN_KEYWORDS)
    room_matches = _find_matching_scenes(loc, "private", ROOM_KEYWORDS)
    candidate_scenes = set(tavern_matches) | set(inn_matches) | set(room_matches)

    seen: dict = {}  # scene -> first category
    duplicates = []
    for cat, scenes in LOCATION_SCENES.get(loc, {}).items():
        for scene in scenes:
            if scene in candidate_scenes:
                if scene in seen:
                    duplicates.append((scene, seen[scene], cat))
                else:
                    seen[scene] = cat

    assert not duplicates, (
        f"Location '{loc}' has tavern/inn/room scene(s) duplicated across categories: {duplicates}"
    )


# ===========================================================================
# C. Pipeline integration
# ===========================================================================

# C-9: is_valid_scene accepts new tavern/inn/room scenes (non-exempted)
@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_is_valid_scene_for_tavern(loc):
    """is_valid_scene must accept matched tavern scenes."""
    for scene in _find_matching_scenes(loc, "semi_public", TAVERN_KEYWORDS):
        assert is_valid_scene(loc, scene), (
            f"is_valid_scene('{loc}', '{scene}') returned False for tavern scene"
        )


@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_is_valid_scene_for_inn(loc):
    """is_valid_scene must accept matched inn scenes."""
    for scene in _find_matching_scenes(loc, "semi_public", INN_KEYWORDS):
        assert is_valid_scene(loc, scene), (
            f"is_valid_scene('{loc}', '{scene}') returned False for inn scene"
        )


@pytest.mark.parametrize("loc", NON_EXEMPTED)
def test_is_valid_scene_for_private_room(loc):
    """is_valid_scene must accept matched private room scenes."""
    for scene in _find_matching_scenes(loc, "private", ROOM_KEYWORDS):
        assert is_valid_scene(loc, scene), (
            f"is_valid_scene('{loc}', '{scene}') returned False for private room scene"
        )


# C-10: format_scenes_for_prompt renders tavern/inn/room for 3 representative locations
_C10_LOCATIONS = ["Вінтерфел", "Королівська Гавань", "Сонячний Спис"]


@pytest.mark.parametrize("loc", _C10_LOCATIONS)
def test_format_scenes_for_prompt_contains_tavern(loc):
    """format_scenes_for_prompt output must contain the tavern scene name."""
    prompt_text = format_scenes_for_prompt(loc)
    tavern_scenes = _find_matching_scenes(loc, "semi_public", TAVERN_KEYWORDS)
    assert tavern_scenes, f"No tavern scene found in '{loc}' semi_public"
    for scene in tavern_scenes:
        assert scene in prompt_text, (
            f"format_scenes_for_prompt('{loc}') does not contain tavern scene '{scene}'"
        )


@pytest.mark.parametrize("loc", _C10_LOCATIONS)
def test_format_scenes_for_prompt_contains_inn(loc):
    """format_scenes_for_prompt output must contain the inn scene name."""
    prompt_text = format_scenes_for_prompt(loc)
    inn_scenes = _find_matching_scenes(loc, "semi_public", INN_KEYWORDS)
    assert inn_scenes, f"No inn scene found in '{loc}' semi_public"
    for scene in inn_scenes:
        assert scene in prompt_text, (
            f"format_scenes_for_prompt('{loc}') does not contain inn scene '{scene}'"
        )


@pytest.mark.parametrize("loc", _C10_LOCATIONS)
def test_format_scenes_for_prompt_contains_private_room(loc):
    """format_scenes_for_prompt output must contain the private room scene name."""
    prompt_text = format_scenes_for_prompt(loc)
    room_scenes = _find_matching_scenes(loc, "private", ROOM_KEYWORDS)
    assert room_scenes, f"No private room scene found in '{loc}' private"
    for scene in room_scenes:
        assert scene in prompt_text, (
            f"format_scenes_for_prompt('{loc}') does not contain private room scene '{scene}'"
        )


# ===========================================================================
# D. Spot-check canon (named tests for headline requirements)
# ===========================================================================

def test_winterfell_has_tavern():
    """Vinterfel must have a tavern scene in semi_public."""
    matches = _find_matching_scenes("Вінтерфел", "semi_public", TAVERN_KEYWORDS)
    assert matches, (
        f"Вінтерфел missing tavern in semi_public. "
        f"semi_public={LOCATION_SCENES['Вінтерфел'].get('semi_public', [])}"
    )


def test_winterfell_has_inn():
    """Vinterfel must have an inn scene in semi_public."""
    matches = _find_matching_scenes("Вінтерфел", "semi_public", INN_KEYWORDS)
    assert matches, (
        f"Вінтерфел missing inn in semi_public. "
        f"semi_public={LOCATION_SCENES['Вінтерфел'].get('semi_public', [])}"
    )


def test_winterfell_has_private_room():
    """Vinterfel must have a private room scene in private."""
    matches = _find_matching_scenes("Вінтерфел", "private", ROOM_KEYWORDS)
    assert matches, (
        f"Вінтерфел missing private room in private. "
        f"private={LOCATION_SCENES['Вінтерфел'].get('private', [])}"
    )


def test_winterfell_tavern_scene_name():
    """Vinterfel tavern should be 'Замкова Таверна' (castle-themed)."""
    semi_public = LOCATION_SCENES["Вінтерфел"].get("semi_public", [])
    assert "Замкова Таверна" in semi_public, (
        f"Expected 'Замкова Таверна' in Вінтерфел semi_public. Got: {semi_public}"
    )


def test_winterfell_inn_scene_name():
    """Vinterfel inn should be 'Заїжджий Двір'."""
    semi_public = LOCATION_SCENES["Вінтерфел"].get("semi_public", [])
    assert "Заїжджий Двір" in semi_public, (
        f"Expected 'Заїжджий Двір' in Вінтерфел semi_public. Got: {semi_public}"
    )


def test_winterfell_private_room_name():
    """Vinterfel private room should be 'Кімната у Таверні'."""
    private = LOCATION_SCENES["Вінтерфел"].get("private", [])
    assert "Кімната у Таверні" in private, (
        f"Expected 'Кімната у Таверні' in Вінтерфел private. Got: {private}"
    )


def test_wall_fortress_tavern_flavor():
    """Стіна's tavern must be Wall-themed (e.g. 'Таверна Дозору' or similar)."""
    semi_public = LOCATION_SCENES["Стіна"].get("semi_public", [])
    tavern_scenes = [s for s in semi_public if _has_keyword(s, TAVERN_KEYWORDS)]
    assert tavern_scenes, f"Стіна missing tavern in semi_public. semi_public={semi_public}"
    # Wall-specific flavour: name should suggest Night's Watch (Дозор, Варта, Нічна, Льодова, Стіна, Чорн)
    wall_flavour_kws = ("дозор", "варт", "нічн", "льодов", "стін", "чорн")
    has_flavour = any(
        any(kw in s.lower() for kw in wall_flavour_kws)
        for s in tavern_scenes
    )
    assert has_flavour, (
        f"Стіна tavern scene(s) {tavern_scenes} lack Wall-themed flavour. "
        f"Expected one of {wall_flavour_kws} in the scene name."
    )


def test_essos_merchant_location_has_merchant_flavored_tavern():
    """
    At least one Pentos/Braavos location must have a tavern scene with a merchant
    flavour keyword: 'купецьк' (adj.), 'купц', 'торгов'.

    Covers: Прибережний Форт → 'Купецька Таверна', Торговий Квартал → 'Таверна Купців', etc.
    """
    pentos_braavos_locs = [
        loc for loc, region in LOCATION_TO_REGION.items()
        if region in ("Пентос", "Браавос") and loc not in EXEMPTED
    ]
    assert pentos_braavos_locs, "No Pentos/Braavos locations found in LOCATION_TO_REGION"

    merchant_flavour_kws = ("купецьк", "купц", "торгов")
    found_merchant_tavern = False
    for loc in pentos_braavos_locs:
        semi_public = LOCATION_SCENES.get(loc, {}).get("semi_public", [])
        for scene in semi_public:
            if _has_keyword(scene, TAVERN_KEYWORDS):
                scene_lower = scene.lower()
                if any(kw in scene_lower for kw in merchant_flavour_kws):
                    found_merchant_tavern = True
                    break
        if found_merchant_tavern:
            break

    assert found_merchant_tavern, (
        f"No Pentos/Braavos location has a merchant-flavoured tavern. "
        f"Checked locations: {pentos_braavos_locs}. "
        f"Expected at least one tavern name containing one of {merchant_flavour_kws}."
    )


@pytest.mark.parametrize("loc", [e for e in EXEMPTED_LIST if
                                   LOCATION_TO_REGION.get(e) == "Дотракійське море"])
def test_dothraki_camps_have_no_tavern(loc):
    """Dothraki camps must NOT have any tavern scene in semi_public."""
    semi_public = LOCATION_SCENES.get(loc, {}).get("semi_public", [])
    tavern_scenes = [s for s in semi_public if _has_keyword(s, TAVERN_KEYWORDS)]
    assert not tavern_scenes, (
        f"Dothraki location '{loc}' should have NO tavern scene, "
        f"but found: {tavern_scenes}"
    )


@pytest.mark.parametrize("loc", [e for e in EXEMPTED_LIST if
                                   LOCATION_TO_REGION.get(e) == "Дотракійське море"])
def test_dothraki_camps_have_no_inn(loc):
    """Dothraki camps must NOT have any inn scene in semi_public."""
    semi_public = LOCATION_SCENES.get(loc, {}).get("semi_public", [])
    inn_scenes = [s for s in semi_public if _has_keyword(s, INN_KEYWORDS)]
    assert not inn_scenes, (
        f"Dothraki location '{loc}' should have NO inn scene, "
        f"but found: {inn_scenes}"
    )


# ===========================================================================
# E. Regression
# ===========================================================================

# E-15: Outskirts still present and intact (5 representative locations)
_E15_OUTSKIRTS_SPOT = [
    ("Вінтерфел", 3),
    ("Королівська Гавань", 3),
    ("Стіна", 3),
    ("Астапор", 3),
    ("Браавос-локація", None),  # placeholder — we pick Набережна Колосу
]
_E15_CHECKS = [
    ("Вінтерфел", 3),
    ("Королівська Гавань", 3),
    ("Стіна", 3),
    ("Астапор", 3),
    ("Набережна Колосу", 3),
]


@pytest.mark.parametrize("loc,min_count", _E15_CHECKS)
def test_outskirts_still_present(loc, min_count):
    """Outskirts list should still have >= min_count entries (unchanged by this round)."""
    outskirts = LOCATION_SCENES[loc].get("outskirts", [])
    assert len(outskirts) >= min_count, (
        f"Location '{loc}' has only {len(outskirts)} outskirts entries, expected >= {min_count}. "
        f"outskirts={outskirts}"
    )


# E-16: SCENE_DEFINITIONS key count unchanged at 11
def test_scene_definitions_key_count():
    """SCENE_DEFINITIONS must have exactly 11 category keys (unchanged)."""
    count = len(SCENE_DEFINITIONS)
    assert count == 11, (
        f"SCENE_DEFINITIONS has {count} keys, expected exactly 11. "
        f"Keys: {sorted(SCENE_DEFINITIONS.keys())}"
    )


# E-17: PLAYER_MAP_CATEGORIES order — outskirts between semi_public and elite
def test_player_map_categories_outskirts_order():
    """
    PLAYER_MAP_CATEGORIES must have outskirts positioned after semi_public and
    before elite (this was fixed in a prior round and must not regress).
    """
    keys = list(PLAYER_MAP_CATEGORIES.keys())
    assert "semi_public" in keys, "semi_public missing from PLAYER_MAP_CATEGORIES"
    assert "outskirts" in keys, "outskirts missing from PLAYER_MAP_CATEGORIES"
    assert "elite" in keys, "elite missing from PLAYER_MAP_CATEGORIES"

    idx_semi = keys.index("semi_public")
    idx_out = keys.index("outskirts")
    idx_elite = keys.index("elite")

    assert idx_semi < idx_out, (
        f"semi_public (idx {idx_semi}) must come before outskirts (idx {idx_out})"
    )
    assert idx_out < idx_elite, (
        f"outskirts (idx {idx_out}) must come before elite (idx {idx_elite})"
    )


# E — Supplementary: LOCATION_SCENES covers all LOCATION_TO_REGION entries
@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_all_locations_have_location_scenes_entry(loc):
    """Every canonical location must have an entry in LOCATION_SCENES."""
    assert loc in LOCATION_SCENES, (
        f"Location '{loc}' is in LOCATION_TO_REGION but missing from LOCATION_SCENES"
    )
