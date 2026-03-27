import json
import re
import asyncio
from google import genai
from core.prompts import JSON_ONLY_INSTRUCTION
from google.genai import types
from config import (GEMINI_API_KEY, MODEL_MAIN_NAME, MODEL_WORKER_NAME, MODEL_MAIN_TEMP, MODEL_WORKER_TEMP,
                     MODEL_GM_LOGIC_NAME, MODEL_GM_LOGIC_TEMP, MODEL_NARRATOR_NAME, MODEL_NARRATOR_TEMP)

# Ініціалізація клієнта
client = genai.Client(api_key=GEMINI_API_KEY)


class AIWrapper:
    """Обгортка для моделі без використання нативного JSON Mode, щоб уникнути помилки 400."""

    def __init__(self, model_name, temperature=0.7):
        self.model_name = model_name
        self.temperature = temperature

    def generate_content(self, prompt, max_retries=3):
        import time
        delay = 2
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                config_args = {
                    "temperature": self.temperature
                }
                # Жодних response_mime_type. Працюємо як з класичним текстом.
                return client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_args)
                )
            except Exception as e:
                last_error = e
                if attempt == max_retries:
                    raise
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    delay = min(delay * 3, 60)
                else:
                    delay += 2
                print(f"⚠️ AIWrapper retry {attempt}/{max_retries}: {err_str[:80]}... (sleep {delay}s)")
                time.sleep(delay)
        raise last_error


# Створюємо екземпляри моделей
model = AIWrapper(MODEL_MAIN_NAME, temperature=MODEL_MAIN_TEMP)
model_worker = AIWrapper(MODEL_WORKER_NAME, temperature=MODEL_WORKER_TEMP)
model_gm_logic = AIWrapper(MODEL_GM_LOGIC_NAME, temperature=MODEL_GM_LOGIC_TEMP)
model_narrator = AIWrapper(MODEL_NARRATOR_NAME, temperature=MODEL_NARRATOR_TEMP)


def _fix_invalid_escapes(s: str) -> str:
    """
    Виправляє невалідні escape-послідовності у JSON-рядку.
    Стратегія: замінює одиничний \, за яким іде не-валідний символ, на \\,
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