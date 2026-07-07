"""Consent lifecycle services (DRF-457 / Sprint 3 / A2).

Four entry points — all the consent system the rest of the platform
needs:

  * :func:`grant` — explicit consent grant. Appends a ConsentRecord
    row, emits ``consent_granted`` event, writes audit row.
  * :func:`withdraw` — explicit withdrawal. Stamps ``withdrawn_at``
    on the latest active row (does NOT delete; legal trail). Emits
    ``consent_withdrawn``, writes audit.
  * :func:`has_consent` — read-side: True iff the user has an active
    ConsentRecord for the type, optionally matching document version.
  * :func:`get_consents` — list of every active consent for the user.

### Why service-layer instead of model methods

- ``grant`` writes ConsentRecord + Event + AuditLog — three tables in
  one call. Belongs in a service (Unit of Work), not a model method.
- ``has_consent`` is a query with a non-trivial predicate (latest row,
  granted=True, withdrawn_at IS NULL, optional document_version
  match). Inlining at every call site would drift; centralised here.
- Tenant invariant: like every other service in the platform, these
  fail loud with ValueError if ``current_tenant()`` is None.

### PII rule

Events and audit rows carry **consent_type only**, never the row id
or any user-identifying string beyond `bot_user.id` (UUID). The
``has_consent`` boolean is sensitive metadata; never log raw payload.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.consent.models import ConsentRecord
from apps.events.services import emit
from apps.events.vocabulary import CONSENT_GRANTED, CONSENT_WITHDRAWN
from apps.eventbus import services as eventbus_services
from apps.tenancy.context import current_tenant

if TYPE_CHECKING:
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


def grant(
    bot_user: "BotUser",
    *,
    consent_type: str,
    source: str,
    document_version: str = "",
) -> ConsentRecord:
    """Record an explicit consent grant.

    Args:
      bot_user: who's granting. Must belong to the current tenant scope.
      consent_type: one of ConsentRecord.ConsentType values.
      source: where the grant came from (free-form, but conventional
              forms documented in model docstring).
      document_version: privacy-policy version snapshot at grant time.

    Returns:
      The newly created ConsentRecord.

    Raises:
      ValueError: ``current_tenant()`` is None (caller must enter
                  tenant_scope).
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "consent.grant requires a tenant in scope. Wrap the call in "
            "`tenant_scope(t)` before invocation."
        )

    with transaction.atomic():
        record = ConsentRecord.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            consent_type=consent_type,
            granted=True,
            source=source,
            document_version=document_version,
        )

    # Defer event + audit emits to after commit so subscribers
    # (Sprint 5 replay) never see consent events without the matching
    # ConsentRecord row in DB.
    def _emit_grant() -> None:
        emit(
            CONSENT_GRANTED,
            payload={
                "bot_user_id": str(bot_user.id),
                "consent_type": consent_type,
                "document_version": document_version,
            },
        )
        write_audit(
            "consent.granted",
            target="ConsentRecord",
            target_id=record.id,
            payload={
                "bot_user_id": str(bot_user.id),
                "consent_type": consent_type,
                "source": source[:80],  # truncate, no PII
                "document_version": document_version,
            },
        )
        # Domain bus — taxonomy §3.2 customer.consent.changed.
        # Swallowed so a bus outage never breaks the grant.
        try:
            eventbus_services.emit_customer_consent_changed(
                customer_id=str(bot_user.id),
                consent_type=consent_type,
                granted=True,
                granted_at=record.captured_at.isoformat(),
                granted_via=source[:80],
                actor_id=str(bot_user.id),
                tenant=tenant,
            )
        except Exception:  # noqa: BLE001 — domain telemetry never breaks the grant
            logger.exception(
                "consent.grant.eventbus_emit_failed bot_user=%s type=%s",
                bot_user.id,
                consent_type,
            )

    transaction.on_commit(_emit_grant)
    logger.info(
        "consent.granted bot_user=%s type=%s tenant=%s",
        bot_user.id,
        consent_type,
        tenant.id,
    )
    return record


