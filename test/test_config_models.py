"""Stage A: model-name defaults and ENV override in config.py.

config.py is loaded as a PRIVATE COPY (importlib.util.spec_from_file_location) instead of
importlib.reload(config): reload would replace the shared EROTIC_USERS / GODMODE_USERS /
PUPPET_USERS / ADMIN set objects that other modules hold via `from config import ...`.
`dotenv.load_dotenv` is patched to a no-op so a local .env cannot influence the defaults.
The file is located relative to this test file, so a fake `config` placed in sys.modules by
another test (e.g. test_combat_package3.py) does not matter.
"""
import importlib.util
import pathlib
from unittest.mock import patch

import pytest

_CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent / "config.py"
_PRIVATE_NAME = "_config_under_test"

MODEL_ENVS = ("MODEL_MAIN_NAME", "MODEL_WORKER_NAME", "MODEL_GM_LOGIC_NAME")


def _load_private_config():
    spec = importlib.util.spec_from_file_location(_PRIVATE_NAME, _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch("dotenv.load_dotenv", lambda *a, **k: False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_config():
    """Returns a callable that loads a fresh private copy of config.py under the current env."""
    return _load_private_config


def test_load_does_not_touch_shared_config(load_config):
    import sys
    shared = sys.modules.get("config")
    before = {n: id(getattr(shared, n)) for n in ("EROTIC_USERS", "GODMODE_USERS", "PUPPET_USERS")
              if shared is not None and hasattr(shared, n)}
    cfg = load_config()
    assert sys.modules.get("config") is shared
    assert _PRIVATE_NAME not in sys.modules
    for n, i in before.items():
        assert id(getattr(shared, n)) == i
    if before:
        assert cfg.EROTIC_USERS is not getattr(shared, "EROTIC_USERS")


def test_flash_lite_id_value(load_config):
    assert load_config().FLASH_LITE_MODEL_ID == "gemini-3.5-flash-lite"


def test_defaults_are_flash_lite(monkeypatch, load_config):
    for name in MODEL_ENVS:
        monkeypatch.delenv(name, raising=False)
    cfg = load_config()
    assert cfg.MODEL_MAIN_NAME == cfg.FLASH_LITE_MODEL_ID
    assert cfg.MODEL_WORKER_NAME == cfg.FLASH_LITE_MODEL_ID
    assert cfg.MODEL_GM_LOGIC_NAME == cfg.FLASH_LITE_MODEL_ID


def test_narrator_stays_gemma(monkeypatch, load_config):
    for name in MODEL_ENVS:
        monkeypatch.delenv(name, raising=False)
    assert load_config().MODEL_NARRATOR_NAME == "gemma-4-31b-it"


@pytest.mark.parametrize("env_name,attr", [
    ("MODEL_MAIN_NAME", "MODEL_MAIN_NAME"),
    ("MODEL_WORKER_NAME", "MODEL_WORKER_NAME"),
    ("MODEL_GM_LOGIC_NAME", "MODEL_GM_LOGIC_NAME"),
])
def test_env_override(monkeypatch, load_config, env_name, attr):
    monkeypatch.setenv(env_name, "gemma-4-31b-it")
    assert getattr(load_config(), attr) == "gemma-4-31b-it"


def test_env_override_does_not_affect_narrator(monkeypatch, load_config):
    for name in MODEL_ENVS:
        monkeypatch.setenv(name, "some-other-model")
    assert load_config().MODEL_NARRATOR_NAME == "gemma-4-31b-it"


def test_narrator_ab_disabled_by_default(monkeypatch, load_config):
    monkeypatch.delenv("NARRATOR_AB_ENABLED", raising=False)
    assert load_config().NARRATOR_AB_ENABLED is False


@pytest.mark.parametrize("val,expected", [("1", True), ("0", False), ("true", False)])
def test_narrator_ab_flag_parsing(monkeypatch, load_config, val, expected):
    monkeypatch.setenv("NARRATOR_AB_ENABLED", val)
    assert load_config().NARRATOR_AB_ENABLED is expected


def test_narrator_alt_defaults_to_flash_lite(monkeypatch, load_config):
    monkeypatch.delenv("MODEL_NARRATOR_ALT_NAME", raising=False)
    cfg = load_config()
    assert cfg.MODEL_NARRATOR_ALT_NAME == cfg.FLASH_LITE_MODEL_ID
