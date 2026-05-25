"""Tests for Q12aChainTerminatorFilter — admin filter for #530.

The filter surfaces partial-failure reschedule audit rows (rows where
``payload`` contains the key ``q12a_chain_terminator``) so operators
can find «manual rebook required» tickets from the admin UI without
writing raw SQL.

Uses Django's ``payload__has_key`` JSONField lookup (Postgres ``?``
operator + GIN-indexable; SQLite ``JSON_TYPE ... IS NOT NULL``).
The earlier ``payload__contains={...}`` design was rejected because
SQLite parses ``= true`` as an identifier reference, returning 0 rows
in the test backend.
"""

from __future__ import annotations

import pytest

from apps.audit.admin import Q12aChainTerminatorFilter
from apps.audit.models import AuditLog
from apps.audit.services import write_audit
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="audit-flt", name="Audit filter test")


def _make_filter(value: str | None) -> Q12aChainTerminatorFilter:
    """Construct the filter with a chosen lookup value, bypassing the
    Django admin request plumbing. Django 5.x expects ``params`` as
    ``{parameter_name: [value]}`` (multi-value dict shape), not
    ``{parameter_name: value}`` — passing the bare string makes
    ``self.value()`` silently return None."""

    class _StubRequest:
        GET = {} if value is None else {"q12a_terminator": value}

    params: dict = {} if value is None else {"q12a_terminator": [value]}
    return Q12aChainTerminatorFilter(
        request=_StubRequest(),  # type: ignore[arg-type]
        params=params,
        model=AuditLog,
        model_admin=None,  # type: ignore[arg-type]
    )


