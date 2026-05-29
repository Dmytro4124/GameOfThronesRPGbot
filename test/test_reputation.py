"""Tests for core/reputation.py — asymmetric magnitude-gated reputation math.

All 8 spec examples are tested as exact-value assertions. Additional suites cover:
- Farming guard (plateau at magnitude ceiling).
- Asymmetry invariant (+1 step < |-1| drop from neutral).
- Epic jump (+6/+7 → cap; -6/-7 → deep negative).
- Clamp boundaries (score stays in [-100, 100]).
- Raw delta clamping (values outside [-7, 7] treated as ±7).
"""
import pytest
from core.reputation import (
    apply_reputation_step,
    POSITIVE_CAP,
    NEGATIVE_DROP,
    POS_CLIMB_RATE,
    EPIC_THRESHOLD,
    SCORE_MIN,
    SCORE_MAX,
)


# ---------------------------------------------------------------------------
# Spec examples (exact values from task specification)
# ---------------------------------------------------------------------------

class TestSpecExamples:
    """All 8 examples stated in the task specification must hold exactly."""

    def test_example_1_small_gesture_from_zero(self):
        """apply(0, +1) → 0 + ceil(25 * 0.4) = 10."""
        # gap = 25 - 0 = 25; step = ceil(25 * 0.4) = ceil(10.0) = 10
        assert apply_reputation_step(0, 1) == 10

    def test_example_2_farming_guard_already_high(self):
        """apply(60, +1) → 60 unchanged: current 60 >= cap(1)=25 → farming guard."""
        assert apply_reputation_step(60, 1) == 60

    def test_example_3a_first_repeat_magnitude_3(self):
        """apply(0, +3) → ceil(65 * 0.4) = 26."""
        # gap = 65 - 0 = 65; step = ceil(65 * 0.4) = ceil(26.0) = 26
        assert apply_reputation_step(0, 3) == 26

    def test_example_3b_second_repeat_magnitude_3_asymptote(self):
        """apply(26, +3) → 26 + ceil(39 * 0.4) = 26 + 16 = 42."""
        # gap = 65 - 26 = 39; step = ceil(39 * 0.4) = ceil(15.6) = 16
        assert apply_reputation_step(26, 3) == 42

    def test_example_4_epic_positive_jump(self):
        """apply(0, +7) → epic jump → 100 (saving a life → absolute trust)."""
        assert apply_reputation_step(0, 7) == 100

    def test_example_5_negative_from_high(self):
        """apply(60, -1) → 60 - 1 = 59 (petty slight barely registers at new lenient table)."""
        # NEGATIVE_DROP[1] retuned from 15 → 1. Minor slights now barely dent high reputation.
        assert apply_reputation_step(60, -1) == 59

    def test_example_6_negative_from_neutral(self):
        """apply(0, -1) → 0 - 1 = -1 (new table: mag-1 drop is 1 pt, not 15).

        INTENT_CHANGED: small negatives no longer fall fast — confirmed approved by user.
        Under old table NEGATIVE_DROP[1]=15 gave -15. New table gives -1 (linear equivalent),
        making small slights nearly inconsequential. This is the approved gameplay retuning.
        """
        assert apply_reputation_step(0, -1) == -1

    def test_example_7_fatal_negative_clamp(self):
        """apply(85, -7) → 85 - 100 = -15 (no clamp needed).

        INTENT_CHANGED: mag-7 drop retuned from 200 → 100. From score=85, -7 now gives -15,
        not -100. Only reaches -100 from score=0 (0-100=-100 exact clamp). Confirmed approved.
        """
        assert apply_reputation_step(85, -7) == -15

    def test_example_8_zero_delta_no_change(self):
        """apply(0, 0) → 0 (trivial no change)."""
        assert apply_reputation_step(0, 0) == 0


# ---------------------------------------------------------------------------
# Farming guard: repeated +1 from 0 must plateau at 25, not reach 100
# ---------------------------------------------------------------------------