def record_global_consent(
    bot_user: "BotUser",
    *,
    consent_type: str = ConsentRecord.ConsentType.PERSONAL_DATA.value,
    source: str,
    document_version: str = "",
) -> ConsentRecord:
    """Append a ConsentRecord on the tenant-less GLOBAL marketplace path (#1046).

    The global path runs at ``current_tenant() is None`` by design, so
    :func:`grant` (which requires a tenant in scope) cannot be used. We write the
    row via ``all_tenants`` with the explicit sentinel tenant the global
    ``BotUser`` is parked under — the SAME pattern as ``record_global_message`` /
    ``resolve_or_create_global_bot_user`` — so ``current_tenant()`` stays ``None``
    throughout. This is the server-side proof-of-consent the regulator needs for
    the general chat, extending the food-scanner server journal (#956) to the
    marketplace welcome flow.

    Unlike :func:`grant` this does NOT emit the ``customer.consent.changed`` domain
    event: that eventbus helper is tenant-scoped and the sentinel tenant is not a
    real salon. The local ``consent_granted`` analytics event + audit row are
    enough for the pilot proof-of-consent; the domain-bus mirror is post-pilot
    (S1.7 memory wiring, #1054).

    **Idempotent.** Uses ``get_or_create`` keyed on the active grant
    ``(bot_user, consent_type, granted=True, withdrawn_at=None)`` so a repeated
    consent tap never appends a duplicate row, and a repeat tap backfills a row a
    prior transient failure dropped. The event + audit fire only on actual create.
    (Withdrawal still appends a fresh row via :func:`withdraw`/:func:`grant`; this
    helper is the append-once welcome-consent path only.)

    Args:
      bot_user: the global (sentinel-tenant) BotUser granting consent.
      consent_type: one of ConsentRecord.ConsentType values. Defaults to
                    PERSONAL_DATA (152-ФЗ baseline).
      source: where the grant came from (free-form; conventional forms in the
              model docstring).
      document_version: privacy-policy version snapshot at grant time.

    Returns:
      The ConsentRecord for this active grant (created or pre-existing).
    """

    # bot_user.tenant is the ``global_bot`` sentinel (Tenant is not tenant-scoped,
    # so this FK read does not require / trip a tenant scope).
    sentinel = bot_user.tenant

    with transaction.atomic():
        record, created = ConsentRecord.all_tenants.get_or_create(
            bot_user=bot_user,
            consent_type=consent_type,
            granted=True,
            withdrawn_at=None,
            defaults={
                "tenant": sentinel,
                "source": source,
                "document_version": document_version,
            },
        )

        # #1074 — stamp consent_at ATOMICALLY with the ConsentRecord (same
        # transaction). On the global path WelcomeSkill no longer stamps it
        # separately (it defers to this call when current_tenant() is None), so
        # consent_at is set IFF a ConsentRecord exists: a transient failure rolls
        # back BOTH, never leaving consent_at set with no proof-of-consent row.
        # Idempotent — only stamp when currently unset (never overwrite an earlier
        # consent timestamp; also reconciles a legacy row whose consent_at is None).
        if getattr(bot_user, "consent_at", None) is None:
            bot_user.consent_at = record.captured_at
            bot_user.save(update_fields=["consent_at"])

        def _emit_grant() -> None:
            emit(
                CONSENT_GRANTED,
                payload={
                    "bot_user_id": str(bot_user.id),
                    "consent_type": consent_type,
                    "document_version": document_version,
                    "global": True,
                },
            )
            write_audit(
                "consent.granted",
                target="ConsentRecord",
                target_id=record.id,
                payload={
                    "bot_user_id": str(bot_user.id),
                    "consent_type": consent_type,
                    "source": source[:80],  # truncate, no PII
                    "document_version": document_version,
                    "global": True,
                },
            )

        if created:
            transaction.on_commit(_emit_grant)

    if created:
        logger.info(
            "consent.granted(global) bot_user=%s type=%s tenant=%s(sentinel)",
            bot_user.id,
            consent_type,
            sentinel.id,
        )
    return record


