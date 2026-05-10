# test/test_dc_circumstance.py
"""
Тести нової моделі DC-circumstance для resolve_action_mechanics (core/mechanics.py).

Нова модель (CLAUDE.md §5.2 + план vast-watching-neumann.md):
  - DC < 80 (50/60/70) → AUTO_SUCCESS: без 2d50, skill_used="Немає", difficulty=0
  - DC 80  → базовий circumstance ADVANTAGE
  - DC 100 → базовий circumstance NORMAL
  - DC 120 → базовий circumstance DISADVANTAGE
  - DC 140 → базовий circumstance DISADVANTAGE
  Overrides (поверх DC-базису):
  - Енергія < 200 → примусово DISADVANTAGE (фізіологічний override)
  - rep ≥ 80 + соціальний скіл → ADVANTAGE (якщо база не DIS)
  - rep ≤ -80 + соціальний скіл → DISADVANTAGE
  - Репутація НЕ впливає на несоціальні скіли (Бойові, Військові)

Async: resolve_action_mechanics — async функція. Виклик через asyncio.run().
Моки: model_worker.generate_content у core.mechanics — щоб уникнути реальних API.
      random.randint у core.mechanics — для детермінізму кидків.
      config.GODMODE_USERS — порожній set, щоб не тригерити godmode path.
"""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock


# ── Базовий профіль ────────────────────────────────────────────────────────────

def _make_profile(energy: int = 500) -> dict:
    """Мінімальний профіль персонажа для тестів."""
    return {
        "Бойові навички":    50,
        "Військові навички": 20,
        "Інтрига":           30,
        "Управління":        25,
        "Здоров'я":          100,
        "Особисте Золото":   50,
        "Енергія":           energy,
        "Інвентар":          "Пусто",
        "Годинники":         {},
        "temp_debuffs":      {},
    }


# ── Базовий Worker mock ────────────────────────────────────────────────────────

def _make_worker_mock(skill_used: str, difficulty: int,
                      reputation_target_npc: str = "") -> MagicMock:
    """
    Будує mock-відповідь Worker з фіксованим JSON.
    circumstance завжди "NORMAL" — placeholder (Python engine перевизначає).
    """
    mock_data = {
        "skill_check_reasoning": "test reasoning",
        "difficulty_reasoning":  "test difficulty reasoning",
        "gold_reasoning":        "test gold reasoning",
        "action_type":           "standard",
        "skill_used":            skill_used,
        "difficulty":            difficulty,
        "circumstance":          "NORMAL",
        "verdict_text":          "Test verdict.",
        "reputation_delta":      0,
        "reputation_target_npc": reputation_target_npc,
        "updates": {
            "minutes_passed":   5,
            "location_impact":  "none",
            "scene_impact":     "none",
            "health_impact":    "none",
            "energy_impact":    "none",
            "gold_impact":      "none",
            "inventory_new":    [],
            "inventory_lost":   [],
            "clocks_impact":    {},
        },
    }
    mock_response = MagicMock()
    mock_response.text = json.dumps(mock_data, ensure_ascii=False)
    return mock_response


# ── Допоміжна функція виклику ──────────────────────────────────────────────────

def _run(skill_used: str, difficulty: int, energy: int = 500,
         npc_reputation_context: dict = None,
         user_input: str = "Тест дія",
         reputation_target_npc: str = "") -> tuple:
    """
    Будує мок Worker, викликає resolve_action_mechanics через asyncio.run().
    Повертає (verdict_str, updates).
    Мокає:
      - core.mechanics.model_worker.generate_content → Worker JSON
      - core.mechanics.random.randint → 25 (фіксований кидок, не критичний результат)
      - config.GODMODE_USERS → порожній set
    """
    mock_resp = _make_worker_mock(skill_used, difficulty, reputation_target_npc)

    with patch("core.mechanics.model_worker.generate_content", return_value=mock_resp), \
         patch("core.mechanics.random.randint", return_value=25), \
         patch("config.GODMODE_USERS", new=set()):
        verdict, updates = asyncio.run(
            __import__("core.mechanics", fromlist=["resolve_action_mechanics"])
            .resolve_action_mechanics(
                user_input=user_input,
                profile=_make_profile(energy=energy),
                npc_reputation_context=npc_reputation_context or {},
            )
        )
    return verdict, updates


