"""D&D 5e PHB Ch. 8 — Short Rest and Long Rest mechanics (Phase 3).

Standalone module — no engine/prompts/mechanics/world imports.
Imports only from core.dnd_* and stdlib.

Caller responsibilities (NOT handled here):
- Advancing in-world time (minutes_passed) after a rest.
- Enforcing narrative preconditions (e.g., 'not in active combat').
- Heritage-specific interactions (e.g., Wildling cold immunity) — Phase 7.
- Class-specific rest features (e.g., Hedge Knight 'Road Worn' sleep in armour) — Phase 7.
"""

import random
from dataclasses import dataclass, field

from core.dnd_core import ability_modifier
from core.dnd_classes import get_class
from core.dnd_conditions import has_condition, remove_condition

# ---------------------------------------------------------------------------
# Conditions cleared by a long rest (D&D 5e PHB RAW)
# "petrified" is magical and NOT cleared. "unconscious" from 0 HP resolves when
# HP is restored; structural unconscious (magical) is also NOT auto-cleared here.
# ---------------------------------------------------------------------------

_LONG_REST_CLEARED_CONDITIONS: tuple[str, ...] = (
    "poisoned",
    "frightened",
    "charmed",
    "prone",
    "bleeding",
    "blinded",
    "deafened",
    "stunned",
    "paralyzed",
    "restrained",
    "grappled",
    "incapacitated",
)

# NOT cleared on long rest: "petrified", "invisible", "unconscious"

_SHORT_REST_DURATION_MINUTES: int = 60
_LONG_REST_DURATION_MINUTES: int = 480


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RestResult:
    """Unified result object returned by both short_rest() and long_rest()."""
    rest_type: str               # "short" or "long"
    hp_restored: int             # HP actually restored (may be 0)
    hit_dice_spent: int          # hit dice spent (short rest); 0 for long rest
    hit_dice_regained: int       # hit dice regained; 0 for short rest
    conditions_cleared: list[str] = field(default_factory=list)
    duration_minutes: int = 0    # 60 for short, 480 for long
    log: str = ""                # human-readable summary of the rest


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _roll_die(sides: int) -> int:
    """Roll a single die with ``sides`` faces. Minimum result: 1."""
    return random.randint(1, max(1, sides))


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def can_short_rest(profile: dict) -> tuple[bool, str]:
    """Return (True, "") if the character can take a short rest.

    Requirements (D&D 5e PHB):
    - hp_current > 0  (unconscious / dead characters cannot rest)
    - has at least 1 unspent hit die available

    Returns (False, reason_str) if any requirement is not met.
    """
    hp_current: int = profile.get("hp_current", 0)
    if hp_current <= 0:
        return False, "Персонаж непритомний або мертвий (hp_current = 0)."

    hit_dice_total: int = profile.get("hit_dice_total", 1)
    hit_dice_used: int = profile.get("hit_dice_used", 0)
    available = hit_dice_total - hit_dice_used

    if available <= 0:
        return False, (
            f"Немає доступних кубиків хітів для короткого відпочинку "
            f"(використано {hit_dice_used}/{hit_dice_total})."
        )

    return True, ""


def can_long_rest(profile: dict) -> tuple[bool, str]:
    """Return (True, "") if the character can take a long rest.

    Requirements (D&D 5e PHB):
    - hp_current >= 1  (must be conscious to rest)

    Returns (False, reason_str) if the requirement is not met.
    """
    hp_current: int = profile.get("hp_current", 0)
    if hp_current < 1:
        return False, "Персонаж непритомний або мертвий (hp_current < 1)."

    return True, ""


# ---------------------------------------------------------------------------
# Short rest
# ---------------------------------------------------------------------------

