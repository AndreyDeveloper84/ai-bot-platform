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

### T-02 / OD-T02-1 — pilot-scoped allowlist (Wave 1)

Fail-closed is correct but total: the Wave-1 pilot could not ingest a
single ``booking.created``, and the only escape was the *global*
fail-open flag, which disables tenant verification for every tenant and
every event (staging was silently running that way).

OD-T02-1 replaces that with a bounded middle ground. When the canonical
model is unavailable, an envelope is admitted only if its tenant is in
``EVENT_INGEST_ALLOWED_TENANTS``, its event name is in
``EVENT_INGEST_ALLOWED_EVENTS``, and the tenant exists in the bot DB.
Both settings default to empty = deny all. See
:mod:`apps.eventbus.ingest_allowlist` for the parsing contract and
:func:`assert_envelope_tenant_authorized` for the precedence order.

The allowlist is a **scope limiter, not a relationship proof** — it does
not establish that ``user_id`` belongs to ``tenant_id``. Controlled Pilot
only; Public MVP MUST restore the full relationship contract.

### The tenant-null carve-out is bounded, not permanent

Four contract events carry ``tenant_id: null`` and have no tenant
dimension for the allowlist to match on. They are still admitted, but two
things bound that: every admission emits an audit line
(``verification_mode=tenant_null_carveout``), and the check is no longer
evaluated ahead of the canonical probe — once #246 ships, the SUBJECT is
verified (``user_id`` must hold an active relationship with some tenant).
Without that ordering the pilot-era exemption would have outlived the
pilot silently. See :func:`_authorize_tenant_null_envelope`.

The lint test :mod:`tests.contracts.test_consumer_tenant_verification_mandate`
scans handler source code for a call to this exact function name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.eventbus.ingest_allowlist import (
    AllowlistConfigurationError,
    resolve_allowed_events,
    resolve_allowed_tenants,
)


logger = logging.getLogger(__name__)


# `event-contract.md` §2 + AMD-015 — event_names where tenant_id MAY be
# null (user-global change like display_name / avatar; solo-master billing
# events whose subscription has tenant=NULL). Duplicated from
# ingest_envelope.TENANT_NULLABLE_EVENT_NAMES to keep this module
# import-cycle-free with the dispatcher.
_TENANT_NULLABLE_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "user.profile.updated",
        "subscription.activated",
        "subscription.past_due",
        "billing.fee_charged",
    }
)


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

    The ONLY tolerated failure is :class:`ImportError` — "the model has not
    shipped yet", which is the expected pre-#246 state and legitimately
    degrades to the pilot allowlist. Anything else (``AppRegistryNotReady``
    on a worker that imports too early, an error raised inside
    ``apps.tenancy.models`` itself, a circular import during a rolling
    deploy) means the canonical check is broken, NOT absent. Returning
    ``False`` there would silently downgrade every envelope to the pilot
    allowlist — exactly the weakening precedence rule 1 forbids, just
    entered from the other side. Post-#246 that would be a live security
    regression triggered by an unrelated startup fault, so we fail CLOSED.
    """
    # The model lands with Sprint 1 #246; mypy under current tree
    # can't resolve it. The try/except below catches the runtime
    # ImportError when the model isn't yet shipped.
    try:
        from apps.tenancy.models import TenantUserRelationship  # type: ignore[attr-defined]  # noqa: F401
    except ImportError:
        return False
    except Exception as exc:  # noqa: BLE001 — see docstring: broken ≠ absent
        logger.error(
            "eventbus.ingest.tenant_verify_probe_error security=high error=%s — "
            "apps.tenancy.models could not be imported for a reason other than "
            "ImportError. Failing CLOSED rather than degrading to the pilot "
            "allowlist.",
            type(exc).__name__,
        )
        raise TenantAuthorizationError(f"tenant_verify_probe_error: {type(exc).__name__}") from exc
    return True


def _safe_log_value(value: Any, *, limit: int = 64) -> str:
    """Render an untrusted envelope field safe to interpolate into a log line.

    ``ingest_envelope`` type-checks ``tenant_id`` as ``str`` but does NOT
    constrain its shape, so a publisher (or anyone holding the shared HMAC
    secret) can put arbitrary text in it — including newlines. Our reject
    log lines are ``key=value`` pairs that ops greps and alerts on, so an
    unescaped newline would let an attacker forge additional log records
    (e.g. a fake ``tenant_verify_accepted`` line).

    Strip anything that isn't printable-ASCII-safe and cap the length; a
    legitimate value here is a 36-char UUID or a short identifier.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    cleaned = "".join(ch if ch.isprintable() and ch not in "\r\n" else "?" for ch in text)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "...(truncated)"
    return cleaned


