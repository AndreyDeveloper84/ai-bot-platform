"""Customer visit history and repeat intent — reusable capability (DRF-1032).

Owner decision **OD-IR3**: history and repeat are application capabilities,
not a branch inside an intent detector. The deterministic router is one
*caller* today; the LLM concierge becomes another after the pilot, and the
Mini App is a third. Nothing here knows about MAX, buttons, wording, or the
model — callers render, this module decides.

That split is why the return values are dataclasses carrying a stable
``status`` slug rather than text: the same convention
``apps.booking.services.create`` already uses (``BookingCreateError.slug``,
mapped to HTTP by the view) and ``calc_price`` uses inside the booking skill
(``CalcPriceResult`` + a separate formatter).

Source of truth is the **Ayla backend** (OD-H1). The local
``RemoteBookingProxy`` mirror stays operational — reminders, retries,
delivery bookkeeping — and is deliberately NOT read here: a mirror row can
outlive the booking it mirrors (DRF-1034 showed a ghost booking with live
reminders), and history is long-lived truth where that error accumulates.

Identity: the subject is derived from ``BotUser`` inside this module and
sent as ``X-External-User-ID``. No caller — least of all a future LLM tool —
passes a subject in. See ``docs/Ayla-intent-routing-tool-first-architect-prompt``
§5: the model says what the user wants, the trusted runtime decides who
they are.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from django.utils import timezone

from apps.integrations.ayla.booking_client import (
    AylaUserRecord,
    BookingAPIError,
    BookingBadRequestError,
    BookingUnavailableError,
    RepeatIntentUnusableError,
    get_ayla_booking_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for


logger = logging.getLogger(__name__)


# ── display policy ──────────────────────────────────────────────────────────

# OD-H2: the customer list shows visits that ACTUALLY HAPPENED. Cancellations,
# no-shows and stale unconfirmed rows are hidden — a presentation policy, not
# a retention one.
#
# All three values below are emitted only when ``Appointment.status ==
# "completed"`` (``appointments/records_status.py:134-141``); the refund
# variants describe what happened to the MONEY afterwards, not whether the
# visit took place. A refunded visit is still a visit the customer attended.
#
# OD-V1 (2026-08-14) settled who may close a visit — salon, client, or the
# 3-hour auto-close — and every path lands on the same ``completed`` status.
# Keep this set as the ONE place that defines "counts as a visit": a change
# of policy must stay a one-line change.
COMPLETED_VISIT_STATUSES = frozenset({"completed", "refund_completed", "partial_refund"})

# OD-H3: the pilot shows the five most recent visits, with no user-facing
# pagination.
DEFAULT_VISIT_LIMIT = 5

# The backend's history section mixes cancellations and no-shows into the
# same page (``records_api.py:331-337``), so N completed visits can need more
# than N rows. Pull a wider page and top up through the cursor — an internal
# top-up, not user-facing pagination.
_PAGE_SIZE = 20
# Ceiling on the top-up, same defensive shape as the catalog walk
# (``_get_all_rows``): a customer with a long cancellation streak must not
# turn one chat message into an unbounded crawl.
_MAX_PAGES = 3


# ── results ─────────────────────────────────────────────────────────────────

VisitsStatus = Literal["ok", "empty", "backend_unavailable"]

RepeatStatus = Literal[
    "ok",
    "master_unavailable",
    "service_unavailable",
    "link_unavailable",
    "prefill_unusable",
    "backend_unavailable",
]


@dataclass(frozen=True)
class Visit:
    """One completed visit, in the terms a customer thinks in.

    Deliberately narrow: internal identifiers, operational status, tenant
    binding, proxy mechanics and payment rows exist in the backend response
    and stay there (§32 of the owner document — the customer must never see
    them). ``appointment_id`` is the single id that leaves this module,
    because the detail card and the repeat action need a handle.

    ``closed_by`` is reserved for OD-V1: a visit may be closed by the salon,
    by the client, or by the 3-hour auto-close, and the owner requires that
    the three stay distinguishable. The backend does not expose the source
    yet (no such field on ``Appointment`` as of ``5c9abea``), so this is
    ``None`` today — but no code below is allowed to assume all completed
    visits are equal.
    """

    appointment_id: str
    service_name: str
    master_name: str
    start_at: str
    price: Decimal | None
    closed_by: str | None = None


@dataclass(frozen=True)
class VisitsResult:
    status: VisitsStatus
    visits: tuple[Visit, ...] = ()


@dataclass(frozen=True)
class RepeatEntry:
    """What a caller needs to resume the EXISTING booking flow.

    Both ids are Ayla-native and are exactly what the booking entry point
    already expects, so repeat needs no second state machine (OD-H4).
    """

    specialist_id: str
    service_id: str


@dataclass(frozen=True)
class RepeatResult:
    """Outcome of «Записаться ещё» — an intent, never a guarantee.

    Historical facts describe the past; ``current_price`` is what a new
    booking would cost now. When the two differ the caller must show both —
    silently quoting the old price is the failure mode OD-H4 forbids.
    """

    status: RepeatStatus
    entry: RepeatEntry | None = None
    service_name: str = ""
    master_name: str = ""
    historical_price: Decimal | None = None
    current_price: Decimal | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def price_changed(self) -> bool:
        return (
            self.historical_price is not None
            and self.current_price is not None
            and self.historical_price != self.current_price
        )


# ── capability ──────────────────────────────────────────────────────────────


def list_visits(*, bot_user, limit: int = DEFAULT_VISIT_LIMIT) -> VisitsResult:
    """Most recent completed visits of ``bot_user``, newest first.

    Reads the backend, never the mirror (OD-H1). A backend outage returns
    ``backend_unavailable`` rather than stale mirror rows: showing yesterday's
    truth precisely when the source is unreachable is worse than admitting the
    outage (§30 of the owner document).
    """
    client = get_ayla_booking_client()
    external_user_id = external_user_id_for(bot_user)

    collected: list[Visit] = []
    cursor: str | None = None
    if limit <= 0:
        return VisitsResult(status="empty")
    try:
        for _ in range(_MAX_PAGES):
            page = client.get_user_bookings_page(
                external_user_id=external_user_id,
                section="history",
                limit=_PAGE_SIZE,
                cursor=cursor,
            )
            for record in page.records:
                if record.derived_status is None:
                    # The field the whole policy keys off is missing. Filtering
                    # the row out would report "you have no visits" for what is
                    # actually a contract change — the same silent-empty
                    # failure this feature exists to avoid.
                    logger.warning(
                        "records.list_visits.status_missing booking_id=%s",
                        record.appointment_id,
                    )
                    return VisitsResult(status="backend_unavailable")
                if record.derived_status.lower() not in COMPLETED_VISIT_STATUSES:
                    continue
                collected.append(_visit_from_record(record))
                if len(collected) >= limit:
                    return VisitsResult(status="ok", visits=tuple(collected))
            next_cursor = page.next_cursor
            # A cursor that does not move means the backend is not advancing;
            # continuing would re-read the same page until the ceiling.
            if not next_cursor or next_cursor == cursor:
                cursor = None
                break
            cursor = next_cursor
    except BookingAPIError as exc:
        logger.warning("records.list_visits.unavailable err=%s", exc)
        return VisitsResult(status="backend_unavailable")

    if not collected:
        if cursor:
            # The ceiling stopped us with history still unread. Saying "you
            # have no visits" here would be a confident falsehood — the
            # customer may have visits on the very next page.
            logger.warning("records.list_visits.ceiling_reached pages=%d", _MAX_PAGES)
            return VisitsResult(status="backend_unavailable")
        return VisitsResult(status="empty")
    return VisitsResult(status="ok", visits=tuple(collected))


def list_upcoming(*, bot_user, limit: int = DEFAULT_VISIT_LIMIT) -> VisitsResult:
    """Bookings still ahead of ``bot_user``, soonest first.

    Same source as the history read, and that is the point (H-1): one
    question from the customer — «мои записи» / «мои визиты» reach the same
    detector — must not be answered from two different truths depending on
    which word they picked. The backend's ``upcoming`` section already means
    "active status AND in the future", so no status filter is applied here;
    unlike history, every row it returns is something the customer still has.
    """
    client = get_ayla_booking_client()
    try:
        page = client.get_user_bookings_page(
            external_user_id=external_user_id_for(bot_user),
            section="upcoming",
            limit=limit,
        )
    except BookingAPIError as exc:
        logger.warning("records.list_upcoming.unavailable err=%s", exc)
        return VisitsResult(status="backend_unavailable")

    visits = tuple(_visit_from_record(r) for r in page.records[:limit])
    return VisitsResult(status="ok" if visits else "empty", visits=visits)


def get_visit(*, bot_user, appointment_id: str) -> Visit | None:
    """One visit's card, or ``None`` when the backend will not give it.

    A booking belonging to somebody else answers 404 identically to one that
    does not exist, so this returns ``None`` in both cases — the caller has
    nothing to disclose either way.
    """
    client = get_ayla_booking_client()
    try:
        record = client.get_booking_detail(
            external_user_id=external_user_id_for(bot_user),
            booking_id=appointment_id,
        )
    except BookingAPIError as exc:
        logger.warning("records.get_visit.unavailable booking_id=%s err=%s", appointment_id, exc)
        return None
    return _visit_from_record(record)


def prepare_repeat(*, bot_user, appointment_id: str) -> RepeatResult:
    """Check whether a past visit can be repeated right now.

    Order matters and is not an implementation detail. The slots endpoint is
    the AUTHORITY, because it runs the very resolver that booking creation
    runs (``services/service_resolver.py`` — "do NOT add a second resolution
    path elsewhere"). Only after it refuses does this function ask a second,
    cheaper question, and only to tell the customer WHICH thing went away.
    Deciding eligibility from the catalog mirror instead would duplicate a
    business rule the backend owns (§22 of the owner document) and would
    drift from it the first time a master is deactivated.
    """
    client = get_ayla_booking_client()
    external_user_id = external_user_id_for(bot_user)

    try:
        intent = client.get_repeat_intent(
            external_user_id=external_user_id, booking_id=appointment_id
        )
    except RepeatIntentUnusableError as exc:
        # DRF-1049 was fixed upstream (the endpoint now answers 422 instead of
        # the literal string "None"), but the guard stays: a bot that trusts a
        # malformed id strands the customer in a dead end.
        logger.warning("records.repeat.prefill_unusable field=%s", exc.field)
        return RepeatResult(status="prefill_unusable")
    except BookingBadRequestError as exc:
        if exc.code == "SERVICE_NOT_FOUND":
            return RepeatResult(status="prefill_unusable")
        logger.warning("records.repeat.rejected code=%s", exc.code)
        return RepeatResult(status="backend_unavailable")
    except BookingUnavailableError as exc:
        logger.warning("records.repeat.unavailable err=%s", exc)
        return RepeatResult(status="backend_unavailable")

    entry = RepeatEntry(specialist_id=intent.specialist_id, service_id=intent.service_id)
    historical_price = _as_decimal(intent.last_price)

    # Names come from the visit itself, and they matter in EVERY branch: a
    # refusal that says «Мастер сейчас не принимает» instead of naming Инна is
    # barely a repeat intent at all. The booking-time snapshot is also the
    # right source — it is what the customer remembers happening.
    past = get_visit(bot_user=bot_user, appointment_id=appointment_id)
    service_name = past.service_name if past else ""
    master_name = past.master_name if past else ""

    eligibility, edge = _check_current_eligibility(
        client, specialist_id=entry.specialist_id, service_id=entry.service_id
    )
    if eligibility != "ok":
        return RepeatResult(
            status=eligibility,
            entry=entry,
            service_name=service_name,
            master_name=master_name,
            historical_price=historical_price,
        )

    if edge is None:
        edge = _specialist_service_edge(
            client, specialist_id=entry.specialist_id, service_id=entry.service_id
        )
    return RepeatResult(
        status="ok",
        entry=entry,
        service_name=service_name,
        master_name=master_name,
        historical_price=historical_price,
        current_price=_as_decimal(edge.get("price")) if edge else None,
    )


# ── internals ───────────────────────────────────────────────────────────────


def _check_current_eligibility(
    client, *, specialist_id: str, service_id: str
) -> tuple[RepeatStatus, dict[str, Any] | None]:
    """Ask the authority, then name the reason if it says no.

    Returns the verdict and, when it had to look, the catalog edge — so the
    happy path does not pay for the same lookup twice.

    On refusal the reason is never guessed. A 404 carrying ``error.code``
    came from the service resolver; a 404 without one came from Django's
    ``Http404``, which bypasses the backend's envelope handler. That
    difference is real but incidental — ``_err_code`` also yields
    ``"unknown"`` for an nginx error page or a renamed route, and telling a
    customer «мастер не принимает» because a proxy answered HTML would be an
    infrastructure failure dressed as a fact about the salon. So the
    master's absence is CONFIRMED with a direct question before it is
    claimed.
    """
    probe_date = timezone.localdate().isoformat()
    try:
        client.get_available_times(
            specialist_id=specialist_id, date=probe_date, service_id=service_id
        )
    except BookingBadRequestError as exc:
        if exc.status_code != 404:
            logger.warning("records.repeat.probe_rejected code=%s", exc.code)
            return "backend_unavailable", None
        if not exc.code or exc.code == "unknown":
            return _confirm_master_gone(client, specialist_id=specialist_id), None
        return _service_or_link(client, specialist_id=specialist_id, service_id=service_id)
    except BookingUnavailableError as exc:
        logger.warning("records.repeat.probe_unavailable err=%s", exc)
        return "backend_unavailable", None
    # An empty slot list is NOT a refusal: the pair is valid and the booking
    # flow will offer other days. Treating "no slots today" as "unavailable"
    # would send people away from a master who is free tomorrow.
    return "ok", None


def _confirm_master_gone(client, *, specialist_id: str) -> RepeatStatus:
    """Second opinion before blaming the master.

    ``specialists/{id}/`` answers 404 only from the catalog queryset
    (``status=ACTIVE, is_available=True, user__is_active=True``), so a 404
    here is a definite answer rather than an inference from the shape of an
    error body. Anything else means we could not establish the reason, and
    an honest outage beats a confident wrong sentence.
    """
    try:
        client.get_masters(specialist_id=specialist_id)
    except BookingBadRequestError as exc:
        if exc.status_code == 404:
            return "master_unavailable"
        logger.warning("records.repeat.master_probe_rejected code=%s", exc.code)
        return "backend_unavailable"
    except BookingAPIError as exc:
        logger.warning("records.repeat.master_probe_failed err=%s", exc)
        return "backend_unavailable"
    # The master is bookable, yet the slots resolver refused: the reason is
    # on the service side after all.
    return "service_unavailable"


def _service_or_link(
    client, *, specialist_id: str, service_id: str
) -> tuple[RepeatStatus, dict[str, Any] | None]:
    """Distinguish a withdrawn service from a dissolved master↔service link.

    Only reached when the authority already refused — this call never grants
    permission, it only picks the sentence the customer reads. A failed
    lookup must NOT read as "the link is gone": that would turn a timeout
    into a statement about the salon's catalog.
    """
    try:
        rows = client.get_specialist_service_edges(
            specialist_id=specialist_id, service_id=service_id
        )
    except BookingAPIError as exc:
        logger.warning("records.repeat.edge_lookup_failed err=%s", exc)
        return "backend_unavailable", None
    if not rows:
        return "link_unavailable", None
    return "service_unavailable", rows[0]


def _specialist_service_edge(
    client, *, specialist_id: str, service_id: str
) -> dict[str, Any] | None:
    """The active bookable edge for this pair, or ``None`` when unknown.

    Used only for the price, where "unknown" and "no edge" are equally
    harmless: the price is simply not shown, and the historical one is never
    passed off as current.
    """
    try:
        rows = client.get_specialist_service_edges(
            specialist_id=specialist_id, service_id=service_id
        )
    except BookingAPIError as exc:
        logger.warning("records.repeat.edge_lookup_failed err=%s", exc)
        return None
    return rows[0] if rows else None


def _current_price(client, *, specialist_id: str, service_id: str) -> Decimal | None:
    """Price a NEW booking would carry — from the edge, not the salon base.

    The backend stamps ``SpecialistService.price`` onto the appointment
    (``create_booking_service.py`` via the resolver), while the catalog
    mirror the bot quotes from carries ``SalonService.base_price``. They
    agree on the pilot today (232/232 rows), but they are different fields
    and the moment a salon sets a per-master price they diverge. Quoting the
    edge keeps the number the customer sees equal to the number they will be
    charged.
    """
    edge = _specialist_service_edge(client, specialist_id=specialist_id, service_id=service_id)
    if edge is None:
        return None
    return _as_decimal(edge.get("price"))


def _visit_from_record(record: AylaUserRecord) -> Visit:
    service = record.services[0] if record.services else {}
    return Visit(
        appointment_id=record.appointment_id,
        service_name=str(service.get("name") or ""),
        master_name=str(record.master.get("display_name") or ""),
        start_at=record.datetime,
        price=_as_decimal(record.price),
        # OD-V1: reserved. The backend carries no close-source field yet.
        closed_by=None,
    )


def _as_decimal(value: Any) -> Decimal | None:
    """Money as ``Decimal``, never as float arithmetic.

    The wire carries a JSON number (the records views bypass DRF
    serializers), so the client hands over a float; converting through
    ``str`` keeps 2500.0 rendering as "2500", not "2500.0000000001".
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
