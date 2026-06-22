"""Tests for reputation→DC band-matrix (§5.2 hard rule).

Covers:
  A) Unit tests for core.dnd_core.reputation_adjusted_dc:
     - All 44 matrix cells (4 tiers × 11 bands)
     - Boundary scores (adjacent to each band edge)
     - Out-of-range clamp (>100, <-100)
     - rep_score=None → baseline (1..20 band)
     - Invalid severity → sentinel -1
     - All returned DCs ∈ LEGAL_DCS and ≥ 2

  B) Integration tests for core.dnd_engine.resolve_normal_action:
     - B1: HARD + rep=85 + Worker DC=15 → final DC=2 (matrix LOWERS DC)
     - B2: HARD + rep=-70 + Worker DC=12 → final DC=22 (matrix RAISES DC)
     - B3: HORRIBLE + rep=85 + Worker DC=20 → final DC=2 (matrix LOWERS DC)
     - B4: NORMAL, no target → baseline (difficulty == 10)
     - B5: target given but not in scene → baseline for severity
     - B6: ANTI-DOUBLE-COUNT: Worker DC=5, HARD, rep=-70 → matrix raises to 22
     - B7: FALLBACK: Worker fails → no exception, AUTO_SUCCESS / difficulty 2
     - B8: NPC-directed TRIVIAL forces roll (no is_free_action bypass), ability_used → CHA
     - B9: severity absent (empty string) → rep-DC branch no-ops, Worker difficulty used
     - B10: NORMAL, rep -50 (band -41--60) → difficulty == 17
     - B11: TRIVIAL, rep=0, no target → baseline DC=5
     - B12 NEW: DANGER FLOOR preserved: danger keyword + rep=85 + TRIVIAL → DC=15 (floor)
     - B13 NEW: normal action (no danger keyword) + NORMAL + rep=85 → DC=2 (no floor)
"""

import sys
import asyncio
import json
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Ensure database.sheets is stubbed so database.operations can be imported
# in isolation without a real Google Sheets connection.
# ---------------------------------------------------------------------------
if "database.sheets" not in sys.modules:
    _fake_sheets = MagicMock()
    _fake_sheets.db = MagicMock()
    sys.modules["database.sheets"] = _fake_sheets

from core.dnd_core import (
    reputation_adjusted_dc,
    LEGAL_DCS,
    SEVERITY_TIERS,
    REP_DC_MATRIX,
    REP_DC_FLOOR,
    CRIT_ONLY_DC,
    SEVERITY_BASE_DC,
)

# ===========================================================================
# Part A: Unit tests for reputation_adjusted_dc
# ===========================================================================

# ---------------------------------------------------------------------------
# A1. All 44 matrix cells — parametrized from the approved spec table
# ---------------------------------------------------------------------------

# Format: (band_label, rep_sample, severity, expected_dc)
# rep_sample is one value guaranteed to be inside that band (mid-point or low-bound).
_MATRIX_CASES = [
    # band 81..100
    ("81-100", 90,  "TRIVIAL",  2),
    ("81-100", 90,  "NORMAL",   2),
    ("81-100", 90,  "HARD",     2),
    ("81-100", 90,  "HORRIBLE", 2),
    # band 61..80
    ("61-80",  70,  "TRIVIAL",  2),
    ("61-80",  70,  "NORMAL",   2),
    ("61-80",  70,  "HARD",     2),
    ("61-80",  70,  "HORRIBLE", 5),
    # band 41..60
    ("41-60",  50,  "TRIVIAL",  2),
    ("41-60",  50,  "NORMAL",   2),
    ("41-60",  50,  "HARD",     5),
    ("41-60",  50,  "HORRIBLE", 10),
    # band 21..40
    ("21-40",  30,  "TRIVIAL",  2),
    ("21-40",  30,  "NORMAL",   5),
    ("21-40",  30,  "HARD",     12),
    ("21-40",  30,  "HORRIBLE", 15),
    # band 1..20
    ("1-20",   10,  "TRIVIAL",  5),
    ("1-20",   10,  "NORMAL",   10),
    ("1-20",   10,  "HARD",     15),
    ("1-20",   10,  "HORRIBLE", 20),
    # band 0 (explicit neutral)
    ("0",       0,  "TRIVIAL",  5),
    ("0",       0,  "NORMAL",   10),
    ("0",       0,  "HARD",     15),
    ("0",       0,  "HORRIBLE", 20),
    # band -1..-20
    ("-1--20", -10, "TRIVIAL",  10),
    ("-1--20", -10, "NORMAL",   12),
    ("-1--20", -10, "HARD",     17),
    ("-1--20", -10, "HORRIBLE", 22),
    # band -21..-40
    ("-21--40", -30, "TRIVIAL",  10),
    ("-21--40", -30, "NORMAL",   15),
    ("-21--40", -30, "HARD",     20),
    ("-21--40", -30, "HORRIBLE", 22),
    # band -41..-60
    ("-41--60", -50, "TRIVIAL",  15),
    ("-41--60", -50, "NORMAL",   17),
    ("-41--60", -50, "HARD",     20),
    ("-41--60", -50, "HORRIBLE", 22),
    # band -61..-80
    ("-61--80", -70, "TRIVIAL",  15),
    ("-61--80", -70, "NORMAL",   20),
    ("-61--80", -70, "HARD",     22),
    ("-61--80", -70, "HORRIBLE", 22),
    # band -81..-100
    ("-81--100", -90, "TRIVIAL",  20),
    ("-81--100", -90, "NORMAL",   20),
    ("-81--100", -90, "HARD",     22),
    ("-81--100", -90, "HORRIBLE", 22),
]