class TestFarmingGuard:
    """50× apply(+1) starting from 0 must plateau at POSITIVE_CAP[1]=25."""

    def test_farming_50_times_magnitude_1(self):
        score = 0
        for _ in range(50):
            score = apply_reputation_step(score, 1)
        assert score == POSITIVE_CAP[1], (
            f"50× +1 from 0 must plateau at {POSITIVE_CAP[1]}, got {score}"
        )

    def test_farming_plateau_each_magnitude(self):
        """For each magnitude m in 1..5, repeated +m applications converge to POSITIVE_CAP[m]."""
        for mag in range(1, 6):
            score = 0
            for _ in range(100):
                score = apply_reputation_step(score, mag)
            assert score == POSITIVE_CAP[mag], (
                f"Repeated +{mag} must plateau at {POSITIVE_CAP[mag]}, got {score}"
            )

    def test_farming_guard_already_at_cap(self):
        """apply(cap, +m) for non-epic m → unchanged (farming guard)."""
        for mag in range(1, 6):
            cap = POSITIVE_CAP[mag]
            result = apply_reputation_step(cap, mag)
            assert result == cap, (
                f"apply({cap}, +{mag}): at cap → must return {cap}, got {result}"
            )

    def test_farming_guard_above_cap(self):
        """apply(score > cap, +m) → unchanged (score already above that magnitude's cap)."""
        # e.g. score=80, +1 has cap=25; 80 >= 25 → no change
        result = apply_reputation_step(80, 1)
        assert result == 80, f"apply(80, +1): 80 >= cap(25) → must stay 80, got {result}"

    def test_farming_guard_exact_boundary(self):
        """apply(POSITIVE_CAP[2], +2) → unchanged (boundary case: equal to cap)."""
        cap = POSITIVE_CAP[2]  # 45
        result = apply_reputation_step(cap, 2)
        assert result == cap, f"apply({cap}, +2): at exact cap → {cap}, got {result}"


# ---------------------------------------------------------------------------
# Asymmetry invariant
# ---------------------------------------------------------------------------

class TestAsymmetry:
    """Asymmetry tests for the new NEGATIVE_DROP table.

    Under the old table (NEGATIVE_DROP[1..5] = 15,28,42,58,75), negative drops always
    exceeded the gap-based positive gain from 0 at every magnitude. Under the new
    LENIENT table (1,2,5,10,25), small-magnitude drops (1-4) are now SMALLER than
    the gap-based climb from 0. Only magnitude-5 drop (25) is still smaller than
    the +5 positive gain (36 = ceil(88*0.4)).

    INTENT_CHANGED: The asymmetry shape has inverted at low magnitudes — confirmed
    approved by user. The new design intent is: positive gap-climb is bigger than
    trivial negative drops, but severe drops (mag 6-7: 50-100 pts) remain catastrophic.
    We now test the ACTUAL new relationship rather than the old "loss always > gain".
    """

    def test_asymmetry_magnitude_1_from_zero(self):
        """From 0: +1 gain (10) vs -1 loss (1). New table: gain > loss at mag 1.

        INTENT_CHANGED: small negatives no longer fall faster than climbs at mag 1.
        New arithmetic: gain=10 (ceil(25*0.4)), drop=1 (NEGATIVE_DROP[1]=1).
        """
        gain = apply_reputation_step(0, 1) - 0    # 10
        loss = 0 - apply_reputation_step(0, -1)   # 1
        assert gain == 10, f"Positive gain from 0 at mag-1 must be 10, got {gain}"
        assert loss == 1, f"Negative drop from 0 at mag-1 must be 1, got {loss}"
        # gain(10) > loss(1) — inverted from old table; this is the approved new design

    def test_asymmetry_magnitude_2_from_zero(self):
        """From 0: +2 gain (18) vs -2 loss (2). New table: gain > loss at mag 2.

        INTENT_CHANGED: gain=ceil(45*0.4)=18, drop=NEGATIVE_DROP[2]=2.
        """
        gain = apply_reputation_step(0, 2)         # 18
        drop = -apply_reputation_step(0, -2)       # 2
        assert gain == 18, f"Positive gain from 0 at mag-2 must be 18, got {gain}"
        assert drop == 2, f"Negative drop from 0 at mag-2 must be 2, got {drop}"

    def test_asymmetry_magnitude_3_from_zero(self):
        """From 0: +3 gain (26) vs -3 loss (5). New table: gain > loss at mag 3.

        INTENT_CHANGED: gain=ceil(65*0.4)=26, drop=NEGATIVE_DROP[3]=5.
        """
        gain = apply_reputation_step(0, 3)         # 26
        drop = -apply_reputation_step(0, -3)       # 5
        assert gain == 26, f"Positive gain from 0 at mag-3 must be 26, got {gain}"
        assert drop == 5, f"Negative drop from 0 at mag-3 must be 5, got {drop}"

    def test_asymmetry_magnitude_4_from_zero(self):
        """From 0: +4 gain (32) vs -4 loss (10). New table: gain > loss at mag 4.

        INTENT_CHANGED: gain=ceil(78*0.4)=32 (actually ceil(31.2)=32), drop=NEGATIVE_DROP[4]=10.
        """
        gain = apply_reputation_step(0, 4)         # 32
        drop = -apply_reputation_step(0, -4)       # 10
        assert gain == 32, f"Positive gain from 0 at mag-4 must be 32, got {gain}"
        assert drop == 10, f"Negative drop from 0 at mag-4 must be 10, got {drop}"

    def test_asymmetry_magnitude_5_from_zero(self):
        """From 0: +5 gain (36) vs -5 loss (25). New table: gain still > loss at mag 5.

        INTENT_CHANGED: gain=ceil(88*0.4)=36 (actually ceil(35.2)=36), drop=NEGATIVE_DROP[5]=25.
        """
        gain = apply_reputation_step(0, 5)         # 36
        drop = -apply_reputation_step(0, -5)       # 25
        assert gain == 36, f"Positive gain from 0 at mag-5 must be 36, got {gain}"
        assert drop == 25, f"Negative drop from 0 at mag-5 must be 25, got {drop}"

    def test_asymmetry_severe_magnitudes_6_7_still_catastrophic(self):
        """High-magnitude negative drops (6: 50, 7: 100) remain severe relative to positive climbs.

        From 0: +6 → 95 (epic jump to POSITIVE_CAP[6]); -6 → -50 (NEGATIVE_DROP[6]=50).
        From 0: +7 → 100 (epic jump); -7 → -100 (NEGATIVE_DROP[7]=100, exact clamp).
        The big-negative drops are still the "nuclear" part of the design.
        """
        # mag 6: positive epic jump vs negative severe drop
        pos6 = apply_reputation_step(0, 6)    # 95 (epic jump to cap)
        neg6 = apply_reputation_step(0, -6)   # -50
        assert pos6 == 95, f"apply(0,+6) must be 95, got {pos6}"
        assert neg6 == -50, f"apply(0,-6) must be -50 (NEGATIVE_DROP[6]=50), got {neg6}"

        # mag 7: both extremes
        pos7 = apply_reputation_step(0, 7)    # 100
        neg7 = apply_reputation_step(0, -7)   # -100 (0-100=-100, clamped)
        assert pos7 == 100, f"apply(0,+7) must be 100, got {pos7}"
        assert neg7 == -100, f"apply(0,-7) must be -100, got {neg7}"


