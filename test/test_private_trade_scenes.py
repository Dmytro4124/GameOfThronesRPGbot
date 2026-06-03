# test/test_private_trade_scenes.py
"""
Tests for the `private` and `semi_public` scene fill implemented in
core/world_constants.py (all 88 locations).

Invariants checked:
  - Structural: private >= 3 scenes, semi_public >= 2 scenes per location
  - Format: <= 3 words per scene, non-empty strings, title-case
  - Cross-category uniqueness within each location
  - Validation pipeline: is_valid_scene, get_scenes_for_location, format_scenes_for_prompt
  - Trade content sanity: at least one trade keyword per semi_public list
  - Regression: outskirts unchanged, hub unchanged, SCENE_DEFINITIONS key count == 11

Trade keyword whitelist (substring match, case-insensitive):
  {"ринок", "ринкова", "базар", "купецьк", "торгов", "лавк", "крамниц",
   "ряд", "кузня", "пристан", "брам", "стоянк", "склад"}

  Dothraki camp locations (Ваес Дотрак, Ваес Орехас, Ваес Аресак, Серце Степу)
  use "стоянк" / "базар" variants so they ARE covered by the whitelist.

No mocks needed: world_constants.py has zero external imports.
"""

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

# Trade keyword whitelist (substring match, lower-cased scene name).
# Covers markets, bazaars, merchant rows, smithies, docks, stalls, warehouses.
_TRADE_KEYWORDS = frozenset({
    "ринок", "ринкова", "базар", "купецьк", "торгов",
    "лавк", "крамниц", "ряд", "кузня", "пристан",
    "брам", "стоянк", "склад",
})

# Locations where the lack of a classic trade keyword is a KNOWN BUG (not exempted,
# but we skip the assertion and document the finding instead — see module docstring).
_TRADE_KEYWORD_KNOWN_BUGS: frozenset[str] = frozenset()

ALL_LOCATIONS = list(LOCATION_TO_REGION.keys())


# ---------------------------------------------------------------------------
# Section 1 — Coverage: private >= 3
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_private_minimum_three(loc):
    """Every location must have at least 3 private scenes."""
    scenes = LOCATION_SCENES[loc].get("private", [])
    assert len(scenes) >= 3, (
        f"Location '{loc}' has only {len(scenes)} private scenes; expected >= 3. "
        f"scenes={scenes}"
    )


# ---------------------------------------------------------------------------
# Section 2 — Coverage: semi_public >= 2
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_semi_public_minimum_two(loc):
    """Every location must have at least 2 semi_public scenes."""
    scenes = LOCATION_SCENES[loc].get("semi_public", [])
    assert len(scenes) >= 2, (
        f"Location '{loc}' has only {len(scenes)} semi_public scenes; expected >= 2. "
        f"scenes={scenes}"
    )


# ---------------------------------------------------------------------------
# Section 3 — 3-word limit per scene in private and semi_public
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_private_word_limit(loc):
    """Every private scene name must be at most 3 words."""
    violations = [
        (s, len(s.split()))
        for s in LOCATION_SCENES[loc].get("private", [])
        if len(s.split()) > 3
    ]
    assert not violations, (
        f"Location '{loc}' has private scene(s) exceeding 3 words: {violations}"
    )


@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_semi_public_word_limit(loc):
    """Every semi_public scene name must be at most 3 words."""
    violations = [
        (s, len(s.split()))
        for s in LOCATION_SCENES[loc].get("semi_public", [])
        if len(s.split()) > 3
    ]
    assert not violations, (
        f"Location '{loc}' has semi_public scene(s) exceeding 3 words: {violations}"
    )


# ---------------------------------------------------------------------------
# Section 4 — Non-empty strings, no None
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_private_no_empty_or_none(loc):
    """Private scene names must be non-empty strings, not None."""
    for scene in LOCATION_SCENES[loc].get("private", []):
        assert scene is not None, (
            f"Location '{loc}' has None in private"
        )
        assert isinstance(scene, str), (
            f"Location '{loc}' has non-string entry in private: {scene!r}"
        )
        assert scene.strip(), (
            f"Location '{loc}' has empty/whitespace private scene name"
        )


