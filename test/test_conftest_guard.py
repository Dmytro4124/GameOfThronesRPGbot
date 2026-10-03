"""Verifies test/conftest.py isolation (skipped when ALLOW_LIVE_TESTS=1)."""
import os
import socket

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ALLOW_LIVE_TESTS") == "1", reason="guard disabled")


def test_env_is_dummy():
    assert os.environ["SPREADSHEET_ID"] == "dummy-spreadsheet-id"
    assert os.environ["GEMINI_API_KEY"] == "dummy-gemini-key"


def test_gspread_authorize_is_fake():
    import gspread
    from unittest.mock import MagicMock
    assert isinstance(gspread.authorize(object()), MagicMock)


def test_service_account_loader_is_fake():
    from unittest.mock import MagicMock
    from oauth2client.service_account import ServiceAccountCredentials as S
    assert isinstance(S.from_json_keyfile_name("credentials.json", []), MagicMock)


def test_database_sheets_uses_mock_client():
    from unittest.mock import MagicMock
    import database.sheets as sh
    assert isinstance(sh.db.client, MagicMock)
    assert isinstance(sh.db.get_sheet("Users_DB"), MagicMock)


def test_public_ip_connect_blocked():
    s = socket.socket()
    try:
        with pytest.raises(OSError, match="TEST GUARD"):
            s.connect(("8.8.8.8", 443))
        with pytest.raises(OSError, match="TEST GUARD"):
            s.connect_ex(("8.8.8.8", 443))
    finally:
        s.close()


def test_dns_blocked():
    with pytest.raises(OSError, match="TEST GUARD"):
        socket.getaddrinfo("sheets.googleapis.com", 443)


def test_ab_log_path_is_temp():
    assert "narrator_ab.jsonl" in os.environ["NARRATOR_AB_LOG_PATH"]
    assert not os.environ["NARRATOR_AB_LOG_PATH"].startswith("logs")
