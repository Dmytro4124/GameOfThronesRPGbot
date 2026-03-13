# core/external_api.py
import requests

BASE_URL = "https://anapioficeandfire.com/api"


def fetch_character_from_api(name: str) -> dict:
    """
    Шукає персонажа у відкритій базі An API of Ice And Fire.
    Якщо знаходить - перетворює його на готового NPC для нашої гри.
    """
    try:
        url = f"{BASE_URL}/characters?name={name}"
        response = requests.get(url, timeout=5)

        if response.status_code == 200 and response.json():
            data = response.json()[0]  # Беремо перший збіг

            # Якщо ім'я порожнє (таке буває в API для безіменних), ігноруємо
            if not data.get("name"):
                return None

            # Формуємо красивий опис з масивів API
            titles = ", ".join(filter(None, data.get("titles", [])))
            aliases = ", ".join(filter(None, data.get("aliases", [])))
            culture = data.get("culture", "Невідома")

            desc_parts = []
            if titles: desc_parts.append(f"Титули: {titles}")
            if aliases: desc_parts.append(f"Прізвиська: {aliases}")
            if culture: desc_parts.append(f"Культура: {culture}")

            description = " | ".join(desc_parts) if desc_parts else "Зовнішність невідома."

            # Повертаємо словник, який ідеально лягає у структуру нашої Google Таблиці
            return {
                "Status": "Active",
                "Name": data.get("name"),
                "Location": "GLOBAL",
                "Description": description,
                "Character": f"Канонічний персонаж. Культура: {culture}. Діє згідно з книжковим лором.",
                "Goal": "Діяти у власних інтересах.",
                "Relation_Player": "Нейтральна",
                "Memory_Anchor": "",
                "Relation_NPCs": "",
                "Secrets": "Канонічні секрети з ПЛІО",
                "Is_Canon": "TRUE",
                "Inventory": ""
            }
        return None
    except Exception as e:
        print(f"⚠️ [API Ice & Fire ERROR]: {e}")
        return None