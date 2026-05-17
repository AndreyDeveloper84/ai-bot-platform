"""Telegram outbound (send_message / send_photo / answer_callback_query) tests.

Phase 1 / CH1 (DRF-848). Behaviours pinned:

5. Proxy: TELEGRAM_PROXY → requests.post called with proxies={"https": url, ...}
6. Proxy fallback: empty TELEGRAM_PROXY + set OPENAI_PROXY → uses OPENAI_PROXY
7. No proxy: both empty → proxies=None (don't break local dev)
8. Returns r.ok: 200 → True, 500 → False
9. ConnectionError → False with logger.exception (NOT True unconditionally)
19. URL contains bot<token> from the tenant row
20. answer_callback_query gets called

Tests mock ``requests.post`` — never make live HTTP. Patches the symbol
imported into ``apps.channels.telegram.outbound`` so the assertions
work even if requests is referenced elsewhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import requests  # type: ignore[import-untyped]

from apps.channels.telegram import outbound


class _FakeTenant:
    """Stand-in for the Tenant model — avoids django_db setup for pure
    outbound tests. The outbound funnel only reads ``id`` and
    ``telegram_bot_token``."""

    def __init__(self, *, id="tenant-1", token="bottoken12345"):
        self.id = id
        self.telegram_bot_token = token


def _ok_response(status=200, text='{"ok": true}'):
    """Build a requests.Response stand-in with the .ok / .status_code / .text fields."""
    return SimpleNamespace(ok=200 <= status < 400, status_code=status, text=text)


class TestProxyResolution:
    """Behaviour 5, 6, 7 — proxy threading."""

    def test_telegram_proxy_used_when_set(self, settings):
        settings.TELEGRAM_PROXY = "http://prox:1080"
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert kwargs["proxies"] == {"https": "http://prox:1080", "http": "http://prox:1080"}

    def test_openai_proxy_fallback(self, settings):
        """TELEGRAM_PROXY empty, OPENAI_PROXY set → OPENAI_PROXY threaded."""
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = "http://openai-prox:443"
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert kwargs["proxies"] == {
            "https": "http://openai-prox:443",
            "http": "http://openai-prox:443",
        }

    def test_no_proxy_local_dev(self, settings):
        """Both empty → proxies=None (don't break local dev / CI)."""
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert kwargs["proxies"] is None

    def test_telegram_proxy_wins_over_openai(self, settings):
        """When both set, Telegram-specific override wins."""
        settings.TELEGRAM_PROXY = "http://tele-prox:1"
        settings.OPENAI_PROXY = "http://openai-prox:2"
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert kwargs["proxies"]["https"] == "http://tele-prox:1"


class TestReturnsRok:
    """Behaviour 8 — return value mirrors requests.Response.ok."""

    def test_200_returns_true(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response(200)):
            assert outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant()) is True

    def test_500_returns_false(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(
            outbound.requests, "post", return_value=_ok_response(500, text="srv err")
        ):
            assert outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant()) is False

    def test_400_returns_false(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(
            outbound.requests,
            "post",
            return_value=_ok_response(400, text='{"ok":false,"description":"bad"}'),
        ):
            assert outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant()) is False


class TestNetworkErrors:
    """Behaviour 9 — explicit guard: ConnectionError → False, NOT True."""

    def test_connection_error_returns_false(self, settings, caplog):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(
            outbound.requests,
            "post",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            result = outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant())
        assert result is False
        # logger.exception was called — captured by caplog.
        assert any("network_error" in rec.message for rec in caplog.records), (
            "expected a network_error log line"
        )

    def test_timeout_returns_false(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", side_effect=requests.Timeout("read timeout")):
            assert outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant()) is False

    def test_proxy_error_returns_false(self, settings):
        """When the proxy itself errors out, return False (don't unconditionally True)."""
        settings.TELEGRAM_PROXY = "http://nope:1"
        settings.OPENAI_PROXY = ""
        with patch.object(
            outbound.requests, "post", side_effect=requests.exceptions.ProxyError("proxy down")
        ):
            assert outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant()) is False