# ---------------------------------------------------------------------------
# Epic jump (+6/+7) and epic negative (-6/-7)
# ---------------------------------------------------------------------------

class TestEpicActions:
    """Epic actions (|raw| >= EPIC_THRESHOLD=6) must hit extremes."""

    def test_epic_positive_6_from_zero(self):
        """apply(0, +6) → POSITIVE_CAP[6] = 95."""
        assert apply_reputation_step(0, 6) == POSITIVE_CAP[6]

    def test_epic_positive_7_from_zero(self):
        """apply(0, +7) → POSITIVE_CAP[7] = 100."""
        assert apply_reputation_step(0, 7) == POSITIVE_CAP[7]

    def test_epic_positive_6_from_low(self):
        """apply(-50, +6) → POSITIVE_CAP[6] = 95 (epic heals all enmity)."""
        assert apply_reputation_step(-50, 6) == POSITIVE_CAP[6]

    def test_epic_positive_7_from_negative(self):
        """apply(-80, +7) → 100 (absolute devotion from rock-bottom)."""
        assert apply_reputation_step(-80, 7) == 100

    def test_epic_positive_farming_guard_still_applies(self):
        """apply(96, +6) → 96 unchanged: 96 >= cap(6)=95 → farming guard."""
        # Once you're above cap for epic magnitude, even epic can't move it further
        # (without a +7 which has a higher cap of 100)
        assert apply_reputation_step(96, 6) == 96

    def test_epic_negative_6_deep_drop(self):
        """apply(0, -6) → 0 - 50 = -50 (NEGATIVE_DROP[6] retuned from 95 → 50)."""
        assert apply_reputation_step(0, -6) == -50

    def test_epic_negative_7_clamp_from_high(self):
        """apply(85, -7) → 85 - 100 = -15 (no clamp; NEGATIVE_DROP[7] retuned from 200 → 100).

        INTENT_CHANGED: -7 from score=85 no longer guarantees Blood Enemy. From scores >= 0,
        the drop is 100 pts, so only scores in [0..100] are brought to [-100..0].
        apply(85, -7) = 85 - 100 = -15. Clamp only activates when result < -100.
        """
        assert apply_reputation_step(85, -7) == -15

    def test_epic_negative_7_from_zero(self):
        """apply(0, -7) → 0 - 100 = -100 (NEGATIVE_DROP[7]=100; clamp not needed here)."""
        assert apply_reputation_step(0, -7) == -100

    def test_epic_negative_7_from_negative(self):
        """apply(-30, -7) → -30 - 100 = -130 → clamped -100."""
        assert apply_reputation_step(-30, -7) == -100

    def test_epic_positive_6_stops_at_cap_not_100(self):
        """apply(0, +6) → 95, not 100 (magnitude 6 cap is 95)."""
        result = apply_reputation_step(0, 6)
        assert result == 95, f"Expected 95 (POSITIVE_CAP[6]), got {result}"


