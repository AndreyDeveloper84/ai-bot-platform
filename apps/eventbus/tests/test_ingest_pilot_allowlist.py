"""T-02 / OD-T02-1 — pilot-scoped tenant + event allowlists for the ingest.

Covers the whole authorization matrix introduced by PR-T02-1:

* configuration parsing (:mod:`apps.eventbus.ingest_allowlist`) — strict,
  deny-by-default, and never widened by a malformed value;
* the authorization decision in
  :func:`apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized` —
  happy path, every reject reason, and the precedence order against both
  the canonical relationship check and the legacy global fail-open;
* cross-tenant isolation at the handler boundary (an allowlisted tenant B
  must not be able to mutate tenant A's proxy via a shared appointment_id);
* dedupe/transaction semantics — a rejected event must NOT leave a
  successful ``IngestDedupe`` row, so a retry after fixing the config still
  works;
* the boot-time configuration checks in
  :mod:`apps.eventbus.startup_checks`.

The happy path here uses the ALLOWLIST, never
``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN``. The fail-open flag appears only
in the dedicated escape-hatch class, which asserts it still works AND that
it screams in the log when it does.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import pytest
from django.test import override_settings

from apps.eventbus.ingest_allowlist import (
    AllowlistConfigurationError,
    parse_event_allowlist,
    parse_tenant_allowlist,
    resolve_allowed_events,
    resolve_allowed_tenants,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import (
    TenantAuthorizationError,
    assert_envelope_tenant_authorized,
)
from apps.tenancy.models import Tenant


# Two distinct pilot tenants — A is the "real" one, B is the attacker /
# neighbour used by the cross-tenant scenarios.
TENANT_A = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
TENANT_B = "1d4f8a2c-6b3e-4a7d-9c1f-2e5b8d3a6c9f"
# Allowlisted in settings but deliberately never created in the DB.
TENANT_GHOST = "7e2b9c4d-8a1f-4b3e-a6d2-5c9f1b8e3a7d"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"

# The OD-T02-2 + OD-T02-5 pilot event set. Note what is ABSENT:
# ``booking.rescheduled`` is the repo-local legacy alias and is explicitly
# NOT part of the pilot scope — ``appointment.rescheduled`` is the canonical
# cross-repo name. ``booking.confirmed`` was added by PR-T02-2 to close the
# awaiting-payment reminder lifecycle.
PILOT_EVENTS = frozenset(
    {"booking.created", "booking.confirmed", "booking.cancelled", "appointment.rescheduled"}
)


def _envelope(
    *,
    event_name: str = "booking.created",
    tenant_id: str | None = TENANT_A,
    user_id: str = AYLA_USER_ID,
    event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
    data: dict[str, Any] | None = None,
) -> IngestEnvelope:
    """A schema-valid envelope, as it would arrive post-HMAC-verification."""
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=user_id,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data if data is not None else {},
    )


def _pilot_settings(**overrides: Any) -> Any:
    """``override_settings`` preloaded with the canonical pilot config."""
    config: dict[str, Any] = {
        "EVENT_INGEST_ALLOWED_TENANTS": frozenset({TENANT_A}),
        "EVENT_INGEST_ALLOWED_EVENTS": PILOT_EVENTS,
        "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN": False,
    }
    config.update(overrides)
    return override_settings(**config)


# ─── configuration parsing ─────────────────────────────────────────────────


class TestTenantAllowlistParsing:
    """``EVENT_INGEST_ALLOWED_TENANTS`` — strict CSV → canonical UUID set."""

    def test_empty_string_is_deny_all(self) -> None:
        assert parse_tenant_allowlist("") == frozenset()

    def test_unset_is_deny_all(self) -> None:
        assert parse_tenant_allowlist(None) == frozenset()

    def test_whitespace_only_is_deny_all(self) -> None:
        assert parse_tenant_allowlist("   \t ") == frozenset()

    def test_single_uuid(self) -> None:
        assert parse_tenant_allowlist(TENANT_A) == frozenset({TENANT_A})

    def test_csv_of_uuids(self) -> None:
        assert parse_tenant_allowlist(f"{TENANT_A},{TENANT_B}") == frozenset({TENANT_A, TENANT_B})

    def test_surrounding_whitespace_trimmed(self) -> None:
        assert parse_tenant_allowlist(f"  {TENANT_A} ,\t{TENANT_B}  ") == frozenset(
            {TENANT_A, TENANT_B}
        )

    def test_uppercase_normalized_to_canonical_lowercase(self) -> None:
        assert parse_tenant_allowlist(TENANT_A.upper()) == frozenset({TENANT_A})

    def test_duplicates_collapsed(self) -> None:
        raw = f"{TENANT_A},{TENANT_A},{TENANT_A.upper()}"
        assert parse_tenant_allowlist(raw) == frozenset({TENANT_A})

    def test_accepts_already_normalized_frozenset(self) -> None:
        assert parse_tenant_allowlist(frozenset({TENANT_A})) == frozenset({TENANT_A})

    @pytest.mark.parametrize(
        "raw",
        [
            "not-a-uuid",
            "9c3a7e1b4d524f8eb3a17c2d8e1f0a5c",  # dash-less  # pragma: allowlist secret
            "{9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c}",  # brace-wrapped
            "urn:uuid:9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
            "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5",  # one char short
            "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5cc",  # one char long
            "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1g0a5c",  # non-hex 'g'
        ],
    )
    def test_malformed_uuid_rejected(self, raw: str) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist(raw)

    def test_one_bad_entry_rejects_the_whole_value(self) -> None:
        """Partial acceptance would leave the operator's mental model wrong."""
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist(f"{TENANT_A},not-a-uuid")

    @pytest.mark.parametrize("raw", ["*", "**", "all", "ALL", "any", "%", f"{TENANT_A},*"])
    def test_wildcard_rejected(self, raw: str) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist(raw)

    @pytest.mark.parametrize("raw", [f"{TENANT_A},", f",{TENANT_A}", f"{TENANT_A},,{TENANT_B}"])
    def test_empty_element_rejected(self, raw: str) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist(raw)

    @pytest.mark.parametrize("raw", [123, True, {"a": 1}, [1, 2], b"abc"])
    def test_non_string_input_rejected(self, raw: Any) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist(raw)


