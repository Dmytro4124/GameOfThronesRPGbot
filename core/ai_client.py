import hashlib
import json
import logging
import re
import time
import threading
import random
import asyncio
import unicodedata
import contextvars
import uuid
from typing import Optional
from google import genai
from core.prompts import JSON_ONLY_INSTRUCTION
from google.genai import types
from config import (GEMINI_API_KEY, MODEL_MAIN_NAME, MODEL_WORKER_NAME, MODEL_MAIN_TEMP, MODEL_WORKER_TEMP,
                     MODEL_GM_LOGIC_NAME, MODEL_GM_LOGIC_TEMP, MODEL_NARRATOR_NAME, MODEL_NARRATOR_TEMP,
                     MODEL_NARRATOR_ALT_NAME, GEMINI_EXPLICIT_CACHE_ENABLED,
                     NARRATOR_THINKING_LEVEL, NARRATOR_PREAMBLE)

logger = logging.getLogger(__name__)

# Ініціалізація клієнта
client = genai.Client(api_key=GEMINI_API_KEY)

# ─── Unicode normalization ───────────────────────────────────────────────────

def _normalize_prompt(prompt) -> str:
    """NFC-normalize prompt to avoid edge cases with exotic Unicode
    (can occasionally trigger malformed model responses).

    NFC composes characters (e.g. e + combining accent → single codepoint).
    Handles emoji ZWJ sequences, RTL/LTR markers, unusual diacritics.
    Non-string values are returned as-is without crashing.
    """
    if isinstance(prompt, str):
        return unicodedata.normalize("NFC", prompt)
    return prompt


# ─── Circuit Breaker (module-level, per-model) ───────────────────────────────
# Навмисний mutable global: single-process asyncio — race-safe.
# Для multi-instance prod потрібен Redis (deferred).
_CIRCUIT_STATE: dict = {}  # {model_name: {"consecutive_failures": int, "cooldown_until": float}}

DEFAULT_MAX_RETRIES = 3
TRANSIENT_HTTP_CODES = (500, 502, 503, 504)
RATE_LIMIT_CODES = (429,)
PERMANENT_HTTP_CODES = (400, 401, 403, 404)
CIRCUIT_BREAKER_THRESHOLD = 5   # consecutive failures → open
CIRCUIT_BREAKER_COOLDOWN = 60   # seconds


# ─── Schema rejection memo ───────────────────────────────────────────────────
# {(model_name, schema_fingerprint)}: схеми, які API відхилив з 400. Лише add/contains
# (атомарно під GIL) — локи не потрібні. Живе до рестарту процесу.
_REJECTED_SCHEMAS: set = set()


def _schema_fingerprint(schema) -> str:
    """Стабільний ідентифікатор схеми (dict або types.Schema) для _REJECTED_SCHEMAS."""
    try:
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump(exclude_none=True, mode="json")
        raw = json.dumps(schema, sort_keys=True, default=str)
    except Exception:
        raw = repr(schema)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _is_bad_request(e: Exception) -> bool:
    """True для 400 INVALID_ARGUMENT (ClientError.code==400 або текст помилки)."""
    if getattr(e, "code", None) == 400:
        return True
    err_str = str(e)
    return "400" in err_str and "INVALID_ARGUMENT" in err_str


# ─── Explicit context cache за ТЕКСТОМ system_instruction ────────────────────
# Реєстр (model_name, sha256(text)) -> (cache_name, expire_at). Виклики йдуть з
# asyncio.to_thread (різні потоки), тому: _CACHE_LOCK захищає словники (короткі
# секції), per-key lock серіалізує caches.create (мережа), щоб паралельні ходи не
# створили кілька кешів одного тексту. Мережевий виклик НЕ тримає _CACHE_LOCK.
_CACHE_TTL_SECONDS = 3600          # TTL кешу на боці API
_CACHE_RENEW_MARGIN = 300          # перестворюємо за 5 хв до закінчення (запас на довгі ходи/hedge)
_CACHE_MIN_TOKENS = 1024           # мін. розмір кешу для Flash-Lite
_CACHE_CHARS_PER_TOKEN = 3.5       # грубо; кирилиця токенізується гірше -> оцінка консервативна
_CACHE_DENY_TRANSIENT_SECONDS = 300  # тимчасові збої (429/5xx/мережа): повторна спроба через 5 хв
_CACHE_REGISTRY: dict = {}   # key -> (cache_name, expire_at)
_CACHE_DENY: dict = {}       # key -> deny_until (float("inf") = до рестарту процесу)
_CACHE_KEY_LOCKS: dict = {}  # key -> threading.Lock
_CACHE_LOCK = threading.Lock()
_CACHE_ERROR_RE = re.compile(r"cached[\s_]?content", re.IGNORECASE)


def _cache_key(model_name: str, text: str) -> tuple:
    return (model_name, hashlib.sha256(text.encode("utf-8")).hexdigest())


_TRANSIENT_CODES_ALT = "|".join(str(c) for c in TRANSIENT_HTTP_CODES + RATE_LIMIT_CODES)
# Число вважається кодом лише в контексті: на початку рядка, після code/http/status, або перед
# статус-словом (INTERNAL/UNAVAILABLE/...). "500 tokens", "cachedContents/5001", "v2.503" -- не коди.
_TRANSIENT_CODE_RE = re.compile(
    r"(?:(?:^|\bcode|\bhttp|\bstatus)[\s:=\"']*(?:%s)(?![\w/.-]))"
    r"|(?:(?<![\w/.-])(?:%s)\s*[.:]?\s*(?:INTERNAL|UNAVAILABLE|RESOURCE_EXHAUSTED|BAD_GATEWAY|DEADLINE_EXCEEDED)\b)"
    % (_TRANSIENT_CODES_ALT, _TRANSIENT_CODES_ALT), re.IGNORECASE)


def _error_code(e: Exception) -> Optional[int]:
    """HTTP-код з google.genai.errors (ClientError/ServerError .code) або None."""
    code = getattr(e, "code", None)
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _is_transient_error(e: Exception) -> bool:
    """429/5xx/408/мережа. Спершу код/тип помилки; підрядки (з межами слова) -- лише fallback без коду."""
    code = _error_code(e)
    if code is not None:
        return code == 429 or code == 408 or code >= 500
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    err_str = str(e)
    return bool(_TRANSIENT_CODE_RE.search(err_str) or "RESOURCE_EXHAUSTED" in err_str or "UNAVAILABLE" in err_str)


