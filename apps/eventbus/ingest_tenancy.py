"""Canonical tenant-authorization primitive for event-ingest handlers.

PR #507 adversarial pass A3. HMAC verification (§6.2 of
`event-contract.md`) proves only that some Ayla service holding the
shared secret signed the envelope — it does NOT prove the envelope's
``tenant_id`` is legitimate. A compromised Ayla worker / debug script
could sign an envelope with ``tenant_id=victim_tenant`` and write
attribution to bot-platform.

This module is the ONE place every consumer handler calls to verify
``(envelope.user_id, envelope.tenant_id)`` against the canonical
``TenantUserRelationship`` (per ADR-0009 §Hard rule #6).

### Why one canonical helper

The lint test :mod:`tests.contracts.test_consumer_tenant_verification_mandate`
scans handler source code for a call to this exact function name. A
single helper makes the lint reliable; six different verify-then-raise
implementations across the consumer family would inevitably drift.

### Activation timeline — FAIL-CLOSED by default (Round-2 AS8)

Round-1 stub fail-OPENED when ``tenant_id`` was set and the
canonical model unavailable. That would mean ZERO tenant-spoof
defense if #442 ships before Sprint 1 #246. An attacker exploits
the gap by minting an HMAC-valid envelope with
``tenant_id=<victim_tenant>`` and bot-platform writes against it.

Round-2 fix: **fail-CLOSED by default**. If ``tenant_id`` is set
and the canonical model is unavailable, raise
:class:`TenantAuthorizationError`. The opt-in
``settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True`` is the
documented pre-#246 transition bridge — operators must explicitly
flip it after reading this docstring + the startup warning.

When Sprint 1 #246 ships ``TenantUserRelationship``, the model
becomes importable + the real lookup runs unconditionally — the
fail-open flag stops mattering.

The lint test :mod:`tests.contracts.test_consumer_tenant_verification_mandate`
scans handler source code for a call to this exact function name.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings


logger = logging.getLogger(__name__)


# `event-contract.md` §2 — the only event_name where tenant_id MAY be
# null (user-global change like display_name / avatar). Duplicated from
# ingest_envelope.TENANT_NULLABLE_EVENT_NAMES to keep this module
# import-cycle-free with the dispatcher.
_TENANT_NULLABLE_EVENT_NAMES: frozenset[str] = frozenset({"user.profile.updated"})


class TenantAuthorizationError(Exception):
    """Raised when an envelope's ``tenant_id`` is not authorized for ``user_id``.

    The dispatcher catches this and surfaces it as
    :attr:`apps.eventbus.ingest_dispatcher.DispatchOutcome.HANDLER_EXCEPTION`
    per `event-contract.md` §8.1. The publisher's retry budget will
    expend and the event will dead-letter — operator triage required.
    """


def _tenant_user_relationship_available() -> bool:
    """True iff the canonical TenantUserRelationship model can be imported.

    Sprint 1 #246 ships this model. Until then, the import is None.
    Round-2 AS8: the answer drives fail-closed vs fall-back-open
    behaviour. We probe at every call (cheap importlib check) so the
    transition from #246-pre to #246-post is automatic — no settings
    flip required at deploy.
    """
    try:
        from apps.tenancy.models import TenantUserRelationship  # noqa: F401
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 — defensive
        return False
    return True


def assert_envelope_tenant_authorized(envelope: Any) -> None:
    """Verify ``(envelope.user_id, envelope.tenant_id)`` authorization.

    Raises :class:`TenantAuthorizationError` on mismatch. Returns
    ``None`` on success. Consumer handlers MUST call this BEFORE any
    side-effect.

    Round-2 AS8 — FAIL-CLOSED by default. The previous Round-1 stub
    fail-OPENED when ``tenant_id`` was set + the canonical model
    unavailable. That would mean ZERO tenant-spoof defense if #442
    ships before Sprint 1 #246.

    Decision matrix:

    | tenant_id state | model available | flag set      | outcome    |
    |-----------------|-----------------|---------------|------------|
    | None + nullable | n/a             | n/a           | pass       |
    | None + others   | n/a             | n/a           | RAISE      |
    | set             | yes             | n/a           | real check |
    | set             | no              | flag True     | log + pass |
    | set             | no              | flag False    | RAISE      |

    The "flag True + no model" branch is the documented opt-in
    bridge for pre-#246 development. Operators must explicitly set
    ``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True`` to use it; the
    startup warning surfaces the risk.
    """
    event_name = getattr(envelope, "event_name", "")
    user_id = getattr(envelope, "user_id", "")
    tenant_id = getattr(envelope, "tenant_id", None)

    if tenant_id is None:
        if event_name not in _TENANT_NULLABLE_EVENT_NAMES:
            raise TenantAuthorizationError(
                f"tenant_id is null for non-nullable event {event_name!r}"
            )
        return

    if _tenant_user_relationship_available():
        # Sprint 1 #246 has shipped — do the real lookup.
        try:
            from apps.tenancy.models import (  # type: ignore[import-not-found]
                TenantUserRelationship,
            )
        except ImportError:
            # Race between probe + import — shouldn't happen, but if
            # it does, fall through to the fail-closed branch below.
            pass
        else:
            try:
                exists = TenantUserRelationship.objects.filter(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    is_active=True,
                ).exists()
            except Exception as exc:  # noqa: BLE001 — DB lookup failure
                # Fail-CLOSED on DB error too. A transient DB issue
                # is a worse story for the operator than a brief
                # rejection burst per §6.3 (which Ayla will retry).
                raise TenantAuthorizationError(
                    f"tenant_verify_db_error: {type(exc).__name__}"
                ) from exc
            if not exists:
                raise TenantAuthorizationError(
                    f"no_active_relationship user_id={user_id} tenant_id={tenant_id}"
                )
            return

    # Canonical model not available (pre-#246 transition window).
    # FAIL-CLOSED unless ops EXPLICITLY opts into the open mode.
    fail_open = bool(getattr(settings, "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN", False))
    if not fail_open:
        raise TenantAuthorizationError(
            "TenantUserRelationship model not available + "
            "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=False. Per Round-2 AS8 "
            "the helper fails CLOSED until Sprint 1 #246 ships the "
            "canonical model. Set the flag True explicitly to enable "
            "pre-#246 consumer development."
        )

    # Opt-in fall-through. Log every invocation so ops sees the
    # exposure window in the audit trail.
    logger.warning(
        "eventbus.ingest.tenant_verify_fail_open event_name=%s user_id=%s tenant_id=%s",
        event_name,
        user_id,
        tenant_id,
    )
