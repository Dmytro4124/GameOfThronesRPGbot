"""Asymmetric magnitude-gated reputation progression.

Reputation_Score range: -100..+100 (maps to 15-step Relation_Player scale).
Worker supplies raw delta -7..+7 (action significance, mirrors relation steps).
Climb is HARD (gap-based diminishing → plateau at magnitude ceiling);
fall is MAGNITUDE-GATED: minor slights barely register (mag 1-2: 1-2 pts),
severe betrayals are nuclear (mag 6-7: 50-100 pts).
Epic actions (|raw|>=6) jump to extreme on the positive side.

Integration contract (for data-rag-agent Round 2):
    from core.reputation import apply_reputation_step

    Signature: apply_reputation_step(current_score: int, raw_delta: int) -> int

    update_npc_reputation MUST call:
        new_val = apply_reputation_step(old_val, delta)
    instead of:
        max(-100, min(100, old_val + delta))

    raw_delta comes from updates["reputation_delta"] as set by engine.
    current_score is the existing NPC reputation_score from the DB.

Pure sync function — no state, no I/O, no async (§5.1 / §5.4 invariants satisfied).
"""
import math

# Positive magnitude → soft-cap score this magnitude can reach by repetition.
# Dicts keyed by magnitude 1..7. Values are max Reputation_Score achievable
# by repeatedly applying actions of this magnitude.
POSITIVE_CAP: dict[int, int] = {
    1: 25,   # small favor / polite gesture
    2: 45,   # helpful action
    3: 65,   # significant aid
    4: 78,   # major deed
    5: 88,   # heroic action
    6: 95,   # exceptional deed (just below epic)
    7: 100,  # absolute devotion (max possible)
}

# Negative magnitude → fixed score drop per single action.
# Scale is LENIENT at low magnitudes (minor slights barely register) and
# SEVERE at high magnitudes (great betrayals are catastrophic).
# magnitude-7 drop of 100 still guarantees clamp to -100 from any non-negative start.
NEGATIVE_DROP: dict[int, int] = {
    1: 1,    # petty slight / trivial rudeness
    2: 2,    # minor offence / small broken promise
    3: 5,    # noticeable betrayal / moderate harm
    4: 10,   # serious harm or clear betrayal
    5: 25,   # grave betrayal / severe harm
    6: 50,   # devastating act / killing close one
    7: 100,  # murder / unforgivable act → Blood Enemy from any non-negative score
}

# Fraction of gap-to-cap closed per positive action (gap-based diminishing returns).
POS_CLIMB_RATE: float = 0.4

# |raw| >= EPIC_THRESHOLD → instant jump to magnitude ceiling (positive only).
# Negative epics still use NEGATIVE_DROP (fast fall is already drastic enough).
EPIC_THRESHOLD: int = 6

SCORE_MIN: int = -100
SCORE_MAX: int = 100


def apply_reputation_step(current_score: int, raw_delta: int) -> int:
    """Compute new Reputation_Score from current score + raw action significance.

    Args:
        current_score: Current NPC reputation score, expected in [-100, 100].
                       Values outside this range are accepted and result is
                       always clamped back into [-100, 100].
        raw_delta:     Action significance from Worker output, -7..+7.
                       Values outside [-7, 7] are clamped to that range first.
                       0 → no change.

    Returns:
        New Reputation_Score clamped to [SCORE_MIN, SCORE_MAX] = [-100, 100].

    Positive delta behaviour (hard to climb):
        - Finds soft-cap = POSITIVE_CAP[magnitude].
        - If current_score >= cap → returns current_score unchanged (farming guard).
        - If magnitude >= EPIC_THRESHOLD (6 or 7) → jumps straight to cap.
        - Otherwise → closes gap by ceil(gap * POS_CLIMB_RATE), at least +1.
        - Result is clamped to SCORE_MAX (100).

    Negative delta behaviour (easy to fall):
        - Subtracts NEGATIVE_DROP[magnitude] from current_score unconditionally.
        - Result is clamped to SCORE_MIN (-100).

    Asymmetry example: +1 from 0 gives +10; -1 from 0 gives -1.
    Epic example: +7 from 0 jumps straight to 100; -7 from 50 drops to -50.
    Scale note: minor slights barely register (mag 1-2: 1-2 pts); severe betrayals
    are nuclear (mag 6-7: 50-100 pts).

    Boundary conditions:
        - raw_delta == 0 → no change, returns current_score unchanged.
        - raw_delta > 7 or raw_delta < -7 → clamped to ±7 before processing.
        - current_score already at SCORE_MAX with positive delta → unchanged.
        - current_score already at SCORE_MIN with negative delta → unchanged (clamp).
    """
    # Clamp raw input to legal range
    raw = max(-7, min(7, int(raw_delta)))

    if raw == 0:
        return int(current_score)

    mag = abs(raw)

    if raw > 0:
        cap = POSITIVE_CAP[mag]
        current = int(current_score)

        # Farming guard: already at or above this magnitude's ceiling → no gain
        if current >= cap:
            return current

        if mag >= EPIC_THRESHOLD:
            # Epic deed → instant jump to ceiling
            new = cap
        else:
            # Gap-based diminishing: close POS_CLIMB_RATE fraction of remaining gap
            gap = cap - current
            step = max(1, math.ceil(gap * POS_CLIMB_RATE))
            new = current + step
            new = min(new, cap)  # never overshoot the cap

        return min(SCORE_MAX, new)

    else:  # raw < 0 — easy fall
        drop = NEGATIVE_DROP[mag]
        new = int(current_score) - drop
        return max(SCORE_MIN, new)
