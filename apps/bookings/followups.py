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

### Privacy / consent gate (DRF-1301)

**This section used to say there was no consent gate and none was
possible. Both halves were wrong, and the paragraph outlived the code
by two months.** It claimed :class:`apps.identity.models.BotUser` has
"no explicit consent boolean", so the task sent to everyone with a
non-empty ``chat_id``. ``BotUser.consent_at`` — the 152-ФЗ welcome
consent, stamped when the person taps «Да, продолжим» — has been on
that model the whole time, three fields above the opt-out flag this
module already reads. PR #874 later added the
``proactive_messages_opt_out`` blocker without touching the paragraph,
so an auditor reading top-down concluded the task had no gate at all
while an auditor reading :func:`_should_send_b11` concluded it had one.
Both were reading honestly. Only one of them was reading the code.

What is gated now, in :func:`_consent_blocker`, and why each is here:

* ``proactive_messages_opt_out`` — the person's global "do not write to
  me first". Checked first and unconditionally, per the ordering
  argument in :mod:`apps.nutrition_proactive.selection`: a veto
  evaluated late is a veto a future edit can skip.
* ``consent_at IS NULL`` — never consented under 152-ФЗ. This is the
  hole DRF-1301 was filed for. Measured against the pilot database on
  2026-08-23: **seven follow-ups had already gone to two people, and
  neither of them had ``consent_at`` set.** Not a hypothetical.
* an active ``ConsentRecord`` for ``PERSONAL_DATA``. ``consent_at`` is a
  denormalised stamp and :func:`apps.consent.services.withdraw` never
  clears it — it stamps ``withdrawn_at`` on the record and leaves the
  BotUser column alone. Gating on ``consent_at`` alone would therefore
  keep messaging somebody who explicitly withdrew: the same failure
  this ticket is about, one step further along.
* ``deleted_at`` — a GDPR erasure request is the strongest withdrawal
  there is. ``soft_delete_user()`` scrubs display_name / avatar_url /
  client_name / phone / context but **not** ``chat_id``, so an erased
  person stays reachable and, before this commit, stayed a recipient.

Deliberately NOT gated on ``ConsentType.MARKETING``. A «как прошёл
визит?» nudge is arguably marketing, but nothing in this codebase
collects that consent, so gating on it would silence the feature
permanently while looking like it worked. That is an owner decision,
not one to smuggle in under a bug fix — raised as an open question in
``docs/REPORT_DRF1301.md``.

### Two switches, both closed (DRF-1301)

Copied from the DRF-1285 precedent rather than invented:

* ``POST_VISIT_FOLLOWUP_ENABLED`` (default ``False``) — the task returns
  immediately.
* ``POST_VISIT_FOLLOWUP_DRY_RUN`` (default ``True``) — full selection,
  full gate evaluation, logs exactly whom it would have written to and
  why, sends nothing.

Note what this changes. The nutrition tasks shipped dark, so their
switches cost nothing. **This beat was live and sending.** Turning it
off by default stops a running feature on purpose: it was writing to
people who never consented, and the honest default for a task in that
state is off, with the operator re-enabling after reading a dry run
against the real recipient list. ``manage.py post_visit_followup_dryrun``
prints exactly that list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder
from apps.booking.reminder_lookup import ayla_appointment_id_of
from apps.channels.max.outbound import MaxAPIError, send_message

logger = logging.getLogger(__name__)


# Tunables — module-scope so tests can monkeypatch.
BATCH_LIMIT = 500
MSK_TZ = ZoneInfo("Europe/Moscow")
CONTEXT_KEY = "last_followup_sent_at"


def enabled() -> bool:
    """False until an operator opens the master switch (DRF-1301)."""
    return bool(getattr(settings, "POST_VISIT_FOLLOWUP_ENABLED", False))


def dry_run() -> bool:
    """True unless an operator has explicitly turned the safety off."""
    return bool(getattr(settings, "POST_VISIT_FOLLOWUP_DRY_RUN", True))


