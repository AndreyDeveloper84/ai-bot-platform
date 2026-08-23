"""May we write to this person unprompted? — one implementation (DRF-1307).

Extracted verbatim from ``apps.bookings.followups._consent_blocker``
(DRF-1301, PR #1242). Nothing about the four conditions or their order
changed in the move; ``followups`` now delegates here and its 57 tests
are what proves that.

### Why it moved

DRF-1301 established that ``apps.channels.max.outbound.send_message`` has
no central gate: what the caller did not check, nobody checks. When
DRF-1307 found the same hole in the master-deactivation broadcast, the
choice was to paste the four conditions into a third module or to give
them one home. A third copy is a fourth copy within the month, and the
conditions are exactly the kind that drift apart silently — a blocker
added here and not there produces no error, only a message somebody
should not have received.

**This is deliberately NOT a gate on the delivery boundary.** Putting it
inside ``send_message`` was considered and rejected: that function has
163 production call sites and the overwhelming majority are *reactive*
— a handler answering somebody who just wrote in. A consent check there
would mean a person without ``consent_at`` writes «отмени запись» and
gets silence. The reactive outbound boundary is DRF-1306's subject and
its behaviour-on-trip is an open question for the owner. This module
answers the narrower question its name asks: may we write **first**.

### Proactive vs contract-required — read before adding a caller

``BotUser.proactive_messages_opt_out`` carves reminders out of itself
explicitly (``apps/identity/models.py:207-213``): T-24h / T-2h are
«contract-required dispatch ahead of confirmed bookings, not proactive
в the marketing-engagement sense». That carve-out names reminders and
nothing else, but the reasoning is about the *class* of message, not
about the two tasks that happen to send it.

So this gate answers "may we write unprompted", and a caller sending a
**contract-required** message (the person's confirmed booking was
cancelled, moved, or paid for) must not treat a blocker here as "stay
silent and move on". It means "this channel is closed — reach them
another way, and tell the operator so somebody can". DRF-1307's caller
does exactly that: it returns the blocked recipients to the admin who
pressed the button. Silence would be the worse failure there, and the
gate is not what makes it silent — dropping the result on the floor is.
"""

from __future__ import annotations

from typing import Any

#: Every slug :func:`outreach_blocker` can return. Callers that report
#: per-reason counts iterate this so a new blocker cannot be added here
#: and quietly go uncounted downstream.
OUTREACH_BLOCKER_SLUGS: tuple[str, ...] = (
    "opt_out",
    "deleted",
    "no_consent",
    "consent_withdrawn",
    "consent_unproven",
)


def outreach_blocker(bot_user: Any) -> str | None:
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
    :func:`~apps.consent.services.has_consent`. Callers here are
    cross-tenant system paths with no tenant in scope, and the
    tenant-scoped reader raises there. That is not a workaround: the
    pilot's client bot runs the global path, so the global reader is the
    one that answers for the people these callers actually write to. It
    anchors on ``bot_user``, whose FK already pins exactly one tenant, so
    no cross-tenant row is reachable.

    Two mines this steps around, both measured on the pilot 2026-08-23:

    * ``consent_at`` is a one-way latch. :func:`apps.consent.services.
      withdraw` stamps ``withdrawn_at`` on the record and never touches
      the column; :func:`~apps.consent.services.grant` sets it only when
      it is NULL. Gating on the column alone would keep writing to
      somebody who explicitly withdrew. The authoritative read is the
      active record; the column is a cheap prefilter.
    * ``soft_delete_user()`` scrubs display_name / avatar_url /
      client_name / phone / context but **not** ``chat_id``, so an erased
      person stays reachable and, ungated, stays a recipient.

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

    # Local imports: apps.consent.services imports identity models, and
    # this module is imported from task/service modules at their scope.
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


__all__ = ["OUTREACH_BLOCKER_SLUGS", "outreach_blocker"]