def _is_cache_error(e: Exception) -> bool:
    """True, якщо помилка generate вказує на відсутній/протухлий/недоступний cached_content."""
    if _is_transient_error(e):
        return False
    return bool(_CACHE_ERROR_RE.search(str(e)))


def _cache_lookup_locked(key: tuple, now: float):
    """Під _CACHE_LOCK. Повертає ("hit", name) | ("deny", None) | ("miss", None)."""
    entry = _CACHE_REGISTRY.get(key)
    if entry is not None:
        if now < entry[1]:
            return "hit", entry[0]
        _CACHE_REGISTRY.pop(key, None)
    until = _CACHE_DENY.get(key)
    if until is not None:
        if now < until:
            return "deny", None
        _CACHE_DENY.pop(key, None)
    return "miss", None


def get_or_create_text_cache(model_name: str, text: str) -> Optional[str]:
    """Повертає name explicit-кешу для (model, text) або None (-> inline system_instruction).

    Ніколи не кидає. None якщо: текст явно < порога токенів (без запиту в API),
    текст у deny-списку, або caches.create впав. Deny: постійний (до рестарту) для
    too small / not supported / free tier (limit 0) / 400 / 403 / 404; 5 хв для 429/5xx/мережевих збоїв.
    Потокобезпечно; кеш перестворюється за _CACHE_RENEW_MARGIN до закінчення TTL.
    """
    if not GEMINI_EXPLICIT_CACHE_ENABLED:
        return None
    if not text or len(text) / _CACHE_CHARS_PER_TOKEN < _CACHE_MIN_TOKENS:
        return None
    key = _cache_key(model_name, text)
    with _CACHE_LOCK:
        state, name = _cache_lookup_locked(key, time.time())
        if state != "miss":
            return name
        key_lock = _CACHE_KEY_LOCKS.setdefault(key, threading.Lock())
    with key_lock:
        with _CACHE_LOCK:  # інший потік міг створити, поки ми чекали
            state, name = _cache_lookup_locked(key, time.time())
            if state != "miss":
                return name
        try:
            cache = client.caches.create(
                model=model_name,
                config=types.CreateCachedContentConfig(
                    system_instruction=text,
                    ttl=f"{_CACHE_TTL_SECONDS}s",
                ),
            )
            cache_name = cache.name
            with _CACHE_LOCK:
                _CACHE_REGISTRY[key] = (cache_name, time.time() + _CACHE_TTL_SECONDS - _CACHE_RENEW_MARGIN)
            print(f"[CACHE] {model_name}: created {cache_name} "
                  f"(~{int(len(text) / _CACHE_CHARS_PER_TOKEN)} tok, ttl {_CACHE_TTL_SECONDS}s)")
            return cache_name
        except Exception as e:
            err_str = str(e)
            # Спершу код/тип (ClientError/ServerError.code); "too small" (400) -> постійний deny.
            transient = _is_transient_error(e) and "limit: 0" not in err_str
            until = time.time() + _CACHE_DENY_TRANSIENT_SECONDS if transient else float("inf")
            with _CACHE_LOCK:
                _CACHE_DENY[key] = until
            print(f"[CACHE] {model_name}: inline fallback ({'retry in 5m' if transient else 'until restart'}): "
                  f"{type(e).__name__}: {err_str[:100]}")
            return None


def invalidate_text_cache(model_name: str, text: str, deny_seconds: float = 0) -> None:
    """Прибирає запис реєстру (кеш протух/видалений). Без deny_seconds наступний виклик одразу
    створить новий; з deny_seconds > 0 ключ ще й блокується на цей час (анти-churn)."""
    key = _cache_key(model_name, text)
    with _CACHE_LOCK:
        _CACHE_REGISTRY.pop(key, None)
        if deny_seconds > 0:
            until = time.time() + deny_seconds
            if _CACHE_DENY.get(key, 0) < until:
                _CACHE_DENY[key] = until


def get_circuit_breaker_status(model_name: str) -> dict:
    """Повертає поточний стан circuit breaker для вказаної моделі. Для адмін-діагностики."""
    cb = _CIRCUIT_STATE.get(model_name, {})
    now = time.time()
    return {
        "model": model_name,
        "consecutive_failures": cb.get("consecutive_failures", 0),
        "cooldown_remaining": max(0.0, cb.get("cooldown_until", 0.0) - now),
        "is_open": cb.get("cooldown_until", 0.0) > now,
    }


# ─── Debug call-meta capture (per-task через ContextVar, як _thoughts_log_var) ───────────
# Вмикається engine-ом лише для debug-гравця: start_call_meta_capture(). Список ділиться за
# посиланням з worker-threads (asyncio.to_thread копіює context) — лише append (атомарно під GIL).
_call_meta_var: contextvars.ContextVar = contextvars.ContextVar("call_meta", default=None)
# Контекст конкретного виклику (hedge_idx / label), виставляється у worker-thread hedged-обгортки.
_call_ctx_var: contextvars.ContextVar = contextvars.ContextVar("call_meta_ctx", default=None)

_SECRET_PATTERNS = (
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "AIza***"),
    (re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"), "***:***"),
    (re.compile(r"(?i)\b((?:api[_-]?)?key|token|access_token)=([^&\s'\"]+)"), r"\1=***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"(?i)\bAuthorization\s*[:=]\s*Basic\s+[A-Za-z0-9+/=._~-]+"), "Authorization: Basic ***"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/]{16,}={0,2}"), "Basic ***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "***PRIVATE KEY***"),
    (re.compile(r"(?i)([\"']?(?:client_secret|private_key|refresh_token)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,&}]+)"), r"\1***"),
)


def mask_secrets(text: str, limit: int = 200) -> str:
    """Маскує API-ключі/токени у тексті й обрізає до `limit` символів (з "…")."""
    try:
        s = "" if text is None else str(text)
        for pat, repl in _SECRET_PATTERNS:
            s = pat.sub(repl, s)
        if limit is not None and len(s) > limit:
            s = s[:limit] + "…"
        return s
    except Exception:
        return ""


def start_call_meta_capture() -> None:
    _call_meta_var.set([])


def stop_call_meta_capture() -> None:
    _call_meta_var.set(None)


def get_call_meta() -> list:
    lst = _call_meta_var.get()
    return lst if lst is not None else []


def call_meta_mark() -> int:
    return len(get_call_meta())