def vet_outbound(text: str) -> tuple[str, str | None]:
    """Run the composed nudge past the outbound safety check (DRF-1301).

    Returns ``(text, None)`` when it may go out and ``("", reason)`` when
    it may not.

    This module writes its own copy, so at first glance there is nothing
    for :func:`~apps.orchestrator.safety.outbound.evaluate_outbound` to
    catch. There is exactly one thing: the nudge interpolates
    ``reminder.master_name``, which is not ours. It is catalogue text
    mirrored from Ayla, edited by salon staff, and a master name carrying
    a phone number or an email trips the ``contact`` shape — which is the
    pattern doing its job, not a false positive, because a contact detail
    is the one thing DRF-1039 says these messages must never carry.

    Applied at PLAN time, not at send time, so a dry run reports a
    blocked message the same way it reports every other non-send and an
    operator sees it before anything is enabled.

    One deliberate difference from the conversational pipeline, taken
    from DRF-1285 and re-argued rather than copied: a blocked reply there
    is REPLACED with ``REPLACEMENT_TEXT`` («тут нужен человек…»), which
    is right for an answer somebody is waiting for. Here the person asked
    nothing, so «тут нужен человек» is a non-sequitur that would puzzle
    them and hand an administrator a conversation with no question in it.
    A hit means **send nothing at all**.

    The idempotency key is not bumped either, and the reason differs from
    the nutrition one. There, tomorrow's report is different text and
    deserves a fresh evaluation. Here the text is stable, so not bumping
    buys only a retry inside the same day — the same at-most-twice
    semantics this module already applies to a failed send. That is the
    point: a blocked message and a failed one leave identical state, so
    no reader has to remember which branch bumps what.
    """
    from apps.orchestrator.safety.outbound import evaluate_outbound

    verdict = evaluate_outbound(text)
    if verdict.blocked:
        return "", "outbound_safety_" + ("_".join(verdict.categories) or "hit")
    return text, None


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


#: Mirror statuses that block B11 on the Ayla path (DRF-1144). The analogue of
#: ``_b11_blocked_statuses_frozen_at_pr_time`` for bookings whose lifecycle
#: lands on ``RemoteBookingProxy`` instead of ``BookingRequest``. ``completed``
#: is NOT here — it is the state B11 exists for.
_B11_BLOCKED_MIRROR_STATUSES: tuple[str, ...] = ("cancelled", "no_show")