@pytest.mark.parametrize("band,rep,severity,expected_dc", _MATRIX_CASES,
                         ids=[f"rep={r}_sev={s}" for (_, r, s, _) in _MATRIX_CASES])
def test_matrix_cell(band, rep, severity, expected_dc):
    """Each matrix cell returns the expected DC from the spec table."""
    result = reputation_adjusted_dc(severity, rep)
    assert result == expected_dc, (
        f"Band {band}: reputation_adjusted_dc({severity!r}, {rep}) "
        f"expected {expected_dc}, got {result}"
    )


# ---------------------------------------------------------------------------
# A2. Boundary scores: verify the correct band is chosen at each edge
# ---------------------------------------------------------------------------

# Format: (rep_score, severity, expected_dc, description)
_BOUNDARY_CASES = [
    # 81 vs 80
    (81,  "HARD", 2,  "rep=81 → band 81-100"),
    (80,  "HARD", 2,  "rep=80 → band 61-80"),
    # 61 vs 60
    (61,  "HARD", 2,  "rep=61 → band 61-80"),
    (60,  "HARD", 5,  "rep=60 → band 41-60"),
    # 41 vs 40
    (41,  "HARD", 5,  "rep=41 → band 41-60"),
    (40,  "HARD", 12, "rep=40 → band 21-40"),
    # 21 vs 20
    (21,  "HARD", 12, "rep=21 → band 21-40"),
    (20,  "HARD", 15, "rep=20 → band 1-20"),
    # 1 vs 0
    (1,   "HARD", 15, "rep=1 → band 1-20"),
    (0,   "HARD", 15, "rep=0 → band 0"),
    # 0 vs -1
    (0,   "NORMAL", 10, "rep=0 → band 0"),
    (-1,  "NORMAL", 12, "rep=-1 → band -1--20"),
    # -20 vs -21
    (-20, "NORMAL", 12, "rep=-20 → band -1--20"),
    (-21, "NORMAL", 15, "rep=-21 → band -21--40"),
    # -40 vs -41
    (-40, "NORMAL", 15, "rep=-40 → band -21--40"),
    (-41, "NORMAL", 17, "rep=-41 → band -41--60"),
    # -60 vs -61
    (-60, "NORMAL", 17, "rep=-60 → band -41--60"),
    (-61, "NORMAL", 20, "rep=-61 → band -61--80"),
    # -80 vs -81
    (-80, "NORMAL", 20, "rep=-80 → band -61--80"),
    (-81, "NORMAL", 20, "rep=-81 → band -81--100"),
    # Extremes
    (100, "TRIVIAL", 2,  "rep=100 → band 81-100 (max)"),
    (-100, "HORRIBLE", 22, "rep=-100 → band -81--100 (min)"),
]


@pytest.mark.parametrize("rep,severity,expected_dc,desc", _BOUNDARY_CASES,
                         ids=[d for (_, _, _, d) in _BOUNDARY_CASES])
def test_boundary_score(rep, severity, expected_dc, desc):
    """Band edges must resolve to the correct DC."""
    result = reputation_adjusted_dc(severity, rep)
    assert result == expected_dc, (
        f"{desc}: reputation_adjusted_dc({severity!r}, {rep}) "
        f"expected {expected_dc}, got {result}"
    )


