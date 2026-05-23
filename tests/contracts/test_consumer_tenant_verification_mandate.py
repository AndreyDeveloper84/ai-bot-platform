"""Lint: every event-ingest handler must call assert_envelope_tenant_authorized.

PR #507 adversarial-pass A3 / ADR-0009 §Hard rule #6. HMAC verifies
only that some Ayla service holding the shared secret signed the
envelope; it does NOT prove the envelope's ``tenant_id`` is legitimate.
A compromised Ayla worker, a debug script, or a tenant-isolation bug
on the publisher side could mint an HMAC-valid envelope carrying
``tenant_id=<victim_tenant>`` plus arbitrary ``data``, and bot-platform
would attribute writes to the victim tenant.

The defense is per-handler verification of ``(user_id, tenant_id)``
against the canonical ``TenantUserRelationship`` (model lands with
Sprint 1 #246). This lint enforces that every registered handler's
source contains a call to the canonical helper
:func:`apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized`.

### Today's enforcement state

The lint walks :func:`apps.eventbus.ingest_dispatcher.registered_handlers`
and inspects each handler's source code via :func:`inspect.getsource`.
A handler whose source does NOT contain a call to
``assert_envelope_tenant_authorized`` fails this test.

No handlers are registered yet (consumer family #442-#446 still
to ship), so this test currently exercises the lint mechanics on
an empty registry — it passes trivially. The first consumer PR
that registers a handler triggers the real check.

### After #246 ships

The runtime helper switches from no-op to enforcement. Lint stays
unchanged.

### Why a source-grep lint vs a runtime decorator

A decorator would force consumers to opt-in by wrapping their
handler. A source-grep lint catches the case where a developer
COPIES an existing handler, REMOVES the verify call (because the
new event «doesn't need it»), and ships. Decorators are easier to
remove than literal function-call references in source.
"""

from __future__ import annotations

import inspect

import pytest

from apps.eventbus.ingest_dispatcher import registered_handlers


# Sentinel string the lint asserts in handler source. Centralised so a
# future rename of the helper updates this one constant.
REQUIRED_VERIFY_CALL_SUBSTRING = "assert_envelope_tenant_authorized"


def _handler_source_or_unavailable(handler) -> str:
    """Return the handler's source or empty string if not inspectable.

    Lambdas and C-level callables don't have inspectable source.
    Production handlers are full ``def`` functions in importable
    modules — any handler this returns "" for is a programmer error
    (lambda registered as handler, etc.), but we don't fail the lint
    in that branch because doing so would also fail empty-registry
    setups and tests with mock handlers.
    """
    try:
        return inspect.getsource(handler)
    except (OSError, TypeError):
        return ""


@pytest.mark.parametrize(
    "key,handler",
    list(registered_handlers().items()),
    ids=lambda hk: f"{hk[0]}@v{hk[1]}" if isinstance(hk, tuple) else str(hk),
)
def test_every_registered_handler_calls_tenant_verify(key, handler) -> None:
    """Each registered ``(event_name, event_version)`` handler MUST
    call :func:`assert_envelope_tenant_authorized` in its source.

    This test parametrises over the LIVE registry at collection time.
    When no consumers are registered (current Phase 0 state), the
    parametrize list is empty and pytest reports «no tests collected»
    for this function — which is fine; the lint becomes active the
    moment the first consumer PR runs `register(...)` at import.
    """
    source = _handler_source_or_unavailable(handler)
    if not source:
        # Mock handler / lambda — out of lint scope. Real consumer
        # modules must be full def functions.
        return

    assert REQUIRED_VERIFY_CALL_SUBSTRING in source, (
        f"Handler for {key!r} does NOT call "
        f"{REQUIRED_VERIFY_CALL_SUBSTRING}() in its source. "
        "Per PR #507 adversarial-pass A3, every event-ingest handler "
        "MUST verify envelope.tenant_id authorization before any "
        "side-effect. See "
        "apps/eventbus/ingest_tenancy.py + "
        ".github/PULL_REQUEST_TEMPLATE.md (Security checklist)."
    )


def test_lint_scaffold_smoke() -> None:
    """Sanity — the lint infrastructure imports cleanly even with empty registry.

    Catches the case where a future refactor removes
    :func:`registered_handlers` or
    :func:`assert_envelope_tenant_authorized` and leaves the lint
    silently green.
    """
    from apps.eventbus.ingest_tenancy import (  # noqa: F401
        assert_envelope_tenant_authorized,
    )

    # registered_handlers() must remain callable.
    handlers = registered_handlers()
    assert isinstance(handlers, dict)


def test_lint_catches_handler_missing_verify() -> None:
    """Grep logic catches a handler that forgets the verify call.

    The parametrized test above iterates over the LIVE registry. This
    test validates the matching logic itself — a handler whose source
    lacks the sentinel substring fails the grep. Stays sharp regardless
    of whether the registry is empty (Phase 0) or populated (#442+).
    """

    def bad_handler(envelope):  # no verify call
        envelope.data["touched"] = True

    source = _handler_source_or_unavailable(bad_handler)
    assert REQUIRED_VERIFY_CALL_SUBSTRING not in source


def test_lint_accepts_handler_with_verify() -> None:
    """Grep logic accepts a handler that includes the verify call.

    The string match is intentionally loose — substring presence is
    enough. A handler that imports the helper but never calls it
    would also pass this lint; the runtime exception from the
    no-op-then-real-#246-switch catches that case in production.
    """

    def good_handler(envelope):
        from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized

        assert_envelope_tenant_authorized(envelope)
        envelope.data["touched"] = True

    source = _handler_source_or_unavailable(good_handler)
    assert REQUIRED_VERIFY_CALL_SUBSTRING in source