@dataclass(frozen=True)
class _PilotDecision:
    """Outcome of the T-02 pilot-allowlist evaluation.

    ``reason`` is ``None`` on allow and one of the documented reject
    reasons on deny — it is what lands in the structured audit log, so the
    vocabulary is deliberately closed and stable for log-based alerting:

    ``relationship_unavailable`` — no canonical model AND no pilot scope
    configured at all (both allowlists empty). The ordinary, safe
    fail-closed resting state.
    ``event_not_allowed`` — event name not in ``EVENT_INGEST_ALLOWED_EVENTS``.
    ``tenant_not_allowed`` — tenant not in ``EVENT_INGEST_ALLOWED_TENANTS``
    (also covers a non-canonical / unparseable ``tenant_id``).
    ``tenant_not_found`` — allowlisted, but no active ``Tenant`` row in the
    bot DB (unknown or soft-disabled tenant).
    ``tenant_lookup_error`` — the DB lookup itself failed; fail closed
    rather than guess.
    ``malformed_configuration`` — an allowlist setting could not be parsed;
    treated as deny-all, never as allow-all.
    """

    allowed: bool
    reason: str | None = None


def _evaluate_pilot_allowlist(*, event_name: str, tenant_id: str) -> _PilotDecision:
    """T-02 / OD-T02-1 — decide a tenant-scoped envelope against the pilot scope.

    Called only when the canonical ``TenantUserRelationship`` is
    unavailable. All four conditions must hold for an allow:

    1. ``tenant_id`` is in ``EVENT_INGEST_ALLOWED_TENANTS``;
    2. ``event_name`` is in ``EVENT_INGEST_ALLOWED_EVENTS``;
    3. the tenant genuinely exists (and is active) in the bot DB;
    4. (established by the caller chain) the envelope already passed HMAC
       verification in the view and schema validation in the envelope
       parser — this helper runs strictly downstream of both.

    Every failure mode returns a deny. There is no branch in this function
    that can return ``allowed=True`` without all of 1–3 holding.
    """
    # (0) Configuration. A malformed value is deny-all, never allow-all —
    # the process normally refuses to boot on bad config (base.py raises
    # ImproperlyConfigured), so reaching here means the value was injected
    # after settings load.
    try:
        allowed_tenants = resolve_allowed_tenants(
            getattr(settings, "EVENT_INGEST_ALLOWED_TENANTS", frozenset())
        )
        allowed_events = resolve_allowed_events(
            getattr(settings, "EVENT_INGEST_ALLOWED_EVENTS", frozenset())
        )
    except AllowlistConfigurationError:
        logger.exception("eventbus.ingest.tenant_allowlist.malformed_configuration")
        return _PilotDecision(allowed=False, reason="malformed_configuration")

    # Both empty = the pilot scope was never configured. Report the
    # underlying cause (no relationship model) rather than a misleading
    # "tenant not allowed" — nothing is allowed, by design.
    if not allowed_tenants and not allowed_events:
        return _PilotDecision(allowed=False, reason="relationship_unavailable")

    # (1) Event dimension. Exact match against the normalized lower-case
    # allowlist; the envelope parser has already pinned event_name to the
    # closed §3 vocabulary, so a case- or whitespace-tricked name never
    # reaches here in a form that could match.
    if event_name not in allowed_events:
        return _PilotDecision(allowed=False, reason="event_not_allowed")

    # (2) Tenant dimension. Compare canonically: the allowlist holds
    # lower-case hyphenated UUIDs, so an upper-case or space-padded
    # tenant_id on the wire is normalized the same way before comparison
    # (and anything that isn't a UUID at all simply fails to match).
    #
    # Case is folded because an upper-case UUID is a legitimate spelling of
    # the same identifier and Django's UUIDField accepts it downstream.
    # Whitespace is deliberately NOT stripped: " <uuid> " is not something
    # the handlers can resolve (``uuid.UUID`` rejects it), so accepting it
    # here would authorize an envelope that then explodes in the handler.
    # Let it fall through to a clean ``tenant_not_allowed`` reject instead.
    normalized_tenant = tenant_id.lower() if isinstance(tenant_id, str) else ""
    if not normalized_tenant or normalized_tenant not in allowed_tenants:
        return _PilotDecision(allowed=False, reason="tenant_not_allowed")

    # (3) Local existence. An allowlist entry is an operator's claim; this
    # is the check that the claim corresponds to a real row. ``Tenant.objects``
    # is the active-only manager, so a soft-disabled tenant reads as
    # not-found — deliberate: a frozen tenant must not ingest.
    try:
        from apps.tenancy.models import Tenant

        exists = Tenant.objects.filter(id=normalized_tenant).exists()
    except Exception as exc:  # noqa: BLE001 — DB / import failure
        # Fail CLOSED, mirroring the relationship-lookup DB-error branch
        # below: a transient rejection burst that Ayla retries is a better
        # story than admitting unverified writes.
        logger.warning(
            "eventbus.ingest.tenant_allowlist.lookup_error tenant_id=%s error=%s",
            _safe_log_value(tenant_id),
            type(exc).__name__,
        )
        return _PilotDecision(allowed=False, reason="tenant_lookup_error")

    if not exists:
        return _PilotDecision(allowed=False, reason="tenant_not_found")

    return _PilotDecision(allowed=True)