def withdraw(
    bot_user: "BotUser",
    *,
    consent_type: str,
    source: str,
) -> ConsentRecord | None:
    """Withdraw the latest active consent for `(bot_user, consent_type)`.

    Args:
      bot_user: whose consent to withdraw.
      consent_type: which type to withdraw.
      source: where the withdrawal came from.

    Returns:
      The ConsentRecord that was withdrawn (with ``withdrawn_at`` set),
      or None if there was no active consent to withdraw.

    Notes:
      The row is NOT deleted. ``withdrawn_at`` is stamped, leaving the
      full lifecycle visible. A future ``grant()`` for the same type
      appends a fresh row — the withdrawn row stays as audit trail.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "consent.withdraw requires a tenant in scope. Wrap the call "
            "in `tenant_scope(t)` before invocation."
        )

    # Latest active grant for this (bot_user, consent_type).
    active = (
        ConsentRecord.objects.filter(
            bot_user=bot_user,
            consent_type=consent_type,
            granted=True,
            withdrawn_at__isnull=True,
        )
        .order_by("-captured_at")
        .first()
    )
    if active is None:
        # Nothing to withdraw. Emit a "no-op withdraw" audit row so the
        # operator can see the user tried — silent return would be
        # forensically misleading.
        write_audit(
            "consent.withdraw_noop",
            target="ConsentRecord",
            payload={
                "bot_user_id": str(bot_user.id),
                "consent_type": consent_type,
                "reason": "no_active_grant",
                "source": source[:80],
            },
        )
        return None

    now = timezone.now()
    with transaction.atomic():
        ConsentRecord.all_tenants.filter(pk=active.pk).update(withdrawn_at=now)
    active.withdrawn_at = now

    def _emit_withdraw() -> None:
        emit(
            CONSENT_WITHDRAWN,
            payload={
                "bot_user_id": str(bot_user.id),
                "consent_type": consent_type,
            },
        )
        write_audit(
            "consent.withdrawn",
            target="ConsentRecord",
            target_id=active.id,
            payload={
                "bot_user_id": str(bot_user.id),
                "consent_type": consent_type,
                "source": source[:80],
            },
        )
        # Domain bus — taxonomy §3.2 customer.consent.changed
        # (granted=False). ``granted_at`` carries the withdrawal
        # timestamp; the bus has no separate ``withdrawn_at`` field —
        # the granted flag is the lifecycle bit.
        try:
            eventbus_services.emit_customer_consent_changed(
                customer_id=str(bot_user.id),
                consent_type=consent_type,
                granted=False,
                granted_at=now.isoformat(),
                granted_via=source[:80],
                actor_id=str(bot_user.id),
                tenant=tenant,
            )
        except Exception:  # noqa: BLE001 — domain telemetry never breaks the withdraw
            logger.exception(
                "consent.withdraw.eventbus_emit_failed bot_user=%s type=%s",
                bot_user.id,
                consent_type,
            )

    transaction.on_commit(_emit_withdraw)
    logger.info(
        "consent.withdrawn bot_user=%s type=%s tenant=%s",
        bot_user.id,
        consent_type,
        tenant.id,
    )
    return active


def has_consent(
    bot_user: "BotUser",
    consent_type: str,
    *,
    document_version: str | None = None,
) -> bool:
    """True iff `bot_user` has an active consent for `consent_type`.

    Active = the latest ConsentRecord for this (bot_user, consent_type)
    has ``granted=True`` AND ``withdrawn_at IS NULL`` AND (if
    document_version passed) ``document_version == passed value``.

    The document_version match exists for policy-rotation flows: a
    privacy-policy bump from v1.x → v2.0 makes ``has_consent(...,
    document_version="privacy-v2.0")`` return False for users who only
    granted under v1.x, triggering re-prompt UX.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "consent.has_consent requires a tenant in scope. Wrap the call "
            "in `tenant_scope(t)` before invocation."
        )

    qs = ConsentRecord.objects.filter(
        bot_user=bot_user,
        consent_type=consent_type,
        granted=True,
        withdrawn_at__isnull=True,
    )
    if document_version is not None:
        qs = qs.filter(document_version=document_version)
    return qs.exists()


def get_consents(bot_user: "BotUser") -> list[ConsentRecord]:
    """Return every active consent for `bot_user` under current tenant.

    "Active" = ``granted=True AND withdrawn_at IS NULL``. Withdrawn or
    declined rows are excluded — callers that need the full lifecycle
    history go to ``ConsentRecord.all_tenants.filter(bot_user=...)``
    directly.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "consent.get_consents requires a tenant in scope. Wrap the call "
            "in `tenant_scope(t)` before invocation."
        )

    return list(
        ConsentRecord.objects.filter(
            bot_user=bot_user,
            granted=True,
            withdrawn_at__isnull=True,
        ).order_by("-captured_at")
    )