def _should_send_b11_ayla(reminder: BookingReminder) -> tuple[bool, str | None]:
    """B11 blocker #2 for the Ayla path — read the mirror, not the FK.

    ``BookingRequest.status`` is the legacy path's terminal-state signal and no
    inbound event ever moves it, so on the Ayla path blocker #2 was simply
    absent: «как прошёл визит?» went out for bookings the mirror already knew
    were cancelled or a no-show. Same family as DRF-1144 — a lifecycle
    transition that reached the mirror and stopped there.

    Deliberately narrower than the reminder-dispatch classifier: this blocks
    only on the terminal-negative states and on a missing mirror row. It does
    NOT require ``completed`` — the pilot mirror frequently stays ``confirmed``
    after the visit because Ayla does not always emit ``booking.completed``,
    and demanding it would silence the follow-up entirely. Widening this to a
    positive «visit actually happened» proof needs the completion signal to be
    reliable first.
    """
    appointment_id = ayla_appointment_id_of(reminder)
    if appointment_id is None:
        # Legacy YClients row — no mirror to consult, behaviour unchanged.
        return (True, None)

    from apps.booking.models import RemoteBookingProxy

    status = (
        RemoteBookingProxy.all_tenants.filter(
            appointment_id=appointment_id,
            tenant_id=reminder.tenant_id,
        )
        .values_list("status", flat=True)
        .first()
    )
    if status is None:
        return (False, "ayla_mirror_missing")
    if status in _B11_BLOCKED_MIRROR_STATUSES:
        return (False, f"ayla_mirror_status_{status}")
    return (True, None)


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
    blockers, but the consent gate + payment-failure gate still apply.
    Phase 1 Ayla event integration tightens this.
    """
    consent_blocker = _consent_blocker(bot_user)
    if consent_blocker:
        return (False, consent_blocker)

    # Payment-failure check is tenant-scoped via reminder.tenant —
    # cascade в salon A must NOT block followup в salon B (CR #874 B2).
    payment_blocker = _payment_failures_blocker(bot_user, reminder.tenant)
    if payment_blocker:
        return (False, payment_blocker)

    booking_request = reminder.booking_request
    if booking_request is None:
        # NULL FK — legacy YClients row OR Ayla-path. The Ayla path has its own
        # source of truth; consult it (DRF-1144). Legacy rows keep the old
        # «gates cleared, send» behaviour because there is nothing to consult.
        return _should_send_b11_ayla(reminder)

    if booking_request.completed_at is None:
        return (False, "completed_at_null")

    if booking_request.status in _b11_blocked_statuses_frozen_at_pr_time():
        return (False, f"booking_status_{booking_request.status}")

    return (True, None)


def _consent_blocker(bot_user: Any) -> str | None:
    """May we write to this person unprompted at all? (DRF-1301)

    Returns a reason slug when we may not, ``None`` when we may. Four
    conditions, in the order a person would state them, each a separate
    slug so a dry run tells the operator *which* one fired rather than a
    single undifferentiated "blocked".

    Order matters: ``proactive_messages_opt_out`` is evaluated first and
    unconditionally, because it is the one veto whose failure is a trust
    break rather than a missed message. Nothing below it can re-enable a
    message for somebody who set it.

    The consent-record read uses
    :func:`~apps.consent.services.has_global_consent`, not
    :func:`~apps.consent.services.has_consent`. This beat is a
    cross-tenant system task with no tenant in scope, and the
    tenant-scoped reader raises there. That is not a workaround: the
    pilot's client bot runs the global path, so the global reader is the
    one that answers for the people this task actually writes to. It
    anchors on ``bot_user``, whose FK already pins exactly one tenant, so
    no cross-tenant row is reachable.

    Distinguishing "never proved" from "withdrawn" costs a second query
    on the failing branch only — the common case is one ``EXISTS``. The
    distinction is worth it: they are different operator problems. A
    ``consent_unproven`` row is a data-provenance gap left by grants
    predating #1074, which stamped ``consent_at`` and the record
    atomically. A ``consent_withdrawn`` row is somebody who said no.
    """
    if getattr(bot_user, "proactive_messages_opt_out", False):
        return "opt_out"

    if getattr(bot_user, "deleted_at", None) is not None:
        return "deleted"

    if getattr(bot_user, "consent_at", None) is None:
        return "no_consent"

    # Local imports: apps.consent imports identity models, and this module
    # is imported from apps.bookings.tasks at module scope.
    from apps.consent.models import ConsentRecord
    from apps.consent.services import has_global_consent

    personal_data = ConsentRecord.ConsentType.PERSONAL_DATA.value
    if has_global_consent(bot_user, personal_data):
        return None

    ever = ConsentRecord.all_tenants.filter(
        bot_user=bot_user,
        consent_type=personal_data,
    ).exists()
    return "consent_withdrawn" if ever else "consent_unproven"


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

    **Two vetoes are applied here as well as per-row in**
    :func:`_consent_blocker` **(DRF-1301):** ``proactive_messages_opt_out``
    and ``deleted_at``. Belt and braces, the same shape as
    :func:`apps.nutrition_proactive.selection.base_queryset`. The ticket
    asked that a person who opted out "never enters the selection", and a
    filter that the batch limit is applied *after* is the only way to
    mean that literally: with a post-fetch check alone, a large enough
    run of opted-out rows could crowd a consenting person out of the
    batch, and the only symptom would be a message that never arrived.

    **Consent deliberately stays a per-row check and is NOT filtered
    here**, which is the one place this diverges from the nutrition
    precedent. Filtering it would make the people it excludes invisible:
    they would vanish before the counters, the audit rows and the dry-run
    listing ever saw them. "How many people did we not write to because
    they never consented?" is precisely the number an operator needs in
    order to decide whether to re-enable this beat, so it has to survive
    into the output. Opt-out and erasure need no such visibility — both
    are plain columns anyone can ``count()`` at any time, and neither is
    a number that changes what the operator does next.
    """
    return list(
        BookingReminder.all_tenants.filter(
            visit_at__gte=window_start,
            visit_at__lt=window_end,
            bot_user__proactive_messages_opt_out=False,
            bot_user__deleted_at__isnull=True,
        )
        .exclude(status=BookingReminder.Status.CANCELLED)
        .select_related("tenant", "bot_user", "booking_request")
        .order_by("visit_at", "pk")[:BATCH_LIMIT]
    )


