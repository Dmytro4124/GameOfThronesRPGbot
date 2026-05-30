# core/character_creation.py
"""Pure-math helpers for D&D 5e standard point-buy character creation.

No I/O, no async, no Sheets, no Gemini calls.  All public symbols are
intentionally named to match what the parallel bot/handlers.py worker
imports directly — do NOT rename them without coordinating with python-dev.

§5.2 invariants honoured:
- Base ability scores constrained to POINT_BUY_MIN_BASE..POINT_BUY_MAX_BASE (8-15).
- After heritage bonuses are applied by the caller, scores may reach 20 (D&D cap).
- No new numeric scales are introduced here.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# D&D 5e SRD point-buy cost table (PHB p.13).
# Key = final score (8-15), value = points spent to reach that score.
POINT_BUY_COSTS: dict[int, int] = {
    8: 0,
    9: 1,
    10: 2,
    11: 3,
    12: 4,
    13: 5,
    14: 7,
    15: 9,
}

# Total budget available to the player.
POINT_BUY_BUDGET: int = 27

# Hard floor and ceiling for base scores (before heritage bonuses).
POINT_BUY_MIN_BASE: int = 8
POINT_BUY_MAX_BASE: int = 15

# Canonical order of the six D&D ability keys.
ABILITY_KEYS: tuple[str, ...] = ("STR", "DEX", "CON", "INT", "WIS", "CHA")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def point_buy_initial() -> dict[str, int]:
    """Return a fresh stat dict — all six abilities at POINT_BUY_MIN_BASE (8).

    This is the starting state shown to the player before any allocation.
    All 27 points remain unspent.
    """
    return {ab: POINT_BUY_MIN_BASE for ab in ABILITY_KEYS}


def point_buy_cost(scores: dict[str, int]) -> int:
    """Return total points spent for the given score dict.

    Validation (raises ValueError on first violation):
    - scores must contain exactly the 6 ABILITY_KEYS.
    - Every value must be an int.
    - Every value must be in POINT_BUY_COSTS (i.e. 8-15).

    Does NOT silently clamp — out-of-range scores are a programmer error.
    """
    _validate_scores(scores)
    return sum(POINT_BUY_COSTS[scores[ab]] for ab in ABILITY_KEYS)


def point_buy_remaining(scores: dict[str, int]) -> int:
    """Return POINT_BUY_BUDGET - point_buy_cost(scores).

    May return a negative integer if the caller somehow constructed an
    overspent dict (e.g. by bypassing can_increment).  This function does
    NOT raise on overspend — it just reports the signed delta so the UI can
    display a meaningful warning.  Validation still raises on non-int or
    out-of-range scores (delegated to point_buy_cost).
    """
    return POINT_BUY_BUDGET - point_buy_cost(scores)


def point_buy_can_increment(scores: dict[str, int], ability: str) -> bool:
    """Return True iff incrementing scores[ability] by 1 is legal.

    Both conditions must hold:
    1. scores[ability] < POINT_BUY_MAX_BASE (15)  — not already at ceiling.
    2. The *marginal* cost of the increment fits within the remaining budget.
       Marginal cost = POINT_BUY_COSTS[current + 1] - POINT_BUY_COSTS[current].
       This correctly handles the 13→14 (cost 2) and 14→15 (cost 2) steps.

    Raises ValueError if ability is not in ABILITY_KEYS or if the score for
    any ability is invalid (delegated through point_buy_remaining).
    """
    if ability not in ABILITY_KEYS:
        raise ValueError(
            f"Unknown ability key '{ability}'. Must be one of {ABILITY_KEYS}."
        )
    current = scores.get(ability)
    if not isinstance(current, int):
        raise ValueError(
            f"scores['{ability}'] must be int, got {type(current).__name__!r}."
        )
    if current >= POINT_BUY_MAX_BASE:
        return False
    marginal_cost = POINT_BUY_COSTS[current + 1] - POINT_BUY_COSTS[current]
    return point_buy_remaining(scores) >= marginal_cost


def point_buy_can_decrement(scores: dict[str, int], ability: str) -> bool:
    """Return True iff decrementing scores[ability] by 1 is legal.

    Only condition: scores[ability] > POINT_BUY_MIN_BASE (8).
    Decrementing never violates the budget (it only refunds points), so no
    budget check is needed.

    Raises ValueError if ability is not in ABILITY_KEYS.
    """
    if ability not in ABILITY_KEYS:
        raise ValueError(
            f"Unknown ability key '{ability}'. Must be one of {ABILITY_KEYS}."
        )
    current = scores.get(ability)
    if not isinstance(current, int):
        raise ValueError(
            f"scores['{ability}'] must be int, got {type(current).__name__!r}."
        )
    return current > POINT_BUY_MIN_BASE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_scores(scores: dict[str, int]) -> None:
    """Raise ValueError if scores is not a fully valid point-buy dict.

    Checks (in order):
    1. scores contains exactly all 6 ABILITY_KEYS (no extra, no missing).
    2. Every value is an int.
    3. Every value is within [POINT_BUY_MIN_BASE, POINT_BUY_MAX_BASE] (8-15).
    """
    if not isinstance(scores, dict):
        raise ValueError(
            f"scores must be a dict, got {type(scores).__name__!r}."
        )

    missing = [k for k in ABILITY_KEYS if k not in scores]
    if missing:
        raise ValueError(
            f"scores is missing required ability keys: {missing}."
        )

    extra = [k for k in scores if k not in ABILITY_KEYS]
    if extra:
        raise ValueError(
            f"scores contains unexpected keys: {extra}. "
            f"Only {list(ABILITY_KEYS)} are allowed."
        )

    for ab in ABILITY_KEYS:
        val = scores[ab]
        if not isinstance(val, int):
            raise ValueError(
                f"scores['{ab}'] must be int, got {type(val).__name__!r} "
                f"(value={val!r})."
            )
        if val not in POINT_BUY_COSTS:
            raise ValueError(
                f"scores['{ab}']={val} is outside the legal point-buy range "
                f"[{POINT_BUY_MIN_BASE}, {POINT_BUY_MAX_BASE}]. "
                f"Valid values: {sorted(POINT_BUY_COSTS)}."
            )