class TestEventAllowlistParsing:
    """``EVENT_INGEST_ALLOWED_EVENTS`` — strict CSV → canonical name set."""

    def test_empty_is_deny_all(self) -> None:
        assert parse_event_allowlist("") == frozenset()
        assert parse_event_allowlist(None) == frozenset()

    def test_pilot_set(self) -> None:
        raw = "booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled"
        assert parse_event_allowlist(raw) == PILOT_EVENTS

    def test_whitespace_trimmed_and_duplicates_collapsed(self) -> None:
        raw = " booking.created , booking.created ,\tbooking.cancelled "
        assert parse_event_allowlist(raw) == frozenset({"booking.created", "booking.cancelled"})

    def test_case_normalized(self) -> None:
        assert parse_event_allowlist("Booking.Created") == frozenset({"booking.created"})

    def test_multi_segment_name(self) -> None:
        assert parse_event_allowlist("master.schedule.updated") == frozenset(
            {"master.schedule.updated"}
        )

    @pytest.mark.parametrize(
        "raw",
        ["*", "booking.*", "booking*", "all", "any", "booking.created,*"],
    )
    def test_wildcard_rejected(self, raw: str) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_event_allowlist(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            "booking",  # single segment — not a dotted event name
            "booking..created",  # empty segment
            ".booking.created",
            "booking.created.",
            "booking created",  # inner whitespace
            "booking.créated",  # non-ascii
        ],
    )
    def test_malformed_event_name_rejected(self, raw: str) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_event_allowlist(raw)

    @pytest.mark.parametrize("raw", ["booking.created,", ",booking.created"])
    def test_empty_element_rejected(self, raw: str) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_event_allowlist(raw)

    def test_underscore_is_legal_within_a_segment(self) -> None:
        """``booking.no_show`` is a real contract name — must still parse."""
        assert parse_event_allowlist("booking.no_show") == frozenset({"booking.no_show"})

    def test_underscore_is_not_a_segment_separator(self) -> None:
        """``booking_created`` must NOT parse as a dotted event name.

        Accepting it would let a typo'd underscore sit in the allowlist
        looking configured while silently denying the real event — the boot
        log would report ``allowlist_active`` the whole time.
        """
        with pytest.raises(AllowlistConfigurationError):
            parse_event_allowlist("booking_created")


class TestContainerTypeStrictness:
    """``_split_raw`` accepts explicit containers only, not "any Iterable".

    A ``dict`` is iterable, so a loose check silently accepted
    ``{uuid: "note"}`` as a one-element allowlist keyed on its keys. A
    generator is worse: consumed by the first read, so the startup check and
    the request path would disagree about what is configured.
    """

    def test_dict_rejected_even_with_valid_uuid_keys(self) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist({TENANT_A: "pilot tenant"})

    def test_generator_rejected(self) -> None:
        with pytest.raises(AllowlistConfigurationError):
            parse_tenant_allowlist(t for t in (TENANT_A,))

    @pytest.mark.parametrize("container", [list, tuple, set, frozenset])
    def test_explicit_containers_accepted(self, container: type) -> None:
        assert parse_tenant_allowlist(container([TENANT_A])) == frozenset({TENANT_A})


class TestResolveRevalidates:
    """``resolve_*`` must re-validate, not trust, whatever settings hold.

    A frozenset injected after settings load (``override_settings``, a live
    reload) never went through the environment parser. If ``resolve_*``
    fast-pathed on type alone, that value would bypass validation entirely.
    """

    def test_resolve_tenants_rejects_injected_garbage(self) -> None:
        with pytest.raises(AllowlistConfigurationError):
            resolve_allowed_tenants(frozenset({"not-a-uuid"}))

    def test_resolve_events_rejects_injected_wildcard(self) -> None:
        with pytest.raises(AllowlistConfigurationError):
            resolve_allowed_events(frozenset({"*"}))

    def test_resolve_passes_canonical_values_through(self) -> None:
        assert resolve_allowed_tenants(frozenset({TENANT_A})) == frozenset({TENANT_A})
        assert resolve_allowed_events(PILOT_EVENTS) == PILOT_EVENTS


class TestSettingsDefaults:
    def test_both_allowlists_default_to_deny_all(self) -> None:
        from django.conf import settings as dj_settings

        assert dj_settings.EVENT_INGEST_ALLOWED_TENANTS == frozenset()
        assert dj_settings.EVENT_INGEST_ALLOWED_EVENTS == frozenset()

    def test_global_fail_open_defaults_false(self) -> None:
        from django.conf import settings as dj_settings

        assert dj_settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN is False

    def test_ingest_allowlist_module_imports_nothing_from_django_or_apps(self) -> None:
        """``config/settings/base.py`` imports this module at settings-load time.

        That is only safe while the module is stdlib-only: the Django app
        registry does not exist yet at that point, and ``django.conf.settings``
        is mid-construction. A future edit adding ``from django.conf import
        settings`` (or any ``apps.*`` import) would turn every ``manage.py``
        invocation into an opaque settings-import failure. The property is
        asserted in three docstrings; pin it here.
        """
        import ast
        from pathlib import Path

        import apps.eventbus.ingest_allowlist as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        offenders = [
            name
            for name in imported
            if name.split(".")[0] in {"django", "apps", "config", "celery"}
        ]
        assert offenders == [], (
            f"ingest_allowlist must stay stdlib-only (imported at settings load); "
            f"found: {offenders}"
        )

    def test_parent_packages_stay_import_free(self) -> None:
        """``apps/__init__.py`` and ``apps/eventbus/__init__.py`` must stay empty.

        Importing ``apps.eventbus.ingest_allowlist`` from settings executes
        both package ``__init__`` modules first. Guarding only the leaf
        module is not enough: a future import in either parent touching
        ``django.conf.settings`` turns every ``manage.py`` invocation into an
        opaque settings-recursion failure.
        """
        import ast
        from pathlib import Path

        import apps
        import apps.eventbus

        for package in (apps, apps.eventbus):
            path = Path(package.__file__)  # type: ignore[arg-type]
            tree = ast.parse(path.read_text(encoding="utf-8"))
            statements = [
                node
                for node in tree.body
                if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
            ]
            assert statements == [], (
                f"{path} must stay empty — it executes during settings load. "
                f"Found {len(statements)} statement(s)."
            )