def _log_pilot_accepted(
    *,
    event_id: str,
    event_name: str,
    tenant_id: str,
    user_id: str,
    correlation_id: str | None,
) -> None:
    """Structured audit line for an event admitted by the pilot allowlist.

    Identifiers only — never the payload. ``event_id``/``correlation_id``
    are the join keys an operator needs to reconcile a bot-side accept with
    Ayla's outbox row; the payload itself may carry PII (§6.4).

    ``user_id`` is included deliberately. The pilot allowlist verifies the
    TENANT dimension only — it never establishes that ``user_id`` belongs to
    ``tenant_id`` (that is the accepted Controlled-Pilot residual risk). This
    log line is therefore the ONLY detective control over it: without it,
    nobody can enumerate which users an allowlisted tenant asserted, or
    alert on one tenant claiming an anomalous spread of user_ids.
    """
    logger.info(
        "eventbus.ingest.tenant_verify_accepted "
        "verification_mode=pilot_allowlist event_id=%s event_name=%s "
        "tenant_id=%s user_id=%s correlation_id=%s",
        _safe_log_value(event_id),
        _safe_log_value(event_name),
        _safe_log_value(tenant_id),
        _safe_log_value(user_id),
        _safe_log_value(correlation_id),
    )


def _log_pilot_rejected(
    *,
    event_id: str,
    event_name: str,
    tenant_id: str,
    user_id: str,
    reason: str,
    correlation_id: str | None,
) -> None:
    """Structured audit line for an event the pilot allowlist refused.

    WARNING severity: on the pilot path a reject means either a
    misconfiguration (an onboarded tenant that was never allowlisted) or a
    genuine spoof attempt. Both want operator eyes.
    """
    logger.warning(
        "eventbus.ingest.tenant_verify_rejected "
        "verification_mode=pilot_allowlist reason=%s event_id=%s "
        "event_name=%s tenant_id=%s user_id=%s correlation_id=%s",
        reason,
        _safe_log_value(event_id),
        _safe_log_value(event_name),
        _safe_log_value(tenant_id),
        _safe_log_value(user_id),
        _safe_log_value(correlation_id),
    )


