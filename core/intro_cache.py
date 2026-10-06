"""Intro narrative cache — per-user, keyed by user_id + character fingerprint.

Інтро містить ім'я героя і його родичів, тому кеш НЕ ділиться між гравцями:
запис віддається лише тому самому user_id для того самого персонажа.
Відбиток (sha1 від канонічного JSON: ім'я, клас, heritage, регіон, стать, родина)
змінюється при зміні персонажа -> старий запис не збігається.
На кожен user_id зберігається лише останній запис (новий персонаж витісняє старий),
тож файл росте не швидше за кількість гравців.

Формат файлу: {"<user_id>": {"fp": "<sha1>", "text": "<intro>"}}.
Старі спільні записи (ключ "class|heritage|region[|gender]" -> str) при завантаженні
відкидаються (у памʼяті одразу, на диску — при наступному збереженні).

Cache file: intro_cache.json in project root (git-ignored).
NB: Race-safe enough for single-process aiogram; for multi-instance prod use Redis.
"""
import json
import hashlib
import asyncio
from pathlib import Path
from typing import Optional

_CACHE_FILE = Path(__file__).parent.parent / "intro_cache.json"
_CACHE: dict[str, dict] = {}
_CACHE_LOCK = asyncio.Lock()
_LOADED = False


def _fingerprint(profile: dict) -> str:
    """sha1 від канонічного JSON полів персонажа, що потрапляють в інтро."""
    from core.hero_identity import get_gender, get_family
    profile = profile if isinstance(profile, dict) else {}
    data = {
        "name": str(profile.get("Ім'я", "") or "").strip(),
        "class": str(profile.get("class", "") or "").strip().lower(),
        "heritage": str(profile.get("heritage", "") or "").strip().lower(),
        "region": str(profile.get("Регіон", "") or "").strip().lower(),
        "gender": get_gender(profile),
        "family": get_family(profile),
    }
    canon = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


def _load_cache_sync() -> None:
    global _CACHE, _LOADED
    if _LOADED:
        return
    if _CACHE_FILE.exists():
        try:
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
            _CACHE = {
                str(k): v for k, v in raw.items()
                if isinstance(v, dict) and isinstance(v.get("fp"), str) and isinstance(v.get("text"), str)
            }
            dropped = len(raw) - len(_CACHE)
            if dropped:
                print(f"[intro_cache] dropped {dropped} legacy/invalid entries")
        except Exception as e:
            print(f"[intro_cache] Failed to load {_CACHE_FILE}: {e} — starting empty")
            _CACHE = {}
    _LOADED = True


def _save_cache_sync() -> None:
    """Persist current cache to disk. Atomic via temp file."""
    tmp = _CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def get_cached_intro(user_id, profile: dict) -> Optional[str]:
    """Cached intro for this user + this exact character, or None.
    Lazy sync load on first call (малий локальний JSON; один раз за процес)."""
    _load_cache_sync()
    entry = _CACHE.get(str(user_id))
    if entry and entry.get("fp") == _fingerprint(profile):
        return entry["text"]
    return None


async def set_cached_intro(user_id, profile: dict, intro_text: str) -> None:
    """Stores intro for user (replaces their previous entry) and persists to disk off-loop."""
    async with _CACHE_LOCK:
        _load_cache_sync()
        _CACHE[str(user_id)] = {"fp": _fingerprint(profile), "text": intro_text}
        await asyncio.to_thread(_save_cache_sync)


def get_cache_size() -> int:
    _load_cache_sync()
    return len(_CACHE)


def clear_cache() -> None:
    """Test/admin helper — clear in-memory and on-disk cache."""
    global _CACHE, _LOADED
    _CACHE = {}
    _LOADED = True
    if _CACHE_FILE.exists():
        try:
            _CACHE_FILE.unlink()
        except Exception as e:
            print(f"[intro_cache] clear_cache: failed to delete file: {e}")