class TestUrlContainsToken:
    """Behaviour 19 — request URL carries bot<token> from the tenant row."""

    def test_url_uses_tenant_token(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        tenant = _FakeTenant(token="bot-token-abcdef")
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="42", text="hi", tenant=tenant)
        args, _ = mock_post.call_args
        # URL is the first positional arg.
        assert "/botbot-token-abcdef/sendMessage" in args[0]
        assert args[0].startswith("https://api.telegram.org/bot")

    def test_different_tenants_use_different_tokens(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        t1 = _FakeTenant(id="t1", token="TOKEN-AAA")
        t2 = _FakeTenant(id="t2", token="TOKEN-BBB")
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="x", tenant=t1)
            outbound.send_message(chat_id="1", text="x", tenant=t2)
        urls = [call.args[0] for call in mock_post.call_args_list]
        assert "TOKEN-AAA" in urls[0]
        assert "TOKEN-BBB" in urls[1]

    def test_empty_token_returns_false_without_http_call(self, settings):
        """Empty token must NOT hit the network — no fallback, no leak."""
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post") as mock_post:
            result = outbound.send_message(chat_id="1", text="x", tenant=_FakeTenant(token=""))
        assert result is False
        assert mock_post.called is False


class TestSendMessageBody:
    """Body structure pinning — chat_id, text, reply_markup."""

    def test_text_and_chat_id_in_body(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="42", text="привет", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["chat_id"] == "42"
        assert kwargs["json"]["text"] == "привет"

    def test_reply_markup_passes_through(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "cb:1"}]]}
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant(), reply_markup=markup)
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["reply_markup"] == markup

    def test_no_reply_markup_when_none(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_message(chat_id="1", text="hi", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert "reply_markup" not in kwargs["json"]


class TestSendPhoto:
    """send_photo basics — body shape + tenant token routing."""

    def test_send_photo_basic(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            ok = outbound.send_photo(
                chat_id="1",
                photo="https://x/y.jpg",
                caption="hi",
                tenant=_FakeTenant(),
            )
        assert ok is True
        args, kwargs = mock_post.call_args
        assert "sendPhoto" in args[0]
        assert kwargs["json"]["photo"] == "https://x/y.jpg"
        assert kwargs["json"]["caption"] == "hi"

    def test_send_photo_empty_caption_omitted(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            outbound.send_photo(chat_id="1", photo="file_id_abc", tenant=_FakeTenant())
        _, kwargs = mock_post.call_args
        assert "caption" not in kwargs["json"]


class TestAnswerCallbackQuery:
    """Behaviour 20 — answer_callback_query exists + works."""

    def test_answer_callback_query_basic(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response()) as mock_post:
            ok = outbound.answer_callback_query(callback_query_id="cqid-1", tenant=_FakeTenant())
        assert ok is True
        args, kwargs = mock_post.call_args
        assert "answerCallbackQuery" in args[0]
        assert kwargs["json"]["callback_query_id"] == "cqid-1"

    def test_answer_callback_query_failure_returns_false(self, settings):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        with patch.object(outbound.requests, "post", return_value=_ok_response(500)):
            ok = outbound.answer_callback_query(callback_query_id="cqid-1", tenant=_FakeTenant())
        assert ok is False


class TestTokenNotLogged:
    """Bot token must never appear in log records."""

    def test_token_not_in_error_log(self, settings, caplog):
        settings.TELEGRAM_PROXY = ""
        settings.OPENAI_PROXY = ""
        secret_token = "SUPER-SECRET-TOKEN-NOT-IN-LOGS"  # pragma: allowlist secret
        with patch.object(outbound.requests, "post", return_value=_ok_response(500)):
            outbound.send_message(chat_id="1", text="x", tenant=_FakeTenant(token=secret_token))
        assert all(secret_token not in rec.getMessage() for rec in caplog.records), (
            "bot token leaked into log message"
        )
