"""
test_intro_cache.py

Unit-тести для core/intro_cache.py.

Async-тести запускаються через asyncio.run() (без pytest-asyncio — відповідно до
патерну, прийнятого у test/test_help_command.py та test/test_npc_isolation.py).

Кожен тест ізолований: clear_cache() + тимчасовий _CACHE_FILE через monkeypatch.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch


# ── Фікстура ізоляції кешу ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_cache(tmp_path):
    """
    Кожен тест отримує власний тимчасовий файл кешу.
    Після тесту глобальний стан модуля скидається.
    """
    import core.intro_cache as ic

    tmp_file = tmp_path / "intro_cache.json"

    # Патчимо шлях до файлу та скидаємо in-memory стан
    with patch.object(ic, "_CACHE_FILE", tmp_file):
        ic._CACHE = {}
        ic._LOADED = False
        yield tmp_file
        # Cleanup
        ic._CACHE = {}
        ic._LOADED = False


# ── Тест 1: get_cached_intro для відсутнього ключа → None ────────────────────

def test_get_missing_key_returns_none():
    """get_cached_intro для невідомої комбінації повертає None."""
    from core.intro_cache import get_cached_intro

    result = get_cached_intro("Knight", "Westerosi (Andal)", "The North")
    assert result is None


# ── Тест 2: set + get roundtrip ──────────────────────────────────────────────

def test_set_get_roundtrip():
    """Після set_cached_intro той самий ключ повертається get_cached_intro."""
    from core.intro_cache import get_cached_intro, set_cached_intro

    intro = "Ви — лицар Вестеросу. Ваш шлях починається."
    asyncio.run(set_cached_intro("Knight", "Westerosi (Andal)", "The North", intro))

    result = get_cached_intro("Knight", "Westerosi (Andal)", "The North")
    assert result == intro


# ── Тест 3: кеш персистується на диск ────────────────────────────────────────

def test_cache_persists_to_disk(isolated_cache):
    """
    Після set_cached_intro файл існує і містить правильний JSON.
    """
    import core.intro_cache as ic
    from core.intro_cache import set_cached_intro

    intro = "Детерміністичний вступ."
    asyncio.run(set_cached_intro("Maester", "Westerosi (Andal)", "The Reach", intro))

    cache_file: Path = isolated_cache
    assert cache_file.exists(), "Файл кешу не створено"

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    key = "maester|westerosi (andal)|the reach"
    assert key in data, f"Ключ '{key}' не знайдено у файлі: {list(data.keys())}"
    assert data[key] == intro


# ── Тест 4: нормалізація ключа (case-insensitive) ────────────────────────────

def test_key_normalization_case_insensitive():
    """
    Ключ формується з lowercased-значень.
    get_cached_intro("KNIGHT", "WESTEROSI (ANDAL)", "THE NORTH")
    повертає той самий результат, що і ("knight", "westerosi (andal)", "the north").
    """
    from core.intro_cache import get_cached_intro, set_cached_intro

    intro = "Тест нормалізації."
    asyncio.run(set_cached_intro("KNIGHT", "WESTEROSI (ANDAL)", "THE NORTH", intro))

    # Запит у нижньому регістрі
    result_lower = get_cached_intro("knight", "westerosi (andal)", "the north")
    assert result_lower == intro

    # Запит у верхньому регістрі
    result_upper = get_cached_intro("KNIGHT", "WESTEROSI (ANDAL)", "THE NORTH")
    assert result_upper == intro

    # Змішаний регістр
    result_mixed = get_cached_intro("Knight", "Westerosi (Andal)", "The North")
    assert result_mixed == intro


# ── Тест 5: кілька ключів не конфліктують ────────────────────────────────────

def test_multiple_keys_no_collision():
    """Різні комбінації class/heritage/region зберігаються незалежно."""
    from core.intro_cache import get_cached_intro, set_cached_intro

    async def _run():
        await set_cached_intro("Knight", "Westerosi (Andal)", "The North", "Intro A")
        await set_cached_intro("Maester", "Westerosi (Andal)", "The Reach", "Intro B")
        await set_cached_intro("Spy", "Westerosi (First Men)", "The Riverlands", "Intro C")

    asyncio.run(_run())

    assert get_cached_intro("Knight", "Westerosi (Andal)", "The North") == "Intro A"
    assert get_cached_intro("Maester", "Westerosi (Andal)", "The Reach") == "Intro B"
    assert get_cached_intro("Spy", "Westerosi (First Men)", "The Riverlands") == "Intro C"
    # Чужий ключ — None
    assert get_cached_intro("Bastard", "Free Folk", "Beyond the Wall") is None


# ── Тест 6: завантаження існуючого кешу з диску ──────────────────────────────

def test_loads_existing_cache_from_disk(isolated_cache, tmp_path):
    """
    Якщо файл кешу вже існує на диску — get_cached_intro завантажує його
    і повертає правильне значення без попереднього set_cached_intro.
    """
    import core.intro_cache as ic

    pre_written_key = "courtier|westerosi (andal)|the crownlands"
    pre_written_value = "Ви придворний у Королівській Гавані."

    # Записуємо файл безпосередньо, минаючи cache API
    cache_file: Path = isolated_cache
    cache_file.write_text(
        json.dumps({pre_written_key: pre_written_value}, ensure_ascii=False),
        encoding="utf-8"
    )

    # Скидаємо in-memory стан, щоб симулювати cold start
    ic._CACHE = {}
    ic._LOADED = False

    from core.intro_cache import get_cached_intro
    result = get_cached_intro("Courtier", "Westerosi (Andal)", "The Crownlands")
    assert result == pre_written_value, (
        f"Очікувалося '{pre_written_value}', отримано: {repr(result)}"
    )


# ── Тест 7: get_cache_size ────────────────────────────────────────────────────

def test_get_cache_size():
    """get_cache_size повертає кількість записів у кеші."""
    from core.intro_cache import get_cache_size, set_cached_intro

    async def _run():
        await set_cached_intro("Knight", "Westerosi (Andal)", "The North", "A")
        await set_cached_intro("Maester", "Westerosi (Andal)", "The Reach", "B")

    asyncio.run(_run())
    assert get_cache_size() == 2


# ── Тест 8: clear_cache скидає стан ──────────────────────────────────────────

def test_clear_cache_resets_state(isolated_cache):
    """clear_cache() очищає in-memory кеш і видаляє файл."""
    import core.intro_cache as ic
    from core.intro_cache import set_cached_intro, get_cached_intro, clear_cache, get_cache_size

    asyncio.run(set_cached_intro("Knight", "Westerosi (Andal)", "The North", "X"))
    assert get_cache_size() == 1

    clear_cache()

    assert get_cache_size() == 0
    assert get_cached_intro("Knight", "Westerosi (Andal)", "The North") is None
    assert not isolated_cache.exists(), "Файл кешу повинен бути видалений"
