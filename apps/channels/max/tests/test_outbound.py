"""MAX outbound REST send_message tests (DRF-441 / Sprint 2 / D2).

Uses pytest-httpx to mock httpx.post. Tests pin the wire format
(Authorization header raw token, chat_id as query param, body shape)
plus error handling on 4xx/5xx and network failures.
"""

from __future__ import annotations

import pytest

from apps.channels.max.outbound import MaxAPIError, send_message


@pytest.fixture(autouse=True)
def _max_token(settings):
    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"
    return settings


class TestSendMessageHappyPath:
    def test_returns_response_json_on_200(self, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="https://botapi.max.ru/messages?chat_id=67890",
            json={"message": {"body": {"mid": "out-1"}}},
            status_code=200,
        )
        result = send_message(chat_id="67890", text="Привет")
        assert result == {"message": {"body": {"mid": "out-1"}}}

    def test_authorization_header_raw_token_not_bearer(self, httpx_mock):
        """MAX uses raw access token in Authorization, NOT Bearer prefix.
        Regression guard: the legacy code in
        legacy_notifications.max_bot.send_max_message has been working
        with raw token since 2026-04. If someone "fixes" it to Bearer,
        prod breaks silently.
        """

        httpx_mock.add_response(json={"ok": True}, status_code=200)
        send_message(chat_id="1", text="x")
        req = httpx_mock.get_request()
        assert req.headers["Authorization"] == "test-token-xyz"
        # Defensive: ensure no "Bearer " prefix snuck in.
        assert not req.headers["Authorization"].lower().startswith("bearer")

    def test_chat_id_as_query_param_not_body(self, httpx_mock):
        httpx_mock.add_response(json={"ok": True}, status_code=200)
        send_message(chat_id="42", text="hi")
        req = httpx_mock.get_request()
        assert "chat_id=42" in str(req.url)
        # Body must NOT contain chat_id (it lives in the query string).
        import json

        body = json.loads(req.content)
        assert "chat_id" not in body
        assert body["text"] == "hi"

    def test_attachments_pass_through_to_body(self, httpx_mock):
        httpx_mock.add_response(json={"ok": True}, status_code=200)
        send_message(
            chat_id="1",
            text="",
            attachments=[{"type": "image", "payload": {"url": "https://x/y.jpg"}}],
        )
        req = httpx_mock.get_request()
        import json

        body = json.loads(req.content)
        assert body["attachments"][0]["type"] == "image"


class TestSendMessageErrors:
    def test_4xx_raises_max_api_error(self, httpx_mock):
        httpx_mock.add_response(status_code=400, text="bad chat_id")
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="bad", text="x")
        assert exc_info.value.status_code == 400
        assert "bad chat_id" in exc_info.value.body

    def test_5xx_raises_max_api_error(self, httpx_mock):
        httpx_mock.add_response(status_code=502, text="upstream down")
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="1", text="x")
        assert exc_info.value.status_code == 502

    def test_network_error_raises_max_api_error_with_status_zero(self, httpx_mock):
        import httpx as _httpx

        httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="1", text="x")
        assert exc_info.value.status_code == 0
        assert "connection refused" in exc_info.value.body

    def test_missing_token_raises(self, settings, httpx_mock):
        settings.MAX_BOT_TOKEN = ""
        with pytest.raises(MaxAPIError) as exc_info:
            send_message(chat_id="1", text="x")
        assert exc_info.value.status_code == 0
        assert "MAX_BOT_TOKEN" in exc_info.value.body
        # No HTTP call should have been attempted.
        assert httpx_mock.get_requests() == []
