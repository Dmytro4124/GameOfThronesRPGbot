import pytest
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


def test_apply_system_impacts_gold_and_time():
    profile = {
        "Особисте Золото": 50,
        "Енергія": 20,  # Додаємо стартову енергію для перевірки
        "Ігровий час": "298 рік В.Е., 1-й місяць, День 1, 21:00",  # Використовуємо точний час
        "Час_хвилини": 1260  # 21 * 60
    }
    impacts = {
        "gold_impact": "spend_medium",  # віднімає від 50 до 150
        "energy_impact": "sleep"  # мотає час до 08:00 наступного дня і відновлює енергію
    }
    updated, logs = apply_system_impacts(profile, impacts)

    # 1. Золото не може бути мінусовим
    assert updated["Особисте Золото"] == 0

    # 2. Перевіряємо перехід на наступний день
    assert "День 2" in updated["Ігровий час"]

    # 3. Перевіряємо, що гравець прокинувся рівно о 08:00
    assert "08:00" in updated["Ігровий час"]
    assert updated["Час_хвилини"] == 480  # 8 * 60

    # 4. Перевіряємо, чи відновилася енергія після сну
    assert updated["Енергія"] == 100


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
    profile = {"Бойові навички": 50, "Особисте Золото": 10}
    # Імітуємо запит на тренування
    user_input = "Я хочу потренуватися битися мечем"

    # Оскільки Worker AI використовується всередині, нам треба його "замокати"
    # Але для спрощення перевіримо базову логіку: якщо ми підмінимо відповідь ШІ
    from unittest.mock import patch, MagicMock

    with patch('core.mechanics.model_worker.generate_content') as mock_gen:
        mock_response = MagicMock()
        # ШІ каже, що гравець хоче тренувати "Бойові"
        mock_response.text = '{"is_training": true, "skill": "Бойові"}'
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
def test_resolve_action_mechanics_success(mock_randint, mock_generate, sample_profile):
    """Тестуємо успішну обробку валідного JSON від Worker AI"""
    # 1. Фіксуємо кидок кубика
    mock_randint.return_value = 15

    # 2. Імітуємо ідеальну відповідь від Worker'а
    mock_response = MagicMock()
    mock_response.text = """
    {
        "skill_used": "Бойові",
        "difficulty": 60,
        "total_score": 65,
        "outcome": "SUCCESS",
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
    assert "Skill: Бойові" in verdict_str
    assert "Player successfully parried the attack." in verdict_str

    # 5. Перевіряємо, чи правильно повернувся словник updates
    assert isinstance(updates, dict)
    assert updates["minutes_passed"] == 2
    assert updates["energy_impact"] == "spend_small"


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