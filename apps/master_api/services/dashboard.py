"""Master mobile dashboard M1 aggregator (master-mobile §M1).

Single-call endpoint that powers the master Mini App home screen
(``Screen M1`` in ``docs/design/handoffs/2026-05-18-master-mobile-handoff.md``).

Spec quote (§M1, layout block — see docstring for ``build_dashboard``):

    «СЕЙЧАС ┌─────────────────────────────┐
            │ ● Мария И. — маникюр гель-  │
            │ лак                         │
            │ Началось 14:30 · 90 мин     │
            │ До конца ≈ 78 мин           │
            │ [Заметка к визиту ›]        │
            └─────────────────────────────┘»

The endpoint composes the six independently-testable helpers below into a
single JSON-serialisable :class:`DashboardSnapshot`. Each helper takes
``(master, now)`` so tests can pin the clock and assert per-state
behaviour without spinning up the full view.

### Scope cuts (per PR 7 spec)

* Frontend / Mini App UI — separate PR (PR 8+).
* «Заметка к визиту» feature — ``ActiveVisit.note`` is a fixed empty
  string placeholder; the spec ships separately.
* M3 schedule, M5 conversations, M6 conversation detail, M7
  notification settings — separate PRs.
* AI-summarised ``customer_intent_hint`` — best-effort raw last
  customer message text (truncated 100 chars). Reads from
  :class:`apps.conversations.models.Conversation`. If no conversation
  is linked to the booking, returns an empty string with a TODO.
* WebSocket / push update — basic GET endpoint suffices.

### Tenant-TZ semantics

«Today» boundaries are computed in the tenant's IANA timezone
(:attr:`apps.tenancy.models.Tenant.timezone`, default
``Europe/Moscow``). Falling back to UTC for invalid TZ values matches
the model's docstring. The ``now`` argument is timezone-aware UTC; the
helper functions internally convert to tenant-local for date math.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.master_api.services.visit_source import (
    UPCOMING_STATUSES,
    VisitRow,
    master_client_ids,
    master_visit_count,
    master_visits,
)
from apps.catalog.models import CatalogMaster, CatalogService
from apps.internal_chat.models import MasterAdminMessage
from apps.scheduling.models import ScheduleChangeRequest, ScheduleException, WorkingHours

logger = logging.getLogger(__name__)


# Defaults --------------------------------------------------------------

DEFAULT_SERVICE_DURATION_MIN = 60
"""Fallback service duration when neither BookingRequest.duration_min
nor CatalogService.duration_min are populated. Matches the booking
attribution model's implicit default."""

INBOX_PREVIEW_LIMIT = 3
"""Per master-mobile §M1: «Inbox preview (max 3 cards)»."""

CUSTOMER_INTENT_HINT_MAX_LEN = 100
"""Truncation cap for ``next_visit.customer_intent_hint``."""

SLA_RED_HOURS = 4
SLA_YELLOW_MINUTES = 30
"""Best-effort SLA tiering for ``inbox_preview`` when no upstream
SLA-tier signal is available. Red ≥ 4h since last customer message,
yellow ≥ 30min, white otherwise."""

NEXT_FREE_WINDOW_MIN_GAP_MIN = 30
"""Minimum gap to surface as ``today_summary.next_free_window`` —
shorter slivers aren't useful as advertised slots."""


# Response dataclasses --------------------------------------------------


@dataclass(frozen=True)
class ActiveVisit:
    booking_id: str
    client_first_name: str
    client_last_initial: str
    service_name: str
    started_at: str  # iso
    duration_min: int
    minutes_remaining: int
    is_in_progress: bool
    note: str  # placeholder


@dataclass(frozen=True)
class NextVisit:
    booking_id: str
    client_first_name: str
    client_last_initial: str
    visit_at: str  # iso
    service_name: str
    duration_min: int
    is_returning_customer: bool
    customer_intent_hint: str


@dataclass(frozen=True)
class InboxItem:
    conversation_id: str
    client_first_name: str
    client_last_initial: str
    last_message_excerpt: str
    last_message_at: str  # iso
    sla_tier: str  # red / yellow / white
    ai_drafted_reply_available: bool


@dataclass(frozen=True)
class TodaySummary:
    total_clients_today: int
    completed_count: int
    next_free_window: dict[str, str] | None  # {"start": "HH:MM", "end": "HH:MM"} or None


@dataclass(frozen=True)
class TabBadges:
    conversations_unread: int
    schedule_has_pending_change: bool
    profile_has_owner_pending_change: bool


