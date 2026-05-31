"""D&D 5e XP progression, level-up math, ASI (Phase 3).

Standalone module — no engine/prompts/mechanics/world imports.
Imports only from core.dnd_* and stdlib.
"""

import logging
import random
from dataclasses import dataclass, field

from core.dnd_core import ability_modifier, proficiency_bonus
from core.dnd_classes import Feature, get_class, get_class_features_at_level, GOT_CLASSES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XP table — D&D 5e PHB p.15 "Character Advancement"
# ---------------------------------------------------------------------------

XP_TABLE: dict[int, int] = {
    1:  0,        2:  300,      3:  900,      4:  2_700,    5:  6_500,
    6:  14_000,   7:  23_000,   8:  34_000,   9:  48_000,   10: 64_000,
    11: 85_000,   12: 100_000,  13: 120_000,  14: 140_000,  15: 165_000,
    16: 195_000,  17: 225_000,  18: 265_000,  19: 305_000,  20: 355_000,
}

MAX_LEVEL: int = 20
MAX_ABILITY_SCORE: int = 20  # ASI hard cap per D&D PHB

# ASI-eligible levels per D&D 5e (same for all classes in this implementation)
_ASI_LEVELS: frozenset[int] = frozenset({4, 8, 12, 16, 19})

# Valid ability keys
_ABILITY_KEYS: frozenset[str] = frozenset({"STR", "DEX", "CON", "INT", "WIS", "CHA"})

# ---------------------------------------------------------------------------
# Encounter XP thresholds per character — DMG p.82 "Encounter Building"
# Full L1-20 table (hardcoded from DMG, not extrapolated).
# ---------------------------------------------------------------------------

_ENCOUNTER_XP_THRESHOLDS: dict[int, dict[str, int]] = {
    1:  {"easy":  25,   "medium":  50,   "hard":   75,   "deadly":  100},
    2:  {"easy":  50,   "medium":  100,  "hard":   150,  "deadly":  200},
    3:  {"easy":  75,   "medium":  150,  "hard":   225,  "deadly":  400},
    4:  {"easy":  125,  "medium":  250,  "hard":   375,  "deadly":  500},
    5:  {"easy":  250,  "medium":  500,  "hard":   750,  "deadly":  1_100},
    6:  {"easy":  300,  "medium":  600,  "hard":   900,  "deadly":  1_400},
    7:  {"easy":  350,  "medium":  750,  "hard":   1_100, "deadly": 1_700},
    8:  {"easy":  450,  "medium":  900,  "hard":   1_400, "deadly": 2_100},
    9:  {"easy":  550,  "medium":  1_100, "hard":  1_600, "deadly": 2_400},
    10: {"easy":  600,  "medium":  1_200, "hard":  1_900, "deadly": 2_800},
    11: {"easy":  800,  "medium":  1_600, "hard":  2_400, "deadly": 3_600},
    12: {"easy":  1_000, "medium": 2_000, "hard":  3_000, "deadly": 4_500},
    13: {"easy":  1_100, "medium": 2_200, "hard":  3_400, "deadly": 5_100},
    14: {"easy":  1_250, "medium": 2_500, "hard":  3_800, "deadly": 5_700},
    15: {"easy":  1_400, "medium": 2_800, "hard":  4_300, "deadly": 6_400},
    16: {"easy":  1_600, "medium": 3_200, "hard":  4_800, "deadly": 7_200},
    17: {"easy":  2_000, "medium": 3_900, "hard":  5_900, "deadly": 8_800},
    18: {"easy":  2_100, "medium": 4_200, "hard":  6_300, "deadly": 9_500},
    19: {"easy":  2_400, "medium": 4_900, "hard":  7_300, "deadly": 10_900},
    20: {"easy":  2_800, "medium": 5_700, "hard":  8_500, "deadly": 12_700},
}