# ---------------------------------------------------------------------------
# A3. Out-of-range clamp: scores beyond ±100 treated as ±100
# ---------------------------------------------------------------------------

def test_out_of_range_high_101_clamped_to_100():
    """rep=101 → clamped to 100 → band 81-100, all severities same as 100."""
    for sev in SEVERITY_TIERS:
        expected = reputation_adjusted_dc(sev, 100)
        result = reputation_adjusted_dc(sev, 101)
        assert result == expected, (
            f"reputation_adjusted_dc({sev!r}, 101) must equal result for 100 "
            f"({expected}), got {result}"
        )


def test_out_of_range_low_minus101_clamped_to_minus100():
    """rep=-101 → clamped to -100 → band -81--100, all severities same as -100."""
    for sev in SEVERITY_TIERS:
        expected = reputation_adjusted_dc(sev, -100)
        result = reputation_adjusted_dc(sev, -101)
        assert result == expected, (
            f"reputation_adjusted_dc({sev!r}, -101) must equal result for -100 "
            f"({expected}), got {result}"
        )


def test_out_of_range_very_high_clamped():
    """rep=999 → same as 100."""
    for sev in SEVERITY_TIERS:
        assert reputation_adjusted_dc(sev, 999) == reputation_adjusted_dc(sev, 100)


def test_out_of_range_very_low_clamped():
    """rep=-999 → same as -100."""
    for sev in SEVERITY_TIERS:
        assert reputation_adjusted_dc(sev, -999) == reputation_adjusted_dc(sev, -100)


# ---------------------------------------------------------------------------
# A4. rep_score=None → baseline band (1..20): TRIVIAL=5, NORMAL=10, HARD=15, HORRIBLE=20
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("severity,expected_dc", [
    ("TRIVIAL",  5),
    ("NORMAL",   10),
    ("HARD",     15),
    ("HORRIBLE", 20),
])
def test_none_rep_score_uses_baseline_band(severity, expected_dc):
    """rep_score=None → treated as baseline (1..20 band)."""
    result = reputation_adjusted_dc(severity, None)
    assert result == expected_dc, (
        f"reputation_adjusted_dc({severity!r}, None) expected {expected_dc}, got {result}"
    )


# ---------------------------------------------------------------------------
# A5. Invalid severity → sentinel -1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_severity", [
    "",
    "foo",
    "HARD HORRIBLE",
    "123",
    "!!!",
])
def test_invalid_severity_returns_sentinel(bad_severity):
    """Genuinely invalid severity strings (after strip().upper()) must return -1 sentinel.

    Note: lowercase variants like 'trivial', 'normal', 'hard', 'horrible' are VALID
    because strip().upper() normalizes them — they are tested separately.
    """
    result = reputation_adjusted_dc(bad_severity, 0)
    assert result == -1, (
        f"reputation_adjusted_dc({bad_severity!r}, 0) must return -1 (invalid severity), "
        f"got {result}"
    )


def test_severity_lowercase_trivial_is_valid():
    """'trivial' lowercase → .strip().upper() = 'TRIVIAL' → valid, returns DC (not -1)."""
    result = reputation_adjusted_dc("trivial", 0)
    # Should equal TRIVIAL at rep=0 (band 0) → DC 5
    assert result == 5, (
        f"'trivial' (lowercase) must normalize to TRIVIAL and return 5, got {result}"
    )


def test_severity_lowercase_normal_is_valid():
    """'normal' lowercase → .strip().upper() = 'NORMAL' → valid, returns DC (not -1)."""
    result = reputation_adjusted_dc("normal", 0)
    assert result == 10, (
        f"'normal' (lowercase) must normalize to NORMAL and return 10, got {result}"
    )


def test_severity_with_spaces_stripped():
    """' HARD ' with surrounding spaces → strip() removes them → valid."""
    result = reputation_adjusted_dc("  HARD  ", 0)
    assert result == 15, (
        f"'  HARD  ' must strip to HARD and return 15, got {result}"
    )


def test_garbage_severity_returns_sentinel():
    """Pure garbage string returns -1."""
    for garbage in ["xyz", "IMPOSSIBLE", "DC15", "!!!"]:
        result = reputation_adjusted_dc(garbage, 0)
        assert result == -1, (
            f"reputation_adjusted_dc({garbage!r}, 0) must return -1, got {result}"
        )