# ══════════════════════════════════════════════════════════════════════════════
# 1. DC 70 → AUTO_SUCCESS (без 2d50)
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_70_auto_success():
    """DC=70 + skill_used='Бойові' → AUTO_SUCCESS; skill_used='Немає', difficulty=0; 2d50 не викликається."""
    mock_resp = _make_worker_mock("Бойові", 70)

    randint_mock = MagicMock(return_value=25)

    with patch("core.mechanics.model_worker.generate_content", return_value=mock_resp), \
         patch("core.mechanics.random.randint", randint_mock), \
         patch("config.GODMODE_USERS", new=set()):
        from core.mechanics import resolve_action_mechanics
        verdict, updates = asyncio.run(
            resolve_action_mechanics(
                user_input="Тренування удару мечем",
                profile=_make_profile(energy=500),
                npc_reputation_context={},
            )
        )

    # AUTO_SUCCESS шлях
    assert "AUTO_SUCCESS" in verdict, f"Очікувався AUTO_SUCCESS у verdict: {verdict}"
    # Нова DC-модель: skill_used скидається до Немає
    assert updates.get("skill_used") == "Немає", f"skill_used={updates.get('skill_used')!r}"
    # difficulty скидається до 0
    assert updates.get("difficulty") == 0, f"difficulty={updates.get('difficulty')}"
    # 2d50 не викликався (random.randint не потрібен для AUTO_SUCCESS)
    # Примітка: roll_2d50 — локальна функція в resolve_action_mechanics;
    # підтверджуємо через dice_roll = "-"
    assert updates.get("dice_roll") == "-", f"dice_roll={updates.get('dice_roll')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 2. DC 60 → AUTO_SUCCESS (бойовий скіл)
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_60_auto_success_combat():
    """DC=60 + skill_used='Бойові' → AUTO_SUCCESS; skill_used='Немає', difficulty=0, dice_roll='-'."""
    verdict, updates = _run("Бойові", 60)

    assert "AUTO_SUCCESS" in verdict, f"Очікувався AUTO_SUCCESS: {verdict}"
    assert updates.get("skill_used") == "Немає"
    assert updates.get("difficulty") == 0
    assert updates.get("dice_roll") == "-"


# ══════════════════════════════════════════════════════════════════════════════
# 3. DC 50 + помилковий skill → AUTO_SUCCESS, скидання до Немає/0
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_50_auto_success_with_skill_assigned():
    """Worker помилково ставить skill_used='Інтрига', DC=50. Python переводить у no-skill."""
    verdict, updates = _run("Інтрига", 50)

    assert "AUTO_SUCCESS" in verdict, f"Очікувався AUTO_SUCCESS: {verdict}"
    assert updates.get("skill_used") == "Немає", f"skill_used={updates.get('skill_used')!r}"
    assert updates.get("difficulty") == 0, f"difficulty={updates.get('difficulty')}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. DC 80 → базовий circumstance ADVANTAGE; 2d50 викликається
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_80_forces_advantage():
    """DC=80, energy=500, без rep target. BASE з DC=80 → ADVANTAGE. 2d50 ВИКЛИКАНИЙ."""
    mock_resp = _make_worker_mock("Бойові", 80)

    randint_mock = MagicMock(return_value=25)

    with patch("core.mechanics.model_worker.generate_content", return_value=mock_resp), \
         patch("core.mechanics.random.randint", randint_mock), \
         patch("config.GODMODE_USERS", new=set()):
        from core.mechanics import resolve_action_mechanics
        verdict, updates = asyncio.run(
            resolve_action_mechanics(
                user_input="Звичайна дія бою",
                profile=_make_profile(energy=500),
                npc_reputation_context={},
            )
        )

    # Не AUTO_SUCCESS — реальний кидок
    assert "AUTO_SUCCESS" not in verdict, f"Не мав бути AUTO_SUCCESS: {verdict}"
    # circumstance=ADVANTAGE
    assert updates.get("circumstance") == "ADVANTAGE", \
        f"Очікувався circumstance=ADVANTAGE, отримано: {updates.get('circumstance')!r}"
    # 2d50 викликаний: random.randint викликається мінімум двічі (два кидки d50)
    assert randint_mock.call_count >= 2, \
        f"random.randint мав бути викликаний ≥2 рази (2d50), але call_count={randint_mock.call_count}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. DC 100 → circumstance NORMAL
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_100_forces_normal():
    """DC=100, energy=500, без rep. BASE від DC=100 → NORMAL."""
    verdict, updates = _run("Бойові", 100, energy=500)

    assert "AUTO_SUCCESS" not in verdict
    assert updates.get("circumstance") == "NORMAL", \
        f"Очікувався NORMAL, отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 6. DC 120 → circumstance DISADVANTAGE
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_120_forces_disadvantage():
    """DC=120, energy=500. BASE від DC=120 → DISADVANTAGE."""
    verdict, updates = _run("Бойові", 120, energy=500)

    assert "AUTO_SUCCESS" not in verdict
    assert updates.get("circumstance") == "DISADVANTAGE", \
        f"Очікувався DISADVANTAGE, отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 7. DC 140 → circumstance DISADVANTAGE
# ══════════════════════════════════════════════════════════════════════════════

def test_dc_140_forces_disadvantage():
    """DC=140, energy=500. BASE від DC=140 → DISADVANTAGE."""
    verdict, updates = _run("Бойові", 140, energy=500)

    assert "AUTO_SUCCESS" not in verdict
    assert updates.get("circumstance") == "DISADVANTAGE", \
        f"Очікувався DISADVANTAGE, отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Низька енергія (< 200) overrides ADVANTAGE → DISADVANTAGE
# ══════════════════════════════════════════════════════════════════════════════

