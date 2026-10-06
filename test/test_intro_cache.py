"""
test_intro_cache.py

Unit-тести для core/intro_cache.py (per-user кеш: ключ str(user_id),
значення {"fp": sha1(канонічний JSON персонажа), "text": ...}).

Async-тести запускаються через asyncio.run(). Кожен тест ізольований:
тимчасовий _CACHE_FILE + скидання in-memory стану.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path):
    import core.intro_cache as ic

    tmp_file = tmp_path / "intro_cache.json"
    with patch.object(ic, "_CACHE_FILE", tmp_file):
        ic._CACHE = {}
        ic._LOADED = False
        yield tmp_file
        ic._CACHE = {}
        ic._LOADED = False


FAM = [{"name": "Кейтлін Старк", "relation": "мати"}]


def _profile(**kw):
    p = {
        "Ім'я": "Тест Герой", "class": "Knight", "heritage": "Westerosi (Andal)",
        "Регіон": "The North", "Стать": "чоловіча", "Родина": list(FAM),
    }
    p.update(kw)
    return p


def _reset_memory():
    import core.intro_cache as ic
    ic._CACHE = {}
    ic._LOADED = False


def test_get_missing_returns_none():
    from core.intro_cache import get_cached_intro
    assert get_cached_intro(1, _profile()) is None


def test_set_get_roundtrip():
    from core.intro_cache import get_cached_intro, set_cached_intro
    asyncio.run(set_cached_intro(1, _profile(), "Інтро A"))
    assert get_cached_intro(1, _profile()) == "Інтро A"


def test_user_id_int_and_str_are_same_key():
    from core.intro_cache import get_cached_intro, set_cached_intro
    asyncio.run(set_cached_intro(1, _profile(), "X"))
    assert get_cached_intro("1", _profile()) == "X"


def test_isolation_between_players_same_class_region():
    """Гравець B з тим самим класом/регіоном/heritage не отримує інтро гравця A."""
    from core.intro_cache import get_cached_intro, set_cached_intro
    asyncio.run(set_cached_intro(1, _profile(), "Інтро A"))
    assert get_cached_intro(2, _profile()) is None
    asyncio.run(set_cached_intro(2, _profile(), "Інтро B"))
    assert get_cached_intro(1, _profile()) == "Інтро A"
    assert get_cached_intro(2, _profile()) == "Інтро B"


@pytest.mark.parametrize("change", [
    {"Ім'я": "Інший Герой"},
    {"Стать": "жіноча"},
    {"Родина": [{"name": "Робб Старк", "relation": "брат"}]},
    {"Родина": []},
    {"class": "Maester"},
    {"Регіон": "The Reach"},
])
def test_fingerprint_invalidation_on_character_change(change):
    from core.intro_cache import get_cached_intro, set_cached_intro
    asyncio.run(set_cached_intro(1, _profile(), "Інтро"))
    assert get_cached_intro(1, _profile(**change)) is None


def test_fingerprint_case_insensitive_for_class_heritage_region():
    from core.intro_cache import get_cached_intro, set_cached_intro
    asyncio.run(set_cached_intro(1, _profile(), "Інтро"))
    p = _profile(**{"class": "KNIGHT", "heritage": "WESTEROSI (ANDAL)", "Регіон": "THE NORTH"})
    assert get_cached_intro(1, p) == "Інтро"


def test_one_entry_per_user_replaced():
    """Новий запис того ж user_id витісняє старий."""
    from core.intro_cache import get_cached_intro, get_cache_size, set_cached_intro
    p_old, p_new = _profile(), _profile(**{"Ім'я": "Новий"})
    asyncio.run(set_cached_intro(1, p_old, "Старе"))
    asyncio.run(set_cached_intro(1, p_new, "Нове"))
    assert get_cache_size() == 1
    assert get_cached_intro(1, p_old) is None
    assert get_cached_intro(1, p_new) == "Нове"


def test_cache_persists_to_disk(isolated_cache):
    from core.intro_cache import _fingerprint, set_cached_intro
    p = _profile()
    asyncio.run(set_cached_intro(7, p, "Вступ"))
    cache_file: Path = isolated_cache
    assert cache_file.exists()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data == {"7": {"fp": _fingerprint(p), "text": "Вступ"}}


def test_loads_existing_cache_from_disk(isolated_cache):
    from core.intro_cache import _fingerprint, get_cached_intro
    p = _profile()
    isolated_cache.write_text(
        json.dumps({"5": {"fp": _fingerprint(p), "text": "З диска"}}, ensure_ascii=False),
        encoding="utf-8")
    _reset_memory()
    assert get_cached_intro(5, p) == "З диска"
    assert get_cached_intro(6, p) is None


def test_legacy_entries_dropped_on_load(isolated_cache):
    from core.intro_cache import _fingerprint, get_cache_size, get_cached_intro
    p = _profile()
    isolated_cache.write_text(json.dumps({
        "knight|westerosi (andal)|the north": "legacy-text",
        "knight|westerosi (andal)|the north|жіноча": "legacy-gendered",
        "1": "рядкове значення без fp",
        "2": {"fp": 123, "text": "bad fp"},
        "3": {"fp": "x"},
        "9": {"fp": _fingerprint(p), "text": "валідний"},
    }, ensure_ascii=False), encoding="utf-8")
    _reset_memory()
    assert get_cache_size() == 1
    assert get_cached_intro(9, p) == "валідний"
    assert get_cached_intro(1, p) is None


def test_legacy_purged_from_disk_on_next_save(isolated_cache):
    from core.intro_cache import set_cached_intro
    isolated_cache.write_text(json.dumps({"knight|h|r": "legacy"}), encoding="utf-8")
    _reset_memory()
    asyncio.run(set_cached_intro(1, _profile(), "Нове"))
    data = json.loads(isolated_cache.read_text(encoding="utf-8"))
    assert "knight|h|r" not in data and list(data) == ["1"]


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_corrupt_or_non_dict_file_starts_empty(isolated_cache, content):
    from core.intro_cache import get_cache_size
    isolated_cache.write_text(content, encoding="utf-8")
    _reset_memory()
    assert get_cache_size() == 0


def test_get_cache_size_counts_users():
    from core.intro_cache import get_cache_size, set_cached_intro

    async def _run():
        await set_cached_intro(1, _profile(), "A")
        await set_cached_intro(2, _profile(), "B")
    asyncio.run(_run())
    assert get_cache_size() == 2


def test_clear_cache_resets_state(isolated_cache):
    from core.intro_cache import clear_cache, get_cache_size, get_cached_intro, set_cached_intro
    asyncio.run(set_cached_intro(1, _profile(), "X"))
    assert get_cache_size() == 1
    clear_cache()
    assert get_cache_size() == 0
    assert get_cached_intro(1, _profile()) is None
    assert not isolated_cache.exists()


@pytest.mark.parametrize("bad", [None, "str", [], 5])
def test_non_dict_profile_does_not_crash(bad):
    from core.intro_cache import get_cached_intro
    assert get_cached_intro(1, bad) is None