@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_semi_public_no_empty_or_none(loc):
    """Semi_public scene names must be non-empty strings, not None."""
    for scene in LOCATION_SCENES[loc].get("semi_public", []):
        assert scene is not None, (
            f"Location '{loc}' has None in semi_public"
        )
        assert isinstance(scene, str), (
            f"Location '{loc}' has non-string entry in semi_public: {scene!r}"
        )
        assert scene.strip(), (
            f"Location '{loc}' has empty/whitespace semi_public scene name"
        )


# ---------------------------------------------------------------------------
# Section 5 — Title case + Ukrainian preposition whitelist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_private_title_case(loc):
    """
    Each word in a private scene name must start uppercase, UNLESS it is a
    recognized Ukrainian preposition/conjunction appearing after the first word.
    """
    violations = []
    for scene in LOCATION_SCENES[loc].get("private", []):
        words = scene.split()
        for i, word in enumerate(words):
            if not word:
                continue
            first_char = word[0]
            if first_char.isalpha() and first_char.islower():
                if i == 0:
                    violations.append((scene, f"first word '{word}' is lowercase"))
                elif word.lower() not in _LOWERCASE_ALLOWED:
                    violations.append(
                        (scene, f"word[{i}] '{word}' is lowercase and not a preposition")
                    )
    assert not violations, (
        f"Location '{loc}' has title-case violations in private: {violations}"
    )


@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_semi_public_title_case(loc):
    """
    Each word in a semi_public scene name must start uppercase, UNLESS it is a
    recognized Ukrainian preposition/conjunction appearing after the first word.
    """
    violations = []
    for scene in LOCATION_SCENES[loc].get("semi_public", []):
        words = scene.split()
        for i, word in enumerate(words):
            if not word:
                continue
            first_char = word[0]
            if first_char.isalpha() and first_char.islower():
                if i == 0:
                    violations.append((scene, f"first word '{word}' is lowercase"))
                elif word.lower() not in _LOWERCASE_ALLOWED:
                    violations.append(
                        (scene, f"word[{i}] '{word}' is lowercase and not a preposition")
                    )
    assert not violations, (
        f"Location '{loc}' has title-case violations in semi_public: {violations}"
    )


# ---------------------------------------------------------------------------
# Section 6 — In-location uniqueness across ALL categories
# (No scene name appears in two different categories of the same location)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_no_cross_category_duplicates(loc):
    """
    No scene name may appear in more than one category within the same location.
    E.g. "Покої Лорда" cannot be both private AND elite for the same location.
    """
    seen: dict[str, str] = {}  # scene_name -> first_seen_category
    duplicates = []
    for cat, scenes in LOCATION_SCENES.get(loc, {}).items():
        for scene in scenes:
            if scene in seen:
                duplicates.append((scene, seen[scene], cat))
            else:
                seen[scene] = cat
    assert not duplicates, (
        f"Location '{loc}' has scene(s) appearing in multiple categories: "
        f"{duplicates}"
    )


# ---------------------------------------------------------------------------
# Section 7 — Validation pipeline: is_valid_scene accepts new scenes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_is_valid_scene_for_private(loc):
    """is_valid_scene must return True for every private scene of every location."""
    for scene in LOCATION_SCENES[loc].get("private", []):
        assert is_valid_scene(loc, scene), (
            f"is_valid_scene('{loc}', '{scene}') returned False for private scene"
        )


@pytest.mark.parametrize("loc", ALL_LOCATIONS)
def test_is_valid_scene_for_semi_public(loc):
    """is_valid_scene must return True for every semi_public scene of every location."""
    for scene in LOCATION_SCENES[loc].get("semi_public", []):
        assert is_valid_scene(loc, scene), (
            f"is_valid_scene('{loc}', '{scene}') returned False for semi_public scene"
        )


