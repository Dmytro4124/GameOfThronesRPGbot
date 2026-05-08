# Telegram Game of Thrones RPG Bot

Текстова RPG у всесвіті Game of Thrones з LLM-гейммастером і Telegram як клієнтом.

## Стек
- Python 3 (TODO: уточнити мінімальну версію — імовірно 3.10+)
- aiogram 3 — асинхронний Telegram bot framework
- google-genai → Gemma 3 (`gemma-3-4b-it` для логіки/парсингу, `gemma-3-27b-it` для художнього тексту) + `gemini-embedding-2-preview` для RAG
- Google Sheets як БД (через gspread)
- numpy — локальний векторний пошук (RAG)

## Запуск
Режим обирається автоматично за наявністю env-змінної `WEBHOOK_URL`:

- Polling (локально, без `WEBHOOK_URL`):
  ```
  python main.py
  ```
- Webhook (продакшн, із заданим `WEBHOOK_URL`):
  ```
  python main.py
  ```

Env-змінні: `TELEGRAM_TOKEN` (обовʼязково), `WEBHOOK_URL` (опц.), `PORT` (опц., default 8080). TODO: уточнити повний перелік у `config.py`.

## Тести
```
pytest test/ -v
```

## Структура
- `bot/` — Telegram-маршрутизація (`handlers.py`, `menus.py`, `utils.py`), захист від спаму, UX/тайм-аути.
- `core/` — ігровий рушій: `engine.py` (головний цикл, GM-оркестрація), `mechanics.py` (2d50 roll-over, обчислення наслідків), `ai_client.py` (ініціалізація Gemini, парсинг JSON), `prompts.py` (усі системні промпти), `world.py` / `world_constants.py`, `cheats.py`.
- `database/` — Google Sheets (`sheets.py`, `operations.py`), канон NPC (`canon_npc.py`), локальна векторна база (RAG на numpy).
- `test/` — pytest-тести (`test_engine.py`, `test_mechanics.py`, `test_ai_parsing.py`).