def _enum_name(v):
    if v is None:
        return None
    return getattr(v, "name", None) or str(v)


def _new_call_meta(model_name, path, meta_label=None):
    """Новий запис або None, якщо збір вимкнено (або збій). Ніколи не кидає."""
    try:
        sink = _call_meta_var.get()
        if sink is None:
            return None
        ctx = _call_ctx_var.get() or {}
        rec = {
            "model": model_name, "path": path,
            "label": meta_label if meta_label is not None else ctx.get("label"),
            "elapsed_s": None, "attempts": [], "finish_reason": None, "block_reason": None,
            "safety": [], "text_len": 0,
            "usage": {"prompt": None, "candidates": None, "thoughts": None, "cached": None, "total": None},
            "schema_sent": False, "schema_retry": False, "cache_mode": "none", "cache_fallback": False,
            "exception": None, "breaker_open": False,
            "_sink": sink, "_t0": time.monotonic(),
        }
        if "hedge_idx" in ctx:
            rec["hedge_idx"] = ctx["hedge_idx"]
            rec["winner"] = None
            if "hedge_group" in ctx:
                rec["hedge_group"] = ctx["hedge_group"]
                rec["_gstate"] = ctx.get("gstate")
        return rec
    except Exception:
        return None


def _meta_commit(meta) -> None:
    try:
        sink = meta.pop("_sink", None)
        t0 = meta.pop("_t0", None)
        gstate = meta.pop("_gstate", None)
        if gstate is not None and gstate.get("winner") is not None:
            # переможця групи вже визначено -> пізній програшний запис = False (не None)
            meta["winner"] = (meta.get("hedge_idx") == gstate["winner"])
        if t0 is not None:
            meta["elapsed_s"] = round(time.monotonic() - t0, 3)
        if sink is not None:
            sink.append(meta)
    except Exception:
        pass


def _meta_set_exception(meta, e) -> None:
    try:
        meta["exception"] = {"type": type(e).__name__, "msg": mask_secrets(str(e))}
    except Exception:
        pass


def _meta_before_call(meta, cfg, cache_fallback) -> None:
    try:
        meta["schema_sent"] = getattr(cfg, "response_schema", None) is not None
        if getattr(cfg, "cached_content", None):
            meta["cache_mode"] = "cached"
        elif isinstance(getattr(cfg, "system_instruction", None), str) and cfg.system_instruction:
            meta["cache_mode"] = "inline"
        else:
            meta["cache_mode"] = "none"
        if cache_fallback:
            meta["cache_fallback"] = True
    except Exception:
        pass


def _meta_add_attempt(meta, n, e):
    if meta is None:
        return None
    try:
        rec = {"n": n, "error_code": _error_code(e), "error": mask_secrets(str(e)), "sleep_s": None}
        meta["attempts"].append(rec)
        return rec
    except Exception:
        return None


def _meta_absorb_response(meta, resp, accumulate_text=False) -> None:
    """Витягує finish_reason/block_reason/safety/usage/text_len з відповіді або stream-чанка."""
    try:
        pf = getattr(resp, "prompt_feedback", None)
        br = _enum_name(getattr(pf, "block_reason", None)) if pf is not None else None
        if br:
            meta["block_reason"] = br
        cands = getattr(resp, "candidates", None) or []
        text_len = 0
        if cands:
            cand = cands[0]
            fr = _enum_name(getattr(cand, "finish_reason", None))
            if fr:
                meta["finish_reason"] = fr
            ratings = getattr(cand, "safety_ratings", None)
            if ratings:
                meta["safety"] = [
                    f"{_enum_name(getattr(r, 'category', None))}:{_enum_name(getattr(r, 'probability', None))}"
                    f"{'(blocked)' if getattr(r, 'blocked', False) else ''}"
                    for r in ratings
                ]
            parts = getattr(getattr(cand, "content", None), "parts", None) or []
            for p in parts:
                if not getattr(p, "thought", False):
                    text_len += len(getattr(p, "text", None) or "")
        if accumulate_text:
            meta["text_len"] += text_len
        else:
            meta["text_len"] = text_len
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            meta["usage"] = {
                "prompt": getattr(um, "prompt_token_count", None),
                "candidates": getattr(um, "candidates_token_count", None),
                "thoughts": getattr(um, "thoughts_token_count", None),
                "cached": getattr(um, "cached_content_token_count", None),
                "total": getattr(um, "total_token_count", None),
            }
    except Exception:
        pass


def _wrap_stream_meta(stream, meta, state=None):
    """Прозорий генератор над stream: збирає метадані, запис додає у finally."""
    try:
        for chunk in stream:
            _meta_absorb_response(meta, chunk, accumulate_text=True)
            yield chunk
    except GeneratorExit:
        raise
    except BaseException as e:
        _meta_set_exception(meta, e)
        raise
    finally:
        try:
            if state and state.get("cache_fallback"):
                meta["cache_fallback"] = True
        except Exception:
            pass
        _meta_commit(meta)


def _meta_mark_winner(group, idx, gstate=None) -> None:
    """Позначає переможця лише в записах групи `group` (один виклик hedged_generate_content_async)."""
    try:
        if gstate is not None:
            gstate["winner"] = idx
        for rec in get_call_meta():
            if rec.get("hedge_group") == group and "hedge_idx" in rec:
                rec["winner"] = (rec["hedge_idx"] == idx)
    except Exception:
        pass