@dataclass
class Decision:
    """One evaluated recipient. ``send=False`` always carries a ``reason``.

    The unit of a decision is the *person*, not the reminder: a visit has
    a DAY_BEFORE and a TWO_HOURS row and the nudge is per client per
    visit. ``reminder_id`` records which of the two the decision was made
    against, so an audit row can be traced back to a specific row.
    """

    bot_user_id: Any
    reminder_id: Any
    tenant_slug: str
    #: The visit this nudge is about. Carried so the sent-audit payload
    #: keeps the shape it had before the plan/execute split — the rows
    #: already on the pilot have it, and it is what ties an audit row to
    #: a specific appointment when somebody reconstructs who was written
    #: to and about what.
    visit_at: datetime | None
    send: bool
    reason: str
    chat_id: str = ""
    text: str = ""

    def as_log(self) -> dict[str, Any]:
        """PII-free projection for logs and the dry-run listing.

        ``text`` and ``chat_id`` are excluded on purpose. The rendered
        nudge carries the master's name and the chat id is the address
        itself; neither belongs in a log line an operator will paste into
        a ticket.
        """
        return {
            "bot_user_id": str(self.bot_user_id),
            "reminder_id": str(self.reminder_id),
            "tenant": self.tenant_slug,
            "send": self.send,
            "reason": self.reason,
        }


def plan_post_visit_followups(*, now_utc: datetime | None = None) -> list[Decision]:
    """Evaluate every candidate for a post-visit nudge, send nothing.

    The beat and ``manage.py post_visit_followup_dryrun`` both call this,
    so "who would we write to?" can never be answered differently from
    "who did we write to?" — the failure mode DRF-1285 called out and the
    reason this is a shared planner rather than a second implementation.

    Pure with respect to the outside world: reads the database, composes
    text, runs the safety check, and returns. No sends, no writes.
    """
    now = now_utc or timezone.now()
    window_start, window_end, today_msk = _moscow_day_window(now)

    decisions: list[Decision] = []
    seen_bot_user_ids: set[Any] = set()

    for reminder in _eligible_reminders(window_start, window_end):
        bu = reminder.bot_user
        if bu is None:
            # Reminder rows have a non-null bot_user FK constraint
            # (CASCADE in the model), so this branch is defensive — a row
            # with a stale .bot_user attr (e.g. select_related cache miss
            # after a concurrent GDPR purge) shouldn't crash the beat.
            continue
        if bu.pk in seen_bot_user_ids:
            # Duplicate visit reminder (DAY_BEFORE + TWO_HOURS for the
            # same visit) — already decided above.
            continue
        seen_bot_user_ids.add(bu.pk)

        tenant_slug = reminder.tenant.slug if reminder.tenant else ""

        def decide(reason: str, *, send: bool = False, **kwargs: Any) -> Decision:
            """Bind the row-invariant fields so no branch can forget one."""
            return Decision(
                bot_user_id=bu.pk,
                reminder_id=reminder.pk,
                tenant_slug=tenant_slug,
                visit_at=reminder.visit_at,
                send=send,
                reason=reason,
                **kwargs,
            )

        chat_id = (bu.chat_id or "").strip()
        if not chat_id:
            logger.warning(
                "bookings.followup.no_chat_id bot_user=%s tenant=%s",
                bu.pk,
                tenant_slug,
            )
            decisions.append(decide("no_chat_id"))
            continue

        if _already_followed_up_today(bu.context, today_msk):
            logger.info(
                "bookings.followup.already_sent bot_user=%s date=%s",
                bu.pk,
                today_msk.isoformat(),
            )
            decisions.append(decide("already_sent"))
            continue

        # The consent + B11 conservative blockers. If any triggers, the
        # decision carries the reason and the caller audits it.
        should_send, block_reason = _should_send_b11(reminder, bu)
        if not should_send:
            decisions.append(decide(block_reason or "blocked"))
            continue

        text, blocked_by = vet_outbound(_format_followup_text(reminder))
        if blocked_by:
            # Not sent, and the idempotency key is NOT bumped — see the
            # reasoning in :func:`vet_outbound`.
            decisions.append(decide(blocked_by))
            continue

        decisions.append(decide("due", send=True, chat_id=chat_id, text=text))

    return decisions