_VALID_DIFFICULTIES: frozenset[str] = frozenset({"easy", "medium", "hard", "deadly"})


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class XPAwardResult:
    """Result of a single award_xp() call."""
    xp_before: int
    xp_after: int
    level_before: int
    level_after: int
    leveled_up: bool
    levels_gained: int   # >1 possible when a single XP grant crosses multiple thresholds


@dataclass
class LevelUpResult:
    """Result of a single level_up() call."""
    new_level: int
    hp_gained: int                    # roll(hit_die) + CON_mod, minimum 1
    new_features: list[Feature] = field(default_factory=list)
    proficiency_bonus_changed: bool = False
    asi_pending: bool = False         # True if this level is L4/8/12/16/19


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def xp_required_for_level(level: int) -> int:
    """Return total XP required to reach ``level`` (D&D 5e PHB p.15).

    Raises ValueError if level is outside 1-20.
    """
    if level < 1 or level > MAX_LEVEL:
        raise ValueError(f"Level must be 1-{MAX_LEVEL}, got {level!r}.")
    return XP_TABLE[level]


def get_level_for_xp(xp: int) -> int:
    """Return character level for the given total XP (reverse-lookup of XP_TABLE).

    Edge cases:
    - xp < 0  → level 1
    - xp >= 355000 → level 20
    """
    if xp < 0:
        return 1
    current_level = 1
    for lvl in range(1, MAX_LEVEL + 1):
        if xp >= XP_TABLE[lvl]:
            current_level = lvl
        else:
            break
    return current_level


def award_xp(profile: dict, amount: int, reason: str = "") -> XPAwardResult:
    """Add ``amount`` XP to profile and update profile['level'] if thresholds crossed.

    NOTE: this function does NOT call level_up(). It only updates the 'xp'
    and 'level' keys in the profile dict. The caller is responsible for invoking
    level_up() once per level gained to apply HP / features / proficiency_bonus.

    Mutates profile in-place: profile['xp'] and profile['level'].
    Negative amount is treated as 0 (no XP removal).
    Returns XPAwardResult.
    """
    if amount < 0:
        amount = 0

    xp_before: int = profile.get("xp", 0)
    level_before: int = profile.get("level", 1)

    xp_after = xp_before + amount
    level_after = get_level_for_xp(xp_after)

    profile["xp"] = xp_after
    profile["level"] = level_after

    levels_gained = level_after - level_before
    leveled_up = levels_gained > 0

    return XPAwardResult(
        xp_before=xp_before,
        xp_after=xp_after,
        level_before=level_before,
        level_after=level_after,
        leveled_up=leveled_up,
        levels_gained=levels_gained,
    )


def level_up(profile: dict, class_name: str, hp_roll: int | None = None) -> LevelUpResult:
    """Apply a single level-up to profile (mutates in-place).

    The caller guarantees that profile['xp'] >= XP_TABLE[new_level] before
    calling this function. level_up does not re-validate XP.

    What is mutated:
    - profile['level'] += 1
    - profile['hp_max'] += hp_gained  (min 1 per level)
    - profile['hp_current'] is NOT changed (caller may want to heal separately)
    - profile['hit_dice_total'] += 1
    - profile['features'] list is extended with new_features
    - profile['proficiency_bonus'] is updated
    - profile['asi_pending'] is set True if new level is in _ASI_LEVELS

    hp_roll: optional override for the hit-die roll (used in tests). If None,
    rolls random.randint(1, hit_die). CON modifier is always added on top.

    Raises:
    - KeyError if class_name is not a known GoT class.
    - ValueError if profile['level'] is already 20 (cannot level up beyond cap).
    """
    current_level: int = profile.get("level", 1)
    if current_level >= MAX_LEVEL:
        raise ValueError(
            f"Cannot level up beyond level {MAX_LEVEL}. "
            f"Profile is already at level {current_level}."
        )

    cls = get_class(class_name)  # KeyError on unknown class

    new_level = current_level + 1
    profile["level"] = new_level

    # --- HP calculation ---
    hit_die = cls.hit_die
    con_score = profile.get("ability_scores", {}).get("CON", 10)
    con_mod = ability_modifier(con_score)

    if hp_roll is None:
        rolled = random.randint(1, hit_die)
    else:
        # Clamp caller-supplied roll to valid range
        rolled = max(1, min(hp_roll, hit_die))

    hp_gained = max(1, rolled + con_mod)

    old_hp_max = profile.get("hp_max", 1)
    profile["hp_max"] = old_hp_max + hp_gained

    # --- Hit dice pool ---
    profile["hit_dice_total"] = profile.get("hit_dice_total", current_level) + 1

    # --- Features ---
    new_features: list[Feature] = get_class_features_at_level(class_name, new_level)
    existing_features: list = profile.get("features", [])
    # Append as dicts for JSON-serialisability; callers that need Feature objects
    # can reconstruct via Feature(**f_dict).
    for feat in new_features:
        existing_features.append({
            "name": feat.name,
            "desc": feat.desc,
            "source": feat.source,
        })
    profile["features"] = existing_features

    # --- Proficiency bonus ---
    old_prof = proficiency_bonus(current_level)
    new_prof = proficiency_bonus(new_level)
    profile["proficiency_bonus"] = new_prof
    prof_changed = new_prof != old_prof

    # --- ASI flag ---
    asi_pending = new_level in _ASI_LEVELS
    profile["asi_pending"] = asi_pending

    return LevelUpResult(
        new_level=new_level,
        hp_gained=hp_gained,
        new_features=new_features,
        proficiency_bonus_changed=prof_changed,
        asi_pending=asi_pending,
    )


