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