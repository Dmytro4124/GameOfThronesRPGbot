"""Unit tests for climate-aware event filtering (4-fix package, 2026-05).

Covers:
- REGION_CLIMATE_MAP spot-checks for all 21 canonical regions.
- EVENT_PLAUSIBILITY structure (blizzard cold-only, cortege/war universal).
- Engine filter logic replicated in isolation:
  - Day 5 blizzard in Пентос (mediterranean) -> event_injection skipped.
  - Day 5 blizzard in Північ (cold) -> event injected.
  - Day 5 blizzard in unknown region (defaults to 'temperate') -> event skipped.
  - Day 14 cortege (universal '*') -> injected in any region.
  - Day 1 (no event key) -> event_injection stays empty.
"""

import pytest

from core.world_constants import REGION_CLIMATE_MAP, EVENT_PLAUSIBILITY


# ---------------------------------------------------------------------------
# Helper — replicate engine.py filter logic without importing engine
# (avoids heavy async/gspread/Gemini deps in unit tests).
# The logic is intentionally kept as a pure function mirror of engine.py ~L1023.
# ---------------------------------------------------------------------------

def _filter_event(current_day: int, region: str, world_events: dict) -> str:
    """Mirror of the engine.py climate-filter block.

    Returns the event_injection string (non-empty) if the event is plausible
    for the given region/climate, else returns "".
    """
    if current_day not in world_events:
        return ""

    region_climate = REGION_CLIMATE_MAP.get(region, "temperate")
    plausible_climates = EVENT_PLAUSIBILITY.get(current_day, ["*"])

    if "*" in plausible_climates or region_climate in plausible_climates:
        return f"[EVENT: {world_events[current_day]}]"
    else:
        return ""


_WORLD_EVENTS = {
    5: "Починається сильна хуртовина.",
    14: "До міста прибуває величезний королівський кортеж.",
    30: "Починається війна. Оголошено загальну мобілізацію.",
}


# ---------------------------------------------------------------------------
# 1. REGION_CLIMATE_MAP spot-checks — all 21 canonical regions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("region,expected_climate", [
    ("Північ",                  "cold"),
    ("За Стіною",               "cold"),
    ("Долина Аррен",            "cold"),
    ("Залізні Острови",         "temperate"),
    ("Вестерлянди",             "temperate"),
    ("Річкові Землі",           "temperate"),
    ("Штормові Землі",          "temperate"),
    ("Простір",                 "temperate"),
    ("Королівство Корони",     "temperate"),
    ("Ступені",                 "temperate"),
    ("Спірні землі",            "temperate"),
    ("Ессос",                   "temperate"),
    ("Дорн",                    "hot_desert"),
    ("Дотракійське море",       "hot_desert"),
    ("Пентос",                  "mediterranean"),
    ("Браавос",                 "mediterranean"),
    ("Лис",                     "mediterranean"),
    ("Тірош",                   "mediterranean"),
    ("Мір",                     "mediterranean"),
    ("Волантіс",                "mediterranean"),
    ("Затока Работорговців",    "tropical"),
])
def test_region_climate_map_all_21_regions(region, expected_climate):
    """Every canonical region must map to its expected climate type."""
    assert region in REGION_CLIMATE_MAP, (
        f"Region '{region}' is missing from REGION_CLIMATE_MAP"
    )
    assert REGION_CLIMATE_MAP[region] == expected_climate, (
        f"REGION_CLIMATE_MAP['{region}'] == {REGION_CLIMATE_MAP[region]!r}, "
        f"expected {expected_climate!r}"
    )


def test_region_climate_map_has_exactly_21_entries():
    """REGION_CLIMATE_MAP must contain exactly 21 entries (one per canonical region)."""
    assert len(REGION_CLIMATE_MAP) == 21, (
        f"Expected 21 regions, got {len(REGION_CLIMATE_MAP)}: {list(REGION_CLIMATE_MAP.keys())}"
    )


# ---------------------------------------------------------------------------
# 2. EVENT_PLAUSIBILITY structure
# ---------------------------------------------------------------------------

def test_event_plausibility_day5_is_cold_only():
    """Day 5 blizzard is plausible ONLY in cold regions."""
    assert EVENT_PLAUSIBILITY[5] == ["cold"], (
        f"EVENT_PLAUSIBILITY[5] expected ['cold'], got {EVENT_PLAUSIBILITY[5]!r}"
    )


