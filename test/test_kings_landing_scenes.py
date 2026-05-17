# test/test_kings_landing_scenes.py
"""
Структурні юніт-тести для розширеного LOCATION_SCENES["Королівська Гавань"].
mechanics-dev додав сцени з 24 до ~52 у 10 категоріях, а "Кімната Малої Ради"
перенесена з private в elite.

Не мокає gspread/Gemini: world_constants.py не імпортує нічого з проєкту.
"""

import pytest
from core.world_constants import LOCATION_SCENES, SCENE_DEFINITIONS

KL = "Королівська Гавань"
KL_DATA = LOCATION_SCENES[KL]


# ── Тест 1: загальна кількість сцен лежить у [50, 60] ─────────────────────────

def test_kings_landing_total_scene_count_in_range():
    """
    Після розширення Королівська Гавань повинна мати від 50 до 60 сцен сумарно.

    Межі діапазону:
      50 — мінімум за вимогою задачі (специфікація розширення до ~52 сцен).
      60 — запас на майбутні розширення. При перевищенні цієї межі слід
           переглянути промптові обмеження у format_scenes_for_prompt (context
           budget), оскільки великий список сцен збільшує токен-навантаження
           на кожен хід.
    """
    total = sum(len(scenes) for scenes in KL_DATA.values())
    assert 50 <= total <= 60, (
        f"Кількість сцен у Королівській Гавані = {total}, "
        f"очікувано діапазон [50, 60]. Категорії: "
        + ", ".join(f"{k}={len(v)}" for k, v in KL_DATA.items())
    )


# ── Тест 2: присутні всі 10 категорій з SCENE_DEFINITIONS ────────────────────

def test_kings_landing_has_all_10_categories():
    """
    Усі 10 ключів SCENE_DEFINITIONS мають бути присутні у Королівській Гавані.
    """
    expected_categories = set(SCENE_DEFINITIONS.keys())
    actual_categories = set(KL_DATA.keys())
    missing = expected_categories - actual_categories
    assert not missing, (
        f"У Королівській Гавані відсутні категорії: {missing}. "
        f"Наявні категорії: {actual_categories}"
    )


# ── Тест 3: унікальність назв сцен у межах локації ────────────────────────────

def test_kings_landing_no_duplicate_scene_names():
    """
    Жодне ім'я сцени не повторюється у межах Королівської Гавані.
    """
    all_scenes: list[str] = [
        scene
        for scenes in KL_DATA.values()
        for scene in scenes
    ]
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in all_scenes:
        if name in seen:
            duplicates.append(name)
        seen.add(name)
    assert not duplicates, (
        f"Знайдено дублікати сцен у Королівській Гавані: {duplicates}"
    )


# ── Тест 4: спот-чек нових сцен за специфікацією ─────────────────────────────

@pytest.mark.parametrize("category,scene_name", [
    # Базові нові сцени з початкового розширення
    ("hub",         "Блошине Дно"),
    ("hub",         "Гачок"),
    ("semi_public", "Вулиця Шовку"),
    ("private",     "Башта Десниці"),
    ("containment", "Чорні Камери"),
    ("military",    "Драконова Брама"),
    ("stealth",     "Руїни Драконового Лігва"),
    # Переміщені сцени: "Таємні Ходи" private → stealth
    ("stealth",     "Таємні Ходи"),
    # Переміщені сцени: "Дівочий Притулок" private → elite
    ("elite",       "Дівочий Притулок"),
    # Переміщені сцени: "Майгорова Твердиня" private → elite
    ("elite",       "Майгорова Твердиня"),
    # Наявні elite-сцени що потребують явної перевірки
    ("elite",       "Вежа Білого Меча"),
    ("elite",       "Богорощ Червоного Замку"),
])
def test_kings_landing_spot_check_new_scenes(category, scene_name):
    """
    Нові та переміщені сцени присутні у правильних категоріях.
    Охоплює: початкове розширення, переміщення private→stealth/elite,
    та явну перевірку elite-сцен Вежа Білого Меча і Богорощ Червоного Замку.
    """
    scenes_in_cat = KL_DATA.get(category, [])
    assert scene_name in scenes_in_cat, (
        f"Сцена '{scene_name}' не знайдена у категорії '{category}'. "
        f"Наявні сцени в '{category}': {scenes_in_cat}"
    )


# ── Тест 5: "Кімната Малої Ради" в elite, не в private ───────────────────────

def test_small_council_chamber_moved_to_elite():
    """
    "Кімната Малої Ради" переміщена з private в elite (виправлення семантики).
    """
    assert "Кімната Малої Ради" in KL_DATA.get("elite", []), (
        f"'Кімната Малої Ради' повинна бути в категорії 'elite'. "
        f"Вміст 'elite': {KL_DATA.get('elite', [])}"
    )
    assert "Кімната Малої Ради" not in KL_DATA.get("private", []), (
        f"'Кімната Малої Ради' не повинна бути в категорії 'private'. "
        f"Вміст 'private': {KL_DATA.get('private', [])}"
    )


# ── Тест 6: всі категорії — підмножина ключів SCENE_DEFINITIONS ──────────────

def test_kings_landing_no_illegal_categories():
    """
    Жодна категорія в LOCATION_SCENES["Королівська Гавань"] не виходить за межі
    SCENE_DEFINITIONS — не з'явилось нових нелегальних ключів.
    """
    valid_keys = set(SCENE_DEFINITIONS.keys())
    for cat_key in KL_DATA.keys():
        assert cat_key in valid_keys, (
            f"Нелегальна категорія '{cat_key}' у Королівській Гавані. "
            f"Допустимі категорії: {valid_keys}"
        )


# ── Тест 7: переміщені сцени більше НЕ є в категорії private ─────────────────

@pytest.mark.parametrize("scene_name", [
    "Таємні Ходи",        # переміщено private → stealth
    "Дівочий Притулок",   # переміщено private → elite
    "Майгорова Твердиня", # переміщено private → elite
])
def test_kings_landing_moved_scenes_not_in_private(scene_name):
    """
    Сцени, переміщені з private в іншу категорію, більше не присутні в private.
    Регресійний тест: захищає від випадкового дублювання або відкату.
    """
    private_scenes = KL_DATA.get("private", [])
    assert scene_name not in private_scenes, (
        f"Сцена '{scene_name}' помилково залишилась у категорії 'private'. "
        f"Вміст 'private': {private_scenes}"
    )