# ---------------------------------------------------------------------------
# Clamp boundaries (score stays in [-100, 100])
# ---------------------------------------------------------------------------

class TestClampBoundaries:
    """Score is always clamped to [SCORE_MIN, SCORE_MAX] = [-100, 100]."""

    def test_positive_at_score_max_minus_1(self):
        """apply(99, +7) → 100 (epic jump to cap, clamped to SCORE_MAX)."""
        result = apply_reputation_step(99, 7)
        assert result == 100, f"Expected 100, got {result}"

    def test_positive_already_at_score_max(self):
        """apply(100, +7) → 100 unchanged (already at max; 100 >= cap(7)=100)."""
        result = apply_reputation_step(100, 7)
        assert result == 100, f"Expected 100 (farming guard at max), got {result}"

    def test_negative_at_score_min_plus_1(self):
        """apply(-99, -7) → -100 (clamped to SCORE_MIN)."""
        result = apply_reputation_step(-99, -7)
        assert result == -100, f"Expected -100, got {result}"

    def test_negative_already_at_score_min(self):
        """apply(-100, -1) → -100 (clamped, cannot go below SCORE_MIN)."""
        result = apply_reputation_step(-100, -1)
        assert result == -100, f"Expected -100, got {result}"

    def test_result_never_below_score_min(self):
        """For any (score, raw) combo, result >= SCORE_MIN."""
        test_cases = [(-100, -7), (-100, -1), (-90, -7), (0, -7), (50, -7)]
        for score, raw in test_cases:
            result = apply_reputation_step(score, raw)
            assert result >= SCORE_MIN, (
                f"apply({score}, {raw}) = {result} < SCORE_MIN={SCORE_MIN}"
            )

    def test_result_never_above_score_max(self):
        """For any (score, raw) combo, result <= SCORE_MAX."""
        test_cases = [(100, 7), (100, 1), (90, 7), (0, 7), (-50, 7)]
        for score, raw in test_cases:
            result = apply_reputation_step(score, raw)
            assert result <= SCORE_MAX, (
                f"apply({score}, {raw}) = {result} > SCORE_MAX={SCORE_MAX}"
            )

    def test_clamp_does_not_change_valid_scores(self):
        """Scores already in [-100, 100] are not altered by clamp alone."""
        # This tests that we don't artificially clamp a score that was in range
        for score in [-100, -50, 0, 50, 100]:
            result = apply_reputation_step(score, 0)  # delta 0 → no change
            assert result == score, (
                f"apply({score}, 0) must return {score}, got {result}"
            )


# ---------------------------------------------------------------------------
# Raw delta clamping: values outside [-7, 7] treated as ±7
# ---------------------------------------------------------------------------