def test_event_plausibility_day14_is_universal():
    """Day 14 royal cortege is plausible everywhere ('*')."""
    assert EVENT_PLAUSIBILITY[14] == ["*"], (
        f"EVENT_PLAUSIBILITY[14] expected ['*'], got {EVENT_PLAUSIBILITY[14]!r}"
    )


def test_event_plausibility_day30_is_universal():
    """Day 30 war declaration is plausible everywhere ('*')."""
    assert EVENT_PLAUSIBILITY[30] == ["*"], (
        f"EVENT_PLAUSIBILITY[30] expected ['*'], got {EVENT_PLAUSIBILITY[30]!r}"
    )


# ---------------------------------------------------------------------------
# 3. Engine filter logic: blizzard (day 5)
# ---------------------------------------------------------------------------

def test_blizzard_skipped_in_pentos():
    """Day 5 blizzard must NOT inject in Пентос (mediterranean)."""
    result = _filter_event(5, "Пентос", _WORLD_EVENTS)
    assert result == "", (
        f"Blizzard must be skipped in mediterranean climate. Got: {result!r}"
    )


def test_blizzard_skipped_in_dorn():
    """Day 5 blizzard must NOT inject in Дорн (hot_desert)."""
    result = _filter_event(5, "Дорн", _WORLD_EVENTS)
    assert result == "", (
        f"Blizzard must be skipped in hot_desert climate. Got: {result!r}"
    )


def test_blizzard_skipped_in_temperate_kingdom():
    """Day 5 blizzard must NOT inject in Королівство Корони (temperate)."""
    result = _filter_event(5, "Королівство Корони", _WORLD_EVENTS)
    assert result == "", (
        f"Blizzard must be skipped in temperate climate. Got: {result!r}"
    )


def test_blizzard_injected_in_north():
    """Day 5 blizzard MUST inject in Північ (cold)."""
    result = _filter_event(5, "Північ", _WORLD_EVENTS)
    assert result != "", (
        "Blizzard must be injected in cold climate (Північ)"
    )
    assert "хуртовина" in result or "EVENT" in result


def test_blizzard_injected_beyond_wall():
    """Day 5 blizzard MUST inject За Стіною (cold)."""
    result = _filter_event(5, "За Стіною", _WORLD_EVENTS)
    assert result != "", (
        "Blizzard must be injected in cold climate (За Стіною)"
    )


def test_blizzard_injected_in_vale():
    """Day 5 blizzard MUST inject in Долина Аррен (cold)."""
    result = _filter_event(5, "Долина Аррен", _WORLD_EVENTS)
    assert result != ""


# ---------------------------------------------------------------------------
# 4. Unknown/missing region defaults to 'temperate' — blizzard skipped
# ---------------------------------------------------------------------------

def test_blizzard_skipped_for_unknown_region():
    """Unknown region defaults to 'temperate' -> blizzard (cold-only) is skipped."""
    result = _filter_event(5, "НевідомийРегіон", _WORLD_EVENTS)
    assert result == "", (
        "Unknown region should default to 'temperate'; blizzard (cold-only) must be skipped"
    )


def test_unknown_region_not_in_map():
    """Sanity: 'НевідомийРегіон' is indeed absent from REGION_CLIMATE_MAP."""
    assert "НевідомийРегіон" not in REGION_CLIMATE_MAP


# ---------------------------------------------------------------------------
# 5. Universal events ('*') inject everywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("region", [
    "Пентос", "Дорн", "Північ", "За Стіною", "Королівство Корони",
    "Затока Работорговців",
])
def test_cortege_day14_injects_everywhere(region):
    """Day 14 royal cortege ('*') must inject in any climate."""
    result = _filter_event(14, region, _WORLD_EVENTS)
    assert result != "", (
        f"Day 14 cortege must inject in any region, but skipped for {region!r}"
    )


@pytest.mark.parametrize("region", [
    "Пентос", "Дорн", "Північ",
])
def test_war_day30_injects_everywhere(region):
    """Day 30 war declaration must inject in any climate."""
    result = _filter_event(30, region, _WORLD_EVENTS)
    assert result != ""


# ---------------------------------------------------------------------------
# 6. Day with no event -> empty injection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("day", [1, 2, 3, 7, 10, 15, 20])
def test_no_event_day_returns_empty(day):
    """Days not in world_events dict must produce empty event_injection."""
    result = _filter_event(day, "Північ", _WORLD_EVENTS)
    assert result == "", (
        f"Day {day} has no world event — event_injection must be empty"
    )