@dataclass(frozen=True)
class DashboardStates:
    is_day_done: bool
    is_offline_safe_response: bool


@dataclass(frozen=True)
class DashboardSnapshot:
    """Top-level result of :func:`build_dashboard`. JSON-serialisable
    via :meth:`to_dict`."""

    master: dict[str, Any]
    salon: dict[str, Any]
    now_iso: str
    active_visit: ActiveVisit | None
    next_visit: NextVisit | None
    inbox_preview: list[InboxItem]
    today_summary: TodaySummary
    tab_badges: TabBadges
    states: DashboardStates

    def to_dict(self) -> dict[str, Any]:
        return {
            "master": self.master,
            "salon": self.salon,
            "now_iso": self.now_iso,
            "active_visit": _dc_to_dict(self.active_visit),
            "next_visit": _dc_to_dict(self.next_visit),
            "inbox_preview": [_dc_to_dict(i) for i in self.inbox_preview],
            "today_summary": _dc_to_dict(self.today_summary),
            "tab_badges": _dc_to_dict(self.tab_badges),
            "states": _dc_to_dict(self.states),
        }


def _dc_to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "__dataclass_fields__"):
        return {f: getattr(obj, f) for f in obj.__dataclass_fields__}
    return obj


# Helpers ----------------------------------------------------------------


def get_tenant_tz(tenant: Any) -> ZoneInfo:
    """Resolve the tenant's IANA TZ — falls back to UTC on bad values.

    The :attr:`Tenant.timezone` field's docstring promises this fallback
    so callers don't need to guard against operator typos.
    """

    tz_name = getattr(tenant, "timezone", "") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("master_api.dashboard.bad_tenant_tz tz=%s", tz_name)
        return ZoneInfo("UTC")


def _split_name(client_name: str) -> tuple[str, str]:
    """Split ``Мария Иванова`` → («Мария», «И.»).

    Defensive: empty or single-word input returns the input as
    first_name + empty last_initial. The master-mobile spec §M1 calls
    for «Мария И.» style — first name + last initial — to balance
    customer privacy with master recognition.
    """

    parts = (client_name or "").strip().split()
    if not parts:
        return "", ""
    first = parts[0]
    if len(parts) == 1:
        return first, ""
    last_initial = parts[1][:1].upper() + "."
    return first, last_initial


def _today_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime, date]:
    """Return (start_of_today, end_of_today, today_date) in tenant TZ.

    Both bounds are timezone-aware UTC ``datetime`` objects so they
    compose with the storage layer. The middle return value is the
    tenant-local date used by helpers that need a calendar day.
    """

    local_now = now.astimezone(tz)
    today = local_now.date()
    start_local = datetime.combine(today, time.min, tzinfo=tz)
    end_local = datetime.combine(today, time.max, tzinfo=tz)
    return (
        start_local.astimezone(dt_timezone.utc),
        end_local.astimezone(dt_timezone.utc),
        today,
    )


def _resolve_duration(booking: VisitRow) -> int:
    """Pick the best-available duration for a visit.

    Order: the visit's own window (``end_at - start_at``, which the mirror
    always carries) → linked ``CatalogService.duration_min`` →
    :data:`DEFAULT_SERVICE_DURATION_MIN`.

    The catalog fallback is keyed on ``ayla_service_id``, not the local
    primary key: on the Ayla path ``service_id`` is Ayla's id, and the
    mirror stores it verbatim.
    """

    if booking.duration_min:
        return int(booking.duration_min)
    if booking.service_id:
        svc = CatalogService.all_tenants.filter(ayla_service_id=booking.service_id).first()
        if svc and svc.duration_min:
            return int(svc.duration_min)
    return DEFAULT_SERVICE_DURATION_MIN


# Active visit ----------------------------------------------------------


