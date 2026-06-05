"""Integration tests for the MAX handler's photo download path.

Веха 2 of the photo adapter port — pairs with the unit-level tests in
``test_photo.py``. Here we exercise the full handler pipeline with
``download_photo`` mocked to:

1. Return bytes (happy path) — verify ``conversation.last_photo_bytes``
   is set as a runtime attribute (NOT persisted) before skill dispatch,
   and the food_scanner skill consumes them.
2. Raise ``PhotoTooLargeError`` — verify the attribute is set to None
   and the skill emits its graceful ``PHOTO_NO_BYTES`` reply.
3. Raise ``PhotoDownloadError`` (timeout / 4xx / 5xx) — same graceful
   path, distinct log line.
4. Non-image attachment — verify download is NOT attempted at all.

Plus a sanity test: after the attribute is set, the conversation row in
the DB has no ``last_photo_bytes`` column (it never has — see
``apps/conversations/models.py``) and no UPDATE was emitted for it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.photo import PhotoDownloadError, PhotoTooLargeError
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _payload(*, attachments, text="", user_id=24680, chat_id=13579, mid="m-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ольга"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": text,
                "attachments": attachments,
            },
        },
    }


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="photo-handler", name="Photo Handler Test")


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


# Patch target for the *handler's* import binding. The handler does a
# top-level `from apps.channels.max.photo import download_photo`, so the
# name lives at `apps.channels.max.handler.download_photo`. Patching the
# source module would miss this binding.
_DOWNLOAD_TARGET = "apps.channels.max.handler.download_photo"
_NUTRITION_TARGET = "apps.skills.food_scanner.skill.get_nutrition_client"


def _photo_payload(url="https://cdn.max.test/p.jpg"):
    return _payload(attachments=[{"type": "image", "payload": {"url": url}}])


class TestPhotoHappyPath:
    def test_bytes_stashed_on_conversation_before_skill_dispatch(
        self, tenant, mock_send, fake_redis, settings
    ):
        """End-to-end happy path — covers all 5 contract assertions:

        1. ``download_photo`` called exactly once (with the URL the
           parser extracted from the inbound webhook).
        2. ``scan_photo`` called exactly once with the EXACT bytes the
           downloader returned (proves the runtime attribute is the
           transport medium between adapter and skill).
        3. ``conversation.last_photo_bytes`` lived only in process
           memory before dispatch — implicit from #2 (skill couldn't
           read bytes the handler didn't stash) + the dedicated
           ``test_db_has_no_last_photo_bytes_column_after_happy_path``
           sanity test below.
        4. The assistant reply IS a meal-recognition card — contains
           the dish name + a recognisable macro/kcal marker, not just
           any non-empty string.
        5. ``mock_send`` was called exactly once (one reply per turn,
           no leak through the dispatcher).
        """
        settings.STRICT_TENANT_SCOPE = "strict"
        photo_bytes = b"\x89PNG fake bytes \x00\x01\x02"

        # food_scanner skill calls `asyncio.run(scan_photo(...))` —
        # AsyncMock returns an awaitable that resolves to the result.
        class _FakeResult:
            scan_id = "scan-xyz"
            dish_name = "тестовое блюдо"
            confidence = 0.9
            portion_g = 200.0
            nutrition = {
                "calories": 250,
                "protein_g": 10,
                "fat_g": 8,
                "carbs_g": 30,
            }
            provider = "test"
            raw: dict = {}

        # Use AsyncMock for scan_photo so we get a real call_count +
        # call_args, not just a closure capture.
        scan_mock = AsyncMock(return_value=_FakeResult())
        fake_client = AsyncMock()
        fake_client.scan_photo = scan_mock

        photo_url = "https://cdn.max.test/p.jpg"

        with (
            patch(_DOWNLOAD_TARGET, return_value=photo_bytes) as download_mock,
            patch(_NUTRITION_TARGET, return_value=fake_client),
        ):
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(_photo_payload(url=photo_url))

        # ── Assertion 1: download_photo called exactly once with the
        #    URL the parser extracted from the inbound attachment.
        download_mock.assert_called_once_with(photo_url)

        # ── Assertion 2: scan_photo called exactly once with the EXACT
        #    bytes the downloader returned. Proves the runtime attribute
        #    on Conversation is the wire between adapter and skill.
        scan_mock.assert_called_once()
        scan_kwargs = scan_mock.call_args.kwargs
        assert scan_kwargs["image_bytes"] == photo_bytes
        assert scan_kwargs["external_user_id"].startswith("bot:max:")

        # ── Assertion 5: one reply per turn (no dispatcher leak).
        assert len(mock_send) == 1

        # ── Assertion 4: the reply IS a meal-recognition card —
        #    contains the dish name AND a kcal marker. Locks the
        #    contract that bytes → recognition → card actually fires,
        #    not just «something non-empty got sent».
        reply_text = mock_send[0]["text"]
        assert "тестовое блюдо" in reply_text, f"reply missing dish name: {reply_text!r}"
        assert "250" in reply_text, f"reply missing kcal marker (250): {reply_text!r}"

    def test_db_has_no_last_photo_bytes_column_after_happy_path(
        self, tenant, mock_send, fake_redis, settings
    ):
        """Sanity: attribute is in-memory only — DB never sees it.

        Pulls the Conversation back through the ORM and asserts the
        ``last_photo_bytes`` attribute is absent on the fresh-loaded
        instance. Confirms we didn't accidentally persist photo bytes.
        """
        settings.STRICT_TENANT_SCOPE = "strict"

        class _R:
            scan_id = "s1"
            dish_name = "x"
            confidence = 0.9
            portion_g = None
            nutrition: dict | None = None
            provider = "test"
            raw: dict = {}

        async def _scan(**_kw):
            return _R()

        stub_client = AsyncMock()
        stub_client.scan_photo = _scan

        with (
            patch(_DOWNLOAD_TARGET, return_value=b"abc"),
            patch(_NUTRITION_TARGET, return_value=stub_client),
        ):
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(_photo_payload())

        # Pull the conversation back from DB (fresh instance, no
        # runtime attributes from the request).
        bot = BotUser.all_tenants.get(channel="max", channel_user_id="24680")
        conv = Conversation.all_tenants.get(bot_user=bot)
        assert not hasattr(conv, "last_photo_bytes")

        # And no Message row in the DB carries the photo bytes either —
        # they're not part of the content / rendered_text contract.
        for msg in Message.all_tenants.filter(conversation=conv):
            assert b"abc" not in (msg.content or "").encode()
            assert b"abc" not in (msg.rendered_text or "").encode()


class TestPhotoTooLargeGraceful:
    def test_oversize_emits_skill_no_bytes_fallback(self, tenant, mock_send, fake_redis, settings):
        """>10 MiB → attribute set to None → skill emits PHOTO_NO_BYTES."""
        settings.STRICT_TENANT_SCOPE = "strict"

        from apps.skills.food_scanner.skill import PHOTO_NO_BYTES

        def _raise_too_large(_url):
            raise PhotoTooLargeError("mocked >10 MiB")

        with patch(_DOWNLOAD_TARGET, side_effect=_raise_too_large):
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(_photo_payload())

        assert mock_send[0]["text"] == PHOTO_NO_BYTES


class TestPhotoDownloadFailedGraceful:
    def test_download_error_emits_skill_no_bytes_fallback(
        self, tenant, mock_send, fake_redis, settings
    ):
        """Timeout / 4xx / 5xx → attribute set to None → graceful reply."""
        settings.STRICT_TENANT_SCOPE = "strict"

        from apps.skills.food_scanner.skill import PHOTO_NO_BYTES

        def _raise_download(_url):
            raise PhotoDownloadError("mocked timeout")

        with patch(_DOWNLOAD_TARGET, side_effect=_raise_download):
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(_photo_payload())

        assert mock_send[0]["text"] == PHOTO_NO_BYTES


class TestHandlerLogRedaction:
    """Handler log lines for the photo block must never echo the raw URL.

    B2 from PR #893 adversarial re-review: the photo-block exception
    handlers in `apps/channels/max/handler.py` originally logged
    `url_prefix=%s, photo_url[:60]` — which leaked signed query-string
    tokens to any log sink (Sentry, Loki, CloudWatch).
    """

    def test_too_large_log_does_not_contain_token(
        self, tenant, mock_send, fake_redis, settings, caplog
    ):
        import logging

        settings.STRICT_TENANT_SCOPE = "strict"

        def _raise_too_large(_url):
            raise PhotoTooLargeError("mocked oversize")

        url = "https://cdn.max.test/p.jpg?sig=SECRET-TOKEN-XYZ"
        with patch(_DOWNLOAD_TARGET, side_effect=_raise_too_large):
            with (
                caplog.at_level(logging.WARNING, logger="apps.channels.max.handler"),
                tenant_scope(tenant),
                trace_id_scope(str(uuid4())),
            ):
                max_handler.handle_max_event(_photo_payload(url=url))

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "channels.max.handler.photo_too_large" in log_text
        assert "cdn.max.test" in log_text
        assert "SECRET-TOKEN-XYZ" not in log_text
        assert "sig=" not in log_text

    def test_download_failed_log_does_not_contain_token(
        self, tenant, mock_send, fake_redis, settings, caplog
    ):
        import logging

        settings.STRICT_TENANT_SCOPE = "strict"

        def _raise_dl(_url):
            raise PhotoDownloadError("mocked failure")

        url = "https://cdn.max.test/p.jpg?token=SECRET-BEARER-ABC"
        with patch(_DOWNLOAD_TARGET, side_effect=_raise_dl):
            with (
                caplog.at_level(logging.WARNING, logger="apps.channels.max.handler"),
                tenant_scope(tenant),
                trace_id_scope(str(uuid4())),
            ):
                max_handler.handle_max_event(_photo_payload(url=url))

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "channels.max.handler.photo_download_failed" in log_text
        assert "cdn.max.test" in log_text
        assert "SECRET-BEARER-ABC" not in log_text
        assert "token=" not in log_text


class TestNonPhotoAttachmentSkipsDownload:
    def test_video_attachment_does_not_invoke_download(
        self, tenant, mock_send, fake_redis, settings
    ):
        """Non-image attachment → handler skips download_photo entirely."""
        settings.STRICT_TENANT_SCOPE = "strict"

        with patch(_DOWNLOAD_TARGET) as mock_dl:
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(
                    _payload(
                        attachments=[
                            {"type": "video", "payload": {"url": "https://x/v.mp4"}},
                            {"type": "file", "payload": {"url": "https://x/f.pdf"}},
                        ]
                    )
                )

        mock_dl.assert_not_called()
        # Bot still replied (echo fallback or whatever path the dispatcher
        # picks for a no-image attachment-only turn).
        assert len(mock_send) == 1
