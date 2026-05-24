"""Intro narrative cache — keyed by (class, heritage, home_region).

For one combination (e.g. Knight + Westerosi Andal + Riverlands), all new players
get the same intro text. This avoids re-calling LLM for identical inputs and provides
instant gratification when cache hits.

Cache file: intro_cache.json in project root (git-ignored).
NB: Race-safe enough for single-process aiogram; for multi-instance prod use Redis.
"""
import json
import asyncio
from pathlib import Path
from typing import Optional

_CACHE_FILE = Path(__file__).parent.parent / "intro_cache.json"
_CACHE: dict[str, str] = {}
_CACHE_LOCK = asyncio.Lock()
_LOADED = False


def _make_key(class_name: str, heritage: str, home_region: str) -> str:
    return f"{class_name.strip().lower()}|{heritage.strip().lower()}|{home_region.strip().lower()}"


def _load_cache_sync() -> None:
    global _CACHE, _LOADED
    if _LOADED:
        return
    if _CACHE_FILE.exists():
        try:
            _CACHE = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[intro_cache] Failed to load {_CACHE_FILE}: {e} — starting empty")
            _CACHE = {}
    _LOADED = True


def _save_cache_sync() -> None:
    """Persist current cache to disk. Atomic via temp file."""
    tmp = _CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def get_cached_intro(class_name: str, heritage: str, home_region: str) -> Optional[str]:
    """Returns cached intro text or None. Loads cache lazily on first call."""
    _load_cache_sync()
    return _CACHE.get(_make_key(class_name, heritage, home_region))


async def set_cached_intro(class_name: str, heritage: str, home_region: str, intro_text: str) -> None:
    """Stores intro in cache and persists to disk asynchronously."""
    async with _CACHE_LOCK:
        _load_cache_sync()
        _CACHE[_make_key(class_name, heritage, home_region)] = intro_text
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
