"""Master Mini App customer roster aggregator (master-solo-surface §4.3).

Read-only aggregation over existing ``apps.booking.BookingRequest`` history
grouped by ``bot_user`` for a given master. Surfaces the "Клиенты" tab in
the solo provider Mini App per Tau's Variant B navigation verdict.

### Scope (Tier 2 Phase 1)

* Roster of customers who've booked with this master (>=1 confirmed/completed
  booking each).
* Per-customer aggregates: name, masked phone, last visit timestamp, last
  service name, total visit count, "is returning" flag, "at risk" flag.

### Out of scope (deferred post-pilot — see tracking issues filed by W1)

* Customer notes CRUD (``MasterCustomerNote`` model).
* Phone reveal with audit event (``RedZoneReader`` pattern from W4 #710).
* Customer detail screen with full visit history (Alpha endpoint needed).
* Search / sort customisation (UI ships stub-disabled selectors).

### Tenant safety

* Caller MUST hold a verified :class:`apps.catalog.models.CatalogMaster`
  via :func:`apps.master_api.auth.require_master_init_data` — the master
  carries the tenant scope. We pass ``tenant_id`` into every ORM filter
  as defence-in-depth (BookingRequest already has a tenant FK).
* Phone masking is enforced **server-side**: the full :attr:`BotUser.phone`
  is replaced before the dict leaves this module. No path returns the
  full phone — even an XSS-style client can't recover it.

### Performance note

The aggregation runs a single grouped query over ``BookingRequest`` with
:func:`Count` + :func:`Max`, then joins :class:`BotUser` for display
fields. For a busy master with ~1k bookings the worst-case row count is
~hundreds of distinct ``bot_user_id`` values — well under any pagination
threshold. No pagination ships in Phase 1; the screen renders all rows
client-side per Tau §4.3 "Mature (50+ customers): groupings + pagination"
(deferred to post-pilot once usage data justifies).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Max
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


# Tunables ----------------------------------------------------------------

AT_RISK_DAYS = 60
"""A customer is flagged ``at_risk`` when their last visit is more than this
many days in the past AND their lifetime visit count is >= ``AT_RISK_MIN_VISITS``.
Per Tau §4.3 "Давно не были" group ("⚠ Возможно, ушла")."""

AT_RISK_MIN_VISITS = 3
"""Threshold for the at-risk flag — only customers with >= 3 historical
visits qualify. Avoids labelling one-off walk-ins as "lost"."""

IS_RETURNING_MIN_VISITS = 2
"""Mirrors :func:`apps.master_api.services.dashboard._is_returning_customer`
semantics — a customer is "returning" once they have a 2nd booking on the
books (per master-mobile §M1 spec)."""

PHONE_MASK_CHARS = "•••"
"""Masking literal — three bullet characters per Tau §4.3 mock
("+7 ••• ••• 14 67")."""


# Helpers -----------------------------------------------------------------


def _mask_phone(raw: str | None) -> str:
    """Mask a phone for read-only display.

    Pattern per Tau §4.3 mock: ``+7 ••• ••• 14 67`` — country prefix + 3
    bullet groups + last 4 visible digits split into two pairs. We
    normalise the input to digits-only first so any storage format works.

    Returns an empty string when input is None / has fewer than 4 digits
    (cold-start rows / GDPR-erased BotUsers). Empty-string return is
    safe for the client — Tau renders it as no phone line at all.
    """

    if not raw:
        return ""
    # Preserve a leading + when present, but otherwise reduce to digits.
    has_plus = raw.lstrip().startswith("+")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 4:
        return ""
    last4 = digits[-4:]
    # Two-pair grouping per Tau mock ("14 67").
    last_pair_a = last4[:2]
    last_pair_b = last4[2:]
    prefix = f"+{digits[0]}" if has_plus else f"{digits[0]}"
    return f"{prefix} {PHONE_MASK_CHARS} {PHONE_MASK_CHARS} {last_pair_a} {last_pair_b}"


def _split_name(raw: str | None) -> str:
    """Return the customer's first display name only (Tau §4.3 card title).

    The booking snapshot stores ``client_name`` as the full name the
    client gave at booking time; for the roster card we surface just the
    first word so the card stays compact. Falls back to "Гость" when
    the snapshot is empty (e.g. unattributed walk-in).
    """

    if not raw:
        return "Гость"
    parts = raw.strip().split()
    if not parts:
        return "Гость"
    return parts[0]


# Public API --------------------------------------------------------------


def list_master_customers(
    *,
    master: CatalogMaster,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Aggregate a roster of customers who've booked with ``master``.

    Returns a list sorted by ``last_visit_at`` DESC (Tau §4.3 Q-MSL-8
    default "По последнему визиту"). Empty list when the master has no
    bookings yet (cold-start tenant) — caller renders Tau's empty-state
    copy in that case.

    Each dict has the shape (all keys always present, never omitted):

    .. code-block:: python

        {
            "bot_user_id": "<uuid>",
            "first_name": "Анна",
            "phone_masked": "+7 ••• ••• 14 67",  # empty string when no phone
            "last_visit_at": "2026-05-20T14:30:00+00:00",  # ISO 8601
            "last_visit_service_name": "маникюр гель-лак",
            "total_visits": 5,
            "is_returning": True,
            "at_risk": False,
        }

    ### Counting rules

    A "visit" is any :class:`BookingRequest` row for this master with
    ``status`` IN (``CONFIRMED``, ``RESCHEDULED``) — both terminal-good
    states. ``CANCELLED`` and the interim ``*_requested`` rows are
    excluded so a customer who cancels every booking doesn't pad the
    roster. ``RESCHEDULED`` IS counted because Tau §4.3 wants to credit
    the customer for showing up the first time even if the visit row
    was later replaced — the new replacement row is also CONFIRMED and
    contributes a separate count, so the customer's "total_visits"
    matches what they (and the master) remember.

    Rows without a linked ``bot_user`` (legacy walk-ins, snapshot-only
    bookings) are SKIPPED — there's no stable identity to aggregate
    against. They remain visible in the booking history view.

    ### Cross-tenant isolation

    Filtered explicitly by ``tenant_id=master.tenant_id`` AND
    ``master_id=master.id`` even though :func:`require_master_init_data`
    already enters ``tenant_scope`` — defence-in-depth so an accidental
    decorator regression can't leak a customer from a sibling salon.
    """

    if now is None:
        now = dj_timezone.now()

    # Status filter — see docstring "Counting rules".
    counted_statuses = (
        BookingRequest.Status.CONFIRMED,
        BookingRequest.Status.RESCHEDULED,
    )

    # Single grouped query: per bot_user_id, count rows + find the most-recent
    # visit. We then load BotUser display fields in a second query keyed by
    # the resulting ids. Two queries total — no N+1 in the row count.
    aggregates = (
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            status__in=counted_statuses,
            bot_user__isnull=False,
        )
        .values("bot_user_id")
        .annotate(
            total_visits=Count("id"),
            last_visit_at=Max("visit_at"),
        )
        .order_by("-last_visit_at")
    )

    aggregates_list = list(aggregates)
    if not aggregates_list:
        return []

    bot_user_ids = [row["bot_user_id"] for row in aggregates_list]
    # all_tenants + explicit tenant filter — defence-in-depth.
    bot_users = {
        bu.id: bu
        for bu in BotUser.all_tenants.filter(
            tenant_id=master.tenant_id,
            id__in=bot_user_ids,
        ).only("id", "display_name", "phone", "client_name")
    }

    # For the "last service name" we need the booking row that matches
    # max(visit_at) per bot_user. The aggregate query lost the row; rather
    # than a correlated subquery (which Postgres optimises but Django ORM
    # makes awkward), do a single ordered fetch and pick the first match
    # per bot_user_id while walking. Per-master booking volume is bounded
    # in the hundreds for Phase 1 pilots.
    last_service_by_user: dict[Any, str] = {}
    last_visit_rows = (
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            status__in=counted_statuses,
            bot_user_id__in=bot_user_ids,
        )
        .order_by("bot_user_id", "-visit_at")
        .values("bot_user_id", "visit_at", "service_name")
    )
    for row in last_visit_rows:
        bu_id = row["bot_user_id"]
        if bu_id in last_service_by_user:
            # We've already seen the most-recent row for this bot_user
            # because the inner ``order_by`` placed it first within the
            # group. Skipping is cheaper than DISTINCT ON.
            continue
        last_service_by_user[bu_id] = row["service_name"] or ""

    at_risk_cutoff = now - timedelta(days=AT_RISK_DAYS)

    out: list[dict[str, Any]] = []
    for agg in aggregates_list:
        bu_id = agg["bot_user_id"]
        bu = bot_users.get(bu_id)
        last_visit_at = agg["last_visit_at"]
        total_visits = int(agg["total_visits"])

        # Display name: prefer BotUser.client_name (what the client typed
        # when introducing themselves / booking), then channel display_name;
        # final fallback is "Гость". We surface the first word only — the
        # roster card stays compact.
        if bu is None:
            # BotUser was soft-deleted (GDPR scrub) but the booking row
            # still has the FK. Skip — there's nothing to display.
            continue
        first_name = _split_name(bu.client_name) if bu.client_name else _split_name(bu.display_name)

        # Mask phone server-side. Phone reveal endpoint with audit event
        # is deferred Tier 2 — see W1 tracking issue.
        phone_masked = _mask_phone(bu.phone)

        is_returning = total_visits >= IS_RETURNING_MIN_VISITS
        at_risk = (
            last_visit_at is not None
            and last_visit_at < at_risk_cutoff
            and total_visits >= AT_RISK_MIN_VISITS
        )

        out.append(
            {
                "bot_user_id": str(bu_id),
                "first_name": first_name,
                "phone_masked": phone_masked,
                "last_visit_at": last_visit_at.isoformat() if last_visit_at else None,
                "last_visit_service_name": last_service_by_user.get(bu_id, ""),
                "total_visits": total_visits,
                "is_returning": is_returning,
                "at_risk": at_risk,
            }
        )

    return out


__all__ = [
    "AT_RISK_DAYS",
    "AT_RISK_MIN_VISITS",
    "IS_RETURNING_MIN_VISITS",
    "PHONE_MASK_CHARS",
    "list_master_customers",
]
