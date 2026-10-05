"""Hermetic test guard for the whole `pytest test/` run.

Why: database/sheets.py builds GoogleSheetsDB() at import time and the repo
root holds a real credentials.json + .env, so unguarded tests could write
fake users into the production Users_DB / call Gemini / Telegram.

What it does (module level, before any project import):
  * overrides env vars with dummies (load_dotenv() does not override existing
    env, so these win);
  * patches gspread.authorize and oauth2client ServiceAccountCredentials
    loaders to return MagicMocks (credentials.json is never read);
  * blocks non-loopback socket connect / DNS with a loud OSError.

Escape hatch: set ALLOW_LIVE_TESTS=1 to disable the guard entirely
(deliberate live runs only; the real .env / credentials are then used).
"""
import os
import socket
import tempfile
from unittest.mock import MagicMock

LIVE = os.environ.get("ALLOW_LIVE_TESTS") == "1"
GUARD_ACTIVE = not LIVE

FAKE_GSPREAD_CLIENT = MagicMock(name="fake_gspread_client")

if GUARD_ACTIVE:
    os.environ["GEMINI_API_KEY"] = "dummy-gemini-key"
    os.environ["TELEGRAM_TOKEN"] = "123456:DUMMY"
    os.environ["SPREADSHEET_ID"] = "dummy-spreadsheet-id"
    os.environ["GOOGLE_CREDENTIALS_JSON"] = "{}"
    for _k in [k for k in os.environ if k.startswith("GEMINI_API_KEY_TEST")]:
        del os.environ[_k]
    os.environ["GEMINI_API_KEY_TEST"] = "dummy-gemini-test-key"
    os.environ["GEMINI_API_KEY_TEST_1"] = "dummy-gemini-test-key-1"
    os.environ["NARRATOR_AB_ENABLED"] = "0"
    os.environ["GEMINI_EXPLICIT_CACHE"] = "0"
    os.environ["NARRATOR_AB_LOG_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="got_tests_"), "narrator_ab.jsonl")
    for _k in ("MODEL_MAIN_NAME", "MODEL_WORKER_NAME", "MODEL_GM_LOGIC_NAME",
               "MODEL_NARRATOR_NAME", "MODEL_NARRATOR_ALT_NAME", "WEBHOOK_URL",
               "NARRATOR_THINKING_LEVEL", "NARRATOR_PREAMBLE"):
        os.environ.pop(_k, None)

    import gspread
    import oauth2client.service_account as _sa

    gspread.authorize = lambda creds, *a, **k: FAKE_GSPREAD_CLIENT
    _sa.ServiceAccountCredentials.from_json_keyfile_name = classmethod(
        lambda cls, *a, **k: MagicMock(name="fake_creds"))
    _sa.ServiceAccountCredentials.from_json_keyfile_dict = classmethod(
        lambda cls, *a, **k: MagicMock(name="fake_creds"))

    _LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "", None}
    _orig_connect = socket.socket.connect
    _orig_connect_ex = socket.socket.connect_ex
    _orig_gai = socket.getaddrinfo

    def _check(addr):
        host = addr[0] if isinstance(addr, (tuple, list)) else addr
        if host not in _LOCAL:
            raise OSError(f"TEST GUARD: real network blocked -> {host} "
                          f"(set ALLOW_LIVE_TESTS=1 to allow)")

    def _connect(self, addr):
        _check(addr)
        return _orig_connect(self, addr)

    def _connect_ex(self, addr):
        _check(addr)
        return _orig_connect_ex(self, addr)

    def _gai(host, *a, **k):
        if host not in _LOCAL:
            raise OSError(f"TEST GUARD: DNS blocked -> {host} "
                          f"(set ALLOW_LIVE_TESTS=1 to allow)")
        return _orig_gai(host, *a, **k)

    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.getaddrinfo = _gai


import pytest as _pytest_cache  # noqa: E402


@_pytest_cache.fixture
def explicit_cache_on(monkeypatch):
    """Enable the Gemini explicit context cache (default OFF) for tests that exercise it."""
    import core.ai_client as _ai
    monkeypatch.setattr(_ai, "GEMINI_EXPLICIT_CACHE_ENABLED", True)
    yield
