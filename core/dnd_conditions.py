"""D&D 5e PHB Appendix A conditions + GoT-custom 'bleeding' (Фаза 2)."""

import random
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CONDITION_NAMES: tuple[str, ...] = (
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
    "bleeding",  # GoT-custom: 1d4 dmg per round until removed (DC 12 Medicine, handled by caller)
)

# Short Ukrainian descriptions for UI display
_CONDITION_DESCRIPTIONS: dict[str, str] = {
    "blinded": "Осліплений: не бачить, атаки з перешкодою, атаки проти — з перевагою.",
    "charmed": "Зачарований: не може атакувати джерело, перевага на взаємодії.",
    "deafened": "Оглушений: не чує, провал перевірок слуху.",
    "frightened": "Переляканий: перешкода на атаки/перевірки, не може наближатись до джерела страху.",
    "grappled": "Захоплений: швидкість = 0.",
    "incapacitated": "Знепритомнілий: не може діяти.",
    "invisible": "Невидимий: атаки з перевагою, атаки проти — з перешкодою.",
    "paralyzed": "Паралізований: всі дії заблоковано, автопровал STR/DEX, удари впритул — автокрит.",
    "petrified": "Скам'янілий: всі дії заблоковано, стійкість до всіх пошкоджень.",
    "poisoned": "Отруєний: перешкода на атаки і перевірки.",
    "prone": "Лежачий: перешкода на атаки, рукопашні проти — з перевагою, дальні — з перешкодою.",
    "restrained": "Скований: швидкість = 0, перешкода на атаки і рятівні кидки DEX.",
    "stunned": "Приголомшений: всі дії заблоковано, автопровал STR/DEX.",
    "unconscious": "Непритомний: всі дії заблоковано, падає, удари впритул — автокрит.",
    "bleeding": "Кровотеча: 1d4 пошкоджень на початку кожного раунду до лікування (DC 12 Медицина).",
}


@dataclass
class Condition:
    """Serialisable condition attached to a combatant."""

    name: str               # one of CONDITION_NAMES
    duration_rounds: int    # -1 = until removed externally (e.g. petrified by a spell)
    source: str             # human-readable source description
    save_dc: int = 0        # DC for end-of-turn save to remove (0 = no repeating save)
    save_ability: str = ""  # ability key for repeating save, e.g. "CON"
    extra: dict = field(default_factory=dict)  # condition-specific auxiliary data


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _condition_to_dict(c: Condition) -> dict:
    """Serialise a Condition dataclass to a plain dict."""
    return {
        "name": c.name,
        "duration_rounds": c.duration_rounds,
        "source": c.source,
        "save_dc": c.save_dc,
        "save_ability": c.save_ability,
        "extra": c.extra,
    }


def _dict_to_condition(d: dict) -> Condition:
    """Deserialise a plain dict back to a Condition dataclass."""
    return Condition(
        name=d["name"],
        duration_rounds=d["duration_rounds"],
        source=d["source"],
        save_dc=d.get("save_dc", 0),
        save_ability=d.get("save_ability", ""),
        extra=d.get("extra", {}),
    )


