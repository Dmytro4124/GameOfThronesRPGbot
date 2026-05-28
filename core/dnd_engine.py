# core/dnd_engine.py
"""
Phase 5: D&D 5e engine adapter for NORMAL pipeline.

Роль: замінює resolve_action_mechanics (старий 2d50 Worker) для NORMAL mode.
apply_dnd_impacts — wrapper поверх старого apply_system_impacts для нових D&D-тегів.
COMBAT pipeline — Phase 7, тут не реалізовано.
"""

import asyncio
import random
import logging

logger = logging.getLogger(__name__)

from core.ai_client import model_worker, clean_and_parse_json, build_strict_config
from core.dnd_core import (
    roll_d20, ability_modifier, proficiency_bonus, clamp_dc,
    LEGAL_DCS, skill_check, ability_check, saving_throw, CheckResult,
)
from core.dnd_skills import SKILLS, get_ability_for_skill, is_valid_skill
from core.dnd_classes import GOT_CLASSES, get_class_features_at_level
from core.dnd_progression import award_xp, level_up, apply_asi, get_level_for_xp, apply_pending_levelups
from core.dnd_conditions import (
    apply_condition, remove_condition, has_condition, condition_modifies,
)
from core.prompts import build_normal_resolve_prompt, build_normal_resolve_schema
from core.world_constants import (
    get_region_for_location, get_locations_for_region,
    LOCATION_TO_REGION, VALID_REGIONS_ORDERED,
)

# ---------------------------------------------------------------------------
# AUTO_SUCCESS DC threshold: DC <= this value means no roll needed.
# LEGAL_DCS[0] == 2 — ultra-trivial actions (sensory, body movement, prosaic
# interaction, social signals) skip the roll entirely with DC 2 auto-success.
# DC 5 still triggers a (very easy) roll for slightly riskier trivial actions.
# ---------------------------------------------------------------------------
AUTO_SUCCESS_MAX_DC: int = 2

# Legal XP award values (from prompts output_schema)
_LEGAL_XP: frozenset[int] = frozenset({0, 25, 50, 100, 200})

# Physical danger keywords triggering DC floor (D&D PHB: falling ≥ 10 ft → 1d6 per 10 ft)
_DANGER_KEYWORDS = [
    "стриб", "паді", "зістриб", "зіскоч", "стін", "вікн", "дха",
    "обрив", "прірв", "вогонь", "полум",
]
# DC floor for clearly dangerous physical actions.
# Lowered to 15 (testing): was 25, which was unreachable for L1 chars (max roll ~25).
_DANGER_DC_FLOOR: int = 15

# Attack keywords for Layer 2 guard rail (defense-in-depth).
# Suppresses hp_damage_dice when player attacks NPC (which must use COMBAT pipeline).
# Memory `feedback_no_keyword_matching` permits this narrow safety-critical use.
_ATTACK_KEYWORDS = (
    "атак",    # атакую, атака, атакувати
    "удар",    # удар, ударом, ударити
    "бий",     # бий, б'ю, бійся
    "ріж",     # ріжу, різати
    "стріля",  # стріляю, стріляти, вистрілив
    "кида",    # кидаю, кидати (метальна зброя)
    "поран",   # поранити, пораню, пораниш, поранивши (не false-positive: "пора йти")
    "вдар",    # вдаряю, вдарити, вдари
    "руба",    # рубаю, рубати, рубонув
    "колот",   # колоти, колеш
    "штрика",  # штрикаю, штрикнути
)

# Dice roll map for hp_damage_dice / hp_heal_dice tags
_DICE_MAP: dict[str, tuple[int, int]] = {
    "1d4":  (1, 4),
    "1d6":  (1, 6),
    "1d8":  (1, 8),
    "2d6":  (2, 6),
    "2d8":  (2, 8),
}


def safe_int(v, default: int = 0) -> int:
    """Convert any value to int safely; return default on failure."""
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return default


def _roll_dice_str(dice_str: str) -> int:
    """Roll a dice expression like '2d6' and return the total."""
    entry = _DICE_MAP.get(dice_str)
    if entry is None:
        return 0
    num, sides = entry
    return sum(random.randint(1, sides) for _ in range(num))


def _has_danger_keyword(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _DANGER_KEYWORDS)