# ---------------------------------------------------------------------------
# A6. INVARIANT: every returned DC (except -1) ∈ LEGAL_DCS and ≥ REP_DC_FLOOR
# ---------------------------------------------------------------------------

def test_all_valid_results_in_legal_dcs():
    """Every DC returned for valid inputs must be in LEGAL_DCS."""
    legal_set = set(LEGAL_DCS)
    test_scores = list(range(-100, 101, 10)) + [-100, -1, 0, 1, 100]
    for sev in SEVERITY_TIERS:
        for rep in test_scores:
            result = reputation_adjusted_dc(sev, rep)
            assert result in legal_set, (
                f"reputation_adjusted_dc({sev!r}, {rep}) = {result} not in LEGAL_DCS {legal_set}"
            )


def test_all_valid_results_at_least_dc_floor():
    """Every returned DC must be ≥ REP_DC_FLOOR (2)."""
    test_scores = list(range(-100, 101, 5))
    for sev in SEVERITY_TIERS:
        for rep in test_scores:
            result = reputation_adjusted_dc(sev, rep)
            assert result >= REP_DC_FLOOR, (
                f"reputation_adjusted_dc({sev!r}, {rep}) = {result} < REP_DC_FLOOR={REP_DC_FLOOR}"
            )


def test_all_valid_results_at_most_crit_only_dc():
    """Every returned DC must be ≤ CRIT_ONLY_DC (22)."""
    test_scores = list(range(-100, 101, 5))
    for sev in SEVERITY_TIERS:
        for rep in test_scores:
            result = reputation_adjusted_dc(sev, rep)
            assert result <= CRIT_ONLY_DC, (
                f"reputation_adjusted_dc({sev!r}, {rep}) = {result} > CRIT_ONLY_DC={CRIT_ONLY_DC}"
            )


# ---------------------------------------------------------------------------
# A7. SEVERITY_BASE_DC reference values match the 1..20 band in the matrix
# ---------------------------------------------------------------------------

def test_severity_base_dc_matches_baseline_band():
    """SEVERITY_BASE_DC values must match the matrix output for rep in 1..20."""
    for sev, expected_base in SEVERITY_BASE_DC.items():
        result = reputation_adjusted_dc(sev, 10)  # 10 ∈ (1, 20) band
        assert result == expected_base, (
            f"SEVERITY_BASE_DC[{sev!r}]={expected_base} must match "
            f"reputation_adjusted_dc({sev!r}, 10)={result}"
        )


# ===========================================================================
# Part B: Integration tests for resolve_normal_action with rep-DC matrix
# ===========================================================================

def _minimal_profile(**overrides) -> dict:
    base = {
        "Ім'я": "TestHero",
        "Здоров'я": 100,
        "Енергія": 800,
        "Особисте Золото": 200,
        "Поточне місцезнаходження": "Вінтерфелл",
        "Годинники": {"Scene_Tension": "0/4"},
        "level": 1,
        "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "skill_profs": [],
        "skill_expertise": [],
        "saves_proficient": [],
        "conditions": [],
        "xp": 0,
    }
    base.update(overrides)
    return base


def _minimal_worker_json(
    severity: str = "NORMAL",
    target_npc: str = "",
    difficulty: int = 10,
    skill_used: str = "None",
    ability_used: str = "None",
    **extra,
) -> dict:
    """Canonical Worker JSON with action_severity and reputation_target_npc."""
    base = {
        "ability_used": ability_used,
        "skill_used": skill_used,
        "difficulty": difficulty,
        "action_severity": severity,
        "advantage_reason": "",
        "disadvantage_reason": "",
        "combat_imminent": False,
        "verdict_text": "Test verdict.",
        "xp_award": 0,
        "reputation_delta_success": 0,
        "reputation_delta_failure": 0,
        "reputation_target_npc": target_npc,
        "updates": {
            "minutes_passed": 5,
            "location_impact": "none",
            "scene_impact": "none",
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "gold_impact": "none",
            "inventory_new": [],
            "inventory_lost": [],
            "clocks_impact": {},
            "condition_apply": [],
            "condition_remove": [],
        },
    }
    base.update(extra)
    return base


