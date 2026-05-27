"""Periodic Celery task: nudge clients for feedback the day after a visit.

DRF-846 / Phase 1 / R3. Runs once daily at 19:00 МСК (16:00 UTC) via
Celery beat (see ``CELERY_BEAT_SCHEDULE`` in ``config/settings/base.py``).

### Why a separate module from ``tasks.py`` / ``escalation.py``

Same justification as R2: the dispatcher (R1), the escalator (R2), and
this follow-up nudge (R3) share the per-tenant fan-out idiom but their
*selection predicates*, *idempotency keys*, and *message content* are
disjoint. A reader scanning ``escalation.py`` should not have to mentally
distinguish "escalation branch" vs "follow-up branch". Sibling modules
with a uniform shape ─ the right amount of DRY for Phase 1.

### Selection

Picks BotUsers whose most recent BookingReminder satisfies:

* ``visit_at`` falls within **yesterday's Moscow-local day window**
  (``[yesterday 00:00 МСК, yesterday 24:00 МСК)``). The window is
  computed at task start from ``timezone.now()`` cast to Europe/Moscow
  so the boundaries follow the salon calendar, not UTC. A visit at
  23:59 МСК yesterday is "yesterday's visit" even though it's the same
  UTC day as today's first-hour rows.
* ``status != CANCELLED`` — a CANCELLED reminder means the booking
  was scrapped; the client did not actually visit, so we don't ask
  about a non-event.
* ``kind`` is unconstrained: a single visit has both a DAY_BEFORE and a
  TWO_HOURS reminder row, but we collapse duplicates to "one message
  per bot_user" via the per-row ``seen`` set in the loop.

### "Did the visit actually happen?"

We have no true client-showed-up signal in Phase 1 (YClients has a
``deleted`` flag but not a "no-show" event). The working assumption:
if ``visit_at`` was yesterday and the reminder isn't CANCELLED, the
client visited. A handful of no-show clients getting a "how was it?"
nudge is a mild false-positive; the alternative (silence after every
visit, missing every review opportunity) is the larger cost. Phase 2
may layer in YClients visit-completion polling to tighten this.

### Idempotency

Stored on ``BotUser.context["last_followup_sent_at"]`` as a Moscow-local
ISO date string (``YYYY-MM-DD``). Rationale:

* Date string, not datetime: the comparison granularity is "did we
  message this user already today?", which is a per-day boolean. Storing
  a full timestamp would force every read to parse + truncate, and any
  drift in the parse path would risk re-sending.
* Moscow-local date, not UTC date: a beat running at 19:00 МСК is firmly
  inside Moscow's "today", but in UTC that's 16:00 of the *same* UTC
  calendar day — so UTC vs МСК agree at beat time. The МСК choice is
  defensive: a follow-up retry at 23:30 МСК (which is 20:30 UTC) is
  still "today" by salon calendar, and we want the idempotency key to
  reflect that. Using UTC would make a 23:30 МСК retry think it's a
  different day from the original 19:00 МСК send.
* ``context`` defaults to ``dict``: any BotUser with a fresh / None /
  missing-key ``context`` is treated as "never followed-up before" and
  proceeds normally. We tolerate schema deviations (None instead of {})
  rather than crash on a malformed row — operationally important when
  data was backfilled from legacy mysite where ``context`` might be
  None instead of {}.

### Send-failure semantics

If the MAX outbound raises, the BotUser's ``context`` is **not** bumped
to today's date. The next daily run re-tries (at most one more attempt;
tomorrow's run won't pick this client because their visit will be
day-before-yesterday by then). This is at-most-twice semantics — a
deliberate compromise between "never send a follow-up nudge again on
transient error" (at-most-once) and "spam the client every 24h until
infrastructure recovers" (at-least-once unbounded). Two attempts is
manager-friendly: a duplicate follow-up nudge is mild, and we'd rather
have it than silently drop the review opportunity.

### Sentiment classification

Marked "Optional" in the DRF-846 spec; **deferred to a future ticket**
(R3.1 or follow-up). This task only sends the nudge; harvesting the
reply text into a sentiment label is a separate concern that requires:
either an LLM round-trip (adds latency + cost to a non-time-critical
batch beat) or a local classifier (adds a heavyweight dep that we don't
need yet). Phase 1 is "send the nudge"; sentiment ships later.

### Privacy / consent gate

The spec says "skip clients who have not consented (check the existing
field on BotUser; if such a flag exists, gate on it)". Inspection of
:class:`apps.identity.models.BotUser` (this commit) shows **no
explicit consent boolean** — consent is recorded via
:mod:`apps.consent.models` rows, not on the BotUser itself. Per the
spec rule "do not invent one", we send to all clients with a non-empty
``chat_id``. Empty chat_id is the only filter (no way to reach them).
A follow-up ticket can wire in a query against ``ConsentRecord`` if
ops decides to gate follow-ups on explicit consent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder
from apps.channels.max.outbound import MaxAPIError, send_message

logger = logging.getLogger(__name__)


# Tunables — module-scope so tests can monkeypatch.
BATCH_LIMIT = 500
MSK_TZ = ZoneInfo("Europe/Moscow")
CONTEXT_KEY = "last_followup_sent_at"


# ── B11 conservative blockers (P0 PRE_PILOT, founder sequence #3) ──────────
#
# Per Tau spec §4.1 + founder pilot_scope_discipline verdict 2026-05-26
# (pilot-scope CUT): block B11 if ANY of these conditions hold. Conservative
# semantics — false positive (lost review opportunity) acceptable; false
# negative (sent inappropriately) is trust-break.
#
# 4 pilot blockers implementable со существующей моделью:
#
#   1. ``booking.completed_at IS NULL`` — visit never registered. Without
#      proof of visit, не имеем right to ask «как прошёл».
#   2. ``booking.status IN {CANCELLED, RESCHEDULED}`` — booking is in a
#      terminal state where review prompt makes no sense. NOTE: CANCELLED
#      bundles «customer cancelled with refund» case (Tau §4.1) because
#      bot-platform mirror cannot today distinguish refund-cancellation
#      from regular cancellation — Phase 1 follow-up adds the distinction
#      via Ayla event integration.
#   3. ``BotUser.proactive_messages_opt_out IS True`` — customer
#      explicitly opted out of proactive messages.
#   4. ``Conversation.consecutive_payment_failures >= threshold`` — active
#      payment-failure cascade (per project_payment_failed_dm_threshold).
#      Reviewing a visit while customer is in payment dispute = poor UX.
#      Threshold от ``settings.PAYMENT_FAILED_HANDOFF_THRESHOLD`` (default 3).
#
# **Phase 1 follow-up (7 additional Tau §4.1 blocker states)** требует Ayla
# event integration: refund_pending / refund_completed / partial_refund /
# payment_disputed / chargeback_pending / chargeback / provider_cancelled /
# no_fault_* / active_dispute states. Mirror doesn't have them yet;
# don't add fields just для B11 without the upstream signal source.
# Use enum .value rather than literal strings — single source of truth
# matches PR #874 CR B3 follow-up. Frozen-at-PR-time naming convention
# retained для explicit drift-risk acknowledgement on future enum changes.
def _b11_blocked_statuses_frozen_at_pr_time() -> tuple[str, ...]:
    """Return the booking statuses that block B11 (lazily resolved через
    enum для drift-safety per CR #874 follow-up B3)."""
    from apps.booking.models import BookingRequest

    return (
        BookingRequest.Status.CANCELLED.value,
        BookingRequest.Status.RESCHEDULED.value,
    )


def _should_send_b11(
    reminder: BookingReminder,
    bot_user: Any,
) -> tuple[bool, str | None]:
    """Return ``(send, reason)`` для B11 followup gate.

    ``send=True`` → all 4 pilot blockers pass.
    ``send=False`` + reason slug → at least one blocker triggered;
    caller logs + audit emit с the reason для analytics.

    Reads BookingRequest via ``reminder.booking_request`` (FK). NULL
    FK = legacy / Ayla-path; can't check completed_at + booking.status
    blockers, but ``proactive_messages_opt_out`` + payment-failure
    gates still apply. Phase 1 Ayla event integration tightens this.
    """
    if bot_user.proactive_messages_opt_out:
        return (False, "opt_out")

    # Payment-failure check is tenant-scoped via reminder.tenant —
    # cascade в salon A must NOT block followup в salon B (CR #874 B2).
    payment_blocker = _payment_failures_blocker(bot_user, reminder.tenant)
    if payment_blocker:
        return (False, payment_blocker)

    booking_request = reminder.booking_request
    if booking_request is None:
        # NULL FK — legacy row OR Ayla-path. opt_out + payment_failures
        # gates already cleared above; send. Phase 1 closes this gap.
        return (True, None)

    if booking_request.completed_at is None:
        return (False, "completed_at_null")

    if booking_request.status in _b11_blocked_statuses_frozen_at_pr_time():
        return (False, f"booking_status_{booking_request.status}")

    return (True, None)


def _payment_failures_blocker(bot_user: Any, tenant: Any) -> str | None:
    """Check whether the bot_user has an active payment-failure cascade
    в the **specific tenant** the reminder belongs to.

    Per CR #874 findings B1 + B2:

      * **B1 (stale/shadow Conversation)**: filter к
        ``is_active=True, is_shadow=False, deleted_at__isnull=True``
        чтобы исключить shadow-observability rows (counter meaningless)
        и soft-deleted closed conversations (counter stale).
      * **B2 (cross-tenant aggregation)**: scope query к ``tenant=...``
        of the current reminder. BotUser CAN bridge multiple tenants
        per ``project_cross_tenant_invisible_relationship``; payment
        cascade в salon A must not be checked against B11 for salon B.

    Orders by ``-last_message_at`` (recency) — primary active conversation
    is the one most recently interacted with.

    Returns reason slug когда blocked, или None when threshold not
    breached / no eligible Conversation row found.
    """
    threshold = int(getattr(settings, "PAYMENT_FAILED_HANDOFF_THRESHOLD", 3))
    # Local import to avoid module-level cycle.
    from apps.conversations.models import Conversation

    conv = (
        Conversation.all_tenants.filter(
            bot_user=bot_user,
            tenant=tenant,
            is_active=True,
            is_shadow=False,
            deleted_at__isnull=True,
        )
        .order_by("-last_message_at", "-id")
        .only("consecutive_payment_failures")
        .first()
    )
    if conv is None:
        return None
    if conv.consecutive_payment_failures >= threshold:
        return f"payment_failures_threshold_{threshold}"
    return None


def _moscow_day_window(now_utc: datetime) -> tuple[datetime, datetime, date]:
    """Return (yesterday_start_utc, yesterday_end_utc, today_msk_date).

    The salon calendar is the source of truth: "yesterday" means the
    full Moscow-local calendar day preceding the calendar day in which
    the task runs. We compute the window in МСК then cast back to UTC
    for the DB filter (``visit_at`` is stored UTC-aware).

    Returns the today MSK date alongside the window so the caller can
    use it as the idempotency-key date — guaranteed consistent with
    the window math (no risk of midnight drift between two ``now()``
    calls inside the same task invocation).
    """
    now_msk = now_utc.astimezone(MSK_TZ)
    today_msk = now_msk.date()
    yesterday_msk = today_msk - timedelta(days=1)
    # Local midnight at the start of yesterday, MSK.
    yest_start_msk = datetime.combine(yesterday_msk, time.min, tzinfo=MSK_TZ)
    # End-exclusive: midnight at the start of today, MSK.
    yest_end_msk = datetime.combine(today_msk, time.min, tzinfo=MSK_TZ)
    # Cast to UTC for the DB filter. Use stdlib ``datetime.timezone.utc``
    # rather than ``django.utils.timezone.utc`` — the latter is
    # deprecated in Django 5+ (stubs already drop the attr).
    return (
        yest_start_msk.astimezone(dt_timezone.utc),
        yest_end_msk.astimezone(dt_timezone.utc),
        today_msk,
    )


def _format_followup_text(reminder: BookingReminder) -> str:
    """Render the follow-up body.

    Russian, warm, low-pressure — matches the prevailing brand voice
    in :mod:`apps.bookings.tasks` (the T-24h / T-2h reminder copy is
    the canonical reference). Single-question prompt, no buttons (a
    plain reply lands in the inbound channel and the conversation
    handler picks it up like any other free-text message).
    """
    master = reminder.master_name or "мастеру"
    return (
        f"Привет! Как прошёл вчерашний визит к {master}? "
        "Будем рады услышать впечатления — это поможет нам стать лучше."
    )


def _already_followed_up_today(context: dict[str, Any] | None, today_msk: date) -> bool:
    """Return True if the BotUser has already been nudged today.

    Tolerant: a ``None`` context, a missing key, or a non-string value
    all count as "never sent" (proceed normally). A malformed date
    string (anything that doesn't parse) is treated the same — we'd
    rather re-send than silently swallow a once-in-a-blue-moon
    corrupted JSON value.

    Idempotency comparison uses ISO date string equality. We don't
    parse and compare via ``date.fromisoformat`` because string
    equality is faster and the writer-side guarantees the format.
    """
    if not context:
        return False
    raw = context.get(CONTEXT_KEY)
    if not isinstance(raw, str) or not raw:
        return False
    return raw == today_msk.isoformat()


def _eligible_reminders(window_start: datetime, window_end: datetime) -> list[BookingReminder]:
    """Return reminders whose visit_at falls in yesterday's MSK window.

    Cross-tenant scan via ``all_tenants`` — this beat is system-level
    and runs across every tenant. The per-row ``select_related`` keeps
    bot_user + tenant joins in one query (we read both inside the loop).

    Ordering by ``visit_at`` (earliest first) is stable — a single visit
    has two reminders (DAY_BEFORE + TWO_HOURS) and we want the dedup
    pass below to pick whichever the DB returns first, deterministically.
    Both rows for the same visit share ``visit_at`` exactly (the factory
    derives both from the same source datetime) so the order between
    them is by secondary key (PK) which is a stable UUID — good enough.
    """
    return list(
        BookingReminder.all_tenants.filter(
            visit_at__gte=window_start,
            visit_at__lt=window_end,
        )
        .exclude(status=BookingReminder.Status.CANCELLED)
        .select_related("tenant", "bot_user", "booking_request")
        .order_by("visit_at", "pk")[:BATCH_LIMIT]
    )


@shared_task(name="bookings.send_post_visit_followups")
def send_post_visit_followups() -> dict[str, int]:
    """Send one "how was it?" nudge to every client whose visit was yesterday.

    Per-row flow:

    1. Compute yesterday's MSK day window once at task start.
    2. Fetch reminders whose ``visit_at`` falls in the window and whose
       status is not CANCELLED. Up to :data:`BATCH_LIMIT` rows.
    3. Deduplicate by ``bot_user_id`` — a visit has two reminders, but
       a follow-up is per-client-per-visit, not per-reminder.
    4. For each unique client:
       a. Skip if ``chat_id`` is empty (no way to reach them) — WARN.
       b. Skip if ``context[last_followup_sent_at]`` equals today's
          MSK date (already sent).
       c. Send via MAX outbound. On exception: log, don't bump the
          idempotency key, continue.
       d. On success: update ``context[last_followup_sent_at]`` to
          today's MSK ISO date, write audit row.

    Returns ``{"sent": int, "skipped_already_sent": int,
    "skipped_no_chat_id": int, "skipped_blocked": int, "send_failed": int}``
    for telemetry / test visibility.

    ``skipped_blocked`` counts B11 conservative blocker hits — opt_out,
    completed_at NULL, terminal booking status, payment-failure threshold
    (per ``_should_send_b11``).
    """
    now = timezone.now()
    window_start, window_end, today_msk = _moscow_day_window(now)
    today_iso = today_msk.isoformat()

    sent = 0
    skipped_already_sent = 0
    skipped_no_chat_id = 0
    skipped_blocked = 0
    send_failed = 0

    seen_bot_user_ids: set[Any] = set()
    for reminder in _eligible_reminders(window_start, window_end):
        bu = reminder.bot_user
        if bu is None:
            # Reminder rows have a non-null bot_user FK constraint
            # (CASCADE in the model), so this branch is defensive — a
            # row with a stale .bot_user attr (e.g. select_related cache
            # miss after a concurrent GDPR purge) shouldn't crash the
            # beat.
            continue
        if bu.pk in seen_bot_user_ids:
            # Duplicate visit reminder (DAY_BEFORE + TWO_HOURS for the
            # same visit) — already processed above.
            continue
        seen_bot_user_ids.add(bu.pk)

        chat_id = (bu.chat_id or "").strip()
        if not chat_id:
            logger.warning(
                "bookings.followup.no_chat_id bot_user=%s tenant=%s",
                bu.pk,
                reminder.tenant.slug,
            )
            skipped_no_chat_id += 1
            continue

        if _already_followed_up_today(bu.context, today_msk):
            logger.info(
                "bookings.followup.already_sent bot_user=%s date=%s",
                bu.pk,
                today_iso,
            )
            skipped_already_sent += 1
            continue

        # B11 conservative blockers gate (P0 PRE_PILOT). 4 pilot blockers —
        # if any triggers, log + audit + skip the send. Phase 1 extends к
        # full Tau §4.1 list when Ayla event integration ships.
        should_send, block_reason = _should_send_b11(reminder, bu)
        if not should_send:
            logger.info(
                "bookings.followup.blocked bot_user=%s reason=%s",
                bu.pk,
                block_reason,
            )
            # Best-effort audit для post-pilot analytics («which blocker
            # fires most» / «opt-out adoption rate»). Не failing the
            # batch loop if audit raises.
            try:
                write_audit(
                    action="bookings.followup.blocked",
                    target="BotUser",
                    target_id=bu.pk,
                    payload={
                        "reminder_id": str(reminder.pk),
                        "reason": block_reason,
                        "booking_request_id": (
                            str(reminder.booking_request_id)
                            if reminder.booking_request_id
                            else None
                        ),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "bookings.followup.block_audit_failed bot_user=%s reason=%s",
                    bu.pk,
                    block_reason,
                )
            skipped_blocked += 1
            continue

        text = _format_followup_text(reminder)
        try:
            send_message(chat_id=chat_id, text=text, attachments=None)
        except MaxAPIError as exc:
            logger.warning(
                "bookings.followup.send_failed bot_user=%s status=%s err=%s",
                bu.pk,
                exc.status_code,
                exc.body[:200] if exc.body else "",
            )
            write_audit(
                action="bookings.followup.send_failed",
                target="BotUser",
                target_id=bu.pk,
                payload={
                    "reminder_id": str(reminder.pk),
                    "status_code": exc.status_code,
                },
            )
            send_failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 — defensive, see escalation.py
            logger.exception("bookings.followup.send_unexpected bot_user=%s", bu.pk)
            write_audit(
                action="bookings.followup.send_failed",
                target="BotUser",
                target_id=bu.pk,
                payload={
                    "reminder_id": str(reminder.pk),
                    "exception_type": type(exc).__name__,
                },
            )
            send_failed += 1
            continue

        # Success — bump the idempotency key.
        #
        # We re-load .context defensively rather than relying on the
        # cached attr. Two reasons:
        # 1. The fetch happened at task start; another process may have
        #    touched .context in the interim.
        # 2. .context defaults to ``{}`` per the model but legacy data
        #    may have None — we normalise on write so future reads are
        #    consistent.
        current_context = dict(bu.context) if isinstance(bu.context, dict) else {}
        current_context[CONTEXT_KEY] = today_iso
        # Use ``all_tenants`` manager: this beat is cross-tenant and the
        # default TenantScopedManager would refuse the update outside a
        # tenant context.
        type(bu).all_tenants.filter(pk=bu.pk).update(context=current_context)

        write_audit(
            action="bookings.followup.sent",
            target="BotUser",
            target_id=bu.pk,
            payload={
                "reminder_id": str(reminder.pk),
                "visit_at": reminder.visit_at.isoformat(),
                "date": today_iso,
            },
        )
        sent += 1

    if sent or skipped_already_sent or skipped_no_chat_id or skipped_blocked or send_failed:
        logger.info(
            "bookings.followup.summary sent=%d skipped_already_sent=%d "
            "skipped_no_chat_id=%d skipped_blocked=%d send_failed=%d",
            sent,
            skipped_already_sent,
            skipped_no_chat_id,
            skipped_blocked,
            send_failed,
        )
    return {
        "sent": sent,
        "skipped_already_sent": skipped_already_sent,
        "skipped_no_chat_id": skipped_no_chat_id,
        "skipped_blocked": skipped_blocked,
        "send_failed": send_failed,
    }