class AIWrapper:
    """Обгортка над google-genai моделлю: retry/backoff, circuit breaker, thinking, prompt caching.

    JSON-режим вмикається через response_mime_type (параметр конструктора або build_strict_config).
    """

    def __init__(self, model_name, temperature=0.7, max_output_tokens=None, thinking_budget=None, thinking_level=None, include_thoughts=False, response_mime_type=None, block_none=False, system_instruction=None):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level
        self.include_thoughts = include_thoughts
        self.response_mime_type = response_mime_type
        self.block_none = block_none
        self.system_instruction = system_instruction
        # ── Prompt caching (Gemini cached_content) ──────────────────────────
        self._cached_content_name: Optional[str] = None
        self._cache_attempted: bool = False
        self._cache_deny_until: float = 0.0  # після cache-fallback не пробуємо create до цього часу

    def _ensure_cache(self) -> Optional[str]:
        """Creates a cached_content for system_instruction (one-time per process).

        Idempotent: subsequent calls return the existing cache name immediately.
        Tolerant: if caching is not supported for this model, returns None and
        falls back to inline system_instruction on every call.

        Skips caching if system_instruction is shorter than 200 chars — too
        small to yield meaningful token savings.

        Threshold lowered from 1000 → 200 to include model_narrator system_instruction
        (~269 chars NSFW preamble). Verified: model/model_worker/model_gm_logic have
        system_instruction=None so they skip caching regardless of threshold.

        WARNING: This method is NOT thread-safe. Currently safe because only
        model_narrator has system_instruction set (everyone else has system_instruction=None
        and skips early on the guard). If you add system_instruction to model_worker or
        model_gm_logic in the future, wrap _ensure_cache invocations with a lock or
        ensure they happen only from the event loop (not from asyncio.to_thread threads).
        """
        if not GEMINI_EXPLICIT_CACHE_ENABLED:
            return None
        if self._cache_attempted:
            return self._cached_content_name
        if time.time() < self._cache_deny_until:
            return None
        self._cache_attempted = True
        if not self.system_instruction or len(self.system_instruction) < 200:
            return None
        try:
            from google.genai import types as _gtypes
            cache = client.caches.create(
                model=self.model_name,
                config=_gtypes.CreateCachedContentConfig(
                    system_instruction=self.system_instruction,
                    ttl="3600s",
                )
            )
            self._cached_content_name = cache.name
            print(f"[CACHE] {self.model_name}: cached_content={cache.name}")
            return cache.name
        except Exception as e:
            print(f"[CACHE] {self.model_name}: caching not supported or failed: {e}")
            self._cached_content_name = None
            return None

    def _build_config(self, system_instruction=None):
        config_args = {"temperature": self.temperature}
        if self.max_output_tokens:
            config_args["max_output_tokens"] = self.max_output_tokens
        if self.thinking_level is not None or self.thinking_budget is not None or self.include_thoughts:
            tc_args = {}
            if self.thinking_level is not None:
                tc_args["thinking_level"] = self.thinking_level
            if self.thinking_budget is not None:
                tc_args["thinking_budget"] = self.thinking_budget
            if self.include_thoughts:
                tc_args["include_thoughts"] = True
            config_args["thinking_config"] = types.ThinkingConfig(**tc_args)
        if self.response_mime_type:
            config_args["response_mime_type"] = self.response_mime_type
        if self.block_none:
            config_args["safety_settings"] = [
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",        threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
            ]
        if system_instruction:
            # Per-call system_instruction: ЗАВЖДИ inline у конфізі (мережі тут немає: config_with
            # викликається з event loop). Явний кеш підставляє generate_content/_upgrade_config у thread.
            # Wrapper-level преамбула (narrator: safety/NSFW) лишається першою частиною тексту.
            if self.system_instruction:
                system_instruction = f"{self.system_instruction}\n\n{system_instruction}"
            config_args["system_instruction"] = system_instruction
            return types.GenerateContentConfig(**config_args)
        # Use cached_content when available, otherwise fall back to inline system_instruction
        cache_name = self._ensure_cache()
        if cache_name:
            config_args["cached_content"] = cache_name
        elif self.system_instruction:
            config_args["system_instruction"] = self.system_instruction
        return types.GenerateContentConfig(**config_args)

    def config_with(self, **overrides) -> "types.GenerateContentConfig":
        """Повертає КОПІЮ повного ефективного конфіга обгортки з перевизначеними полями.

        Базою є `_build_config()` (той самий конфіг, що й у звичайному generate-шляху):
        temperature, max_output_tokens, thinking_config, safety_settings,
        response_mime_type і cached_content АБО system_instruction (взаємовиключно).
        `_build_config` щоразу створює новий об'єкт, а `model_copy` ще раз копіюється,
        тож стан обгортки не мутується.

        Приклад: wrapper.config_with(temperature=0.5)

        `system_instruction=TEXT` (per-call, статична частина промпта ролі): конфіг отримує
        inline system_instruction = [wrapper.system_instruction + "\n\n"] + TEXT (преамбула
        wrapper-а, напр. narrator NSFW-контекст, зберігається) і БЕЗ cached_content.
        Перетворення на cached_content (якщо текст >= порога і кеш вдалося створити) робить
        generate_content / generate_content_stream у worker-thread (_upgrade_config), тому
        config_with не робить мережевих викликів. Обидва поля одночасно не потрапляють у конфіг.
        Без TEXT: поведінка як раніше (wrapper-level system_instruction / кеш).
        """
        overrides = dict(overrides)
        si = overrides.pop("system_instruction", None)
        return self._build_config(system_instruction=si).model_copy(update=overrides)

    def _upgrade_config(self, cfg):
        """Inline system_instruction (str) -> cached_content, якщо кеш існує/створюється.

        Повертає (cfg, inline_text): inline_text != None лише коли підставлено кеш
        (потрібен для inline-fallback при протуханні). Викликати ТІЛЬКИ з worker-thread
        (може робити caches.create). Не кидає.
        """
        if not GEMINI_EXPLICIT_CACHE_ENABLED:
            return cfg, None
        try:
            si = getattr(cfg, "system_instruction", None)
            if getattr(cfg, "cached_content", None) or not isinstance(si, str) or not si:
                return cfg, None
            name = get_or_create_text_cache(self.model_name, si)
            if not name:
                return cfg, None
            return cfg.model_copy(update={"system_instruction": None, "cached_content": name}), si
        except Exception as e:
            logger.warning(f"[CACHE] {self.model_name}: upgrade failed, inline: {type(e).__name__}: {str(e)[:100]}")
            return cfg, None

    def _cache_inline_text(self, cfg, cache_text):
        """Текст для inline-fallback, коли cached_content у cfg не спрацював (або None)."""
        if cache_text:
            return cache_text
        if getattr(cfg, "cached_content", None) and cfg.cached_content == self._cached_content_name:
            return self.system_instruction
        return None

    def _drop_cache(self, cfg, text):
        """Інвалідує реєстр/wrapper-кеш після помилки cached_content."""
        # deny на ключ: без churn create->fail->inline на кожному ході при стійкій помилці
        invalidate_text_cache(self.model_name, text, deny_seconds=_CACHE_DENY_TRANSIENT_SECONDS)
        if getattr(cfg, "cached_content", None) == self._cached_content_name:
            self._cached_content_name = None
            self._cache_attempted = False  # після deny-вікна спробує створити заново
            self._cache_deny_until = time.time() + _CACHE_DENY_TRANSIENT_SECONDS
        # один канал (logger), щоб раннер не рахував подію двічі
        logger.warning(f"[CACHE] {self.model_name}: cached content invalid/expired -> inline retry")

    def generate_content(self, prompt, max_retries=DEFAULT_MAX_RETRIES, config=None, meta_label=None):
        """Синхронний виклик Gemini (див. _generate_content_inner) + збір debug-метаданих.

        Збір (start_call_meta_capture) вмикається лише engine для debug-гравця; помилка збору
        ніколи не впливає на виклик моделі.
        """
        meta = _new_call_meta(self.model_name, "generate", meta_label)
        if meta is None:
            return self._generate_content_inner(prompt, max_retries, config, None)
        try:
            return self._generate_content_inner(prompt, max_retries, config, meta)
        except BaseException as e:
            _meta_set_exception(meta, e)
            raise
        finally:
            _meta_commit(meta)

    def _generate_content_inner(self, prompt, max_retries, config, meta):
        """Синхронний виклик Gemini з retry/backoff і circuit breaker.

        Schema fallback: якщо з активним response_schema API повертає 400, робиться
        рівно один повтор без схеми. Цей повтор НЕ споживає retry-бюджет (attempt),
        без sleep і не рахується в circuit breaker. Схема потрапляє в _REJECTED_SCHEMAS
        (тобто наступні виклики одразу йдуть без неї) ЛИШЕ якщо повтор без схеми
        завершився успіхом; якщо й він падає — схема не запам'ятовується, помилка
        йде звичайним шляхом (permanent -> raise).
        """
        prompt = _normalize_prompt(prompt)
        # ── 1. Circuit breaker check ──────────────────────────────────────────
        cb = _CIRCUIT_STATE.setdefault(
            self.model_name,
            {"consecutive_failures": 0, "cooldown_until": 0.0}
        )
        now = time.time()
        if cb["cooldown_until"] > now:
            wait = cb["cooldown_until"] - now
            if meta is not None:
                meta["breaker_open"] = True
            print(f"[CIRCUIT BREAKER] {self.model_name} in cooldown for {wait:.1f}s more — fast fail")
            raise RuntimeError(f"AIWrapper circuit breaker open for {self.model_name}")

        # Override config from caller wins (e.g. for low-temp fallback narrator).
        # Otherwise build default config from self.
        effective_config = config if config is not None else self._build_config()
        last_error = None

        # ── Schema fallback state ─────────────────────────────────────────────
        # Якщо config має response_schema, яку API вже відхиляв для цієї моделі —
        # одразу йдемо без неї (не палимо квоту на гарантований 400).
        schema_key = None
        pending_reject_key = None  # схема, відхилена 400; фіксується лише після успішного повтору без неї
        if getattr(effective_config, "response_schema", None) is not None:
            schema_key = (self.model_name, _schema_fingerprint(effective_config.response_schema))
            if schema_key in _REJECTED_SCHEMAS:
                effective_config = effective_config.model_copy(update={"response_schema": None})
                schema_key = None

        # ── Explicit cache: inline system_instruction -> cached_content (у thread) ──
        effective_config, cache_text = self._upgrade_config(effective_config)
        cache_fallback_used = False

        attempt = 0
        while attempt < max_retries:
            attempt += 1
            if meta is not None:
                _meta_before_call(meta, effective_config, cache_fallback_used)
            try:
                raw = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=effective_config
                )
                if meta is not None:
                    _meta_absorb_response(meta, raw)
                # SUCCESS — reset circuit breaker
                cb["consecutive_failures"] = 0
                cb["cooldown_until"] = 0.0
                if pending_reject_key is not None:
                    _REJECTED_SCHEMAS.add(pending_reject_key)
                    logger.warning(
                        f"[AIWrapper {self.model_name}] schema remembered as rejected "
                        f"(retry without schema succeeded)"
                    )
                    pending_reject_key = None
                if self.include_thoughts:
                    try:
                        thoughts, content = split_thoughts(raw)
                        if thoughts:
                            record_thought(self.model_name, thoughts)
                        return _AIResponse(content)
                    except Exception as e:
                        logger.warning(
                            f"[AIWrapper {self.model_name}] split_thoughts failed (MALFORMED?): "
                            f"{type(e).__name__}: {str(e)[:120]}"
                        )
                        return raw  # повертаємо raw, нехай caller обробить через clean_and_parse_json
                return raw

            except Exception as e:
                last_error = e
                err_str = str(e)
                att_rec = _meta_add_attempt(meta, attempt, e)

                # ── 2. Classify error ─────────────────────────────────────────
                is_transient = (
                    any(str(code) in err_str for code in TRANSIENT_HTTP_CODES)
                    or "INTERNAL" in err_str
                    or "UNAVAILABLE" in err_str
                )
                is_rate_limit = (
                    any(str(code) in err_str for code in RATE_LIMIT_CODES)
                    or "RESOURCE_EXHAUSTED" in err_str
                )
                is_permanent = (
                    any(str(code) in err_str for code in PERMANENT_HTTP_CODES)
                    or "INVALID_ARGUMENT" in err_str
                    or "PERMISSION_DENIED" in err_str
                )

                # Cached content missing/expired: one retry with inline system_instruction.
                # Not a breaker failure, attempt not consumed (як і schema fallback).
                if (not cache_fallback_used and getattr(effective_config, "cached_content", None)
                        and _is_cache_error(e)):
                    inline_text = self._cache_inline_text(effective_config, cache_text)
                    if inline_text:
                        self._drop_cache(effective_config, inline_text)
                        effective_config = effective_config.model_copy(
                            update={"cached_content": None, "system_instruction": inline_text})
                        cache_fallback_used = True
                        attempt -= 1
                        continue

                # Schema rejected (400) — one retry of the same request without
                # response_schema. Not a breaker failure, not a transient retry
                # (attempt counter is not consumed).
                if schema_key is not None and _is_bad_request(e):
                    logger.warning(
                        f"[AIWrapper {self.model_name}] response_schema rejected (400), "
                        f"schema rejected, retrying without schema: {err_str[:200]}"
                    )
                    effective_config = effective_config.model_copy(update={"response_schema": None})
                    if meta is not None:
                        meta["schema_retry"] = True
                    # Запам'ятовуємо лише якщо повтор без схеми успішний (див. SUCCESS).
                    pending_reject_key = schema_key
                    schema_key = None
                    attempt -= 1
                    continue

                # Permanent errors — fail immediately, no retry
                if is_permanent:
                    print(f"[AIWrapper {self.model_name}] PERMANENT error: {err_str[:120]} — no retry")
                    raise

                # Last attempt — bump circuit breaker counter, then raise
                if attempt == max_retries:
                    cb["consecutive_failures"] += 1
                    if cb["consecutive_failures"] >= CIRCUIT_BREAKER_THRESHOLD:
                        cb["cooldown_until"] = time.time() + CIRCUIT_BREAKER_COOLDOWN
                        print(
                            f"[CIRCUIT BREAKER OPEN] {self.model_name}: "
                            f"{cb['consecutive_failures']} consecutive failures "
                            f"-> cooldown {CIRCUIT_BREAKER_COOLDOWN}s"
                        )
                    raise

                # ── 3. Compute backoff ────────────────────────────────────────
                if is_rate_limit:
                    # 429 / RESOURCE_EXHAUSTED — long backoff: 30, 60, 120 capped
                    base_delay = min(30 * (2 ** (attempt - 1)), 120)
                elif is_transient:
                    # 500/502/503/504 — exponential: 5, 10, 20, 40, 60 capped
                    base_delay = min(5 * (2 ** (attempt - 1)), 60)
                else:
                    # Unknown — moderate linear backoff
                    base_delay = min(3 * attempt, 30)

                # Jitter ±20% to avoid thundering herd
                jitter = base_delay * 0.2 * (2 * random.random() - 1)
                delay = max(1.0, base_delay + jitter)
                if att_rec is not None:
                    att_rec["sleep_s"] = round(delay, 2)

                err_class = (
                    "transient 5xx" if is_transient
                    else ("rate limit" if is_rate_limit else "unknown")
                )
                print(
                    f"[AIWrapper {self.model_name}] retry {attempt}/{max_retries} "
                    f"({err_class}): {err_str[:100]} — sleep {delay:.1f}s"
                )
                time.sleep(delay)

        raise last_error

    def generate_content_stream(self, prompt, config=None, meta_label=None):
        """Повертає синхронний ітератор чанків для streaming.

        `config` (опційно, напр. wrapper.config_with(system_instruction=...)). Якщо inline
        system_instruction замінено на cached_content і кеш невалідний ДО першого чанка --
        один повтор з inline (як у generate_content). Викликати з worker-thread.

        Якщо debug-збір увімкнено — ітератор обгортається в генератор, що додає запис у
        call-meta після вичерпання/падіння стріму (finally).
        """
        prompt = _normalize_prompt(prompt)
        meta = _new_call_meta(self.model_name, "stream", meta_label)
        cfg = config if config is not None else self._build_config()
        cfg, cache_text = self._upgrade_config(cfg)
        if meta is not None:
            _meta_before_call(meta, cfg, False)
        if cache_text is None:
            try:
                stream = client.models.generate_content_stream(
                    model=self.model_name,
                    contents=prompt,
                    config=cfg
                )
            except BaseException as e:
                if meta is not None:
                    _meta_set_exception(meta, e)
                    _meta_commit(meta)
                raise
            return stream if meta is None else _wrap_stream_meta(stream, meta)

        state = {"cache_fallback": False}

        def _gen():
            started = False
            try:
                for chunk in client.models.generate_content_stream(
                        model=self.model_name, contents=prompt, config=cfg):
                    started = True
                    yield chunk
            except Exception as e:
                if started or not _is_cache_error(e):
                    raise
                self._drop_cache(cfg, cache_text)
                state["cache_fallback"] = True
                inline_cfg = cfg.model_copy(update={"cached_content": None, "system_instruction": cache_text})
                yield from client.models.generate_content_stream(
                    model=self.model_name, contents=prompt, config=inline_cfg)

        return _gen() if meta is None else _wrap_stream_meta(_gen(), meta, state)