def apply_asi(profile: dict, choices: dict[str, int]) -> dict:
    """Apply an Ability Score Improvement to the profile (mutates in-place).

    choices must be exactly one of:
    - {'ABILITY': 2}          — put both points in one ability
    - {'ABILITY': 1, 'OTHER': 1}  — split one point each across two abilities

    Rules:
    - sum(choices.values()) must equal 2.
    - All keys must be in {"STR","DEX","CON","INT","WIS","CHA"}.
    - No ability may exceed MAX_ABILITY_SCORE (20) after the increase.
    - profile['asi_pending'] is set to False after a successful apply.

    Returns the mutated profile dict.
    Raises ValueError on any validation failure.
    """
    # --- Validate keys ---
    bad_keys = set(choices.keys()) - _ABILITY_KEYS
    if bad_keys:
        raise ValueError(
            f"Invalid ability key(s): {bad_keys!r}. "
            f"Must be subset of {sorted(_ABILITY_KEYS)}."
        )

    # --- Validate point total ---
    total_points = sum(choices.values())
    if total_points != 2:
        raise ValueError(
            f"ASI choices must sum to exactly 2, got {total_points} "
            f"(choices={choices!r})."
        )

    # --- Validate individual values are positive ---
    for ability, delta in choices.items():
        if delta <= 0:
            raise ValueError(
                f"Each ASI delta must be positive, got {ability}={delta!r}."
            )

    # --- Validate cap ---
    ability_scores: dict = profile.setdefault("ability_scores", {})
    for ability, delta in choices.items():
        current = ability_scores.get(ability, 10)
        new_score = current + delta
        if new_score > MAX_ABILITY_SCORE:
            raise ValueError(
                f"ASI would raise {ability} from {current} to {new_score}, "
                f"exceeding the cap of {MAX_ABILITY_SCORE}."
            )

    # --- Apply ---
    for ability, delta in choices.items():
        ability_scores[ability] = ability_scores.get(ability, 10) + delta

    profile["ability_scores"] = ability_scores
    profile["asi_pending"] = False

    # A4: Recompute AC after ASI — DEX change would otherwise leave AC stale until
    # the next inventory event.  try/except guards against circular-import risk in
    # pure unit-test contexts where dnd_engine may not be importable.
    try:
        from core.dnd_engine import recompute_ac
        recompute_ac(profile)
    except ImportError as _e:
        logger.warning(
            "[DND_PROGRESSION] recompute_ac import failed in apply_asi — AC may be stale: %s",
            _e,
        )

    return profile


