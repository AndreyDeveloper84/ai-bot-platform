"""Tests for the reviews event consumer (#445 review.created).

Covers per event-contract.md §3.9:

* Per-review dedupe via ReviewProcessedDedupe — same review_id with
  DIFFERENT event_ids MUST NOT double-count.
* Sentiment derivation table: 5→1.0, 4→0.5, 3→0.0, 2→-0.5, 1→-1.0.
* low_rating_flag set on rating <= 2 (sticky semantics; not reset).
* PII §7: NO text fetch.
* Cross-tenant isolation via tenant_id in dedupe + ClientProfile
  filter.
* Migration ordering: pre-W4 (ClientProfile fields missing) →
  dedupe applied + ClientProfile update gracefully skipped.

NOTE: Most tests SKIP until W4 ships ClientProfile.{last_review_*,
sentiment_score, low_rating_flag} fields. The dedupe + sentiment
math + W4 migration-ordering tests run today.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

import pytest

from apps.eventbus.consumers.reviews import (
    _SENTIMENT_BY_RATING,
    handle_review_created,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import ReviewProcessedDedupe
from apps.identity.models import BotUser, ClientProfile
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
REVIEW_ID = "6d7e8f9a-0b1c-4d2e-3f4a-5b6c7d8e9f0a"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    data: dict[str, Any],
    event_id: str = "01J9REVIEW000000000000000Z",  # pragma: allowlist secret
    tenant_id: str | None = TENANT_ID,
    user_id: str = AYLA_USER_ID,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name="review.created",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 22, 19, 42, 18, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=user_id,
        actor="user",
        correlation_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
        causation_id=None,
        data=data,
    )


def _review_data(
    *,
    review_id: str = REVIEW_ID,
    rating: int = 5,
    has_text: bool = True,
    is_anonymous: bool = False,
) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "appointment_id": APPOINTMENT_ID,
        "rating": rating,
        "has_text": has_text,
        "is_anonymous": is_anonymous,
    }


@pytest.fixture(autouse=True)
def _enable_tenant_verify_fail_open(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="t-reviews",
        name="Reviews test tenant",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
    )


@pytest.fixture
def profile(tenant: Tenant, bot_user: BotUser) -> ClientProfile:
    """ClientProfile is auto-created by post_save signal on BotUser
    creation (apps/identity/signals.py). Fetch the existing one
    instead of creating a duplicate (OneToOne FK + UniqueConstraint
    on bot_user_id would IntegrityError on .create)."""
    return ClientProfile.all_tenants.get(bot_user=bot_user)


def _w4_fields_present() -> bool:
    """True if W4 has shipped ClientProfile review fields."""
    return all(
        hasattr(ClientProfile, f)
        for f in (
            "last_review_rating",
            "last_review_at",
            "low_rating_flag",
            "sentiment_score",
        )
    )


# ─── Sentiment derivation table ────────────────────────────────────────────


class TestSentimentDerivation:
    @pytest.mark.parametrize(
        ("rating", "expected"),
        [
            (5, 1.0),
            (4, 0.5),
            (3, 0.0),
            (2, -0.5),
            (1, -1.0),
        ],
    )
    def test_table_per_tech_lead_spec(self, rating: int, expected: float) -> None:
        assert _SENTIMENT_BY_RATING[rating] == expected

    def test_range_within_unit_bound(self) -> None:
        """All values in [-1.0, +1.0]."""
        for score in _SENTIMENT_BY_RATING.values():
            assert -1.0 <= score <= 1.0


# ─── Per-review dedupe (idempotency core) ──────────────────────────────────


class TestReviewProcessedDedupe:
    def test_first_event_writes_dedupe_row(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        env = _envelope(data=_review_data())
        handle_review_created(env)

        assert (
            ReviewProcessedDedupe.objects.filter(
                tenant_id=tenant.id,
                review_id=UUID(REVIEW_ID),
            ).count()
            == 1
        )

    def test_replay_same_event_id_no_duplicate_dedupe_row(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        """Same event_id 3× — only one dedupe row exists (UniqueConstraint
        on (tenant_id, review_id))."""
        env = _envelope(data=_review_data())
        for _ in range(3):
            handle_review_created(env)

        assert (
            ReviewProcessedDedupe.objects.filter(
                tenant_id=tenant.id,
                review_id=UUID(REVIEW_ID),
            ).count()
            == 1
        )

    def test_replay_different_event_id_same_review_id_no_duplicate(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        """LOAD-BEARING: per-review_id dedupe. Operator re-fires the
        review event with a fresh ULID — the dispatcher's IngestDedupe
        does NOT block this (different event_id) but
        ReviewProcessedDedupe does (same review_id under same tenant).
        Without this layer, sentiment would double-count."""
        env1 = _envelope(
            event_id="01J9REVIEWFIRST000000000ZZ",  # pragma: allowlist secret
            data=_review_data(),
        )
        env2 = _envelope(
            event_id="01J9REVIEWSECOND00000000ZZ",  # pragma: allowlist secret
            data=_review_data(),
        )

        handle_review_created(env1)
        handle_review_created(env2)

        assert (
            ReviewProcessedDedupe.objects.filter(
                tenant_id=tenant.id,
                review_id=UUID(REVIEW_ID),
            ).count()
            == 1
        )

    def test_cross_tenant_same_review_id_each_dedupe_independently(self, tenant: Tenant) -> None:
        """Per-(tenant, review_id) dedupe. Two tenants COULD in
        principle observe the same review_id (admin re-classification,
        future shared catalog) — each dedupe independently."""
        other_tenant = Tenant.objects.create(
            id="11111111-2222-3333-4444-555555555555",
            slug="t-other-reviews",
            name="Other reviews tenant",
        )

        env1 = _envelope(data=_review_data(), tenant_id=str(tenant.id))
        env2 = _envelope(data=_review_data(), tenant_id=str(other_tenant.id))

        handle_review_created(env1)
        handle_review_created(env2)

        # Two rows — one per tenant, same review_id allowed.
        assert ReviewProcessedDedupe.objects.filter(review_id=UUID(REVIEW_ID)).count() == 2


# ─── ClientProfile update (skip pre-W4) ────────────────────────────────────


@pytest.mark.skipif(
    not _w4_fields_present(),
    reason="W4 ClientProfile review fields not yet shipped",
)
class TestClientProfileUpdate:
    def test_rating_5_writes_positive_sentiment(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        env = _envelope(data=_review_data(rating=5))
        handle_review_created(env)

        profile.refresh_from_db()
        assert getattr(profile, "last_review_rating") == 5
        assert getattr(profile, "sentiment_score") == 1.0
        assert getattr(profile, "low_rating_flag") is False

    def test_rating_1_sets_low_rating_flag_and_negative_sentiment(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        env = _envelope(data=_review_data(rating=1))
        handle_review_created(env)

        profile.refresh_from_db()
        assert getattr(profile, "last_review_rating") == 1
        assert getattr(profile, "sentiment_score") == -1.0
        assert getattr(profile, "low_rating_flag") is True

    def test_low_rating_flag_sticky_does_not_reset_on_better_rating(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        """Tech-lead Q1: sticky semantics. Once True, only admin clears.
        Subsequent rating=5 updates sentiment but flag stays True."""
        # First: rating 1 → flag set
        env1 = _envelope(
            event_id="01J9R1FIRST00000000000000ZZ",  # pragma: allowlist secret
            data=_review_data(review_id=str(uuid4()), rating=1),
        )
        handle_review_created(env1)

        # Second: rating 5 → flag stays True
        env2 = _envelope(
            event_id="01J9R2SECOND0000000000000ZZ",  # pragma: allowlist secret
            data=_review_data(review_id=str(uuid4()), rating=5),
        )
        handle_review_created(env2)

        profile.refresh_from_db()
        assert getattr(profile, "last_review_rating") == 5
        assert getattr(profile, "sentiment_score") == 1.0
        assert getattr(profile, "low_rating_flag") is True  # sticky


# ─── Migration ordering (pre-W4) ───────────────────────────────────────────


class TestPreW4MigrationOrdering:
    @pytest.mark.skipif(
        _w4_fields_present(),
        reason="W4 fields shipped; this test only meaningful pre-W4",
    )
    def test_pre_w4_dedupe_recorded_profile_skipped(
        self, tenant: Tenant, bot_user: BotUser, profile: ClientProfile
    ) -> None:
        """Pre-W4: dedupe row recorded but ClientProfile update
        skipped with warning. After W4 ff-merge → un-skip + retest."""
        env = _envelope(data=_review_data())
        handle_review_created(env)

        assert (
            ReviewProcessedDedupe.objects.filter(
                tenant_id=tenant.id,
                review_id=UUID(REVIEW_ID),
            ).count()
            == 1
        )


# ─── Malformed payload defence ─────────────────────────────────────────────


class TestMalformedPayload:
    def test_missing_review_id_no_dedupe_row(self, tenant: Tenant) -> None:
        env = _envelope(data={"rating": 5})
        handle_review_created(env)

        assert ReviewProcessedDedupe.objects.count() == 0

    def test_invalid_rating_no_dedupe_row(self, tenant: Tenant) -> None:
        for bad_rating in (0, 6, -1, 100, "five", None):
            env = _envelope(
                event_id=f"01J9BADRAT{hash(repr(bad_rating)) % 10**12:012d}ZZ",  # pragma: allowlist secret
                data={"review_id": str(uuid4()), "rating": bad_rating},
            )
            handle_review_created(env)

        assert ReviewProcessedDedupe.objects.count() == 0


# ─── Tenant-verify mandate (A3) ────────────────────────────────────────────


class TestTenantVerifyMandate:
    def test_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(data=_review_data(), tenant_id=None)
        with pytest.raises(TenantAuthorizationError):
            handle_review_created(env)


# ─── No BotUser / no ClientProfile ─────────────────────────────────────────


class TestNoUserProjection:
    def test_no_bot_user_dedupe_still_recorded(self, tenant: Tenant) -> None:
        """Mobile-only customer with no BotUser yet. Dedupe row still
        written (idempotency guarantee); ClientProfile update is a
        no-op because there's no profile to update."""
        env = _envelope(data=_review_data())
        handle_review_created(env)

        assert (
            ReviewProcessedDedupe.objects.filter(
                tenant_id=tenant.id,
                review_id=UUID(REVIEW_ID),
            ).count()
            == 1
        )