# ─── authorization: happy path ─────────────────────────────────────────────


@pytest.fixture
def tenant_a(db) -> Tenant:  # noqa: ANN001 — pytest-django fixture
    return Tenant.objects.create(id=TENANT_A, slug="t02-pilot-a", name="Pilot tenant A")


@pytest.fixture
def tenant_b(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(id=TENANT_B, slug="t02-pilot-b", name="Pilot tenant B")


@pytest.mark.django_db
class TestPilotHappyPath:
    """All four conditions hold → the envelope is admitted."""

    def test_allowlisted_tenant_and_event_with_existing_tenant_passes(
        self, tenant_a: Tenant
    ) -> None:
        with _pilot_settings():
            assert_envelope_tenant_authorized(_envelope(event_name="booking.created"))

    @pytest.mark.parametrize("event_name", sorted(PILOT_EVENTS))
    def test_every_pilot_event_passes(self, tenant_a: Tenant, event_name: str) -> None:
        with _pilot_settings():
            assert_envelope_tenant_authorized(_envelope(event_name=event_name))

    def test_uppercase_tenant_id_on_the_wire_is_normalized(self, tenant_a: Tenant) -> None:
        """Same tenant, different spelling — must resolve to the same decision."""
        with _pilot_settings():
            assert_envelope_tenant_authorized(_envelope(tenant_id=TENANT_A.upper()))

    def test_accepted_event_is_audit_logged(
        self, tenant_a: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="apps.eventbus.ingest_tenancy")
        with _pilot_settings():
            assert_envelope_tenant_authorized(_envelope(event_id="EV-ACCEPT-1"))

        line = next(
            r.getMessage() for r in caplog.records if "tenant_verify_accepted" in r.getMessage()
        )
        assert "verification_mode=pilot_allowlist" in line
        assert "event_id=EV-ACCEPT-1" in line
        assert "event_name=booking.created" in line
        assert f"tenant_id={TENANT_A}" in line
        assert "correlation_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890" in line
        # The allowlist verifies the TENANT dimension only — it never proves
        # user_id belongs to tenant_id. This field is the sole detective
        # control over that accepted residual risk.
        assert f"user_id={AYLA_USER_ID}" in line


# ─── authorization: tenant rejection ───────────────────────────────────────


@pytest.mark.django_db
class TestTenantRejection:
    def test_tenant_not_in_allowlist(self, tenant_a: Tenant, tenant_b: Tenant) -> None:
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(tenant_id=TENANT_B))
        assert "tenant_not_allowed" in str(exc.value)

    def test_allowlisted_tenant_absent_from_db(self) -> None:
        """An operator's allowlist entry is a claim; the DB is the check."""
        with (
            _pilot_settings(EVENT_INGEST_ALLOWED_TENANTS=frozenset({TENANT_GHOST})),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope(tenant_id=TENANT_GHOST))
        assert "tenant_not_found" in str(exc.value)

    def test_whitespace_padded_tenant_id_rejected(self, tenant_a: Tenant) -> None:
        """Whitespace is NOT stripped from the wire value.

        ``uuid.UUID(" <uuid> ")`` raises, so a padded tenant_id is something
        the handlers cannot resolve. Authorizing it would admit an envelope
        that then explodes downstream; reject it cleanly instead.
        """
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(tenant_id=f" {TENANT_A} "))
        assert "tenant_not_allowed" in str(exc.value)

    def test_log_injection_via_tenant_id_is_neutralized(
        self, tenant_a: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``tenant_id`` is only type-checked as ``str`` by the envelope parser.

        A holder of the shared HMAC secret can therefore put newlines in it
        and forge extra log records — e.g. a fake ``tenant_verify_accepted``
        line to hide a rejected spoof. Control characters must be stripped
        before interpolation.
        """
        caplog.set_level(logging.WARNING, logger="apps.eventbus.ingest_tenancy")
        forged = (
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n"
            "eventbus.ingest.tenant_verify_accepted verification_mode=pilot_allowlist"
        )
        with _pilot_settings(), pytest.raises(TenantAuthorizationError):
            assert_envelope_tenant_authorized(_envelope(tenant_id=forged))

        for record in caplog.records:
            assert "\n" not in record.getMessage()
            assert "\r" not in record.getMessage()
        assert not any("tenant_verify_accepted" in r.getMessage() for r in caplog.records), (
            "forged accept line leaked into the log"
        )

    def test_soft_disabled_tenant_rejected(self, tenant_a: Tenant) -> None:
        """A frozen tenant must not ingest, even while allowlisted."""
        tenant_a.is_active = False
        tenant_a.save(update_fields=["is_active"])

        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope())
        assert "tenant_not_found" in str(exc.value)

    def test_empty_tenant_allowlist_denies_even_allowlisted_event(self, tenant_a: Tenant) -> None:
        with (
            _pilot_settings(EVENT_INGEST_ALLOWED_TENANTS=frozenset()),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope())
        assert "tenant_not_allowed" in str(exc.value)

    def test_both_allowlists_empty_is_fail_closed(self, tenant_a: Tenant) -> None:
        with (
            _pilot_settings(
                EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
                EVENT_INGEST_ALLOWED_EVENTS=frozenset(),
            ),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope())
        assert "relationship_unavailable" in str(exc.value)

    def test_non_uuid_tenant_id_on_the_wire_rejected(self, tenant_a: Tenant) -> None:
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(tenant_id="../../etc/passwd"))
        assert "tenant_not_allowed" in str(exc.value)

    def test_rejected_event_is_audit_logged_with_reason(
        self, tenant_a: Tenant, tenant_b: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger="apps.eventbus.ingest_tenancy")
        with _pilot_settings(), pytest.raises(TenantAuthorizationError):
            assert_envelope_tenant_authorized(_envelope(tenant_id=TENANT_B, event_id="EV-REJECT-1"))

        line = next(
            r.getMessage() for r in caplog.records if "tenant_verify_rejected" in r.getMessage()
        )
        assert "reason=tenant_not_allowed" in line
        assert "event_id=EV-REJECT-1" in line
        assert "event_name=booking.created" in line
        assert f"tenant_id={TENANT_B}" in line
        assert f"user_id={AYLA_USER_ID}" in line


# ─── authorization: event rejection ────────────────────────────────────────


@pytest.mark.django_db
class TestEventRejection:
    def test_booking_rescheduled_is_not_in_pilot_scope(self, tenant_a: Tenant) -> None:
        """OD-T02-2 — the legacy alias is deliberately excluded."""
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(event_name="booking.rescheduled"))
        assert "event_not_allowed" in str(exc.value)

    @pytest.mark.parametrize(
        "event_name",
        [
            # booking.confirmed is now part of the pilot set (OD-T02-5).
            "booking.completed",
            "booking.no_show",
            "payment.captured",
            "review.created",
            "service.updated",
        ],
    )
    def test_contract_event_outside_pilot_scope_rejected(
        self, tenant_a: Tenant, event_name: str
    ) -> None:
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(event_name=event_name))
        assert "event_not_allowed" in str(exc.value)

    def test_empty_event_allowlist_denies_allowlisted_tenant(self, tenant_a: Tenant) -> None:
        with (
            _pilot_settings(EVENT_INGEST_ALLOWED_EVENTS=frozenset()),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope())
        assert "event_not_allowed" in str(exc.value)

    def test_case_tricked_event_name_does_not_match(self, tenant_a: Tenant) -> None:
        """Comparison is exact against the normalized lower-case allowlist."""
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(event_name="Booking.Created"))
        assert "event_not_allowed" in str(exc.value)

    def test_whitespace_padded_event_name_does_not_match(self, tenant_a: Tenant) -> None:
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(event_name=" booking.created "))
        assert "event_not_allowed" in str(exc.value)


# ─── authorization: configuration failure ──────────────────────────────────


@pytest.mark.django_db
class TestMalformedConfigurationDeniesAll:
    """A broken allowlist must never be read as "no restriction"."""

    def test_malformed_tenant_allowlist_denies(
        self, tenant_a: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger="apps.eventbus.ingest_tenancy")
        with (
            _pilot_settings(EVENT_INGEST_ALLOWED_TENANTS=frozenset({"not-a-uuid"})),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope())
        assert "malformed_configuration" in str(exc.value)

    def test_malformed_event_allowlist_denies(self, tenant_a: Tenant) -> None:
        with (
            _pilot_settings(EVENT_INGEST_ALLOWED_EVENTS=frozenset({"*"})),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope())
        assert "malformed_configuration" in str(exc.value)

    def test_wrong_type_denies(self, tenant_a: Tenant) -> None:
        with (
            _pilot_settings(EVENT_INGEST_ALLOWED_TENANTS=12345),
            pytest.raises(TenantAuthorizationError) as exc,
        ):
            assert_envelope_tenant_authorized(_envelope())
        assert "malformed_configuration" in str(exc.value)


# ─── precedence ────────────────────────────────────────────────────────────


class _FakeRelationshipQuerySet:
    def __init__(self, *, exists: bool) -> None:
        self._exists = exists

    def exists(self) -> bool:
        return self._exists


class _FakeRelationshipManager:
    def __init__(self, *, exists: bool) -> None:
        self._exists = exists

    def filter(self, **_kwargs: Any) -> _FakeRelationshipQuerySet:
        return _FakeRelationshipQuerySet(exists=self._exists)


def _fake_relationship_model(*, exists: bool) -> type:
    return type("TenantUserRelationship", (), {"objects": _FakeRelationshipManager(exists=exists)})


@pytest.mark.django_db
class TestPrecedence:
    """Rule 1 — a working full relationship check outranks the allowlist.

    The allowlist is a FALLBACK for when ``TenantUserRelationship`` cannot
    be imported. It must never override or weaken a live relationship
    verification: allowlisting a tenant cannot admit an envelope the real
    check would reject.
    """

    def test_relationship_check_wins_over_allowlist_when_it_rejects(
        self, tenant_a: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import apps.tenancy.models as tenancy_models

        monkeypatch.setattr(
            tenancy_models,
            "TenantUserRelationship",
            _fake_relationship_model(exists=False),
            raising=False,
        )
        # Tenant AND event are both allowlisted — the allowlist would say
        # yes. The relationship check says no, and it must win.
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope())
        assert "no_active_relationship" in str(exc.value)

    def test_relationship_check_wins_when_it_accepts_a_non_allowlisted_tenant(
        self, tenant_b: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The allowlist must not narrow a working canonical check either."""
        import apps.tenancy.models as tenancy_models

        monkeypatch.setattr(
            tenancy_models,
            "TenantUserRelationship",
            _fake_relationship_model(exists=True),
            raising=False,
        )
        with _pilot_settings():
            assert_envelope_tenant_authorized(_envelope(tenant_id=TENANT_B))

    def test_probe_import_race_fails_closed(
        self, tenant_a: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Probe says the model exists, the import then fails — deny.

        Falling through to the allowlist here would admit an allowlisted
        tenant with NO relationship verification, which is exactly the
        weakening precedence rule 1 forbids.
        """
        import apps.eventbus.ingest_tenancy as tenancy

        monkeypatch.setattr(tenancy, "_tenant_user_relationship_available", lambda: True)
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope())
        assert "tenant_verify_import_race" in str(exc.value)

    def test_null_tenant_contract_behaviour_unchanged(self) -> None:
        """Rule 0 — the allowlist does not touch tenant-null events."""
        # A nullable event with tenant_id=None still passes...
        assert_envelope_tenant_authorized(
            _envelope(event_name="user.profile.updated", tenant_id=None)
        )
        # ...and a non-nullable one still raises, allowlist or not.
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(
                _envelope(event_name="booking.created", tenant_id=None)
            )
        assert "tenant_id is null" in str(exc.value)

    def test_probe_failing_for_a_non_import_reason_fails_closed(
        self, tenant_a: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A BROKEN canonical check must not degrade to the allowlist.

        ``ImportError`` means "not shipped yet" and legitimately falls back.
        Anything else — ``AppRegistryNotReady`` on an eager worker, an error
        raised inside ``apps.tenancy.models``, a circular import mid rolling
        deploy — means the check exists and is malfunctioning. Silently
        answering "unavailable" there would downgrade every envelope to the
        allowlist, i.e. weaken precedence rule 1 from the other side.
        """
        import builtins

        real_import = builtins.__import__

        def _boom(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "apps.tenancy.models":
                raise RuntimeError("AppRegistryNotReady")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _boom)
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope())
        assert "tenant_verify_probe_error" in str(exc.value)

    def test_canonical_rejection_message_is_sanitized(
        self, tenant_a: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The #246 branch must not be a log-injection vector either.

        ``user_id``/``tenant_id`` are type-checked as non-empty ``str`` and
        nothing more, so a newline in either would let the holder of the
        shared HMAC secret forge extra log records out of the exception
        message the dispatcher writes to the log.
        """
        import apps.tenancy.models as tenancy_models

        monkeypatch.setattr(
            tenancy_models,
            "TenantUserRelationship",
            _fake_relationship_model(exists=False),
            raising=False,
        )
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(
                _envelope(user_id="victim\neventbus.ingest.tenant_verify_accepted")
            )
        message = str(exc.value)
        assert "\n" not in message
        assert "no_active_relationship" in message


# ─── the global fail-open escape hatch ─────────────────────────────────────


@pytest.mark.django_db
class TestGlobalFailOpenEscapeHatch:
    """The flag survives as an emergency hatch — bounded, loud, and last.

    These are the ONLY tests that turn it on. It is not the happy path for
    anything; the pilot allowlist is.
    """

    def test_flag_admits_what_the_allowlist_refused(self, tenant_a: Tenant) -> None:
        with _pilot_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
            EVENT_INGEST_ALLOWED_EVENTS=frozenset(),
            EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True,
        ):
            assert_envelope_tenant_authorized(_envelope())

    def test_flag_is_consulted_only_after_the_allowlist(
        self, tenant_a: Tenant, tenant_b: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Precedence rule 3 — the reject is still evaluated and logged."""
        caplog.set_level(logging.WARNING, logger="apps.eventbus.ingest_tenancy")
        with _pilot_settings(EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True):
            assert_envelope_tenant_authorized(_envelope(tenant_id=TENANT_B))

        messages = [r.getMessage() for r in caplog.records]
        # The allowlist decision was made and recorded...
        assert any(
            "tenant_verify_rejected" in m and "reason=tenant_not_allowed" in m for m in messages
        )
        # ...and the override that bulldozed it is flagged as critical.
        override = next(m for m in messages if "tenant_verify_fail_open" in m)
        assert "security=critical" in override
        assert "verification_mode=global_fail_open" in override
        assert "overridden_reason=tenant_not_allowed" in override


# ─── cross-tenant isolation at the handler boundary ────────────────────────


@pytest.mark.django_db
class TestCrossTenantIsolation:
    """Allowlisting admits an envelope into the consumer — nothing more.

    ``appointment_id`` is a global Ayla UUID. If tenant A and tenant B are
    both pilot-allowlisted, B is a legitimate ingest client; it must still
    be unable to touch A's cached rows. The handler-level
    ``_assert_proxy_tenant`` guard is what stops that, and this pins that
    the allowlist did not weaken it.
    """

    @pytest.fixture
    def _both_tenants_allowlisted(self) -> Any:
        return override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset({TENANT_A, TENANT_B}),
            EVENT_INGEST_ALLOWED_EVENTS=PILOT_EVENTS,
            EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=False,
        )

    def _created_payload(self, *, start_at: str) -> dict[str, Any]:
        return {
            "appointment_id": APPOINTMENT_ID,
            "master_id": "3f7a1b2c-9d8e-4a5b-8c7d-6e5f4a3b2c1d",
            "service_id": "2e6b9c1d-7a4f-4b3e-9d8c-1a2b3c4d5e6f",
            "start_at": start_at,
            "end_at": "2026-08-20T11:00:00+00:00",
            "status": "pending_payment",
            "price_total": "1800.00",
            "source": "bot",
        }

    def test_tenant_b_cannot_mutate_tenant_a_proxy(
        self, tenant_a: Tenant, tenant_b: Tenant, _both_tenants_allowlisted: Any
    ) -> None:
        from apps.booking.models import RemoteBookingProxy
        from apps.eventbus.consumers.booking import (
            handle_booking_cancelled,
            handle_booking_created,
        )

        with _both_tenants_allowlisted:
            # Tenant A legitimately creates the proxy.
            handle_booking_created(
                _envelope(
                    event_name="booking.created",
                    tenant_id=TENANT_A,
                    event_id="EV-A-CREATE",
                    data=self._created_payload(start_at="2026-08-20T10:00:00+00:00"),
                )
            )
            proxy = RemoteBookingProxy.all_tenants.get(appointment_id=APPOINTMENT_ID)
            assert str(proxy.tenant_id) == str(tenant_a.id)
            before = RemoteBookingProxy.all_tenants.filter(appointment_id=APPOINTMENT_ID).values()[
                0
            ]

            # Tenant B — allowlisted, HMAC-valid, schema-valid — targets
            # the SAME appointment_id.
            with pytest.raises(TenantAuthorizationError):
                handle_booking_cancelled(
                    _envelope(
                        event_name="booking.cancelled",
                        tenant_id=TENANT_B,
                        event_id="EV-B-CANCEL",
                        data={
                            "appointment_id": APPOINTMENT_ID,
                            "cancelled_by": "user",
                            "reason": "hostile",
                        },
                    )
                )

        after = RemoteBookingProxy.all_tenants.filter(appointment_id=APPOINTMENT_ID).values()[0]
        assert after == before, "tenant B mutated tenant A's proxy"

    def test_tenant_b_create_cannot_adopt_tenant_a_proxy(
        self, tenant_a: Tenant, tenant_b: Tenant, _both_tenants_allowlisted: Any
    ) -> None:
        from apps.booking.models import RemoteBookingProxy
        from apps.eventbus.consumers.booking import handle_booking_created

        with _both_tenants_allowlisted:
            handle_booking_created(
                _envelope(
                    tenant_id=TENANT_A,
                    event_id="EV-A-CREATE-2",
                    data=self._created_payload(start_at="2026-08-20T10:00:00+00:00"),
                )
            )
            before = RemoteBookingProxy.all_tenants.filter(appointment_id=APPOINTMENT_ID).values()[
                0
            ]

            with pytest.raises(TenantAuthorizationError):
                handle_booking_created(
                    _envelope(
                        tenant_id=TENANT_B,
                        event_id="EV-B-CREATE",
                        data=self._created_payload(start_at="2026-08-21T09:00:00+00:00"),
                    )
                )

        after = RemoteBookingProxy.all_tenants.filter(appointment_id=APPOINTMENT_ID).values()[0]
        assert after == before
        assert RemoteBookingProxy.all_tenants.filter(appointment_id=APPOINTMENT_ID).count() == 1


# ─── dedupe / transaction semantics ────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestDedupeSemantics:
    """A rejected event must not be recorded as successfully processed.

    If the dedupe row survived a tenant rejection, fixing the allowlist and
    letting Ayla retry would be a no-op — the event would be swallowed as a
    duplicate forever. The dispatcher's atomic block is what prevents that;
    this pins it for the T-02 reject path specifically.
    """

    def test_rejected_event_leaves_no_dedupe_row_and_retry_succeeds(self, tenant_a: Tenant) -> None:
        from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope
        from apps.eventbus.models import IngestDedupe

        envelope = _envelope(
            event_id="EV-RETRY-1",
            data={
                "appointment_id": APPOINTMENT_ID,
                "master_id": "3f7a1b2c-9d8e-4a5b-8c7d-6e5f4a3b2c1d",
                "service_id": "2e6b9c1d-7a4f-4b3e-9d8c-1a2b3c4d5e6f",
                "start_at": "2026-08-20T10:00:00+00:00",
                "end_at": "2026-08-20T11:00:00+00:00",
                "status": "pending_payment",
                "price_total": "1800.00",
                "source": "bot",
            },
        )

        # Misconfigured: the pilot tenant was never allowlisted.
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
            EVENT_INGEST_ALLOWED_EVENTS=PILOT_EVENTS,
            EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=False,
        ):
            result = dispatch_envelope(envelope)

        assert result.outcome == DispatchOutcome.HANDLER_EXCEPTION
        assert isinstance(result.exception, TenantAuthorizationError)
        assert not IngestDedupe.objects.filter(event_id="EV-RETRY-1").exists(), (
            "a rejected event must not be marked processed"
        )

        # Operator fixes the config; Ayla's retry now lands.
        with _pilot_settings():
            retry = dispatch_envelope(envelope)

        assert retry.outcome == DispatchOutcome.OK
        assert IngestDedupe.objects.filter(event_id="EV-RETRY-1").exists()