def get_active_visit(master: CatalogMaster, now: datetime) -> ActiveVisit | None:
    """Find the booking currently in progress for this master.

    A booking is «active» when ``visit_at <= now < visit_at + duration``
    AND ``status == CONFIRMED``. Spec quote (§M1):

        «● Мария И. — маникюр гель-лак / Началось 14:30 · 90 мин /
        До конца ≈ 78 мин»

    Returns None when no booking matches.
    """

    # We can't filter by `visit_at + duration > now` directly in SQL
    # without a computed column — so we narrow the search to bookings
    # whose visit_at is within the last 8 hours (longer than any
    # realistic salon service) then Python-filter precisely.
    window_start = now - timedelta(hours=8)
    candidates = master_visits(
        master,
        start=window_start,
        end=now,
        statuses=UPCOMING_STATUSES,
        newest_first=True,
        limit=10,
    )
    for booking in candidates:
        visit_at = booking.visit_at
        if visit_at is None:  # narrowed by visit_at__gte/__lte filters
            continue
        duration = _resolve_duration(booking)
        end_at = visit_at + timedelta(minutes=duration)
        if visit_at <= now < end_at:
            remaining = int((end_at - now).total_seconds() // 60)
            first, last_initial = _split_name(booking.client_name)
            return ActiveVisit(
                booking_id=str(booking.id),
                client_first_name=first,
                client_last_initial=last_initial,
                service_name=booking.service_name,
                started_at=visit_at.isoformat(),
                duration_min=duration,
                minutes_remaining=max(remaining, 0),
                is_in_progress=True,
                note="",  # placeholder — «заметка к визиту» is future work
            )
    return None


# Next visit ------------------------------------------------------------


def _is_returning_customer(master: CatalogMaster, booking: VisitRow) -> bool:
    """True when the client has more than one live visit with this master.

    Counts booked statuses — upcoming plus completed. Cancellations and
    no-shows do not make someone a returning customer.
    """

    if booking.bot_user_id is None:
        return False
    return master_visit_count(master, bot_user_id=booking.bot_user_id) > 1


def _customer_intent_hint(booking: VisitRow) -> str:
    """Best-effort: the customer's last message, for context before the visit.

    Empty string when the customer has no conversation or no user-role
    messages. The «AI summary» path (§M1 «Сказала: …») is out of scope —
    PR 7 ships raw last-message-excerpt only.

    DRF-1085: the mirror has no ``conversation`` FK (the local model had a
    snapshot one), so the conversation is found via the customer instead —
    the same identity the inbox already groups by. Same answer whenever the
    customer has one conversation with the salon, which is every pilot case.

    TODO(master mobile §M1, future PR): replace with an AI-summarised
    intent hint produced by the conversation summariser. Tracked
    alongside the M5 conversation list backend.
    """

    if booking.bot_user_id is None:
        return ""
    # Import locally to avoid an apps-loading cycle if conversations
    # isn't installed for some test runners.
    from apps.conversations.models import Conversation, Message

    conversation_id = (
        Conversation.all_tenants.filter(
            bot_user_id=booking.bot_user_id,
            deleted_at__isnull=True,
            is_shadow=False,
        )
        .order_by("-last_message_at")
        .values_list("id", flat=True)
        .first()
    )
    if conversation_id is None:
        return ""

    last = (
        Message.all_tenants.filter(
            conversation_id=conversation_id,
            role=Message.Role.USER,
        )
        .order_by("-created_at")
        .values_list("content", "rendered_text")
        .first()
    )
    if not last:
        return ""
    raw = (last[0] or last[1] or "").strip()
    if len(raw) > CUSTOMER_INTENT_HINT_MAX_LEN:
        return raw[: CUSTOMER_INTENT_HINT_MAX_LEN - 1].rstrip() + "…"
    return raw


def get_next_visit(master: CatalogMaster, now: datetime) -> NextVisit | None:
    """The soonest CONFIRMED booking after ``now`` within today (tenant TZ).

    Per §M1 «СЛЕДУЮЩИЙ КЛИЕНТ» card. Tomorrow's bookings explicitly
    don't qualify — the dashboard's empty-state copy handles that
    («Сегодня нет записей. … Ближайшая запись завтра в 10:00»).
    """

    tz = get_tenant_tz(master.tenant)
    _, end_of_today, _ = _today_bounds(now, tz)

    upcoming = master_visits(
        master,
        start=now + timedelta(microseconds=1),  # strictly after now
        end=end_of_today,
        statuses=UPCOMING_STATUSES,
        limit=1,
    )
    booking = upcoming[0] if upcoming else None
    if booking is None or booking.visit_at is None:
        return None

    duration = _resolve_duration(booking)
    first, last_initial = _split_name(booking.client_name)
    return NextVisit(
        booking_id=str(booking.id),
        client_first_name=first,
        client_last_initial=last_initial,
        visit_at=booking.visit_at.isoformat(),
        service_name=booking.service_name,
        duration_min=duration,
        is_returning_customer=_is_returning_customer(master, booking),
        customer_intent_hint=_customer_intent_hint(booking),
    )


# Inbox preview ---------------------------------------------------------


def _sla_tier(last_customer_message_at: datetime | None, now: datetime) -> str:
    """Best-effort SLA tiering — red ≥ 4h, yellow ≥ 30min, else white."""

    if last_customer_message_at is None:
        return "white"
    delta = now - last_customer_message_at
    if delta >= timedelta(hours=SLA_RED_HOURS):
        return "red"
    if delta >= timedelta(minutes=SLA_YELLOW_MINUTES):
        return "yellow"
    return "white"


_SLA_TIER_RANK = {"red": 0, "yellow": 1, "white": 2}


def get_inbox_preview(
    master: CatalogMaster,
    now: datetime,
    limit: int = INBOX_PREVIEW_LIMIT,
) -> list[InboxItem]:
    """Up to ``limit`` customer conversations needing master attention.

    Per §M1 «ТРЕБУЮТ ВНИМАНИЯ (n)» — capped at 3 cards. «Involved»
    is defined as «this master has had at least one booking with the
    client». We pull distinct ``bot_user_id``s from bookings, then look
    up their active customer conversations and surface the ones with
    unseen user-role messages.

    TODO(master mobile §M5+): replace with proper «assigned to me»
    routing once the Conversation→master attribution model lands. The
    current heuristic surfaces every client the master has booked which
    will be wrong on multi-master salons where the conversation belongs
    to a different master's queue.
    """

    from apps.conversations.models import Conversation, Message

    client_ids = master_client_ids(master)
    if not client_ids:
        return []

    conversations = list(
        Conversation.all_tenants.filter(
            tenant_id=master.tenant_id,
            bot_user_id__in=list(client_ids),
            is_active=True,
            deleted_at__isnull=True,
            is_shadow=False,
        )
        .select_related("bot_user")
        .order_by("-last_message_at")[: limit * 4]
    )

    items: list[tuple[str, datetime, InboxItem]] = []
    for conv in conversations:
        last_user_msg = (
            Message.all_tenants.filter(
                conversation_id=conv.id,
                role=Message.Role.USER,
            )
            .order_by("-created_at")
            .first()
        )
        if last_user_msg is None:
            continue
        last_msg_at = last_user_msg.created_at
        if last_msg_at is None:  # auto_now_add — defensive only
            continue
        # Look for any assistant message after the last user message —
        # if there isn't one, the customer is still waiting on the
        # salon. Best-effort proxy for «unread for the master».
        has_response = Message.all_tenants.filter(
            conversation_id=conv.id,
            role=Message.Role.ASSISTANT,
            created_at__gt=last_msg_at,
        ).exists()
        if has_response:
            continue

        client_name = (conv.bot_user.display_name or "").strip() if conv.bot_user else ""
        first, last_initial = _split_name(client_name)
        body = (last_user_msg.rendered_text or last_user_msg.content or "").strip()
        if len(body) > CUSTOMER_INTENT_HINT_MAX_LEN:
            body = body[: CUSTOMER_INTENT_HINT_MAX_LEN - 1].rstrip() + "…"
        tier = _sla_tier(last_msg_at, now)
        item = InboxItem(
            conversation_id=str(conv.id),
            client_first_name=first,
            client_last_initial=last_initial,
            last_message_excerpt=body,
            last_message_at=last_msg_at.isoformat(),
            sla_tier=tier,
            ai_drafted_reply_available=False,
        )
        items.append((tier, last_msg_at, item))

    items.sort(key=lambda triple: (_SLA_TIER_RANK[triple[0]], -triple[1].timestamp()))
    return [t[2] for t in items[:limit]]


# Today summary ---------------------------------------------------------


def _occupied_intervals_today(
    master: CatalogMaster, now: datetime, tz: ZoneInfo
) -> list[tuple[time, time]]:
    """Return today's booking start/end times in tenant-local TZ.

    Only CONFIRMED bookings count — cancelled / requested-state rows
    don't occupy the slot.
    """

    start_utc, end_utc, _ = _today_bounds(now, tz)
    bookings = master_visits(
        master,
        start=start_utc,
        end=end_utc,
        statuses=UPCOMING_STATUSES,
    )

    intervals: list[tuple[time, time]] = []
    for b in bookings:
        if b.visit_at is None:
            continue
        duration = _resolve_duration(b)
        local_start = b.visit_at.astimezone(tz).time()
        end_dt = (b.visit_at + timedelta(minutes=duration)).astimezone(tz)
        # Cap end at end-of-today to avoid carrying a midnight-spanning
        # interval into the gap calculation.
        local_end = (
            end_dt.time() if end_dt.date() == b.visit_at.astimezone(tz).date() else time(23, 59)
        )
        intervals.append((local_start, local_end))
    return intervals


def _working_block_today(master: CatalogMaster, today_local: date) -> tuple[time, time] | None:
    """Today's effective working block in tenant-local time.

    Picks ScheduleException for the date if present (custom_hours →
    those times; full-day-off types → None). Otherwise falls back to
    WorkingHours for the weekday (None if is_working=False).
    """

    exc = ScheduleException.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
        date=today_local,
    ).first()
    if exc is not None:
        if exc.type == ScheduleException.Type.CUSTOM_HOURS:
            if exc.start_time and exc.end_time:
                return (exc.start_time, exc.end_time)
            return None
        # Any other exception type is a full-day off.
        return None

    wh = WorkingHours.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
        day_of_week=today_local.weekday(),
    ).first()
    if wh is None or not wh.is_working or not wh.start_time or not wh.end_time:
        return None
    return (wh.start_time, wh.end_time)