def _authorize_tenant_null_envelope(
    *,
    event_id: str,
    event_name: str,
    user_id: str,
    correlation_id: str | None,
) -> None:
    """Authorize an envelope that legitimately carries ``tenant_id=null``.

    The four `event-contract.md` §2 + AMD-015 events have no tenant
    dimension, so the pilot allowlist has nothing to match on and T-02 left
    them admitted (OD-T02-1 §1 — preserve the existing contract behaviour
    for envelopes without ``tenant_id``). Two properties of that carve-out
    were wrong and are fixed here.

    **It must not be permanent.** The pre-T-02 code returned *before*
    probing for :class:`TenantUserRelationship`, so these four events would
    never be authorized against the canonical model even after Sprint 1
    #246 ships — a temporary pilot compromise silently frozen into the
    permanent design. Once the model is importable we verify the subject
    exists: ``user_id`` must hold at least one active relationship with
    SOME tenant. That is the strongest statement available for a
    user-global event (there is no tenant to bind to), and it is what
    closes the "arbitrary attacker-chosen ``user_id``" hole.

    **It must be observable.** The carve-out previously logged nothing, so
    the ingest path had the inverted property that events the allowlist
    ADMITTED left an audit trail while events that bypassed it entirely did
    not. Every pass through here now emits an accept line with
    ``verification_mode=tenant_null_carveout``.

    Raises :class:`TenantAuthorizationError` when the canonical model is
    available and the subject has no active relationship anywhere.
    """
    if _tenant_user_relationship_available():
        try:
            from apps.tenancy.models import TenantUserRelationship  # type: ignore[attr-defined]
        except ImportError as exc:
            raise TenantAuthorizationError("tenant_verify_import_race") from exc
        try:
            known_user = TenantUserRelationship.objects.filter(
                user_id=user_id,
                is_active=True,
            ).exists()
        except Exception as exc:  # noqa: BLE001 — DB lookup failure
            raise TenantAuthorizationError(f"tenant_verify_db_error: {type(exc).__name__}") from exc
        if not known_user:
            logger.warning(
                "eventbus.ingest.tenant_verify_rejected "
                "verification_mode=tenant_null_carveout reason=unknown_user "
                "event_id=%s event_name=%s user_id=%s correlation_id=%s",
                _safe_log_value(event_id),
                _safe_log_value(event_name),
                _safe_log_value(user_id),
                _safe_log_value(correlation_id),
            )
            raise TenantAuthorizationError(
                f"no_active_relationship_user_scope "
                f"event_name={_safe_log_value(event_name)} "
                f"user_id={_safe_log_value(user_id)}"
            )

    # Identifiers only — never the payload (§6.4). This is the sole
    # detective control over the tenant-null surface: it is what lets an
    # operator enumerate which user_ids were asserted without any tenant
    # binding, and alert on an anomalous spread.
    logger.info(
        "eventbus.ingest.tenant_verify_accepted "
        "verification_mode=tenant_null_carveout event_id=%s event_name=%s "
        "user_id=%s correlation_id=%s",
        _safe_log_value(event_id),
        _safe_log_value(event_name),
        _safe_log_value(user_id),
        _safe_log_value(correlation_id),
    )


