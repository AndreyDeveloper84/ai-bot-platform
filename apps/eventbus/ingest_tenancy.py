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

### Activation timeline

* **Today (Phase 0 #432 follow-up):** the function exists as a stub
  that ONLY enforces the §2 "tenant_id MAY be null for
  user.profile.updated" rule. The full ``TenantUserRelationship``
  lookup is a no-op — the model arrives via Sprint 1 #246.

* **After #246 ships:** the lookup is enabled. Re-grep + dispatcher
  exception catch surface a P0 alert on the FIRST tenant-spoof attempt
  in production.

The stub today is deliberate: consumer authors writing #442-#446 SHALL
import + call this function. When #246 ships, the function gains teeth
without any consumer rewrite.
"""

from __future__ import annotations

import logging
from typing import Any


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


def assert_envelope_tenant_authorized(envelope: Any) -> None:
    """Verify ``(envelope.user_id, envelope.tenant_id)`` authorization.

    Raises :class:`TenantAuthorizationError` on mismatch. Returns
    ``None`` on success. Consumer handlers MUST call this BEFORE any
    side-effect.

    Today's enforcement (stub state):

    * If ``envelope.tenant_id is None`` and ``envelope.event_name``
      is not in the §2 nullable set (i.e. not ``user.profile.updated``)
      → raise.
    * If ``envelope.tenant_id is None`` and the event IS nullable →
      pass (user-global change; no tenant binding to verify).
    * Otherwise (tenant_id is set): currently a NO-OP. The full
      :class:`TenantUserRelationship` lookup arrives with Sprint 1
      #246; the helper signature is stable so consumers don't change.

    After #246 ships, the no-op branch becomes:

    .. code-block:: python

        from apps.tenancy.models import TenantUserRelationship
        if not TenantUserRelationship.objects.filter(
            user_id=envelope.user_id,
            tenant_id=envelope.tenant_id,
            is_active=True,
        ).exists():
            raise TenantAuthorizationError(...)
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

    # Sprint 1 #246 will replace this no-op with the real
    # TenantUserRelationship lookup. The log line is here today so
    # ops can confirm the helper is actually being called by every
    # consumer (lint enforces presence in source; this confirms
    # runtime invocation).
    logger.debug(
        "eventbus.ingest.tenant_verify_stub event_name=%s user_id=%s tenant_id=%s",
        event_name,
        user_id,
        tenant_id,
    )