def _next_free_window(master: CatalogMaster, now: datetime) -> dict[str, str] | None:
    """First gap of ≥30min after now in today's working block."""

    tz = get_tenant_tz(master.tenant)
    local_now = now.astimezone(tz)
    today_local = local_now.date()
    block = _working_block_today(master, today_local)
    if block is None:
        return None
    block_start, block_end = block

    # Start scanning from max(now, block_start).
    cursor = max(local_now.time().replace(microsecond=0), block_start)
    if cursor >= block_end:
        return None

    intervals = _occupied_intervals_today(master, now, tz)
    # Only consider intervals that intersect the remaining working block.
    relevant = sorted(
        (
            (max(s, cursor), min(e, block_end))
            for (s, e) in intervals
            if e > cursor and s < block_end
        ),
        key=lambda iv: iv[0],
    )

    def _gap_ok(start: time, end: time) -> bool:
        return _time_diff_min(start, end) >= NEXT_FREE_WINDOW_MIN_GAP_MIN

    for occ_start, occ_end in relevant:
        if occ_start > cursor and _gap_ok(cursor, occ_start):
            # Cap gap at the working block end.
            window_end = min(occ_start, block_end)
            return {
                "start": cursor.strftime("%H:%M"),
                "end": window_end.strftime("%H:%M"),
            }
        cursor = max(cursor, occ_end)
        if cursor >= block_end:
            return None

    if cursor < block_end and _gap_ok(cursor, block_end):
        return {
            "start": cursor.strftime("%H:%M"),
            "end": block_end.strftime("%H:%M"),
        }
    return None