def assert_envelope_tenant_authorized(envelope: Any) -> None:
    """Verify ``(envelope.user_id, envelope.tenant_id)`` authorization.

    Raises :class:`TenantAuthorizationError` on mismatch. Returns
    ``None`` on success. Consumer handlers MUST call this BEFORE any
    side-effect.

    Round-2 AS8 — FAIL-CLOSED by default. The previous Round-1 stub
    fail-OPENED when ``tenant_id`` was set + the canonical model
    unavailable. That would mean ZERO tenant-spoof defense if #442
    ships before Sprint 1 #246.

    ### Precedence (T-02 / OD-T02-1)

    Strictly ordered — the first applicable rule decides:

    1. **Canonical relationship verification**, whenever
       ``TenantUserRelationship`` is importable. The pilot allowlist MUST
       NOT weaken or short-circuit a working full check: if the model is
       there, an allowlisted tenant with no active relationship is still
       rejected.
    2. **Pilot-scoped allowlist**, only when that model is unavailable.
       Requires tenant AND event allowlisted AND the tenant to exist
       locally. See :func:`_evaluate_pilot_allowlist`.
    3. **Global fail-open** (``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN``),
       only as an emergency escape hatch, and only after the allowlist has
       already refused. Defaults False, logs a security warning, and is not
       part of any recommended configuration.
    4. Otherwise **fail closed** — raise :class:`TenantAuthorizationError`.

    Decision matrix:

    | tenant_id state | model available | pilot allowlist | flag  | outcome    |
    |-----------------|-----------------|-----------------|-------|------------|
    | None + nullable | no              | n/a             | n/a   | log + pass |
    | None + nullable | yes             | n/a             | n/a   | user check |
    | None + others   | n/a             | n/a             | n/a   | RAISE      |
    | set             | yes             | not consulted   | n/a   | real check |
    | set             | no              | allow           | n/a   | log + pass |
    | set             | no              | deny            | True  | warn + pass|
    | set             | no              | deny            | False | RAISE      |

    Note rule 1's consequence: the allowlist is a *fallback*, never an
    override. Adding a tenant to ``EVENT_INGEST_ALLOWED_TENANTS`` cannot
    admit an envelope that a live relationship check would reject.
    """
    event_name = getattr(envelope, "event_name", "")
    user_id = getattr(envelope, "user_id", "")
    tenant_id = getattr(envelope, "tenant_id", None)
    event_id = getattr(envelope, "event_id", "")
    correlation_id = getattr(envelope, "correlation_id", None)

    if tenant_id is None:
        if event_name not in _TENANT_NULLABLE_EVENT_NAMES:
            raise TenantAuthorizationError(
                f"tenant_id is null for non-nullable event {_safe_log_value(event_name)!r}"
            )
        _authorize_tenant_null_envelope(
            event_id=event_id,
            event_name=event_name,
            user_id=user_id,
            correlation_id=correlation_id,
        )
        return

    if _tenant_user_relationship_available():
        # Sprint 1 #246 has shipped — do the real lookup.
        try:
            from apps.tenancy.models import TenantUserRelationship  # type: ignore[attr-defined]
        except ImportError as exc:
            # Race between the probe and this import. Falling through to
            # the pilot allowlist here would let an allowlisted tenant in
            # WITHOUT any relationship verification — precisely the
            # weakening precedence rule 1 forbids. Fail closed instead;
            # this is not reachable in practice (module caching), and if it
            # ever fires the operator needs to see it, not absorb it.
            raise TenantAuthorizationError("tenant_verify_import_race") from exc
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
                # Sanitized: both fields are attacker-influenced (the
                # envelope parser type-checks them as non-empty ``str`` and
                # nothing more), and this message is written to the log by
                # the dispatcher — the same log-injection vector the pilot
                # branch already defends against. This branch activates
                # with #246, so it must be safe before then, not after.
                raise TenantAuthorizationError(
                    f"no_active_relationship "
                    f"user_id={_safe_log_value(user_id)} "
                    f"tenant_id={_safe_log_value(tenant_id)}"
                )
            return

    # Canonical model not available. Precedence rule 2 — try the T-02
    # pilot-scoped allowlist before considering any global escape hatch.
    decision = _evaluate_pilot_allowlist(event_name=event_name, tenant_id=tenant_id)
    if decision.allowed:
        _log_pilot_accepted(
            event_id=event_id,
            event_name=event_name,
            tenant_id=tenant_id,
            user_id=user_id,
            correlation_id=correlation_id,
        )
        return

    reason = decision.reason or "relationship_unavailable"
    _log_pilot_rejected(
        event_id=event_id,
        event_name=event_name,
        tenant_id=tenant_id,
        user_id=user_id,
        reason=reason,
        correlation_id=correlation_id,
    )

    # Precedence rules 3 + 4. FAIL-CLOSED unless ops EXPLICITLY opted into
    # the global escape hatch.
    fail_open = bool(getattr(settings, "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN", False))
    if not fail_open:
        raise TenantAuthorizationError(
            f"tenant_authorization_denied reason={reason} "
            f"event_name={_safe_log_value(event_name)} "
            f"tenant_id={_safe_log_value(tenant_id)}. "
            "TenantUserRelationship is not available and the T-02 pilot "
            "allowlist did not admit this envelope, so the helper fails "
            "CLOSED. Add the tenant to "
            "EVENT_INGEST_ALLOWED_TENANTS and the event to "
            "EVENT_INGEST_ALLOWED_EVENTS if this delivery is expected."
        )

    # Opt-in fall-through. Round-3 NEW-5 + Round-4 R3-2 — log +
    # audit row (sampled per (user_id, tenant_id)) per fall-through.
    # The startup warning catches the deploy-time misconfig; this
    # catches every runtime exposure event.
    #
    # Round-4 R3-2 — sample by COMPOSITE (user_id, tenant_id), not
    # just user_id. An attacker controlling user_id rotating
    # tenant_id was dodging the sampler. AND we now track a counter
    # that reflects the TRUE call volume — sampled audit row carries
    # count_in_window so 1000 events produce 1 row with count=1000.
    #
    # T-02: this branch is now reached ONLY after the pilot allowlist has
    # already denied the envelope, so every line here represents an event
    # admitted purely because tenant verification is globally disabled.
    # The log carries the allowlist reason it overrode.
    logger.warning(
        "eventbus.ingest.tenant_verify_fail_open security=critical "
        "verification_mode=global_fail_open overridden_reason=%s "
        "event_id=%s event_name=%s user_id=%s tenant_id=%s",
        reason,
        _safe_log_value(event_id),
        _safe_log_value(event_name),
        _safe_log_value(user_id),
        _safe_log_value(tenant_id),
    )
    try:
        from apps.audit.services import write_audit
        from apps.eventbus.ingest_rate_audit_sampler import (
            increment_tenant_fail_open_count,
            should_emit_tenant_fail_open_audit,
        )

        composite_key = f"{user_id}:{tenant_id}"
        # Round-4 R3-2 — increment-then-threshold-emit. Counter
        # increments on every call (no sampling at counter layer).
        # The threshold ladder decides which counts emit an audit
        # row: {1, 10, 50, 100, 500, 1000, 5000, 10000} + every
        # 10k thereafter. Each row carries count_in_window so
        # operators see the volume curve.
        count = increment_tenant_fail_open_count(composite_key)

        if should_emit_tenant_fail_open_audit(count):
            # T-02 — the pilot tenant-existence lookup above runs inside
            # the dispatcher's transaction.atomic(). If it raised a
            # DatabaseError (reason=tenant_lookup_error), Django has
            # already flagged the connection needs_rollback and ANY query
            # here — including this audit write — raises
            # TransactionManagementError. That is caught below and logged
            # as a generic audit_failed, indistinguishable from a real
            # audit bug, silently losing the one durable forensic artifact
            # for the module's most dangerous branch. Detect the poisoned
            # transaction explicitly and say so.
            from django.db import transaction as _db_transaction

            if _db_transaction.get_connection().needs_rollback:
                logger.error(
                    "eventbus.ingest.tenant_verify_fail_open.audit_skipped_broken_tx "
                    "security=high overridden_reason=%s count_in_window=%d — an event "
                    "was admitted by the global fail-open but the AuditLog row could "
                    "not be written because the transaction was already broken. The "
                    "WARNING line above is the only record of this exposure.",
                    reason,
                    count,
                )
                return
            write_audit(
                action="eventbus.ingest.tenant_verify_fail_open",
                target="eventbus.ingest",
                payload={
                    "event_name": event_name,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    # T-02 — which pilot-allowlist denial this override
                    # bulldozed. Distinguishes "tenant simply wasn't
                    # onboarded" from "an unknown tenant got admitted".
                    "overridden_reason": reason,
                    # Round-4 R3-2 — forensic count surface. The
                    # row reflects the TRUE call volume at the
                    # ladder threshold.
                    "count_in_window": count,
                },
            )
    except Exception:  # noqa: BLE001 — audit MUST NEVER block the handler
        logger.exception("eventbus.ingest.tenant_verify_fail_open.audit_failed")