# ─── startup checks ────────────────────────────────────────────────────────


class TestStartupChecks:
    """Every ineffective or unsafe configuration gets a boot-time line."""

    def _run(self, caplog: pytest.LogCaptureFixture) -> list[str]:
        from apps.eventbus.startup_checks import check_event_ingest_allowlists

        caplog.set_level(logging.INFO, logger="apps.eventbus.startup_checks")
        check_event_ingest_allowlists()
        return [r.getMessage() for r in caplog.records]

    def test_both_empty_warns_fail_closed_and_says_it_is_safe(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
            EVENT_INGEST_ALLOWED_EVENTS=frozenset(),
        ):
            messages = self._run(caplog)
        line = next(m for m in messages if "allowlist_empty" in m)
        assert "fail-closed" in line
        assert "SAFE" in line

    def test_tenants_without_events_is_deny_all_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset({TENANT_A}),
            EVENT_INGEST_ALLOWED_EVENTS=frozenset(),
        ):
            messages = self._run(caplog)
        line = next(m for m in messages if "allowlist_half_configured" in m)
        assert "DENY ALL" in line
        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    def test_events_without_tenants_is_deny_all_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
            EVENT_INGEST_ALLOWED_EVENTS=PILOT_EVENTS,
        ):
            messages = self._run(caplog)
        line = next(m for m in messages if "allowlist_half_configured" in m)
        assert "DENY ALL" in line

    def test_fully_configured_reports_active_pilot_scope(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset({TENANT_A}),
            EVENT_INGEST_ALLOWED_EVENTS=PILOT_EVENTS,
        ):
            messages = self._run(caplog)
        line = next(m for m in messages if "allowlist_active" in m)
        assert "verification_mode=pilot_allowlist" in line
        assert "does NOT prove" in line
        # The allowlist gates tenant-scoped events only. Four contract
        # events carry tenant_id=null and bypass it — the boot line must
        # name them so nobody reads "active" as "complete ingest surface".
        assert "does NOT bound the tenant-null events" in line
        for exempt in (
            "user.profile.updated",
            "subscription.activated",
            "subscription.past_due",
            "billing.fee_charged",
        ):
            assert exempt in line

    def test_malformed_configuration_reports_deny_all(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset({"not-a-uuid"}),
            EVENT_INGEST_ALLOWED_EVENTS=PILOT_EVENTS,
        ):
            messages = self._run(caplog)
        line = next(m for m in messages if "allowlist_malformed" in m)
        assert "deny-all" in line

    def test_unknown_event_name_flagged_as_typo(self, caplog: pytest.LogCaptureFixture) -> None:
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset({TENANT_A}),
            EVENT_INGEST_ALLOWED_EVENTS=frozenset({"booking.creted"}),
        ):
            messages = self._run(caplog)
        line = next(m for m in messages if "allowlist_unknown_event" in m)
        assert "booking.creted" in line

    def test_global_fail_open_logs_security_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        from apps.eventbus.startup_checks import warn_if_tenant_verify_fail_open

        caplog.set_level(logging.WARNING, logger="apps.eventbus.startup_checks")
        with override_settings(EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True):
            warn_if_tenant_verify_fail_open()

        line = next(
            r.getMessage()
            for r in caplog.records
            if "tenant_verify_fail_open_enabled" in r.getMessage()
        )
        assert "security=critical" in line
        assert "DISABLED" in line

    def test_no_security_warning_when_fail_open_is_off(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from apps.eventbus.startup_checks import warn_if_tenant_verify_fail_open

        caplog.set_level(logging.WARNING, logger="apps.eventbus.startup_checks")
        with override_settings(EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=False):
            warn_if_tenant_verify_fail_open()

        assert not [
            r for r in caplog.records if "tenant_verify_fail_open_enabled" in r.getMessage()
        ]


# ─── end-to-end through the HTTP view ──────────────────────────────────────


INGEST_URL = "/api/v1/internal/events/ingest"
_E2E_SECRET = "t02-allowlist-e2e-secret"  # pragma: allowlist secret


@pytest.mark.django_db
class TestAllowlistThroughTheHttpView:
    """Prove the allowlist is actually wired into the request path.

    Every other test in this module calls the helper or the dispatcher
    directly. That leaves two things unproven: that the allowlist is
    reached at all from an HTTP request, and — acceptance criterion (8) —
    that HMAC verification still runs BEFORE it, so a forged signature is
    401 regardless of how generous the allowlist is.
    """

    @pytest.fixture(autouse=True)
    def _wired(self, settings, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
        settings.EVENT_INGEST_HMAC_SECRET = _E2E_SECRET
        settings.RATELIMIT_ENABLE = False
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
        settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_A})
        settings.EVENT_INGEST_ALLOWED_EVENTS = PILOT_EVENTS

        # SQLite test backend deadlocks on the timeout threadpool (A12).
        from apps.eventbus import views as _views
        from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

        monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)

    def _post(self, body: dict[str, Any], *, sign_with: str = _E2E_SECRET):
        import hashlib
        import hmac
        import json
        import time

        from django.test import Client

        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(sign_with.encode(), raw, hashlib.sha256).hexdigest()
        return Client().post(
            INGEST_URL,
            data=raw,
            content_type="application/json",
            HTTP_X_AYLA_EVENT_SIGNATURE=sig,
            HTTP_X_AYLA_EVENT_TIMESTAMP=str(int(time.time() * 1000)),
        )

    def _payload(self, *, tenant_id: str, event_name: str, event_id: str) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "event_name": event_name,
            "event_version": 1,
            "occurred_at": "2026-08-06T12:00:00+00:00",
            "tenant_id": tenant_id,
            "user_id": AYLA_USER_ID,
            "actor": "user",
            "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "causation_id": None,
            "data": {
                "appointment_id": APPOINTMENT_ID,
                "master_id": "3f7a1b2c-9d8e-4a5b-8c7d-6e5f4a3b2c1d",
                "service_id": "2e6b9c1d-7a4f-4b3e-9d8c-1a2b3c4d5e6f",
                "start_at": "2026-08-20T10:00:00+00:00",
                "end_at": "2026-08-20T11:00:00+00:00",
                "status": "pending_payment",
                "price_total": "1800.00",
                "source": "bot",
            },
        }

    def test_allowlisted_tenant_is_ingested_end_to_end(self, tenant_a: Tenant) -> None:
        from apps.eventbus.consumers.booking import register_booking_handlers
        from apps.eventbus.ingest_dispatcher import registered_handlers

        if ("booking.created", 1) not in registered_handlers():
            register_booking_handlers()

        response = self._post(
            self._payload(tenant_id=TENANT_A, event_name="booking.created", event_id="EV-E2E-OK")
        )
        assert response.status_code == 200, response.content

    def test_non_allowlisted_tenant_is_refused_end_to_end(
        self, tenant_a: Tenant, tenant_b: Tenant
    ) -> None:
        from apps.eventbus.consumers.booking import register_booking_handlers
        from apps.eventbus.ingest_dispatcher import registered_handlers
        from apps.eventbus.models import IngestDedupe

        if ("booking.created", 1) not in registered_handlers():
            register_booking_handlers()

        response = self._post(
            self._payload(tenant_id=TENANT_B, event_name="booking.created", event_id="EV-E2E-DENY")
        )
        assert response.status_code != 200
        assert not IngestDedupe.objects.filter(event_id="EV-E2E-DENY").exists()

    def test_invalid_hmac_is_401_even_for_an_allowlisted_tenant(self, tenant_a: Tenant) -> None:
        """AC (8) — signature verification precedes allowlist evaluation.

        A generous allowlist must never become a way around the HMAC gate.
        """
        response = self._post(
            self._payload(
                tenant_id=TENANT_A, event_name="booking.created", event_id="EV-E2E-BADSIG"
            ),
            sign_with="wrong-secret",  # pragma: allowlist secret
        )
        assert response.status_code == 401