def _time_diff_min(start: time, end: time) -> int:
    """Minutes between two ``time`` values on the same day."""

    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def get_today_summary(master: CatalogMaster, now: datetime) -> TodaySummary:
    """Aggregate counts + next-free-window for today (tenant TZ)."""

    tz = get_tenant_tz(master.tenant)
    start_utc, end_utc, _ = _today_bounds(now, tz)
    # Booked statuses only — cancelled and no-show rows are not clients the
    # master is expecting. The mirror has no `rescheduled` state at all: a
    # reschedule moves the existing row in place rather than superseding it.
    today = master_visits(master, start=start_utc, end=end_utc)

    total = len(today)
    # The mirror carries no completion timestamp, only the status — which
    # is the more honest marker anyway.
    completed = sum(1 for visit in today if visit.is_completed)
    window = _next_free_window(master, now)
    return TodaySummary(
        total_clients_today=total,
        completed_count=completed,
        next_free_window=window,
    )


# Tab badges ------------------------------------------------------------


def get_tab_badges(master: CatalogMaster, now: datetime) -> TabBadges:
    """Badge counters for the bottom tab bar (§M1 «[💬 2]»).

    * ``conversations_unread``: customer conversations with unanswered
      user messages PLUS internal_chat threads with at least one
      unread (to-master) message.
    * ``schedule_has_pending_change``: a ScheduleChangeRequest exists
      with status=PENDING for this master.
    * ``profile_has_owner_pending_change``: hard-False placeholder
      pending the «owner edited master profile pending ack» spec.
    """

    from apps.conversations.models import Conversation, Message

    # Customer-side unread — same definition as inbox_preview (no
    # assistant reply after last user message).
    client_ids = master_client_ids(master)
    customer_unread = 0
    if client_ids:
        conversations = Conversation.all_tenants.filter(
            tenant_id=master.tenant_id,
            bot_user_id__in=client_ids,
            is_active=True,
            deleted_at__isnull=True,
            is_shadow=False,
        ).values_list("id", flat=True)
        for conv_id in conversations:
            last_user = (
                Message.all_tenants.filter(
                    conversation_id=conv_id,
                    role=Message.Role.USER,
                )
                .order_by("-created_at")
                .first()
            )
            if last_user is None:
                continue
            has_response = Message.all_tenants.filter(
                conversation_id=conv_id,
                role=Message.Role.ASSISTANT,
                created_at__gt=last_user.created_at,
            ).exists()
            if not has_response:
                customer_unread += 1

    # Internal-chat threads: count threads where at least one
    # admin/founder/system message is unread by the master.
    internal_unread = (
        MasterAdminMessage.objects.filter(
            thread__tenant_id=master.tenant_id,
            thread__master_id=master.id,
            read_by_master_at__isnull=True,
        )
        .exclude(sender_role="master")
        .values("thread_id")
        .distinct()
        .count()
    )

    pending_change = ScheduleChangeRequest.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
        status=ScheduleChangeRequest.Status.PENDING,
    ).exists()

    return TabBadges(
        conversations_unread=customer_unread + internal_unread,
        schedule_has_pending_change=pending_change,
        # TODO(master mobile §M4 + decisions-log): wire to «owner
        # edited master profile pending ack» once that spec is locked.
        profile_has_owner_pending_change=False,
    )


