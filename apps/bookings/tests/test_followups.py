"""Post-visit follow-up beat tests (DRF-846 / Phase 1 / R3).

Covers the 12 spec behaviours plus a few defensive cases:

1. Happy path — yesterday's visit → message sent + context bumped.
2. Yesterday 23:59 МСК edge — still included.
3. Day-before-yesterday excluded.
4. Today excluded.
5. CANCELLED reminder excluded.
6. Dedup — two reminders for same visit → one message.
7. Idempotency: already sent today → skip.
8. Idempotency: sent yesterday → proceed.
9. Idempotency: missing context → proceed.
10. MAX send failure → no context bump, audit'd.
11. Multi-tenant fan-out — per-tenant, no leak.
12. No chat_id → skip with WARN.

Plus defensive cases: empty queue, race-condition tolerance, batch
limit, MSK midnight rollover.

Time handling: tests freeze ``timezone.now`` at a known UTC moment so
the "yesterday MSK" window math is deterministic. The MSK day window
helper is exercised directly via fixtures that construct ``visit_at``
relative to the frozen now.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from apps.booking.models import BookingReminder
from apps.bookings import followups as followups_mod
from apps.bookings.followups import (
    CONTEXT_KEY,
    send_post_visit_followups,
)
from apps.channels.max.outbound import MaxAPIError
from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


MSK = ZoneInfo("Europe/Moscow")

# Frozen "now" = 2026-05-16 19:00 МСК = 2026-05-16 16:00 UTC.
# This is exactly the beat fire moment per the schedule, which makes
# the window math easy to reason about:
#   yesterday MSK = 2026-05-15
#   yesterday MSK window UTC = [2026-05-14 21:00, 2026-05-15 21:00)
NOW_MSK = datetime(2026, 5, 16, 19, 0, tzinfo=MSK)
NOW_UTC = NOW_MSK.astimezone(dt_timezone.utc)
YESTERDAY_MSK_DATE = NOW_MSK.date() - timedelta(days=1)  # 2026-05-15
TODAY_MSK_ISO = NOW_MSK.date().isoformat()  # "2026-05-16"


@pytest.fixture(autouse=True)
def _freeze_now():
    """Freeze ``timezone.now`` at NOW_UTC for deterministic window math.

    Patching ``django.utils.timezone.now`` rather than the followups
    module's own reference because the helper calls ``timezone.now()``
    via the module-level import.
    """
    with patch("apps.bookings.followups.timezone.now", return_value=NOW_UTC):
        yield


def _msk(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """Helper — build a MSK datetime cast to UTC for visit_at."""
    return datetime(year, month, day, hour, minute, tzinfo=MSK).astimezone(dt_timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="rem-fu",
        name="Salon Follow-up",
    )


@pytest.fixture(autouse=True)
def _switches_open(settings):
    """Open both DRF-1301 switches for the behavioural suite.

    The production defaults are ENABLED=False / DRY_RUN=True, which is the
    point of the ticket. Every test below this line is about *which*
    people the task selects and what it says to them, and a task that
    returns at line one cannot answer either question. The switches
    themselves are tested in ``TestSwitches``, which sets them explicitly
    rather than relying on this fixture.
    """
    settings.POST_VISIT_FOLLOWUP_ENABLED = True
    settings.POST_VISIT_FOLLOWUP_DRY_RUN = False


def grant_consent(bot_user: BotUser, *, consent_type: str | None = None) -> ConsentRecord:
    """Give ``bot_user`` an active 152-ФЗ consent record."""
    return ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=consent_type or ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        source="test:fixture",
    )


def make_consented_user(tenant: Tenant, **kwargs) -> BotUser:
    """A BotUser that clears the consent gate unless a kwarg says otherwise."""
    kwargs.setdefault("channel", "max")
    kwargs.setdefault("channel_user_id", "bu-fu-1")
    kwargs.setdefault("chat_id", "chat-fu-1")
    kwargs.setdefault("context", {})
    kwargs.setdefault("consent_at", NOW_UTC - timedelta(days=30))
    user = BotUser.all_tenants.create(tenant=tenant, **kwargs)
    if user.consent_at is not None:
        grant_consent(user)
    return user


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    """A client who completed onboarding and consented under 152-ФЗ.

    ``consent_at`` and the matching ConsentRecord are part of the fixture
    rather than of each test because the suite below is about window
    math, dedup and delivery — none of which is reachable for somebody
    who never consented. That the gate actually bites is proven in
    ``TestConsentGate``, against users built without consent, not by
    weakening this fixture.
    """
    return make_consented_user(
        tenant,
        phone="79991234567",
        client_name="Anna",
    )


def _make_reminder(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    visit_at,
    yc_id: str = "yc-fu",
    kind: str = BookingReminder.Kind.DAY_BEFORE,
    status: str = BookingReminder.Status.SENT,
    master_name: str = "Lera",
) -> BookingReminder:
    """Helper — create a BookingReminder with sensible defaults.

    ``scheduled_at`` is computed from visit_at to match the factory
    convention (T-24h or T-2h ahead of the visit).
    """
    if kind == BookingReminder.Kind.DAY_BEFORE:
        scheduled_at = visit_at - timedelta(hours=24)
    else:
        scheduled_at = visit_at - timedelta(hours=2)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=kind,
        status=status,
        scheduled_at=scheduled_at,
        master_name=master_name,
        service_name="Массаж",
    )


# ────────────────────────────────────────────────────────────────────
# 1. Happy path
# ────────────────────────────────────────────────────────────────────
class TestHappyPath:
    def test_yesterday_visit_sends_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),  # yesterday 14:00 MSK
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()

        assert result["sent"] == 1
        assert result["skipped_already_sent"] == 0
        assert result["skipped_no_chat_id"] == 0
        assert result["send_failed"] == 0

        assert mock_send.call_count == 1
        kwargs = mock_send.call_args.kwargs
        assert kwargs["chat_id"] == "chat-fu-1"
        assert kwargs["attachments"] is None
        text = kwargs["text"]
        # Copy should mention "вчерашний визит" + the master name.
        assert "вчерашний визит" in text
        assert "Lera" in text

        # Idempotency key bumped.
        bot_user.refresh_from_db()
        assert bot_user.context[CONTEXT_KEY] == TODAY_MSK_ISO

    def test_master_name_blank_uses_fallback_in_copy(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
            master_name="",
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        text = mock_send.call_args.kwargs["text"]
        # No empty "к " — uses the fallback word.
        assert "к мастеру" in text or "мастеру" in text


# ────────────────────────────────────────────────────────────────────
# 2. Yesterday-window edges
# ────────────────────────────────────────────────────────────────────
class TestYesterdayWindowEdges:
    def test_yesterday_23_59_msk_included(self, tenant: Tenant, bot_user: BotUser) -> None:
        # Visit at the very last minute of yesterday MSK — still in window.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 23, 59),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1

    def test_yesterday_00_00_msk_included(self, tenant: Tenant, bot_user: BotUser) -> None:
        # First instant of yesterday MSK is the inclusive start.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 0, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1


# ────────────────────────────────────────────────────────────────────
# 3. Day-before-yesterday excluded
# ────────────────────────────────────────────────────────────────────
class TestDayBeforeYesterdayExcluded:
    def test_two_days_ago_not_sent(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 14, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        mock_send.assert_not_called()

    def test_yesterday_minus_one_second_excluded(self, tenant: Tenant, bot_user: BotUser) -> None:
        # 2026-05-14 23:59:59 MSK — last second BEFORE yesterday-start.
        # (Yesterday-start = 2026-05-15 00:00 MSK exactly.)
        visit_at = datetime(2026, 5, 14, 23, 59, 59, tzinfo=MSK).astimezone(dt_timezone.utc)
        _make_reminder(tenant=tenant, bot_user=bot_user, visit_at=visit_at)
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        mock_send.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# 4. Today's visit excluded
# ────────────────────────────────────────────────────────────────────
class TestTodayExcluded:
    def test_today_visit_not_sent(self, tenant: Tenant, bot_user: BotUser) -> None:
        # Today's visit hasn't happened yet at 19:00 MSK fire time (or
        # is happening right now); either way it's not "yesterday's".
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 16, 10, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        mock_send.assert_not_called()

    def test_today_00_00_msk_excluded(self, tenant: Tenant, bot_user: BotUser) -> None:
        # End-exclusive boundary — today 00:00 MSK is the first instant
        # OUTSIDE the yesterday window.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 16, 0, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        mock_send.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# 5. CANCELLED reminder excluded
# ────────────────────────────────────────────────────────────────────
class TestCancelledExcluded:
    def test_cancelled_reminder_no_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
            status=BookingReminder.Status.CANCELLED,
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        mock_send.assert_not_called()
        # Context untouched.
        bot_user.refresh_from_db()
        assert CONTEXT_KEY not in (bot_user.context or {})

    def test_cancelled_one_of_two_falls_back_to_other(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # If the DAY_BEFORE row was cancelled but TWO_HOURS still SENT,
        # the visit did happen — TWO_HOURS row alone yields one nudge.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
            yc_id="yc-cx-1",
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.CANCELLED,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
            yc_id="yc-cx-2",
            kind=BookingReminder.Kind.TWO_HOURS,
            status=BookingReminder.Status.SENT,
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1


# ────────────────────────────────────────────────────────────────────
# 6. Dedup — one client, two reminders → one message
# ────────────────────────────────────────────────────────────────────
class TestDedup:
    def test_two_reminders_one_visit_one_message(self, tenant: Tenant, bot_user: BotUser) -> None:
        visit_at = _msk(2026, 5, 15, 14, 0)
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=visit_at,
            yc_id="yc-dedup-1",
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.SENT,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=visit_at,
            yc_id="yc-dedup-2",
            kind=BookingReminder.Kind.TWO_HOURS,
            status=BookingReminder.Status.SENT,
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        # Exactly one nudge, not two.
        assert result["sent"] == 1
        assert mock_send.call_count == 1


# ────────────────────────────────────────────────────────────────────
# 7-9. Idempotency
# ────────────────────────────────────────────────────────────────────
class TestIdempotency:
    def test_already_sent_today_skipped(self, tenant: Tenant, bot_user: BotUser) -> None:
        bot_user.context = {CONTEXT_KEY: TODAY_MSK_ISO}
        bot_user.save()
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        assert result["skipped_already_sent"] == 1
        mock_send.assert_not_called()

    def test_sent_yesterday_proceeds(self, tenant: Tenant, bot_user: BotUser) -> None:
        # A "last_followup_sent_at" from yesterday is stale — proceed
        # and overwrite with today's date.
        bot_user.context = {CONTEXT_KEY: YESTERDAY_MSK_DATE.isoformat()}
        bot_user.save()
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1
        bot_user.refresh_from_db()
        assert bot_user.context[CONTEXT_KEY] == TODAY_MSK_ISO

    def test_missing_context_key_proceeds(self, tenant: Tenant, bot_user: BotUser) -> None:
        # Context dict exists but doesn't have the key.
        bot_user.context = {"some_other_flag": True}
        bot_user.save()
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1
        bot_user.refresh_from_db()
        assert bot_user.context[CONTEXT_KEY] == TODAY_MSK_ISO
        # Pre-existing keys preserved.
        assert bot_user.context["some_other_flag"] is True

    def test_none_context_tolerated(self, tenant: Tenant, bot_user: BotUser) -> None:
        # Defensive: even though the column is NOT NULL (default=dict),
        # the helper must tolerate a None attribute on the in-memory
        # instance (e.g. a select_related cache miss, a corrupted ORM
        # load, or future schema relaxation). We exercise the helper
        # directly to assert it doesn't crash, then verify the full
        # path with an empty-dict context (the JSONField-NOT-NULL
        # equivalent).
        from apps.bookings.followups import _already_followed_up_today

        assert _already_followed_up_today(None, NOW_MSK.date()) is False

        # Now exercise the full task path with the default empty {}.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1
        bot_user.refresh_from_db()
        assert bot_user.context == {CONTEXT_KEY: TODAY_MSK_ISO}

    def test_malformed_context_value_treated_as_unset(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # A non-string stored value (legacy bug — e.g. someone wrote a
        # timestamp int) shouldn't crash the comparison. Treat as
        # "never sent" and proceed.
        bot_user.context = {CONTEXT_KEY: 1234567890}  # int, not str
        bot_user.save()
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 1
        assert mock_send.call_count == 1


# ────────────────────────────────────────────────────────────────────
# 10. Send failure — no context bump
# ────────────────────────────────────────────────────────────────────
class TestSendFailure:
    def test_max_api_error_no_context_bump(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch(
            "apps.bookings.followups.send_message",
            side_effect=MaxAPIError(503, "service unavailable"),
        ):
            result = send_post_visit_followups()
        assert result["send_failed"] == 1
        assert result["sent"] == 0
        bot_user.refresh_from_db()
        # Idempotency key NOT bumped — next day's run can retry.
        assert CONTEXT_KEY not in (bot_user.context or {})

    def test_unexpected_exception_no_context_bump(
        self, tenant: Tenant, bot_user: BotUser, caplog
    ) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with (
            patch(
                "apps.bookings.followups.send_message",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level(logging.ERROR, logger="apps.bookings.followups"),
        ):
            result = send_post_visit_followups()
        assert result["send_failed"] == 1
        bot_user.refresh_from_db()
        assert CONTEXT_KEY not in (bot_user.context or {})
        # log.exception fired.
        assert any("send_unexpected" in rec.getMessage() for rec in caplog.records)


# ────────────────────────────────────────────────────────────────────
# 11. Multi-tenant fan-out
# ────────────────────────────────────────────────────────────────────
class TestMultiTenant:
    def test_each_tenant_gets_own_message(self, db) -> None:
        t1 = Tenant.objects.create(slug="salon-fu-a", name="Salon A")
        t2 = Tenant.objects.create(slug="salon-fu-b", name="Salon B")
        bu1 = make_consented_user(
            t1,
            channel_user_id="bu-fu-a",
            chat_id="cli-A",
            phone="79990000001",
            client_name="Alice",
        )
        bu2 = make_consented_user(
            t2,
            channel_user_id="bu-fu-b",
            chat_id="cli-B",
            phone="79990000002",
            client_name="Bob",
        )
        _make_reminder(
            tenant=t1,
            bot_user=bu1,
            visit_at=_msk(2026, 5, 15, 12, 0),
            yc_id="yc-mt-A",
        )
        _make_reminder(
            tenant=t2,
            bot_user=bu2,
            visit_at=_msk(2026, 5, 15, 15, 0),
            yc_id="yc-mt-B",
        )

        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()

        assert result["sent"] == 2
        assert mock_send.call_count == 2
        chat_ids = {call.kwargs["chat_id"] for call in mock_send.call_args_list}
        assert chat_ids == {"cli-A", "cli-B"}

        bu1.refresh_from_db()
        bu2.refresh_from_db()
        assert bu1.context[CONTEXT_KEY] == TODAY_MSK_ISO
        assert bu2.context[CONTEXT_KEY] == TODAY_MSK_ISO


# ────────────────────────────────────────────────────────────────────
# 12. No chat_id → skip + WARN
# ────────────────────────────────────────────────────────────────────
class TestNoChatId:
    def test_empty_chat_id_skipped(self, tenant: Tenant, caplog) -> None:
        bu = make_consented_user(
            tenant,
            channel_user_id="bu-no-chat",
            chat_id="",  # empty — can't reach them
            phone="79990000099",
            client_name="No Reach",
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bu,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with (
            patch("apps.bookings.followups.send_message") as mock_send,
            caplog.at_level(logging.WARNING, logger="apps.bookings.followups"),
        ):
            result = send_post_visit_followups()
        assert result["sent"] == 0
        assert result["skipped_no_chat_id"] == 1
        mock_send.assert_not_called()
        assert any("no_chat_id" in rec.getMessage() for rec in caplog.records)
        # Context NOT bumped — a future re-link (e.g. client logs into a
        # different channel) should let the follow-up fire later.
        bu.refresh_from_db()
        assert CONTEXT_KEY not in (bu.context or {})

    def test_whitespace_only_chat_id_treated_as_empty(self, tenant: Tenant) -> None:
        bu = make_consented_user(
            tenant,
            channel_user_id="bu-ws-chat",
            chat_id="   ",
            phone="79990000098",
            client_name="WS",
        )
        _make_reminder(tenant=tenant, bot_user=bu, visit_at=_msk(2026, 5, 15, 14, 0))
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["skipped_no_chat_id"] == 1
        mock_send.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# Defensive — empty queue, batch limit, second run idempotent
# ────────────────────────────────────────────────────────────────────
class TestEmptyQueue:
    def test_no_eligible_rows_returns_zero_counters(self, tenant: Tenant) -> None:
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result == {
            "sent": 0,
            "skipped_already_sent": 0,
            "skipped_no_chat_id": 0,
            "skipped_blocked": 0,
            "send_failed": 0,
            "would_send": 0,
            "dry_run": 0,
        }
        mock_send.assert_not_called()


class TestBatchLimit:
    def test_batch_limit_enforced(self, tenant: Tenant) -> None:
        # 5 distinct BotUsers, each with one yesterday-visit reminder.
        for i in range(5):
            bu = make_consented_user(
                tenant,
                channel_user_id=f"bu-batch-{i}",
                chat_id=f"chat-batch-{i}",
                phone=f"7999000{i:04d}",
                client_name=f"Client {i}",
            )
            _make_reminder(
                tenant=tenant,
                bot_user=bu,
                visit_at=_msk(2026, 5, 15, 10 + i, 0),
                yc_id=f"yc-batch-{i}",
            )
        with (
            patch.object(followups_mod, "BATCH_LIMIT", 2),
            patch("apps.bookings.followups.send_message") as mock_send,
        ):
            result = send_post_visit_followups()
        assert mock_send.call_count == 2
        assert result["sent"] == 2


class TestSecondRunIdempotent:
    def test_second_run_same_day_is_noop(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            first = send_post_visit_followups()
            second = send_post_visit_followups()
        assert first["sent"] == 1
        assert second["sent"] == 0
        assert second["skipped_already_sent"] == 1
        # Only the first run dispatched.
        assert mock_send.call_count == 1


class TestRaceCondition:
    def test_concurrent_already_sent_value_blocks_second_worker(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Race scenario: worker A bumps context mid-flight; worker B
        re-reads the BotUser inside the loop and finds the key already
        set. We simulate by pre-setting the context just before the
        send for the only row, then verifying the second invocation
        is a no-op.

        Strictly speaking this is the same as TestSecondRunIdempotent
        — included as a separate test to document the intent.
        """
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 14, 0),
        )
        # Simulate "the other worker already finished" by stamping the
        # idempotency key before our run starts.
        BotUser.all_tenants.filter(pk=bot_user.pk).update(context={CONTEXT_KEY: TODAY_MSK_ISO})
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        assert result["sent"] == 0
        assert result["skipped_already_sent"] == 1
        mock_send.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# MSK midnight rollover — direct check of window helper
