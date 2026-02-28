import gspread
import json
import os
from oauth2client.service_account import ServiceAccountCredentials


class GoogleSheetsDB:
    _instance = None

    def __new__(cls):
        # Патерн Singleton: гарантує, що існує лише один екземпляр класу
        if cls._instance is None:
            cls._instance = super(GoogleSheetsDB, cls).__new__(cls)
            cls._instance._init_client()
        return cls._instance

    def _init_client(self):
        """Ініціалізація підключення один раз"""
        self.spreadsheet_id = os.getenv("SPREADSHEET_ID")
        if not self.spreadsheet_id:
            raise ValueError("❌ SPREADSHEET_ID не знайдено у змінних середовища.")

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials_file = 'credentials.json'

        # 1. Пробуємо знайти файл
        if os.path.exists(credentials_file):
            creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)
        else:
            # 2. Якщо файлу немає, шукаємо змінну середовища
            json_creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if not json_creds:
                raise Exception("❌ Не знайдено файл credentials.json і змінну GOOGLE_CREDENTIALS_JSON")

            creds_dict = json.loads(json_creds)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

        # Авторизація
        self.client = gspread.authorize(creds)

        # Одразу відкриваємо таблицю, щоб не робити це при кожному запиті
        self.spreadsheet = self.client.open_by_key(self.spreadsheet_id)
        print("✅ [DB] Підключення до Google Sheets успішно встановлено.")

    def get_sheet(self, worksheet_name):
        """Повертає об'єкт аркуша за його назвою"""
        try:
            return self.spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"❌ [DB ERROR] Аркуш '{worksheet_name}' не знайдено.")
            return None


# Створюємо єдиний екземпляр бази даних
# Тепер в інших файлах ви просто будете робити: from database.sheets import db
db = GoogleSheetsDB()