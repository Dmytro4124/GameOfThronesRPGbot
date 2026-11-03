"""ENV-перемикачі експерименту Narrator: NARRATOR_THINKING_LEVEL.

config.py та core/ai_client.py завантажуються ПРИВАТНИМИ копіями (spec_from_file_location), без
importlib.reload: спільні модулі (GODMODE_USERS тощо) не чіпаються. Для ai_client у sys.modules["config"]
на час завантаження підставляється приватний config (monkeypatch відновлює).
"""
import importlib.util
import logging
import pathlib
import sys
from unittest.mock import patch

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

ENVS = ("NARRATOR_THINKING_LEVEL",)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ENVS:
        monkeypatch.delenv(k, raising=False)


def _load_cfg():
    spec = importlib.util.spec_from_file_location("_cfg_env_switch", _ROOT / "config.py")
    m = importlib.util.module_from_spec(spec)
    with patch("dotenv.load_dotenv", lambda *a, **k: False):
        spec.loader.exec_module(m)
    return m


def _load_ai(monkeypatch):
    cfg = _load_cfg()
    monkeypatch.setitem(sys.modules, "config", cfg)
    spec = importlib.util.spec_from_file_location("_ai_env_switch", _ROOT / "core" / "ai_client.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ============================ _env_choice / config ============================

def test_defaults():
    cfg = _load_cfg()
    assert cfg.NARRATOR_THINKING_LEVEL == "high"


@pytest.mark.parametrize("raw,expected", [
    ("high", "high"), ("medium", "medium"), ("low", "low"), ("minimal", "minimal"),
    ("  LOW  ", "low"), ("Minimal", "minimal"), ("MEDIUM\n", "medium"),
])
def test_thinking_level_valid_values_normalized(monkeypatch, raw, expected):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", raw)
    assert _load_cfg().NARRATOR_THINKING_LEVEL == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t"])
def test_empty_or_blank_is_default_without_warning(monkeypatch, caplog, raw):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", raw)
    with caplog.at_level(logging.WARNING):
        cfg = _load_cfg()
    assert cfg.NARRATOR_THINKING_LEVEL == "high"
    assert not [r for r in caplog.records if "NARRATOR_" in r.getMessage()]


@pytest.mark.parametrize("raw", ["ultra", "HIGHEST", "0", "none", "neutral"])
def test_invalid_thinking_level_falls_back_to_default_with_warning(monkeypatch, caplog, raw):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", raw)
    with caplog.at_level(logging.WARNING):
        cfg = _load_cfg()
    assert cfg.NARRATOR_THINKING_LEVEL == "high"
    assert any("NARRATOR_THINKING_LEVEL" in r.getMessage() for r in caplog.records)


def test_env_choice_helper_direct():
    cfg = _load_cfg()
    assert cfg._env_choice("NOT_SET_XYZ", ("a", "b"), "a") == "a"


def test_each_switch_independent(monkeypatch):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", "low")
    cfg = _load_cfg()
    assert cfg.NARRATOR_THINKING_LEVEL == "low"


def test_shared_config_untouched_by_private_load():
    shared = sys.modules.get("config")
    _load_cfg()
    assert sys.modules.get("config") is shared


def test_shared_ai_client_defaults_in_isolated_test_env():
    """У ізольованому тестовому ENV (conftest чистить змінні) shared-інстанси мають дефолти."""
    import core.ai_client as ai
    assert ai.model_narrator.thinking_level == "high"
    assert ai.model_narrator_alt.thinking_level == "high"


# ============================ ai_client wiring ============================

@pytest.mark.parametrize("level", ["high", "medium", "low", "minimal"])
def test_narrator_wrappers_get_thinking_level(monkeypatch, level):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", level)
    ai = _load_ai(monkeypatch)
    assert ai.model_narrator.thinking_level == level
    assert ai.model_narrator_alt.thinking_level == level


def test_other_roles_thinking_not_affected(monkeypatch):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", "minimal")
    ai = _load_ai(monkeypatch)
    assert ai.model.thinking_level == "high"
    assert ai.model_worker.thinking_level == "minimal"
    assert ai.model_gm_logic.thinking_level == "minimal"


def test_thinking_level_reaches_generation_config(monkeypatch):
    monkeypatch.setenv("NARRATOR_THINKING_LEVEL", "low")
    ai = _load_ai(monkeypatch)
    cfg = ai.model_narrator._build_config()
    tc = cfg.thinking_config
    assert "LOW" in str(tc.thinking_level).upper()


def test_system_instruction_wired_to_narrator_wrappers(monkeypatch):
    ai = _load_ai(monkeypatch)
    assert ai.model_narrator.system_instruction == ai._NARRATOR_SYSTEM_INSTRUCTION
    assert ai.model_narrator_alt.system_instruction == ai._NARRATOR_SYSTEM_INSTRUCTION


# ============================ system instruction text ============================

@pytest.mark.parametrize("needed", ["unambiguously adults (18+)", "including the hero"])
def test_system_instruction_contains_18plus_rule(needed):
    import core.ai_client as ai
    assert needed in ai._NARRATOR_SYSTEM_INSTRUCTION


@pytest.mark.parametrize("bad", ["EROTIC", "NSFW", "explicit sexual"])
def test_system_instruction_has_no_erotic_tokens(bad):
    import core.ai_client as ai
    assert bad.lower() not in ai._NARRATOR_SYSTEM_INSTRUCTION.lower()