# ────────────────────────────────────────────────────────────────────
class TestMoscowWindow:
    def test_window_correctly_brackets_yesterday(self) -> None:
        from apps.bookings.followups import _moscow_day_window

        start_utc, end_utc, today_msk = _moscow_day_window(NOW_UTC)
        # Yesterday start MSK = 2026-05-15 00:00 MSK = 2026-05-14 21:00 UTC.
        assert start_utc == datetime(2026, 5, 14, 21, 0, tzinfo=dt_timezone.utc)
        # Today start MSK = 2026-05-16 00:00 MSK = 2026-05-15 21:00 UTC.
        assert end_utc == datetime(2026, 5, 15, 21, 0, tzinfo=dt_timezone.utc)
        assert today_msk.isoformat() == TODAY_MSK_ISO

    def test_window_at_msk_midnight(self) -> None:
        from apps.bookings.followups import _moscow_day_window

        # Run "now" at exactly 2026-05-16 00:00 MSK = 2026-05-15 21:00 UTC.
        midnight_msk_as_utc = datetime(2026, 5, 15, 21, 0, tzinfo=dt_timezone.utc)
        start_utc, end_utc, today_msk = _moscow_day_window(midnight_msk_as_utc)
        # "Yesterday" should still be 2026-05-15 from midnight's
        # perspective (we just rolled into 2026-05-16).
        assert today_msk.isoformat() == "2026-05-16"
        assert start_utc == datetime(2026, 5, 14, 21, 0, tzinfo=dt_timezone.utc)
        assert end_utc == datetime(2026, 5, 15, 21, 0, tzinfo=dt_timezone.utc)