# Створюємо екземпляри моделей
model = AIWrapper(MODEL_MAIN_NAME, temperature=MODEL_MAIN_TEMP, thinking_level="high", include_thoughts=True,
                  response_mime_type="application/json")  # профіль, NPC, intro — якість важлива
model_worker = AIWrapper(MODEL_WORKER_NAME, temperature=MODEL_WORKER_TEMP, thinking_level="minimal",
                         include_thoughts=True, response_mime_type="application/json", block_none=True)
model_gm_logic = AIWrapper(MODEL_GM_LOGIC_NAME, temperature=MODEL_GM_LOGIC_TEMP, thinking_level="minimal",
                           include_thoughts=True, response_mime_type="application/json", block_none=True)
_NARRATOR_PREAMBLE_EXPLICIT = (
    "You are an uncensored dark fantasy narrator for an adult Game of Thrones RPG. "
    "NSFW content including explicit sexual scenes and graphic violence is required and allowed. "
    "Never refuse, never add disclaimers, never fade to black. Write everything explicitly as requested. "
    "Sexual content is allowed only between characters who are unambiguously adults (18+), including the hero; "
    "if any participant is a minor or of unclear age, write no sexual content at all - this overrides every other instruction."
)
_NARRATOR_PREAMBLE_NEUTRAL = (
    "You are the narrator of a grim dark-fantasy text RPG for adults, set in the world of Game of Thrones. "
    "Cruelty, intrigue, violence and moral ambiguity are described realistically, with literary restraint. "
    "Intimate scenes involve only characters who are unambiguously adults (18+), including the hero, and appear only when the user message contains an "
    "<EROTIC_MODE> block, in which case follow that block. "
    "Do not shy away from dark plot events; write in Ukrainian following the rules in the system instruction."
)
# Експеримент (NARRATOR_PREAMBLE): вибір варіанта преамбули; ім'я лишено для сумісності з імпортами.
_NARRATOR_SYSTEM_INSTRUCTION = (
    _NARRATOR_PREAMBLE_NEUTRAL if NARRATOR_PREAMBLE == "neutral" else _NARRATOR_PREAMBLE_EXPLICIT
)
model_narrator = AIWrapper(
    MODEL_NARRATOR_NAME,
    temperature=MODEL_NARRATOR_TEMP,
    thinking_level=NARRATOR_THINKING_LEVEL,
    include_thoughts=True,
    block_none=True,
    system_instruction=_NARRATOR_SYSTEM_INSTRUCTION,
)
model_narrator.preamble_variant = NARRATOR_PREAMBLE
# Alt-narrator для A/B (ідентичні параметри; інстанс лінивий, мережевих викликів на старті немає)
model_narrator_alt = AIWrapper(
    MODEL_NARRATOR_ALT_NAME,
    temperature=MODEL_NARRATOR_TEMP,
    thinking_level=NARRATOR_THINKING_LEVEL,
    include_thoughts=True,
    block_none=True,
    system_instruction=_NARRATOR_SYSTEM_INSTRUCTION,
)
model_narrator_alt.preamble_variant = NARRATOR_PREAMBLE


