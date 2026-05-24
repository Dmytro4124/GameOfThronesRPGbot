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

# Heritage names that grant fire resistance (Valyrian Descent)
_FIRE_RESISTANT_HERITAGES: frozenset[str] = frozenset({"Valyrian Descent"})

# Heritage names that grant cold immunity (Free Folk)
_COLD_IMMUNE_HERITAGES: frozenset[str] = frozenset({"Free Folk"})

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
    """Return True if profile has Valyrian fire resistance."""
    heritage = _get_heritage(profile)
    return heritage in _FIRE_RESISTANT_HERITAGES


def _is_cold_immune(profile: dict) -> bool:
    """Return True if profile has Free Folk cold immunity (Phase 7 environment context)."""
    heritage = _get_heritage(profile)
    return heritage in _COLD_IMMUNE_HERITAGES


def _npc_is_alive(npc: dict) -> bool:
    """Return True if NPC is still capable of acting."""
    status = npc.get("Status", "Active")
    hp = npc.get("hp_current", 1)
    return status not in ("Dead", "Fled", "Unconscious") and hp > 0


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
        # Guarantee required combat fields
        if "hp_current" not in snapshot:
            snapshot["hp_current"] = snapshot.get("hp_max", 10)
        if "ac" not in snapshot:
            snapshot["ac"] = 10
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
) -> int:
    """Roll damage dice. Critical: roll dice twice (D&D 5e crit rule). Minimum 1."""
    if die_size == 0:
        # Flat damage (parse_damage_dice fallback path)
        total = max(1, num_dice + modifier)
        return total

    dice_count = num_dice * 2 if critical else num_dice
    total = sum(random.randint(1, die_size) for _ in range(dice_count)) + modifier
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
    """
    player = state.player_snapshot
    player_name: str = player.get("Ім'я", player.get("name", "Player"))

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

    # Determine attack ability: use STR or DEX (finesse weapon detection)
    equipment = player.get("equipment", {})
    weapon_str = equipment.get("weapon_main", "Unarmed (1d4 bludgeoning)")
    if weapon_name and weapon_name.lower() not in weapon_str.lower():
        # Caller provided a specific weapon name not matching main — use as-is for parsing
        weapon_str = weapon_name

    # Finesse weapons: use better of STR/DEX
    finesse_keywords = ("dagger", "shortsword", "rapier", "кинджал", "рапіра", "короткий меч")
    weapon_lower = weapon_str.lower()
    if any(kw in weapon_lower for kw in finesse_keywords):
        str_mod = _get_str_mod(player)
        dex_mod = _get_dex_mod(player)
        attack_mod = max(str_mod, dex_mod)
    else:
        attack_mod = _get_str_mod(player)

    level = player.get("level", 1)
    prof = proficiency_bonus(level)
    target_ac = npc.get("ac", 10)

    natural, rolls = roll_d20(final_adv, final_dis)
    to_hit_total = natural + attack_mod + prof
    critical = (natural == 20)
    hit = critical or (natural != 1 and to_hit_total >= target_ac)

    # Auto-crit if target is paralyzed/unconscious (within 5ft implied in melee)
    if not critical and cond_info.get("auto_crit") and hit:
        critical = True

    # Damage
    num_dice, die_size, dmg_mod, dmg_type = parse_damage_dice(weapon_str)
    damage_total = 0
    damage_dice_str = weapon_str.split("(")[-1].rstrip(")") if "(" in weapon_str else weapon_str

    if hit:
        damage_total = _roll_damage(num_dice, die_size, dmg_mod, critical)
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
        # Fallback: unarmed 1d4 bludgeoning with +0 to hit
        attack_data = {"name": "Unarmed", "to_hit": 0, "dmg": "1d4 bludgeoning", "range": 5}
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
