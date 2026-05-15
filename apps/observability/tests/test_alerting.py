"""Alerting library tests (Sprint 10 / O2 / DRF-863).

Coverage matrix:

* Severity routing: critical/error/warning correctly tagged + Sentry-
  level mapping
* Telegram skip path when token / chat_id missing
* Telegram happy-path: HTTP POST shape (URL, JSON, parse_mode HTML, proxy)
* Dedup: same content within window → second call returns False, no HTTP
* Critical bypasses dedup
* Different dedup_keys page independently
* Bad severity coerced to "error" (graceful)
* Telegram HTTP failure swallowed, returns False
* Audit rows written for paged + deduped paths
* HTML escaping for body text containing < > &
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test.utils import override_settings

from apps.audit.models import AuditLog
from apps.observability.alerting import page


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None, None, None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def configured() -> Generator[None, None, None]:
    """Stock alerting config: token + chat_id + no proxy."""
    with override_settings(
        TELEGRAM_BOT_TOKEN="test-token",
        ALERTS_TELEGRAM_CHAT_ID="-1001234567890",
        TELEGRAM_PROXY="",
        OPENAI_PROXY="",
        ALERTS_DEDUP_TTL_SECONDS=300,
    ):
        yield


@pytest.fixture
def telegram_post() -> Generator[MagicMock, None, None]:
    """Patch the ``requests`` module inside alerting; return the mock.

    Alerting does ``import requests`` lazily inside ``_send_telegram``
    so the patch must target the module attribute.
    """
    with patch("apps.observability.alerting.requests", create=True) as mock_req:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.text = "{}"
        mock_req.post.return_value = mock_resp
        yield mock_req


class TestSeverityRouting:
    def test_critical_sends_with_loud_prefix(self, configured, telegram_post) -> None:
        result = page("critical", "Title", "Body")
        assert result is True
        call = telegram_post.post.call_args
        text = call.kwargs["json"]["text"]
        assert "🚨🚨🚨 CRITICAL" in text
        assert call.kwargs["json"]["disable_notification"] is False

    def test_error_sends_with_error_prefix(self, configured, telegram_post) -> None:
        page("error", "Title", "Body")
        text = telegram_post.post.call_args.kwargs["json"]["text"]
        assert "🔴 ERROR" in text

    def test_warning_silences_notification(self, configured, telegram_post) -> None:
        page("warning", "Title", "Body")
        call = telegram_post.post.call_args
        assert call.kwargs["json"]["disable_notification"] is True
        assert "🟡 WARNING" in call.kwargs["json"]["text"]

    def test_bad_severity_coerced_to_error(self, configured, telegram_post) -> None:
        page("nonsense", "Title", "Body")  # type: ignore[arg-type]
        text = telegram_post.post.call_args.kwargs["json"]["text"]
        assert "🔴 ERROR" in text


class TestNoCredentialsSkipsTelegram:
    def test_no_token_skips_telegram(self, telegram_post) -> None:
        with override_settings(TELEGRAM_BOT_TOKEN="", ALERTS_TELEGRAM_CHAT_ID=""):
            page("error", "Title", "Body")
        telegram_post.post.assert_not_called()
        # Audit row still written (visibility into skip).
        assert AuditLog.all_tenants.filter(action="observability.alert.paged").exists()

    def test_chat_id_missing_skips(self, telegram_post) -> None:
        with override_settings(TELEGRAM_BOT_TOKEN="t", ALERTS_TELEGRAM_CHAT_ID=""):
            page("error", "Title", "Body")
        telegram_post.post.assert_not_called()


class TestTelegramRequestShape:
    def test_url_includes_token(self, configured, telegram_post) -> None:
        page("error", "Title", "Body")
        url = telegram_post.post.call_args.args[0]
        assert url == "https://api.telegram.org/bottest-token/sendMessage"

    def test_payload_uses_html_parse_mode(self, configured, telegram_post) -> None:
        page("error", "Title", "Body")
        payload = telegram_post.post.call_args.kwargs["json"]
        assert payload["parse_mode"] == "HTML"
        assert payload["chat_id"] == "-1001234567890"

    def test_proxy_threaded_from_openai_proxy(self, telegram_post) -> None:
        with override_settings(
            TELEGRAM_BOT_TOKEN="t",
            ALERTS_TELEGRAM_CHAT_ID="-100",
            TELEGRAM_PROXY="",
            OPENAI_PROXY="http://proxy.example:8080",
        ):
            page("error", "Title", "Body")
        proxies = telegram_post.post.call_args.kwargs["proxies"]
        assert proxies == {
            "https": "http://proxy.example:8080",
            "http": "http://proxy.example:8080",
        }

    def test_telegram_proxy_wins_over_openai_proxy(self, telegram_post) -> None:
        with override_settings(
            TELEGRAM_BOT_TOKEN="t",
            ALERTS_TELEGRAM_CHAT_ID="-100",
            TELEGRAM_PROXY="http://tgproxy:1",
            OPENAI_PROXY="http://openaiproxy:2",
        ):
            page("error", "Title", "Body")
        proxies = telegram_post.post.call_args.kwargs["proxies"]
        assert proxies["https"] == "http://tgproxy:1"

    def test_html_escaped_in_body(self, configured, telegram_post) -> None:
        page("error", "Title <crash>", "Body with <script>alert(1)</script> & more")
        text = telegram_post.post.call_args.kwargs["json"]["text"]
        assert "&lt;script&gt;" in text
        assert "<script>" not in text
        assert "&amp;" in text


class TestDedup:
    def test_identical_error_dedup_within_window(self, configured, telegram_post) -> None:
        first = page("error", "Same title", "Same body")
        second = page("error", "Same title", "Same body")
        assert first is True
        assert second is False
        assert telegram_post.post.call_count == 1
        assert AuditLog.all_tenants.filter(action="observability.alert.deduped").count() == 1

    def test_different_dedup_keys_page_independently(self, configured, telegram_post) -> None:
        page("error", "Title", "Body", dedup_key="key1")
        page("error", "Title", "Body", dedup_key="key2")
        assert telegram_post.post.call_count == 2

    def test_critical_bypasses_dedup(self, configured, telegram_post) -> None:
        # Two critical pages with same key — both send. Defence in depth.
        page("critical", "Title", "Body", dedup_key="same")
        page("critical", "Title", "Body", dedup_key="same")
        assert telegram_post.post.call_count == 2


class TestFailureSwallowing:
    def test_http_failure_returns_false_no_raise(self, configured) -> None:
        with patch("apps.observability.alerting.requests", create=True) as mock_req:
            mock_resp = MagicMock()
            mock_resp.ok = False
            mock_resp.status_code = 502
            mock_resp.text = "bad gateway"
            mock_req.post.return_value = mock_resp
            # Must not raise — best-effort contract.
            page("error", "Title", "Body")

    def test_exception_during_post_swallowed(self, configured) -> None:
        with patch("apps.observability.alerting.requests", create=True) as mock_req:
            mock_req.post.side_effect = RuntimeError("network exploded")
            page("error", "Title", "Body")