class TestRawDeltaClamping:
    """Worker values outside [-7, 7] must be clamped before processing."""

    def test_raw_delta_10_treated_as_7(self):
        """apply(0, 10) behaves identically to apply(0, 7)."""
        assert apply_reputation_step(0, 10) == apply_reputation_step(0, 7)

    def test_raw_delta_minus_99_treated_as_minus_7(self):
        """apply(0, -99) behaves identically to apply(0, -7)."""
        assert apply_reputation_step(0, -99) == apply_reputation_step(0, -7)

    def test_raw_delta_100_from_zero(self):
        """apply(0, 100) → treated as +7 → epic jump → 100."""
        assert apply_reputation_step(0, 100) == 100

    def test_raw_delta_minus_100_from_zero(self):
        """apply(0, -100) → treated as -7 → 0 - 100 = -100 (NEGATIVE_DROP[7]=100)."""
        assert apply_reputation_step(0, -100) == -100

    def test_raw_delta_8_treated_as_7(self):
        """apply(0, 8) → same as apply(0, 7) = 100."""
        assert apply_reputation_step(0, 8) == apply_reputation_step(0, 7)

    def test_raw_delta_minus_8_treated_as_minus_7(self):
        """apply(0, -8) → same as apply(0, -7) = -100."""
        assert apply_reputation_step(0, -8) == apply_reputation_step(0, -7)

    def test_raw_delta_zero_always_no_change(self):
        """apply(any, 0) → any (zero delta is a no-op)."""
        for score in [-100, -50, 0, 50, 100]:
            assert apply_reputation_step(score, 0) == score


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants:
    """Module-level constants must match spec values."""

    def test_positive_cap_keys(self):
        """POSITIVE_CAP must cover magnitudes 1..7."""
        assert set(POSITIVE_CAP.keys()) == {1, 2, 3, 4, 5, 6, 7}

    def test_negative_drop_keys(self):
        """NEGATIVE_DROP must cover magnitudes 1..7."""
        assert set(NEGATIVE_DROP.keys()) == {1, 2, 3, 4, 5, 6, 7}

    def test_positive_cap_values_spec(self):
        """POSITIVE_CAP must match spec values exactly."""
        expected = {1: 25, 2: 45, 3: 65, 4: 78, 5: 88, 6: 95, 7: 100}
        assert POSITIVE_CAP == expected, f"POSITIVE_CAP mismatch: {POSITIVE_CAP}"

    def test_negative_drop_values_spec(self):
        """NEGATIVE_DROP must match the approved new (lenient) values exactly.

        Retuned from OLD: {1:15, 2:28, 3:42, 4:58, 5:75, 6:95, 7:200}
        to          NEW:  {1:1,  2:2,  3:5,  4:10, 5:25, 6:50, 7:100}
        Small slights barely register; only grave betrayals are catastrophic.
        """
        expected = {1: 1, 2: 2, 3: 5, 4: 10, 5: 25, 6: 50, 7: 100}
        assert NEGATIVE_DROP == expected, f"NEGATIVE_DROP mismatch: {NEGATIVE_DROP}"

    def test_pos_climb_rate(self):
        """POS_CLIMB_RATE must be 0.4."""
        assert POS_CLIMB_RATE == 0.4

    def test_epic_threshold(self):
        """EPIC_THRESHOLD must be 6."""
        assert EPIC_THRESHOLD == 6

    def test_score_bounds(self):
        """SCORE_MIN=-100, SCORE_MAX=100."""
        assert SCORE_MIN == -100
        assert SCORE_MAX == 100

    def test_positive_caps_monotonically_increasing(self):
        """Higher magnitude → higher or equal cap."""
        caps = [POSITIVE_CAP[m] for m in sorted(POSITIVE_CAP)]
        for i in range(1, len(caps)):
            assert caps[i] >= caps[i - 1], (
                f"POSITIVE_CAP not monotonic: cap[{i}]={caps[i]} < cap[{i-1}]={caps[i-1]}"
            )

    def test_negative_drops_monotonically_increasing(self):
        """Higher magnitude → larger or equal drop."""
        drops = [NEGATIVE_DROP[m] for m in sorted(NEGATIVE_DROP)]
        for i in range(1, len(drops)):
            assert drops[i] >= drops[i - 1], (
                f"NEGATIVE_DROP not monotonic: drop[{i}]={drops[i]} < drop[{i-1}]={drops[i-1]}"
            )


# ---------------------------------------------------------------------------
# Gap-based diminishing returns (step always >= 1)
# ---------------------------------------------------------------------------

class TestDiminishingReturns:
    """Step must always be at least 1 even when gap is tiny (boundary near cap)."""

    def test_step_at_least_1_near_cap(self):
        """apply(cap - 1, +m) gives step of exactly 1 (minimum step)."""
        import math
        for mag in range(1, 6):
            cap = POSITIVE_CAP[mag]
            score_near_cap = cap - 1  # gap = 1
            # step = max(1, ceil(1 * 0.4)) = max(1, 1) = 1
            expected = min(cap, score_near_cap + 1)
            result = apply_reputation_step(score_near_cap, mag)
            assert result == expected, (
                f"apply({score_near_cap}, +{mag}): expected {expected}, got {result}"
            )

    def test_step_does_not_overshoot_cap(self):
        """apply(cap - 2, +m) must not exceed cap."""
        for mag in range(1, 6):
            cap = POSITIVE_CAP[mag]
            score = cap - 2  # gap = 2
            result = apply_reputation_step(score, mag)
            assert result <= cap, (
                f"apply({score}, +{mag}): result {result} overshoots cap {cap}"
            )

    def test_positive_result_always_greater_than_current(self):
        """Positive delta with room to grow always increases the score."""
        for mag in range(1, 6):
            cap = POSITIVE_CAP[mag]
            # Start below cap so farming guard doesn't trigger
            score = cap - 10
            result = apply_reputation_step(score, mag)
            assert result > score, (
                f"apply({score}, +{mag}): {result} must be > {score} (room to grow)"
            )
