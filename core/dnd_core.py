"""Core D&D 5e math adapted for ASoIaF setting (Фаза 1)."""

import random
from dataclasses import dataclass
from typing import Literal

# Legal DC values for the ASoIaF-calibrated d20 system (max 22; see CLAUDE.md §5.2).
# DC 2 reserved for ultra-trivial actions (auto-success skip; see AUTO_SUCCESS_MAX_DC).
LEGAL_DCS: tuple[int, ...] = (2, 5, 10, 12, 15, 17, 20, 22)

_ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

# ---------------------------------------------------------------------------
# Relation ↔ Score mapping (15-step scale)
# Shared API for dnd_engine.py and database/canon_npc.py.
# Relation↔score mapping is the single source of truth here.
# database/canon_npc.py re-imports these (RELATION_TO_SCORE, map_relation_to_score,
# score_to_relation_text) — do not duplicate the table elsewhere.
# ---------------------------------------------------------------------------

# Reverse mapping: canonical lowercase label → numeric score.
# The "старі значення" block preserves backward compat with legacy CANON_NPCS entries.
RELATION_TO_SCORE: dict[str, int] = {
    # ── 15-ступенева шкала ──
    "смертельна ненависть":  -80,
    "кривавий ворог":        -65,
    "відкрита ворожість":    -50,
    "ворожий":               -35,
    "глибока підозра":       -25,
    "підозрілий":            -15,
    "холодний":               -5,
    "нейтральний":             0,
    "обережно відкритий":     10,
    "тепле ставлення":        20,
    "прихильний":             30,
    "дружній":                45,
    "довіряє":                60,
    "глибока довіра":         70,
    "абсолютна довіра":       85,
    # ── Старі значення (backward compat для CANON_NPCS) ──
    "дружня":                 30,
    "дружня (фальшива)":      10,
    "нейтральна":              0,
    "обережна":              -10,
    "зверхня":               -20,
    "підозріла":             -30,
    "агресивна":             -50,
    "ворожа":                -60,
}


def map_relation_to_score(relation_text: str) -> int:
    """Convert a textual relation label to a numeric Reputation_Score.

    Case-insensitive.  Unknown labels return 0 (Neutral).
    """
    if not relation_text:
        return 0
    key = str(relation_text).strip().lower()
    return RELATION_TO_SCORE.get(key, 0)


def score_to_relation_text(score: int) -> str:
    """Convert a numeric Reputation_Score to a 15-step textual relation label.

    Thresholds (inclusive lower bound, descending):
      ≥  85 → "Абсолютна довіра"
      ≥  70 → "Глибока довіра"
      ≥  60 → "Довіряє"
      ≥  45 → "Дружній"
      ≥  30 → "Прихильний"
      ≥  20 → "Тепле ставлення"
      ≥  10 → "Обережно відкритий"
      ≥  -5 → "Нейтральний"
      ≥ -15 → "Холодний"
      ≥ -25 → "Підозрілий"
      ≥ -35 → "Глибока підозра"
      ≥ -50 → "Ворожий"
      ≥ -65 → "Відкрита ворожість"
      ≥ -80 → "Кривавий ворог"
           < -80 → "Смертельна ненависть"
    """
    if score >= 85:  return "Абсолютна довіра"
    if score >= 70:  return "Глибока довіра"
    if score >= 60:  return "Довіряє"
    if score >= 45:  return "Дружній"
    if score >= 30:  return "Прихильний"
    if score >= 20:  return "Тепле ставлення"
    if score >= 10:  return "Обережно відкритий"
    if score >= -5:  return "Нейтральний"
    if score >= -15: return "Холодний"
    if score >= -25: return "Підозрілий"
    if score >= -35: return "Глибока підозра"
    if score >= -50: return "Ворожий"
    if score >= -65: return "Відкрита ворожість"
    if score >= -80: return "Кривавий ворог"
    return "Смертельна ненависть"


