"""Who may receive a bot-initiated nutrition message (DRF-1285, DRF-1314).

One implementation of the gate, read by both beat tasks and by the dry-run
command, so "who would we write to?" can never be answered differently from
"who did we write to?".

### The gate, in order

The first four conditions are **not implemented here**. They are
:func:`apps.notifications.proactive.consent_blocker` -- the one gate every
surface that writes first shares -- and this module delegates to it before
it asks anything of its own (DRF-1314):

* ``proactive_messages_opt_out`` -- checked **first and unconditionally**.
  It is the person's global veto on the bot writing first; nothing
  downstream can re-enable a message for someone who set it. That ordering
  is the point -- a veto evaluated late is a veto that a future edit can
  accidentally skip. Delegating as the *first* step is what keeps that
  literally true here: no nutrition-specific condition is evaluated ahead
  of it.
* ``deleted_at`` -- a GDPR-erased row is not a recipient.
* ``consent_at`` -- the broad 152-FZ welcome consent.
* an **active** ``ConsentRecord`` for ``personal_data``.

Then two conditions only this surface has:

* ``chat_id`` -- no address, no message. Also applied as a queryset filter
  in :func:`base_queryset`, so in production this per-row check is
  belt-and-braces; it exists for callers that hand :func:`check_common` a
  row they built themselves.
* ``food_scanner_consent_at`` -- the feature-specific 152-FZ consent for the
  nutrition diary surface. Reading someone's food diary back to them,
  unprompted, is processing that data; the same consent that gates writing
  it gates volunteering it.

  Unlike ``consent_at``, this column has **no** ``ConsentRecord`` behind it:
  ``ConsentRecord.ConsentType`` has no food-scanner member, so there is no
  second source to reconcile it against and no withdrawal that could leave
  it stale. The column *is* the record. That is precisely why reading it
  directly is correct and why reading ``consent_at`` directly was not --
  the two look alike and are not alike.

The per-feature opt-in (``daily_report_time`` / ``water_reminders``), which
is OFF for everyone until they ask, is checked in
:mod:`~apps.nutrition_proactive.tasks`, not here.

### Why the first four moved out (DRF-1314)

Until DRF-1314 this module ran its own copy of the gate, and the copy read
``BotUser.consent_at`` **and nothing else**.
:func:`apps.consent.services.withdraw` stamps ``withdrawn_at`` on the
``ConsentRecord`` and deliberately leaves the denormalised column alone
(soft delete on a live row, spec §4), so a person who explicitly withdrew
still read as consenting here.

Measured against the pilot on 2026-08-23: of the twelve reachable
``BotUser`` rows, five had ``consent_at`` set and **four of those five had
withdrawn**. The column-only check therefore admitted four fifths of its
own "consenting" population wrongly. The layer was live behind
``NUTRITION_PROACTIVE_ENABLED``; the flag was the only thing between those
four people and a message.

``tools/lint/consent_column_guard.py`` now refuses new bare reads of
``consent_at`` outside the shared gate and the place consent is stamped, so
a fourth surface cannot re-derive the same mistake.

### Consent is a different question from the opt-out flag

The opt-out flag says "do not write to me first". The consent timestamps say
"you may process this class of my data at all". A proactive nutrition
message needs both, and neither substitutes for the other -- which is why
both are checked rather than collapsed into one boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.identity.models import BotUser
from apps.notifications.proactive import consent_blocker

#: Cap on rows pulled per beat tick. The pilot recipient list is single
#: digits; the cap exists so a future import cannot turn one tick into an
#: unbounded fan-out.
BATCH_LIMIT = 500

#: Every slug :func:`check_common` can return. The first five come from
#: :data:`apps.notifications.proactive.BLOCK_REASONS`; the last two are
#: this surface's own. Enumerated so the dry-run report and the tests can
#: assert against a stable vocabulary instead of scattered literals.
BLOCK_REASONS = (
    "opt_out",
    "deleted",
    "no_consent",
    "consent_withdrawn",
    "consent_unproven",
    "no_chat_id",
    "no_food_consent",
)


@dataclass(frozen=True)
class Blocked:
    """A candidate that will not be written to, and why."""

    bot_user_id: Any
    reason: str


def base_queryset():
    """BotUsers that could conceivably be written to.

    Cross-tenant (``all_tenants``) because the beat is system-level, and
    ``select_related("tenant")`` because :func:`~apps.nutrition_proactive.
    prefs.resolve_timezone` reads the salon timezone as a fallback.

    The opt-out veto is applied here as a queryset filter *and* re-asserted
    per row in :func:`check_common` -- belt and braces on the one condition
    whose failure is a trust break rather than a missed message.

    Consent is deliberately **not** filtered here. The active-record half
    of it is a join against ``ConsentRecord`` whose answer depends on
    ``withdrawn_at`` and on the latest row, and expressing that as a
    queryset filter would be a second implementation of the shared gate --
    the exact duplication DRF-1314 exists to remove. Rows are filtered by
    the cheap columns here and vetted one at a time by
    :func:`check_common`, so there is one answer, not two.
    """
    return (
        BotUser.all_tenants.filter(proactive_messages_opt_out=False)
        .filter(deleted_at__isnull=True)
        .exclude(chat_id="")
        .exclude(chat_id__isnull=True)
        .select_related("tenant")
        .order_by("pk")[:BATCH_LIMIT]
    )


def check_common(bot_user: Any) -> str | None:
    """Return a block reason, or None when the common gate passes.

    The shared gate runs first and whole: it owns opt-out, erasure,
    ``consent_at`` and the active ``ConsentRecord``, and it owns the
    ordering between them. Only the two nutrition-specific conditions are
    asked here, and only after it has passed.

    Costs one ``EXISTS`` per candidate row that gets as far as the consent
    question, and a second one on the failing branch to tell "withdrew"
    apart from "never proved". At ``BATCH_LIMIT`` = 500 that is bounded and
    per-tick; the previous version cost nothing and gave the wrong answer.
    """
    blocked = consent_blocker(bot_user)
    if blocked:
        return blocked
    if not (getattr(bot_user, "chat_id", "") or "").strip():
        return "no_chat_id"
    if getattr(bot_user, "food_scanner_consent_at", None) is None:
        return "no_food_consent"
    return None
