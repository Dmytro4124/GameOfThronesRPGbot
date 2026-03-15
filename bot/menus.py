# bot/menus.py
from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def get_main_menu() -> ReplyKeyboardMarkup:
    """Головні кнопки меню (ReplyKeyboardMarkup) для aiogram 3.x"""
    builder = ReplyKeyboardBuilder()

    # Додаємо кнопки
    builder.button(text="📜 Профіль")
    builder.button(text="🎒 Інвентар")
    builder.button(text="🎲 Кинути кубик")
    builder.button(text="🆘 Допомога")
    builder.button(text="🔄 Рестарт")

    # Форматуємо сітку клавіатури: по 2 кнопки в рядку
    builder.adjust(2)

    # Генеруємо розмітку з параметром resize_keyboard=True (щоб клавіатура не була на пів екрана)
    return builder.as_markup(resize_keyboard=True)