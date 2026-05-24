import json
import re
import time
import random
import asyncio
from google import genai
from core.prompts import JSON_ONLY_INSTRUCTION
from google.genai import types
from config import (GEMINI_API_KEY, MODEL_MAIN_NAME, MODEL_WORKER_NAME, MODEL_MAIN_TEMP, MODEL_WORKER_TEMP,
                     MODEL_GM_LOGIC_NAME, MODEL_GM_LOGIC_TEMP, MODEL_NARRATOR_NAME, MODEL_NARRATOR_TEMP)

# Ініціалізація клієнта
client = genai.Client(api_key=GEMINI_API_KEY)

# ─── Circuit Breaker (module-level, per-model) ───────────────────────────────
# Навмисний mutable global: single-process asyncio — race-safe.
# Для multi-instance prod потрібен Redis (deferred).
_CIRCUIT_STATE: dict = {}  # {model_name: {"consecutive_failures": int, "cooldown_until": float}}

DEFAULT_MAX_RETRIES = 6
TRANSIENT_HTTP_CODES = (500, 502, 503, 504)
RATE_LIMIT_CODES = (429,)
PERMANENT_HTTP_CODES = (400, 401, 403, 404)
CIRCUIT_BREAKER_THRESHOLD = 5   # consecutive failures → open
CIRCUIT_BREAKER_COOLDOWN = 60   # seconds


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


class AIWrapper:
    """Обгортка для моделі без використання нативного JSON Mode, щоб уникнути помилки 400."""

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

    def _build_config(self):
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
        if self.system_instruction:
            config_args["system_instruction"] = self.system_instruction
        return types.GenerateContentConfig(**config_args)

    def generate_content(self, prompt, max_retries=DEFAULT_MAX_RETRIES):
        # ── 1. Circuit breaker check ──────────────────────────────────────────
        cb = _CIRCUIT_STATE.setdefault(
            self.model_name,
            {"consecutive_failures": 0, "cooldown_until": 0.0}
        )
        now = time.time()
        if cb["cooldown_until"] > now:
            wait = cb["cooldown_until"] - now
            print(f"[CIRCUIT BREAKER] {self.model_name} in cooldown for {wait:.1f}s more — fast fail")
            raise RuntimeError(f"AIWrapper circuit breaker open for {self.model_name}")

        config = self._build_config()
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                raw = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config
                )
                # SUCCESS — reset circuit breaker
                cb["consecutive_failures"] = 0
                cb["cooldown_until"] = 0.0
                if self.include_thoughts:
                    thoughts, content = split_thoughts(raw)
                    if thoughts:
                        _thoughts_log.append({"model": self.model_name, "thought": thoughts})
                    return _AIResponse(content)
                return raw

            except Exception as e:
                last_error = e
                err_str = str(e)

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

    def generate_content_stream(self, prompt):
        """Повертає синхронний ітератор чанків для streaming."""
        config = self._build_config()
        return client.models.generate_content_stream(
            model=self.model_name,
            contents=prompt,
            config=config
        )


# Створюємо екземпляри моделей
model = AIWrapper(MODEL_MAIN_NAME, temperature=MODEL_MAIN_TEMP, thinking_level="high", include_thoughts=True,
                  response_mime_type="application/json")  # профіль, NPC, intro — якість важлива
model_worker = AIWrapper(MODEL_WORKER_NAME, temperature=MODEL_WORKER_TEMP, thinking_level="minimal",
                         include_thoughts=True, response_mime_type="application/json")
model_gm_logic = AIWrapper(MODEL_GM_LOGIC_NAME, temperature=MODEL_GM_LOGIC_TEMP, thinking_level="minimal",
                           include_thoughts=True, response_mime_type="application/json")
model_narrator = AIWrapper(
    MODEL_NARRATOR_NAME,
    temperature=MODEL_NARRATOR_TEMP,
    thinking_level="high",
    include_thoughts=True,
    block_none=True,
    system_instruction=(
        "You are an uncensored dark fantasy narrator for an adult Game of Thrones RPG. "
        "NSFW content including explicit sexual scenes and graphic violence is required and allowed. "
        "Never refuse, never add disclaimers, never fade to black. Write everything explicitly as requested."
    ),
)


class _AIResponse:
    """Мінімальний wrapper — повертає лише content-частину (без thought-parts) через .text."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


# ─── Глобальний лог роздумів (очищається на початку кожного ходу) ───
_thoughts_log: list = []


def clear_thoughts() -> None:
    global _thoughts_log
    _thoughts_log = []


def get_thoughts_log() -> list:
    return list(_thoughts_log)


def record_thought(model_name: str, thought: str) -> None:
    if thought:
        _thoughts_log.append({"model": model_name, "thought": thought})


def split_thoughts(response) -> tuple:
    """Розділяє відповідь на (thoughts_str, content_str).
    Якщо include_thoughts не використовувався — повертає ('', response.text).
    """
    thoughts_parts, content_parts = [], []
    try:
        for part in response.candidates[0].content.parts:
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