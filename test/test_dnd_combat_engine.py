"""test_dnd_combat_engine.py

Unit-тести для core/dnd_combat_engine.py (Phase 7 COMBAT pipeline adapter).

Стратегія мокування:
- model_worker.generate_content  → patch('core.dnd_combat_engine.model_worker.generate_content')
- clean_and_parse_json           → patch('core.dnd_combat_engine.clean_and_parse_json')
- combat state registry          → patch модулі core.combat_state.*
- dnd_combat pure functions      → patch де потрібна детерміністична поведінка
- Жодного реального LLM чи Google Sheets.
"""

import asyncio
import json
from contextlib import ExitStack
from typing import Optional
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run async coroutine in a fresh event loop."""
    return asyncio.run(coro)


def _mock_llm_response(data) -> MagicMock:
    """Return a MagicMock that mimics model_worker response with .text = JSON."""
    m = MagicMock()
    if isinstance(data, str):
        m.text = data
    else:
        m.text = json.dumps(data, ensure_ascii=False)
    return m


def _make_combat_state(
    chat_id: int = 1001,
    phase: str = "ONGOING",
    player_hp: int = 40,
    player_hp_max: int = 40,
    npc_data: Optional[dict] = None,
) -> MagicMock:
    """Build a minimal CombatState-like MagicMock for isolation tests."""
    from core.dnd_combat import CombatState, CombatantRef

    if npc_data is None:
        npc_data = {
            "Goblin": {
                "Name": "Goblin",
                "hp_current": 10,
                "hp_max": 10,
                "ac": 12,
                "Status": "Active",
                "conditions": [],
                "attacks": [{"name": "Scimitar", "to_hit": 4, "dmg": "1d6+2 slashing", "range": 5}],
                "cr": "1/4",
            }
        }

    player_snap = {
        "Ім'я": "TestPlayer",
        "hp_current": player_hp,
        "hp_max": player_hp_max,
        "ac": 16,
        "conditions": [],
        "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        "level": 3,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
    }

    refs = []
    for i, npc_name in enumerate(npc_data.keys()):
        refs.append(CombatantRef(kind="npc", name=npc_name, init_roll=15 - i))
    refs.append(CombatantRef(kind="player", name="TestPlayer", init_roll=10))

    state = CombatState(
        chat_id=chat_id,
        round=1,
        initiative_order=refs,
        current_actor_idx=0,
        npcs=npc_data,
        player_snapshot=player_snap,
        log=["--- Раунд 1 ---"],
    )
    state.phase = phase
    return state


def _minimal_player_profile(**overrides) -> dict:
    """Return minimal player profile for tests."""
    base = {
        "Ім'я": "TestPlayer",
        "Здоров'я": 100,
        "Енергія": 800,
        "Особисте Золото": 50,
        "Поточне місцезнаходження": "Вінтерфелл",
        "Поточна сцена": "Courtyard",
        "Регіон": "Північ",
        "Ігровий час": "Day 1, Morning",
        "Інвентар": "Longsword",
        "Зброя": "Longsword",
        "Броня": "Chainmail",
        "Транспорт": "",
        "Світогляд": "Neutral",
        "Риси": "Brave",
        "Вади": "Stubborn",
        "Вороги": "",
        "Друзі": "",
        "Годинники": {"Scene_Tension": "0/4"},
        "level": 3,
        "hp_current": 40,
        "hp_max": 40,
        "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        "conditions": [],
        "equipment": {"weapon_main": "Longsword (1d8+3 slashing)"},
        "mode": "NORMAL",
    }
    base.update(overrides)
    return base


# ===========================================================================
# Тест 1: parse_player_combat_intent — валідний JSON від LLM
# ===========================================================================

def test_parse_player_combat_intent_valid_json():
    """LLM повертає валідний JSON {intent:'attack', target_npc:'Goblin'} → parsed dict."""
    from core.dnd_combat_engine import parse_player_combat_intent

    llm_data = {
        "intent": "attack",
        "target_npc": "Goblin",
        "weapon": "Longsword",
        "spell_or_ability": None,
        "tactic": "normal",
        "move_to": None,
        "verdict_text": "Player swings at the goblin.",
        "reasoning": "Goblin is closest.",
    }

    state = _make_combat_state()
    profile = _minimal_player_profile()

    with patch("core.dnd_combat_engine.model_worker.generate_content", return_value=_mock_llm_response(llm_data)):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=llm_data):
            with patch("core.dnd_combat_engine.build_combat_round_prompt", return_value="MOCK_PROMPT"):
                result = _run(parse_player_combat_intent("Атакую гобліна", profile, state))

    assert isinstance(result, dict)
    assert result["intent"] == "attack"
    assert result["target_npc"] == "Goblin"


# ===========================================================================
# Тест 2: parse_player_combat_intent — невалідний JSON → fallback dodge
# ===========================================================================

def test_parse_player_combat_intent_invalid_json_returns_fallback():
    """LLM повертає невалідний JSON → повертається fallback dict з intent='dodge'."""
    from core.dnd_combat_engine import parse_player_combat_intent

    state = _make_combat_state()
    profile = _minimal_player_profile()

    with patch("core.dnd_combat_engine.model_worker.generate_content", return_value=_mock_llm_response("NOT_JSON")):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=None):
            with patch("core.dnd_combat_engine.build_combat_round_prompt", return_value="MOCK_PROMPT"):
                result = _run(parse_player_combat_intent("щось незрозуміле", profile, state))

    assert isinstance(result, dict)
    assert result["intent"] == "dodge", f"Expected 'dodge' fallback, got: {result['intent']!r}"


# ===========================================================================
# Тест 3: parse_player_combat_intent — LLM падає → fallback dodge
# ===========================================================================

def test_parse_player_combat_intent_llm_exception_returns_fallback():
    """LLM виклик падає з Exception → повертається fallback dict з intent='dodge'."""
    from core.dnd_combat_engine import parse_player_combat_intent

    state = _make_combat_state()
    profile = _minimal_player_profile()

    with patch("core.dnd_combat_engine.model_worker.generate_content", side_effect=Exception("Network error")):
        with patch("core.dnd_combat_engine.build_combat_round_prompt", return_value="MOCK_PROMPT"):
            result = _run(parse_player_combat_intent("Атакую", profile, state))

    assert isinstance(result, dict)
    assert result["intent"] == "dodge", f"Expected 'dodge' fallback on exception, got: {result['intent']!r}"


# ===========================================================================
# Тест 4: decide_spotlight_npcs — 1 NPC alive → returns [npc_name]
# ===========================================================================

def test_decide_spotlight_npcs_single_alive_npc():
    """1 живий NPC → returns [npc_name]."""
    from core.dnd_combat_engine import decide_spotlight_npcs

    state = _make_combat_state(npc_data={
        "Goblin": {"hp_current": 10, "hp_max": 10, "Status": "Active", "conditions": []}
    })

    result = _run(decide_spotlight_npcs(state))

    assert result == ["Goblin"], f"Expected ['Goblin'], got {result}"


# ===========================================================================
# Тест 5: decide_spotlight_npcs — 3 NPCs alive → returns top 2
# ===========================================================================

def test_decide_spotlight_npcs_three_npcs_returns_top_two():
    """3 живих NPC → returns тільки перших 2 (cap max_spotlight=2)."""
    from core.dnd_combat_engine import decide_spotlight_npcs
    from core.dnd_combat import CombatantRef, CombatState

    npc_data = {
        "Goblin A": {"hp_current": 10, "hp_max": 10, "Status": "Active", "conditions": []},
        "Goblin B": {"hp_current": 8, "hp_max": 10, "Status": "Active", "conditions": []},
        "Goblin C": {"hp_current": 6, "hp_max": 10, "Status": "Active", "conditions": []},
    }
    refs = [
        CombatantRef(kind="npc", name="Goblin A", init_roll=20),
        CombatantRef(kind="npc", name="Goblin B", init_roll=15),
        CombatantRef(kind="npc", name="Goblin C", init_roll=10),
        CombatantRef(kind="player", name="TestPlayer", init_roll=12),
    ]
    player_snap = {"Ім'я": "TestPlayer", "hp_current": 40, "hp_max": 40, "ac": 16, "conditions": []}
    state = CombatState(
        chat_id=1001, round=1, initiative_order=refs, current_actor_idx=0,
        npcs=npc_data, player_snapshot=player_snap, log=[]
    )

    result = _run(decide_spotlight_npcs(state, max_spotlight=2))

    assert len(result) == 2, f"Expected 2 spotlight NPCs, got {len(result)}: {result}"
    assert all(name in npc_data for name in result), f"Spotlight NPCs must be from npc_data: {result}"


# ===========================================================================
# Тест 6: decide_spotlight_npcs — всі NPC мертві → returns []
# ===========================================================================

def test_decide_spotlight_npcs_all_dead_returns_empty():
    """Всі NPC мертві → returns []."""
    from core.dnd_combat_engine import decide_spotlight_npcs

    state = _make_combat_state(npc_data={
        "Dead Goblin": {"hp_current": 0, "hp_max": 10, "Status": "Dead", "conditions": []},
        "Fled Goblin": {"hp_current": 5, "hp_max": 10, "Status": "Fled", "conditions": []},
    })

    result = _run(decide_spotlight_npcs(state))

    assert result == [], f"Expected [], got {result}"


# ===========================================================================
# Тест 7: decide_spotlight_npcs — spotlight cap max=2 дотримується
# ===========================================================================

def test_decide_spotlight_npcs_cap_enforced():
    """5 живих NPC, max_spotlight=2 → returns не більше 2."""
    from core.dnd_combat_engine import decide_spotlight_npcs
    from core.dnd_combat import CombatantRef, CombatState

    npc_names = [f"Goblin_{i}" for i in range(5)]
    npc_data = {
        name: {"hp_current": 10, "hp_max": 10, "Status": "Active", "conditions": []}
        for name in npc_names
    }
    refs = [CombatantRef(kind="npc", name=name, init_roll=20 - i) for i, name in enumerate(npc_names)]
    refs.append(CombatantRef(kind="player", name="TestPlayer", init_roll=10))
    player_snap = {"Ім'я": "TestPlayer", "hp_current": 40, "hp_max": 40, "ac": 16, "conditions": []}
    state = CombatState(
        chat_id=1001, round=1, initiative_order=refs, current_actor_idx=0,
        npcs=npc_data, player_snapshot=player_snap, log=[]
    )

    result = _run(decide_spotlight_npcs(state, max_spotlight=2))

    assert len(result) <= 2, f"Spotlight cap max=2 violated: got {len(result)} NPCs: {result}"


# ===========================================================================
# Тест 8: execute_npc_actions — 2 spotlight NPCs, обидва attack
# ===========================================================================

def test_execute_npc_actions_two_spotlight_both_attack():
    """2 spotlight NPCs з action='attack' → обидва виконують npc_attack."""
    from core.dnd_combat_engine import execute_npc_actions

    npc_data = {
        "Goblin A": {
            "hp_current": 10, "hp_max": 10, "Status": "Active", "conditions": [],
            "attacks": [{"name": "Scimitar", "to_hit": 4, "dmg": "1d6+2 slashing", "range": 5}],
        },
        "Goblin B": {
            "hp_current": 8, "hp_max": 8, "Status": "Active", "conditions": [],
            "attacks": [{"name": "Spear", "to_hit": 3, "dmg": "1d6+1 piercing", "range": 5}],
        },
    }
    state = _make_combat_state(npc_data=npc_data)

    llm_response = {
        "actions": [
            {"npc_name": "Goblin A", "action": "attack", "target": "player"},
            {"npc_name": "Goblin B", "action": "attack", "target": "player"},
        ]
    }

    with patch("core.dnd_combat_engine.model_worker.generate_content", return_value=_mock_llm_response(llm_response)):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=llm_response):
            with patch("core.dnd_combat_engine.build_npc_combat_action_prompt", return_value="MOCK_PROMPT"):
                results = _run(execute_npc_actions(state, ["Goblin A", "Goblin B"]))

    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    action_taken_values = {r["npc_name"]: r["action_taken"] for r in results}
    assert action_taken_values["Goblin A"] == "attack"
    assert action_taken_values["Goblin B"] == "attack"


# ===========================================================================
# Тест 9: execute_npc_actions — NPC вибирає "flee" → Status='Fled'
# ===========================================================================

def test_execute_npc_actions_npc_flee_sets_status_fled():
    """NPC вибирає action='flee' → npc['Status'] == 'Fled'."""
    from core.dnd_combat_engine import execute_npc_actions

    npc_data = {
        "Cowardly Goblin": {
            "hp_current": 2, "hp_max": 10, "Status": "Active", "conditions": [],
            "attacks": [{"name": "Dagger", "to_hit": 2, "dmg": "1d4 piercing", "range": 5}],
        }
    }
    state = _make_combat_state(npc_data=npc_data)

    llm_response = {
        "actions": [
            {"npc_name": "Cowardly Goblin", "action": "flee", "target": "none"},
        ]
    }

    with patch("core.dnd_combat_engine.model_worker.generate_content", return_value=_mock_llm_response(llm_response)):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=llm_response):
            with patch("core.dnd_combat_engine.build_npc_combat_action_prompt", return_value="MOCK_PROMPT"):
                results = _run(execute_npc_actions(state, ["Cowardly Goblin"]))

    assert len(results) == 1
    assert results[0]["action_taken"] == "flee"
    assert state.npcs["Cowardly Goblin"]["Status"] == "Fled", (
        f"NPC that chose 'flee' must have Status='Fled', got: {state.npcs['Cowardly Goblin']['Status']!r}"
    )


# ===========================================================================
# Тест 10: execute_combat_round — повний раунд, гравець атакує, NPC реагує → ONGOING
# ===========================================================================

def test_execute_combat_round_full_round_ongoing():
    """Повний раунд: player attack → 1 NPC реагує → end_round → ONGOING phase."""
    from core.dnd_combat_engine import execute_combat_round
    from core.combat_state import set_combat_state

    chat_id = 2001
    state = _make_combat_state(chat_id=chat_id)
    set_combat_state(chat_id, state)

    player_intent = {
        "intent": "attack", "target_npc": "Goblin",
        "weapon": "Longsword", "spell_or_ability": None,
        "tactic": "normal", "move_to": None,
        "verdict_text": "Swings at goblin.", "reasoning": "obvious",
    }
    npc_action_response = {
        "actions": [{"npc_name": "Goblin", "action": "attack", "target": "player"}]
    }

    with patch("core.dnd_combat_engine.parse_player_combat_intent", new=AsyncMock(return_value=player_intent)):
        with patch("core.dnd_combat_engine.decide_spotlight_npcs", new=AsyncMock(return_value=["Goblin"])):
            with patch("core.dnd_combat_engine.model_worker.generate_content", return_value=_mock_llm_response(npc_action_response)):
                with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=npc_action_response):
                    with patch("core.dnd_combat_engine.build_npc_combat_action_prompt", return_value="MOCK_PROMPT"):
                        profile = _minimal_player_profile(mode="COMBAT")
                        combat_log, updates, npc_results = _run(
                            execute_combat_round(chat_id, "Атакую гобліна!", profile)
                        )

    # After 1 round with alive goblin, phase should be ONGOING (unless goblin was killed)
    # We just verify the return contract
    assert isinstance(combat_log, str)
    assert isinstance(updates, dict)
    assert isinstance(npc_results, list)
    assert "combat_phase" in updates
    assert "combat_ended" in updates

    # Cleanup
    from core.combat_state import clear_combat_state
    clear_combat_state(chat_id)


# ===========================================================================
# Тест 11: execute_combat_round — гравець вбиває останнього NPC → PLAYER_WIN
# ===========================================================================

def test_execute_combat_round_player_kills_last_npc_player_win():
    """Гравець вбиває останнього NPC → phase=PLAYER_WIN, combat_ended=True."""
    from core.dnd_combat_engine import execute_combat_round
    from core.combat_state import set_combat_state, clear_combat_state

    chat_id = 2002

    # Goblin з 1 HP — один удар вбиває
    npc_data = {
        "WeakGoblin": {
            "hp_current": 1, "hp_max": 10, "Status": "Active", "conditions": [],
            "attacks": [{"name": "Fist", "to_hit": 0, "dmg": "1d4 bludgeoning", "range": 5}],
            "cr": "1/4",
        }
    }
    state = _make_combat_state(chat_id=chat_id, npc_data=npc_data)
    set_combat_state(chat_id, state)

    player_intent = {
        "intent": "attack", "target_npc": "WeakGoblin",
        "weapon": "Longsword", "spell_or_ability": None,
        "tactic": "normal", "move_to": None,
        "verdict_text": "Kills goblin.", "reasoning": "easy target",
    }

    def _force_kill_player_attack(cs, target_name, weapon_name, advantage=False, disadvantage=False):
        """Force-kill the NPC via apply_damage."""
        from core.dnd_combat import apply_damage, AttackResult
        apply_damage(cs, target_name, 999, "slashing")  # guaranteed kill
        return AttackResult(
            attacker="TestPlayer", target=target_name, weapon=weapon_name,
            to_hit_total=20, natural_roll=15, target_ac=12,
            hit=True, critical=False, damage_total=999,
            damage_dice_str="1d8+3", damage_type="slashing",
            log_line=f"TestPlayer kills {target_name} instantly.",
        )

    with patch("core.dnd_combat_engine.parse_player_combat_intent", new=AsyncMock(return_value=player_intent)):
        with patch("core.dnd_combat_engine.decide_spotlight_npcs", new=AsyncMock(return_value=[])):
            with patch("core.dnd_combat_engine.player_attack", side_effect=_force_kill_player_attack):
                profile = _minimal_player_profile(mode="COMBAT")
                combat_log, updates, npc_results = _run(
                    execute_combat_round(chat_id, "Вбиваю гобліна!", profile)
                )

    phase = updates.get("combat_phase")
    assert phase == "PLAYER_WIN", (
        f"After killing last NPC, phase must be PLAYER_WIN. Got: {phase!r}"
    )
    assert updates.get("combat_ended") is True

    clear_combat_state(chat_id)


# ===========================================================================
# Тест 12: execute_combat_round — HP гравця → 0 → phase=PLAYER_DEAD
# ===========================================================================

def test_execute_combat_round_player_dead_when_hp_zero():
    """HP гравця падає до 0 → phase=PLAYER_DEAD."""
    from core.dnd_combat_engine import execute_combat_round
    from core.combat_state import set_combat_state, clear_combat_state
    from core.dnd_combat import apply_damage

    chat_id = 2003

    npc_data = {
        "BossKnight": {
            "hp_current": 50, "hp_max": 50, "Status": "Active", "conditions": [],
            "attacks": [{"name": "Greatsword", "to_hit": 6, "dmg": "2d6+4 slashing", "range": 5}],
        }
    }
    state = _make_combat_state(chat_id=chat_id, player_hp=1, player_hp_max=40, npc_data=npc_data)
    set_combat_state(chat_id, state)

    # Force player to dodge (no attack this round)
    player_intent = {
        "intent": "dodge", "target_npc": None,
        "weapon": None, "spell_or_ability": None,
        "tactic": "normal", "move_to": None,
        "verdict_text": "Player dodges.", "reasoning": "cautious",
    }

    def _force_kill_player_in_npc_action(cs, npc_name, target="player"):
        """Force-kill the player via apply_damage in NPC action."""
        from core.dnd_combat import AttackResult
        apply_damage(cs, "player", 999, "slashing")  # guaranteed kill
        return AttackResult(
            attacker=npc_name, target="player", weapon="Greatsword",
            to_hit_total=25, natural_roll=19, target_ac=16,
            hit=True, critical=False, damage_total=999,
            damage_dice_str="2d6+4", damage_type="slashing",
            log_line=f"{npc_name} mortally wounds TestPlayer.",
        )

    npc_action_response = {
        "actions": [{"npc_name": "BossKnight", "action": "attack", "target": "player"}]
    }

    with patch("core.dnd_combat_engine.parse_player_combat_intent", new=AsyncMock(return_value=player_intent)):
        with patch("core.dnd_combat_engine.decide_spotlight_npcs", new=AsyncMock(return_value=["BossKnight"])):
            with patch("core.dnd_combat_engine.npc_attack", side_effect=_force_kill_player_in_npc_action):
                with patch("core.dnd_combat_engine.model_worker.generate_content", return_value=_mock_llm_response(npc_action_response)):
                    with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=npc_action_response):
                        with patch("core.dnd_combat_engine.build_npc_combat_action_prompt", return_value="MOCK_PROMPT"):
                            profile = _minimal_player_profile(mode="COMBAT")
                            combat_log, updates, npc_results = _run(
                                execute_combat_round(chat_id, "Захищаюсь!", profile)
                            )

    phase = updates.get("combat_phase")
    assert phase == "PLAYER_DEAD", (
        f"When player HP=0, phase must be PLAYER_DEAD. Got: {phase!r}"
    )

    clear_combat_state(chat_id)


# ===========================================================================
# Тест 13: initiate_combat_from_normal — profile mode='NORMAL', scene_npcs=[1 NPC]
# ===========================================================================

def test_initiate_combat_from_normal_sets_combat_mode():
    """profile mode='NORMAL', scene_npcs=[1 NPC] → returns CombatState, profile['mode']=='COMBAT'."""
    from core.dnd_combat_engine import initiate_combat_from_normal
    from core.combat_state import clear_combat_state, get_combat_state

    chat_id = 3001
    profile = _minimal_player_profile(mode="NORMAL")
    scene_npcs = [
        {"Name": "Guard", "hp_current": 15, "hp_max": 15, "ac": 14,
         "conditions": [], "attacks": [], "Status": "Active"}
    ]

    state = _run(initiate_combat_from_normal(chat_id, profile, scene_npcs))

    assert profile["mode"] == "COMBAT", (
        f"After initiate_combat_from_normal, profile['mode'] must be 'COMBAT'. Got: {profile['mode']!r}"
    )
    assert state is not None
    assert "Guard" in state.npcs, f"NPC 'Guard' must be in CombatState.npcs. Got: {list(state.npcs.keys())}"

    stored = get_combat_state(chat_id)
    assert stored is state, "CombatState must be stored in registry."

    clear_combat_state(chat_id)


# ===========================================================================
# Тест 14: initiate_combat_from_normal — scene_npcs=[] → CombatState без NPC
# ===========================================================================

def test_initiate_combat_from_normal_empty_npcs():
    """scene_npcs=[] → initiate_combat_from_normal повертає CombatState з порожнім npcs."""
    from core.dnd_combat_engine import initiate_combat_from_normal
    from core.combat_state import clear_combat_state

    chat_id = 3002
    profile = _minimal_player_profile(mode="NORMAL")

    state = _run(initiate_combat_from_normal(chat_id, profile, []))

    assert profile["mode"] == "COMBAT"
    # No NPCs — combat state has empty npcs dict
    assert state.npcs == {}, (
        f"With empty scene_npcs, CombatState.npcs must be {{}}. Got: {state.npcs}"
    )

    clear_combat_state(chat_id)


# ===========================================================================
# Тест 15: cleanup_and_exit_combat — PLAYER_WIN → xp gained, npc_updates з Status='Dead'
# ===========================================================================

def test_cleanup_and_exit_combat_player_win_xp_and_dead_npc():
    """PLAYER_WIN → updates містить xp gained > 0 та npc_updates з Status='Dead'."""
    from core.dnd_combat_engine import cleanup_and_exit_combat
    from core.combat_state import set_combat_state, clear_combat_state

    chat_id = 4001
    npc_data = {
        "DeadGoblin": {
            "hp_current": 0, "hp_max": 10, "Status": "Dead",
            "conditions": [], "cr": "1/4",
        }
    }
    state = _make_combat_state(chat_id=chat_id, phase="PLAYER_WIN", npc_data=npc_data)
    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT", hp_current=30, hp_max=40)

    # award_xp is imported inside cleanup_and_exit_combat via
    # `from core.dnd_progression import award_xp` — patch at source module.
    with patch("core.dnd_progression.award_xp") as mock_award_xp:
        updates = _run(cleanup_and_exit_combat(chat_id, profile))

    assert updates.get("player_xp_gained", 0) > 0, (
        f"PLAYER_WIN must yield xp > 0 (from 1/4 CR Goblin = 50 xp). Got: {updates.get('player_xp_gained')}"
    )

    npc_updates = updates.get("npc_updates", [])
    assert len(npc_updates) >= 1, f"npc_updates must contain at least one entry. Got: {npc_updates}"

    dead_entries = [u for u in npc_updates if u.get("Status") == "Dead"]
    assert dead_entries, (
        f"npc_updates must have at least one entry with Status='Dead'. Got: {npc_updates}"
    )
    dead_hp = dead_entries[0].get("hp_current", -1)
    assert dead_hp == 0, f"Dead NPC must have hp_current=0 in npc_updates. Got: {dead_hp}"


# ===========================================================================
# Тест 16: cleanup_and_exit_combat — PLAYER_DEAD → player_hp_change негативний
# ===========================================================================

def test_cleanup_and_exit_combat_player_dead_negative_hp_change():
    """PLAYER_DEAD → updates['player_hp_change'] <= 0."""
    from core.dnd_combat_engine import cleanup_and_exit_combat
    from core.combat_state import set_combat_state, clear_combat_state

    chat_id = 4002

    state = _make_combat_state(chat_id=chat_id, phase="PLAYER_DEAD", player_hp=0, player_hp_max=40)
    # Simulate that initial HP was 40 (set via __post_init__)
    state._initial_player_hp = 40
    state.player_snapshot["hp_current"] = 0

    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT", hp_current=0, hp_max=40)

    with patch("core.dnd_progression.award_xp"):
        updates = _run(cleanup_and_exit_combat(chat_id, profile))

    hp_change = updates.get("player_hp_change", 0)
    assert hp_change <= 0, (
        f"PLAYER_DEAD must have player_hp_change <= 0. Got: {hp_change}"
    )


# ===========================================================================
# Тест 17: cleanup_and_exit_combat — profile['mode'] == 'NORMAL' після cleanup
# ===========================================================================

def test_cleanup_and_exit_combat_resets_mode_to_normal():
    """Після cleanup_and_exit_combat profile['mode'] == 'NORMAL'."""
    from core.dnd_combat_engine import cleanup_and_exit_combat
    from core.combat_state import set_combat_state, clear_combat_state

    chat_id = 4003
    state = _make_combat_state(chat_id=chat_id, phase="PLAYER_WIN")
    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT")

    with patch("core.dnd_progression.award_xp"):
        _run(cleanup_and_exit_combat(chat_id, profile))

    assert profile["mode"] == "NORMAL", (
        f"After cleanup_and_exit_combat, profile['mode'] must be 'NORMAL'. Got: {profile['mode']!r}"
    )


# ===========================================================================
# Тест 18: cleanup_and_exit_combat — clear_combat_state + cleanup_lock виконані
# ===========================================================================

def test_cleanup_and_exit_combat_clears_state_and_lock():
    """cleanup_and_exit_combat викликає clear_combat_state і cleanup_lock."""
    from core.dnd_combat_engine import cleanup_and_exit_combat
    from core.combat_state import set_combat_state, get_combat_state

    chat_id = 4004
    state = _make_combat_state(chat_id=chat_id, phase="PLAYER_WIN")
    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT")

    with patch("core.dnd_progression.award_xp"):
        with patch("core.dnd_combat_engine.clear_combat_state") as mock_clear:
            with patch("core.dnd_combat_engine.cleanup_lock") as mock_cleanup_lock:
                _run(cleanup_and_exit_combat(chat_id, profile))

    mock_clear.assert_called_once_with(chat_id)
    mock_cleanup_lock.assert_called_once_with(chat_id)


# ===========================================================================
# Тест 19 (Integration): process_game_turn — mode='COMBAT' → COMBAT pipeline
# ===========================================================================

def test_process_game_turn_combat_mode_uses_combat_pipeline():
    """profile mode='COMBAT', valid combat_state → process_game_turn веде COMBAT гілкою."""
    from core.combat_state import set_combat_state, clear_combat_state

    chat_id = 5001
    state = _make_combat_state(chat_id=chat_id)
    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT")

    combat_log_str = "--- Раунд 1 ---\nTestPlayer attacks Goblin."
    combat_upd = {
        "combat_phase": "ONGOING",
        "combat_ended": False,
        "combat_round": 1,
        "player_hp_current": 40,
        "alive_npc_names": ["Goblin"],
        "action_type": "combat",
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
        "outcome": "SUCCESS",
    }

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["Battle continues."],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "Attack", "intent": "Attack goblin"},
            {"button": "Dodge", "intent": "Dodge"},
            {"button": "Flee", "intent": "Run away"},
            {"button": "Item", "intent": "Use item"},
        ],
    }

    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json, ensure_ascii=False)
    narrator_resp = MagicMock()
    narrator_resp.text = "The battle rages on. Swords clash and sparks fly."

    execute_round_mock = AsyncMock(return_value=(combat_log_str, combat_upd, []))

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        # COMBAT pipeline — patch the binding in core.engine (top-level import)
        patch("core.engine.execute_combat_round", new=execute_round_mock),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(chat_id=chat_id, user_input="Атакую!", narrator_queue=None)

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = _run(_run_engine())

    execute_round_mock.assert_called_once()

    clear_combat_state(chat_id)


# ===========================================================================
# Тест 20 (Integration): combat_imminent=True → initiate_combat_from_normal
# ===========================================================================

def test_process_game_turn_combat_imminent_triggers_initiation():
    """combat_imminent=true з NORMAL pipeline → initiate_combat_from_normal викликається."""
    from core.combat_state import clear_combat_state

    chat_id = 5002
    profile = _minimal_player_profile(mode="NORMAL")

    # Worker відповідь з combat_imminent=True
    worker_updates = {
        "action_type": "standard",
        "skill_used": "None",
        "difficulty": 70,
        "circumstance": "NORMAL",
        "outcome": "SUCCESS",
        "dice_roll": "50",
        "skill_val": 20,
        "total_score": 70,
        "reputation_delta": 0,
        "reputation_target_npc": None,
        "minutes_passed": 5,
        "location_impact": "none",
        "scene_impact": "none",
        "health_impact": "none",
        "energy_impact": "none",
        "gold_impact": "none",
        "inventory_new": [],
        "inventory_lost": [],
        "clocks_impact": {},
        "combat_imminent": True,
    }

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["An enemy approaches."],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "Attack", "intent": "Attack"},
            {"button": "Flee", "intent": "Flee"},
            {"button": "Talk", "intent": "Talk"},
            {"button": "Wait", "intent": "Wait"},
        ],
    }
    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json, ensure_ascii=False)
    narrator_resp = MagicMock()
    narrator_resp.text = "An enemy emerges from the shadows. Combat is imminent."

    initiate_mock = AsyncMock(return_value=_make_combat_state(chat_id=chat_id))

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.resolve_normal_action", new=AsyncMock(return_value=("VERDICT", worker_updates))),
        patch("core.engine.get_location_npcs", return_value=("Guard is here.", ["Guard"], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        patch("core.dnd_combat_engine.initiate_combat_from_normal", new=initiate_mock),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(chat_id=chat_id, user_input="Провокую охоронця!", narrator_queue=None)

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        _run(_run_engine())

    initiate_mock.assert_called_once()

    clear_combat_state(chat_id)


# ===========================================================================
# КРИТИЧНИЙ ТЕСТ (BUG PROBE): NPC DB write path для COMBAT
# ===========================================================================

def test_combat_npc_death_persisted_to_sheets_via_background_task():
    """
    BUG PROBE: Перевіряємо, чи dead NPC з combat cleanup потрапляють в Sheets.

    flow:
    1. COMBAT round ends → PLAYER_WIN → cleanup_and_exit_combat повертає
       updates з npc_updates=[{Name:'Goblin', Status:'Dead', hp_current:0}]
    2. engine.py зчитує npc_changes = ai_data.get('npc_updates', [])
       де ai_data = GM_Logic JSON, а НЕ combat_updates.
    3. background_task отримує npc_changes_arg і викликає update_npcs_in_db.

    Якщо dead NPC НЕ потрапляють у npc_changes → update_npcs_in_db викликається
    з порожнім списком → dead NPCs НЕ записуються в Sheets.

    Цей тест перевіряє, що update_npcs_in_db отримує dead NPC entries.
    """
    from core.combat_state import set_combat_state, clear_combat_state

    chat_id = 6001
    npc_data = {
        "BattleGoblin": {
            "hp_current": 0, "hp_max": 10, "Status": "Dead",
            "conditions": [], "cr": "1/4",
        }
    }
    state = _make_combat_state(chat_id=chat_id, phase="PLAYER_WIN", npc_data=npc_data)
    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT", hp_current=35, hp_max=40)

    combat_log_str = "BattleGoblin defeated."
    # execute_combat_round returns PLAYER_WIN in updates
    combat_upd = {
        "combat_phase": "PLAYER_WIN",
        "combat_ended": True,
        "combat_round": 1,
        "player_hp_current": 35,
        "alive_npc_names": [],
        "action_type": "combat",
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
        "outcome": "SUCCESS",
    }

    # cleanup_and_exit_combat includes npc_updates in its return value
    cleanup_result = {
        "combat_ended": True,
        "player_hp_change": -5,
        "player_xp_gained": 50,
        "npcs_status_changes": [
            {"name": "BattleGoblin", "old_status": "Active", "new_status": "Dead", "final_hp": 0}
        ],
        "combat_summary": "Гравець переміг за 1 раундів",
        "npc_updates": [{"Name": "BattleGoblin", "Status": "Dead", "hp_current": 0}],
    }

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["Victory!"],
        "companion_npcs": [], "npc_updates": [],  # GM_Logic does NOT know about combat deaths
        "suggested_actions": [
            {"button": "Loot", "intent": "Loot goblin"},
            {"button": "Rest", "intent": "Rest"},
            {"button": "Move on", "intent": "Move on"},
            {"button": "Search", "intent": "Search area"},
        ],
    }
    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json, ensure_ascii=False)
    narrator_resp = MagicMock()
    narrator_resp.text = "The goblin falls. Victory is yours. The courtyard is silent once more."

    update_npcs_calls = []

    async def _spy_update_npcs(chat_id_arg, npc_changes_arg, **kwargs):
        update_npcs_calls.append(npc_changes_arg)

    # captured_tasks is declared before patches so the closure in _capture_create_task
    # and the task-execution loop share the same list reference.
    captured_tasks = []

    def _capture_create_task(coro):
        captured_tasks.append(coro)
        return MagicMock()

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.get_location_npcs", return_value=("BattleGoblin is here.", ["BattleGoblin"], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        # Execute combat round — patch the binding in core.engine (top-level import)
        patch("core.engine.execute_combat_round", new=AsyncMock(return_value=(combat_log_str, combat_upd, []))),
        # Cleanup — patch the binding in core.engine (top-level import)
        patch("core.engine.cleanup_and_exit_combat", new=AsyncMock(return_value=cleanup_result)),
        # Spy on update_npcs_in_db
        patch("core.engine.update_npcs_in_db", new=_spy_update_npcs),
        patch("core.engine.save_user_data", new=AsyncMock()),
        patch("core.engine.summarize_full_turn", new=AsyncMock(return_value="Turn summary")),
        # Capture background tasks so we can run them while patches are still active
        patch("core.engine._run_bg_task", side_effect=_capture_create_task),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(chat_id=chat_id, user_input="Добиваю гобліна!", narrator_queue=None)

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        _run(_run_engine())

        # Run captured background tasks INSIDE the ExitStack so all patches remain active.
        # This is critical: update_npcs_in_db spy must be active when the task runs.
        for task in captured_tasks:
            try:
                _run(task)
            except Exception:
                pass  # Some tasks may fail without full env; we care only about npc write

    clear_combat_state(chat_id)

    # === The critical assertion ===
    # After COMBAT cleanup, does update_npcs_in_db receive the dead goblin?
    # If npc_changes comes from ai_data.get('npc_updates',[]) (GM_Logic JSON which is []),
    # then the dead goblin from combat cleanup IS NOT written.
    #
    # Expected: update_npcs_in_db called with list containing BattleGoblin Dead entry.
    # BUG: if combat cleanup npc_updates are not merged into ai_data, the call gets [] instead.

    all_npc_entries = [entry for call_args in update_npcs_calls for entry in call_args]
    dead_goblin_entries = [
        e for e in all_npc_entries
        if e.get("Name") == "BattleGoblin" and e.get("Status") == "Dead"
    ]

    # This assertion WILL FAIL if the bug exists:
    # (dead NPCs from combat not written to DB)
    assert dead_goblin_entries, (
        "BUG: Dead NPC 'BattleGoblin' from COMBAT cleanup was NOT passed to update_npcs_in_db. "
        "combat cleanup npc_updates are NOT merged into ai_data in engine.py. "
        "See engine.py line 1075: npc_changes = ai_data.get('npc_updates', []) — "
        "this reads from GM_Logic JSON, not from combat_updates/cleanup_result. "
        f"All npc entries received by update_npcs_in_db: {all_npc_entries!r}"
    )


# ===========================================================================
# Тест BUG #2: COMBAT mode — dice summary block відображається у відповіді
# ===========================================================================

def test_process_game_turn_combat_mode_shows_dice_summary():
    """BUG #2 перевірка: COMBAT mode → result_text містить '📊' з рядком
    'БІЙ Раунд' і log_line атак гравця/NPC.

    Мокуємо execute_combat_round з нетривіальним combat_log і npc_actions що
    містить AttackResult з log_line. Перевіряємо що process_game_turn повертає
    рядок з '📊' і бойовими деталями.
    """
    from core.combat_state import set_combat_state, clear_combat_state
    from core.dnd_combat import AttackResult

    chat_id = 7001
    state = _make_combat_state(chat_id=chat_id)
    set_combat_state(chat_id, state)

    profile = _minimal_player_profile(mode="COMBAT")

    # Реалістичний combat_log: заголовок + player attack line
    _player_log_line = (
        "TestPlayer атакує Goblin зброєю Longsword: [15]→15 +3(атак) +2(проф) = 20 проти КЗ 12 → ВЛУЧАННЯ, 7 slashing."
    )
    combat_log_str = f"--- Раунд 1 ---\n{_player_log_line}"

    # NPC attack result з log_line
    _npc_log_line = (
        "Goblin атакує TestPlayer (Scimitar): [8]→8 +4 = 12 проти КЗ 16 → ПРОМАХ"
    )
    _npc_attack_result = AttackResult(
        attacker="Goblin", target="TestPlayer", weapon="Scimitar",
        to_hit_total=12, natural_roll=8, target_ac=16,
        hit=False, critical=False, damage_total=0,
        damage_dice_str="1d6+2 slashing", damage_type="slashing",
        log_line=_npc_log_line,
    )

    combat_upd = {
        "combat_phase": "ONGOING",
        "combat_ended": False,
        "combat_round": 1,
        "player_hp_current": 40,
        "alive_npc_names": ["Goblin"],
        "action_type": "combat",
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
        "outcome": "SUCCESS",
    }

    # npc_actions з attack_result
    npc_actions_data = [
        {
            "npc_name": "Goblin",
            "action_taken": "attack",
            "attack_result": _npc_attack_result,
            "log_lines": [_npc_log_line],
        }
    ]

    _gm_json = {
        "reasoning": "test", "npc_reasoning": "test",
        "director_notes": ["Battle continues."],
        "companion_npcs": [], "npc_updates": [],
        "suggested_actions": [
            {"button": "Attack", "intent": "Attack goblin"},
            {"button": "Dodge", "intent": "Dodge"},
            {"button": "Flee", "intent": "Run away"},
            {"button": "Item", "intent": "Use item"},
        ],
    }

    gm_resp = MagicMock()
    gm_resp.text = json.dumps(_gm_json, ensure_ascii=False)
    narrator_resp = MagicMock()
    narrator_resp.text = "The battle rages. Swords clash in the moonlight."

    execute_round_mock = AsyncMock(return_value=(combat_log_str, combat_upd, npc_actions_data))

    patches = [
        patch("core.engine.get_user_data", new=AsyncMock(return_value=(profile, 2))),
        patch("core.engine.validate_action", new=AsyncMock(return_value=(True, ""))),
        patch("core.engine.get_location_npcs", return_value=("", [], {})),
        patch("core.engine.get_relevant_context", new=AsyncMock(return_value="")),
        patch("core.engine.get_dead_npc_names", return_value=set()),
        patch("core.engine.model_gm_logic.generate_content", return_value=gm_resp),
        patch("core.engine.model_narrator.generate_content", return_value=narrator_resp),
        patch("core.engine._run_bg_task", return_value=MagicMock()),
        patch("core.engine.execute_combat_round", new=execute_round_mock),
    ]

    async def _run_engine():
        from core.engine import process_game_turn
        return await process_game_turn(chat_id=chat_id, user_input="Атакую!", narrator_queue=None)

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result_text, suggested_actions = _run(_run_engine())

    clear_combat_state(chat_id)

    # === Ключові перевірки BUG #2 ===
    assert "📊" in result_text, (
        f"BUG #2: COMBAT result_text не містить '📊' dice summary. "
        f"result_text (перші 300 символів): {result_text[:300]!r}"
    )
    assert "БІЙ Раунд" in result_text, (
        f"BUG #2: result_text не містить 'БІЙ Раунд'. "
        f"result_text: {result_text[:300]!r}"
    )
    # Перевіряємо що player attack log_line присутній
    assert "ВЛУЧАННЯ" in result_text or "TestPlayer атакує" in result_text, (
        f"BUG #2: Player attack log_line відсутній у result_text. "
        f"result_text: {result_text[:400]!r}"
    )
    # Перевіряємо що NPC attack log_line присутній
    assert "ПРОМАХ" in result_text or "Goblin атакує" in result_text, (
        f"BUG #2: NPC attack log_line відсутній у result_text. "
        f"result_text: {result_text[:400]!r}"
    )


# ===========================================================================
# Тести Item 2 (Phase 11b): schema config передається до model_worker
# ===========================================================================

def test_parse_player_combat_intent_passes_schema_config():
    """parse_player_combat_intent must call model_worker.generate_content
    with a non-None config that has response_mime_type='application/json'."""
    from core.dnd_combat_engine import parse_player_combat_intent

    captured_configs = []
    llm_data = {
        "intent": "attack", "target_npc": "Goblin",
        "weapon": "Longsword", "spell_or_ability": "",
        "tactic": "normal", "move_to": "",
        "verdict_text": "Attacks.", "reasoning": "Direct.",
    }

    def _mock_gen(prompt, max_retries=6, config=None):
        captured_configs.append(config)
        return _mock_llm_response(llm_data)

    state = _make_combat_state()
    profile = _minimal_player_profile()

    with patch("core.dnd_combat_engine.model_worker.generate_content", side_effect=_mock_gen):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=llm_data):
            with patch("core.dnd_combat_engine.build_combat_round_prompt", return_value="MOCK"):
                result = _run(parse_player_combat_intent("Атакую", profile, state))

    assert result["intent"] == "attack"
    assert len(captured_configs) >= 1, "generate_content must be called"
    cfg = captured_configs[0]
    assert cfg is not None, "config must not be None (schema mode)"
    assert cfg.response_mime_type == "application/json", (
        f"Expected 'application/json', got {cfg.response_mime_type!r}"
    )
    assert cfg.response_schema is not None, "response_schema must be set"


def test_parse_player_combat_intent_schema_fallback_on_invalid_argument():
    """When schema raises INVALID_ARGUMENT, parse_player_combat_intent falls back
    to a free-JSON call and returns a valid intent dict."""
    from core.dnd_combat_engine import parse_player_combat_intent

    llm_data = {
        "intent": "dodge", "target_npc": "",
        "weapon": "", "spell_or_ability": "",
        "tactic": "cautious", "move_to": "",
        "verdict_text": "Dodge.", "reasoning": "Safe.",
    }
    call_count = []

    def _mock_gen_fallback(prompt, max_retries=6, config=None):
        call_count.append(config)
        if config is not None:
            raise Exception("400 INVALID_ARGUMENT: schema not supported")
        return _mock_llm_response(llm_data)

    state = _make_combat_state()
    profile = _minimal_player_profile()

    with patch("core.dnd_combat_engine.model_worker.generate_content", side_effect=_mock_gen_fallback):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=llm_data):
            with patch("core.dnd_combat_engine.build_combat_round_prompt", return_value="MOCK"):
                result = _run(parse_player_combat_intent("Захищаюсь", profile, state))

    assert result["intent"] == "dodge"
    assert len(call_count) == 2, (
        f"Must call twice (schema attempt + fallback). Got {len(call_count)}"
    )
    assert call_count[0] is not None, "First call must have config (schema)"
    assert call_count[1] is None, "Second call must have config=None (fallback)"


def test_execute_npc_actions_passes_schema_config():
    """execute_npc_actions must call model_worker.generate_content
    with a non-None config containing response_schema."""
    from core.dnd_combat_engine import execute_npc_actions

    captured_configs = []
    llm_response = {
        "actions": [
            {"npc_name": "Goblin", "action": "attack", "target": "player",
             "weapon": "Scimitar", "reason": "close range"},
        ]
    }

    def _mock_gen(prompt, max_retries=6, config=None):
        captured_configs.append(config)
        return _mock_llm_response(llm_response)

    state = _make_combat_state()

    with patch("core.dnd_combat_engine.model_worker.generate_content", side_effect=_mock_gen):
        with patch("core.dnd_combat_engine.clean_and_parse_json", return_value=llm_response):
            with patch("core.dnd_combat_engine.build_npc_combat_action_prompt", return_value="MOCK"):
                results = _run(execute_npc_actions(state, ["Goblin"]))

    assert len(results) == 1
    assert len(captured_configs) >= 1, "generate_content must be called"
    cfg = captured_configs[0]
    assert cfg is not None, "config must not be None (schema mode)"
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is not None