def roll_d20(advantage: bool = False, disadvantage: bool = False) -> tuple[int, list[int]]:
    """Roll d20 with optional advantage/disadvantage; both True cancels out to normal roll."""
    if advantage and disadvantage:
        advantage = False
        disadvantage = False

    if advantage:
        rolls = [random.randint(1, 20), random.randint(1, 20)]
        return max(rolls), rolls
    if disadvantage:
        rolls = [random.randint(1, 20), random.randint(1, 20)]
        return min(rolls), rolls

    roll = random.randint(1, 20)
    return roll, [roll]


def ability_modifier(score: int) -> int:
    """Return (score - 10) // 2, clamped to -5..+10 per D&D PHB p.13."""
    mod = (score - 10) // 2
    return max(-5, min(10, mod))


def proficiency_bonus(level: int) -> int:
    """Return proficiency bonus for given level per D&D PHB p.15 (L1-20)."""
    if level <= 0:
        level = 1
    if level > 20:
        level = 20
    # +2 at L1-4, +3 at L5-8, +4 at L9-12, +5 at L13-16, +6 at L17-20
    return 2 + (level - 1) // 4


def clamp_dc(value: int) -> int:
    """Snap value to nearest legal ASoIaF DC in (5,10,12,15,17,20,22)."""
    if value <= LEGAL_DCS[0]:
        return LEGAL_DCS[0]
    if value >= LEGAL_DCS[-1]:
        return LEGAL_DCS[-1]
    best = LEGAL_DCS[0]
    best_dist = abs(value - best)
    for dc in LEGAL_DCS[1:]:
        d = abs(value - dc)
        if d < best_dist:
            best_dist = d
            best = dc
    return best


# ---------------------------------------------------------------------------
# Reputation → DC band-matrix (§5.2 hard rule, implemented in Python engine)
# ---------------------------------------------------------------------------

# Action severity tiers — source of truth for reputation_adjusted_dc.
# Worker returns action_severity ∈ SEVERITY_TIERS; engine translates to DC.
SEVERITY_TIERS: tuple[str, ...] = ("TRIVIAL", "NORMAL", "HARD", "HORRIBLE")

# DC boundaries for the band-matrix.
REP_DC_FLOOR: int = 2   # DC can never go below 2 (LEGAL_DCS[0])
CRIT_ONLY_DC: int = 22  # DC 22 = "crit-only" (nat-20 auto-success still fires)

# Fallback / documentation base-DC per severity (baseline band 0..20).
# Used as quick reference; authoritative values are in REP_DC_MATRIX.
SEVERITY_BASE_DC: dict[str, int] = {
    "TRIVIAL":  5,
    "NORMAL":  10,
    "HARD":    15,
    "HORRIBLE": 20,
}

# Reputation→DC band-matrix.
# Each entry: ((score_low, score_high), {tier: dc}).
# All DC values are already legal (∈ LEGAL_DCS).
# Bands are inclusive, non-overlapping, and cover -100..100 exhaustively.
# Neutral band (score == 0) is listed explicitly for readability even though
# it shares DC values with the 1..20 band.
REP_DC_MATRIX: list[tuple[tuple[int, int], dict[str, int]]] = [
    # (low, high)   TRIVIAL  NORMAL  HARD  HORRIBLE
    ((81,  100), {"TRIVIAL":  2, "NORMAL":  2, "HARD":  2, "HORRIBLE":  2}),
    ((61,   80), {"TRIVIAL":  2, "NORMAL":  2, "HARD":  2, "HORRIBLE":  5}),
    ((41,   60), {"TRIVIAL":  2, "NORMAL":  2, "HARD":  5, "HORRIBLE": 10}),
    ((21,   40), {"TRIVIAL":  2, "NORMAL":  5, "HARD": 12, "HORRIBLE": 15}),
    (( 1,   20), {"TRIVIAL":  5, "NORMAL": 10, "HARD": 15, "HORRIBLE": 20}),
    (( 0,    0), {"TRIVIAL":  5, "NORMAL": 10, "HARD": 15, "HORRIBLE": 20}),
    ((-20,  -1), {"TRIVIAL": 10, "NORMAL": 12, "HARD": 17, "HORRIBLE": 22}),
    ((-40, -21), {"TRIVIAL": 10, "NORMAL": 15, "HARD": 20, "HORRIBLE": 22}),
    ((-60, -41), {"TRIVIAL": 15, "NORMAL": 17, "HARD": 20, "HORRIBLE": 22}),
    ((-80, -61), {"TRIVIAL": 15, "NORMAL": 20, "HARD": 22, "HORRIBLE": 22}),
    ((-100, -81), {"TRIVIAL": 20, "NORMAL": 20, "HARD": 22, "HORRIBLE": 22}),
]


