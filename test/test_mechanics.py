import pytest
from core.mechanics import safe_int, apply_system_impacts, process_training_request
from unittest.mock import patch
from core.mechanics import apply_system_impacts

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
    """Перевірка повного відновлення енергії (restore_full або sleep)"""
    profile = {"Енергія": 15}
    ai_impacts = {"energy_impact": "restore_full"}

    updated_profile, logs = apply_system_impacts(profile, ai_impacts)

    assert updated_profile["Енергія"] == 100
    assert any("⚡ Енергія: 15 -> 100 (+85)" in log for log in logs)


def test_energy_impact_overheal():
    """Перевірка, чи не виходить енергія за межі 100 при лікуванні"""
    profile = {"Енергія": 95}
    ai_impacts = {"energy_impact": "restore_medium"}

    # Мокаємо randint, щоб він повернув 20 (95 + 20 = 115)
    with patch('core.mechanics.random.randint', return_value=20):
        updated_profile, logs = apply_system_impacts(profile, ai_impacts)

    assert updated_profile["Енергія"] == 100  # Має обрізатися до 100
    assert any("⚡ Енергія: 95 -> 100 (+20)" in log for log in logs)


def test_apply_system_impacts_inventory():
    profile = {"Інвентар": "Меч, Яблуко"}
    impacts = {
        "inventory_new": ["Щит"],
        "inventory_lost": ["яблуко"]  # Перевірка на нечутливість до регістру
    }
    updated, logs = apply_system_impacts(profile, impacts)

    assert "Щит" in updated["Інвентар"]
    assert "Яблуко" not in updated["Інвентар"]
    assert "Меч" in updated["Інвентар"]


def test_apply_system_impacts_skills():
    profile = {"Бойові навички": 98}
    impacts = {"skill_impact": {"Бойові": 5}}

    updated, logs = apply_system_impacts(profile, impacts)
    # Навичка не може перевищити 100
    assert updated["Бойові навички"] == 100


def test_process_training_request_insufficient_gold():
    profile = {"Бойові навички": 50, "Особисте Золото": 10, "Енергія": 100}
    user_input = "Я хочу потренуватися битися мечем у майстра"

    from unittest.mock import patch, MagicMock

    with patch('core.mechanics.model_worker.generate_content') as mock_gen:
        mock_response = MagicMock()
        # Додаємо is_possible та method: mentor, щоб тригернути перевірку золота
        mock_response.text = '{"is_training": true, "is_possible": true, "method": "mentor", "skill": "Бойові"}'
        mock_gen.return_value = mock_response

        result = process_training_request(user_input, profile)

        assert result is not None
        assert "error" in result
        assert "Не вистачає золота" in result["error"]


# test/test_mechanics.py
import pytest
from unittest.mock import patch, MagicMock
from core.mechanics import resolve_action_mechanics


@pytest.fixture
def sample_profile():
    return {
        "Бойові навички": 50,
        "Військові навички": 10,
        "Інтрига": 20,
        "Управління": 5,
        "Здоров'я": 100,
        "Особисте Золото": 50,
        "Годинники": {"Scene_Tension": "1/4"}
    }


@patch('core.mechanics.model_worker.generate_content')
@patch('core.mechanics.random.randint')  # Мокаємо кубик для стабільності
def test_resolve_action_mechanics_success(mock_randint, mock_generate):
    sample_profile = {
        'Інтрига': 20,
        'Бойові навички': 50,
        'Військові навички': 10,
        'Годинники': {'Scene_Tension': '1/4'}
    }

    # 1. Фіксуємо кидок кубика так, щоб 2d50 дав достатньо для успіху
    # random.randint викликається 4 рази (по 2 рази для roll_1 і roll_2).
    # Нехай завжди випадає 25. 25+25 = 50. Навичка 50. 50+50 = 100 (Успіх!)
    mock_randint.return_value = 25

    # 2. Імітуємо ідеальну відповідь від Worker'а (Новий JSON-формат)
    mock_response = MagicMock()
    mock_response.text = """
        {
            "action_type": "standard",
            "skill_used": "Бойові",
            "circumstance": "NORMAL",
            "verdict_text": "Player successfully parried the attack.",
            "updates": {
                 "minutes_passed": 2,
                 "health_impact": "none",
                 "energy_impact": "spend_small",
                 "gold_impact": "none",
                 "inventory_new": [],
                 "inventory_lost": [],
                 "clocks_impact": {}
            }
        }
        """
    mock_generate.return_value = mock_response

    # 3. Викликаємо функцію
    user_input = "Я відбиваю удар мечем"
    verdict_str, updates = resolve_action_mechanics(user_input, sample_profile)

    # 4. Перевіряємо, чи правильно сформувався текстовий вердикт
    assert "MECHANICAL VERDICT: SUCCESS!" in verdict_str
    assert updates.get("total_score") == 100
    assert updates.get("action_type") == "standard"


@patch('core.mechanics.model_worker.generate_content')
def test_resolve_action_mechanics_fallback(mock_generate, sample_profile):
    """Тестуємо поведінку системи, якщо Worker AI падає з помилкою"""
    # 1. Змушуємо API викинути виняток (імітуємо таймаут або 500 помилку сервера)
    mock_generate.side_effect = Exception("Google API Error Timeout")

    # 2. Викликаємо функцію
    user_input = "Я намагаюся вижити при падінні сервера"
    verdict_str, updates = resolve_action_mechanics(user_input, sample_profile)

    # 3. Перевіряємо спрацювання Fallback-логіки (щоб гравець не чекав вічно)
    assert "MECHANICAL VERDICT: AUTO_SUCCESS" in verdict_str
    assert isinstance(updates, dict)
    assert updates["minutes_passed"] == 5