def _ensure_conditions(target: dict) -> list[dict]:
    """Guarantee target['conditions'] exists and return it."""
    if "conditions" not in target:
        target["conditions"] = []
    return target["conditions"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_condition(
    target: dict,
    condition_name: str,
    duration_rounds: int,
    source: str,
    save_dc: int = 0,
    save_ability: str = "",
) -> None:
    """Add condition to target['conditions']. If same name already present, refresh to max(old, new) duration."""
    if condition_name not in CONDITION_NAMES:
        raise ValueError(f"Unknown condition: {condition_name!r}. Must be one of {CONDITION_NAMES}.")

    conditions = _ensure_conditions(target)

    for entry in conditions:
        if entry["name"] == condition_name:
            # Refresh: keep the longer duration (-1 wins over any positive)
            old_dur = entry["duration_rounds"]
            if old_dur == -1 or duration_rounds == -1:
                entry["duration_rounds"] = -1
            else:
                entry["duration_rounds"] = max(old_dur, duration_rounds)
            entry["source"] = source
            if save_dc:
                entry["save_dc"] = save_dc
            if save_ability:
                entry["save_ability"] = save_ability
            return

    new_cond = Condition(
        name=condition_name,
        duration_rounds=duration_rounds,
        source=source,
        save_dc=save_dc,
        save_ability=save_ability,
    )
    conditions.append(_condition_to_dict(new_cond))


def remove_condition(target: dict, condition_name: str) -> bool:
    """Remove condition from target by name. Returns True if removed, False if not present."""
    conditions = _ensure_conditions(target)
    before = len(conditions)
    target["conditions"] = [c for c in conditions if c["name"] != condition_name]
    return len(target["conditions"]) < before


def has_condition(target: dict, condition_name: str) -> bool:
    """Return True if target currently has the named condition."""
    conditions = _ensure_conditions(target)
    return any(c["name"] == condition_name for c in conditions)


def tick_conditions(target: dict) -> list[str]:
    """Advance one round for all conditions on target.

    - Decrement duration_rounds for timed conditions (skip -1 = indefinite).
    - Conditions reaching 0 are removed.
    - 'bleeding' inflicts 1d4 damage to target['hp_current'] (min 0); caller must
      propagate HP check (death / unconscious) — tick_conditions only modifies hp_current.
    Returns list of human-readable log lines describing what happened.
    """
    conditions = _ensure_conditions(target)
    log: list[str] = []
    target_name: str = target.get("name", target.get("Ім'я", "?"))

    # Bleeding damage first (before potential removal)
    for entry in conditions:
        if entry["name"] == "bleeding":
            bleed_dmg = random.randint(1, 4)
            old_hp = target.get("hp_current", 0)
            target["hp_current"] = max(0, old_hp - bleed_dmg)
            log.append(
                f"{target_name} отримує {bleed_dmg} пошкоджень від кровотечі "
                f"(HP: {old_hp} → {target['hp_current']})"
            )

    # Tick durations; collect expired
    expired: list[str] = []
    for entry in conditions:
        if entry["duration_rounds"] == -1:
            continue  # indefinite — never tick
        entry["duration_rounds"] -= 1
        if entry["duration_rounds"] <= 0:
            expired.append(entry["name"])

    # Remove expired
    for name in expired:
        target["conditions"] = [c for c in target["conditions"] if c["name"] != name]
        log.append(f"{target_name} більше не має умови «{name}»")

    return log


def condition_modifies(
    action_type: str,
    actor: dict,
    target: dict | None = None,
) -> dict:
    """Return combat modifiers based on D&D 5e PHB Appendix A condition rules.

    action_type: 'attack', 'move', 'save_str', 'save_dex', 'check', 'any'
    Returns: {
        'advantage': bool,
        'disadvantage': bool,
        'blocked': bool,
        'blocked_reason': str,
        'auto_crit': bool,   # true when target is paralyzed/unconscious and attacker is within 5ft
        'auto_fail_save': str | None,  # 'STR', 'DEX', or None
    }
    """
    result = {
        "advantage": False,
        "disadvantage": False,
        "blocked": False,
        "blocked_reason": "",
        "auto_crit": False,
        "auto_fail_save": None,
    }

    def actor_has(name: str) -> bool:
        return has_condition(actor, name)

    def target_has(name: str) -> bool:
        return target is not None and has_condition(target, name)

    # --- ACTOR conditions that block all actions ---
    if actor_has("incapacitated"):
        result["blocked"] = True
        result["blocked_reason"] = "Знепритомнілий: не може діяти."
        return result

    if actor_has("paralyzed"):
        result["blocked"] = True
        result["blocked_reason"] = "Паралізований: не може діяти."
        return result

    if actor_has("petrified"):
        result["blocked"] = True
        result["blocked_reason"] = "Скам'янілий: не може діяти."
        return result

    if actor_has("stunned"):
        result["blocked"] = True
        result["blocked_reason"] = "Приголомшений: не може діяти."
        return result

    if actor_has("unconscious"):
        result["blocked"] = True
        result["blocked_reason"] = "Непритомний: не може діяти."
        return result

    # --- MOVE action ---
    if action_type == "move":
        if actor_has("grappled"):
            result["blocked"] = True
            result["blocked_reason"] = "Захоплений: швидкість = 0."
            return result
        if actor_has("restrained"):
            result["blocked"] = True
            result["blocked_reason"] = "Скований: швидкість = 0."
            return result
        if actor_has("frightened"):
            # Cannot move closer to source of fear — we block the move conservatively
            result["blocked"] = True
            result["blocked_reason"] = "Переляканий: не може наближатись до джерела страху."
            return result
        return result

    # --- CHARMED: cannot attack charmer ---
    if action_type == "attack" and actor_has("charmed"):
        # We cannot check if target is charmer without more context; block conservatively
        # when charmer info is in extra. If caller wants precision, inspect actor['conditions']
        for entry in _ensure_conditions(actor):
            if entry["name"] == "charmed":
                charmer = entry.get("extra", {}).get("charmer")
                t_name = None
                if target is not None:
                    t_name = target.get("name", target.get("Ім'я"))
                if charmer and t_name and charmer == t_name:
                    result["blocked"] = True
                    result["blocked_reason"] = "Зачарований: не може атакувати того, хто зачарував."
                    return result

    # --- ATTACK action modifiers ---
    if action_type == "attack":
        # Actor penalties
        if actor_has("blinded"):
            result["disadvantage"] = True
        if actor_has("frightened"):
            result["disadvantage"] = True
        if actor_has("poisoned"):
            result["disadvantage"] = True
        if actor_has("restrained"):
            result["disadvantage"] = True
        if actor_has("prone"):
            result["disadvantage"] = True

        # Actor bonuses
        if actor_has("invisible"):
            result["advantage"] = True

        # Target-side modifiers (attacks against target)
        if target is not None:
            if target_has("blinded"):
                result["advantage"] = True
            if target_has("invisible"):
                result["disadvantage"] = True
            if target_has("prone"):
                # melee vs prone = advantage; ranged vs prone = disadvantage
                # action_type doesn't differentiate melee/ranged here — caller may refine
                # Default: treat as melee advantage (common case in tight combat)
                result["advantage"] = True
            if target_has("restrained"):
                result["advantage"] = True
            if target_has("paralyzed") or target_has("unconscious"):
                result["advantage"] = True
                result["auto_crit"] = True  # within 5 feet assumed in melee

        return result

    # --- CHECK / SAVE modifiers ---
    if action_type in ("check", "save_str", "save_dex", "any"):
        if actor_has("blinded"):
            result["disadvantage"] = True
        if actor_has("frightened"):
            result["disadvantage"] = True
        if actor_has("poisoned"):
            result["disadvantage"] = True
        if actor_has("restrained") and action_type == "save_dex":
            result["disadvantage"] = True

        # Auto-fail STR/DEX saves for paralyzed, stunned, unconscious
        if actor_has("paralyzed") or actor_has("stunned") or actor_has("unconscious"):
            if action_type in ("save_str", "save_dex"):
                result["auto_fail_save"] = action_type.replace("save_", "").upper()
                result["blocked"] = True
                result["blocked_reason"] = "Автопровал STR/DEX рятівного кидка."

    return result


def get_condition_description(condition_name: str) -> str:
    """Return short Ukrainian description of condition for UI display."""
    return _CONDITION_DESCRIPTIONS.get(condition_name, f"Невідома умова: {condition_name}")
