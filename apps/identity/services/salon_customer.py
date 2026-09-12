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


def advance_to_linked(channel: str, channel_user_id: str, *, now: datetime | None = None) -> int:
    """An identity link was just written: the person is LINKED by §2.1(3).

    Every shell of the person that is still SHADOW or UNRESOLVED becomes
    LINKED; the source is kept where it was set and filled where it was
    not (salon shells → SALON_ASSISTANT, client contour → CLIENT_BOT).
    A shell already LINKED is left alone. Returns rows written.
    """
    moment = now or datetime.now(UTC)
    pending = BotUser.all_tenants.filter(channel=channel, channel_user_id=channel_user_id).exclude(
        customer_status=BotUser.CustomerStatus.LINKED
    )
    written = 0
    for shell in pending.select_related("tenant"):
        is_client = shell.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        source = shell.customer_source or (
            BotUser.CustomerSource.CLIENT_BOT
            if is_client
            else BotUser.CustomerSource.SALON_ASSISTANT
        )
        written += BotUser.all_tenants.filter(pk=shell.pk).update(
            customer_status=BotUser.CustomerStatus.LINKED,
            customer_source=source,
            customer_status_at=moment,
        )
    return written


def client_contour_only() -> list[tuple[str, str]]:
    """People with a ``global_bot`` shell and NO salon shell, still UNRESOLVED.

    Outside §2's fourteen — they never met a salon — and therefore outside
    :func:`salon_people`. Their standing is nonetheless fixed by construction
    (the client contour IS the profile's side), and a status that stays
    UNRESOLVED for them forever would make ``unresolved_count`` lie about the
    one thing it exists to say. Found on the pilot 12.09: four such shells
    (900284–900287) after the first ``--apply``.
    """
    salon = {tuple(p) for p in salon_people()}
    out: list[tuple[str, str]] = []
    for channel, cid in (
        BotUser.all_tenants.filter(
            tenant__slug=GLOBAL_BOT_TENANT_SLUG, customer_status=BotUser.CustomerStatus.UNRESOLVED
        )
        .order_by("first_seen", "id")
        .values_list("channel", "channel_user_id")
    ):
        if (channel, cid) not in salon and (channel, cid) not in out:
            out.append((channel, cid))
    return out


def stamp_client_contour(channel: str, channel_user_id: str, *, now: datetime | None = None) -> int:
    """LINKED / CLIENT_BOT on the person's global shells that are still UNRESOLVED."""
    moment = now or datetime.now(UTC)
    return BotUser.all_tenants.filter(
        tenant__slug=GLOBAL_BOT_TENANT_SLUG,
        channel=channel,
        channel_user_id=channel_user_id,
        customer_status=BotUser.CustomerStatus.UNRESOLVED,
    ).update(
        customer_status=BotUser.CustomerStatus.LINKED,
        customer_source=BotUser.CustomerSource.CLIENT_BOT,
        customer_status_at=moment,
    )


def unresolved_count() -> int:
    return BotUser.all_tenants.filter(Q(customer_status=BotUser.CustomerStatus.UNRESOLVED)).count()


__all__ = [
    "MATCH_IDENTITY",
    "MATCH_MAX_ID",
    "MATCH_PHONE",
    "MATCH_SHADOW",
    "Classification",
    "advance_to_linked",
    "apply_classification",
    "classify",
    "client_contour_only",
    "salon_people",
    "stamp_client_contour",
    "unresolved_count",
]