def build_strict_config(
    model_wrapper: "AIWrapper",
    schema=None,
    temperature: float = None,
    system_instruction: Optional[str] = None,
) -> "types.GenerateContentConfig":
    """Build a strict-JSON GenerateContentConfig from a wrapper's full effective config.

    Inherits EVERYTHING from `model_wrapper` (via AIWrapper.config_with):
    thinking_config, max_output_tokens, safety_settings, cached_content /
    system_instruction. Overrides only response_mime_type="application/json"
    and (optionally) temperature.

    If `schema` is not None it is passed as `response_schema` (OpenAPI-subset
    dict / types.Schema). If the API rejects it with 400, AIWrapper.generate_content
    transparently retries without it and remembers the rejection (see
    _REJECTED_SCHEMAS). JSON keys are still enforced by prompts and
    clean_and_parse_json (CLAUDE.md §5.3) as the second line of defence.

    Args:
        model_wrapper: AIWrapper (e.g. model_worker / model_gm_logic) to inherit from.
        schema: Optional response_schema (dict or types.Schema). None = prompt-only JSON.
        temperature: Override temperature. If None, uses model_wrapper.temperature.
        system_instruction: Per-call static system text (див. AIWrapper.config_with). Конфіг
            містить його inline; generate_content у thread підміняє на cached_content, якщо
            кеш доступний. None = wrapper-level поведінка.

    Returns:
        types.GenerateContentConfig to pass as model_wrapper.generate_content(..., config=cfg).
    """
    overrides: dict = {"response_mime_type": "application/json"}
    if temperature is not None:
        overrides["temperature"] = temperature
    if schema is not None:
        overrides["response_schema"] = schema
    if system_instruction:
        overrides["system_instruction"] = system_instruction
    return model_wrapper.config_with(**overrides)


