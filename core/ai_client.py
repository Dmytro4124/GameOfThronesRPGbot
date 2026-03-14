import json
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, MODEL_MAIN_NAME, MODEL_WORKER_NAME, MODEL_MAIN_TEMP, MODEL_WORKER_TEMP

# Ініціалізація клієнта
client = genai.Client(api_key=GEMINI_API_KEY)


class AIWrapper:
    """Обгортка для моделі без використання нативного JSON Mode, щоб уникнути помилки 400."""

    def __init__(self, model_name, temperature=0.7):
        self.model_name = model_name
        self.temperature = temperature

    def generate_content(self, prompt):
        config_args = {
            "temperature": self.temperature
        }

        # Жодних response_mime_type. Працюємо як з класичним текстом.
        return client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**config_args)
        )


# Створюємо екземпляри моделей
model = AIWrapper(MODEL_MAIN_NAME, temperature=MODEL_MAIN_TEMP)
model_worker = AIWrapper(MODEL_WORKER_NAME, temperature=MODEL_WORKER_TEMP)


def clean_and_parse_json(text):
    """Витягує JSON з тексту. Підтримує і словники {}, і списки []."""
    try:
        # Чистимо від markdown
        text = text.replace("```json", "").replace("```", "").strip()

        start_obj = text.find('{')
        start_arr = text.find('[')

        possible_starts = [i for i in [start_obj, start_arr] if i != -1]
        if not possible_starts:
            return None

        start_index = min(possible_starts)
        end_index = text.rfind('}') if start_index == start_obj else text.rfind(']')

        if start_index != -1 and end_index != -1 and end_index > start_index:
            json_str = text[start_index: end_index + 1]

            # === РІШЕННЯ: strict=False дозволяє переноси рядків ===
            return json.loads(json_str, strict=False)

        return None

    except json.JSONDecodeError as e:
        print(f"⚠️ Помилка парсингу JSON (JSONDecodeError): {e}\nТекст: {text[:150]}...")
        return None
    except Exception as e:
        print(f"⚠️ Помилка парсингу: {e}")
        return None


def ask_gemini(prompt, use_worker=False):
    """Універсальна функція запиту з повторними спробами та очищенням JSON."""
    retries = 3
    delay = 2
    active_model = model_worker if use_worker else model

    strict_prompt = prompt + "\n\nВАЖЛИВО: Відповідай ТІЛЬКИ валідним JSON кодом. Без Markdown. Без слів 'Ось ваш JSON'."

    for attempt in range(retries):
        try:
            # Зверни увагу: ми БІЛЬШЕ НЕ передаємо require_json=True
            response = active_model.generate_content(strict_prompt)
            result = clean_and_parse_json(response.text)

            if result:
                return result

            print(f"⚠️ Спроба {attempt + 1}: Отримано не JSON. Текст: {response.text[:50]}...")
            time.sleep(delay)

        except Exception as e:
            print(f"❌ Помилка API (спроба {attempt + 1}): {e}")
            time.sleep(delay)
            delay += 2

    print("❌ Не вдалося отримати JSON від AI.")
    return None