def apply_pending_levelups(
    profile: dict,
    level_before: int,
    levels_gained: int,
    class_name: str = "",
) -> list[str]:
    """Apply level_up() one step at a time after award_xp() has set new level.

    Caller MUST call award_xp() before this function — award_xp() sets
    profile['level'] = level_after.  This function temporarily resets
    profile['level'] to level_before and then calls level_up() for each
    gained level so that HP, features, proficiency bonus, and ASI are all
    applied correctly one level at a time.

    Args:
        profile:      Character profile dict (mutated in-place).
        level_before: Level before award_xp() was called.
        levels_gained: Number of levels crossed (level_after - level_before).
        class_name:   GoT class name (e.g. "Knight"). Falls back to "Knight"
                      if empty or unknown.

    Returns:
        List of human-readable log strings describing HP gains, proficiency
        changes, new features, and ASI decisions.  Never raises — errors are
        captured into log strings so callers do not need try/except.

    Invariants preserved:
        - ability_scores capped at MAX_ABILITY_SCORE (20) per D&D PHB.
        - HP minimum gain of 1 per level (enforced inside level_up()).
        - ASI deferred — `profile['asi_pending']` is left True for UI
          confirmation.  The handler calls `apply_asi(profile, choices)` after
          the player selects via the picker.  `ability_scores` are NOT mutated
          by this function for ASI levels.
        - profile['level'] ends at level_before + levels_gained on success,
          or at the last successfully processed level on ValueError (max level).
    """
    if levels_gained <= 0:
        return []

    logs: list[str] = []

    # Resolve and validate class name with fallback
    if not class_name or class_name not in GOT_CLASSES:
        logger.warning(
            "[DND_PROGRESSION] Unknown or missing class %r — "
            "falling back to 'Knight' for apply_pending_levelups.",
            class_name,
        )
        class_name = "Knight"

    # award_xp() already set profile['level'] = level_after.
    # level_up() increments profile['level'] += 1 per call, so we must
    # reset to level_before first and walk up one level at a time.
    profile["level"] = level_before

    for _ in range(levels_gained):
        try:
            level_result = level_up(profile, class_name, hp_roll=None)
        except ValueError as exc:
            # Already at MAX_LEVEL — level_up() raises ValueError.
            logs.append(f"Max level reached: {exc}")
            break

        logs.append(
            f"LEVEL {profile['level'] - 1} -> {profile['level']}! "
            f"HP +{level_result.hp_gained}"
        )

        if level_result.proficiency_bonus_changed:
            logs.append(
                f"Proficiency bonus -> +{profile.get('proficiency_bonus', '?')}"
            )

        for feat in level_result.new_features:
            logs.append(f"New feature: {feat.name} -- {feat.desc[:80]}...")

        # ASI: do NOT auto-distribute. Leave asi_pending=True (already set by
        # level_up() above). The player must confirm via UI; the handler will
        # call apply_asi(profile, choices) once the player selects their stat.
        if level_result.asi_pending:
            new_level = profile["level"]
            logs.append(
                f"⚠️ Ability Score Improvement (рівень {new_level}): оберіть розподіл"
                f" через UI — натисніть кнопки на повідомленні."
            )

    return logs


def xp_for_encounter_difficulty(party_level: int, difficulty: str = "medium") -> int:
    """Return XP threshold per character for an encounter of given difficulty (DMG p.82).

    party_level: 1-20. Values outside the range are clamped.
    difficulty: one of {'easy', 'medium', 'hard', 'deadly'}.

    Raises ValueError for unknown difficulty strings.
    """
    if difficulty not in _VALID_DIFFICULTIES:
        raise ValueError(
            f"Unknown difficulty {difficulty!r}. "
            f"Must be one of {sorted(_VALID_DIFFICULTIES)}."
        )

    # Clamp party level to valid range
    level = max(1, min(party_level, MAX_LEVEL))

    return _ENCOUNTER_XP_THRESHOLDS[level][difficulty]
