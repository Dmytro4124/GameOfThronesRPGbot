import pytest
from core.ai_client import clean_and_parse_json

def test_pure_json_dict():
    text = '{"story": "Ви йдете лісом.", "updates": {}}'
    result = clean_and_parse_json(text)
    assert result == {"story": "Ви йдете лісом.", "updates": {}}

def test_pure_json_list():
    text = '["Еддард Старк", "Джон Сноу"]'
    result = clean_and_parse_json(text)
    assert result == ["Еддард Старк", "Джон Сноу"]

def test_json_with_markdown():
    text = """```json\n{"status": "ok"}\n```"""
    result = clean_and_parse_json(text)
    assert result == {"status": "ok"}

def test_json_with_conversational_text():
    text = """Ось ваш результат, як ви і просили:
    {
        "is_valid": true,
        "refusal_reason": ""
    }
    Сподіваюсь, це допоможе!"""
    result = clean_and_parse_json(text)
    assert result == {"is_valid": True, "refusal_reason": ""}

def test_invalid_json_returns_none():
    text = "Тут взагалі немає дужок, просто звичайний текст."
    result = clean_and_parse_json(text)
    assert result is None

def test_broken_json_syntax_returns_none():
    # Пропущена лапки або кома
    text = '{"story": "Текст", "updates": { }'
    result = clean_and_parse_json(text)
    assert result is None