def _patches_for_resolve(worker_data: dict):
    """Return context managers that isolate resolve_normal_action from all I/O.

    GODMODE_USERS is imported locally inside resolve_normal_action via
    `from config import GODMODE_USERS`, so we patch it at the config module.
    user_id is NOT passed in _run_resolve (defaults to None) — this makes
    `if user_id and user_id in GODMODE_USERS` evaluate to False, so GODMODE
    is naturally disabled even without the patch.  The patch is kept as
    defense-in-depth in case the import path changes.
    """
    return [
        patch("core.dnd_engine.model_worker.generate_content",
              return_value=MagicMock(text=json.dumps(worker_data))),
        patch("core.dnd_engine.clean_and_parse_json", return_value=worker_data),
        patch("core.dnd_engine.build_normal_resolve_prompt", return_value="MOCK_PROMPT"),
        # GODMODE: patch at the source module so the local `from config import` picks it up
        patch("config.GODMODE_USERS", new=set()),
    ]


def _run_resolve(
    worker_data: dict,
    npc_reputation_context: dict = None,
    roll: int = 10,
    user_input: str = "переконую X показати лист",
) -> tuple[str, dict]:
    """Run resolve_normal_action with mocked LLM and fixed d20 roll.

    Default user_input is a social action with NO danger keywords — safe for
    B1/B3/B13 which must NOT trigger the danger floor.
    """
    profile = _minimal_profile()

    async def _coro():
        from core.dnd_engine import resolve_normal_action
        return await resolve_normal_action(
            user_input=user_input,
            profile=profile,
            npc_reputation_context=npc_reputation_context or {},
            npc_names=list((npc_reputation_context or {}).keys()),
        )

    with ExitStack() as stack:
        for p in _patches_for_resolve(worker_data):
            stack.enter_context(p)
        with patch("core.dnd_core.random.randint", return_value=roll):
            return asyncio.run(_coro())


# ---------------------------------------------------------------------------
# B1. РЕАЛЬНА ПЕРЕВІРКА ЗНИЖЕННЯ DC:
#     HARD + rep=85 + Worker DC=15, дія БЕЗ danger keyword.
#     Очікування: final DC=2 (матриця знизила з 15 до 2).
#
#     Логіка фіксу:
#       clamped_dc = clamp_dc(15) = 15
#       _danger_floor_dc = LEGAL_DCS[0] = 2  (немає danger keyword)
#       adj_dc = reputation_adjusted_dc("HARD", 85) = 2
#       difficulty = max(2, 2) = 2  → зниження відбулось!
# ---------------------------------------------------------------------------

def test_integration_b1_hard_rep85_dc_lowered_from_worker_15_to_2():
    """B1: HARD + rep=85 + Worker DC=15, no danger keyword → final DC=2.

    Це головна перевірка зниження DC після фіксу _danger_floor_dc.
    До фіксу: _danger_floor_dc = clamped_dc = 15 → max(2, 15) = 15 (баг).
    Після фіксу: _danger_floor_dc = LEGAL_DCS[0] = 2 → max(2, 2) = 2 (правильно).
    """
    worker_data = _minimal_worker_json(
        severity="HARD",
        target_npc="Еддард Старк",
        difficulty=15,    # Worker DC=15, реальне значення для HARD severity
        skill_used="Persuasion",
        ability_used="CHA",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},
        roll=10,
        user_input="переконую Еддарда Старка показати лист",  # соціальна дія, БЕЗ danger
    )
    assert updates["difficulty"] == 2, (
        f"B1: HARD + rep=85 + Worker DC=15 (no danger keyword) → "
        f"matrix LOWERED DC to 2. Got {updates['difficulty']}. "
        f"If this is 15, the bug _danger_floor_dc = clamped_dc is NOT fixed."
    )


# ---------------------------------------------------------------------------
# B2. severity HARD + target NPC rep -70 → difficulty == 22 (crit-only)
# ---------------------------------------------------------------------------