# ─── the tenant-null carve-out, made visible ───────────────────────────────


@pytest.mark.django_db
class TestTenantNullCarveOutIsIntentional:
    """The allowlist gates TENANT-SCOPED events only — pinned, not implied.

    Four contract events carry ``tenant_id=null`` by design and are admitted
    by the null-tenant rule before the allowlist is consulted. That is
    pre-existing contract behaviour which OD-T02-1 says to preserve
    ("envelope без tenant_id — сохранить существующее контрактное
    поведение"), so it is deliberate, not an oversight.

    It is pinned here so the exemption is VISIBLE: a reader of this suite
    must not conclude that "allowlist configured" means "nothing else can
    be ingested".

    Two properties bound it, and both are pinned below: every admission is
    logged (``verification_mode=tenant_null_carveout``), and the carve-out
    is no longer evaluated ahead of the canonical probe — once
    ``TenantUserRelationship`` ships, a tenant-null envelope is verified
    against its subject. Without that second property the pilot compromise
    would have been frozen into the permanent design.
    """

    @pytest.mark.parametrize(
        "event_name",
        [
            "user.profile.updated",
            "subscription.activated",
            "subscription.past_due",
            "billing.fee_charged",
        ],
    )
    def test_tenant_null_events_bypass_the_allowlist(self, event_name: str) -> None:
        # A pilot config that lists NEITHER this event nor any tenant.
        with override_settings(
            EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
            EVENT_INGEST_ALLOWED_EVENTS=frozenset({"booking.created"}),
            EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=False,
        ):
            # Admitted anyway — by the null-tenant contract rule.
            assert_envelope_tenant_authorized(_envelope(event_name=event_name, tenant_id=None))

    def test_the_same_names_are_still_gated_when_tenant_scoped(self, tenant_a: Tenant) -> None:
        """The carve-out is about tenant_id=null, not about the name.

        The same event name WITH a tenant_id goes through the allowlist
        normally — so the exemption cannot be used as a general bypass by
        simply choosing one of these four names.
        """
        with _pilot_settings(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(
                _envelope(event_name="billing.fee_charged", tenant_id=TENANT_A)
            )
        assert "event_not_allowed" in str(exc.value)

    def test_carve_out_admission_is_audited(self, caplog: pytest.LogCaptureFixture) -> None:
        """The bypass leaves a trail — otherwise observability is inverted.

        Events the allowlist ADMITS log an accept line; without this, events
        that skip the allowlist entirely would log nothing at all, so the
        least-verified surface would be the least visible one.
        """
        with caplog.at_level(logging.INFO, logger="apps.eventbus.ingest_tenancy"):
            assert_envelope_tenant_authorized(
                _envelope(event_name="user.profile.updated", tenant_id=None)
            )
        accepted = [
            record.getMessage()
            for record in caplog.records
            if "tenant_verify_accepted" in record.getMessage()
        ]
        assert accepted, "tenant-null admission produced no audit line"
        assert "verification_mode=tenant_null_carveout" in accepted[0]
        assert f"user_id={AYLA_USER_ID}" in accepted[0]

    def test_carve_out_log_line_is_sanitized(self, caplog: pytest.LogCaptureFixture) -> None:
        """``user_id`` is publisher-controlled on this path — and unverified."""
        with caplog.at_level(logging.INFO, logger="apps.eventbus.ingest_tenancy"):
            assert_envelope_tenant_authorized(
                _envelope(
                    event_name="user.profile.updated",
                    tenant_id=None,
                    user_id="victim\ntenant_verify_accepted forged=1",
                )
            )
        rendered = [
            record.getMessage()
            for record in caplog.records
            if "tenant_null_carveout" in record.getMessage()
        ]
        assert rendered
        assert "\n" not in rendered[0]

    def test_carve_out_verifies_the_subject_once_the_model_ships(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Post-#246 the exemption stops being unconditional.

        A tenant-null event has no tenant to bind to, but it still names a
        SUBJECT. Once the canonical model exists, that subject must be a
        user the platform actually knows — which is what kills the
        "arbitrary attacker-chosen ``user_id``" primitive.
        """
        import apps.tenancy.models as tenancy_models

        monkeypatch.setattr(
            tenancy_models,
            "TenantUserRelationship",
            _fake_relationship_model(exists=False),
            raising=False,
        )
        with pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(
                _envelope(event_name="user.profile.updated", tenant_id=None)
            )
        assert "no_active_relationship_user_scope" in str(exc.value)

    def test_carve_out_admits_a_known_subject_once_the_model_ships(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import apps.tenancy.models as tenancy_models

        monkeypatch.setattr(
            tenancy_models,
            "TenantUserRelationship",
            _fake_relationship_model(exists=True),
            raising=False,
        )
        assert_envelope_tenant_authorized(
            _envelope(event_name="user.profile.updated", tenant_id=None)
        )
