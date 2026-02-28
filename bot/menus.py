# bot/menus.py
from telebot import types

def get_main_menu():
    """Головні кнопки меню (ReplyKeyboardMarkup)"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_profile = types.KeyboardButton("📜 Профіль")
    btn_inv = types.KeyboardButton("🎒 Інвентар")
    btn_roll = types.KeyboardButton("🎲 Кинути кубик")
    btn_help = types.KeyboardButton("🆘 Допомога")
    btn_restart = types.KeyboardButton("🔄 Рестарт")

    markup.add(btn_profile, btn_inv, btn_roll, btn_help, btn_restart)
    return markup