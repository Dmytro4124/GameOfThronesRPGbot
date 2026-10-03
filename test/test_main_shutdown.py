"""Tests for main.on_shutdown / health_check / webhook-branch routes.

Async tests run via asyncio.run() (pytest-asyncio is not installed).
Regression: on_shutdown must NOT delete the webhook (Render sleep/wake).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import main

WEBHOOK = "https://example.onrender.com"


def _mock_bot():
    bot = MagicMock()
    bot.delete_webhook = AsyncMock()
    bot.session.close = AsyncMock()
    return bot


def test_shutdown_webhook_mode_keeps_webhook_and_closes_session():
    bot = _mock_bot()
    with patch.object(main, "WEBHOOK_URL", WEBHOOK), \
         patch("core.narrator_ab.drain_pending_sheet_writes", new=AsyncMock()):
        asyncio.run(main.on_shutdown(bot))
    bot.delete_webhook.assert_not_called()
    bot.session.close.assert_awaited_once()


def test_shutdown_polling_mode_does_not_delete_webhook():
    bot = _mock_bot()
    with patch.object(main, "WEBHOOK_URL", None), \
         patch("core.narrator_ab.drain_pending_sheet_writes", new=AsyncMock()):
        asyncio.run(main.on_shutdown(bot))
    bot.delete_webhook.assert_not_called()
    bot.session.close.assert_awaited_once()


def test_shutdown_drain_failure_still_closes_session():
    bot = _mock_bot()
    with patch.object(main, "WEBHOOK_URL", WEBHOOK), \
         patch("core.narrator_ab.drain_pending_sheet_writes",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        asyncio.run(main.on_shutdown(bot))  # must not raise
    bot.delete_webhook.assert_not_called()
    bot.session.close.assert_awaited_once()


def test_health_check_returns_200():
    resp = asyncio.run(main.health_check(MagicMock()))
    assert resp.status == 200
    assert resp.text == "Bot is alive"


def test_webhook_branch_registers_get_root():
    captured = {}

    def fake_run_app(app, **kwargs):
        captured["app"] = app

    with patch.object(main, "WEBHOOK_URL", WEBHOOK), \
         patch.object(main.web, "run_app", fake_run_app):
        main.main()

    app = captured["app"]
    routes = [(r.method, r.resource.canonical) for r in app.router.routes()]
    assert ("GET", "/") in routes
    assert any(m == "POST" and p == main.WEBHOOK_PATH for m, p in routes)
