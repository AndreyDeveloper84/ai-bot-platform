"""Tests for IdempotencyKey + with_idempotency() helper (DRF-427 / B2).

Unit-level coverage of the happy path, TTL behaviour, and retention.
The G3 race-condition test lives in ``tests/tools/test_idempotency_race.py``
because it spawns real threads and needs cross-cutting fixture setup.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.tools.idempotency import AlreadyClaimed, with_idempotency
from apps.tools.models import IdempotencyKey
from apps.tools.tasks import cleanup_old_idempotency_keys

pytestmark = pytest.mark.django_db


class TestHappyPath:
    def test_first_caller_acquires_key(self):
        with with_idempotency("op:webhook:1", ttl_seconds=60) as row:
            assert row.key == "op:webhook:1"
            assert row.expires_at > timezone.now()
        # Row persists after the block exits.
        assert IdempotencyKey.objects.filter(key="op:webhook:1").exists()

    def test_second_caller_within_ttl_raises_already_claimed(self):
        with with_idempotency("op:webhook:2", ttl_seconds=60):
            pass
        with pytest.raises(AlreadyClaimed) as exc:
            with with_idempotency("op:webhook:2", ttl_seconds=60):
                pytest.fail("Block should not execute on AlreadyClaimed.")
        assert exc.value.row.key == "op:webhook:2"

    def test_second_caller_after_expiry_re_acquires(self):
        # Acquire with a tiny TTL, expire it, retry.
        with with_idempotency("op:webhook:3", ttl_seconds=1) as row:
            row.expires_at = timezone.now() - timedelta(seconds=10)
            row.save(update_fields=["expires_at"])

        # Now expired — a new caller should win.
        with with_idempotency("op:webhook:3", ttl_seconds=60) as new_row:
            assert new_row.key == "op:webhook:3"
            assert new_row.expires_at > timezone.now()

        # Exactly one row in the DB at any time.
        assert IdempotencyKey.objects.filter(key="op:webhook:3").count() == 1

    def test_payload_storage(self):
        with with_idempotency("op:webhook:4", ttl_seconds=60, payload={"status": "ok"}) as row:
            assert row.payload == {"status": "ok"}


class TestRetention:
    def test_cleanup_removes_expired_keys(self, settings):
        settings.IDEMPOTENCY_KEY_RETENTION_DAYS = 7
        now = timezone.now()

        # Key past its TTL but still inside the retention window — should still be
        # deleted because the cleanup logic OR's both conditions.
        IdempotencyKey.objects.create(
            key="expired-1",
            expires_at=now - timedelta(hours=1),
        )
        # Key well within TTL.
        IdempotencyKey.objects.create(
            key="active-1",
            expires_at=now + timedelta(hours=1),
        )

        deleted = cleanup_old_idempotency_keys()
        assert deleted == 1
        assert IdempotencyKey.objects.filter(key="expired-1").exists() is False
        assert IdempotencyKey.objects.filter(key="active-1").exists() is True

    def test_cleanup_removes_keys_older_than_retention(self, settings):
        settings.IDEMPOTENCY_KEY_RETENTION_DAYS = 7
        now = timezone.now()

        # Insert a key with a far-future TTL (caller passed days=99). After
        # 8 days the row is past retention even though it still has TTL.
        IdempotencyKey.objects.create(
            key="long-ttl",
            expires_at=now + timedelta(days=99),
        )
        IdempotencyKey.objects.filter(key="long-ttl").update(
            created_at=now - timedelta(days=8),
        )

        deleted = cleanup_old_idempotency_keys()
        assert deleted == 1

    def test_cleanup_default_7_days(self, settings):
        # Don't override; rely on base.py default.
        if hasattr(settings, "IDEMPOTENCY_KEY_RETENTION_DAYS"):
            del settings.IDEMPOTENCY_KEY_RETENTION_DAYS
        now = timezone.now()

        IdempotencyKey.objects.create(
            key="ancient",
            expires_at=now + timedelta(days=99),
        )
        IdempotencyKey.objects.filter(key="ancient").update(
            created_at=now - timedelta(days=8),
        )
        deleted = cleanup_old_idempotency_keys()
        assert deleted == 1

    def test_cleanup_returns_zero_when_nothing_to_delete(self, settings):
        settings.IDEMPOTENCY_KEY_RETENTION_DAYS = 7
        IdempotencyKey.objects.create(
            key="fresh",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        deleted = cleanup_old_idempotency_keys()
        assert deleted == 0