class TestQ12aChainTerminatorFilter:
    """The filter has 3 modes:
    * value == "yes"  → only chain-terminator rows
    * value == "no"   → all rows EXCEPT chain-terminator rows
    * value == None   → no filtering (passes queryset through)
    """

    def test_yes_returns_only_terminator_rows(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            write_audit(
                "booking.reschedule.partial",
                payload={"q12a_chain_terminator": True, "old_record_id": "r1"},
            )
            write_audit("booking.confirmed", payload={"foo": "bar"})
            write_audit(
                "booking.reschedule.partial",
                payload={"q12a_chain_terminator": True, "old_record_id": "r2"},
            )

        flt = _make_filter("yes")
        result = flt.queryset(None, AuditLog.all_tenants.all())
        # Only the 2 terminator rows.
        assert result.count() == 2
        for row in result:
            assert row.payload.get("q12a_chain_terminator") is True

    def test_no_excludes_terminator_rows(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            write_audit(
                "booking.reschedule.partial",
                payload={"q12a_chain_terminator": True},
            )
            write_audit("booking.confirmed", payload={"foo": "bar"})
            write_audit("booking.cancelled", payload={})

        flt = _make_filter("no")
        result = flt.queryset(None, AuditLog.all_tenants.all())
        # 2 non-terminator rows.
        assert result.count() == 2
        for row in result:
            assert row.payload.get("q12a_chain_terminator") is not True

    def test_none_passes_queryset_through(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            write_audit(
                "booking.reschedule.partial",
                payload={"q12a_chain_terminator": True},
            )
            write_audit("booking.confirmed", payload={"foo": "bar"})

        flt = _make_filter(None)
        result = flt.queryset(None, AuditLog.all_tenants.all())
        # Pass-through — no filtering applied.
        assert result.count() == 2

    def test_lookups_present(self) -> None:
        """The admin sidebar lookups must surface both 'yes' and 'no'
        options so operators can flip between «show terminator tickets»
        and «hide them»."""

        flt = _make_filter(None)
        lookups = flt.lookups(request=None, model_admin=None)
        keys = [k for k, _label in lookups]
        assert "yes" in keys
        assert "no" in keys

    def test_q12a_terminator_reason_preserved_for_grouping(self, tenant: Tenant) -> None:
        """The payload also carries ``q12a_chain_terminator_reason``
        (today: always ``partial_failure``). The filter doesn't slice
        by reason today — but the field stays queryable via the row
        detail view + future filters can join on it. This test pins
        the contract so a refactor that drops the reason field is
        loudly caught.
        """

        with tenant_scope(tenant):
            write_audit(
                "booking.reschedule.partial",
                payload={
                    "q12a_chain_terminator": True,
                    "q12a_chain_terminator_reason": "partial_failure",
                },
            )

        row = AuditLog.all_tenants.get(action="booking.reschedule.partial")
        assert row.payload.get("q12a_chain_terminator_reason") == "partial_failure"


class TestWriterContractGuard:
    """Static guard against contract drift on ``q12a_chain_terminator``.

    The :class:`Q12aChainTerminatorFilter` relies on the contract
    «writer ALWAYS sets the key to True, never False». Any future
    writer that stores ``q12a_chain_terminator: False`` (e.g. to mean
    «explicitly not terminator») would silently break the filter:
    ``has_key`` would return both True-rows AND False-rows.

    This guard greps the apps/ source tree for literal
    ``q12a_chain_terminator.*False`` patterns. If a match is found,
    fail loudly — the developer must either restore the «True-only»
    convention OR switch the filter to a Postgres-only
    ``payload__contains={...}`` lookup gated by backend.
    """

    def test_no_false_writer_in_source_tree(self) -> None:
        import re
        from pathlib import Path

        apps_root = Path(__file__).resolve().parents[3] / "apps"
        assert apps_root.exists(), f"Source root not found: {apps_root}"

        # Forbidden patterns: explicit False assignment in any form
        # ('q12a_chain_terminator': False, q12a_chain_terminator=False,
        # "q12a_chain_terminator": false in JSON literals).
        pattern = re.compile(
            r"q12a_chain_terminator\s*[:=]\s*[Ff]alse",
        )
        offenders: list[tuple[Path, int, str]] = []
        for py in apps_root.rglob("*.py"):
            # Skip THIS guard file (it contains the literal pattern).
            if py.name == "test_admin_filters.py":
                continue
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append((py, lineno, line.rstrip()))

        assert not offenders, (
            "q12a_chain_terminator: False writer detected — this BREAKS the "
            "Q12aChainTerminatorFilter `has_key` semantics. Options: "
            "(1) restore True-only convention; "
            "(2) switch admin filter to payload__contains={'q12a_chain_terminator': True} "
            "gated by connection.vendor=='postgresql'. Offenders:\n"
            + "\n".join(f"  {p}:{lineno}: {line}" for p, lineno, line in offenders)
        )


class TestAdminChangelistRender:
    """Integration smoke test — render the AuditLog changelist with the
    filter active. Catches Django middleware / auth wiring issues that
    unit tests with ``_make_filter`` bypass.
    """

    def test_changelist_with_terminator_filter_active(self, tenant: Tenant) -> None:
        import secrets as _secrets

        from django.contrib.auth import get_user_model
        from django.test import Client

        with tenant_scope(tenant):
            write_audit(
                "booking.reschedule.partial",
                payload={"q12a_chain_terminator": True},
            )

        # Create a staff superuser so Django admin allows changelist access.
        # Generate a one-shot random password — avoids tripping the
        # detect-secrets pre-commit hook on a hardcoded string literal.
        ops_password = _secrets.token_urlsafe(24)
        User = get_user_model()
        User.objects.create_user(
            username="ops-test",
            password=ops_password,
            is_staff=True,
            is_superuser=True,
        )

        client = Client()
        client.login(username="ops-test", password=ops_password)

        response = client.get("/admin/audit/auditlog/?q12a_terminator=yes")
        assert response.status_code == 200
        # Sanity: «Chain terminators» label from the filter's lookups
        # should appear in the rendered sidebar (or the URL param should
        # at least round-trip; both are weak signals but catch wiring
        # breakage).
        body = response.content.decode("utf-8")
        assert "q12a_terminator" in body or "Chain terminator" in body