# ---------------------------------------------------------------------------
# Section 8 — get_scenes_for_location includes new entries (5 representative)
# ---------------------------------------------------------------------------

_PIPELINE_SPOT_CHECK_LOCATIONS = [
    "Вінтерфел",
    "Королівська Гавань",
    "Сонячний Спис",
    "Набережна Колосу",
    "Ваес Дотрак",
]


@pytest.mark.parametrize("loc", _PIPELINE_SPOT_CHECK_LOCATIONS)
def test_get_scenes_includes_private(loc):
    """get_scenes_for_location must contain all private scenes for spot-check locations."""
    all_scenes = get_scenes_for_location(loc)
    for scene in LOCATION_SCENES[loc].get("private", []):
        assert scene in all_scenes, (
            f"get_scenes_for_location('{loc}') does not contain private scene '{scene}'"
        )


@pytest.mark.parametrize("loc", _PIPELINE_SPOT_CHECK_LOCATIONS)
def test_get_scenes_includes_semi_public(loc):
    """get_scenes_for_location must contain all semi_public scenes for spot-check locations."""
    all_scenes = get_scenes_for_location(loc)
    for scene in LOCATION_SCENES[loc].get("semi_public", []):
        assert scene in all_scenes, (
            f"get_scenes_for_location('{loc}') does not contain semi_public scene '{scene}'"
        )


# ---------------------------------------------------------------------------
# Section 9 — format_scenes_for_prompt renders both categories
# (3 representative locations: Вінтерфел, Королівська Гавань, Сонячний Спис)
# ---------------------------------------------------------------------------

_FORMAT_CHECK_LOCATIONS = ["Вінтерфел", "Королівська Гавань", "Сонячний Спис"]

_PRIVATE_LABEL = SCENE_DEFINITIONS["private"]["label"]     # "ПРИВАТНА ЗОНА"
_SEMI_PUBLIC_LABEL = SCENE_DEFINITIONS["semi_public"]["label"]  # "НАПІВПУБЛІЧНА ЗОНА"


@pytest.mark.parametrize("loc", _FORMAT_CHECK_LOCATIONS)
def test_format_scenes_includes_private_label(loc):
    """format_scenes_for_prompt must contain the SCENE_DEFINITIONS private label."""
    formatted = format_scenes_for_prompt(loc)
    assert _PRIVATE_LABEL in formatted, (
        f"format_scenes_for_prompt('{loc}') does not contain private label '{_PRIVATE_LABEL}'"
    )


@pytest.mark.parametrize("loc", _FORMAT_CHECK_LOCATIONS)
def test_format_scenes_includes_semi_public_label(loc):
    """format_scenes_for_prompt must contain the SCENE_DEFINITIONS semi_public label."""
    formatted = format_scenes_for_prompt(loc)
    assert _SEMI_PUBLIC_LABEL in formatted, (
        f"format_scenes_for_prompt('{loc}') does not contain semi_public label "
        f"'{_SEMI_PUBLIC_LABEL}'"
    )


@pytest.mark.parametrize("loc", _FORMAT_CHECK_LOCATIONS)
def test_format_scenes_includes_sample_private_scene(loc):
    """format_scenes_for_prompt must contain at least one private scene from the data."""
    formatted = format_scenes_for_prompt(loc)
    private_scenes = LOCATION_SCENES[loc].get("private", [])
    assert private_scenes, f"No private scenes defined for '{loc}' — test setup error"
    found = any(scene in formatted for scene in private_scenes)
    assert found, (
        f"format_scenes_for_prompt('{loc}') contains none of the private scenes: "
        f"{private_scenes}"
    )


