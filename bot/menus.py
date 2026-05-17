# bot/menus.py
from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def get_dynamic_menu(actions: list = None) -> ReplyKeyboardMarkup:
    """
    Генерує Reply-клавіатуру.
    Якщо передані actions (дії), розміщує їх зверху.
    Системні кнопки завжди залишаються внизу.
    """
    builder = ReplyKeyboardBuilder()

    actions_count = 0
    if actions and isinstance(actions, list):
        for act in actions[:4]:
            clean_act = str(act).strip()
            if clean_act:
                # Додаємо префікс для візуального відділення ігрових дій від системних
                builder.button(text=f"👉 {clean_act[:40]}")
                actions_count += 1

    builder.button(text="📜 Профіль")
    builder.button(text="🗺 Карта")
    builder.button(text="📖 Інструкція")
    builder.button(text="🔄 Рестарт")

    # Форматуємо сітку
    row_sizes = []
    if actions_count > 0:
        row_sizes.extend([2] * (actions_count // 2))
        if actions_count % 2 != 0:
            row_sizes.append(1)

    row_sizes.extend([2, 2])

    builder.adjust(*row_sizes)
    return builder.as_markup(resize_keyboard=True)


def get_main_menu() -> ReplyKeyboardMarkup:
    """Дефолтне порожнє меню для старту"""
    return get_dynamic_menu()