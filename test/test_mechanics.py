import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from core.mechanics import safe_int, apply_system_impacts, process_training_request

def test_safe_int():
    assert safe_int("100") == 100
    assert safe_int(50.9) == 50
    assert safe_int("invalid") == 0
    assert safe_int(None) == 0


def test_apply_system_impacts_health_limits():
    profile = {"Здоров'я": 10}

    # Тестуємо зцілення (не більше 100)
    impacts = {"health_impact": "heal_full"}
    updated, logs = apply_system_impacts(profile.copy(), impacts)
    assert updated["Здоров'я"] == 100

    # Тестуємо фатальну шкоду (не менше 0)
    impacts = {"health_impact": "dmg_fatal"}
    updated, logs = apply_system_impacts(profile.copy(), impacts)
    assert updated["Здоров'я"] == 0



def test_energy_impact_spend():
    """Перевірка витрати енергії"""
    profile = {"Енергія": 50}
    ai_impacts = {"energy_impact": "spend_medium"}

    # Мокаємо randint, щоб він завжди повертав 5
    with patch('core.mechanics.random.randint', return_value=5):
        updated_profile, logs = apply_system_impacts(profile, ai_impacts)

    assert updated_profile["Енергія"] == 45
    assert any("⚡ Енергія: 50 -> 45 (-5)" in log for log in logs)


def test_energy_impact_restore_partial():
    """Перевірка часткового відновлення енергії"""
    profile = {"Енергія": 30}
    ai_impacts = {"energy_impact": "restore_small"}

    # Мокаємо randint, щоб він завжди повертав 12
    with patch('core.mechanics.random.randint', return_value=12):
        updated_profile, logs = apply_system_impacts(profile, ai_impacts)

    assert updated_profile["Енергія"] == 42
    assert any("⚡ Енергія: 30 -> 42 (+12)" in log for log in logs)


def test_energy_impact_restore_full():
    """Перевірка повного відновлення енергії (restore_full або sleep) до 1000"""
    profile = {"Енергія": 15}
    ai_impacts = {"energy_impact": "restore_full"}

    updated_profile, logs = apply_system_impacts(profile, ai_impacts)

    assert updated_profile["Енергія"] == 1000
    assert any("⚡ Енергія: 15 -> 1000 (+985)" in log for log in logs)


def test_energy_impact_overheal():
    """Перевірка, чи не виходить енергія за межі 1000 при лікуванні (cap на 1000)"""
    profile = {"Енергія": 990}
    ai_impacts = {"energy_impact": "restore_medium"}

    # Мокаємо randint, щоб він повернув 200 — реалістичне значення restore_medium (160-300).
    # 990 + 200 = 1190, що більше за 1000, тому cap має спрацювати.
    with patch('core.mechanics.random.randint', return_value=200):
        updated_profile, logs = apply_system_impacts(profile, ai_impacts)

    assert updated_profile["Енергія"] == 1000  # Має обрізатися до 1000
    assert any("⚡ Енергія: 990 -> 1000 (+200)" in log for log in logs)


def test_apply_system_impacts_inventory():
    from core.inventory import parse_inventory
    profile = {"Інвентар": "Меч, Яблуко"}
    impacts = {
        "inventory_new": ["Щит"],
        "inventory_lost": ["яблуко"]  # Перевірка на нечутливість до регістру
    }
    updated, logs = apply_system_impacts(profile, impacts)

    # profile["Інвентар"] is now list[dict] — check by item names
    items = parse_inventory(updated["Інвентар"])
    names = [i["name"] for i in items]
    assert "Щит" in names
    assert "Яблуко" not in names
    assert "Меч" in names


def test_apply_system_impacts_skills():
    profile = {"Бойові навички": 98}
    impacts = {"skill_impact": {"Бойові": 5}}

    updated, logs = apply_system_impacts(profile, impacts)
    # Навичка не може перевищити 100
    assert updated["Бойові навички"] == 100


# ---------------------------------------------------------------------------
# D&D Training tests (process_training_request — D&D XP-based)
# ---------------------------------------------------------------------------

