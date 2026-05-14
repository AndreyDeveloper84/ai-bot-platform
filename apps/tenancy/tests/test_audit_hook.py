"""Audit-writer callback registry tests (Sprint 8 review P1-cycle1).

Sanity for :mod:`apps.tenancy.audit_hook` — the callback registry that
breaks the audit↔tenancy load cycle.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.tenancy import audit_hook


@pytest.fixture(autouse=True)
def _restore_writer() -> Any:
    """Save + restore the registered writer around each test.

    apps.audit.apps.AuditConfig.ready() installs the production writer
    at process boot. Tests muck with the registry; we restore it so
    sibling tests don't see a None writer.
    """
    saved = audit_hook.get_audit_writer()
    yield
    audit_hook.register_audit_writer(saved)


class TestRegisterAndCall:
    def test_no_writer_returns_false(self) -> None:
        audit_hook.register_audit_writer(None)
        assert audit_hook.write("test.nothing", {"key": "v"}) is False

    def test_writer_called_with_action_and_payload(self) -> None:
        seen: list[tuple[str, dict[str, Any]]] = []

        def writer(action: str, payload: dict[str, Any]) -> None:
            seen.append((action, payload))

        audit_hook.register_audit_writer(writer)
        result = audit_hook.write("test.called", {"k": 1})
        assert result is True
        assert seen == [("test.called", {"k": 1})]

    def test_writer_exception_swallowed(self) -> None:
        """Audit writer must never crash the caller."""

        def boom_writer(action: str, payload: dict[str, Any]) -> None:
            raise RuntimeError("synthetic")

        audit_hook.register_audit_writer(boom_writer)
        # Should NOT raise. Return value is True (writer ran, even if failed).
        assert audit_hook.write("test.boom", {}) is True


class TestIdempotency:
    def test_re_registering_same_writer_noop(self) -> None:
        def writer(action: str, payload: dict[str, Any]) -> None:
            pass

        audit_hook.register_audit_writer(writer)
        audit_hook.register_audit_writer(writer)  # second call, same fn
        assert audit_hook.get_audit_writer() is writer

    def test_register_none_clears_registry(self) -> None:
        def writer(action: str, payload: dict[str, Any]) -> None:
            pass

        audit_hook.register_audit_writer(writer)
        audit_hook.register_audit_writer(None)
        assert audit_hook.get_audit_writer() is None
        assert audit_hook.write("test", {}) is False


class TestProductionWiringIntegration:
    """The AppConfig.ready() hook should leave a writer registered
    when apps.audit is in INSTALLED_APPS.
    """

    def test_writer_registered_at_boot(self) -> None:
        # The test runner already booted Django with apps.audit in
        # INSTALLED_APPS, so the registered writer should not be None.
        assert audit_hook.get_audit_writer() is not None