def test_integration_hard_rep_minus70_difficulty_22():
    """HARD severity + rep -70 (band -61--80) → matrix says DC 22, overrides Worker DC 12."""
    worker_data = _minimal_worker_json(
        severity="HARD",
        target_npc="Рамсі Болтон",
        difficulty=12,
        skill_used="Athletics",
        ability_used="STR",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Рамсі Болтон": -70},
        roll=10,
    )
    # Matrix: HARD + rep=-70 → 22; max(22, 2) = 22
    assert updates["difficulty"] == 22, (
        f"HARD + rep=-70 → matrix DC=22, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B3. РЕАЛЬНА ПЕРЕВІРКА ЗНИЖЕННЯ DC:
#     HORRIBLE + rep=85 + Worker DC=20, дія БЕЗ danger keyword.
#     Очікування: final DC=2 (матриця знизила з 20 до 2).
#
#     Логіка фіксу:
#       clamped_dc = clamp_dc(20) = 20
#       _danger_floor_dc = LEGAL_DCS[0] = 2  (немає danger keyword)
#       adj_dc = reputation_adjusted_dc("HORRIBLE", 85) = 2
#       difficulty = max(2, 2) = 2  → зниження відбулось!
# ---------------------------------------------------------------------------

def test_integration_b3_horrible_rep85_dc_lowered_from_worker_20_to_2():
    """B3: HORRIBLE + rep=85 + Worker DC=20, no danger keyword → final DC=2.

    До фіксу: _danger_floor_dc = clamped_dc = 20 → max(2, 20) = 20 (баг).
    Після фіксу: _danger_floor_dc = 2 → max(2, 2) = 2 (правильно).
    """
    worker_data = _minimal_worker_json(
        severity="HORRIBLE",
        target_npc="Еддард Старк",
        difficulty=20,    # Worker DC=20, реальне значення для HORRIBLE
        skill_used="Persuasion",
        ability_used="CHA",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},
        roll=10,
        user_input="прошу Еддарда Старка про надзвичайну послугу",  # соціальна дія, БЕЗ danger
    )
    assert updates["difficulty"] == 2, (
        f"B3: HORRIBLE + rep=85 + Worker DC=20 (no danger keyword) → "
        f"matrix LOWERED DC to 2. Got {updates['difficulty']}. "
        f"If this is 20, the bug _danger_floor_dc = clamped_dc is NOT fixed."
    )


# ---------------------------------------------------------------------------
# B4. severity NORMAL, no target NPC → baseline (difficulty == 10)
# ---------------------------------------------------------------------------

