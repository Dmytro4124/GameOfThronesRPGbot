# core/ai_client.py
import json
import time
import google.generativeai as genai
from config import GEMINI_API_KEY, MODEL_MAIN_NAME, MODEL_WORKER_NAME

# Ініціалізація Gemini
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel(model_name=MODEL_MAIN_NAME)
model_worker = genai.GenerativeModel(model_name=MODEL_WORKER_NAME)


def clean_and_parse_json(text):
    """Витягує JSON з тексту. Підтримує і словники {}, і списки []."""
    try:
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
            return json.loads(json_str)
        return None
    except json.JSONDecodeError:
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