class _AIResponse:
    """Мінімальний wrapper — повертає лише content-частину (без thought-parts) через .text."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


# ─── Per-task лог роздумів (ізольований між гравцями через ContextVar) ──────
# ContextVar ізолює стан між asyncio Tasks. Кожен Telegram update = окремий Task
# = окремий context. asyncio.to_thread propagates context у worker thread (Python 3.9+),
# тому record_thought, що викликається з generate_content (через to_thread), бачить
# правильний per-task list.
#
# Принцип роботи:
#   clear_thoughts() → _thoughts_log_var.set([]) — створює list binding у ЦЬОМУ task context.
#   asyncio.to_thread копіює поточний context у worker thread.
#   record_thought у thread: .get() → той самий list object (mutable) → .append() мутує його.
#   get_thoughts_log() у task context → той самий list → бачить всі appends.
#   Player B's Task має ОКРЕМИЙ context → окремий list binding → clear_thoughts() B не
#   торкається list гравця A.
_thoughts_log_var: contextvars.ContextVar[list] = contextvars.ContextVar("thoughts_log")


def clear_thoughts() -> None:
    _thoughts_log_var.set([])


def get_thoughts_log() -> list:
    try:
        return list(_thoughts_log_var.get())
    except LookupError:
        return []


def record_thought(model_name: str, thought: str) -> None:
    if not thought:
        return
    try:
        log = _thoughts_log_var.get()
    except LookupError:
        # Defensive fallback: clear_thoughts() не було викликано у цьому context.
        # process_game_turn завжди викликає clear_thoughts() на старті — цей branch
        # спрацьовує тільки якщо record_thought викликали обхідним шляхом поза turn-ом.
        # NOTE: .set() тут не propagate назад з to_thread worker (копія context),
        # але цей branch лише для edge-cases поза нормальним pipeline.
        log = []
        _thoughts_log_var.set(log)
    log.append({"model": model_name, "thought": thought})


def split_thoughts(response) -> tuple:
    """Розділяє відповідь на (thoughts_str, content_str).
    Якщо include_thoughts не використовувався — повертає ('', response.text).
    """
    thoughts_parts, content_parts = [], []
    try:
        content = response.candidates[0].content
        parts = getattr(content, "parts", None) or []  # контент-блок: content/parts=None
        for part in parts:
            if getattr(part, "thought", False):
                thoughts_parts.append(part.text or "")
            else:
                content_parts.append(part.text or "")
    except (AttributeError, IndexError):
        return "", response.text or ""
    return "\n".join(thoughts_parts), "".join(content_parts)


def _fix_invalid_escapes(s: str) -> str:
    """
    Виправляє невалідні escape-послідовності у JSON-рядку.
    Стратегія: замінює одиничний \\, за яким іде не-валідний символ, на \\\\,
    що дозволяє json.loads розпарсити його як літеральний бекслеш.

    JSON дозволяє лише: \" \\ \/ \b \f \n \r \t \\uXXXX
    """
    # Placeholder, який гарантовано не зустрічається у JSON
    PLACEHOLDER = "\x00DBLSLASH\x00"

    # Крок 1: захистити вже валідні \\ від подвійної обробки
    s = s.replace('\\\\', PLACEHOLDER)

    # Крок 2: знайти \ за яким іде НЕ-валідний escape-символ і замінити на \\
    # Negative lookahead: не чіпаємо " \ / b f n r t u + 4 hex
    s = re.sub(r'\\(?!["\\\/bfnrt]|u[0-9a-fA-F]{4})', r'\\\\', s)

    # Крок 3: відновити оригінальні \\
    s = s.replace(PLACEHOLDER, '\\\\')

    return s


def clean_and_parse_json(text):
    """Витягує JSON з тексту. Підтримує {} і []. Stack-based bracket matching.
    Шарова оборона проти невалідних escape-послідовностей від LLM."""
    if not text:
        return None

    try:
        # Крок 1: Стрипінг всіх варіантів markdown-фенсів (```json, ```JSON, ~~~json, тощо)
        text = re.sub(r'^\s*[`~]{3,}\s*\w*\s*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[`~]{3,}\s*$', '', text, flags=re.MULTILINE)
        text = text.strip()

        # Крок 2: Знайти початок JSON-структури
        start_obj = text.find('{')
        start_arr = text.find('[')
        possible_starts = [i for i in [start_obj, start_arr] if i != -1]
        if not possible_starts:
            return None

        start_index = min(possible_starts)
        opening_char = text[start_index]
        closing_char = '}' if opening_char == '{' else ']'

        # Крок 3: Stack-based сканування — коректно ігнорує дужки всередині рядків
        depth = 0
        in_string = False
        escaped = False
        end_index = -1

        for i in range(start_index, len(text)):
            ch = text[i]
            if escaped:
                escaped = False
                continue
            if ch == '\\' and in_string:
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opening_char:
                depth += 1
            elif ch == closing_char:
                depth -= 1
                if depth == 0:
                    end_index = i
                    break

        if end_index == -1 or end_index <= start_index:
            return None

        json_str = text[start_index: end_index + 1]

    except Exception as e:
        print(f"⚠️ Помилка екстракції JSON: {e}")
        return None

    # Шар 1: стандартний парсинг (strict=False дозволяє літеральні переноси рядків)
    try:
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError as e:
        snippet = json_str[max(0, e.pos - 40): e.pos + 40]
        print(f"⚠️ JSONDecodeError шар 1: {e} | Фрагмент: {snippet!r}")

    # Шар 2: виправити невалідні escape-послідовності → повторний парсинг
    try:
        sanitized = _fix_invalid_escapes(json_str)
        return json.loads(sanitized, strict=False)
    except json.JSONDecodeError as e:
        snippet = json_str[max(0, e.pos - 40): e.pos + 40]
        print(f"⚠️ JSONDecodeError шар 2 (після escape-fix): {e} | Фрагмент: {snippet!r}")

    print(f"❌ JSON парсинг повністю провалився. Початок тексту: {json_str[:200]!r}")
    return None


async def ask_gemini(prompt, use_worker=False):
    """Універсальна асинхронна функція запиту з повторними спробами та очищенням JSON."""
    retries = 3
    delay = 2
    active_model = model_worker if use_worker else model

    strict_prompt = prompt + JSON_ONLY_INSTRUCTION

    for attempt in range(retries):
        try:
            def _sync_gen():
                return active_model.generate_content(strict_prompt)

            response = await asyncio.to_thread(_sync_gen)
            result = clean_and_parse_json(response.text)

            if result:
                return result

            print(f"⚠️ Спроба {attempt + 1}: Отримано не JSON. Текст: {response.text[:50]}...")
            await asyncio.sleep(delay)

        except Exception as e:
            print(f"❌ Помилка API (спроба {attempt + 1}): {e}")
            await asyncio.sleep(delay)
            delay += 2

    print("❌ Не вдалося отримати JSON від AI.")
    return None


async def hedged_generate_content_async(
    model_wrapper: "AIWrapper",
    prompt: str,
    config=None,
    hedge_count: int = 2,
    max_retries: int = 2,
    meta_label: Optional[str] = None,
) -> object:
    """Run hedge_count parallel generate_content calls, return first successful.

    Cancels pending tasks once one succeeds.  Useful for high-criticality
    requests where latency variance is unacceptable (e.g. Narrator blocking).

    Cost: hedge_count x token usage per call.  Intended ONLY for Narrator
    (blocking path).  Do NOT use for Worker/GM_Logic — cost-prohibitive.

    Args:
        model_wrapper: AIWrapper instance to call.
        prompt: The prompt string.
        config: Optional GenerateContentConfig override (passed to generate_content).
        hedge_count: Number of parallel attempts.  Default 2.
        max_retries: Max retries inside each individual generate_content call.
            Lower than default because hedging itself provides redundancy.

    Returns:
        The response object from the first successful generate_content call.

    Raises:
        The exception from the first completed (failed) task when all hedges fail.
    """
    group_id = uuid.uuid4().hex
    gstate = {"winner": None}

    def _safe_call(idx=0):
        """Wrap sync call to convert StopIteration → RuntimeError.

        Python asyncio bug: asyncio.to_thread cannot propagate StopIteration
        into Future ("StopIteration interacts badly with generators and cannot
        be raised into a Future") — Future hangs forever. This happens when
        MagicMock.side_effect is exhausted in tests, or when any iterator-based
        sync code raises StopIteration. Conversion makes failure explicit.
        """
        try:
            # hedge_idx/label — через ContextVar (контекст thread-а ізольований копією), тож
            # сигнатура generate_content(prompt, max_retries, config) для моків не змінюється.
            _call_ctx_var.set({"hedge_idx": idx, "label": meta_label, "hedge_group": group_id, "gstate": gstate})
        except Exception:
            pass
        try:
            return model_wrapper.generate_content(prompt, max_retries, config)
        except StopIteration as exc:
            raise RuntimeError(f"StopIteration in generate_content (likely exhausted mock): {exc}") from exc

    async def _one_attempt(idx=0):
        return await asyncio.to_thread(_safe_call, idx)

    tasks = [asyncio.create_task(_one_attempt(i)) for i in range(hedge_count)]

    try:
        # Defensive timeout: hedge should never hang longer than 60s (per-attempt retries already capped).
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED, timeout=60.0
        )
        if not done:
            # Hedging timed out — cancel all and raise
            for t in tasks:
                t.cancel()
            raise asyncio.TimeoutError("hedged_generate_content_async: timeout after 60s")

        # Cancel pending hedges immediately
        for p in pending:
            p.cancel()

        # Return first successful result from the done set
        first_exception = None
        for completed in done:
            try:
                res = completed.result()
                _meta_mark_winner(group_id, tasks.index(completed), gstate)
                return res
            except (Exception, asyncio.CancelledError) as exc:
                if first_exception is None and not isinstance(exc, asyncio.CancelledError):
                    first_exception = exc

        # All tasks in the done set raised.  Wait briefly for any pending
        # that may have completed between cancellation and now.
        if pending:
            done2, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for completed in done2:
                try:
                    res = completed.result()
                    _meta_mark_winner(group_id, tasks.index(completed), gstate)
                    return res
                except (Exception, asyncio.CancelledError):
                    pass

        # Every hedge failed — raise the real exception (not CancelledError)
        raise first_exception or RuntimeError("hedged_generate_content_async: all hedges failed")
    finally:
        # Guarantee no dangling tasks regardless of control flow
        for t in tasks:
            if not t.done():
                t.cancel()