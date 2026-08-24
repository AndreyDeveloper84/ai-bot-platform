"""DRF-1362 — MAX in-place message edit and its fallback.

The claim this file exists to settle: MAX *can* edit a message, so a
multi-select does not have to stack one new message per tap. The proof is not
this test but `legacy_maxbot/handlers/health_screening.py:243-320`, which has
run a chronic-illness multi-select on `bot.edit_message(...)` on this channel
since Phase 3.2A; these tests pin the REST call that the legacy SDK wraps.

The half that matters more is the negative one. MAX refuses edits routinely —
and it refuses them with **HTTP 200**, body `{"success": false}`. A caller
that only checks the status code reports those as successes and the user
stares at a keyboard that never moved. So: a refusal must be detected, and it
must cost a new message rather than the turn.
"""

from __future__ import annotations

import json

import pytest

from apps.channels.max.outbound import (
    MaxAPIError,
    edit_message,
    edit_message_or_send,
)


@pytest.fixture(autouse=True)
def _max_token(settings):
    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"
    return settings


class TestEditWireFormat:
    def test_put_to_messages_with_message_id_as_query_param(self, httpx_mock):
        httpx_mock.add_response(
            method="PUT",
            url="https://botapi.max.ru/messages?message_id=mid-1",
            json={"message": {"body": {"mid": "mid-1"}}},
        )
        edit_message(message_id="mid-1", text="новый текст")
        req = httpx_mock.get_request()
        assert req.method == "PUT"
        assert "message_id=mid-1" in str(req.url)
        body = json.loads(req.content)
        # Same quirk as send_message: the id lives in the query string only.
        assert "message_id" not in body
        assert body["text"] == "новый текст"

    def test_authorization_header_is_the_raw_token(self, httpx_mock):
        httpx_mock.add_response(method="PUT", json={"ok": True})
        edit_message(message_id="m", text="x")
        auth = httpx_mock.get_request().headers["Authorization"]
        assert auth == "test-token-xyz"
        assert not auth.lower().startswith("bearer")

    def test_empty_attachment_list_is_sent_and_strips_the_keyboard(self, httpx_mock):
        """`legacy_maxbot/menu_state.py:51` takes a menu off an old message
        with exactly this call. `if attachments:` would swallow it."""
        httpx_mock.add_response(method="PUT", json={"ok": True})
        edit_message(message_id="m", text="прежний текст", attachments=[])
        body = json.loads(httpx_mock.get_request().content)
        assert body["attachments"] == []

    def test_text_none_omits_the_field_entirely(self, httpx_mock):
        httpx_mock.add_response(method="PUT", json={"ok": True})
        edit_message(message_id="m", attachments=[{"type": "inline_keyboard"}])
        body = json.loads(httpx_mock.get_request().content)
        assert "text" not in body

    def test_attachments_none_leaves_the_existing_ones_alone(self, httpx_mock):
        httpx_mock.add_response(method="PUT", json={"ok": True})
        edit_message(message_id="m", text="x")
        body = json.loads(httpx_mock.get_request().content)
        assert "attachments" not in body

    def test_missing_token_raises_without_a_request(self, httpx_mock, settings):
        settings.MAX_BOT_TOKEN = ""
        with pytest.raises(MaxAPIError):
            edit_message(message_id="m", text="x")
        assert not httpx_mock.get_requests()


class TestEditRefusal:
    """How MAX actually says no. Measured against the documented contract
    (`dev.max.ru/docs-api/methods/PUT/messages`), which lists 200 as
    "success **or failure** — check the response"."""

    def test_two_hundred_with_success_false_is_an_error_not_a_success(self, httpx_mock):
        httpx_mock.add_response(
            method="PUT",
            status_code=200,
            json={"success": False, "message": "message.not.found"},
        )
        with pytest.raises(MaxAPIError) as exc:
            edit_message(message_id="stale", text="x")
        assert "message.not.found" in exc.value.body

    def test_a_success_envelope_without_the_key_is_not_a_failure(self, httpx_mock):
        """`success` is absent from the ordinary reply. Only explicit False
        counts — reading a missing key as failure would break every edit."""
        httpx_mock.add_response(method="PUT", json={"message": {"body": {"mid": "m"}}})
        assert edit_message(message_id="m", text="x") == {"message": {"body": {"mid": "m"}}}

    def test_success_true_passes(self, httpx_mock):
        httpx_mock.add_response(method="PUT", json={"success": True})
        assert edit_message(message_id="m", text="x") == {"success": True}

    @pytest.mark.parametrize("status", [400, 403, 404, 429, 500])
    def test_non_2xx_raises_with_the_status(self, httpx_mock, status):
        httpx_mock.add_response(method="PUT", status_code=status, text="nope")
        with pytest.raises(MaxAPIError) as exc:
            edit_message(message_id="m", text="x")
        assert exc.value.status_code == status

    def test_network_failure_raises_status_zero(self, httpx_mock):
        import httpx

        httpx_mock.add_exception(httpx.ConnectError("boom"))
        with pytest.raises(MaxAPIError) as exc:
            edit_message(message_id="m", text="x")
        assert exc.value.status_code == 0

    def test_two_hundred_with_a_non_json_body_does_not_crash(self, httpx_mock):
        httpx_mock.add_response(method="PUT", status_code=200, text="not json")
        assert edit_message(message_id="m", text="x") == {}