def test_integration_normal_no_target_baseline():
    """NORMAL severity, reputation_target_npc='' → baseline (1..20 band) → DC 10."""
    worker_data = _minimal_worker_json(
        severity="NORMAL",
        target_npc="",     # no target
        difficulty=10,
        skill_used="Athletics",
        ability_used="STR",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={},
        roll=10,
    )
    assert updates["difficulty"] == 10, (
        f"NORMAL + no target → baseline DC=10, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B5. Target named but NOT in npc_reputation_context (not in scene) → baseline for severity
# ---------------------------------------------------------------------------

def test_integration_target_not_in_scene_baseline():
    """target_npc named but absent from npc_reputation_context → baseline DC for HARD = 15."""
    worker_data = _minimal_worker_json(
        severity="HARD",
        target_npc="НеіснуючийNPC",
        difficulty=12,
        skill_used="Athletics",
        ability_used="STR",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},  # different NPC in scene
        roll=10,
    )
    # NPC not in scene → rep_score=None → baseline band (1..20) → HARD=15
    assert updates["difficulty"] == 15, (
        f"Target not in scene → baseline HARD=15, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B6. ANTI-DOUBLE-COUNT: Matrix raises DC regardless of Worker difficulty.
#     Worker HARD + difficulty=5, rep=-70 → matrix=22 (hostile band) wins.
#
#     Перевірка що матриця ПІДВИЩУЄ DC при ворожому NPC.
#     Це коректна поведінка: engine ігнорує Worker DC і використовує матрицю.
# ---------------------------------------------------------------------------

def test_integration_anti_double_count_matrix_raises_over_worker():
    """ANTI-DOUBLE-COUNT: Matrix raises DC from 5 to 22 when NPC is hostile (rep -70, HARD)."""
    worker_data = _minimal_worker_json(
        severity="HARD",
        target_npc="Рамсі Болтон",
        difficulty=5,    # Worker says easy, but hostile NPC should raise it
        skill_used="Athletics",
        ability_used="STR",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Рамсі Болтон": -70},
        roll=10,
    )
    # Matrix: HARD + rep=-70 → 22; max(22, 2) = 22
    assert updates["difficulty"] == 22, (
        f"Matrix must raise Worker DC=5 to 22 (HARD + rep=-70). "
        f"Got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B7. FALLBACK: Worker call fails (returns None/unparseable) → no exception,
#     AUTO_SUCCESS with difficulty == AUTO_SUCCESS_MAX_DC (2).
# ---------------------------------------------------------------------------

def test_integration_llm_failure_no_exception_auto_success():
    """If LLM returns None/unparseable, resolve_normal_action must not raise and return AUTO_SUCCESS."""
    profile = _minimal_profile()

    async def _coro():
        from core.dnd_engine import resolve_normal_action
        return await resolve_normal_action(
            user_input="Test fallback",
            profile=profile,
            npc_reputation_context={},
        )

    with ExitStack() as stack:
        # Make LLM always return None response text
        stack.enter_context(patch(
            "core.dnd_engine.model_worker.generate_content",
            return_value=MagicMock(text=None),
        ))
        stack.enter_context(patch(
            "core.dnd_engine.clean_and_parse_json",
            return_value=None,
        ))
        stack.enter_context(patch(
            "core.dnd_engine.build_normal_resolve_prompt",
            return_value="MOCK_PROMPT",
        ))

        verdict, updates = asyncio.run(_coro())

    # Must not raise — just return AUTO_SUCCESS
    assert "AUTO_SUCCESS" in verdict or updates["difficulty"] == 2, (
        f"LLM fallback must produce AUTO_SUCCESS verdict or difficulty=2. "
        f"verdict={verdict!r}, difficulty={updates.get('difficulty')}"
    )
    assert updates["difficulty"] == 2, (
        f"LLM fallback difficulty must be AUTO_SUCCESS_MAX_DC=2, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B8. NPC-directed TRIVIAL forces roll (bypasses is_free_action), ability_used → CHA
# ---------------------------------------------------------------------------

def test_integration_trivial_npc_directed_forces_roll_cha():
    """TRIVIAL + rep 85 → DC 2 but rep_directed=True → no free-action skip → CHA roll."""
    worker_data = _minimal_worker_json(
        severity="TRIVIAL",
        target_npc="Еддард Старк",
        difficulty=2,
        skill_used="None",
        ability_used="None",  # Worker did not specify; engine must default to CHA
    )
    verdict, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},
        roll=10,
    )
    # Must NOT be a free-action auto-skip verdict
    assert "AUTO_SUCCESS (Free action" not in verdict, (
        f"NPC-directed TRIVIAL must NOT be routed to free-action skip. "
        f"Verdict: {verdict!r}"
    )
    # ability_used must have been set to CHA by the engine
    assert updates["ability_used"] == "CHA", (
        f"NPC-directed action with no skill/ability must default to CHA. "
        f"Got ability_used={updates['ability_used']!r}"
    )
    # Verify an actual roll was made (natural_roll != 0)
    assert updates["natural_roll"] != 0, (
        f"NPC-directed TRIVIAL must perform an actual d20 roll (natural_roll != 0). "
        f"Got {updates['natural_roll']}"
    )
    # DC must be 2 (matrix: TRIVIAL + rep=85 → 2)
    assert updates["difficulty"] == 2, (
        f"TRIVIAL + rep=85 → matrix DC=2, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B9. severity absent (empty string) → rep-DC branch no-ops, Worker difficulty used
# ---------------------------------------------------------------------------

def test_integration_no_severity_worker_difficulty_unchanged():
    """If action_severity='' (absent/invalid), engine must not apply rep-DC matrix."""
    worker_data = _minimal_worker_json(
        severity="",       # invalid → sentinel -1 → branch no-op
        target_npc="Еддард Старк",
        difficulty=12,     # Worker's value must survive unchanged
        skill_used="Athletics",
        ability_used="STR",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},
        roll=10,
    )
    # clamp_dc(12) = 12 (already legal); no rep-DC override → stays 12
    assert updates["difficulty"] == 12, (
        f"Empty severity → rep-DC branch skipped, Worker DC=12 must survive. "
        f"Got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B10. NORMAL, rep -50 (band -41--60) → difficulty == 17
# ---------------------------------------------------------------------------

def test_integration_normal_rep_minus50_difficulty_17():
    """NORMAL severity + rep -50 (band -41--60) → matrix says DC 17."""
    worker_data = _minimal_worker_json(
        severity="NORMAL",
        target_npc="Серсея Ланністер",
        difficulty=10,
        skill_used="Persuasion",
        ability_used="CHA",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Серсея Ланністер": -50},
        roll=10,
    )
    assert updates["difficulty"] == 17, (
        f"NORMAL + rep=-50 → matrix DC=17, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B11. TRIVIAL, rep 0 (band 0) → difficulty == 5 — but DC 5 triggers roll (not free)
#      even without rep_directed (free-action bypass requires DC ≤ 2 AND no skill)
# ---------------------------------------------------------------------------

def test_integration_trivial_rep0_no_target_difficulty_5():
    """TRIVIAL + rep=0 (no target) → baseline DC=5. Not a free-action skip (DC > 2)."""
    worker_data = _minimal_worker_json(
        severity="TRIVIAL",
        target_npc="",
        difficulty=2,       # Worker said 2, but matrix overrides to 5
        skill_used="None",
        ability_used="None",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={},
        roll=10,
    )
    # Matrix: TRIVIAL + None (baseline 1..20) → 5
    assert updates["difficulty"] == 5, (
        f"TRIVIAL + no target → baseline DC=5, got {updates['difficulty']}"
    )


# ---------------------------------------------------------------------------
# B12 NEW: DANGER FLOOR ЗБЕРІГАЄТЬСЯ після фіксу.
#
#     Дія з danger keyword + дружній NPC (rep=85) + severity TRIVIAL.
#     Матриця дала б DC=2 (TRIVIAL + rep=85), але danger floor = 15.
#     Очікування: final DC=15 (danger floor не занижено репутацією).
#
#     Danger keyword з реального списку dnd_engine._DANGER_KEYWORDS:
#     "стриб" → "стрибаю зі стіни" — тригерить і "стриб" і "стін"
#
#     Логіка фіксу:
#       clamped_dc = clamp_dc(2) = 2   (TRIVIAL)
#       danger keyword знайдено → clamped_dc = clamp_dc(15) = 15  (danger floor)
#       _danger_floor_dc = clamp_dc(15) = 15
#       adj_dc = reputation_adjusted_dc("TRIVIAL", 85) = 2
#       difficulty = max(2, 15) = 15  → danger floor збережено!
# ---------------------------------------------------------------------------

def test_integration_b12_danger_floor_preserved_for_danger_action():
    """B12 NEW: Danger keyword + rep=85 + TRIVIAL → DC=15 (danger floor not undercut by rep).

    Використовуємо реальне слово зі списку _DANGER_KEYWORDS: "стриб".
    Матриця б дала DC=2 (TRIVIAL + rep=85), але danger floor=15 зберігається.
    Після фіксу це поведінка НЕ змінилась — danger floor залишається.
    """
    worker_data = _minimal_worker_json(
        severity="TRIVIAL",
        target_npc="Еддард Старк",
        difficulty=2,       # Worker DC=2, але danger floor піднімає до 15
        skill_used="Athletics",
        ability_used="STR",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},
        roll=10,
        # "стрибаю" містить "стриб" зі списку _DANGER_KEYWORDS — тригерить danger floor
        user_input="стрибаю вниз через балкон",
    )
    assert updates["difficulty"] == 15, (
        f"B12: Danger action (keyword 'стриб') + rep=85 + TRIVIAL → "
        f"danger floor DC=15 must be preserved. Got {updates['difficulty']}. "
        f"If this is 2, the danger floor was accidentally removed by the fix."
    )


# ---------------------------------------------------------------------------
# B13 NEW: Звичайна дія БЕЗ danger keyword, severity NORMAL, rep=85 → DC=2.
#
#     Підтверджує що звичайні (не-небезпечні) дії не мають floor=15.
#     _danger_floor_dc = LEGAL_DCS[0] = 2 → max(2, 2) = 2.
#
#     Це підтвердження кореговності фіксу для не-danger дій.
# ---------------------------------------------------------------------------

def test_integration_b13_normal_action_no_danger_rep85_dc2():
    """B13 NEW: Normal (no danger keyword) + NORMAL + rep=85 → DC=2 (no floor=15).

    Після фіксу: _danger_floor_dc = 2 для звичайних дій.
    adj_dc = reputation_adjusted_dc("NORMAL", 85) = 2.
    difficulty = max(2, 2) = 2.
    """
    worker_data = _minimal_worker_json(
        severity="NORMAL",
        target_npc="Еддард Старк",
        difficulty=10,      # Worker DC=10 для NORMAL
        skill_used="Persuasion",
        ability_used="CHA",
    )
    _, updates = _run_resolve(
        worker_data,
        npc_reputation_context={"Еддард Старк": 85},
        roll=10,
        # Соціальна дія — жодного слова з _DANGER_KEYWORDS
        user_input="прошу аудієнції у лорда Старка",
    )
    assert updates["difficulty"] == 2, (
        f"B13: Normal social action (no danger keyword) + NORMAL + rep=85 → "
        f"matrix DC=2 (no danger floor). Got {updates['difficulty']}. "
        f"If this is 10 or higher, the reputation matrix is not lowering DC "
        f"for friendly NPCs on non-dangerous actions."
    )
