# test/test_prompts.py
"""
Unit-тести для core/prompts.py.

Покриває:
- Issue 7 (мутація схеми): build_normal_resolve_prompt містить ключ 'reputation_reasoning'
- Issue Censor relax: build_validate_action_prompt містить grimdark/violence-keywords у прикладах
"""


# ─── Тест 1: build_normal_resolve_prompt містить 'reputation_reasoning' ───────

def test_normal_resolve_prompt_has_reputation_reasoning_key():
    """
    Issue 7: build_normal_resolve_prompt повинен містити ключ 'reputation_reasoning'
    в output_schema або в example JSON.

    Інваріант §5.3: будь-яка зміна схеми вимагає синхронного оновлення промпту.
    Якщо ключ відсутній — Worker повертатиме відповідь без поля,
    що зламає парсер у core/engine.py.
    """
    from core.prompts import build_normal_resolve_prompt

    # Мінімальний профіль для побудови промпту
    profile = {
        "Ім'я": "Тест Герой",
        "ability_scores": {"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "proficiency_bonus": 2,
        "skill_profs": [],
        "conditions": [],
        "hp_current": 30,
        "hp_max": 30,
        "Особисте Золото": 100,
        "Інвентар": "Меч",
    }

    prompt = build_normal_resolve_prompt(
        user_input="Піднятися на вежу",
        profile=profile,
        current_scene="Двір замку",
        npcs_in_scene=[],
        last_turn_summary="Герой щойно прибув до замку.",
        current_location="Вінтерфелл",
        npc_reputation_context={},
        clocks_info={},
        nearby_canonical_locs=[],
        all_canonical_locs_grouped="",
    )

    assert isinstance(prompt, str), "build_normal_resolve_prompt must return a string"
    assert "reputation_reasoning" in prompt, (
        "build_normal_resolve_prompt MUST contain 'reputation_reasoning' key "
        "in output schema. Missing key breaks engine parser (Issue 7)."
    )


# ─── Тест 2: build_normal_resolve_prompt містить 'reputation_delta' ──────────

def test_normal_resolve_prompt_has_reputation_delta_key():
    """
    build_normal_resolve_prompt повинен містити 'reputation_delta'
    (парний ключ до reputation_reasoning).
    """
    from core.prompts import build_normal_resolve_prompt

    profile = {
        "Ім'я": "Тест",
        "ability_scores": {},
        "proficiency_bonus": 2,
        "skill_profs": [],
        "conditions": [],
        "hp_current": 20,
        "hp_max": 20,
        "Особисте Золото": 0,
        "Інвентар": "",
    }

    prompt = build_normal_resolve_prompt(
        user_input="Щось зробити",
        profile=profile,
        current_scene="Зала",
        npcs_in_scene=[],
        last_turn_summary="",
        current_location="Вінтерфелл",
        npc_reputation_context=None,
        clocks_info=None,
    )

    assert "reputation_delta" in prompt, (
        "build_normal_resolve_prompt must contain 'reputation_delta' key in output schema."
    )


# ─── Тест 3: Censor промпт містить grimdark/violence-keywords ─────────────────

def test_validate_action_prompt_allows_violent_examples():
    """
    Issue Censor relax: build_validate_action_prompt повинен містити ключові
    слова 'grimdark' та 'violence' (або 'violent') у тексті промпту.

    Ці слова є частиною Genre Declaration та прикладів, що навчають LLM
    не блокувати тематично важкий контент.
    Якщо їх немає — Censor може знову блокувати легітимні dark fantasy дії.
    """
    from core.prompts import build_validate_action_prompt

    prompt = build_validate_action_prompt(
        char_name="Тиріон",
        user_input="Я загрожую охоронцю",
        inventory_list=["Кинджал"],
    )

    assert isinstance(prompt, str), "build_validate_action_prompt must return a string"

    prompt_lower = prompt.lower()
    assert "grimdark" in prompt_lower, (
        "build_validate_action_prompt must contain 'grimdark' to declare genre context. "
        "Without it, Censor LLM reverts to blocking dark fantasy actions."
    )
    assert "violence" in prompt_lower, (
        "build_validate_action_prompt must contain 'violence' in allowed content examples. "
        "Without it, Censor LLM may incorrectly block violent actions."
    )


# ─── Тест 4: Censor промпт містить is_valid та refusal_reason у схемі ─────────

def test_validate_action_prompt_has_required_schema_keys():
    """
    Censor JSON-контракт (§5.3): промпт повинен містити 'is_valid' і 'refusal_reason'.
    Якщо ключ відсутній — парсер engine.py не отримає очікуваного поля.
    """
    from core.prompts import build_validate_action_prompt

    prompt = build_validate_action_prompt(
        char_name="Арія",
        user_input="Я атакую вартового",
        inventory_list=["Голка (меч)"],
    )

    assert "is_valid" in prompt, (
        "build_validate_action_prompt must contain 'is_valid' in output schema."
    )
    assert "refusal_reason" in prompt, (
        "build_validate_action_prompt must contain 'refusal_reason' in output schema."
    )


# ─── Тест 5: Censor промпт містить приклади, що показують is_valid: true ──────

def test_validate_action_prompt_has_valid_true_examples():
    """
    Censor промпт повинен містити приклади з is_valid=true (або 'true' у тексті),
    щоб LLM зрозумів, що grimdark-контент дозволений.
    """
    from core.prompts import build_validate_action_prompt

    prompt = build_validate_action_prompt(
        char_name="Джейме",
        user_input="Я прикладаю меч до горла ворога",
        inventory_list=["Меч"],
    )

    assert "is_valid" in prompt, "Prompt must contain is_valid"
    # Промпт повинен містити приклади зі значенням true (дозволені дії)
    assert "true" in prompt.lower(), (
        "build_validate_action_prompt must contain 'true' examples to teach LLM "
        "to allow dark fantasy content."
    )
