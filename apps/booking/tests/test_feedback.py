"""Phase 4 / F5 — submit_feedback service tests.

Covers:
* Happy path (rating 4-5) — fields written, no handoff.
* Low rating (3) — COMPLAINT AdminTask with NORMAL priority.
* Very low rating (1-2) — COMPLAINT AdminTask with HIGH priority.
* Idempotency: second call raises AlreadyRated.
* Validation: rating out of 1..5, non-int → InvalidRating.
* Pre-visit (visit_at in future) → NotCompletedYet.
* No-conversation degradation: rating saves, handoff_created=False.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.booking.models import BookingRequest
from apps.booking.services.feedback import (
    AlreadyRated,
    InvalidRating,
    NotCompletedYet,
    submit_feedback,
)
from apps.catalog.models import CatalogMaster, CatalogService
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


UTC = timezone.utc


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="fb-test", name="Feedback Test")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="42")


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        slug="lpg",
        name="LPG",
        external_updated_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=10,
        name="Anna",
        external_updated_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def _make_booking(
    tenant: Tenant,
    bot_user: BotUser,
    service: CatalogService,
    master: CatalogMaster,
    *,
    visit_at: datetime,
    conversation: Conversation | None = None,
) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        client_name="x",
        service_name=service.name,
        master_name=master.name,
        service=service,
        master=master,
        visit_at=visit_at,
        duration_min=60,
        status="confirmed",
        booking_source="ai_direct",
        billable=True,
        billing_reason="ai_direct + confirmed",
        ai_assist_score=1,
        attribution_metadata={"actor_type": "customer"},
        conversation=conversation,
    )


def _past_visit(now: datetime) -> datetime:
    return now - timedelta(hours=2)


@pytest.mark.django_db
def test_happy_path_rating_5_no_handoff(tenant, bot_user, service, master, conversation):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=_past_visit(now),
        conversation=conversation,
    )

    with tenant_scope(tenant):
        result = submit_feedback(booking, actor=bot_user, rating=5, comment="Отлично!", now=now)

    assert result.handoff_created is False
    assert result.task_id is None
    assert result.rating == 5
    booking.refresh_from_db()
    assert booking.rating == 5
    assert booking.feedback_comment == "Отлично!"
    assert booking.feedback_at == now


@pytest.mark.django_db
def test_rating_3_creates_normal_priority_complaint(
    tenant, bot_user, service, master, conversation
):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=_past_visit(now),
        conversation=conversation,
    )

    with tenant_scope(tenant):
        result = submit_feedback(booking, actor=bot_user, rating=3, comment="ok", now=now)

    assert result.handoff_created is True
    task = AdminTask.all_tenants.get(pk=result.task_id)
    assert task.task_type == AdminTask.TaskType.COMPLAINT
    assert task.priority == AdminTask.Priority.NORMAL


@pytest.mark.django_db
def test_rating_1_creates_high_priority_complaint(tenant, bot_user, service, master, conversation):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=_past_visit(now),
        conversation=conversation,
    )

    with tenant_scope(tenant):
        result = submit_feedback(booking, actor=bot_user, rating=1, comment="плохо", now=now)

    task = AdminTask.all_tenants.get(pk=result.task_id)
    assert task.priority == AdminTask.Priority.HIGH
    # Reason carries the rating + snippet for the operator dashboard.
    assert "[post-visit rating]" in task.reason
    assert "1/5" in task.reason


@pytest.mark.django_db
def test_no_conversation_degrades_gracefully(tenant, bot_user, service, master):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=_past_visit(now),
        conversation=None,
    )

    with tenant_scope(tenant):
        result = submit_feedback(booking, actor=bot_user, rating=1, comment="x", now=now)

    # Rating still persists — analytics signal preserved.
    assert result.rating == 1
    assert result.handoff_created is False
    assert result.task_id is None


@pytest.mark.django_db
def test_already_rated_is_idempotent_raise(tenant, bot_user, service, master, conversation):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=_past_visit(now),
        conversation=conversation,
    )

    with tenant_scope(tenant):
        submit_feedback(booking, actor=bot_user, rating=4, comment="", now=now)
        with pytest.raises(AlreadyRated):
            submit_feedback(booking, actor=bot_user, rating=2, comment="", now=now)


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [0, 6, -1, "5", 4.5])
def test_invalid_rating_raises(tenant, bot_user, service, master, conversation, bad):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=_past_visit(now),
        conversation=conversation,
    )
    with tenant_scope(tenant), pytest.raises(InvalidRating):
        submit_feedback(booking, actor=bot_user, rating=bad, comment="", now=now)


@pytest.mark.django_db
def test_pre_visit_rejects(tenant, bot_user, service, master, conversation):
    now = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    booking = _make_booking(
        tenant,
        bot_user,
        service,
        master,
        visit_at=now + timedelta(hours=1),
        conversation=conversation,
    )
    with tenant_scope(tenant), pytest.raises(NotCompletedYet):
        submit_feedback(booking, actor=bot_user, rating=5, comment="", now=now)