def reputation_adjusted_dc(severity: str, rep_score: int | None) -> int:
    """Return DC from the reputation band-matrix for the given severity and reputation score.

    Args:
        severity: action severity string. Normalized to .strip().upper() internally.
            Must be one of SEVERITY_TIERS; if not — returns sentinel -1 (caller keeps
            Worker-supplied difficulty unchanged).
        rep_score: numeric reputation score for the target NPC (-100..100).
            None → treated as baseline band (1..20), same as neutral/unknown NPC.

    Returns:
        int DC ∈ LEGAL_DCS (2..22), or -1 if severity is invalid/unknown.

    Guarantees:
        - Never raises on valid inputs (severity str + int/None rep_score).
        - rep_score outside -100..100 is clamped to the nearest extremal band.
        - Every returned DC passes through clamp_dc() as a safety-net even though
          matrix values are pre-validated as legal.
        - DC floor: REP_DC_FLOOR (2), DC ceiling: CRIT_ONLY_DC (22).
    """
    # --- Normalize severity ---
    norm_severity = severity.strip().upper() if severity else ""
    if norm_severity not in SEVERITY_TIERS:
        return -1  # sentinel: caller must leave difficulty unchanged

    # --- Resolve rep_score: None → baseline band (same as 1..20) ---
    if rep_score is None:
        rep_score = 1  # lands in the (1, 20) band

    # --- Clamp score to matrix coverage: -100..100 ---
    # Scores outside this range are theoretical (profile clamp should prevent them),
    # but clamp here defensively so we never miss all matrix rows.
    if rep_score > 100:
        rep_score = 100
    elif rep_score < -100:
        rep_score = -100

    # --- Linear scan: first matching band wins ---
    for (low, high), tier_map in REP_DC_MATRIX:
        if low <= rep_score <= high:
            raw_dc = tier_map[norm_severity]
            # Safety-net clamp (values are pre-validated, but invariant must hold)
            return clamp_dc(raw_dc)

    # Should never reach here (matrix covers -100..100 fully), but be safe:
    return clamp_dc(SEVERITY_BASE_DC[norm_severity])


@dataclass
class CheckResult:
    """Full result of a d20 ability/skill/saving throw check."""
    total: int
    natural: int
    ability_mod: int
    prof_bonus: int
    dc: int
    success: bool
    critical: Literal["fail", "none", "success"]
    roll_str: str  # Human-readable breakdown, e.g. "[12,18]→18 +2(WIS) +2(prof) = 22 vs DC 15 → SUCCESS"


def _resolve_critical(natural: int, success: bool) -> Literal["fail", "none", "success"]:
    """Map natural roll to critical status per D&D 5e ability check rules."""
    if natural == 1:
        return "fail"
    if natural == 20:
        return "success"
    return "none"


def _build_roll_str(
    rolls: list[int],
    natural: int,
    ability: str,
    ab_mod: int,
    prof: int,
    total: int,
    dc: int,
    success: bool,
    expertise: bool = False,
) -> str:
    """Build a human-readable roll breakdown string."""
    rolls_part = f"[{','.join(str(r) for r in rolls)}]→{natural}"
    mod_part = f"{ab_mod:+d}({ability})"
    prof_label = "expertise" if expertise else "prof"
    prof_part = f" {prof:+d}({prof_label})" if prof != 0 else ""
    result_word = "SUCCESS" if success else "FAIL"
    return f"{rolls_part} {mod_part}{prof_part} = {total} vs DC {dc} → {result_word}"