class TestEditOrSendFallback:
    """The brief's negative proof: a refused edit must NOT lose the turn."""

    def test_happy_path_edits_in_place_and_sends_nothing(self, httpx_mock):
        httpx_mock.add_response(method="PUT", json={"ok": True})
        assert edit_message_or_send(chat_id="42", message_id="m", text="обновлено") is True
        assert [r.method for r in httpx_mock.get_requests()] == ["PUT"]

    def test_a_refused_edit_falls_back_to_a_new_message(self, httpx_mock):
        """The 200/success:false case — the one a status check would miss."""
        httpx_mock.add_response(
            method="PUT", status_code=200, json={"success": False, "message": "too.old"}
        )
        httpx_mock.add_response(method="POST", json={"message": {"body": {"mid": "new"}}})

        assert edit_message_or_send(chat_id="42", message_id="old", text="обновлено") is False

        methods = [r.method for r in httpx_mock.get_requests()]
        assert methods == ["PUT", "POST"]
        post = httpx_mock.get_requests()[1]
        # The fallback is a real message to the right chat, carrying the same
        # text and keyboard — the tap is answered, not dropped.
        assert "chat_id=42" in str(post.url)
        assert json.loads(post.content)["text"] == "обновлено"

    @pytest.mark.parametrize("status", [400, 404, 429, 500])
    def test_every_http_refusal_falls_back_too(self, httpx_mock, status):
        httpx_mock.add_response(method="PUT", status_code=status, text="no")
        httpx_mock.add_response(method="POST", json={"ok": True})
        assert edit_message_or_send(chat_id="1", message_id="m", text="t") is False
        assert [r.method for r in httpx_mock.get_requests()] == ["PUT", "POST"]

    def test_a_network_failure_on_edit_falls_back_too(self, httpx_mock):
        import httpx

        httpx_mock.add_exception(httpx.ConnectError("down"), method="PUT")
        httpx_mock.add_response(method="POST", json={"ok": True})
        assert edit_message_or_send(chat_id="1", message_id="m", text="t") is False

    def test_no_message_id_sends_directly_and_is_not_an_error(self, httpx_mock):
        httpx_mock.add_response(method="POST", json={"ok": True})
        assert edit_message_or_send(chat_id="1", message_id=None, text="первый экран") is False
        assert [r.method for r in httpx_mock.get_requests()] == ["POST"]

    def test_the_keyboard_survives_the_fallback(self, httpx_mock):
        """A fallback that dropped the keyboard would answer the tap with a
        dead end — worse than the stacked message it replaces."""
        keyboard = [{"type": "inline_keyboard", "payload": {"buttons": [[{"text": "A"}]]}}]
        httpx_mock.add_response(method="PUT", status_code=404, text="gone")
        httpx_mock.add_response(method="POST", json={"ok": True})
        edit_message_or_send(chat_id="1", message_id="m", text="t", attachments=keyboard)
        assert json.loads(httpx_mock.get_requests()[1].content)["attachments"] == keyboard

    def test_a_failing_fallback_send_does_propagate(self, httpx_mock):
        """Silence on both halves would be a lost turn with no trace."""
        httpx_mock.add_response(method="PUT", status_code=404, text="gone")
        httpx_mock.add_response(method="POST", status_code=500, text="down")
        with pytest.raises(MaxAPIError):
            edit_message_or_send(chat_id="1", message_id="m", text="t")
