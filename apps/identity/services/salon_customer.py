"""The §2 rule: who is LINKED to the client contour, who is a SHADOW (DRF-1700, S2-1).

Owner decision 11.09 §2, migration rules, verbatim in order:

    1. Сопоставлять записи только по подтверждённому телефону, MAX ID или
       существующей identity-связи.
    3. Несовпавшим создавать ограниченный SHADOW-профиль с source = SALON_ASSISTANT.
    7. Объединение по имени запрещено.

This module is that rule and nothing else. It reads one person — one
``(channel, channel_user_id)`` — across every salon shell and answers with
the FIRST criterion that holds, in the order the owner wrote them (MAX ID
first because it is the one the pilot actually has: measured 11.09, phones
are empty for everyone). It never reads a name: ``test_salon_customer.py``
holds that on the function's tree, and a substitution probe showed the
rule would otherwise be a coincidence.

### Two audiences of the answer

The **classifier** (:func:`classify`) is pure and read-only; the operator's
command prints its breakdown without writing. The **writer**
(:func:`apply_classification`) stamps the shells — status, source, moment —
and is reached only through ``--apply``, which is the owner's word: the
pilot's 14 salon people include 7 fixtures (S2-0), and whether fixtures
are migrated or cleared is not this module's call.

### What LINKED and SHADOW mean on a shell

A salon shell (any tenant but ``global_bot``) is the «SalonCustomer» of §2.
LINKED: the person is matchable to the client contour; the shell may be
joined to the person's Ayla profile. SHADOW: the person exists only through
this salon; §2.4 — no goals, nutrition, health or cross-salon memory —
which slice S2-2 enforces at the readers. The client contour's own shells
(``global_bot``) are LINKED with ``source=CLIENT_BOT`` by construction:
they ARE the profile's side of the relationship.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from django.db.models import Q

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser

MatchBy = str  # "MAX-ID" | "identity" | "phone" | "shadow"

MATCH_MAX_ID: MatchBy = "MAX-ID"
MATCH_IDENTITY: MatchBy = "identity"
MATCH_PHONE: MatchBy = "phone"
MATCH_SHADOW: MatchBy = "shadow"


@dataclass(frozen=True)
class Classification:
    channel: str
    channel_user_id: str
    match_by: MatchBy
    salon_shell_ids: tuple
    global_shell_ids: tuple

    @property
    def status(self) -> str:
        return (
            BotUser.CustomerStatus.SHADOW
            if self.match_by == MATCH_SHADOW
            else BotUser.CustomerStatus.LINKED
        )


def classify(channel: str, channel_user_id: str) -> Classification:
    """Apply §2's rule to one person. Reads only; never a name."""
    shells = list(
        BotUser.all_tenants.filter(channel=channel, channel_user_id=channel_user_id)
        .select_related("tenant")
        .order_by("first_seen", "id")
    )
    global_ids = tuple(s.id for s in shells if s.tenant.slug == GLOBAL_BOT_TENANT_SLUG)
    salon_ids = tuple(s.id for s in shells if s.tenant.slug != GLOBAL_BOT_TENANT_SLUG)

    # §2.1, in the owner's order. Each criterion is a fact about the person's
    # identifiers, never about how they were called.
    if global_ids:
        match_by = MATCH_MAX_ID
    elif any(s.ayla_user_id is not None for s in shells):
        match_by = MATCH_IDENTITY
    elif any((s.phone or "").strip() for s in shells):
        match_by = MATCH_PHONE
    else:
        match_by = MATCH_SHADOW
    return Classification(channel, channel_user_id, match_by, salon_ids, global_ids)


def salon_people() -> list[tuple[str, str]]:
    """Every distinct person with at least one salon shell, oldest first."""
    seen: dict[tuple[str, str], None] = {}
    for channel, cid in (
        BotUser.all_tenants.exclude(tenant__slug=GLOBAL_BOT_TENANT_SLUG)
        .order_by("first_seen", "id")
        .values_list("channel", "channel_user_id")
    ):
        seen.setdefault((channel, cid), None)
    return list(seen)


def apply_classification(c: Classification, *, now: datetime | None = None) -> int:
    """Stamp the person's shells. Returns rows written.

    Salon shells take the classification; global shells are LINKED /
    CLIENT_BOT by construction. Only UNRESOLVED shells are written: a status
    that was decided before is not re-decided by a re-run — a second run is
    a report, not a second decision.
    """
    moment = now or datetime.now(UTC)
    written = 0
    if c.salon_shell_ids:
        written += BotUser.all_tenants.filter(
            id__in=c.salon_shell_ids, customer_status=BotUser.CustomerStatus.UNRESOLVED
        ).update(
            customer_status=c.status,
            customer_source=BotUser.CustomerSource.SALON_ASSISTANT,
            customer_status_at=moment,
        )
    if c.global_shell_ids:
        written += BotUser.all_tenants.filter(
            id__in=c.global_shell_ids, customer_status=BotUser.CustomerStatus.UNRESOLVED
        ).update(
            customer_status=BotUser.CustomerStatus.LINKED,
            customer_source=BotUser.CustomerSource.CLIENT_BOT,
            customer_status_at=moment,
        )
    return written


def unresolved_count() -> int:
    return BotUser.all_tenants.filter(Q(customer_status=BotUser.CustomerStatus.UNRESOLVED)).count()


__all__ = [
    "MATCH_IDENTITY",
    "MATCH_MAX_ID",
    "MATCH_PHONE",
    "MATCH_SHADOW",
    "Classification",
    "apply_classification",
    "classify",
    "salon_people",
    "unresolved_count",
]
