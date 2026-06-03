# test/test_world_constants.py
"""
Структурні тести для LOCATION_SCENES — Ланніспорт та Кастерлі Рок private scenes.
Додано 2026-05: розширення категорій усамітнення для player-initiated private дій.
Не мокає нічого: world_constants.py не імпортує нічого з проєкту.
"""

import pytest
from core.world_constants import LOCATION_SCENES


def test_lannisport_has_private_scenes():
    """Lannisport has 6 private 'усамітнення' scenes after 2026-05 update."""
    private = LOCATION_SCENES["Ланніспорт"].get("private", [])
    assert len(private) == 6, (
        f"Очікувано 6 private scenes у Ланніспорті, знайдено {len(private)}: {private}"
    )
    assert "Кімната Таверни" in private, (
        f"'Кімната Таверни' відсутня. private={private}"
    )
    assert "Орендована Кімната" in private, (
        f"'Орендована Кімната' відсутня. private={private}"
    )
    assert "Кімната Куртизанки" in private, (
        f"'Кімната Куртизанки' відсутня. private={private}"
    )


def test_lannisport_private_all_five_scenes():
    """Всі 5 з 6 core private scenes присутні у Ланніспорті (без Кімнати Таверни)."""
    private = LOCATION_SCENES["Ланніспорт"].get("private", [])
    expected = [
        "Орендована Кімната",
        "Каюта Корабля",
        "Альтанка над Гаванню",
        "Покинутий Склад",
        "Кімната Куртизанки",
    ]
    for scene in expected:
        assert scene in private, (
            f"Сцена '{scene}' відсутня у Ланніспорт private. private={private}"
        )


def test_lannisport_existing_categories_preserved():
    """Додавання private не зламало існуючі категорії Ланніспорту."""
    data = LOCATION_SCENES["Ланніспорт"]
    assert "hub" in data and len(data["hub"]) == 3
    assert "semi_public" in data and "Таверна Моряків" in data["semi_public"]
    assert "labor" in data and "Складські Доки" in data["labor"]
    assert "military" in data and "Митний Пост" in data["military"]


def test_casterly_rock_extended_private_scenes():
    """Casterly Rock private scenes extended to 8 (3 NPC-specific + 5 generic)."""
    private = LOCATION_SCENES["Кастерлі Рок"].get("private", [])
    assert len(private) == 8, (
        f"Очікувано 8 private scenes у Кастерлі Рок, знайдено {len(private)}: {private}"
    )
    # NPC-specific preserved:
    assert "Покої Тайвіна" in private, f"'Покої Тайвіна' відсутні. private={private}"
    assert "Жіночі Покої" in private, f"'Жіночі Покої' відсутні. private={private}"
    assert "Кімната Мейстера" in private, f"'Кімната Мейстера' відсутня. private={private}"
    # Generic added:
    assert "Альков у Бібліотеці" in private, f"'Альков у Бібліотеці' відсутній. private={private}"
    assert "Покинута Гостьова Кімната" in private, (
        f"'Покинута Гостьова Кімната' відсутня. private={private}"
    )


def test_casterly_rock_all_eight_private_scenes():
    """Всі 8 private scenes присутні у Кастерлі Рок."""
    private = LOCATION_SCENES["Кастерлі Рок"].get("private", [])
    expected = [
        "Покої Тайвіна",
        "Жіночі Покої",
        "Кімната Мейстера",
        "Покинута Гостьова Кімната",
        "Стара Балюстрада",
        "Альков у Бібліотеці",
        "Зачинена Зала",
        "Покої Гостей",
    ]
    for scene in expected:
        assert scene in private, (
            f"Сцена '{scene}' відсутня у Кастерлі Рок private. private={private}"
        )


def test_casterly_rock_other_categories_preserved():
    """Розширення private не зламало інші категорії Кастерлі Рок."""
    data = LOCATION_SCENES["Кастерлі Рок"]
    assert "hub" in data and "Замкові Ворота" in data["hub"]
    assert "semi_public" in data and "Замкова Кухня" in data["semi_public"]
    assert "restricted" in data and "Тренувальний Плац" in data["restricted"]
    assert "elite" in data and "Зала Лордів" in data["elite"]
    assert "containment" in data and "Підземелля" in data["containment"]
    assert "labor" in data and "Золоті Копальні" in data["labor"]
    assert "military" in data and "Замкові Мури" in data["military"]


def test_no_duplicate_private_scenes_in_lannisport():
    """Немає дублікатів у private scenes Ланніспорту."""
    private = LOCATION_SCENES["Ланніспорт"].get("private", [])
    assert len(private) == len(set(private)), (
        f"Дублікати у private Ланніспорту: {[s for s in private if private.count(s) > 1]}"
    )


def test_no_duplicate_private_scenes_in_casterly_rock():
    """Немає дублікатів у private scenes Кастерлі Рок."""
    private = LOCATION_SCENES["Кастерлі Рок"].get("private", [])
    assert len(private) == len(set(private)), (
        f"Дублікати у private Кастерлі Рок: {[s for s in private if private.count(s) > 1]}"
    )