# ────────────────────────────────────────────────────────────────────
# B11 conservative blockers (P0 PRE_PILOT, founder sequence #3)
# ────────────────────────────────────────────────────────────────────


def _make_booking_for_followup(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    completed_at=None,
    status: str | None = None,
):
    """Helper — create a BookingRequest row to exercise B11 blockers."""
    from apps.booking.models import BookingRequest

    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        service_name="Массаж",
        master_name="Lera",
        client_name="Anna",
        client_phone="79991234567",
        status=status or BookingRequest.Status.CONFIRMED,
        completed_at=completed_at,
    )


def _link_reminder_to_booking(reminder: BookingReminder, booking) -> None:
    """Attach booking_request FK after reminder creation (mirrors B5 wire-up)."""
    BookingReminder.all_tenants.filter(pk=reminder.pk).update(booking_request=booking)
    reminder.refresh_from_db()


class TestB11ConservativeBlockers:
    """Per founder pilot_scope_discipline #3 + Tau §4.1 cut к pilot scope.

    4 pilot blockers implementable со текущей моделью; 7 remaining states
    deferred к Phase 1 Ayla event integration."""

    def test_opt_out_blocks_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        """proactive_messages_opt_out=True → never enters the selection.

        Asserted as "not a candidate", not as "a candidate that was
        blocked" (DRF-1301). The veto moved into ``_eligible_reminders``
        so the batch limit cannot crowd a consenting person out behind a
        run of opted-out rows, which means an opted-out person now
        produces no Decision at all — hence ``skipped_blocked == 0`` here
        and an empty plan below. The stricter assertion is the point: a
        counter of 1 would mean they had been considered.
        """
        BotUser.all_tenants.filter(pk=bot_user.pk).update(proactive_messages_opt_out=True)
        bot_user.refresh_from_db()
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        assert followups_mod.plan_post_visit_followups() == []
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["sent"] == 0
        assert result["skipped_blocked"] == 0

    def test_opt_out_still_blocks_if_the_queryset_filter_is_ever_removed(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """The per-row belt, tested directly because the filter hides it.

        ``_consent_blocker`` re-asserts the opt-out veto that
        ``_eligible_reminders`` already filtered, so in the running task
        that branch is unreachable. Unreachable code rots. This calls it
        directly, so a future edit that drops the queryset filter finds
        the veto still standing rather than discovering it was removed
        along with its only test.
        """
        BotUser.all_tenants.filter(pk=bot_user.pk).update(proactive_messages_opt_out=True)
        bot_user.refresh_from_db()
        assert followups_mod._consent_blocker(bot_user) == "opt_out"

    def test_completed_at_null_blocks_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        """booking.completed_at IS NULL → no send (visit not registered)."""
        booking = _make_booking_for_followup(tenant=tenant, bot_user=bot_user, completed_at=None)
        rem = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        _link_reminder_to_booking(rem, booking)
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["skipped_blocked"] == 1

    def test_booking_cancelled_blocks_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        """booking.status=CANCELLED → no send (bundles refund-cancellation
        case per Tau §4.1 conservative cut)."""
        from apps.booking.models import BookingRequest

        booking = _make_booking_for_followup(
            tenant=tenant,
            bot_user=bot_user,
            completed_at=NOW_UTC - timedelta(hours=1),  # had completed_at
            status=BookingRequest.Status.CANCELLED,  # but then cancelled
        )
        rem = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        _link_reminder_to_booking(rem, booking)
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_not_called()

    def test_booking_rescheduled_blocks_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        from apps.booking.models import BookingRequest

        booking = _make_booking_for_followup(
            tenant=tenant,
            bot_user=bot_user,
            completed_at=NOW_UTC - timedelta(hours=1),
            status=BookingRequest.Status.RESCHEDULED,
        )
        rem = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        _link_reminder_to_booking(rem, booking)
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_not_called()

    def test_payment_failures_threshold_blocks_followup(
        self, tenant: Tenant, bot_user: BotUser, settings
    ) -> None:
        """consecutive_payment_failures >= threshold (default 3) → no send.

        Per project_payment_failed_dm_threshold memory + tech-lead pilot
        verdict — active payment cascade means review prompt = poor UX."""
        from apps.conversations.models import Conversation

        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            consecutive_payment_failures=3,
            is_active=True,
            is_shadow=False,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["skipped_blocked"] == 1

    def test_payment_failures_below_threshold_allows_followup(
        self, tenant: Tenant, bot_user: BotUser, settings
    ) -> None:
        """1-2 payment failures (under threshold=3) still allows B11."""
        from apps.conversations.models import Conversation

        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            consecutive_payment_failures=2,
            is_active=True,
            is_shadow=False,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_called_once()

    def test_happy_path_confirmed_completed_opt_in_no_failures_sends(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """All 4 gates pass → B11 fires (smoke test for full flow)."""
        booking = _make_booking_for_followup(
            tenant=tenant,
            bot_user=bot_user,
            completed_at=NOW_UTC - timedelta(hours=1),
        )
        rem = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        _link_reminder_to_booking(rem, booking)
        # Default opt_out=False; no Conversation row = no payment failures.
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_called_once()
        assert result["sent"] == 1
        assert result["skipped_blocked"] == 0

    def test_null_fk_legacy_or_ayla_path_sends_when_other_gates_pass(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """NULL booking_request FK on a LEGACY YClients reminder.

        ``yc-fu`` is not an Ayla appointment UUID, so there is no mirror to
        consult and the opt_out + payment_failures gates are the only ones
        that apply. Behaviour unchanged by DRF-1144.
        """
        # _make_reminder без _link_reminder_to_booking = NULL FK.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_called_once()
        assert result["sent"] == 1

    @pytest.mark.parametrize("mirror_status", ["cancelled", "no_show"])
    def test_ayla_mirror_terminal_status_blocks_followup(
        self, tenant: Tenant, bot_user: BotUser, mirror_status: str
    ) -> None:
        """DRF-1144: blocker #2 was absent on the Ayla path.

        ``BookingRequest.status`` never moves for an Ayla booking, so «как
        прошёл визит?» went out for visits the mirror already knew were
        cancelled or a no-show.
        """
        import uuid as _uuid

        from apps.booking.models import RemoteBookingProxy

        appointment_id = _uuid.uuid4()
        visit_at = _msk(2026, 5, 15, 18, 0)
        RemoteBookingProxy.all_tenants.create(
            appointment_id=appointment_id,
            tenant=tenant,
            bot_user=bot_user,
            start_at=visit_at,
            end_at=visit_at + timedelta(hours=1),
            status=mirror_status,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=visit_at,
            yc_id=str(appointment_id),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["sent"] == 0
        assert result["skipped_blocked"] == 1

    def test_ayla_mirror_missing_blocks_followup(self, tenant: Tenant, bot_user: BotUser) -> None:
        """No mirror row behind an Ayla-path reminder → nothing to ask about."""
        import uuid as _uuid

        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
            yc_id=str(_uuid.uuid4()),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["sent"] == 0
        assert result["skipped_blocked"] == 1

    def test_ayla_mirror_confirmed_still_sends(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Regression guard: the pilot mirror usually stays ``confirmed`` after
        the visit because Ayla does not reliably emit ``booking.completed``.
        Requiring ``completed`` here would silence the follow-up entirely."""
        import uuid as _uuid

        from apps.booking.models import RemoteBookingProxy

        appointment_id = _uuid.uuid4()
        visit_at = _msk(2026, 5, 15, 18, 0)
        RemoteBookingProxy.all_tenants.create(
            appointment_id=appointment_id,
            tenant=tenant,
            bot_user=bot_user,
            start_at=visit_at,
            end_at=visit_at + timedelta(hours=1),
            status=RemoteBookingProxy.Status.CONFIRMED,
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=visit_at,
            yc_id=str(appointment_id),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_called_once()
        assert result["sent"] == 1

    def test_blocked_send_emits_audit_row(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Blocked send emits ``bookings.followup.blocked`` audit с reason.

        Driven by the consent gate rather than by opt-out (DRF-1301):
        opt-out no longer reaches this code path, having been filtered out
        of the selection, so using it here would assert on a row nothing
        can produce.
        """
        from apps.audit.models import AuditLog

        BotUser.all_tenants.filter(pk=bot_user.pk).update(consent_at=None)
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message"):
            send_post_visit_followups()
        rows = AuditLog.all_tenants.filter(
            action="bookings.followup.blocked",
            target_id=bot_user.pk,
        )
        assert rows.exists()
        first = rows.first()
        assert first is not None  # narrow для mypy
        assert first.payload["reason"] == "no_consent"

    def test_multiple_blockers_first_match_wins(self, tenant: Tenant, bot_user: BotUser) -> None:
        """When multiple blockers apply, the helper returns the first hit.

        Implementation order: consent gate → payment_failures →
        completed_at → status. Asserted against ``_should_send_b11``
        directly rather than through the task, because the strongest of
        these blockers (opt_out) is filtered before the task ever calls
        it — the ordering is a property of the helper, so the helper is
        what the test should hold.
        """
        from apps.booking.models import BookingRequest

        BotUser.all_tenants.filter(pk=bot_user.pk).update(consent_at=None)
        bot_user.refresh_from_db()
        booking = _make_booking_for_followup(
            tenant=tenant,
            bot_user=bot_user,
            status=BookingRequest.Status.CANCELLED,
        )
        rem = _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        _link_reminder_to_booking(rem, booking)
        # Consent is checked first → reason = no_consent, not booking_status_*.
        assert followups_mod._should_send_b11(rem, bot_user) == (False, "no_consent")

        # And opt_out outranks even that, inside the gate itself.
        BotUser.all_tenants.filter(pk=bot_user.pk).update(proactive_messages_opt_out=True)
        bot_user.refresh_from_db()
        assert followups_mod._should_send_b11(rem, bot_user) == (False, "opt_out")

    def test_backward_compat_existing_users_default_send(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Existing users default proactive_messages_opt_out=False → sends
        normally. Migration backfill safety lock."""
        # Verify field default behavior — no opt-out set explicitly.
        assert bot_user.proactive_messages_opt_out is False
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_called_once()

    def test_shadow_conversation_does_not_mask_payment_cascade(
        self, tenant: Tenant, bot_user: BotUser, settings
    ) -> None:
        """CR #874 B1: shadow conversations carry meaningless counters
        (observability artefacts). Active primary conversation с real
        failures must NOT be hidden by a later-inserted shadow row."""
        from apps.conversations.models import Conversation

        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        # Primary active conversation с real payment cascade.
        Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            consecutive_payment_failures=5,  # over threshold
            is_active=True,
            is_shadow=False,
            last_message_at=NOW_UTC - timedelta(hours=2),
        )
        # Later-inserted shadow row с zero failures — must be ignored.
        Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            consecutive_payment_failures=0,
            is_active=True,
            is_shadow=True,
            last_message_at=NOW_UTC - timedelta(minutes=10),
        )
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        # Cascade detected via primary conv, shadow ignored → block.
        mock_send.assert_not_called()
        assert result["skipped_blocked"] == 1

    def test_payment_cascade_in_other_tenant_does_not_block(
        self, tenant: Tenant, bot_user: BotUser, settings
    ) -> None:
        """CR #874 B2: BotUser bridges tenants per cross-tenant invisible
        relationship. Cascade в salon A must NOT block followup для salon
        B's visit."""
        from apps.conversations.models import Conversation

        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 3
        # Another tenant where the same BotUser had a payment cascade.
        other_tenant = Tenant.objects.create(slug="other-salon", name="Other")
        Conversation.all_tenants.create(
            tenant=other_tenant,
            bot_user=bot_user,
            consecutive_payment_failures=10,  # cascade в OTHER tenant
            is_active=True,
            is_shadow=False,
            last_message_at=NOW_UTC,
        )
        # Reminder for our tenant — should NOT see other tenant's cascade.
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        # No conversation в the reminder's tenant → no blocker → send.
        mock_send.assert_called_once()
        assert result["sent"] == 1


# ────────────────────────────────────────────────────────────────────
# DRF-1301 — the consent gate
# ────────────────────────────────────────────────────────────────────
class TestConsentGate:
    """Who may be written to first, and why not.

    The bug this suite exists for: the module docstring claimed BotUser
    had no consent field, so the task sent to everyone reachable. It has
    ``consent_at``, and on the pilot seven follow-ups had already gone to
    two people who never set it.

    Every case below builds a person who differs from the fixture in
    exactly one respect, so a failure names the condition that broke.
    """

    def _remind(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
        )

    def test_never_consented_is_not_written_to(self, tenant: Tenant, bot_user: BotUser) -> None:
        """consent_at IS NULL → blocked. The pilot's actual case."""
        BotUser.all_tenants.filter(pk=bot_user.pk).update(consent_at=None)
        self._remind(tenant, bot_user)
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["sent"] == 0
        assert result["skipped_blocked"] == 1
        assert [d.reason for d in followups_mod.plan_post_visit_followups()] == ["no_consent"]

    def test_withdrawn_consent_is_not_written_to(self, tenant: Tenant, bot_user: BotUser) -> None:
        """The case ``consent_at`` alone cannot see.

        ``withdraw()`` stamps ``withdrawn_at`` on the ConsentRecord and
        leaves ``BotUser.consent_at`` set, so a gate reading only the
        column would keep messaging someone who explicitly said stop.
        """
        ConsentRecord.all_tenants.filter(bot_user=bot_user).update(withdrawn_at=NOW_UTC)
        bot_user.refresh_from_db()
        assert bot_user.consent_at is not None, "the column stays set — that is the trap"

        self._remind(tenant, bot_user)
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["skipped_blocked"] == 1
        assert [d.reason for d in followups_mod.plan_post_visit_followups()] == [
            "consent_withdrawn"
        ]

    def test_consent_stamp_without_a_record_is_reported_as_unproven(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """A stamp with no proof behind it is not consent we can show.

        Distinct slug from ``consent_withdrawn`` on purpose: this is a
        provenance gap from grants predating #1074 (which made the stamp
        and the record atomic), and it is a different operator problem
        from somebody having said no.
        """
        ConsentRecord.all_tenants.filter(bot_user=bot_user).delete()
        self._remind(tenant, bot_user)
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_not_called()
        assert [d.reason for d in followups_mod.plan_post_visit_followups()] == ["consent_unproven"]

    def test_erased_user_is_not_written_to(self, tenant: Tenant, bot_user: BotUser) -> None:
        """soft_delete_user() does not clear chat_id, so they stay reachable."""
        BotUser.all_tenants.filter(pk=bot_user.pk).update(deleted_at=NOW_UTC)
        bot_user.refresh_from_db()
        assert (bot_user.chat_id or "").strip(), "still addressable — that is why this gate exists"

        self._remind(tenant, bot_user)
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_not_called()
        assert followups_mod.plan_post_visit_followups() == []

    def test_a_consenting_client_still_receives_the_nudge(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """The other half of the proof.

        Every assertion above is satisfied by a task that sends nothing to
        anyone, which is exactly the ambiguity a zero-recipient dry run
        carries. This one fails if the gate has been tightened into a
        wall, so "nobody was written to" can be read as protection rather
        than breakage.
        """
        self._remind(tenant, bot_user)
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_called_once()
        assert result["sent"] == 1

    def test_a_blocked_person_keeps_no_idempotency_key(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Blocking must not consume the day.

        If a blocked decision bumped the key, a consent granted an hour
        later would be silently ignored until tomorrow — by which time
        the visit is outside the window and the nudge never happens.
        """
        BotUser.all_tenants.filter(pk=bot_user.pk).update(consent_at=None)
        self._remind(tenant, bot_user)
        with patch("apps.bookings.followups.send_message"):
            send_post_visit_followups()
        bot_user.refresh_from_db()
        assert CONTEXT_KEY not in (bot_user.context or {})


# ────────────────────────────────────────────────────────────────────
# DRF-1301 — the two switches
# ────────────────────────────────────────────────────────────────────
class TestSwitches:
    """Both closed by default; a real message needs both opened, in order."""

    def test_production_defaults_are_both_closed(self) -> None:
        """Read the defaults from settings, not from the helpers' fallbacks.

        ``enabled()`` and ``dry_run()`` use ``getattr(settings, ..., X)``,
        so they would return the safe answer even if the settings module
        never defined the flags at all. That would be a config bug
        invisible to a test of the helpers, so assert on the settings.
        """
        from config.settings import base

        assert base.POST_VISIT_FOLLOWUP_ENABLED is False
        assert base.POST_VISIT_FOLLOWUP_DRY_RUN is True

    def test_disabled_sends_nothing_and_touches_nothing(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        settings.POST_VISIT_FOLLOWUP_ENABLED = False
        _make_reminder(tenant=tenant, bot_user=bot_user, visit_at=_msk(2026, 5, 15, 18, 0))
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result == {
            "sent": 0,
            "skipped_already_sent": 0,
            "skipped_no_chat_id": 0,
            "skipped_blocked": 0,
            "send_failed": 0,
            "would_send": 0,
            "dry_run": 1,
        }
        bot_user.refresh_from_db()
        assert CONTEXT_KEY not in (bot_user.context or {})

    def test_dry_run_plans_the_send_but_does_not_make_it(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """The distinction the ticket asks to be reported honestly.

        ``would_send == 1`` with ``sent == 0`` says "the gate let this
        person through and we chose not to deliver", which is a different
        statement from ``would_send == 0`` ("nobody qualified"). A dry run
        that cannot tell those apart is not evidence of anything.
        """
        settings.POST_VISIT_FOLLOWUP_DRY_RUN = True
        _make_reminder(tenant=tenant, bot_user=bot_user, visit_at=_msk(2026, 5, 15, 18, 0))
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["would_send"] == 1
        assert result["sent"] == 0
        assert result["dry_run"] == 1
        # A dry run must not consume the idempotency key either, or the
        # first real run would find the day already spent.
        bot_user.refresh_from_db()
        assert CONTEXT_KEY not in (bot_user.context or {})


# ────────────────────────────────────────────────────────────────────
# DRF-1301 — outbound safety on an unsolicited message
# ────────────────────────────────────────────────────────────────────
class TestOutboundSafety:
    def test_our_own_copy_passes_the_gate(self) -> None:
        """Guard the copy, not just the interpolation.

        A hit means silence, so a future edit to the nudge that trips one
        of the safety shapes would show up as a message that stopped
        arriving rather than as a failure. Sweep a range of master names
        rather than trusting one sample.
        """
        from apps.orchestrator.safety.outbound import evaluate_outbound

        for master in ("Лера", "мастеру", "Анна-Мария", "Ольга", "Мария", ""):
            reminder = BookingReminder(master_name=master)
            text = followups_mod._format_followup_text(reminder)
            assert evaluate_outbound(text).allowed, f"our own copy blocked for {master!r}"

    def test_a_contact_detail_in_the_master_name_blocks_the_send(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """The one part of this message we do not author.

        ``master_name`` is catalogue text mirrored from Ayla and edited by
        salon staff. A phone number in that field is exactly what
        DRF-1039 says these messages must never carry.
        """
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
            master_name="Лера +7 999 123 45 67",
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            result = send_post_visit_followups()
        mock_send.assert_not_called()
        assert result["skipped_blocked"] == 1
        assert [d.reason for d in followups_mod.plan_post_visit_followups()] == [
            "outbound_safety_contact"
        ]

    def test_a_blocked_message_is_dropped_not_replaced(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Never substitute «тут нужен человек» into an unsolicited message.

        In the conversational pipeline that replacement is right: somebody
        is waiting for an answer. Here the person asked nothing, so the
        line is a non-sequitur and would hand an administrator a
        conversation containing no question. Silence is the correct
        outcome, and the idempotency key stays unspent.
        """
        _make_reminder(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=_msk(2026, 5, 15, 18, 0),
            master_name="Лера +7 999 123 45 67",
        )
        with patch("apps.bookings.followups.send_message") as mock_send:
            send_post_visit_followups()
        mock_send.assert_not_called()
        bot_user.refresh_from_db()
        assert CONTEXT_KEY not in (bot_user.context or {})