#: Decision reasons that are consent/blocker hits rather than routine
#: skips. Kept as a set so the counter and the audit branch cannot drift
#: apart — ``skipped_no_chat_id`` and ``skipped_already_sent`` have their
#: own counters and are not blockers.
_ROUTINE_SKIPS = frozenset({"no_chat_id", "already_sent"})


@shared_task(name="bookings.send_post_visit_followups")
def send_post_visit_followups() -> dict[str, int]:
    """Send one «как прошёл визит?» nudge per client whose visit was yesterday.

    Flow:

    1. Return immediately unless ``POST_VISIT_FOLLOWUP_ENABLED``.
    2. :func:`plan_post_visit_followups` evaluates every candidate.
    3. Blocked decisions are audited (best-effort) for analytics.
    4. Under ``POST_VISIT_FOLLOWUP_DRY_RUN`` (the default) the intended
       recipients are logged and nothing is sent.
    5. Otherwise each due decision is delivered, the idempotency key
       bumped, and an audit row written.

    Returns ``{"sent", "skipped_already_sent", "skipped_no_chat_id",
    "skipped_blocked", "send_failed", "would_send", "dry_run"}``.

    ``skipped_blocked`` counts consent-gate and B11 blocker hits —
    ``opt_out``, ``no_consent``, ``consent_withdrawn``,
    ``consent_unproven``, ``deleted``, ``completed_at_null``, terminal
    booking status, payment-failure threshold, and an outbound-safety
    hit (per :func:`_should_send_b11` and :func:`vet_outbound`).
    """
    if not enabled():
        logger.info("bookings.followup.disabled")
        return {
            "sent": 0,
            "skipped_already_sent": 0,
            "skipped_no_chat_id": 0,
            "skipped_blocked": 0,
            "send_failed": 0,
            "would_send": 0,
            "dry_run": 1,
        }

    now = timezone.now()
    _, _, today_msk = _moscow_day_window(now)
    today_iso = today_msk.isoformat()

    decisions = plan_post_visit_followups(now_utc=now)
    is_dry = dry_run()

    sent = 0
    send_failed = 0
    skipped_already_sent = sum(1 for d in decisions if d.reason == "already_sent")
    skipped_no_chat_id = sum(1 for d in decisions if d.reason == "no_chat_id")
    skipped_blocked = sum(1 for d in decisions if not d.send and d.reason not in _ROUTINE_SKIPS)

    for decision in decisions:
        if decision.send or decision.reason in _ROUTINE_SKIPS:
            continue
        _audit_blocked(decision)

    to_send = [d for d in decisions if d.send]
    for decision in to_send:
        if is_dry:
            logger.info(
                "bookings.followup.dry_run would_send=%s",
                decision.as_log(),
            )
            continue
        try:
            send_message(chat_id=decision.chat_id, text=decision.text, attachments=None)
        except MaxAPIError as exc:
            logger.warning(
                "bookings.followup.send_failed bot_user=%s status=%s err=%s",
                decision.bot_user_id,
                exc.status_code,
                exc.body[:200] if exc.body else "",
            )
            write_audit(
                action="bookings.followup.send_failed",
                target="BotUser",
                target_id=decision.bot_user_id,
                payload={
                    "reminder_id": str(decision.reminder_id),
                    "status_code": exc.status_code,
                },
            )
            send_failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 — defensive, see escalation.py
            logger.exception("bookings.followup.send_unexpected bot_user=%s", decision.bot_user_id)
            write_audit(
                action="bookings.followup.send_failed",
                target="BotUser",
                target_id=decision.bot_user_id,
                payload={
                    "reminder_id": str(decision.reminder_id),
                    "exception_type": type(exc).__name__,
                },
            )
            send_failed += 1
            continue

        _bump_idempotency_key(decision.bot_user_id, today_iso)
        write_audit(
            action="bookings.followup.sent",
            target="BotUser",
            target_id=decision.bot_user_id,
            payload={
                "reminder_id": str(decision.reminder_id),
                "visit_at": (
                    decision.visit_at.isoformat() if decision.visit_at is not None else None
                ),
                "date": today_iso,
            },
        )
        sent += 1

    if decisions:
        logger.info(
            "bookings.followup.summary planned=%d would_send=%d sent=%d "
            "skipped_already_sent=%d skipped_no_chat_id=%d skipped_blocked=%d "
            "send_failed=%d dry_run=%s",
            len(decisions),
            len(to_send),
            sent,
            skipped_already_sent,
            skipped_no_chat_id,
            skipped_blocked,
            send_failed,
            is_dry,
        )
    return {
        "sent": sent,
        "skipped_already_sent": skipped_already_sent,
        "skipped_no_chat_id": skipped_no_chat_id,
        "skipped_blocked": skipped_blocked,
        "send_failed": send_failed,
        "would_send": len(to_send),
        "dry_run": int(is_dry),
    }


