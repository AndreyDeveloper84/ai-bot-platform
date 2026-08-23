"""C5 — customer personal-data export/delete aggregation (152-ФЗ, pilot).

Implements the bot-side half of ``PILOT_CONTRACTS_2026-08-15`` §6:

* **Export** — one JSON aggregating the Ayla export (verbatim upstream
  payload, C5.1) + bot-side green ``MemoryEntry`` rows + ``ConsentRecord``
  history for the person (cross-tenant — the person is keyed by
  ``ayla_user_id`` and may have a ``BotUser`` per tenant).
* **Delete** — the cascade: Ayla personal-data delete (C5.2 upstream),
  bot ``MemoryEntry`` erasure (immediate green soft-delete +
  ``forget_all`` UPC tombstone), consent withdraw cascade
  (:func:`apps.consent.services.withdraw_personal_data_for_bot_users` —
  keyed on local identity, see below), and bot-side profile PII erasure.

### Contract obligations honoured

* **Server-confirmed** — the view requires the ``DELETE_CONFIRMATION_TOKEN``
  primitive in the request body before any of this runs. A client-side
  confirmation sheet is not a confirmation (DRF-956 / T-05 ruling §1-2).
* **Idempotent delete** — every step is naturally re-runnable: upstream
  404 counts as already-deleted, green soft-delete skips tombstoned
  rows, ``forget_all``/consent withdrawal are no-ops on repeat.
  A repeated confirmed DELETE therefore returns the same success.
* **No success for work not done** — a step reports green only when it
  actually erased, or when the absence of state is *provable* locally.
  ``ayla_delete`` with no linkage is a **failure**, not an idempotent
  success: we have no id to address Ayla with, so we can neither perform
  the mandatory remote step nor establish there is nothing to erase
  (ruling §3-4, §6). Consents are keyed on the local ``bot_user`` FK and
  are therefore withdrawn regardless of linkage (ruling §5).
* **Audit without personal values** — both operations append an audit
  row naming the actor, timestamp and *scope* of the action; fact
  values, phones and names never enter the audit payload.
* **Honest partials** — a failed cascade step is reported, not hidden:
  the view maps ``all_ok=False`` to 502 so the miniapp can offer a retry
  (already-done steps no-op on that retry).
* **Pilot scope (C5.2)** — personal context + memory + consents +
  the bot-side profile identifiers on ``BotUser`` + the staff
  assistant's threads (DRF-1276: an employee is a ``BotUser`` like
  anyone else, and what they dictated to the assistant is personal
  data too).
  Transactional records (bookings, payments) follow statutory retention;
  their anonymisation is explicitly post-pilot.

### Erase-in-place, don't drop the row (DRF-956 / T-05)

The ``BotUser`` row is a *technical shell*, not personal data: it is the
channel routing key and it is referenced ``on_delete=PROTECT`` by
``observability.AIRequestMetric``, ``handoff.AdminTask`` and
``tenancy.StaffAssignment``, and ``on_delete=SET_NULL`` by the statutory
transactional records (``booking.BookingRequest``). Physically deleting it
either raises ``ProtectedError`` or destroys the audit/metric trail that
152-ФЗ itself expects us to keep — so the shell is retained and the
*identifying values* on it are blanked instead (:data:`_PII_FIELDS`).

``ayla_user_id`` is deliberately **retained**: it is the stable key of the
person's memory (``UserPersonalContext.user_id``), so it is what the
``forget_all`` tombstone is recorded against. Unlinking it would orphan
that tombstone — the next turn would mint a fresh UPC and the erasure
would silently undo itself — and would make a repeated DELETE report
``not_linked`` success without ever re-reaching Ayla.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.consent.models import ConsentRecord
from apps.consent.services import withdraw_personal_data_for_bot_users
from apps.identity.models import BotUser
from apps.identity.services.memory_deleter import (
    request_forget_all,
    soft_delete_green_entries,
)
from apps.identity.services.memory_key_policy import select_current_facts
from apps.identity.services.memory_reader import read_green_entries
from apps.integrations.ayla.personal_context_client import (
    PersonalContextError,
    PersonalContextHttpClient,
    PersonalContextNotFoundError,
)

logger = logging.getLogger(__name__)


class PrivacyUpstreamError(Exception):
    """Ayla-side export/delete failed — the view maps this to 502."""


class PrivacyIdentityConflictError(Exception):
    """The person's shells carry two or more distinct ayla_user_ids.

    Export (and delete) must not guess which upstream account the caller
    is — picking one could return or destroy a stranger's data.
    """


@dataclass(frozen=True)
class DeleteStep:
    """One cascade step outcome. ``detail`` is a slug, never a value."""

    step: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class DeleteCascadeResult:
    steps: tuple[DeleteStep, ...] = field(default_factory=tuple)

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def failed_steps(self) -> list[str]:
        return [s.step for s in self.steps if not s.ok]


def _resolve_ayla_user_id(bot_user: BotUser) -> uuid.UUID | None:
    raw = getattr(bot_user, "ayla_user_id", None)
    if not raw:
        return None
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


def _has_channel_identity(bot_user: BotUser) -> bool:
    """Is ``(channel, channel_user_id)`` a usable identity key on this row?

    A blank ``channel_user_id`` is NOT an identity — matching on it would
    select every *other* shell that also has a blank one, i.e. unrelated
    people. The column is a ``CharField`` with no blank guard, so this is
    reachable from any code path that creates a shell without it.
    """
    return bool((bot_user.channel or "").strip()) and bool((bot_user.channel_user_id or "").strip())


def _channel_sibling_ids(bot_user: BotUser) -> set[uuid.UUID]:
    """Shell ids sharing this channel account. Empty when the key is unusable.

    Fail-closed: with no usable channel identity we return nothing rather
    than a set that would sweep in strangers. The caller still operates on
    the authenticated row itself, which is provably the right person.
    """
    if not _has_channel_identity(bot_user):
        logger.warning(
            "identity.privacy.no_channel_identity bot_user=%s — blank channel/"
            "channel_user_id, cross-shell resolution skipped (fail-closed)",
            bot_user.id,
        )
        return set()
    return set(
        BotUser.all_tenants.filter(
            channel=bot_user.channel, channel_user_id=bot_user.channel_user_id
        ).values_list("id", flat=True)
    )


@dataclass(frozen=True)
class _PersonLink:
    """Outcome of resolving the person's canonical Ayla id across shells."""

    ayla_user_id: uuid.UUID | None = None
    #: Two or more *distinct* non-null ids among this person's shells. The
    #: identity graph disagrees with itself, so we cannot say whose data an
    #: upstream erasure would destroy.
    conflict: bool = False