async def resolve_normal_action(
    user_input: str,
    profile: dict,
    npc_reputation_context: dict = None,
    current_scene: str = None,
    npc_names: list = None,
    last_turn_summary: str = None,
    user_id: int = None,
    current_location: str = None,
    debug_trace: dict | None = None,
) -> tuple[str, dict]:
    """
    Async D&D Worker — замінює resolve_action_mechanics для NORMAL mode.

    Повертає (verdict_str, updates_dict).
    updates_dict shape сумісний зі старим pipeline (щоб engine.py не ламався).

    Нові D&D-специфічні ключі в updates:
      skill_used, ability_used, difficulty, outcome,
      natural_roll, total_score, advantage_reason, disadvantage_reason,
      hp_damage_dice, hp_heal_dice, gold_impact, location_impact, scene_impact,
      minutes_passed, inventory_new, inventory_lost, clocks_impact,
      condition_apply, condition_remove, xp_award,
      reputation_delta, reputation_target_npc, combat_imminent.

    Ключ action_type="standard" зберігається для сумісності з engine.py.
    """
    # --- 1. Build context for prompt ---
    _curr_region = get_region_for_location(current_location or "")
    nearby_locs = get_locations_for_region(_curr_region) if _curr_region else []

    _region_groups: dict[str, list[str]] = {}
    for loc, reg in LOCATION_TO_REGION.items():
        _region_groups.setdefault(reg, []).append(loc)
    all_locs_grouped = "; ".join(
        f"{reg}: {', '.join(_region_groups[reg])}"
        for reg in VALID_REGIONS_ORDERED if reg in _region_groups
    )

    # npcs_in_scene: для build_normal_resolve_prompt потрібен список dict-ів NPC.
    # npc_names — лише список рядків (із get_location_npcs). Для Phase 5 обгортаємо у мінімальний dict.
    npcs_in_scene_dicts = [{"Name": n, "Relation_Player": "Нейтральний"}
                           for n in (npc_names or [])]

    clocks_info = profile.get("Годинники", {})

    prompt = build_normal_resolve_prompt(
        user_input=user_input,
        profile=profile,
        current_scene=current_scene or "Unknown",
        npcs_in_scene=npcs_in_scene_dicts,
        last_turn_summary=last_turn_summary or "Game start",
        current_location=current_location or "Unknown",
        npc_reputation_context=npc_reputation_context or {},
        clocks_info=clocks_info,
        nearby_canonical_locs=nearby_locs,
        all_canonical_locs_grouped=all_locs_grouped,
    )
    if debug_trace is not None:
        debug_trace["worker"]["prompt"] = prompt

    # --- 2. Call LLM with 1 retry (gemma sometimes returns MALFORMED/None text;
    #        retry before degrading to AUTO_SUCCESS which removes the dice roll).
    #        §5.1 async invariant: all LLM calls via asyncio.to_thread. ---
    _worker_cfg = build_strict_config(model_worker, build_normal_resolve_schema())

    def _sync_gen():
        try:
            return model_worker.generate_content(prompt, config=_worker_cfg)
        except Exception as _schema_err:
            if "INVALID_ARGUMENT" in str(_schema_err) or "schema" in str(_schema_err).lower():
                print(f"⚠️ [DND_ENGINE] Schema rejected, falling back to free JSON: {_schema_err}")
                return model_worker.generate_content(
                    prompt + "\n\nIMPORTANT: Reply ONLY with valid JSON."
                )
            raise

    data = None
    for _w_attempt in range(2):  # 1 initial + 1 retry
        try:
            resp = await asyncio.to_thread(_sync_gen)
        except Exception as e:
            logger.warning(f"[DND_ENGINE] LLM call failed (attempt {_w_attempt + 1}/2): {e}")
            continue
        _raw_text = resp.text if resp else None
        if debug_trace is not None:
            debug_trace["worker"]["raw"] = _raw_text
        _parsed = clean_and_parse_json(_raw_text) if _raw_text else None
        if _parsed:
            data = _parsed
            break
        logger.warning(
            f"[DND_ENGINE] empty/unparseable Worker response (attempt {_w_attempt + 1}/2) — retrying"
        )

    # --- 3. Fallback: LLM failure after retries → AUTO_SUCCESS ---
    if not data:
        logger.warning("[DND_ENGINE] JSON parse failed after retries — falling back to AUTO_SUCCESS")
        fallback_updates = {
            "action_type": "standard",
            "skill_used": "None",
            "ability_used": "None",
            "outcome": "SUCCESS",
            "natural_roll": 0,
            "total_score": 0,
            "difficulty": AUTO_SUCCESS_MAX_DC,
            "advantage_reason": "",
            "disadvantage_reason": "",
            "combat_imminent": False,
            "xp_award": 0,
            "reputation_delta": 0,
            "reputation_target_npc": "",
            "minutes_passed": 5,
            "location_impact": "none",
            "scene_impact": "none",
            "hp_damage_dice": "none",
            "hp_heal_dice": "none",
            "health_impact": "none",  # legacy compat
            "energy_impact": "none",  # legacy compat
            "gold_impact": "none",
            "inventory_new": [],
            "inventory_lost": [],
            "clocks_impact": {},
            "condition_apply": [],
            "condition_remove": [],
        }
        return "MECHANICAL VERDICT: AUTO_SUCCESS (LLM fallback).", fallback_updates

    logger.debug(f"[DND_ENGINE RAW JSON]: {data}")

    # --- 4. Extract core fields ---
    raw_updates: dict = data.get("updates", {})
    ability_used: str = str(data.get("ability_used", "None")).strip()
    skill_used: str = str(data.get("skill_used", "None")).strip()
    raw_difficulty: int = safe_int(data.get("difficulty", AUTO_SUCCESS_MAX_DC), AUTO_SUCCESS_MAX_DC)
    advantage_reason: str = str(data.get("advantage_reason", "")).strip()
    disadvantage_reason: str = str(data.get("disadvantage_reason", "")).strip()
    combat_imminent: bool = bool(data.get("combat_imminent", False))
    verdict_text_llm: str = str(data.get("verdict_text", "")).strip()
    raw_xp: int = safe_int(data.get("xp_award", 0), 0)
    reputation_delta: int = safe_int(data.get("reputation_delta", 0), 0)
    reputation_target_npc: str = str(data.get("reputation_target_npc", "")).strip()

    # --- 4b. P2: Rest resolution (override skill-check flow) ---
    # NOTE: REST has priority over SAVE. If LLM returns both rest_type and save_used,
    # rest is applied first and the early return skips the save block entirely.
    rest_type: str = str(data.get("rest_type", "none")).strip().lower()

    if rest_type in ("long", "short"):
        from core.dnd_rest import long_rest, short_rest
        if rest_type == "long":
            rest_result = long_rest(profile)
            verdict_addendum = (
                f"\nLONG REST: HP {rest_result.hp_restored} restored "
                f"(→ {profile.get('hp_max')}/{profile.get('hp_max')}), "
                f"hit dice regained: {rest_result.hit_dice_regained}, "
                f"conditions cleared: {rest_result.conditions_cleared or 'none'}."
            )
            # Long rest fully heals — suppress any Worker-supplied dice that would double-count
            if raw_updates.get("hp_damage_dice") not in ("none", "None", ""):
                raw_updates["hp_damage_dice"] = "none"
            if raw_updates.get("hp_heal_dice") not in ("none", "None", ""):
                raw_updates["hp_heal_dice"] = "none"
            # Sync legacy UI field
            hp_max = safe_int(profile.get("hp_max", 1), 1)
            profile["Здоров'я"] = 100 if hp_max > 0 else 0

        else:  # short
            hit_dice_spent = 1  # default 1 die; future: parameterise from Worker output
            rest_result = short_rest(profile, hit_dice_spent=hit_dice_spent)
            verdict_addendum = (
                f"\nSHORT REST: spent {rest_result.hit_dice_spent} hit dice, "
                f"restored {rest_result.hp_restored} HP."
            )
            # Short rest already applied HP — suppress Worker damage dice
            if raw_updates.get("hp_damage_dice") not in ("none", "None", ""):
                raw_updates["hp_damage_dice"] = "none"
            # Sync legacy UI field
            hp_current = safe_int(profile.get("hp_current", 0), 0)
            hp_max = safe_int(profile.get("hp_max", 1), 1)
            profile["Здоров'я"] = round(100 * hp_current / hp_max) if hp_max > 0 else 0

        verdict_text_llm = (verdict_text_llm or "") + verdict_addendum

        # Rest actions bypass skill check flow entirely
        updates = _build_updates(
            raw_updates=raw_updates,
            action_type="standard",
            skill_used="None",
            ability_used="None",
            outcome="SUCCESS",
            natural_roll=0,
            total_score=0,
            difficulty=AUTO_SUCCESS_MAX_DC,
            advantage_reason="",
            disadvantage_reason="",
            combat_imminent=combat_imminent,
            xp_award=raw_xp if raw_xp in _LEGAL_XP else 0,
            reputation_delta=reputation_delta,
            reputation_target_npc=reputation_target_npc,
        )
        verdict_str = f"MECHANICAL VERDICT: REST ({rest_type.upper()}). GM INFO: {verdict_text_llm}"
        updates["dice_roll"] = "-"
        updates["skill_val"] = 0
        return verdict_str, updates

    # --- 4c. P1: Saving throw resolution (override skill-check flow) ---
    save_used: str = str(data.get("save_used", "None")).strip().upper()
    save_dc_raw: int = safe_int(data.get("save_dc", 5), 5)

    if save_used in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        # Worker decided this is a save scenario — override normal skill flow
        save_dc: int = clamp_dc(save_dc_raw)
        if save_dc != save_dc_raw:
            logger.info(f"[DND_ENGINE] save_dc clamped: {save_dc_raw} → {save_dc}")

        # GODMODE: always succeed on saves too
        from config import GODMODE_USERS
        if user_id and user_id in GODMODE_USERS:
            save_natural = 20
            save_total = 20
            save_outcome = "CRITICAL SUCCESS"
            save_roll_str = "GODMODE"
        else:
            save_result: CheckResult = saving_throw(
                profile=profile,
                ability=save_used,
                dc=save_dc,
                advantage=False,
                disadvantage=False,
            )
            save_natural = save_result.natural
            save_total = save_result.total
            save_roll_str = save_result.roll_str
            if save_result.critical == "success":
                save_outcome = "CRITICAL SUCCESS"
            elif save_result.critical == "fail":
                save_outcome = "CRITICAL FAILURE"
            elif save_result.success:
                save_outcome = "SUCCESS"
            else:
                save_outcome = "FAILURE"

        # On FAILURE → hp_damage_dice stays as Worker set it (damage happens)
        # On SUCCESS / CRITICAL SUCCESS → no damage (suppress hp_damage_dice)
        if save_outcome in ("SUCCESS", "CRITICAL SUCCESS"):
            if raw_updates.get("hp_damage_dice") not in ("none", "None", ""):
                logger.info(
                    f"[DND_ENGINE] Save {save_outcome}: suppressing hp_damage_dice="
                    f"{raw_updates['hp_damage_dice']!r}"
                )
                raw_updates["hp_damage_dice"] = "none"

        # XP validation (needed before _build_updates)
        xp_award_save: int = raw_xp if raw_xp in _LEGAL_XP else 0

        verdict_str = (
            f"MECHANICAL VERDICT: SAVING THROW {save_outcome}! "
            f"({save_used} save vs DC {save_dc}, Roll: {save_roll_str}, "
            f"Total: {save_total}). "
            f"GM INFO: {verdict_text_llm}"
        )
        updates = _build_updates(
            raw_updates=raw_updates,
            action_type="standard",
            skill_used="None",
            ability_used=save_used,
            outcome=save_outcome,
            natural_roll=save_natural,
            total_score=save_total,
            difficulty=save_dc,
            advantage_reason="",
            disadvantage_reason="",
            combat_imminent=combat_imminent,
            xp_award=xp_award_save,
            reputation_delta=reputation_delta,
            reputation_target_npc=reputation_target_npc,
        )
        updates["dice_roll"] = save_roll_str
        updates["skill_val"] = 0

        # Attack guard rail still applies to save scenarios (defence-in-depth)
        _user_lower_save = (user_input or "").lower()
        if any(kw in _user_lower_save for kw in _ATTACK_KEYWORDS):
            if updates.get("hp_damage_dice") not in ("none", "None", ""):
                updates["hp_damage_dice"] = "none"
            if not updates.get("combat_imminent"):
                updates["combat_imminent"] = True

        return verdict_str, updates

    # --- 5. Validate and clamp DC via clamp_dc (§5.2 DC invariant) ---
    # clamp_dc snaps to nearest legal value in LEGAL_DCS
    clamped_dc: int = clamp_dc(raw_difficulty)
    if clamped_dc != raw_difficulty:
        logger.info(f"[DND_ENGINE] DC clamped: {raw_difficulty} → {clamped_dc}")

    # A09 FIX port: danger floor for physically hazardous actions
    if _has_danger_keyword(user_input) and clamped_dc < _DANGER_DC_FLOOR:
        clamped_dc = clamp_dc(_DANGER_DC_FLOOR)
        logger.info(f"[DND_ENGINE] Danger floor applied: DC → {clamped_dc}")

    difficulty: int = clamped_dc

    # --- 6. Validate XP award ---
    xp_award: int = raw_xp if raw_xp in _LEGAL_XP else 0
    if raw_xp not in _LEGAL_XP:
        logger.info(f"[DND_ENGINE] Illegal xp_award {raw_xp} → clamped to 0")

    # --- 7. Determine advantage/disadvantage ---
    # Both present → cancel out (D&D PHB p.173)
    has_advantage = bool(advantage_reason) and not bool(disadvantage_reason)
    has_disadvantage = bool(disadvantage_reason) and not bool(advantage_reason)

    # Condition-based modifiers (poisoned → disadvantage per dnd_conditions)
    cond_mods = condition_modifies("check", profile)
    if cond_mods.get("advantage"):
        has_advantage = True
    if cond_mods.get("disadvantage"):
        has_disadvantage = True
    # If both come from conditions, they cancel (D&D rule: any advantage + any disadvantage = normal)
    if has_advantage and has_disadvantage:
        has_advantage = False
        has_disadvantage = False

    # Exhaustion / energy override (porting LOW_ENERGY_DIS_THRESHOLD logic from old mechanics.py)
    current_energy = safe_int(profile.get("Енергія", 1000), 1000)
    if current_energy < 200:  # LOW_ENERGY_DIS_THRESHOLD
        has_advantage = False
        has_disadvantage = True
        if not disadvantage_reason:
            disadvantage_reason = "[СИСТЕМНА ВТОМА: Гравець ледве тримається на ногах]"

    # --- 8. Check if action is free (no roll) ---
    is_free_action = (
        skill_used in ("None", "none", "", "Немає")
        and ability_used in ("None", "none", "", "Немає")
        and difficulty <= AUTO_SUCCESS_MAX_DC
    )

    if is_free_action:
        outcome = "SUCCESS"
        natural_roll = 0
        total_score = 0
        roll_str = "-"
        verdict_str = (
            f"MECHANICAL VERDICT: AUTO_SUCCESS (Free action / DC {difficulty}). "
            f"GM INFO: {verdict_text_llm}"
        )
        updates = _build_updates(
            raw_updates=raw_updates,
            action_type="standard",
            skill_used="None",
            ability_used="None",
            outcome=outcome,
            natural_roll=natural_roll,
            total_score=total_score,
            difficulty=difficulty,
            advantage_reason="",
            disadvantage_reason="",
            combat_imminent=combat_imminent,
            xp_award=0,
            reputation_delta=reputation_delta,
            reputation_target_npc=reputation_target_npc,
        )
        return verdict_str, updates

    # --- 9. Roll the dice ---
    # GODMODE check (porting from old mechanics.py)
    from config import GODMODE_USERS
    if user_id and user_id in GODMODE_USERS:
        natural_roll = 20
        rolls = [20]
        roll_str = "GODMODE"
        total_score = 20 + _compute_modifier(profile, ability_used, skill_used)
        outcome = "CRITICAL SUCCESS"
    else:
        # Resolve via dnd_core.skill_check if skill is valid, else ability_check
        if is_valid_skill(skill_used):
            result: CheckResult = skill_check(
                profile=profile,
                skill_name=skill_used,
                dc=difficulty,
                advantage=has_advantage,
                disadvantage=has_disadvantage,
            )
        elif ability_used not in ("None", "none", "", "Немає"):
            proficient = ability_used in profile.get("saves_proficient", [])
            result: CheckResult = ability_check(
                profile=profile,
                ability=ability_used,
                dc=difficulty,
                advantage=has_advantage,
                disadvantage=has_disadvantage,
                proficient=proficient,
            )
        else:
            # No skill, no ability — treat as free action
            result = _fake_auto_success_result(difficulty)

        natural_roll = result.natural
        total_score = result.total
        roll_str = result.roll_str

        # --- 10. Determine outcome with critical rules (D&D 5e nat 1/20) ---
        if result.critical == "success":
            outcome = "CRITICAL SUCCESS"
        elif result.critical == "fail":
            outcome = "CRITICAL FAILURE"
        elif result.success:
            outcome = "SUCCESS"
        else:
            outcome = "FAILURE"

    # --- 11. Format verdict string ---
    adv_label = " (Advantage)" if has_advantage else " (Disadvantage)" if has_disadvantage else ""
    verdict_str = (
        f"MECHANICAL VERDICT: {outcome}! "
        f"(Ability: {ability_used}, Skill: {skill_used}{adv_label}, "
        f"Roll: {roll_str}, Total: {total_score} vs DC {difficulty}). "
        f"GM INFO: {verdict_text_llm}"
    )

    # --- 12. Post-hoc: Scene_Tension escapes (porting A07c from mechanics.py) ---
    clocks_fix = dict(raw_updates.get("clocks_impact") or {})
    scene_changed = str(raw_updates.get("scene_impact", "none")).lower() not in ("none", "немає", "", "null")
    location_changed = str(raw_updates.get("location_impact", "none")).lower() not in (
        "none", "немає", "", "null", "в дорозі"
    )
    escape_keywords = ["тікаю", "біжу", "втікаю", "тікати", "рятуюсь", "ховаюсь", "тікаємо"]
    action_lower = user_input.lower()

    scene_tension_raw = (profile.get("Годинники") or {}).get("Scene_Tension", "0/4")
    scene_tension_val = int(str(scene_tension_raw).split("/")[0]) if isinstance(scene_tension_raw, str) else 0

    if (
        any(kw in action_lower for kw in escape_keywords)
        and scene_tension_val >= 3
        and (scene_changed or location_changed)
        and clocks_fix.get("Scene_Tension") != "clear"
    ):
        clocks_fix["Scene_Tension"] = "clear"

    raw_updates["clocks_impact"] = clocks_fix

    # --- 13. Build final updates dict ---
    # action_type detected by Worker prompt (GATE 7): "standard" or "training"
    detected_action_type = str(data.get("action_type", "standard")).strip().lower()
    if detected_action_type not in ("standard", "training"):
        detected_action_type = "standard"

    updates = _build_updates(
        raw_updates=raw_updates,
        action_type=detected_action_type,
        skill_used=skill_used,
        ability_used=ability_used,
        outcome=outcome,
        natural_roll=natural_roll,
        total_score=total_score,
        difficulty=difficulty,
        advantage_reason=advantage_reason,
        disadvantage_reason=disadvantage_reason,
        combat_imminent=combat_imminent,
        xp_award=xp_award,
        reputation_delta=reputation_delta,
        reputation_target_npc=reputation_target_npc,
    )

    # dice_roll key for UI compatibility (engine.py line 978 reads this)
    updates["dice_roll"] = roll_str
    updates["skill_val"] = _compute_modifier(profile, ability_used, skill_used)

    # Replicate critical hit bonuses from old mechanics.py
    if outcome == "CRITICAL SUCCESS":
        if "skill_impact" not in updates:
            updates["skill_impact"] = {}
        # Old system: +1 to skill on crit — preserve for handlers.py display
        # In D&D we don't auto-gain skill points, but we keep key for UI log
        updates["skill_impact"][skill_used if is_valid_skill(skill_used) else "None"] = 1

    elif outcome == "CRITICAL FAILURE":
        temp_penalty = random.randint(3, 8)  # Smaller than old 15-20; D&D is less punishing
        if "temp_debuff" not in updates:
            updates["temp_debuff"] = {}
        updates["temp_debuff"][skill_used] = temp_penalty

    # --- GUARD RAIL: suppress hp_damage_dice when user action is an attack on NPC.
    # Architectural invariant: hp_damage_dice in NORMAL pipeline = player self-damage
    # (falls, poison, environmental hazards). Player attacks on NPCs must route through
    # COMBAT mode (combat_imminent=True) and be resolved by the COMBAT FSM.
    # This guard is defense-in-depth: Layer 1 (Worker prompt) teaches the LLM;
    # this Layer 2 catches cases where the LLM ignores the prompt instruction.
    _user_lower = (user_input or "").lower()
    _has_attack_kw = any(kw in _user_lower for kw in _ATTACK_KEYWORDS)
    _hp_dmg_dice = updates.get("hp_damage_dice", "none")

    if _has_attack_kw and _hp_dmg_dice not in ("none", "None", ""):
        logger.warning(
            "[GUARD] Suppressed hp_damage_dice=%r for attack action "
            "(user_input=%r). Player attacks must use COMBAT mode.",
            _hp_dmg_dice,
            user_input[:80],
        )
        updates["hp_damage_dice"] = "none"
        # If LLM also forgot to set combat_imminent — force it so the FSM
        # transitions to COMBAT on the next turn.
        if not updates.get("combat_imminent"):
            updates["combat_imminent"] = True
            logger.info("[GUARD] Forced combat_imminent=True for attack action.")

    return verdict_str, updates


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_modifier(profile: dict, ability_used: str, skill_used: str) -> int:
    """Return total modifier (ability_mod + prof if applicable) for display."""
    if is_valid_skill(skill_used):
        try:
            ab = get_ability_for_skill(skill_used)
        except KeyError:
            ab = ability_used
        score = profile.get("ability_scores", {}).get(ab, 10)
        ab_mod = ability_modifier(score)
        level = profile.get("level", 1)
        pb = proficiency_bonus(level)
        skill_profs = profile.get("skill_profs", [])
        skill_expertise = profile.get("skill_expertise", [])
        if skill_used in skill_expertise:
            return ab_mod + pb * 2
        if skill_used in skill_profs:
            return ab_mod + pb
        return ab_mod
    elif ability_used not in ("None", "none", "", "Немає"):
        score = profile.get("ability_scores", {}).get(ability_used, 10)
        return ability_modifier(score)
    return 0