def ability_check(
    profile: dict,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    proficient: bool = False,
) -> CheckResult:
    """Roll 1d20 + ability_mod + (prof_bonus if proficient) vs DC."""
    score = profile.get("ability_scores", {}).get(ability, 10)
    ab_mod = ability_modifier(score)
    level = profile.get("level", 1)
    prof = proficiency_bonus(level) if proficient else 0

    natural, rolls = roll_d20(advantage, disadvantage)
    total = natural + ab_mod + prof
    success = total >= dc
    # nat 1 forces failure, nat 20 forces success on ability checks (optional rule used here)
    if natural == 1:
        success = False
    elif natural == 20:
        success = True

    critical = _resolve_critical(natural, success)
    roll_str = _build_roll_str(rolls, natural, ability, ab_mod, prof, total, dc, success)
    return CheckResult(
        total=total,
        natural=natural,
        ability_mod=ab_mod,
        prof_bonus=prof,
        dc=dc,
        success=success,
        critical=critical,
        roll_str=roll_str,
    )


def skill_check(
    profile: dict,
    skill_name: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
) -> CheckResult:
    """Roll 1d20 + ability_mod + prof_bonus (if proficient) + prof_bonus again (if expertise) vs DC."""
    # Import here to avoid circular import; dnd_skills has no dependency on dnd_core.
    from core.dnd_skills import SKILLS, get_ability_for_skill  # noqa: PLC0415

    ability = get_ability_for_skill(skill_name)  # raises KeyError for invalid skill
    score = profile.get("ability_scores", {}).get(ability, 10)
    ab_mod = ability_modifier(score)
    level = profile.get("level", 1)
    pb = proficiency_bonus(level)

    skill_profs: list[str] = profile.get("skill_profs", [])
    skill_expertise: list[str] = profile.get("skill_expertise", [])

    expertise = skill_name in skill_expertise
    proficient_only = skill_name in skill_profs and not expertise

    if expertise:
        prof = pb * 2
    elif proficient_only:
        prof = pb
    else:
        prof = 0

    natural, rolls = roll_d20(advantage, disadvantage)
    total = natural + ab_mod + prof
    success = total >= dc
    if natural == 1:
        success = False
    elif natural == 20:
        success = True

    critical = _resolve_critical(natural, success)
    roll_str = _build_roll_str(rolls, natural, ability, ab_mod, prof, total, dc, success, expertise=expertise)
    return CheckResult(
        total=total,
        natural=natural,
        ability_mod=ab_mod,
        prof_bonus=prof,
        dc=dc,
        success=success,
        critical=critical,
        roll_str=roll_str,
    )


def saving_throw(
    profile: dict,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
) -> CheckResult:
    """Roll 1d20 + ability_mod + (prof_bonus if ability in profile['saves_proficient']) vs DC."""
    score = profile.get("ability_scores", {}).get(ability, 10)
    ab_mod = ability_modifier(score)
    level = profile.get("level", 1)
    saves_proficient: list[str] = profile.get("saves_proficient", [])
    prof = proficiency_bonus(level) if ability in saves_proficient else 0

    natural, rolls = roll_d20(advantage, disadvantage)
    total = natural + ab_mod + prof
    success = total >= dc
    if natural == 1:
        success = False
    elif natural == 20:
        success = True

    critical = _resolve_critical(natural, success)
    roll_str = _build_roll_str(rolls, natural, ability, ab_mod, prof, total, dc, success)
    return CheckResult(
        total=total,
        natural=natural,
        ability_mod=ab_mod,
        prof_bonus=prof,
        dc=dc,
        success=success,
        critical=critical,
        roll_str=roll_str,
    )
