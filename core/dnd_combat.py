"""Combat FSM resolver for D&D 5e ASoIaF adaptation (Фаза 2 standalone).

Pure functions + dataclasses. No DB calls, no LLM calls, no engine.py imports.
All random calls go through core.dnd_core.roll_d20 or _roll_damage.
"""

import random
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from core.dnd_core import (
    CheckResult,
    ability_modifier,
    proficiency_bonus,
    roll_d20,
    saving_throw,
)
from core.dnd_conditions import (
    apply_condition,
    condition_modifies,
    has_condition,
    tick_conditions,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CombatPhase = Literal["ONGOING", "PLAYER_WIN", "PLAYER_DEAD", "PLAYER_FLED", "TIMEOUT"]
ActorKind = Literal["player", "npc"]

# XP awards by CR string per D&D DMG p.275
_CR_XP: dict[str, int] = {
    "0":    10,
    "1/8":  25,
    "1/4":  50,
    "1/2": 100,
    "1":   200,
    "2":   450,
    "3":   700,
    "4":  1100,
    "5":  1800,
    "6":  2300,
    "7":  2900,
    "8":  3900,
    "9":  5000,
    "10": 5900,
}

# Heritage names that grant cold immunity (Free Folk)
_COLD_IMMUNE_HERITAGES: frozenset[str] = frozenset({"Free Folk"})

# ---------------------------------------------------------------------------
# Tier-based NPC fallback table (used when canon statblock is absent)
# Mirrors D&D 5e Monster Manual archetypes scaled for ASoIaF tone.
# Each entry: (hp, ac, to_hit, attack_name, attack_dmg)
# ---------------------------------------------------------------------------
_NPC_TIER_DEFAULTS: dict[str, dict] = {
    "commoner": {
        "hp":       4,
        "ac":       10,
        "to_hit":   2,
        "attack":   {"name": "Кулак",              "to_hit": 2, "dmg": "1d4 bludgeoning",  "range": 5},
    },
    "guard": {
        "hp":       11,
        "ac":       16,
        "to_hit":   3,
        "attack":   {"name": "Спис",               "to_hit": 3, "dmg": "1d8+1 piercing",   "range": 5},
    },
    "sellsword": {
        "hp":       16,
        "ac":       14,
        "to_hit":   4,
        "attack":   {"name": "Меч",                "to_hit": 4, "dmg": "1d8+2 slashing",   "range": 5},
    },
    "veteran": {
        "hp":       58,
        "ac":       17,
        "to_hit":   5,
        "attack":   {"name": "Дволезовий меч",     "to_hit": 5, "dmg": "1d8+3 slashing",   "range": 5},
    },
}
_NPC_TIER_DEFAULT_KEY = "guard"  # used when tier cannot be resolved

# Relentless Endurance feature (Ironborn / half-orc equivalent)
_RELENTLESS_ENDURANCE_FEATURE = "Relentless Endurance"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CombatantRef:
    """Lightweight initiative-order entry."""

    kind: ActorKind   # "player" or "npc"
    name: str         # unique identifier (player name or NPC name)
    init_roll: int    # 1d20 + DEX_mod result


@dataclass
class AttackResult:
    """Full result of one attack action."""

    attacker: str
    target: str
    weapon: str
    to_hit_total: int       # d20 + STR/DEX + prof
    natural_roll: int       # raw d20
    target_ac: int
    hit: bool
    critical: bool          # nat 20 on attack roll
    damage_total: int       # final damage
    damage_dice_str: str    # e.g. "1d8+3 slashing"
    damage_type: str        # e.g. "slashing"
    log_line: str


@dataclass
class CombatState:
    """In-memory FSM state for one active combat encounter."""

    chat_id: int
    round: int                                   # starts at 1
    initiative_order: list[CombatantRef]
    current_actor_idx: int                       # index into initiative_order
    npcs: dict[str, dict]                        # {npc_name: npc_stub_dict snapshot}
    player_snapshot: dict                        # snapshot of player profile (read-only from DB source)
    log: list[str] = field(default_factory=list)
    round_limit: int = 10                        # forced TIMEOUT after this many rounds
    phase: CombatPhase = "ONGOING"

    # Track initial player HP to compute hp_change in cleanup
    _initial_player_hp: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._initial_player_hp = self.player_snapshot.get("hp_current", 0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_dex_mod(profile: dict) -> int:
    """Return DEX ability modifier for a combatant profile."""
    dex_score = profile.get("ability_scores", {}).get("DEX", 10)
    return ability_modifier(dex_score)


def _get_str_mod(profile: dict) -> int:
    """Return STR ability modifier."""
    str_score = profile.get("ability_scores", {}).get("STR", 10)
    return ability_modifier(str_score)


def _has_feature(profile: dict, feature_name: str) -> bool:
    """Check if profile has a named feature in its features list."""
    features = profile.get("features", [])
    return any(
        f.get("name") == feature_name if isinstance(f, dict) else f.name == feature_name
        for f in features
    )


def _get_heritage(profile: dict) -> str:
    """Return heritage string from profile, empty string if absent."""
    return profile.get("heritage", "")


def _is_fire_resistant(profile: dict) -> bool:
    """Return True if profile has fire resistance.

    Delegates to core.dnd_heritages.is_fire_resistant (shared source of truth).
    Kept as a private alias so existing call-sites within this module are unchanged.
    """
    from core.dnd_heritages import is_fire_resistant
    return is_fire_resistant(profile)


def _is_cold_immune(profile: dict) -> bool:
    """Return True if profile has Free Folk cold immunity (Phase 7 environment context)."""
    heritage = _get_heritage(profile)
    return heritage in _COLD_IMMUNE_HERITAGES


def _npc_is_alive(npc: dict) -> bool:
    """Return True if NPC is still capable of acting."""
    status = npc.get("Status", "Active")
    hp = npc.get("hp_current", 1)
    return status not in ("Dead", "Fled", "Unconscious") and hp > 0


def _guess_npc_tier(npc: dict) -> str:
    """Heuristically derive combat tier from NPC tags and Name.

    Returns one of: 'commoner', 'guard', 'sellsword', 'veteran'.
    Default (unrecognised tag + unrecognised name) → 'guard'.
    Tags and Name are checked together in each tier; the first tier whose
    tag-or-name pattern matches wins, in order: veteran, sellsword, guard, commoner.
    """
    tags: list[str] = [t.lower() for t in npc.get("tags", [])]
    name: str = npc.get("Name", npc.get("name", "")).lower()

    # veteran / knight — check first (most specific)
    if any(t in tags for t in ("veteran", "knight")) or "лицар" in name:
        return "veteran"

    # sellsword / mercenary / bandit
    if (
        any(t in tags for t in ("sellsword", "mercenary"))
        or any(w in name for w in ("найман", "бандит"))
    ):
        return "sellsword"

    # guard / watch / captain
    if (
        any(t in tags for t in ("guard", "watch"))
        or any(w in name for w in ("варта", "стражник", "капітан"))
    ):
        return "guard"

    # commoner — check last so specific roles aren't mistakenly demoted
    if (
        "commoner" in tags
        or any(w in name for w in ("слуга", "торговець", "селянин"))
    ):
        return "commoner"

    return _NPC_TIER_DEFAULT_KEY


# ---------------------------------------------------------------------------
# C2 / C3: Proficiency helpers
# ---------------------------------------------------------------------------

# Weapon category map: Ukrainian and English weapon names → "simple" or "martial".
# Used by is_proficient_with_weapon for substring lookup. First match wins.
# Keys are lowercase; lookup uses str.lower() on the weapon name.
_WEAPON_CATEGORIES: dict[str, str] = {
    # simple weapons
    "дубинка": "simple",
    "club": "simple",
    "спис": "simple",
    "spear": "simple",
    "палиця": "simple",
    "staff": "simple",
    "кинджал": "simple",
    "dagger": "simple",
    "праща": "simple",
    "sling": "simple",
    "булава": "simple",
    "mace": "simple",
    "ціп": "simple",
    "flail": "simple",
    # martial weapons
    "довгий меч": "martial",
    "longsword": "martial",
    "бойова сокира": "martial",
    "battleaxe": "martial",
    "велика сокира": "martial",
    "greataxe": "martial",
    "великий меч": "martial",
    "greatsword": "martial",
    "бойовий молот": "martial",
    "warhammer": "martial",
    "рапіра": "martial",
    "rapier": "martial",
    "арбалет": "martial",
    "crossbow": "martial",
    "лук": "martial",
    "bow": "martial",
    "короткий меч": "martial",
    "shortsword": "martial",
    "шабля": "martial",
    "scimitar": "martial",
}

# Ukrainian weapon name → canonical English stem.  Lets weapons that arrive
# with Ukrainian names (e.g. "Довгий меч") match class-specific English
# proficiency entries (e.g. "longswords") for Spy/Courtier.
_UA_TO_EN_STEMS: dict[str, str] = {
    "рапіра":       "rapier",
    "довгий меч":   "longsword",
    "короткий меч": "shortsword",
    "кинджал":      "dagger",
    "арбалет":      "crossbow",
    "лук":          "bow",
    "булава":       "mace",
    "спис":         "spear",
    "шабля":        "scimitar",
}


def is_proficient_with_weapon(class_name: str, weapon_name: str) -> bool:
    """Return True if the given class is proficient with the given weapon.

    Logic:
    - Read weapon_profs from GOT_CLASSES[class_name].
    - If profs contains "martial" → proficient with all weapons (True).
    - If profs contains "simple" but not "martial" → proficient only with
      simple weapons (look up category via _WEAPON_CATEGORIES substring match).
    - Profs may also contain specific weapon names (e.g. "rapiers", "longswords")
      for classes like Spy and Courtier — check those too.
    - Unknown class or unknown weapon → True (fail-safe; never penalize missing data).

    Args:
        class_name: character's class name (e.g. "Knight").
        weapon_name: weapon identifier string (e.g. "Довгий меч", "longsword").

    Returns:
        bool — True if proficient or data insufficient to determine; False only
        when we have positive evidence of non-proficiency.
    """
    from core.dnd_classes import GOT_CLASSES

    if not class_name or not weapon_name:
        return True  # fail-safe

    cls = GOT_CLASSES.get(class_name)
    if cls is None:
        return True  # unknown class → assume proficient

    weapon_profs: list[str] = cls.weapon_profs  # e.g. ["simple", "martial"]
    name_lower = weapon_name.lower().strip()

    # Unarmed strike — always proficient
    if not name_lower or "unarmed" in name_lower or "безозброй" in name_lower:
        return True

    # "martial" in profs → proficient with everything
    if "martial" in weapon_profs:
        return True

    # Check specific weapon names listed in profs (e.g. "rapiers", "longswords",
    # "hand crossbows").  Match bidirectionally; also try stripping trailing 's'
    # (English plurals like "rapiers" → "rapier") to handle Ukrainian weapon names
    # like "рапіра" that won't contain the English plural as a substring.
    # Additionally check _WEAPON_CATEGORIES: if the weapon maps to a category key
    # that shares a stem with a specific prof entry, treat as proficient.
    for specific in weapon_profs:
        if specific in ("simple", "martial"):
            continue  # handled separately
        spec_lower = specific.lower()
        spec_stem = spec_lower.rstrip("s")  # strip English plural suffix
        if spec_lower in name_lower or name_lower in spec_lower:
            return True
        if spec_stem and (spec_stem in name_lower or name_lower in spec_stem):
            return True
    # Cross-check: map Ukrainian weapon names → canonical English stems so they
    # can be matched against English-named specific proficiency entries like
    # "rapiers", "longswords", "shortswords", "hand crossbows".
    en_stem: str | None = None
    for ua_key, en_val in _UA_TO_EN_STEMS.items():
        if ua_key in name_lower:
            en_stem = en_val
            break
    if en_stem is not None:
        for specific in weapon_profs:
            if specific in ("simple", "martial"):
                continue
            spec_lower = specific.lower().rstrip("s")
            if en_stem == spec_lower or en_stem in spec_lower or spec_lower in en_stem:
                return True

    # Determine weapon category from _WEAPON_CATEGORIES (substring match)
    weapon_cat: str | None = None
    for key, cat in _WEAPON_CATEGORIES.items():
        if key in name_lower:
            weapon_cat = cat
            break

    if weapon_cat is None:
        # Unknown weapon → fail-safe: assume proficient
        return True

    # "simple" in profs and weapon is simple → proficient
    if "simple" in weapon_profs and weapon_cat == "simple":
        return True

    # "simple" in profs but weapon is martial → NOT proficient
    return False


def is_proficient_with_armor(class_name: str, armor_type: str) -> bool:
    """Return True if the given class is proficient with armor of the given type.

    armor_type: "light", "medium", or "heavy" (from equipped_armor["type"]).
    "shields" is treated as a separate proficiency (ignored here — we only check
    armor for the attack-roll disadvantage rule; shield doesn't cause disadvantage).

    Logic:
    - Read armor_profs from GOT_CLASSES[class_name].
    - If profs is empty (e.g. Maester) → not proficient with any armor (False for
      any non-empty armor_type).
    - Unknown class or unknown/empty armor_type → True (fail-safe).

    Returns:
        bool — True if proficient or data insufficient; False only on positive
        evidence of non-proficiency.
    """
    from core.dnd_classes import GOT_CLASSES

    if not class_name or not armor_type:
        return True  # fail-safe

    cls = GOT_CLASSES.get(class_name)
    if cls is None:
        return True  # unknown class → assume proficient

    armor_profs: list[str] = cls.armor_profs  # e.g. ["light", "medium", "heavy", "shields"]
    armor_lower = armor_type.lower().strip()

    if not armor_lower:
        return True  # no armor equipped → irrelevant

    # Heavy armor proficiency implies medium and light too (per D&D tiers)
    if armor_lower == "light":
        return "light" in armor_profs or "medium" in armor_profs or "heavy" in armor_profs
    if armor_lower == "medium":
        return "medium" in armor_profs or "heavy" in armor_profs
    if armor_lower == "heavy":
        return "heavy" in armor_profs

    # Unknown type → fail-safe
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def roll_initiative(combatant_dict: dict) -> int:
    """Roll 1d20 + DEX modifier for initiative. Returns total (no advantage)."""
    natural, _ = roll_d20()
    dex_mod = _get_dex_mod(combatant_dict)
    return natural + dex_mod


def initiate_combat(
    player_profile: dict,
    npc_list: list[dict],
    chat_id: int,
) -> CombatState:
    """Create initial CombatState. Rolls initiative for all, sorts descending. Ties → NPC first.

    Snapshots each NPC: ensures hp_current, ac, conditions=[] are present.
    The player_snapshot is a shallow copy of the profile dict (caller should pass a copy
    if they want isolation; engine.py is responsible for not mutating the DB source).
    """
    import copy

    player_name: str = player_profile.get("Ім'я", player_profile.get("name", "Player"))
    player_init = roll_initiative(player_profile)

    combatants: list[CombatantRef] = [
        CombatantRef(kind="player", name=player_name, init_roll=player_init)
    ]

    npc_snapshots: dict[str, dict] = {}

    for npc in npc_list:
        npc_name: str = npc.get("Name", npc.get("name", "Unknown NPC"))
        npc_init = roll_initiative(npc)
        combatants.append(CombatantRef(kind="npc", name=npc_name, init_roll=npc_init))

        snapshot = copy.deepcopy(npc)
        # Guarantee required combat fields; use tier-based fallback for non-canon NPCs.
        _tier = _guess_npc_tier(snapshot)
        _tier_data = _NPC_TIER_DEFAULTS[_tier]
        if "hp_max" not in snapshot:
            snapshot["hp_max"] = _tier_data["hp"]
        if "hp_current" not in snapshot:
            snapshot["hp_current"] = snapshot["hp_max"]
        if "ac" not in snapshot:
            snapshot["ac"] = _tier_data["ac"]
        if "conditions" not in snapshot:
            snapshot["conditions"] = []
        if "Status" not in snapshot:
            snapshot["Status"] = "Active"
        npc_snapshots[npc_name] = snapshot

    # Sort descending; equal initiative → NPC first (index 0 = first to act)
    # "NPC first" means player is at a disadvantage (tense opening)
    combatants.sort(key=lambda c: (c.init_roll, 0 if c.kind == "npc" else -1), reverse=True)

    player_snap = copy.deepcopy(player_profile)
    if "conditions" not in player_snap:
        player_snap["conditions"] = []

    log_line = (
        f"Бій розпочато! Ініціатива: "
        + ", ".join(f"{c.name}({c.init_roll})" for c in combatants)
    )

    return CombatState(
        chat_id=chat_id,
        round=1,
        initiative_order=combatants,
        current_actor_idx=0,
        npcs=npc_snapshots,
        player_snapshot=player_snap,
        log=[log_line],
    )


def parse_damage_dice(dice_str: str) -> tuple[int, int, int, str]:
    """Parse a weapon/ability damage string into (num_dice, die_size, modifier, dmg_type).

    Supported formats:
      '1d8+3 slashing'  → (1, 8, 3, 'slashing')
      '2d6 piercing'    → (2, 6, 0, 'piercing')
      '1d4-1 bludgeoning' → (1, 4, -1, 'bludgeoning')
      'spell:burning_hands' → (2, 6, 0, 'fire')  # safe default
      '1d6'             → (1, 6, 0, 'slashing')  # missing type defaults to slashing
    """
    if not dice_str:
        return (1, 4, 0, "bludgeoning")

    # spell: prefix → safe default
    if dice_str.strip().lower().startswith("spell:"):
        return (2, 6, 0, "fire")

    # Normalise whitespace
    s = dice_str.strip()

    # Regex: optional leading spaces, NdS, optional ±M, optional type
    pattern = re.compile(
        r"(\d+)d(\d+)"             # NdS
        r"([+-]\d+)?"              # optional modifier
        r"(?:\s+([a-zA-Z_]+))?",   # optional damage type word
        re.IGNORECASE,
    )
    m = pattern.search(s)
    if not m:
        # Fallback: flat integer damage
        flat = re.search(r"(\d+)", s)
        num = int(flat.group(1)) if flat else 1
        # Find type word
        type_m = re.search(r"([a-zA-Z_]+)", s)
        dmg_type = type_m.group(1).lower() if type_m else "bludgeoning"
        return (0, 0, num, dmg_type)

    num_dice = int(m.group(1))
    die_size = int(m.group(2))
    modifier = int(m.group(3)) if m.group(3) else 0
    dmg_type = m.group(4).lower() if m.group(4) else "slashing"

    return (num_dice, die_size, modifier, dmg_type)


def _roll_damage(
    num_dice: int,
    die_size: int,
    modifier: int,
    critical: bool = False,
    reroll_1_2: bool = False,
) -> int:
    """Roll damage dice. Critical: roll dice twice (D&D 5e crit rule). Minimum 1.

    reroll_1_2 (B2d Great Weapon Fighting): when True, any die showing 1 or 2
    after the initial roll is rerolled ONCE and the new value is taken regardless
    (even if lower — per PHB). Applies to all dice in the pool (doubled on crit too).
    reroll_1_2 does NOT apply to the flat-damage fallback path (die_size == 0).
    """
    if die_size == 0:
        # Flat damage (parse_damage_dice fallback path)
        total = max(1, num_dice + modifier)
        return total

    dice_count = num_dice * 2 if critical else num_dice

    rolls: list[int] = []
    for _ in range(dice_count):
        roll = random.randint(1, die_size)
        if reroll_1_2 and roll <= 2:
            roll = random.randint(1, die_size)  # reroll once; take new value per PHB
        rolls.append(roll)

    total = sum(rolls) + modifier
    return max(1, total)


def apply_damage(
    state: CombatState,
    target_name: str,
    damage: int,
    damage_type: str = "slashing",
) -> str:
    """Apply damage to player snapshot or an NPC snapshot; handle heritage resistances.

    Heritage rules (Phase 2):
    - Valyrian Descent: fire damage halved.
    - Free Folk cold immunity: cold damage reduced to 0 (Phase 7 environment context —
      this is the only heritage effect fully resolved here because it's zero-cost).

    Returns a human-readable log line.
    """
    player_name: str = state.player_snapshot.get(
        "Ім'я", state.player_snapshot.get("name", "Player")
    )

    if target_name == "player" or target_name == player_name:
        profile = state.player_snapshot
        display = player_name
    else:
        profile = state.npcs.get(target_name)
        if profile is None:
            return f"[apply_damage] ціль не знайдена: {target_name!r}"
        display = target_name

    # Heritage resistances / immunities
    actual_damage = damage
    resistance_note = ""

    if damage_type == "fire" and _is_fire_resistant(profile):
        actual_damage = max(1, damage // 2)
        resistance_note = " (вогнестійкість Валірійця: пошкодження вдвічі менше)"

    if damage_type == "cold" and _is_cold_immune(profile):
        actual_damage = 0
        resistance_note = " (Вільний Народ: імунітет до холоду)"

    # Petrified: resistance to ALL damage
    if has_condition(profile, "petrified"):
        actual_damage = max(0, actual_damage // 2)
        resistance_note += " (Скам'янілий: стійкість до всіх пошкоджень)"

    old_hp: int = profile.get("hp_current", 0)
    new_hp = max(0, old_hp - actual_damage)
    profile["hp_current"] = new_hp

    log = (
        f"{display} отримує {actual_damage} {damage_type} пошкоджень"
        f"{resistance_note} (HP: {old_hp} → {new_hp})"
    )

    # Check death / unconscious
    if new_hp <= 0:
        is_player = (target_name == "player" or target_name == player_name)

        if is_player:
            state.phase = "PLAYER_DEAD"
            log += " — ГРАВЕЦЬ МЕРТВИЙ!"
        else:
            npc = state.npcs[target_name]
            # Relentless Endurance: stay at 1 HP once per encounter
            if (
                npc.get("Status") == "Active"
                and _has_feature(npc, _RELENTLESS_ENDURANCE_FEATURE)
                and not npc.get("_relentless_used", False)
            ):
                npc["hp_current"] = 1
                npc["_relentless_used"] = True
                log += " — Relentless Endurance! Залишається при 1 HP."
            else:
                npc["Status"] = "Dead"
                log += f" — {target_name} вбито!"

    return log


def player_attack(
    state: CombatState,
    target_name: str,
    weapon_name: str,
    advantage: bool = False,
    disadvantage: bool = False,
) -> AttackResult:
    """Resolve player attack against a named NPC target.

    Reads weapon string from state.player_snapshot['equipment']['weapon_main'].
    Allows explicit advantage/disadvantage or derives from actor conditions.
    Critical hit (nat 20) doubles damage dice.
    Updates state.npcs[target_name].hp_current on hit.

    Safety invariant: target_name must NEVER equal the player's own name or the
    literal string "player" — the player cannot attack themselves.  The caller
    (dnd_combat_engine.execute_combat_round) already sanitizes target_npc before
    reaching here, but this guard is an additional last-resort defensive check so
    that a broken call-site cannot silently damage the player's HP.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    player = state.player_snapshot
    player_name: str = player.get("Ім'я", player.get("name", "Player"))

    # --- Defensive self-targeting guard (Issue 2) ---
    # target_name must not resolve to the player themselves.  If it does, return
    # a blocked miss and log an ERROR so the caller can diagnose the routing bug.
    _self_refs = {"player", "гравець", "я", "me", "self", player_name.lower()}
    if str(target_name).strip().lower() in _self_refs:
        _logger.error(
            f"[player_attack] SELF-TARGETING BLOCKED: target_name={target_name!r} "
            f"resolves to player {player_name!r}.  This is a routing bug in the caller."
        )
        return AttackResult(
            attacker=player_name, target=target_name, weapon=weapon_name,
            to_hit_total=0, natural_roll=0, target_ac=0,
            hit=False, critical=False, damage_total=0,
            damage_dice_str="", damage_type="",
            log_line=(
                f"[player_attack] ЗАБЛОКОВАНО: гравець не може атакувати себе "
                f"(target={target_name!r}). Помилка маршрутизації."
            ),
        )

    npc = state.npcs.get(target_name)
    if npc is None:
        # Return a miss result with error note
        return AttackResult(
            attacker=player_name, target=target_name, weapon=weapon_name,
            to_hit_total=0, natural_roll=0, target_ac=0,
            hit=False, critical=False, damage_total=0,
            damage_dice_str="", damage_type="",
            log_line=f"[player_attack] NPC не знайдено: {target_name!r}",
        )

    if not _npc_is_alive(npc):
        return AttackResult(
            attacker=player_name, target=target_name, weapon=weapon_name,
            to_hit_total=0, natural_roll=0, target_ac=npc.get("ac", 10),
            hit=False, critical=False, damage_total=0,
            damage_dice_str="", damage_type="",
            log_line=f"{target_name} вже мертвий/непритомний.",
        )

    # Derive condition modifiers
    cond_info = condition_modifies("attack", actor=player, target=npc)
    if cond_info["blocked"]:
        return AttackResult(
            attacker=player_name, target=target_name, weapon=weapon_name,
            to_hit_total=0, natural_roll=0, target_ac=npc.get("ac", 10),
            hit=False, critical=False, damage_total=0,
            damage_dice_str="", damage_type="",
            log_line=f"Атака заблокована: {cond_info['blocked_reason']}",
        )

    final_adv = advantage or cond_info["advantage"]
    final_dis = disadvantage or cond_info["disadvantage"]

    # ---------------------------------------------------------------------------
    # C3: Armor proficiency — non-proficient armor imposes disadvantage on attacks.
    # D&D 5e PHB: wearing armor you're not proficient with gives disadvantage on
    # STR/DEX-based attack rolls. Check BEFORE further advantage/disadvantage math.
    # ---------------------------------------------------------------------------
    _player_class = player.get("class", "")
    _equipped_armor = player.get("equipped_armor") or {}
    if _equipped_armor:
        _armor_type = _equipped_armor.get("type", "")
        if _armor_type and not is_proficient_with_armor(_player_class, _armor_type):
            final_dis = True

    # ---------------------------------------------------------------------------
    # C1: Dodge enforcement — if target NPC took the Dodge action this round,
    # all attacks against them have disadvantage (PHB p.192).
    # ---------------------------------------------------------------------------
    if npc.get("_dodge_this_round", False):
        final_dis = True

    # ---------------------------------------------------------------------------
    # A1: Weapon source — primary: equipped_weapon (structured dict); fallback
    # chain: equipment["weapon_main"] → profile["Зброя"] → Unarmed.
    # ---------------------------------------------------------------------------
    _equipped_weapon = player.get("equipped_weapon")
    _use_structured = _equipped_weapon and isinstance(_equipped_weapon, dict)

    if _use_structured:
        # Primary path: structured dict from _build_deterministic_dnd_profile /
        # generate_initial_stats (new profiles).
        damage_dice = _equipped_weapon.get("damage_dice", "1d4")
        dmg_type = str(_equipped_weapon.get("damage_type", "bludgeoning")).strip().lower()
        properties: list = _equipped_weapon.get("properties", [])
        # weapon_str is still needed for the log display name and for fallback to
        # parse_damage_dice on the legacy branches below.  Compose a minimal string
        # from the structured data so existing logging code stays unchanged.
        weapon_str = (
            f"{_equipped_weapon.get('name', 'Зброя')} "
            f"({damage_dice} {dmg_type})"
        )
        num_dice, die_size, dmg_mod_str, _ = parse_damage_dice(f"{damage_dice} {dmg_type}")
        # dmg_mod_str: modifier embedded in dice string (almost always 0 for named
        # weapons; kept for correctness if someone stores "1d8+1 slashing").
        dmg_mod = dmg_mod_str
    else:
        # Fallback chain (legacy profiles): equipment["weapon_main"] → "Зброя" → Unarmed.
        equipment = player.get("equipment", {})
        weapon_str = (
            equipment.get("weapon_main")
            or player.get("Зброя")
            or "Unarmed (1d4 bludgeoning)"
        )
        if weapon_name and weapon_name.lower() not in weapon_str.lower():
            # Caller specified a weapon by name that doesn't match main — use as-is.
            weapon_str = weapon_name
        num_dice, die_size, dmg_mod, dmg_type = parse_damage_dice(weapon_str)
        properties = []

    # ---------------------------------------------------------------------------
    # Finesse / ranged / thrown — determines attack_mod AND ability_dmg_mod (A2).
    # For structured path: use equipped_weapon["properties"] directly.
    # For legacy path: keyword detection (preserved from original code).
    # ---------------------------------------------------------------------------
    if _use_structured:
        _is_finesse = "finesse" in properties
        _is_ranged  = "ranged"  in properties
        _is_thrown  = "thrown"  in properties
    else:
        _finesse_keywords = ("dagger", "shortsword", "rapier", "кинджал", "рапіра", "короткий меч")
        _is_finesse = any(kw in weapon_str.lower() for kw in _finesse_keywords)
        _is_ranged  = any(kw in weapon_str.lower() for kw in ("bow", "crossbow", "лук", "арбалет"))
        _is_thrown  = False  # legacy strings don't encode thrown reliably

    str_mod = _get_str_mod(player)
    dex_mod = _get_dex_mod(player)

    # attack_mod: which ability drives the to-hit roll.
    if _is_finesse:
        attack_mod = max(str_mod, dex_mod)
    elif _is_ranged:
        attack_mod = dex_mod
    else:
        attack_mod = str_mod

    # ---------------------------------------------------------------------------
    # A2: ability_dmg_mod — the ability modifier applied to damage rolls.
    # D&D 5e PHB: melee → STR; finesse → max(STR, DEX); ranged → DEX;
    # thrown (not finesse) → STR.
    # ---------------------------------------------------------------------------
    if _is_finesse:
        ability_dmg_mod = max(str_mod, dex_mod)
    elif _is_ranged:
        ability_dmg_mod = dex_mod
    else:
        # melee or thrown (non-finesse)
        ability_dmg_mod = str_mod

    # ---------------------------------------------------------------------------
    # B2c / B2d: Fighting Style bonuses applied after A2 ability_dmg_mod is set.
    # Fail-safe: absent / None fighting_style → no bonus (backward compatible with
    # profiles created before this feature, and with non-martial classes).
    # ---------------------------------------------------------------------------
    _fighting_style: str | None = player.get("fighting_style")
    _reroll_1_2: bool = False  # B2d Great Weapon Fighting flag for _roll_damage

    if _fighting_style == "Dueling":
        # B2c: +2 damage when wielding a one-handed weapon with no off-hand weapon.
        # Two-handed condition: weapon has "two-handed" property, OR weapon is
        # "versatile" AND player is using it two-handed (no shield).
        # Per spec: versatile used two-handed DISQUALIFIES Dueling.
        _has_off_hand = False  # dual-wielding not implemented; treat as no off-hand weapon
        _is_two_handed_style = "two-handed" in properties or (
            "versatile" in properties and not player.get("equipped_shield", False)
        )
        if not _is_two_handed_style and not _has_off_hand:
            ability_dmg_mod += 2

    elif _fighting_style == "Great Weapon":
        # B2d: reroll 1s and 2s on damage dice (once) when wielding a two-handed weapon.
        # Triggers for: "two-handed" in properties, OR "versatile" in properties AND no shield
        # (player is gripping with both hands, forgoing the one-handed option).
        _is_two_handed_gwf = "two-handed" in properties or (
            "versatile" in properties and not player.get("equipped_shield", False)
        )
        if _is_two_handed_gwf:
            _reroll_1_2 = True

    level = player.get("level", 1)
    # ---------------------------------------------------------------------------
    # C2: Weapon proficiency — only add proficiency bonus when the character is
    # proficient with the weapon (D&D 5e PHB). Unknown weapons → assume proficient
    # (fail-safe: don't penalize missing data). _player_class already set above (C3).
    # ---------------------------------------------------------------------------
    _weapon_for_prof = _equipped_weapon.get("name", "") if _use_structured else weapon_str
    if is_proficient_with_weapon(_player_class, _weapon_for_prof):
        prof = proficiency_bonus(level)
    else:
        prof = 0
    target_ac = npc.get("ac", 10)

    natural, rolls = roll_d20(final_adv, final_dis)
    to_hit_total = natural + attack_mod + prof
    critical = (natural == 20)
    hit = critical or (natural != 1 and to_hit_total >= target_ac)

    # Auto-crit if target is paralyzed/unconscious (within 5ft implied in melee)
    if not critical and cond_info.get("auto_crit") and hit:
        critical = True

    # ---------------------------------------------------------------------------
    # A3 (versatile check): if weapon is versatile (optionally two-handed)
    # and player has no shield, use the larger versatile die (two-handed grip).
    # ---------------------------------------------------------------------------
    if _use_structured:
        damage_dice_to_use = damage_dice
        if "versatile" in properties and not player.get("equipped_shield", False):
            damage_dice_to_use = _equipped_weapon.get("damage_dice_versatile", damage_dice)
        if damage_dice_to_use != damage_dice:
            # Re-parse the larger die for num_dice / die_size.
            num_dice, die_size, dmg_mod_alt, _ = parse_damage_dice(
                f"{damage_dice_to_use} {dmg_type}"
            )
            dmg_mod = dmg_mod_alt  # keep in sync (normally 0 for standard weapons)
    # (for legacy path, damage_dice_to_use is already encoded in weapon_str)

    damage_total = 0
    damage_dice_str = weapon_str.split("(")[-1].rstrip(")") if "(" in weapon_str else weapon_str

    if hit:
        # Roll damage dice + string-embedded modifier (dmg_mod from parse_damage_dice).
        # D&D 5e crit: only dice are doubled, neither modifier is doubled.
        # B2d: pass _reroll_1_2 for Great Weapon Fighting (reroll 1s/2s once, per PHB).
        raw_dice_dmg = _roll_damage(num_dice, die_size, 0, critical, reroll_1_2=_reroll_1_2)  # dice only, no mod
        damage_total = max(1, raw_dice_dmg + dmg_mod + ability_dmg_mod)
        dmg_log = apply_damage(state, target_name, damage_total, dmg_type)
    else:
        dmg_log = ""

    # Build log line
    rolls_str = f"[{','.join(str(r) for r in rolls)}]→{natural}"
    hit_word = "КРИТ! " if critical else ("ВЛУЧАННЯ" if hit else "ПРОМАХ")
    log_line = (
        f"{player_name} атакує {target_name} зброєю {weapon_str.split('(')[0].strip()}: "
        f"{rolls_str} +{attack_mod}(атак) +{prof}(проф) = {to_hit_total} проти КЗ {target_ac} "
        f"→ {hit_word}"
    )
    if hit:
        log_line += f", {damage_total} {dmg_type}. {dmg_log}"

    return AttackResult(
        attacker=player_name,
        target=target_name,
        weapon=weapon_str,
        to_hit_total=to_hit_total,
        natural_roll=natural,
        target_ac=target_ac,
        hit=hit,
        critical=critical,
        damage_total=damage_total,
        damage_dice_str=damage_dice_str,
        damage_type=dmg_type,
        log_line=log_line,
    )


def npc_decide_action(
    state: CombatState,
    npc_id: str,
    llm_client=None,  # Phase 7 placeholder
) -> dict:
    """Heuristic NPC decision. Returns {action, target, reason}.

    Rules (Phase 2, no LLM):
    - HP < 25% of hp_max → attempt flee
    - Multiple targets: prefer player
    - Otherwise: attack player
    llm_client is ignored in Phase 2.
    """
    npc = state.npcs.get(npc_id)
    if npc is None or not _npc_is_alive(npc):
        return {"action": "skip", "target": None, "reason": "NPC відсутній або мертвий."}

    hp_current = npc.get("hp_current", 1)
    hp_max = npc.get("hp_max", hp_current)

    if hp_max > 0 and hp_current / hp_max < 0.25:
        return {
            "action": "flee",
            "target": None,
            "reason": f"{npc_id} рятується втечею (HP {hp_current}/{hp_max} < 25%).",
        }

    return {
        "action": "attack",
        "target": "player",
        "reason": f"{npc_id} атакує гравця.",
    }


def npc_attack(
    state: CombatState,
    npc_id: str,
    target: str = "player",
) -> AttackResult:
    """Resolve NPC attack using npc['attacks'][0]. Updates target HP in state.

    attack_dict expected format: {'name': str, 'to_hit': int, 'dmg': str, 'range': int}
    """
    npc = state.npcs.get(npc_id)
    if npc is None or not _npc_is_alive(npc):
        return AttackResult(
            attacker=npc_id, target=target, weapon="none",
            to_hit_total=0, natural_roll=0, target_ac=0,
            hit=False, critical=False, damage_total=0,
            damage_dice_str="", damage_type="",
            log_line=f"{npc_id}: відсутній або мертвий, пропускає атаку.",
        )

    attacks = npc.get("attacks", [])
    if not attacks:
        # Fallback: tier-appropriate weapon instead of flat unarmed +0 / 1d4
        _tier_key = _guess_npc_tier(npc)
        attack_data = dict(_NPC_TIER_DEFAULTS[_tier_key]["attack"])
    else:
        attack_data = attacks[0]

    weapon_name: str = attack_data.get("name", "Unknown")
    to_hit_bonus: int = attack_data.get("to_hit", 0)
    dmg_str: str = attack_data.get("dmg", "1d4 bludgeoning")

    # Condition modifiers on NPC as actor
    cond_info = condition_modifies("attack", actor=npc)
    if cond_info["blocked"]:
        return AttackResult(
            attacker=npc_id, target=target, weapon=weapon_name,
            to_hit_total=0, natural_roll=0, target_ac=0,
            hit=False, critical=False, damage_total=0,
            damage_dice_str=dmg_str, damage_type="",
            log_line=f"{npc_id} не може атакувати: {cond_info['blocked_reason']}",
        )

    # Determine target profile for AC
    player_name: str = state.player_snapshot.get(
        "Ім'я", state.player_snapshot.get("name", "Player")
    )
    if target == "player" or target == player_name:
        target_profile = state.player_snapshot
        display_target = player_name
    else:
        target_profile = state.npcs.get(target, {})
        display_target = target

    target_ac = target_profile.get("ac", 10)

    # Condition modifiers on target (attacks vs target)
    target_cond = condition_modifies("attack", actor=npc, target=target_profile)
    final_adv = cond_info["advantage"] or target_cond["advantage"]
    final_dis = cond_info["disadvantage"] or target_cond["disadvantage"]

    # C1: Dodge enforcement — if the target (player) took the Dodge action this
    # round, NPC attacks against them have disadvantage (PHB p.192).
    if target == "player" or target == player_name:
        if state.player_snapshot.get("_dodge_this_round", False):
            final_dis = True

    natural, rolls = roll_d20(final_adv, final_dis)
    to_hit_total = natural + to_hit_bonus
    critical = (natural == 20)
    hit = critical or (natural != 1 and to_hit_total >= target_ac)

    if not critical and target_cond.get("auto_crit") and hit:
        critical = True

    num_dice, die_size, dmg_mod, dmg_type = parse_damage_dice(dmg_str)
    damage_total = 0

    if hit:
        damage_total = _roll_damage(num_dice, die_size, dmg_mod, critical)
        dmg_log = apply_damage(state, target, damage_total, dmg_type)
    else:
        dmg_log = ""

    rolls_str = f"[{','.join(str(r) for r in rolls)}]→{natural}"
    hit_word = "КРИТ! " if critical else ("ВЛУЧАННЯ" if hit else "ПРОМАХ")
    log_line = (
        f"{npc_id} атакує {display_target} ({weapon_name}): "
        f"{rolls_str} +{to_hit_bonus} = {to_hit_total} проти КЗ {target_ac} → {hit_word}"
    )
    if hit:
        log_line += f", {damage_total} {dmg_type}. {dmg_log}"

    return AttackResult(
        attacker=npc_id,
        target=target,
        weapon=weapon_name,
        to_hit_total=to_hit_total,
        natural_roll=natural,
        target_ac=target_ac,
        hit=hit,
        critical=critical,
        damage_total=damage_total,
        damage_dice_str=dmg_str,
        damage_type=dmg_type,
        log_line=log_line,
    )


def end_round(state: CombatState) -> CombatPhase:
    """Finalise current round: tick conditions, check phase transitions, reset actor index.

    Call once after all combatants in initiative_order have taken their turn.
    Returns the updated phase.
    """
    # Tick conditions on player
    player_logs = tick_conditions(state.player_snapshot)
    state.log.extend(player_logs)

    # Tick conditions on living NPCs
    for npc_name, npc in state.npcs.items():
        if _npc_is_alive(npc):
            npc_logs = tick_conditions(npc)
            state.log.extend(npc_logs)

    state.round += 1
    state.current_actor_idx = 0

    # --- Phase evaluation ---

    # Player dead?
    player_hp = state.player_snapshot.get("hp_current", 0)
    if player_hp <= 0 or state.phase == "PLAYER_DEAD":
        state.phase = "PLAYER_DEAD"
        state.log.append("КІНЕЦЬ БОЮ: Гравець загинув.")
        return state.phase

    # All NPCs dead/fled/unconscious?
    living_npcs = [
        name for name, npc in state.npcs.items() if _npc_is_alive(npc)
    ]
    if not living_npcs:
        state.phase = "PLAYER_WIN"
        state.log.append("КІНЕЦЬ БОЮ: Всі вороги переможені. Перемога!")
        return state.phase

    # Timeout?
    if state.round > state.round_limit:
        state.phase = "TIMEOUT"
        state.log.append(
            f"КІНЕЦЬ БОЮ: Перевищено ліміт {state.round_limit} раундів — нічия/відступ."
        )
        return state.phase

    state.log.append(f"--- Раунд {state.round} ---")
    return state.phase


def player_flee_check(state: CombatState) -> tuple[bool, CheckResult]:
    """DEX saving throw vs DC = max(alive enemy DEX_mod) + 10.

    On success: state.phase = PLAYER_FLED.
    Returns (success_bool, CheckResult) for logging.
    """
    player = state.player_snapshot

    alive_dex_mods = [
        _get_dex_mod(npc)
        for npc in state.npcs.values()
        if _npc_is_alive(npc)
    ]
    max_enemy_dex = max(alive_dex_mods) if alive_dex_mods else 0
    flee_dc = max_enemy_dex + 10

    result = saving_throw(player, "DEX", flee_dc)

    if result.success:
        state.phase = "PLAYER_FLED"
        state.log.append(
            f"Гравець тікає з бою! (DEX рятівний кидок {result.roll_str})"
        )
    else:
        state.log.append(
            f"Гравець не може втекти (DEX рятівний кидок {result.roll_str})"
        )

    return result.success, result


def cleanup_combat(state: CombatState) -> dict:
    """Build updates dict when combat is over (phase != ONGOING).

    Returns:
    {
      'player_hp_change': int,              # final - initial
      'player_xp_gained': int,             # XP from killed NPCs by CR
      'npcs_status_changes': list[dict],   # per-NPC {name, old_status, new_status, final_hp}
      'combat_summary': str,
    }
    """
    final_hp = state.player_snapshot.get("hp_current", 0)
    hp_change = final_hp - state._initial_player_hp

    xp_gained = 0
    npc_changes: list[dict] = []

    for npc_name, npc in state.npcs.items():
        old_status = "Active"  # we don't track pre-combat status in snapshot
        new_status = npc.get("Status", "Active")
        final_npc_hp = npc.get("hp_current", 0)

        if new_status == "Dead":
            cr_str = str(npc.get("cr", "0"))
            xp_gained += _CR_XP.get(cr_str, 0)

        npc_changes.append({
            "name": npc_name,
            "old_status": old_status,
            "new_status": new_status,
            "final_hp": final_npc_hp,
        })

    phase_labels: dict[str, str] = {
        "PLAYER_WIN": f"Гравець переміг за {state.round - 1} раундів",
        "PLAYER_DEAD": f"Гравець загинув у раунді {state.round - 1}",
        "PLAYER_FLED": f"Гравець втік у раунді {state.round - 1}",
        "TIMEOUT": f"Бій завершився нічиєю після {state.round_limit} раундів",
        "ONGOING": "Бій завершено примусово (невідомий стан)",
    }
    summary = phase_labels.get(state.phase, "Бій завершено")

    return {
        "player_hp_change": hp_change,
        "player_xp_gained": xp_gained,
        "npcs_status_changes": npc_changes,
        "combat_summary": summary,
    }