def _fake_auto_success_result(dc: int) -> "CheckResult":
    """Return a CheckResult representing an auto-success for free actions."""
    from core.dnd_core import CheckResult
    return CheckResult(
        total=dc + 1,
        natural=10,
        ability_mod=0,
        prof_bonus=0,
        dc=dc,
        success=True,
        critical="none",
        roll_str="-",
    )


def _build_updates(
    raw_updates: dict,
    action_type: str,
    skill_used: str,
    ability_used: str,
    outcome: str,
    natural_roll: int,
    total_score: int,
    difficulty: int,
    advantage_reason: str,
    disadvantage_reason: str,
    combat_imminent: bool,
    xp_award: int,
    reputation_delta: int,
    reputation_target_npc: str,
) -> dict:
    """Merge LLM raw_updates with mechanically computed fields."""
    updates = dict(raw_updates)  # shallow copy — raw_updates may be mutated by callers

    # Computed mechanics fields
    updates["action_type"] = action_type
    updates["skill_used"] = skill_used
    updates["ability_used"] = ability_used
    updates["outcome"] = outcome
    updates["natural_roll"] = natural_roll
    updates["total_score"] = total_score
    updates["difficulty"] = difficulty
    updates["advantage_reason"] = advantage_reason
    updates["disadvantage_reason"] = disadvantage_reason
    updates["combat_imminent"] = combat_imminent
    updates["xp_award"] = xp_award
    updates["reputation_delta"] = reputation_delta
    updates["reputation_target_npc"] = reputation_target_npc

    # Legacy keys for apply_system_impacts and engine.py compatibility.
    # hp_damage_dice / hp_heal_dice are NEW keys — apply_dnd_impacts resolves them.
    # health_impact and energy_impact map from legacy Worker; if LLM returned them — keep.
    updates.setdefault("health_impact", "none")
    updates.setdefault("energy_impact", "none")
    updates.setdefault("hp_damage_dice", raw_updates.get("hp_damage_dice", "none"))
    updates.setdefault("hp_heal_dice", raw_updates.get("hp_heal_dice", "none"))
    updates.setdefault("gold_impact", raw_updates.get("gold_impact", "none"))
    updates.setdefault("location_impact", raw_updates.get("location_impact", "none"))
    updates.setdefault("scene_impact", raw_updates.get("scene_impact", "none"))
    updates.setdefault("minutes_passed", raw_updates.get("minutes_passed", 5))
    updates.setdefault("inventory_new", raw_updates.get("inventory_new", []))
    updates.setdefault("inventory_lost", raw_updates.get("inventory_lost", []))
    updates.setdefault("clocks_impact", raw_updates.get("clocks_impact", {}))
    updates.setdefault("condition_apply", raw_updates.get("condition_apply", []))
    updates.setdefault("condition_remove", raw_updates.get("condition_remove", []))

    return updates