@pytest.mark.parametrize("loc", _FORMAT_CHECK_LOCATIONS)
def test_format_scenes_includes_sample_semi_public_scene(loc):
    """format_scenes_for_prompt must contain at least one semi_public scene from the data."""
    formatted = format_scenes_for_prompt(loc)
    semi_public_scenes = LOCATION_SCENES[loc].get("semi_public", [])
    assert semi_public_scenes, f"No semi_public scenes defined for '{loc}' — test setup error"
    found = any(scene in formatted for scene in semi_public_scenes)
    assert found, (
        f"format_scenes_for_prompt('{loc}') contains none of the semi_public scenes: "
        f"{semi_public_scenes}"
    )


# ---------------------------------------------------------------------------
# Section 10 — Trade content sanity: at least ONE semi_public scene with trade keyword
# (Замок Крастера is a KNOWN BUG — see module docstring and Знайдені баги)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", [l for l in ALL_LOCATIONS if l not in _TRADE_KEYWORD_KNOWN_BUGS])
def test_semi_public_has_trade_keyword(loc):
    """
    For each location, at least one semi_public scene must contain a trade keyword.
    Keyword list (substring, case-insensitive):
      ринок, ринкова, базар, купецьк, торгов, лавк, крамниц, ряд, кузня,
      пристан, брам, стоянк, склад.
    """
    scenes = LOCATION_SCENES[loc].get("semi_public", [])
    has_trade = any(
        any(kw in scene.lower() for kw in _TRADE_KEYWORDS)
        for scene in scenes
    )
    assert has_trade, (
        f"Location '{loc}' has no trade keyword in any semi_public scene. "
        f"scenes={scenes}. "
        f"Add at least one market/bazaar/shop/smithy/dock scene."
    )


@pytest.mark.parametrize("loc", list(_TRADE_KEYWORD_KNOWN_BUGS))
def test_semi_public_trade_keyword_known_bug_documented(loc):
    """
    Locations in _TRADE_KEYWORD_KNOWN_BUGS lack trade keywords — this is a known bug.
    This test documents the status: it PASSES only to acknowledge the finding.
    If the bug is fixed (trade keyword added), remove the location from
    _TRADE_KEYWORD_KNOWN_BUGS and it will fall under the normal assertion above.
    """
    scenes = LOCATION_SCENES[loc].get("semi_public", [])
    has_trade = any(
        any(kw in scene.lower() for kw in _TRADE_KEYWORDS)
        for scene in scenes
    )
    # We do NOT assert has_trade here — the finding is documented in the QA report.
    # This test acts as a sentinel: if has_trade becomes True the dev should clean up
    # _TRADE_KEYWORD_KNOWN_BUGS.
    if has_trade:
        pytest.fail(
            f"Location '{loc}' is listed in _TRADE_KEYWORD_KNOWN_BUGS but now "
            f"DOES have a trade keyword. Remove it from _TRADE_KEYWORD_KNOWN_BUGS."
        )


# ---------------------------------------------------------------------------
# Section 11 — Regression: outskirts unchanged for 5 spot-check locations
# ---------------------------------------------------------------------------

_OUTSKIRTS_REGRESSION = [
    ("Вінтерфел",          ["Богоріч Вінтерфелу", "Сосновий Ліс", "Королівський Тракт"]),
    ("Королівська Гавань", ["Передмістя Гавані", "Берег Чорноводи", "Королівський Тракт"]),
    ("Кастерлі Рок",       ["Золоті Пагорби", "Берег Заходу", "Тракт Левів"]),
    ("Стіна",              ["Стара Велика Чи", "Зимовий Ліс", "Замерзла Пустка"]),
    ("Сонячний Спис",      ["Піщана Пустеля", "Червоні Гори", "Дорнійський Тракт"]),
]


@pytest.mark.parametrize("loc,expected_outskirts", _OUTSKIRTS_REGRESSION)
def test_outskirts_unchanged(loc, expected_outskirts):
    """Adding private/semi_public scenes must not modify pre-existing outskirts entries."""
    outskirts = LOCATION_SCENES[loc].get("outskirts", [])
    for scene in expected_outskirts:
        assert scene in outskirts, (
            f"Pre-existing outskirts scene '{scene}' missing from '{loc}'. "
            f"outskirts={outskirts}"
        )


