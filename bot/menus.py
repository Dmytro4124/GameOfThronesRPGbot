# bot/menus.py
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


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


def build_ability_preview_keyboard() -> InlineKeyboardMarkup:
    """Two buttons: 'Прийняти' and 'Перерозподілити' for the auto-roll preview step."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Прийняти", callback_data="ability_accept")
    builder.button(text="Перерозподілити", callback_data="ability_redistribute")
    builder.adjust(2)
    return builder.as_markup()


def build_point_buy_keyboard(
    scores: dict,
    remaining: int,
) -> InlineKeyboardMarkup:
    """6 rows of [−] LABEL [+] per ability + a budget row + a footer action row.

    Disabled buttons (score at floor/ceiling or no budget) are rendered as a
    blank ' ' button with callback_data='noop' so the layout stays aligned.

    Row layout:
      Rows 1-6  : [−] <ABILITY: score> [+]
      Row 7     : budget label
      Row 8     : [Confirm / waiting label] [Reset] [Cancel]
    """
    from core.character_creation import (
        point_buy_can_increment,
        point_buy_can_decrement,
        ABILITY_KEYS,
    )

    builder = InlineKeyboardBuilder()

    # Ukrainian short labels for abilities
    _LABELS = {
        "STR": "Сила",
        "DEX": "Спритн.",
        "CON": "Витрив.",
        "INT": "Інтелект",
        "WIS": "Мудр.",
        "CHA": "Харизма",
    }

    for ab in ABILITY_KEYS:
        score = scores[ab]

        # Decrement button
        try:
            can_dec = point_buy_can_decrement(scores, ab)
        except Exception:
            can_dec = False
        if can_dec:
            builder.button(text="−", callback_data=f"pb_dec_{ab}")
        else:
            builder.button(text=" ", callback_data="noop")

        # Centre label (non-interactive)
        label = _LABELS.get(ab, ab)
        builder.button(text=f"{label}: {score}", callback_data="noop")

        # Increment button
        try:
            can_inc = point_buy_can_increment(scores, ab)
        except Exception:
            can_inc = False
        if can_inc:
            builder.button(text="+", callback_data=f"pb_inc_{ab}")
        else:
            builder.button(text=" ", callback_data="noop")

    # Row 7: budget label
    builder.button(
        text=f"Залишок: {remaining} / 27 очок",
        callback_data="noop",
    )

    # Row 8: confirm / reset / cancel
    if remaining == 0:
        builder.button(text="Підтвердити", callback_data="pb_confirm")
    else:
        builder.button(
            text=f"Залишилось {remaining} очок",
            callback_data="noop",
        )
    builder.button(text="Скинути", callback_data="pb_reset")
    builder.button(text="Скасувати", callback_data="pb_cancel")

    # Adjust: 6 ability rows of 3, then 1 budget row, then 3 footer buttons
    builder.adjust(3, 3, 3, 3, 3, 3, 1, 3)
    return builder.as_markup()