def _make_dnd_profile(gold=200, energy=1000):
    """Minimal D&D profile for training tests."""
    return {
        "level": 1,
        "xp": 0,
        "ability_scores": {"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 15},
        "proficiency_bonus": 2,
        "hp_max": 11,
        "hp_current": 11,
        "skill_profs": ["Athletics"],
        "skill_expertise": [],
        "Особисте Золото": gold,
        "gold": gold,
        "Енергія": energy,
    }


def _llm_response(is_training=True, is_possible=True, skill="Athletics", method="solo", reason=""):
    """Build a mock LLM JSON response for build_training_request_prompt."""
    import json
    data = {
        "is_training": is_training,
        "is_possible": is_possible,
        "skill": skill,
        "method": method,
        "reason_if_failed": reason,
    }
    mock = MagicMock()
    mock.text = json.dumps(data)
    return mock


def test_training_solo_success_xp_and_energy():
    """Solo training: xp_gained=1500, energy_cost=40, cost_gold=0, cost_time_days=2."""
    profile = _make_dnd_profile(gold=0, energy=1000)
    user_input = "Я тренуюся з мечем самостійно"

    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(skill="Athletics", method="solo")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is not None
    assert "error" not in result
    assert result["skill"] == "Athletics"
    assert result["xp_gained"] == 1500
    assert result["energy_cost"] == 40
    assert result["cost_gold"] == 0
    assert result["cost_time_days"] == 2
    assert result["hp_damage_dice"] == "none"
    assert result["method"] == "solo"
    # XP was awarded inside process_training_request
    assert profile["xp"] == 1500


def test_training_mentor_success_xp_and_cost():
    """Mentor training: xp_gained=3000, cost_gold=50, energy_cost=25, cost_time_days=1."""
    profile = _make_dnd_profile(gold=200, energy=1000)
    user_input = "Я прошу майстра навчити мене красномовству"

    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(skill="Persuasion", method="mentor")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is not None
    assert "error" not in result
    assert result["skill"] == "Persuasion"
    assert result["xp_gained"] == 3000
    assert result["cost_gold"] == 50
    assert result["energy_cost"] == 25
    assert result["cost_time_days"] == 1
    assert result["hp_damage_dice"] == "none"
    assert result["method"] == "mentor"
    assert profile["xp"] == 3000


def test_training_mentor_insufficient_gold():
    """Mentor training declined when gold < 50."""
    profile = _make_dnd_profile(gold=10, energy=1000)
    user_input = "Наймаю вчителя стелсу"

    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(skill="Stealth", method="mentor")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is not None
    assert "error" in result
    assert "Не вистачає золота" in result["error"]
    # No XP should have been awarded
    assert profile.get("xp", 0) == 0


def test_training_insufficient_energy_failure_path():
    """Low energy → failure path: xp=500, hp_damage_dice='1d4'."""
    # energy_cost for solo is 40; set energy below that
    profile = _make_dnd_profile(gold=0, energy=10)
    user_input = "Тренуюся попри виснаження"

    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(skill="Athletics", method="solo")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is not None
    assert "error" not in result
    assert result["xp_gained"] == 500
    assert result["hp_damage_dice"] == "1d4"
    assert profile["xp"] == 500


def test_training_invalid_skill_returns_error():
    """LLM returns invalid skill name → error dict returned."""
    profile = _make_dnd_profile()
    user_input = "Хочу прокачати Бойові"

    # "Бойові" is NOT a D&D skill name
    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(skill="Бойові", method="solo")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is not None
    assert "error" in result
    assert "Невідомий скіл" in result["error"]


def test_training_not_training_intent_returns_none():
    """is_training=False from LLM → function returns None (not a training action)."""
    profile = _make_dnd_profile()
    user_input = "Я йду до таверни"

    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(is_training=False, skill="Athletics", method="solo")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is None


def test_training_scene_not_safe_returns_error():
    """is_possible=False from LLM → error dict with reason returned."""
    profile = _make_dnd_profile()
    user_input = "Тренуюся під час бою"

    with patch("core.mechanics.model_worker.generate_content",
               return_value=_llm_response(
                   is_training=True, is_possible=False,
                   skill="Athletics", method="solo",
                   reason="Active combat in progress.")):
        result = asyncio.run(process_training_request(user_input, profile))

    assert result is not None
    assert "error" in result
    assert "Active combat" in result["error"]


