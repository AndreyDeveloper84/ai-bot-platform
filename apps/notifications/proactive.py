"""May we write to this person first, and may this text go out? (DRF-1307)

One implementation of the bot-initiated-message gate, imported by every
surface that writes to somebody who did not just write to us.

### Why this module exists

Before DRF-1307 the same gate existed twice, in two shapes that had
already drifted:

* :mod:`apps.bookings.followups` (DRF-1301) — the full four-condition
  version: opt-out, erasure, ``consent_at``, and an **active**
  ``ConsentRecord``.
* :mod:`apps.nutrition_proactive.selection` (DRF-1285) — opt-out,
  erasure, ``chat_id``, ``consent_at``, ``food_scanner_consent_at``.
  It never reads ``ConsentRecord``, so it still writes to somebody who
  withdrew.

Measured on the pilot 2026-08-23: of the twelve reachable ``BotUser``
rows, five have ``consent_at`` set and **four of those five have
withdrawn their ``personal_data`` consent**. A gate that trusts the
denormalised column alone therefore lets four fifths of its "consenting"
population through wrongly. That is not a theoretical divergence between
the two copies — it is the live pilot.

:mod:`apps.admin_api.services.master_deactivation` was about to become
the third copy. It is the caller this module was extracted for.

### What is NOT gated here, and why not

This is deliberately **not** a gate inside
:func:`apps.channels.max.outbound.send_message`. That function has 163
call sites and the overwhelming majority are *replies* — a person wrote
to the bot and is waiting. Two things break if the consent gate moves
there:

1. ``send_message(chat_id=...)`` has no ``BotUser`` in hand, and
   ``chat_id`` does not resolve back to one uniquely:
   ``soft_delete_user()`` leaves ``chat_id`` populated on erased rows,
   and the same human can hold rows in several tenants.
2. The welcome flow — :mod:`apps.skills.welcome` — is what *asks* for
   152-ФЗ consent, and it must send that question to somebody whose
   ``consent_at`` is by definition ``None``. A consent gate at the
   transport would make consent unobtainable.

The seam that matters is "the bot speaks first", not "bytes leave the
process". That set is small and enumerable; this module is its gate.

### The four conditions, in order

Order is load-bearing. ``proactive_messages_opt_out`` is evaluated
first and unconditionally, because it is the one veto whose failure is
a trust break rather than a missed message — a veto evaluated late is a
veto a future edit can skip.

* ``proactive_messages_opt_out`` — the person's global "do not write to
  me first".
* ``deleted_at`` — a GDPR erasure request is the strongest withdrawal
  there is. ``soft_delete_user()`` scrubs display_name / avatar_url /
  client_name / phone / context but **not** ``chat_id``, so an erased
  person stays reachable and, without this check, stays a recipient.
  One such row exists on the pilot today.
* ``consent_at IS NULL`` — never consented under 152-ФЗ.
* an active ``ConsentRecord`` for ``PERSONAL_DATA``. ``consent_at`` is a
  denormalised stamp and :func:`apps.consent.services.withdraw` never
  clears it — it stamps ``withdrawn_at`` on the record and leaves the
  ``BotUser`` column alone. Gating on ``consent_at`` alone keeps
  messaging somebody who explicitly withdrew.

Reads use :func:`~apps.consent.services.has_global_consent`, not
:func:`~apps.consent.services.has_consent`. The tenant-scoped reader
raises when no tenant is in scope (system beats) and, when a tenant *is*
in scope, filters ``ConsentRecord`` by that tenant — so a grant recorded
on the global marketplace path is invisible to a salon-scoped caller and
reads as "no consent". The global reader anchors on ``bot_user``, whose
FK already pins exactly one tenant, so no cross-tenant row is reachable
either way. Verified against the pilot for DRF-1307: the salon path and
the global path return the same answer for every row there.

Distinguishing "never proved" from "withdrawn" costs a second query on
the failing branch only. It is worth it: they are different operator
problems. ``consent_unproven`` is a data-provenance gap left by grants
predating #1074, which stamped ``consent_at`` and the record atomically.
``consent_withdrawn`` is somebody who said no.

### The text check is separate from the recipient check

:func:`vet_outbound` answers a different question — not "may we write to
them" but "may we say this". Kept as a second function because the
answers have different consequences: a blocked recipient is one person
not written to, a blocked text is a message nobody should get.
"""

from __future__ import annotations

from typing import Any

#: Slugs :func:`consent_blocker` can return. Enumerated so callers can
#: assert on them without importing string literals, and so a dry run can
#: report a stable vocabulary.
BLOCK_REASONS = (
    "opt_out",
    "deleted",
    "no_consent",
    "consent_withdrawn",
    "consent_unproven",
)


def consent_blocker(bot_user: Any) -> str | None:
    """May we write to this person unprompted at all?

    Returns a reason slug when we may not, ``None`` when we may. Each
    condition gets its own slug so a dry run tells the operator *which*
    one fired rather than a single undifferentiated "blocked".

    Accepts anything with the four attributes — the argument is typed
    ``Any`` so callers holding a lazily-loaded FK, a deferred row, or a
    test double do not have to import the model.
    """

    if getattr(bot_user, "proactive_messages_opt_out", False):
        return "opt_out"

    if getattr(bot_user, "deleted_at", None) is not None:
        return "deleted"

    if getattr(bot_user, "consent_at", None) is None:
        return "no_consent"

    # Local imports: apps.consent imports identity models, and this module
    # is imported from task/service modules at module scope.
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


def vet_outbound(text: str) -> tuple[str, str | None]:
    """Run a bot-initiated message past the outbound safety check.

    Returns ``(text, None)`` when it may go out and ``("", reason)``
    when it may not.

    One deliberate difference from the conversational pipeline, taken
    from DRF-1285/1301 and re-argued rather than copied: a blocked reply
    there is REPLACED with ``REPLACEMENT_TEXT`` («тут нужен человек…»),
    which is right for an answer somebody is waiting for. Here the
    person asked nothing, so «тут нужен человек» is a non-sequitur that
    would puzzle them and hand an administrator a conversation with no
    question in it. A hit means **send nothing at all**, and the caller
    is expected to leave a trace an operator can act on.
    """

    from apps.orchestrator.safety.outbound import evaluate_outbound

    verdict = evaluate_outbound(text)
    if verdict.blocked:
        return "", "outbound_safety_" + ("_".join(verdict.categories) or "hit")
    return text, None


__all__ = ["BLOCK_REASONS", "consent_blocker", "vet_outbound"]
