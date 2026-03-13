# core/ai_client.py
import json
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, MODEL_MAIN_NAME, MODEL_WORKER_NAME, MODEL_MAIN_TEMP, MODEL_WORKER_TEMP

# Ініціалізація нового клієнта
client = genai.Client(api_key=GEMINI_API_KEY)


class AIWrapper:
    """
    Обгортка для сумісності зі старим кодом.
    Використовує нативний JSON Mode від Google Gemini для 100% стабільності парсингу.
    """

    def __init__(self, model_name, temperature=0.7):
        self.model_name = model_name
        self.temperature = temperature

    def generate_content(self, prompt, require_json=True):
        # Налаштовуємо конфігурацію
        config_args = {
            "temperature": self.temperature
        }

        # ВАЖЛИВО: Змушуємо API повертати виключно валідний JSON!
        if require_json:
            config_args["response_mime_type"] = "application/json"

        # Виклик через новий SDK
        return client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**config_args)
        )


# Створюємо екземпляри моделей з налаштованою температурою
model = AIWrapper(MODEL_MAIN_NAME, temperature=MODEL_MAIN_TEMP)
model_worker = AIWrapper(MODEL_WORKER_NAME, temperature=MODEL_WORKER_TEMP)


def clean_and_parse_json(text):
    """
    Безпечно парсить JSON.
    Оскільки тепер ми використовуємо application/json, складні регулярні вирази більше не потрібні.
    """
    try:
        # На всякий випадок чистимо від можливих markdown-тегів (хоча API має їх блокувати)
        clean_text = text.replace("```json", "").replace("```", "").strip()

        # strict=False дозволяє переноси рядків (\n) всередині тексту
        return json.loads(clean_text, strict=False)

    except json.JSONDecodeError as e:
        print(f"⚠️ Помилка парсингу JSON (JSONDecodeError): {e}\nТекст: {text[:150]}...")
        return None
    except Exception as e:
        print(f"⚠️ Помилка парсингу: {e}")
        return None


def ask_gemini(prompt, use_worker=False):
    """Універсальна функція запиту з повторними спробами."""
    retries = 3
    delay = 2
    active_model = model_worker if use_worker else model

    for attempt in range(retries):
        try:
            # Модель вже знає, що треба повертати JSON завдяки обгортці AIWrapper
            response = active_model.generate_content(prompt, require_json=True)
            result = clean_and_parse_json(response.text)

            if result:
                return result

            print(f"⚠️ Спроба {attempt + 1}: Помилка парсингу нативного JSON.")
            time.sleep(delay)

        except Exception as e:
            print(f"❌ Помилка API (спроба {attempt + 1}): {e}")
            time.sleep(delay)
            delay += 2

    print("❌ Не вдалося отримати валідний JSON від AI.")
    return None