def _resolve_person_link(bot_user: BotUser) -> _PersonLink:
    """Resolve the person's Ayla id across all their shells, deterministically.

    Reading it off the requesting row alone is wrong: the only writer,
    :func:`~apps.identity.services.resolver.resolve_or_create_global_bot_user`,
    stamps it on the ``global_bot`` sentinel shell, while the Mini App
    request resolves the ``MAX_BOT_TENANT_SLUG`` shell. So a linked person
    looks unlinked from the Mini App side — and memory, which is keyed on
    this id, would be declared "no state" while it is very much alive.

    Semantics (fail-closed on disagreement):

    * 0 distinct linked ids → unlinked.
    * exactly 1 → that is the person.
    * 2+ distinct → identity inconsistency. We do **not** pick one; an
      arbitrary ``.first()`` would delete a stranger's upstream account on
      whichever row the database happened to return first.
    """
    candidates: set[uuid.UUID] = set()
    own = _resolve_ayla_user_id(bot_user)
    if own is not None:
        candidates.add(own)

    sibling_ids = _channel_sibling_ids(bot_user)
    if sibling_ids:
        for raw in BotUser.all_tenants.filter(
            id__in=sibling_ids, ayla_user_id__isnull=False
        ).values_list("ayla_user_id", flat=True):
            candidates.add(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))

    if len(candidates) > 1:
        logger.error(
            "identity.privacy.identity_conflict bot_user=%s distinct_ayla_ids=%d — "
            "refusing destructive upstream/memory operations (fail-closed)",
            bot_user.id,
            len(candidates),  # count only, never the ids themselves
        )
        return _PersonLink(conflict=True)

    if not candidates:
        # DRF-1035. A blank link used to mean «upstream is unaddressable»:
        # export silently omitted the Ayla section and delete reported
        # `not_linked`. That is not the same as «there is nothing upstream» —
        # other bot surfaces (the food scanner and the booking-lookup read)
        # already send X-External-User-ID, which makes Ayla lazily create the
        # proxy and accrete real personal data against it, all without ever
        # populating this field. Those food logs were therefore reachable by
        # neither export nor erasure.
        #
        # Exercising a 152-ФЗ right is an identity-dependent action, so we
        # resolve here — one call on the boundary both export and delete share.
        # Resolution is read-only for an already-existing proxy (the common
        # case); when none exists it creates an empty, PII-free row, which is a
        # far better outcome than reporting an erasure we never attempted.
        from apps.identity.services.ayla_link import ensure_ayla_link

        resolved = ensure_ayla_link(bot_user, trigger="personal_data")
        if resolved is not None:
            return _PersonLink(ayla_user_id=resolved)

    return _PersonLink(ayla_user_id=next(iter(candidates), None))