# ---------------------------------------------------------------------------
# AC recompute helper (P3 fix)
# ---------------------------------------------------------------------------

def recompute_ac(profile: dict) -> int:
    """Recompute Armour Class from equipped_armor and DEX modifier, write to profile["ac"].

    Rules:
    - No armor (or equipped_armor absent): 10 + DEX_mod (unarmored default).
    - Light armor: armor["ac_base"] + DEX_mod (no cap on DEX bonus).
    - Medium armor: armor["ac_base"] + min(DEX_mod, 2).
    - Heavy armor: armor["ac_base"] (no DEX bonus).
    - Shield: +2 regardless of armor type (if equipped_shield is True in profile).

    equipped_armor schema (OPTIONAL, new profile field):
    {
        "ac_base": int,                     # base AC of the armor (e.g. 16 for chainmail)
        "type": "light" | "medium" | "heavy",
    }

    Boundary conditions:
    - DEX_mod can be negative → still applied for light/medium (can reduce AC below base).
    - ac_base 0 or missing → treated as 10 (safety floor).
    - Unknown armor type → falls back to no-armor formula.

    Returns the new AC value and mutates profile["ac"].
    """
    dex_score = profile.get("ability_scores", {}).get("DEX", 10)
    dex_mod = ability_modifier(dex_score)

    armor = profile.get("equipped_armor")
    if armor and isinstance(armor, dict):
        ac_base = safe_int(armor.get("ac_base", 10), 10)
        if ac_base <= 0:
            ac_base = 10
        armor_type = str(armor.get("type", "light")).lower().strip()
        if armor_type == "heavy":
            new_ac = ac_base
        elif armor_type == "medium":
            new_ac = ac_base + min(dex_mod, 2)
        else:  # light or unknown → light formula
            new_ac = ac_base + dex_mod
    else:
        # No armor: unarmored default
        new_ac = 10 + dex_mod

    # Shield bonus
    if profile.get("equipped_shield", False):
        new_ac += 2

    profile["ac"] = new_ac
    return new_ac