# States ----------------------------------------------------------------


def get_states(master: CatalogMaster, now: datetime) -> DashboardStates:
    """Compute :class:`DashboardStates` for §M1's empty/done variants.

    ``is_day_done`` — true when ``now`` is past the end of the last
    booking today (tenant TZ). When no bookings exist today, the day
    can never be «done» — that's the «no clients today» empty state.
    ``is_offline_safe_response`` is always False for a successful API
    response: the client uses it as a banner cue when it falls back to
    cached data, never when it has fresh server data.
    """

    tz = get_tenant_tz(master.tenant)
    start_utc, end_utc, _ = _today_bounds(now, tz)
    latest = master_visits(
        master,
        start=start_utc,
        end=end_utc,
        statuses=UPCOMING_STATUSES,
        newest_first=True,
        limit=1,
    )
    last_today = latest[0] if latest else None
    is_day_done = False
    if last_today is not None and last_today.visit_at is not None:
        duration = _resolve_duration(last_today)
        end_of_last = last_today.visit_at + timedelta(minutes=duration)
        is_day_done = now > end_of_last
    return DashboardStates(
        is_day_done=is_day_done,
        is_offline_safe_response=False,
    )


# Top-level aggregator --------------------------------------------------


def build_dashboard(master: CatalogMaster, now: datetime) -> DashboardSnapshot:
    """Compose every helper into a :class:`DashboardSnapshot`.

    Spec quote (master-mobile §M1, lines 284-340):

        «Screen M1 — Master mobile dashboard (home) … Layout (mobile,
        ~360–430px width) … СЕЙЧАС … СЛЕДУЮЩИЙ КЛИЕНТ … ТРЕБУЮТ
        ВНИМАНИЯ (2) … СЕГОДНЯ … [🏠] [📅] [💬 2] [👤]»

    The aggregator is pure orchestration — every branch lives in a
    dedicated helper so tests pin the helper directly without
    constructing a full Django request.
    """

    return DashboardSnapshot(
        master={
            "id": str(master.id),
            "name": master.name,
            "specialization": master.specialization,
            "photo_url": master.photo_url,
        },
        salon={
            "id": str(master.tenant_id),
            "name": master.tenant.name,
        },
        now_iso=now.isoformat(),
        active_visit=get_active_visit(master, now),
        next_visit=get_next_visit(master, now),
        inbox_preview=get_inbox_preview(master, now),
        today_summary=get_today_summary(master, now),
        tab_badges=get_tab_badges(master, now),
        states=get_states(master, now),
    )


__all__ = [
    "ActiveVisit",
    "DEFAULT_SERVICE_DURATION_MIN",
    "DashboardSnapshot",
    "DashboardStates",
    "InboxItem",
    "NextVisit",
    "TabBadges",
    "TodaySummary",
    "build_dashboard",
    "get_active_visit",
    "get_inbox_preview",
    "get_next_visit",
    "get_states",
    "get_tab_badges",
    "get_tenant_tz",
    "get_today_summary",
]
