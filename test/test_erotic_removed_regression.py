"""Регресія: еротичний режим повністю видалено з коду (core/, bot/, config.py)."""
import inspect
import pathlib
import re

import pytest

import core.prompts as P

_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Токени збираються з частин, щоб цей файл сам не містив заборонених літералів у зайвих місцях.
_BAD = re.compile("ERO" + "TIC|ero" + "tic_mode|ERO" + "TIC_USERS", re.IGNORECASE)


def _files():
    out = [_ROOT / "config.py"]
    for d in ("core", "bot"):
        out += sorted((_ROOT / d).rglob("*.py"))
    return out


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_erotic_tokens_in_source(path):
    text = path.read_text(encoding="utf-8")
    assert not _BAD.search(text)


@pytest.mark.parametrize("name", ["build_narrator_parts", "build_gm_logic_parts"])
def test_builders_have_no_erotic_param(name):
    fn = getattr(P, name)
    assert "ero" + "tic_mode" not in inspect.signature(fn).parameters
    with pytest.raises(TypeError):
        fn(**{"ero" + "tic_mode": True})