def test_low_energy_overrides_to_dis():
    """DC=80 (база ADVANTAGE) + energy=150 (< 200) → фізіологічний override → DISADVANTAGE."""
    verdict, updates = _run("Бойові", 80, energy=150)

    assert "AUTO_SUCCESS" not in verdict
    assert updates.get("circumstance") == "DISADVANTAGE", \
        f"Очікувався DISADVANTAGE (low-energy override), отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Висока репутація + соціальний скіл: NORMAL → ADVANTAGE
# ══════════════════════════════════════════════════════════════════════════════

def test_high_rep_social_to_adv():
    """DC=100 (база NORMAL) + Інтрига + rep_target=90 (≥80) → override до ADVANTAGE."""
    verdict, updates = _run(
        skill_used="Інтрига",
        difficulty=100,
        energy=500,
        npc_reputation_context={"Тиріон": 90},
        user_input="Переконую Тиріона допомогти мені",
        reputation_target_npc="Тиріон",
    )

    assert "AUTO_SUCCESS" not in verdict
    assert updates.get("circumstance") == "ADVANTAGE", \
        f"Очікувався ADVANTAGE (high-rep social override), отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 10. Низька репутація + соціальний скіл: NORMAL → DISADVANTAGE
# ══════════════════════════════════════════════════════════════════════════════

def test_low_rep_social_to_dis():
    """
    DC=100 (база NORMAL) + Управління + rep=-50 (≤-40, dc_mod=+20) →
    final_difficulty=120 → DC_TO_CIRCUMSTANCE[120]='DISADVANTAGE'.

    ВАЖЛИВО: rep=-90 НЕ використовується тут, бо get_npc_reputation_modifier(-90)
    повертає is_blocked=True → функція виходить раніше по BLOCK-шляху, без
    встановлення 'circumstance' в updates. Перевірка BLOCK-шляху — окремий тест.

    rep=-50 дає dc_mod=+20 без блокування: final_difficulty=100+20=120,
    base_circumstance=DISADVANTAGE з DC_TO_CIRCUMSTANCE, energy >= 200 → без overrides.
    """
    verdict, updates = _run(
        skill_used="Управління",
        difficulty=100,
        energy=500,
        npc_reputation_context={"Серсея": -50},
        user_input="Командую Серсеї підписати наказ",
        reputation_target_npc="Серсея",
    )

    assert "AUTO_SUCCESS" not in verdict
    assert "BLOCKED" not in verdict, \
        f"rep=-50 не має блокувати, але вердикт: {verdict}"
    assert updates.get("circumstance") == "DISADVANTAGE", \
        f"Очікувався DISADVANTAGE (rep dc_mod escalates DC to 120), отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 11. Репутація НЕ впливає на несоціальні скіли (Бойові)
# ══════════════════════════════════════════════════════════════════════════════

def test_low_rep_combat_unchanged():
    """DC=100 + Бойові + rep_target=-90. Репутація не впливає на бойові → залишається NORMAL."""
    verdict, updates = _run(
        skill_used="Бойові",
        difficulty=100,
        energy=500,
        npc_reputation_context={"Ворог": -90},
        user_input="Атакую Ворога мечем",
        reputation_target_npc="Ворог",
    )

    assert "AUTO_SUCCESS" not in verdict
    # Бойові — не соціальний скіл, тому rep override не застосовується
    # BASE від DC=100 = NORMAL, overrides для Бойових немає → залишається NORMAL
    assert updates.get("circumstance") == "NORMAL", \
        f"Очікувався NORMAL (rep не впливає на Бойові), отримано: {updates.get('circumstance')!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 12. DC=60 + нейтральна репутація → AUTO_SUCCESS (DC < 80 переважає все)
# ══════════════════════════════════════════════════════════════════════════════

def test_hostile_npc_trivial_action():
    """
    DC=60 + rep=-20 (нейтральна зона: dc_mod=0, без блокування) →
    final_difficulty=60 < 80 → AUTO_SUCCESS незалежно від rep.

    ВАЖЛИВО: rep=-50 НЕ використовується тут, бо get_npc_reputation_modifier(-50)
    повертає dc_mod=+20, що піднімає final_difficulty до 60+20=80, і AUTO_SUCCESS
    shortcut (< 80) вже НЕ спрацьовує. Для перевірки принципу "низьке DC
    перемагає репутацію" потрібна репутація без dc_mod (у діапазоні (-40, 0)).
    """
    verdict, updates = _run(
        skill_used="Інтрига",
        difficulty=60,
        energy=500,
        npc_reputation_context={"Ворожий NPC": -20},
        user_input="Звертаюся до Ворожий NPC",
        reputation_target_npc="Ворожий NPC",
    )

    assert "AUTO_SUCCESS" in verdict, \
        f"Очікувався AUTO_SUCCESS (DC=60, rep=-20 → dc_mod=0 → final=60 < 80), отримано: {verdict}"
    assert updates.get("skill_used") == "Немає"
    assert updates.get("difficulty") == 0
