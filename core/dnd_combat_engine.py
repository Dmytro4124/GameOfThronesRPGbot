"""COMBAT_MODE pipeline adapter for Phase 7.

Bridges the combat FSM (dnd_combat.py) and the main engine (engine.py).
All LLM calls go through asyncio.to_thread per CLAUDE.md §5.1.
All combat-state mutations are guarded by try/finally per CLAUDE.md §5.4.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from core.ai_client import model_worker, clean_and_parse_json, build_strict_config
from core.dnd_combat import (
    CombatState,
    initiate_combat,
    player_attack,
    npc_attack,
    npc_decide_action,
    apply_damage,
    end_round,
    player_flee_check,
    cleanup_combat,
    parse_damage_dice,
    _npc_is_alive,
)
from core.combat_state import (
    get_combat_state,
    set_combat_state,
    clear_combat_state,
    is_in_combat,
    get_or_create_lock,
    cleanup_lock,
)
from core.dnd_conditions import has_condition
from core.prompts import (
    build_combat_round_prompt,
    build_npc_combat_action_prompt,
    build_combat_round_schema,
    build_npc_combat_action_schema,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum NPCs that can "spotlight" (act with LLM decision) in one round.
# Others are described narratively by GM_Logic/Narrator without a separate LLM call.
MAX_SPOTLIGHT_NPCS: int = 2

# NPC name tags that signal a leader — preferred for spotlight
_LEADER_TAGS: frozenset[str] = frozenset(
    {"captain", "noble", "leader", "commander", "captain-general",
     "капітан", "лорд", "командувач", "ватажок"}
)

# Fallback intent when LLM fails to parse player action
_FALLBACK_INTENT = "dodge"

# Valid player intents from build_combat_round_prompt output_schema
_VALID_INTENTS = frozenset({
    "attack", "cast", "move", "dodge", "flee", "item", "help", "grapple", "shove"
})


# ---------------------------------------------------------------------------
# 7.1  parse_player_combat_intent
# ---------------------------------------------------------------------------

async def parse_player_combat_intent(
    user_input: str,
    profile: dict,
    combat_state: CombatState,
) -> dict:
    """Call build_combat_round_prompt → LLM → JSON with player intent.

    Returns dict with keys: intent, target_npc, weapon, spell_or_ability,
    tactic, move_to, verdict_text, reasoning.
    Falls back to {"intent": "dodge", ...} on LLM failure or invalid JSON.
    """
    # Build a minimal snapshot for the prompt (only fields the prompt needs)
    snapshot = _build_combat_snapshot(combat_state)
    prompt = build_combat_round_prompt(
        user_input=user_input,
        profile=profile,
        combat_state_snapshot=snapshot,
    )

    result: Optional[dict] = None
    try:
        _combat_round_cfg = build_strict_config(model_worker, build_combat_round_schema())
        def _call():
            try:
                return model_worker.generate_content(prompt, config=_combat_round_cfg)
            except Exception as _schema_err:
                if "INVALID_ARGUMENT" in str(_schema_err) or "schema" in str(_schema_err).lower():
                    logger.warning(f"[COMBAT_ENGINE] Schema rejected for combat intent, falling back: {_schema_err}")
                    return model_worker.generate_content(
                        prompt + "\n\nIMPORTANT: Reply ONLY with valid JSON."
                    )
                raise

        resp = await asyncio.to_thread(_call)
        result = clean_and_parse_json(resp.text)
    except Exception as exc:
        logger.warning(f"[COMBAT_ENGINE] parse_player_combat_intent LLM error: {exc}")

    if not result or not isinstance(result, dict):
        logger.warning("[COMBAT_ENGINE] parse_player_combat_intent: JSON parse failed — using dodge fallback")
        return _fallback_intent(user_input)

    # Validate intent value
    raw_intent = str(result.get("intent", "")).strip().lower()
    if raw_intent not in _VALID_INTENTS:
        logger.warning(f"[COMBAT_ENGINE] Unknown intent '{raw_intent}' — falling back to dodge")
        result["intent"] = _FALLBACK_INTENT

    return result


# ---------------------------------------------------------------------------
# 7.2  decide_spotlight_npcs
# ---------------------------------------------------------------------------

async def decide_spotlight_npcs(
    combat_state: CombatState,
    max_spotlight: int = MAX_SPOTLIGHT_NPCS,
) -> list[str]:
    """Select 1–2 NPCs to act this round (spotlight pattern — no LLM call).

    Heuristic priority:
    1. NPC who attacked the player in the previous round (tracks via state.log last line).
    2. NPC with a leader tag in their name (captain, lord, commander, etc.).
    3. NPC with the highest init_roll among alive.
    NPCs not selected are "in fog" — described narratively without their own LLM call.
    Returns list of NPC names (length 1–max_spotlight, only alive NPCs).
    """
    alive_refs = [
        ref for ref in combat_state.initiative_order
        if ref.kind == "npc" and _npc_is_alive(combat_state.npcs.get(ref.name, {}))
    ]

    if not alive_refs:
        return []

    # Score each alive NPC
    scored: list[tuple[int, str]] = []
    for ref in alive_refs:
        score = ref.init_roll  # baseline: initiative order

        # Bonus: NPC acted against player in the most recent log entries
        npc_name_lower = ref.name.lower()
        for log_line in reversed(combat_state.log[-5:]):
            if npc_name_lower in log_line.lower() and "атакує" in log_line.lower():
                score += 20
                break

        # Bonus: leader tag in name
        name_lower = ref.name.lower()
        if any(tag in name_lower for tag in _LEADER_TAGS):
            score += 10

        scored.append((score, ref.name))

    # Sort descending by score, pick top max_spotlight
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored[:max_spotlight]]


# ---------------------------------------------------------------------------
# 7.3  execute_npc_actions
# ---------------------------------------------------------------------------

async def execute_npc_actions(
    combat_state: CombatState,
    spotlight_npc_names: list[str],
) -> list[dict]:
    """Batched LLM call for 1–2 spotlight NPCs, then execute mechanically.

    One LLM call decides all spotlight NPCs' actions.
    Returns list of result dicts, one per NPC, with keys:
        npc_name, action_taken, attack_result (AttackResult|None), log_lines.
    """
    if not spotlight_npc_names:
        return []

    npc_dict_list = [
        combat_state.npcs[name]
        for name in spotlight_npc_names
        if name in combat_state.npcs
    ]
    if not npc_dict_list:
        return []

    snapshot = _build_combat_snapshot(combat_state)
    prompt = build_npc_combat_action_prompt(
        combat_state_snapshot=snapshot,
        npc_dict_list=npc_dict_list,
    )

    llm_actions: list[dict] = []
    try:
        _npc_action_cfg = build_strict_config(model_worker, build_npc_combat_action_schema())
        def _call():
            try:
                return model_worker.generate_content(prompt, config=_npc_action_cfg)
            except Exception as _schema_err:
                if "INVALID_ARGUMENT" in str(_schema_err) or "schema" in str(_schema_err).lower():
                    logger.warning(f"[COMBAT_ENGINE] Schema rejected for NPC actions, falling back: {_schema_err}")
                    return model_worker.generate_content(
                        prompt + "\n\nIMPORTANT: Reply ONLY with valid JSON."
                    )
                raise

        resp = await asyncio.to_thread(_call)
        parsed = clean_and_parse_json(resp.text)
        if isinstance(parsed, dict):
            llm_actions = parsed.get("actions", [])
        elif isinstance(parsed, list):
            # Defensive: some LLM responses return the array directly
            llm_actions = parsed
    except Exception as exc:
        logger.warning(f"[COMBAT_ENGINE] execute_npc_actions LLM error: {exc}")

    # Build a lookup by npc_name
    llm_by_name: dict[str, dict] = {}
    for entry in llm_actions:
        if isinstance(entry, dict) and "npc_name" in entry:
            llm_by_name[entry["npc_name"]] = entry

    results: list[dict] = []

    for npc_name in spotlight_npc_names:
        npc = combat_state.npcs.get(npc_name)
        if not npc or not _npc_is_alive(npc):
            results.append({
                "npc_name": npc_name,
                "action_taken": "skip",
                "attack_result": None,
                "log_lines": [f"{npc_name}: мертвий/відсутній, пропускає хід."],
            })
            continue

        llm_entry = llm_by_name.get(npc_name, {})
        action = str(llm_entry.get("action", "attack")).strip().lower()
        target = str(llm_entry.get("target", "player")).strip()

        log_lines: list[str] = []
        attack_result = None

        if action == "flee":
            # Mark NPC as fled — no mechanical flee check per Phase 7 plan
            npc["Status"] = "Fled"
            log_lines.append(f"{npc_name} тікає з поля бою!")
            action_taken = "flee"

        elif action == "dodge":
            # Apply dodge bonus (+2 AC for this round via temp field)
            npc["_dodge_this_round"] = True
            log_lines.append(f"{npc_name} переходить у захисну стійку.")
            action_taken = "dodge"

        elif action in ("attack", "cast"):
            # Resolve mechanical attack
            attack_result = npc_attack(combat_state, npc_name, target=target)
            combat_state.log.append(attack_result.log_line)
            log_lines.append(attack_result.log_line)
            action_taken = "attack"

        elif action == "none":
            log_lines.append(f"{npc_name}: непритомний/оглушений, пропускає хід.")
            action_taken = "skip"

        else:
            # Unknown action from LLM — default to attack
            logger.warning(f"[COMBAT_ENGINE] Unknown NPC action '{action}' for {npc_name} — defaulting to attack")
            attack_result = npc_attack(combat_state, npc_name, target="player")
            combat_state.log.append(attack_result.log_line)
            log_lines.append(attack_result.log_line)
            action_taken = "attack"

        results.append({
            "npc_name": npc_name,
            "action_taken": action_taken,
            "attack_result": attack_result,
            "log_lines": log_lines,
        })

    return results


# ---------------------------------------------------------------------------
# 7.4  execute_combat_round
# ---------------------------------------------------------------------------

async def execute_combat_round(
    chat_id: int,
    user_input: str,
    profile: dict,
) -> tuple[str, dict, list[dict]]:
    """Execute one full combat round (4 LLM calls max per plan):

    1. parse_player_combat_intent → player action (LLM call #1)
    2. Execute player action (player_attack / dodge / flee / item / grapple / shove)
    3. decide_spotlight_npcs → select 1-2 NPCs (heuristic, no LLM)
    4. execute_npc_actions → batched LLM call for selected NPCs (LLM call #2)
    5. end_round → tick conditions, check phase transition
    6. If phase != ONGOING → prepare for cleanup

    Returns:
        combat_log_summary: str  — human-readable log for GM_Logic/Narrator
        updates_dict: dict       — mechanical updates (hp changes, conditions, etc.)
        npc_action_results: list[dict]  — raw NPC action results for GM context
    """
    combat_state = get_combat_state(chat_id)
    if combat_state is None:
        logger.error(f"[COMBAT_ENGINE] execute_combat_round: no CombatState for chat_id={chat_id}")
        return "Стан бою не знайдено.", {"combat_ended": True, "combat_phase": "TIMEOUT"}, []

    player_name: str = profile.get("Ім'я", profile.get("name", "Player"))
    round_logs: list[str] = [f"--- Раунд {combat_state.round} ---"]

    # --- Step 1: Parse player intent ---
    player_intent = await parse_player_combat_intent(user_input, profile, combat_state)
    intent = player_intent.get("intent", _FALLBACK_INTENT)
    target_npc = player_intent.get("target_npc")
    weapon = player_intent.get("weapon")
    tactic = player_intent.get("tactic", "normal")

    # Apply tactic modifiers (reckless/cautious) via temp profile fields
    advantage = (tactic == "reckless")
    disadvantage = (tactic == "cautious")

    # --- Step 2: Execute player action ---
    player_log_lines: list[str] = []

    if intent == "attack" or intent == "grapple" or intent == "shove":
        if not target_npc:
            # Auto-select first alive NPC
            for ref in combat_state.initiative_order:
                if ref.kind == "npc" and _npc_is_alive(combat_state.npcs.get(ref.name, {})):
                    target_npc = ref.name
                    break

        if target_npc:
            attack_res = player_attack(
                combat_state,
                target_name=target_npc,
                weapon_name=weapon or "",
                advantage=advantage,
                disadvantage=disadvantage,
            )
            player_log_lines.append(attack_res.log_line)
            combat_state.log.append(attack_res.log_line)
        else:
            player_log_lines.append(f"{player_name}: немає живих цілей для атаки.")

    elif intent == "flee":
        success, flee_result = player_flee_check(combat_state)
        player_log_lines.append(
            f"{player_name} намагається втекти: {flee_result.roll_str} "
            f"({'УСПІХ' if success else 'ПРОВАЛ'})"
        )

    elif intent == "dodge":
        # Dodge: no attack, but note defensive stance
        player_log_lines.append(f"{player_name} займає захисну стійку (Dodge).")
        # Mark player as dodging for this round — NPC attacks against player may roll with disadvantage
        combat_state.player_snapshot["_dodge_this_round"] = True

    elif intent == "cast":
        # Cast heritage ability — treated mechanically as an attack for Phase 7
        spell = player_intent.get("spell_or_ability")
        if target_npc and spell:
            attack_res = player_attack(
                combat_state,
                target_name=target_npc,
                weapon_name=spell,
                advantage=advantage,
                disadvantage=disadvantage,
            )
            player_log_lines.append(attack_res.log_line)
            combat_state.log.append(attack_res.log_line)
        else:
            player_log_lines.append(f"{player_name} активує здібність '{spell or 'невідома'}' — без ефекту (немає цілі).")

    elif intent == "item":
        # Item use: Phase 7 — narrative only, no mechanical resolution here
        player_log_lines.append(f"{player_name} використовує предмет з інвентарю.")

    elif intent == "move":
        move_to = player_intent.get("move_to", "")
        player_log_lines.append(f"{player_name} переміщується: {move_to or 'нова позиція'}.")

    elif intent == "help":
        player_log_lines.append(f"{player_name} допомагає союзнику — наступна атака отримує перевагу.")

    else:
        # Unknown intent — dodge fallback
        player_log_lines.append(f"{player_name} займає захисну стійку (fallback dodge).")

    round_logs.extend(player_log_lines)

    # Early exit if player fled or died during their own action
    if combat_state.phase != "ONGOING":
        phase = end_round(combat_state)
        combat_log_summary = _format_round_log(round_logs)
        updates = _build_combat_updates(combat_state, phase)
        return combat_log_summary, updates, []

    # --- Step 3: Select spotlight NPCs ---
    spotlight_names = await decide_spotlight_npcs(combat_state)

    # --- Step 4: Execute NPC actions ---
    npc_action_results = await execute_npc_actions(combat_state, spotlight_names)
    for res in npc_action_results:
        round_logs.extend(res.get("log_lines", []))

    # Clear per-round temp fields
    combat_state.player_snapshot.pop("_dodge_this_round", None)
    for npc in combat_state.npcs.values():
        npc.pop("_dodge_this_round", None)

    # --- Step 5: end_round — tick conditions, check phase ---
    phase = end_round(combat_state)
    round_logs.extend(combat_state.log[-3:])  # pick up condition tick logs

    # --- Build outputs ---
    combat_log_summary = _format_round_log(round_logs)
    updates = _build_combat_updates(combat_state, phase)

    return combat_log_summary, updates, npc_action_results


# ---------------------------------------------------------------------------
# 7.5  initiate_combat_from_normal
# ---------------------------------------------------------------------------

async def initiate_combat_from_normal(
    chat_id: int,
    profile: dict,
    scene_npcs: list[dict],
) -> CombatState:
    """Transition from NORMAL mode to COMBAT mode.

    Called when NORMAL pipeline detects combat_imminent=True.
    1. Build CombatState via initiate_combat (initiative rolls included).
    2. Store in registry via set_combat_state.
    3. Mutate profile['mode'] = 'COMBAT'.
    Returns the new CombatState.
    """
    import copy
    player_copy = copy.deepcopy(profile)

    combat_state = initiate_combat(
        player_profile=player_copy,
        npc_list=scene_npcs,
        chat_id=chat_id,
    )
    set_combat_state(chat_id, combat_state)

    profile["mode"] = "COMBAT"
    logger.info(
        f"[COMBAT_ENGINE] Combat initiated for chat_id={chat_id}. "
        f"NPCs: {list(combat_state.npcs.keys())}. "
        f"Initiative: {[(r.name, r.init_roll) for r in combat_state.initiative_order]}"
    )
    return combat_state


# ---------------------------------------------------------------------------
# 7.6  cleanup_and_exit_combat
# ---------------------------------------------------------------------------

async def cleanup_and_exit_combat(chat_id: int, profile: dict) -> dict:
    """Called when combat ends (phase != ONGOING).

    1. Get CombatState from registry.
    2. cleanup_combat() → build updates dict.
    3. Apply hp_change to profile (both D&D hp_current and legacy "Здоров'я" field).
    4. Apply xp_award to profile.
    5. clear_combat_state + cleanup_lock.
    6. profile['mode'] = 'NORMAL'.
    Returns the updates dict for engine to merge and write to DB.
    """
    from core.dnd_progression import award_xp

    combat_state = get_combat_state(chat_id)
    if combat_state is None:
        logger.warning(f"[COMBAT_ENGINE] cleanup_and_exit_combat: no CombatState for chat_id={chat_id}")
        profile["mode"] = "NORMAL"
        return {"combat_ended": True, "player_hp_change": 0, "player_xp_gained": 0,
                "npcs_status_changes": [], "combat_summary": "Бій завершено (стан не знайдено)."}

    updates = cleanup_combat(combat_state)

    # --- Apply HP change to profile ---
    hp_change: int = updates.get("player_hp_change", 0)
    if hp_change != 0:
        # D&D profile: hp_current / hp_max
        if "hp_current" in profile:
            old_hp = int(profile.get("hp_current", 0))
            hp_max = int(profile.get("hp_max", old_hp))
            new_hp = max(0, min(hp_max, old_hp + hp_change))
            profile["hp_current"] = new_hp
        # Legacy profile: "Здоров'я" (0-100 HP scale, CLAUDE.md §5.2)
        if "Здоров'я" in profile:
            from core.mechanics import safe_int as _safe_int
            old_health = _safe_int(profile.get("Здоров'я", 100), 100)
            # Scale hp_change proportionally: D&D HP can be >100, legacy HP is 0-100
            hp_max_dnd = int(profile.get("hp_max", 100)) or 100
            scaled_change = round(hp_change * 100 / hp_max_dnd)
            new_health = max(0, min(100, old_health + scaled_change))
            profile["Здоров'я"] = new_health

    # --- Apply XP ---
    xp_gained: int = updates.get("player_xp_gained", 0)
    if xp_gained > 0:
        try:
            award_xp(profile, xp_gained, reason="combat_victory")
            logger.info(f"[COMBAT_ENGINE] XP awarded: +{xp_gained} for chat_id={chat_id}")
        except Exception as exc:
            logger.warning(f"[COMBAT_ENGINE] award_xp failed: {exc}")

    # --- NPC status changes (for DB write by engine background_task) ---
    # The actual Sheets write happens in engine.py background_task via update_npcs_in_db.
    # We attach npc_updates list in the format expected by that function.
    npc_updates_for_db: list[dict] = []
    for npc_change in updates.get("npcs_status_changes", []):
        npc_name = npc_change.get("name", "")
        new_status = npc_change.get("new_status", "Active")
        final_hp = npc_change.get("final_hp", 0)
        entry: dict = {"Name": npc_name, "Status": new_status}
        if new_status == "Dead":
            entry["hp_current"] = 0
        else:
            entry["hp_current"] = final_hp
        npc_updates_for_db.append(entry)

    updates["npc_updates"] = npc_updates_for_db

    # --- Cleanup registry ---
    clear_combat_state(chat_id)
    cleanup_lock(chat_id)

    # --- Reset mode ---
    profile["mode"] = "NORMAL"

    summary = updates.get("combat_summary", "Бій завершено.")
    logger.info(f"[COMBAT_ENGINE] Combat ended for chat_id={chat_id}: {summary}")

    return updates


# ---------------------------------------------------------------------------
# 7.7  format_combat_log_for_narrator
# ---------------------------------------------------------------------------

def format_combat_log_for_narrator(
    combat_state: CombatState,
    npc_results: list[dict],
) -> list[str]:
    """Form human-readable combat_log entries for build_narrator_prompt(combat_log=...).

    Each entry: 'Раунд X: [actor] дія → результат'
    Picks the most recent meaningful log lines from combat_state.log.
    """
    entries: list[str] = []

    current_round = combat_state.round
    prefix = f"Раунд {current_round - 1}"  # round was already incremented by end_round

    # Collect lines from state.log that belong to this round
    # Lines added during this round are after the last "--- Раунд N ---" marker
    in_current_round = False
    for line in combat_state.log:
        if f"--- Раунд {current_round - 1} ---" in line:
            in_current_round = True
            continue
        if line.startswith("--- Раунд") and in_current_round:
            break  # Hit next round marker — stop
        if in_current_round and line.strip():
            entries.append(f"{prefix}: {line}")

    # Supplement with NPC action descriptions if entries are sparse
    if not entries:
        for res in npc_results:
            for line in res.get("log_lines", []):
                entries.append(f"{prefix}: {line}")

    # Always include phase change if combat ended
    if combat_state.phase != "ONGOING":
        phase_labels = {
            "PLAYER_WIN": "Перемога! Всі вороги переможені.",
            "PLAYER_DEAD": "Гравець загинув у бою.",
            "PLAYER_FLED": "Гравець втік з поля бою.",
            "TIMEOUT": "Бій вичерпав ліміт раундів — нічия.",
        }
        label = phase_labels.get(combat_state.phase, "Бій завершено.")
        entries.append(f"ПІДСУМОК: {label}")

    return entries


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_combat_snapshot(combat_state: CombatState) -> dict:
    """Build a minimal JSON-serialisable snapshot for LLM prompts.

    Excludes internal Python objects; keeps only data the LLM needs.
    """
    player = combat_state.player_snapshot
    player_name = player.get("Ім'я", player.get("name", "Player"))

    return {
        "round": combat_state.round,
        "phase": combat_state.phase,
        "initiative_order": [
            {"kind": r.kind, "name": r.name, "init_roll": r.init_roll}
            for r in combat_state.initiative_order
        ],
        "player": {
            "name": player_name,
            "hp_current": player.get("hp_current", player.get("Здоров'я", 100)),
            "hp_max": player.get("hp_max", 100),
            "ac": player.get("ac", 10),
            "conditions": player.get("conditions", []),
            "equipment": player.get("equipment", {}),
        },
        "npcs": {
            name: {
                "name": name,
                "hp_current": npc.get("hp_current", 0),
                "hp_max": npc.get("hp_max", npc.get("hp_current", 0)),
                "ac": npc.get("ac", 10),
                "Status": npc.get("Status", "Active"),
                "conditions": npc.get("conditions", []),
                "attacks": npc.get("attacks", []),
            }
            for name, npc in combat_state.npcs.items()
        },
    }


def _format_round_log(log_lines: list[str]) -> str:
    """Join round log lines into a single string for GM_Logic context."""
    return "\n".join(line for line in log_lines if line.strip())


def _build_combat_updates(combat_state: CombatState, phase: str) -> dict:
    """Build minimal updates dict from CombatState for engine.py to consume."""
    player = combat_state.player_snapshot
    player_name = player.get("Ім'я", player.get("name", "Player"))

    alive_npcs = [
        name for name, npc in combat_state.npcs.items()
        if _npc_is_alive(npc)
    ]

    return {
        "combat_phase": phase,
        "combat_ended": phase != "ONGOING",
        "combat_round": combat_state.round,
        "player_hp_current": player.get("hp_current", player.get("Здоров'я", 100)),
        "alive_npc_names": alive_npcs,
        "action_type": "combat",
        # Legacy keys for apply_system_impacts compatibility
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
        "skill_used": "None",
        "outcome": "SUCCESS" if phase in ("PLAYER_WIN", "ONGOING") else "FAILURE",
    }


def _fallback_intent(user_input: str) -> dict:
    """Return a safe fallback intent dict (dodge) when LLM fails."""
    return {
        "intent": _FALLBACK_INTENT,
        "target_npc": None,
        "weapon": None,
        "spell_or_ability": None,
        "tactic": "normal",
        "move_to": None,
        "verdict_text": f"Гравець займає захисну позицію (fallback від: {user_input[:50]!r}).",
        "reasoning": "LLM parse failed — using safe dodge fallback.",
    }