# ---------------------------------------------------------------------------
# Section 12 — Regression: hub category unchanged for Вінтерфел
# ---------------------------------------------------------------------------

def test_vinterfell_hub_unchanged():
    """Adding private/semi_public scenes must not affect Вінтерфел's hub list."""
    hub = LOCATION_SCENES["Вінтерфел"].get("hub", [])
    expected = ["Замкові Ворота", "Внутрішній Двір", "Замкове Подвір'я"]
    for scene in expected:
        assert scene in hub, (
            f"Pre-existing hub scene '{scene}' missing from Вінтерфел hub={hub}"
        )


# ---------------------------------------------------------------------------
# Section 13 — No new SCENE_DEFINITIONS entries: exactly 11 keys
# ---------------------------------------------------------------------------

_EXPECTED_SCENE_DEF_KEYS = frozenset({
    "hub", "semi_public", "restricted", "private", "stealth",
    "elite", "labor", "sacred", "containment", "military", "outskirts",
})


def test_scene_definitions_key_count():
    """SCENE_DEFINITIONS must have exactly 11 keys — no new categories added."""
    actual = set(SCENE_DEFINITIONS.keys())
    assert len(actual) == 11, (
        f"SCENE_DEFINITIONS has {len(actual)} keys; expected 11. "
        f"Extra: {actual - _EXPECTED_SCENE_DEF_KEYS}, "
        f"Missing: {_EXPECTED_SCENE_DEF_KEYS - actual}"
    )


def test_scene_definitions_exact_keys():
    """SCENE_DEFINITIONS must contain exactly the 11 canonical category keys."""
    actual = set(SCENE_DEFINITIONS.keys())
    assert actual == _EXPECTED_SCENE_DEF_KEYS, (
        f"SCENE_DEFINITIONS key mismatch. "
        f"Extra: {actual - _EXPECTED_SCENE_DEF_KEYS}, "
        f"Missing: {_EXPECTED_SCENE_DEF_KEYS - actual}"
    )


# ---------------------------------------------------------------------------
# Section 14–16 — Canon adherence spot checks
# ---------------------------------------------------------------------------

def test_braavos_has_iron_bank_scene():
    """
    Браавос (Набережна Колосу) must have 'Лавка Залізного Банку' in semi_public.
    The Залізний Банк is a defining feature of Браавос.
    """
    semi_public = LOCATION_SCENES["Набережна Колосу"].get("semi_public", [])
    assert "Лавка Залізного Банку" in semi_public, (
        f"'Лавка Залізного Банку' not found in Набережна Колосу semi_public: {semi_public}"
    )


def test_sunspear_has_water_gardens_in_private():
    """
    Сонячний Спис must have 'Водяні Сади' in private.
    The Water Gardens are the private retreat of House Martell.
    """
    private = LOCATION_SCENES["Сонячний Спис"].get("private", [])
    assert "Водяні Сади" in private, (
        f"'Водяні Сади' not found in Сонячний Спис private: {private}"
    )


def test_oldtown_has_book_shop_in_semi_public():
    """
    Старомісто must have 'Книжкова Лавка' in semi_public.
    Oldtown is the seat of the Citadel and book trade.
    """
    semi_public = LOCATION_SCENES["Старомісто"].get("semi_public", [])
    assert "Книжкова Лавка" in semi_public, (
        f"'Книжкова Лавка' not found in Старомісто semi_public: {semi_public}"
    )


def test_oldtown_has_citadel_library_in_private():
    """
    Старомісто must have 'Цитадельна Бібліотека' in private.
    The Citadel library is a restricted private area of scholarly work.
    """
    private = LOCATION_SCENES["Старомісто"].get("private", [])
    assert "Цитадельна Бібліотека" in private, (
        f"'Цитадельна Бібліотека' not found in Старомісто private: {private}"
    )
