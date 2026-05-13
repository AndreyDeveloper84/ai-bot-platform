"""X-Shadow header → enqueued payload threading (DRF-701 / Sprint 8 / N2)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from django.test import Client


_WEBHOOK_SECRET = "n2-secret"  # pragma: allowlist secret — test-only literal


def _build_payload(*, text: str = "Привет", mid: str | None = None) -> dict:
    mid = mid or f"n2-mid-{uuid.uuid4().hex[:8]}"
    return {
        "update_id": mid,
        "update_type": "message_created",
        "timestamp": 1_731_320_000_000,
        "message": {
            "sender": {"user_id": 42, "name": "N2 User"},
            "recipient": {"chat_id": 99, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": text,
                "attachments": [],
            },
        },
    }


@pytest.fixture(autouse=True)
def _webhook_secret(settings) -> None:
    settings.MAX_WEBHOOK_SECRET = _WEBHOOK_SECRET


@pytest.fixture
def _mock_enqueue():
    """Capture the payload passed to ``enqueue`` without touching Redis."""
    with patch("apps.ingress.views.enqueue") as mock:
        yield mock


@pytest.fixture
def _mock_record_webhook():
    """Pretend the journal write succeeded and the row is fresh."""
    with patch("apps.ingress.views.record_webhook") as mock:

        class _Row:
            resolved_tenant_id = None

        mock.return_value = (_Row(), True)  # (journal_row, created=True)
        yield mock


class TestShadowHeaderEnqueued:
    @pytest.mark.django_db
    def test_x_shadow_1_marks_payload(self, _mock_enqueue, _mock_record_webhook) -> None:
        """`X-Shadow: 1` → enqueued payload carries ``_shadow=True``."""
        client = Client()
        response = client.post(
            "/api/v1/ingress/max/",
            data=json.dumps(_build_payload()),
            content_type="application/json",
            HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
            HTTP_X_SHADOW="1",
        )
        assert response.status_code == 200

        _mock_enqueue.assert_called_once()
        kwargs = _mock_enqueue.call_args.kwargs
        assert kwargs["payload"].get("_shadow") is True

    @pytest.mark.django_db
    def test_missing_header_no_shadow_flag(self, _mock_enqueue, _mock_record_webhook) -> None:
        """Default path — no `X-Shadow` header → no ``_shadow`` key on payload."""
        client = Client()
        response = client.post(
            "/api/v1/ingress/max/",
            data=json.dumps(_build_payload()),
            content_type="application/json",
            HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
        )
        assert response.status_code == 200

        kwargs = _mock_enqueue.call_args.kwargs
        assert "_shadow" not in kwargs["payload"]

    @pytest.mark.django_db
    def test_x_shadow_other_value_ignored(self, _mock_enqueue, _mock_record_webhook) -> None:
        """Only `X-Shadow: 1` is treated as shadow — `true`, `yes`, etc. are
        ignored to keep the contract narrow and the parser cheap.
        """
        client = Client()
        response = client.post(
            "/api/v1/ingress/max/",
            data=json.dumps(_build_payload()),
            content_type="application/json",
            HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
            HTTP_X_SHADOW="true",
        )
        assert response.status_code == 200

        kwargs = _mock_enqueue.call_args.kwargs
        assert "_shadow" not in kwargs["payload"]


class TestChannelMessageField:
    """`ChannelMessage.is_shadow` exists with default False so callers
    that build the dataclass directly don't need to know about the field.
    """

    def test_is_shadow_default_false(self) -> None:
        from apps.orchestrator.pipeline import ChannelMessage

        msg = ChannelMessage(
            tenant_slug="t",
            channel="max",
            channel_user_id="u",
            chat_id="c",
            text="x",
        )
        assert msg.is_shadow is False

    def test_is_shadow_can_be_set(self) -> None:
        from apps.orchestrator.pipeline import ChannelMessage

        msg = ChannelMessage(
            tenant_slug="t",
            channel="max",
            channel_user_id="u",
            chat_id="c",
            text="x",
            is_shadow=True,
        )
        assert msg.is_shadow is True
