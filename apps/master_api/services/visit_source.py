"""Where a master's visits come from (DRF-1085).

### The bug this fixes

``dashboard.py`` and ``schedule.py`` built the master's day from
``booking.BookingRequest``. On the pilot that table holds 4 rows and
``master_id`` is NULL on **all four**, while the live bookings — 23 rows,
every one carrying ``specialist_id`` — sit in ``booking.RemoteBookingProxy``,
which is what the Ayla event consumers write when
``BOOKING_VIA_AYLA_REST`` is on.

So the master's day rendered as HTTP 200 with ``active_visit: null``,
``next_visit: null``, ``today_summary {0, 0, null}`` — indistinguishable
from "no appointments today". The worst kind of break: it looks like an
answer.

The split itself is deliberate and stays (owner decision A2-POST,
2026-06-01, deferring the write-target flip past the pilot). What was never
decided is that the master surface should read only the half that is empty.
This module reads the half that has the data.

### Why an adapter and not a rewrite

The dashboard's logic — active-visit windowing, returning-customer counts,
free-window arithmetic — is correct and tested; only its *source* is wrong.
:class:`VisitRow` therefore presents the proxy under the field names the
existing code already uses (``visit_at``, ``service_name``, ``client_name``
…), so the change stays a source swap rather than a re-derivation of
business logic.

### What the proxy does not carry

``RemoteBookingProxy`` is a mirror, not a snapshot: it has no
``service_name``, no ``client_name`` and no ``conversation`` FK. Those are
resolved by lookup here, batched to avoid N+1:

* service name ← ``CatalogService.ayla_service_id``
* client name  ← ``BotUser`` (``client_name`` → ``display_name`` → «Гость»)

One consequence is worth stating plainly: a snapshot becomes a live lookup,
so renaming a service in the catalog rewrites what the master sees for past
visits. ``BookingRequest`` could not do that by construction. Accepted for
the pilot — the alternative is denormalising fields the mirror is not
allowed to own (ADR-0009 rule 1).

**Client phone is never resolved here, by any path.** Owner decision
DRF-1039: the executor does not receive the customer's phone number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from uuid import UUID

from apps.booking.models import RemoteBookingProxy

# Statuses that mean "this visit is expected to happen / did happen".
#
# Spelled as raw strings rather than the enum on purpose: the pilot mirror
# contains `awaiting_payment`, which is Ayla's wire value and is NOT in
# RemoteBookingProxy.Status.choices (Django does not enforce choices at the
# DB level). miniapp_api hit the same thing and keeps the same defensive
# list — see `_AYLA_UPCOMING_STATUSES` there.
UPCOMING_STATUSES: tuple[str, ...] = ("confirmed", "awaiting_payment", "pending_payment")

#: Statuses that occupy the master's time — used for day arithmetic.
BOOKED_STATUSES: tuple[str, ...] = UPCOMING_STATUSES + ("completed",)

#: Terminal statuses that free the slot.
RELEASED_STATUSES: tuple[str, ...] = ("cancelled", "no_show")

GUEST_NAME = "Гость"


@dataclass(frozen=True)
class VisitRow:
    """One visit, shaped like the ``BookingRequest`` rows this replaces.

    Field names deliberately match the local model so call sites read the
    same as before. ``id`` is Ayla's ``appointment_id`` — the canonical
    booking identity, and the only id the salon and the customer share.
    """

    id: str
    visit_at: datetime | None
    end_at: datetime | None
    duration_min: int
    status: str
    service_name: str
    client_name: str
    bot_user_id: UUID | None
    service_id: UUID | None

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def completed_at(self) -> datetime | None:
        """Approximation of the local model's ``completed_at``.

        The mirror carries no completion timestamp — only the status — so
        the visit's end is the best available stand-in. Callers that merely
        ask "was this done?" should prefer :attr:`is_completed`.
        """

        return self.end_at if self.is_completed else None


def _resolve_service_names(service_ids: Iterable[UUID | None], tenant_id) -> dict[UUID, str]:
    """Batch ``ayla_service_id`` → catalog name. Missing → empty string."""

    ids = {sid for sid in service_ids if sid}
    if not ids:
        return {}

    from apps.catalog.models import CatalogService

    rows = CatalogService.all_tenants.filter(
        tenant_id=tenant_id, ayla_service_id__in=ids
    ).values_list("ayla_service_id", "name")
    return {sid: name for sid, name in rows if sid is not None}


def _resolve_client_names(bot_user_ids: Iterable[UUID | None]) -> dict[UUID, str]:
    """Batch BotUser id → display name.

    ``client_name`` first (what the customer told us), then
    ``display_name`` (what the channel reports), then «Гость». Orphan
    proxies — a booking made in the Ayla app by someone who never opened
    the bot — legitimately have no BotUser at all; 3 of the pilot's 23 rows
    are in that state.
    """

    ids = {bid for bid in bot_user_ids if bid}
    if not ids:
        return {}

    from apps.identity.models import BotUser

    out: dict[UUID, str] = {}
    for pk, client_name, display_name in BotUser.all_tenants.filter(id__in=ids).values_list(
        "id", "client_name", "display_name"
    ):
        out[pk] = (client_name or "").strip() or (display_name or "").strip() or GUEST_NAME
    return out


def _to_rows(proxies: list[RemoteBookingProxy], tenant_id) -> list[VisitRow]:
    service_names = _resolve_service_names((p.service_id for p in proxies), tenant_id)
    client_names = _resolve_client_names((p.bot_user_id for p in proxies))

    rows: list[VisitRow] = []
    for proxy in proxies:
        duration = 0
        if proxy.start_at and proxy.end_at:
            duration = max(int((proxy.end_at - proxy.start_at).total_seconds() // 60), 0)

        rows.append(
            VisitRow(
                id=str(proxy.appointment_id),
                visit_at=proxy.start_at,
                end_at=proxy.end_at,
                duration_min=duration,
                status=proxy.status,
                service_name=service_names.get(proxy.service_id, "") if proxy.service_id else "",
                client_name=(
                    client_names.get(proxy.bot_user_id, GUEST_NAME)
                    if proxy.bot_user_id
                    else GUEST_NAME
                ),
                bot_user_id=proxy.bot_user_id,
                service_id=proxy.service_id,
            )
        )
    return rows


def master_visits(
    master,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    statuses: Iterable[str] | None = BOOKED_STATUSES,
    newest_first: bool = False,
    limit: int | None = None,
) -> list[VisitRow]:
    """Visits belonging to ``master``, newest-or-earliest first.

    ``specialist_id`` on the mirror is Ayla's ``SpecialistProfile.id``, which
    IS ``CatalogMaster.id`` — the catalog upserter keys the mirror on it
    (``apps/catalog/services/upserter.py``). Verified against the pilot: all
    23 proxy rows resolve to a master by primary key, none via
    ``ayla_user_id``.

    ``statuses=None`` means "every status", used where the caller does its
    own filtering.
    """

    qs = RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id=master.id,
    )
    if statuses is not None:
        qs = qs.filter(status__in=list(statuses))
    if start is not None:
        qs = qs.filter(start_at__gte=start)
    if end is not None:
        qs = qs.filter(start_at__lte=end)

    qs = qs.order_by("-start_at" if newest_first else "start_at")
    if limit is not None:
        qs = qs[:limit]

    proxies = list(qs)
    return _to_rows(proxies, master.tenant_id)


def master_visit_count(
    master,
    *,
    bot_user_id: UUID | None = None,
    statuses: Iterable[str] | None = BOOKED_STATUSES,
) -> int:
    """Count visits without building rows — for «returning customer» checks."""

    qs = RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id=master.id,
    )
    if statuses is not None:
        qs = qs.filter(status__in=list(statuses))
    if bot_user_id is not None:
        qs = qs.filter(bot_user_id=bot_user_id)
    return qs.count()


def master_client_ids(master, *, statuses: Iterable[str] | None = None) -> list[UUID]:
    """Distinct customers who have ever booked this master.

    ``statuses=None`` (the default here, unlike elsewhere) means *any*
    status on purpose: someone who booked and cancelled is still someone
    the master may be talking to, and this feeds the inbox rather than the
    calendar. Orphan proxies carry no ``bot_user`` and are skipped — there
    is no one to show.
    """

    qs = RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id=master.id,
        bot_user_id__isnull=False,
    )
    if statuses is not None:
        qs = qs.filter(status__in=list(statuses))
    return list(qs.values_list("bot_user_id", flat=True).distinct())


def occupied_intervals(
    master, *, day_start: datetime, day_end: datetime
) -> list[tuple[datetime, datetime]]:
    """(start, end) pairs for visits occupying the master's day."""

    out: list[tuple[datetime, datetime]] = []
    for row in master_visits(master, start=day_start, end=day_end):
        if row.visit_at is None:
            continue
        end_at = row.end_at or row.visit_at + timedelta(minutes=row.duration_min)
        out.append((row.visit_at, end_at))
    return out
