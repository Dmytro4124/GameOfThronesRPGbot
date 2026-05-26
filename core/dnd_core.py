"""Core D&D 5e math adapted for ASoIaF setting (Фаза 1)."""

import random
from dataclasses import dataclass
from typing import Literal

# Legal DC values for the ASoIaF-calibrated d20 system (max 22; see CLAUDE.md §5.2).
# DC 2 reserved for ultra-trivial actions (auto-success skip; see AUTO_SUCCESS_MAX_DC).
LEGAL_DCS: tuple[int, ...] = (2, 5, 10, 12, 15, 17, 20, 22)

_ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")


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