# ---------------------------------------------------------------------------
# apply_dnd_impacts
# ---------------------------------------------------------------------------

def apply_dnd_impacts(profile: dict, updates: dict) -> tuple[dict, list[str]]:
    """
    D&D adapter for apply_system_impacts.

    Порядок обробки:
    1. D&D-специфічні теги: hp_damage_dice, hp_heal_dice, condition_apply/remove, xp_award.
    2. Делегує старому apply_system_impacts для: time, energy, gold, location,
       scene, inventory, clocks, skills.

    Примітка щодо hp_current:
    - Профіль нової системи використовує 'hp_current' / 'hp_max'.
    - Старий профіль використовує "Здоров'я" (0-100 HP scale).
    - Phase 5 підтримує ОБИДВА: якщо є 'hp_current' — оновлюємо його;
      інакше оновлюємо "Здоров'я" через health_impact для зворотної сумісності.

    Auto-applies level_up + ASI distribution to primary_abilities when xp_award crosses threshold.

    Повертає (updated_profile, logs).
    """
    from core.mechanics import apply_system_impacts  # lazy import, уникаємо circular

    logs: list[str] = []

    # --- Guard: skip hp_damage_dice/hp_heal_dice when called from COMBAT pipeline.
    # COMBAT mode manages player HP exclusively through dnd_combat.apply_damage +
    # cleanup_and_exit_combat. Any hp_damage_dice value in combat_updates is a
    # LLM hallucination or a legacy "none" placeholder — applying it here would
    # double-count damage or heal the player incorrectly.
    _is_combat_update = str(updates.get("action_type", "")).lower() == "combat"

    # --- 1. hp_damage_dice ---
    hp_damage_dice_tag = str(updates.get("hp_damage_dice", "none")).strip()
    if _is_combat_update and hp_damage_dice_tag not in ("none", "None", ""):
        logger.warning(
            f"[DND_ENGINE] apply_dnd_impacts: suppressing hp_damage_dice={hp_damage_dice_tag!r} "
            f"in COMBAT update — HP already managed by combat FSM."
        )
        hp_damage_dice_tag = "none"
    if hp_damage_dice_tag not in ("none", "None", ""):
        if hp_damage_dice_tag == "fatal":
            hp_dmg = 9999  # guaranteed death
        else:
            hp_dmg = _roll_dice_str(hp_damage_dice_tag)
        if hp_dmg > 0:
            if "hp_current" in profile:
                # D&D profile: hp_current / hp_max fields
                old_hp = safe_int(profile.get("hp_current", 0), 0)
                hp_max = safe_int(profile.get("hp_max", 1), 1)
                new_hp = max(0, old_hp - hp_dmg)
                profile["hp_current"] = new_hp
                # Sync legacy UI field: handlers.py reads "Здоров'я" (0-100 scale)
                profile["Здоров'я"] = round(100 * new_hp / hp_max) if hp_max > 0 else 0
                logs.append(f"HP: {old_hp} → {new_hp} (-{hp_dmg}, roll {hp_damage_dice_tag})")
            else:
                # Legacy profile: translate to health_impact tag for apply_system_impacts
                # Map damage amount to closest legacy tag
                if hp_dmg >= 50:
                    updates["health_impact"] = "dmg_heavy"
                elif hp_dmg >= 15:
                    updates["health_impact"] = "dmg_medium"
                elif hp_dmg >= 1:
                    updates["health_impact"] = "dmg_light"
                # apply_system_impacts will handle the actual subtraction

    # --- 2. hp_heal_dice ---
    hp_heal_dice_tag = str(updates.get("hp_heal_dice", "none")).strip()
    if _is_combat_update and hp_heal_dice_tag not in ("none", "None", ""):
        logger.warning(
            f"[DND_ENGINE] apply_dnd_impacts: suppressing hp_heal_dice={hp_heal_dice_tag!r} "
            f"in COMBAT update — HP already managed by combat FSM."
        )
        hp_heal_dice_tag = "none"
    if hp_heal_dice_tag not in ("none", "None", ""):
        hp_heal = _roll_dice_str(hp_heal_dice_tag)
        if hp_heal > 0:
            if "hp_current" in profile:
                old_hp = safe_int(profile.get("hp_current", 0), 0)
                hp_max = safe_int(profile.get("hp_max", 1), 1)
                new_hp = min(hp_max, old_hp + hp_heal)  # capped at hp_max
                profile["hp_current"] = new_hp
                # Sync legacy UI field: handlers.py reads "Здоров'я" (0-100 scale)
                profile["Здоров'я"] = round(100 * new_hp / hp_max) if hp_max > 0 else 0
                logs.append(f"HP: {old_hp} → {new_hp} (+{hp_heal}, roll {hp_heal_dice_tag})")
            else:
                # Legacy profile: use heal_small tag
                updates["health_impact"] = "heal_small"

    # --- 3. condition_apply ---
    condition_apply_list = updates.get("condition_apply") or []
    for entry in condition_apply_list:
        if not isinstance(entry, dict):
            continue
        cond_name = str(entry.get("name", "")).strip().lower()
        duration = safe_int(entry.get("duration", -1), -1)
        target_who = str(entry.get("target", "player")).strip().lower()
        if cond_name and target_who == "player":
            try:
                apply_condition(
                    profile,
                    cond_name,
                    duration_rounds=duration,
                    source=f"action_result",
                )
                logs.append(f"Стан: {cond_name} застосовано до гравця ({duration} раундів).")
            except ValueError as e:
                logger.warning(f"[DND_ENGINE] apply_condition failed: {e}")
        # NPC-targeting conditions are handled by GM_Logic → NPC updates pipeline (Phase 7)

    # --- 4. condition_remove ---
    condition_remove_list = updates.get("condition_remove") or []
    for entry in condition_remove_list:
        if not isinstance(entry, dict):
            continue
        cond_name = str(entry.get("name", "")).strip().lower()
        target_who = str(entry.get("target", "player")).strip().lower()
        if cond_name and target_who == "player":
            removed = remove_condition(profile, cond_name)
            if removed:
                logs.append(f"Стан: {cond_name} знято з гравця.")

    # --- 5. xp_award — auto-triggers level_up + ASI when threshold crossed ---
    xp_award = safe_int(updates.get("xp_award", 0), 0)
    if xp_award > 0:
        xp_result = award_xp(profile, xp_award, reason="action_reward")
        logs.append(f"XP: +{xp_award} (Всього: {xp_result.xp_after})")

        if xp_result.leveled_up:
            class_name = profile.get("class", "")
            if not class_name or class_name not in GOT_CLASSES:
                logger.warning(
                    f"[DND_ENGINE] Unknown or missing class {class_name!r} — "
                    f"falling back to 'Knight' for level_up."
                )
                class_name = "Knight"

            levelup_logs = apply_pending_levelups(
                profile,
                level_before=xp_result.level_before,
                levels_gained=xp_result.levels_gained,
                class_name=class_name,
            )
            logs.extend(levelup_logs)

    # --- 6. Delegate the rest to legacy apply_system_impacts ---
    # Передаємо оригінальний updates без D&D-специфічних ключів,
    # щоб apply_system_impacts обробив: time, energy, gold, location, scene, inventory, clocks.
    #
    # INVARIANT (Issue 3): gold_impact, inventory_new, inventory_lost, clocks_impact
    # are NOT in the exclusion list — they MUST reach apply_system_impacts for every
    # outcome including CRITICAL SUCCESS.  Never add them to this exclusion list.
    legacy_updates = {k: v for k, v in updates.items() if k not in (
        "hp_damage_dice", "hp_heal_dice",
        "condition_apply", "condition_remove",
        "xp_award", "natural_roll", "total_score",
        "advantage_reason", "disadvantage_reason",
        "combat_imminent", "ability_used",
        "action_type", "skill_val", "dice_roll",
        "skill_impact", "temp_debuff", "permanent_scar",
        "reputation_block",
        # D&D metadata consumed upstream; apply_system_impacts does not use them:
        "outcome", "skill_used", "reputation_delta", "reputation_target_npc",
        "difficulty",
    )}

    profile, legacy_logs = apply_system_impacts(profile, legacy_updates)
    logs.extend(legacy_logs)

    # --- 7. AC recompute when inventory changed (P3 fix) ---
    # apply_system_impacts already ran; check whether inventory was touched this turn.
    # Guard: only recompute for D&D-aware profiles (have equipped_armor or ability_scores).
    # Legacy profiles without these would get ac wiped to default (10+0=10) on inventory change.
    _inv_changed = bool(legacy_updates.get("inventory_new")) or bool(legacy_updates.get("inventory_lost"))
    _is_dnd_profile = "equipped_armor" in profile or "ability_scores" in profile
    if _inv_changed and _is_dnd_profile:
        old_ac = profile.get("ac", 10)
        new_ac = recompute_ac(profile)
        if new_ac != old_ac:
            logs.append(f"AC: {old_ac} -> {new_ac}")

    return profile, logs