def _bot_user_ids_for(ayla_user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every BotUser of the person across tenants (memory is global)."""
    return list(BotUser.all_tenants.filter(ayla_user_id=ayla_user_id).values_list("id", flat=True))


# The person's own identifying values held on the BotUser shell. Every one
# is ``blank=True, default=""`` on the model, so the empty string is the
# schema-sanctioned "no value" — this erasure needs no migration.
#
# Deliberately NOT in this set:
#   ``channel_user_id`` / ``chat_id`` — the channel routing key of the
#     retained shell; blanking them breaks the natural key
#     ``(tenant, channel, channel_user_id)`` and orphans the row.
#   ``ayla_user_id`` — the memory tombstone key (see module docstring).
#
# ``context`` is blanked alongside these (it is ``default=dict``), for
# parity with the legacy confirmed path.
_PII_FIELDS: tuple[str, ...] = ("phone", "display_name", "client_name", "avatar_url")


def _person_shell_ids(bot_user: BotUser, link: _PersonLink) -> set[uuid.UUID]:
    """Every BotUser shell that is provably the same human as ``bot_user``.

    Two keys, because neither alone is sufficient:

    * ``ayla_user_id`` — the canonical person key, but it is **NULL on
      most production rows today**. Nothing writes it: the only writer is
      :func:`~apps.identity.services.resolver.resolve_or_create_global_bot_user`
      via an explicit argument, and no production call site passes one
      (``apps/channels/max/handler.py`` passes ``chat_id`` only); every
      eventbus consumer merely *filters* on it.
    * ``(channel, channel_user_id)`` — the channel account. This is what
      actually links a person's shells in the pilot: the Mini App resolves
      the shell under ``MAX_BOT_TENANT_SLUG`` while the chat resolves the
      one under the ``global_bot`` sentinel tenant, and those are two rows
      by ``unique_together (tenant, channel, channel_user_id)``.

    Keyed on ``ayla_user_id`` alone this erase would silently miss the
    user's other shell — i.e. it would report success while leaving the
    phone readable on the row the bot actually talks to.

    **Always fail-closed to the authenticated row.** With a blank channel
    identity, or with an inconsistent identity graph, there is no proven
    person key — so we narrow to ``bot_user`` itself rather than fanning a
    destructive operation out over rows that might belong to someone else.
    """
    ids = {bot_user.id}
    if link.conflict:
        return ids
    if link.ayla_user_id is not None:
        ids.update(_bot_user_ids_for(link.ayla_user_id))
    ids.update(_channel_sibling_ids(bot_user))
    return ids


def _erase_bot_user_pii(bot_user: BotUser, link: _PersonLink) -> None:
    """Blank the person's identifiers on every shell they own + drop prefs.

    Cross-tenant on purpose — the same person may hold a ``BotUser`` per
    tenant and the phone is stored on each of them, so erasing only the
    requesting tenant's row would leave the number readable elsewhere.

    ``UserPreferences`` goes too, matching what the legacy confirmed path
    (:func:`apps.identity.services.profile.soft_delete_user`) already did:
    ``allergies`` is free-text health data and ``birthday_date`` is a
    direct identifier, and the delete sheet promises the customer that
    their «персональные настройки» are removed. ``get_profile`` recreates
    a default row on the next read, so nothing downstream breaks.
    """
    from apps.identity.models import UserPreferences

    ids = _person_shell_ids(bot_user, link)

    with transaction.atomic():
        BotUser.all_tenants.filter(id__in=ids).update(context={}, **dict.fromkeys(_PII_FIELDS, ""))
        UserPreferences.all_tenants.filter(bot_user_id__in=ids).delete()

    # Keep the caller's in-memory instance consistent with the row — the
    # view must never render a value we just erased.
    for field_name in _PII_FIELDS:
        setattr(bot_user, field_name, "")
    bot_user.context = {}


def _erase_staff_assistant(bot_user: BotUser, link: _PersonLink) -> None:
    """Erase the person's staff-assistant dialogue (DRF-1276).

    Step 0 of DRF-1061 introduced ``StaffAssistantThread`` /
    ``StaffAssistantMessage`` without teaching this cascade about them, so
    free text an employee dictated to the salon assistant survived
    «удалите мои данные» verbatim. A staff member is a ``BotUser`` like
    any other, and a *former* one (access revoked via DRF-1227) keeps the
    same 152-ФЗ right.

    Erase-in-place, same posture as the ``BotUser`` shell: message
    ``content`` is blanked, the rows themselves stay — their telemetry
    (tokens, cost) feeds finance reconciliation exactly like the AiDraft
    metadata the draft purge keeps. The thread is soft-deleted via the
    same flag pair :meth:`StaffAssistantThread.mark_deleted` sets, so a
    person who comes back starts a fresh thread instead of resuming a
    ghost — and so the partial-unique constraint lets them.

    Idempotent: the ``deleted_at__isnull=True`` filter makes a repeated
    confirmed DELETE a no-op here.
    """
    from apps.conversations.models import StaffAssistantMessage, StaffAssistantThread

    ids = _person_shell_ids(bot_user, link)
    threads = StaffAssistantThread.all_tenants.filter(bot_user_id__in=ids, deleted_at__isnull=True)
    with transaction.atomic():
        StaffAssistantMessage.all_tenants.filter(thread__in=threads).update(content="")
        threads.update(is_active=False, deleted_at=timezone.now())


# ---------------------------------------------------------------------------
# Export (C5.1)
# ---------------------------------------------------------------------------


def export_personal_data(
    bot_user: BotUser,
    *,
    client: PersonalContextHttpClient | None = None,
) -> dict[str, Any]:
    """Aggregate the person's export payload.

    Raises:
        PrivacyIdentityConflictError: when the person's shells carry two
            or more distinct ``ayla_user_id`` values.
        PrivacyUpstreamError: when the Ayla leg fails (an export silently
            missing its Ayla half would be a compliance lie).

    Bot-side sections are always present; the ``ayla`` section is ``None``
    when the user has no Ayla link yet (nothing exists upstream).
    """
    link = _resolve_person_link(bot_user)

    if link.conflict:
        logger.error(
            "identity.privacy.export_identity_conflict bot_user=%s — "
            "refusing export across ambiguous shells (fail-closed)",
            bot_user.id,
        )
        raise PrivacyIdentityConflictError(
            "person identity conflict: cannot determine canonical subject"
        )

    ayla_user_id = link.ayla_user_id

    ayla_section: dict[str, Any] | None = None
    if ayla_user_id is not None:
        owns = client is None
        client = client or PersonalContextHttpClient()
        try:
            ayla_section = client.get_personal_data_export(ayla_user_id=str(ayla_user_id))
        except PersonalContextError as exc:
            raise PrivacyUpstreamError(f"ayla export failed: {exc}") from exc
        finally:
            if owns:
                client.close()

    memory_section: list[dict[str, Any]] = []
    if ayla_user_id is not None:
        # DRF-1262 — the export stays COMPLETE and becomes UNAMBIGUOUS.
        #
        # The write path keeps history: a changed fact («веган» → «кето»)
        # lands as a new live row and the old one stays live, so two
        # mutually exclusive facts sit in this list at once. The other read
        # surfaces resolve that with `read_current_view` and show one value.
        # This one deliberately does NOT filter, and here is why:
        #
        # 152-ФЗ ст. 14 gives the subject (and the regulator) the composition
        # of the data actually PROCESSED. A superseded row is still stored,
        # still inside the retention window and still in scope for erasure —
        # dropping it would under-report what we hold, which is the worse
        # failure for a legal document. But handing over two contradicting
        # facts with nothing to tell them apart answers the regulator wrongly
        # in the other direction: it hides that the system itself uses only
        # one of them.
        #
        # So: every live row is exported, each carrying `is_current` (does
        # this row survive the key policy the prompt reads through) and its
        # lifecycle `status`. Complete, and self-explaining.
        entries = read_green_entries(ayla_user_id)
        current_ids = {entry.id for entry in select_current_facts(entries)}
        memory_section = [
            {
                "id": str(entry.id),
                "kind": entry.kind,
                "source": entry.source,
                "content": entry.content if isinstance(entry.content, dict) else {},
                "last_inferred_at": entry.last_inferred_at.isoformat()
                if entry.last_inferred_at
                else None,
                "created_at": entry.created_at.isoformat(),
                "status": entry.status,
                "is_current": entry.id in current_ids,
            }
            for entry in entries
        ]

    shell_ids = _person_shell_ids(bot_user, link)
    consents_qs = ConsentRecord.all_tenants.filter(bot_user_id__in=shell_ids).order_by(
        "captured_at"
    )
    consents_section = [
        {
            "consent_type": row.consent_type,
            "granted": row.granted,
            "document_version": row.document_version,
            "source": row.source,
            "captured_at": row.captured_at.isoformat(),
            "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
        }
        for row in consents_qs
    ]

    write_audit(
        "privacy.personal_data_exported",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "actor": "customer",
            "scope": ["ayla_export", "memory_green", "consents"],
        },
    )

    return {
        "generated_at": timezone.now().isoformat(),
        "subject": {
            "ayla_user_id": str(ayla_user_id) if ayla_user_id else None,
        },
        "ayla": ayla_section,
        "memory": memory_section,
        "consents": consents_section,
    }


# ---------------------------------------------------------------------------
# Delete (C5.2)
# ---------------------------------------------------------------------------


def delete_personal_data(
    bot_user: BotUser,
    *,
    client: PersonalContextHttpClient | None = None,
) -> DeleteCascadeResult:
    """Run the C5 delete cascade for the person. Every step is
    idempotent; per-step outcomes are reported, never hidden."""
    # Person-level, not row-level — see _resolve_person_link. A row-level
    # read makes a linked person look unlinked from the Mini App shell,
    # which would report their live memory as "no state".
    link = _resolve_person_link(bot_user)
    ayla_user_id = link.ayla_user_id
    steps: list[DeleteStep] = []

    # Step 1 — Ayla personal-data delete (upstream, C5.2).
    if link.conflict:
        # The person's shells carry two or more distinct ayla_user_ids. We
        # will not guess which one this caller is: deleting the wrong
        # upstream account is unrecoverable and would destroy a third
        # party's data. Fail closed and report it (ruling §3).
        steps.append(DeleteStep("ayla_delete", False, "identity_conflict"))
    elif ayla_user_id is None:
        # NOT a success. Without the linkage we have no id to address Ayla
        # with, so we cannot perform the mandatory remote step *and* cannot
        # establish that there is nothing there to delete. Reporting this
        # green would be a false "deleted" (DRF-956 / T-05 ruling §4+§6).
        logger.warning(
            "identity.privacy.ayla_delete_unaddressable bot_user=%s — "
            "ayla_user_id is NULL, upstream erasure not attempted",
            bot_user.id,
        )
        steps.append(DeleteStep("ayla_delete", False, "not_linked"))
    else:
        owns = client is None
        client = client or PersonalContextHttpClient()
        try:
            client.delete_personal_data(ayla_user_id=str(ayla_user_id))
            steps.append(DeleteStep("ayla_delete", True))
        except PersonalContextNotFoundError:
            # Already gone upstream — idempotent success.
            steps.append(DeleteStep("ayla_delete", True, "already_deleted"))
        except PersonalContextError:
            logger.exception("identity.privacy.ayla_delete_failed")
            steps.append(DeleteStep("ayla_delete", False))
        finally:
            if owns:
                client.close()

    # Step 2 — bot memory. Memory is keyed on ``ayla_user_id``
    # (``UserPersonalContext.user_id`` / ``MemoryEntry.user_id``) and cannot
    # be written without one, so "no linkage" here provably means "no state
    # to erase" — the one case where reporting green is truthful (ruling §4).
    if link.conflict:
        # Memory is keyed on ayla_user_id and we have more than one
        # candidate — soft-deleting the wrong person's entries is exactly
        # the harm this fail-closed branch exists to prevent.
        steps.append(DeleteStep("memory_delete", False, "identity_conflict"))
    elif ayla_user_id is None:
        steps.append(DeleteStep("memory_delete", True, "no_state"))
    else:
        try:
            live_green_ids = [e.id for e in read_green_entries(ayla_user_id)]
            soft_delete_green_entries(ayla_user_id, live_green_ids)
            request_forget_all(ayla_user_id)
            steps.append(DeleteStep("memory_delete", True))
        except Exception:  # noqa: BLE001 — per-step isolation, reported below
            logger.exception("identity.privacy.memory_delete_failed")
            steps.append(DeleteStep("memory_delete", False))

    # Step 3 — consents. ConsentRecord hangs off the ``bot_user`` FK, not off
    # ``ayla_user_id``, so ownership is fully determined by local identity and
    # the withdrawal must not depend on a linkage that is NULL in production
    # (ruling §5). Subject = the same shell set step 4 erases.
    try:
        withdraw_personal_data_for_bot_users(
            BotUser.all_tenants.filter(id__in=_person_shell_ids(bot_user, link)).select_related(
                "tenant"
            ),
            source="privacy_delete",
        )
        steps.append(
            DeleteStep(
                "consent_withdraw",
                True,
                # Narrowed to the authenticated row — say so rather than
                # implying the person's whole consent history was reached.
                "own_row_only" if link.conflict else "",
            )
        )
    except Exception:  # noqa: BLE001 — per-step isolation
        logger.exception("identity.privacy.consent_withdraw_failed")
        steps.append(DeleteStep("consent_withdraw", False))

    # Step 4 — bot-side profile identifiers + preferences on the retained
    # shells. Runs unconditionally and is keyed on the channel account as
    # well as ``ayla_user_id``: unlike memory/consents this data is ours
    # alone and exists whether or not the person was ever linked to Ayla.
    try:
        _erase_bot_user_pii(bot_user, link)
        steps.append(DeleteStep("profile_pii_erase", True, "own_row_only" if link.conflict else ""))
    except Exception:  # noqa: BLE001 — per-step isolation, reported below
        logger.exception("identity.privacy.profile_pii_erase_failed")
        steps.append(DeleteStep("profile_pii_erase", False))

    # Step 5 — staff assistant threads (DRF-1276). Ours alone, like step 4,
    # and keyed on the same shell set: it exists whether or not the person
    # was ever linked to Ayla, so it runs unconditionally.
    try:
        _erase_staff_assistant(bot_user, link)
        steps.append(
            DeleteStep("staff_assistant_erase", True, "own_row_only" if link.conflict else "")
        )
    except Exception:  # noqa: BLE001 — per-step isolation, reported below
        logger.exception("identity.privacy.staff_assistant_erase_failed")
        steps.append(DeleteStep("staff_assistant_erase", False))

    result = DeleteCascadeResult(steps=tuple(steps))
    # Audit: actor + scope only — never the deleted values (C5 §6.2).
    write_audit(
        "privacy.personal_data_deleted",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "actor": "customer",
            "scope": [s.step for s in result.steps],
            "all_ok": result.all_ok,
            "failed_steps": result.failed_steps,
        },
    )
    return result
