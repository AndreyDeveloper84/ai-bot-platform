"""Per-tap webhook dedup key for MAX callbacks (DRF-998)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from django.test import Client

_WEBHOOK_SECRET = "drf998-secret"  # pragma: allowlist secret — test-only literal


def _build_text_payload(*, update_id: str | None = None, mid: str | None = None) -> dict:
    update_id = update_id or f"upd-{uuid.uuid4().hex[:8]}"
    mid = mid or f"mid-{uuid.uuid4().hex[:8]}"
    return {
        "update_id": update_id,
        "update_type": "message_created",
        "timestamp": 1_731_320_000_000,
        "message": {
            "sender": {"user_id": 42, "name": "User"},
            "recipient": {"chat_id": 99, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": "Привет",
                "attachments": [],
            },
        },
    }


def _build_callback_payload(
    *,
    update_id: str | None = None,
    callback_id: str | None = None,
    mid: str | None = None,
    payload: str = "cb:welcome:book",
) -> dict:
    update_id = update_id or f"upd-{uuid.uuid4().hex[:8]}"
    callback_id = callback_id or f"cb-{uuid.uuid4().hex[:8]}"
    mid = mid or f"mid-{uuid.uuid4().hex[:8]}"
    return {
        "update_id": update_id,
        "update_type": "message_callback",
        "timestamp": 1_731_320_000_000,
        "callback": {
            "timestamp": 1_731_320_000_000,
            "callback_id": callback_id,
            "payload": payload,
            "user": {"user_id": 42, "name": "User", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": 99, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": "Привет",
                "attachments": [],
            },
        },
        "user_locale": "ru",
    }


@pytest.fixture(autouse=True)
def _webhook_secret(settings) -> None:
    settings.MAX_WEBHOOK_SECRET = _WEBHOOK_SECRET


@pytest.fixture
def _mock_enqueue():
    """Capture the payload passed to ``enqueue`` without touching Redis."""
    with patch("apps.ingress.views.enqueue") as mock:
        yield mock


@pytest.mark.django_db
def test_two_taps_same_message_are_both_enqueued(_mock_enqueue) -> None:
    """Two different taps on the same message must NOT dedup against each other."""
    client = Client()
    shared_mid = f"mid-{uuid.uuid4().hex[:8]}"
    shared_update_id = f"upd-{uuid.uuid4().hex[:8]}"

    response1 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(
            _build_callback_payload(
                update_id=shared_update_id,
                mid=shared_mid,
                callback_id=f"cb-{uuid.uuid4().hex[:8]}",
                payload="cb:first",
            )
        ),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )
    response2 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(
            _build_callback_payload(
                update_id=shared_update_id,
                mid=shared_mid,
                callback_id=f"cb-{uuid.uuid4().hex[:8]}",
                payload="cb:second",
            )
        ),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )

    assert response1.status_code == 200
    assert response2.status_code == 200
    assert _mock_enqueue.call_count == 2


@pytest.mark.django_db
def test_same_callback_retry_is_deduped(_mock_enqueue) -> None:
    """A replay of the exact same callback must be deduplicated."""
    client = Client()
    callback = _build_callback_payload()

    response1 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(callback),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )
    response2 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(callback),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )

    assert response1.status_code == 200
    assert response2.status_code == 200
    # Second response must report dedup.
    assert response1.json()["dedup"] is False
    assert response2.json()["dedup"] is True
    assert _mock_enqueue.call_count == 1


@pytest.mark.django_db
def test_text_message_still_dedups_by_update_id(_mock_enqueue) -> None:
    """Ordinary text messages keep using update_id/mid for deduplication."""
    client = Client()
    message = _build_text_payload()

    response1 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(message),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )
    response2 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(message),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )

    assert response1.status_code == 200
    assert response2.status_code == 200
    assert response2.json()["dedup"] is True
    assert _mock_enqueue.call_count == 1


@pytest.mark.django_db
def test_oversized_callback_id_is_normalized_and_deduped(_mock_enqueue) -> None:
    """A 250-character callback_id is hashed to fit external_event_id max_length=200,
    and a replay of the same oversized id still deduplicates.
    """
    client = Client()
    oversized_callback_id = "cb-" + "x" * 247  # total 250 chars
    callback = _build_callback_payload(callback_id=oversized_callback_id)

    response1 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(callback),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )
    response2 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(callback),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )

    assert response1.status_code == 200
    assert response2.status_code == 200
    assert response1.json()["dedup"] is False
    assert response2.json()["dedup"] is True
    assert _mock_enqueue.call_count == 1

    from apps.ingress.models import WebhookJournal

    row = WebhookJournal.objects.get()
    assert len(row.external_event_id) <= 200
    assert row.external_event_id.startswith("long-")