def short_rest(profile: dict, hit_dice_spent: int) -> RestResult:
    """Resolve a 1-hour short rest where the player spends N hit dice.

    Each hit die spent: roll(hit_die) + CON_mod, minimum 1 per die → HP restored.
    HP restored is capped at (hp_max - hp_current) — cannot overheal.
    hit_dice_used is incremented by the number actually spent.

    Mutates profile in-place:
    - profile['hp_current']
    - profile['hit_dice_used']

    Args:
        profile: character profile dict.
        hit_dice_spent: number of hit dice the player wishes to spend.
            Clamped to the number of available dice (never negative).

    Returns RestResult.
    Raises KeyError if class_name cannot be resolved (unknown class).
    """
    class_name: str = profile.get("class", "")
    cls = get_class(class_name)   # KeyError on unknown class
    hit_die: int = cls.hit_die

    con_score: int = profile.get("ability_scores", {}).get("CON", 10)
    con_mod: int = ability_modifier(con_score)

    hit_dice_total: int = profile.get("hit_dice_total", 1)
    hit_dice_used: int = profile.get("hit_dice_used", 0)
    available: int = hit_dice_total - hit_dice_used

    # Clamp requested dice to what's actually available (never negative)
    dice_to_spend: int = max(0, min(hit_dice_spent, available))

    hp_current: int = profile.get("hp_current", 0)
    hp_max: int = profile.get("hp_max", 1)
    hp_deficit: int = hp_max - hp_current

    total_restored: int = 0
    roll_log: list[str] = []

    for _ in range(dice_to_spend):
        rolled = _roll_die(hit_die)
        gained = max(1, rolled + con_mod)
        # Do not over-restore beyond deficit
        actual_gain = min(gained, hp_deficit - total_restored)
        if actual_gain <= 0:
            # HP already at max — stop spending dice early
            dice_to_spend = _ + 0  # stop consuming dice (already used 0 here because actual_gain<=0)
            break
        total_restored += actual_gain
        roll_log.append(f"d{hit_die}={rolled}+CON({con_mod:+d})={gained}→+{actual_gain}")

    # Update profile
    profile["hp_current"] = hp_current + total_restored
    profile["hit_dice_used"] = hit_dice_used + dice_to_spend

    log_rolls = ", ".join(roll_log) if roll_log else "немає кидків"
    log = (
        f"Короткий відпочинок (60 хв): витрачено {dice_to_spend}/{hit_dice_total} HD, "
        f"відновлено {total_restored} HP ({log_rolls}). "
        f"HP: {hp_current} → {profile['hp_current']}/{hp_max}."
    )

    return RestResult(
        rest_type="short",
        hp_restored=total_restored,
        hit_dice_spent=dice_to_spend,
        hit_dice_regained=0,
        conditions_cleared=[],
        duration_minutes=_SHORT_REST_DURATION_MINUTES,
        log=log,
    )


# ---------------------------------------------------------------------------
# Long rest
# ---------------------------------------------------------------------------

def long_rest(profile: dict) -> RestResult:
    """Resolve an 8-hour long rest (D&D 5e PHB RAW).

    What happens:
    - HP fully restored to hp_max.
    - Hit dice regained: max(1, hit_dice_total // 2), capped at hit_dice_total.
      (hit_dice_used is reduced accordingly, floor 0.)
    - Temporary conditions cleared: see _LONG_REST_CLEARED_CONDITIONS.
      NOT cleared: petrified, invisible, unconscious.
    - profile['hit_dice_used'] is decremented by regained amount.

    Mutates profile in-place.
    Returns RestResult.

    Note: in-world time advance (minutes_passed) is the caller's responsibility.
    """
    hp_current: int = profile.get("hp_current", 0)
    hp_max: int = profile.get("hp_max", 1)

    # Full HP restoration
    hp_restored: int = hp_max - hp_current
    profile["hp_current"] = hp_max

    # Hit dice regain: half of total (minimum 1), cannot exceed total
    hit_dice_total: int = profile.get("hit_dice_total", 1)
    hit_dice_used: int = profile.get("hit_dice_used", 0)

    dice_to_regain: int = max(1, hit_dice_total // 2)
    # Cannot regain more than what's been spent
    actual_regained: int = min(dice_to_regain, hit_dice_used)
    profile["hit_dice_used"] = max(0, hit_dice_used - actual_regained)

    # Clear temporary conditions
    cleared: list[str] = []
    for cond_name in _LONG_REST_CLEARED_CONDITIONS:
        if has_condition(profile, cond_name):
            remove_condition(profile, cond_name)
            cleared.append(cond_name)

    log_cond = f", знято: {', '.join(cleared)}" if cleared else ""
    log = (
        f"Тривалий відпочинок (8 год): відновлено {hp_restored} HP "
        f"(HP → {hp_max}/{hp_max}), повернено {actual_regained} HD "
        f"(доступно: {hit_dice_total - profile['hit_dice_used']}/{hit_dice_total})"
        f"{log_cond}."
    )

    return RestResult(
        rest_type="long",
        hp_restored=hp_restored,
        hit_dice_spent=0,
        hit_dice_regained=actual_regained,
        conditions_cleared=cleared,
        duration_minutes=_LONG_REST_DURATION_MINUTES,
        log=log,
    )