def _audit_blocked(decision: Decision) -> None:
    """Best-effort audit of a blocked decision.

    Post-pilot analytics wants «which blocker fires most» and «opt-out
    adoption rate». A failure here must not stop the batch.
    """
    logger.info(
        "bookings.followup.blocked bot_user=%s reason=%s",
        decision.bot_user_id,
        decision.reason,
    )
    try:
        write_audit(
            action="bookings.followup.blocked",
            target="BotUser",
            target_id=decision.bot_user_id,
            payload={
                "reminder_id": str(decision.reminder_id),
                "reason": decision.reason,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "bookings.followup.block_audit_failed bot_user=%s reason=%s",
            decision.bot_user_id,
            decision.reason,
        )


def _bump_idempotency_key(bot_user_id: Any, today_iso: str) -> None:
    """Record that this person has been nudged today.

    Re-reads ``context`` rather than trusting the copy fetched at plan
    time: planning and delivery are now separated by the whole batch, so
    another process has had longer to touch the row than it did when this
    was one loop. ``context`` defaults to ``{}`` per the model but legacy
    rows may hold ``None``; we normalise on write so future reads are
    consistent.

    Uses ``all_tenants``: this beat is cross-tenant and the default
    TenantScopedManager would refuse the update outside a tenant context.
    """
    from apps.identity.models import BotUser

    row = BotUser.all_tenants.filter(pk=bot_user_id).only("context").first()
    if row is None:
        return
    context = dict(row.context) if isinstance(row.context, dict) else {}
    context[CONTEXT_KEY] = today_iso
    BotUser.all_tenants.filter(pk=bot_user_id).update(context=context)
