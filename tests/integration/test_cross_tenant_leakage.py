"""Cross-tenant leakage scanner (DRF-430 / E1).

Pattern ported from Ayla `tenants/tests/test_model_fks.py` (blob
``15b48c6d``), generalised to discover models via Django's app
registry instead of a hardcoded list. Sprint 1 has fewer tenant-FK
models than Ayla; new ones added in Sprint 2+ are caught automatically.

What this scanner verifies:
  For every domain model that has a ``tenant`` FK to ``tenancy.Tenant``:
    1. Default manager (``Model.objects``) filters to the current tenant.
    2. With NO tenant context in scope, strict mode raises
       ``CrossTenantError``; audit mode returns empty + writes an audit
       log; off mode returns the full queryset.
    3. ``Model.all_tenants`` escape hatch always returns every row.

The scanner is intentionally "single source of truth" for the
multi-tenant contract. Every new model that holds tenant-owned data
must pick up the contract automatically — there's no opt-in.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps
from django.db import models

from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.managers import TenantScopedManager
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _discover_tenant_scoped_models() -> list[type[models.Model]]:
    """Return every concrete model in apps/* that:

    1. Has a FK field named ``tenant`` pointing to ``tenancy.Tenant``.
    2. Has its default manager set to ``TenantScopedManager`` (or
       subclass thereof).

    Filters out abstract / proxy / `tenancy.Tenant` itself.
    """

    found: list[type[models.Model]] = []
    for model in django_apps.get_models():
        if model is Tenant:
            continue
        if model._meta.abstract or model._meta.proxy:
            continue
        try:
            tenant_field = model._meta.get_field("tenant")
        except Exception:  # noqa: BLE001 — no tenant field
            continue
        # Must be a FK to tenancy.Tenant.
        if not isinstance(tenant_field, models.ForeignKey):
            continue
        if tenant_field.related_model is not Tenant:
            continue
        if not isinstance(model._default_manager, TenantScopedManager):
            continue
        found.append(model)
    return found


# Collect once at module load — pytest parametrize wants a concrete list.
SCANNED_MODELS = _discover_tenant_scoped_models()


# Minimum required fields per scoped model to satisfy NOT-NULL constraints.
# When a new tenant-scoped model lands and trips this scanner, add a row here
# (or the model itself should have sensible defaults for every non-tenant
# field). The factory only fills NOT-NULL columns that have no default —
# scanner tests don't care about field *values*, only that we can insert.
#
# Values can be:
#   * a static string  → used as-is (with ``-{suffix}`` appended when suffix is given)
#   * an empty string  → replaced by the suffix
#   * a callable       → ``factory(tenant, suffix)`` returns the field value
#                        (used when the value is itself a model instance —
#                        e.g. Conversation needs a BotUser FK target)
_MODEL_REQUIRED_FIELDS: dict[str, dict[str, object]] = {
    "AuditLog": {"action": "scanner.test"},
    # BotUser carries `(channel, channel_user_id)` as the natural key; both
    # are NOT NULL CharFields without defaults. Generate a unique
    # channel_user_id per row to dodge the unique_together constraint.
    "BotUser": {"channel": "max", "channel_user_id": ""},
    # Conversation requires a non-null bot_user FK. Lazy-import inside
    # the factory so the test module imports stay clean when identity
    # isn't yet in INSTALLED_APPS during early Sprint 2 development.
    "Conversation": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(tenant, suffix),
    },
    "Message": {
        "conversation": lambda tenant, suffix: _make_conversation_for_scanner(tenant, suffix),
        "role": "user",
    },
    # Sprint 3 / A1: ConsentRecord needs bot_user FK + non-blank
    # consent_type + non-blank source.
    "ConsentRecord": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(tenant, suffix),
        "consent_type": "personal_data",
        "source": "scanner-test",
    },
    # Sprint 3 / C1: AdminTask needs bot_user + conversation + task_type.
    # The same BotUser must back both FKs — Conversation already owns one,
    # and inventing a second bot_user-per-AdminTask collides on the
    # (tenant, channel, channel_user_id) unique constraint. The factory below
    # returns a shared (bot_user, conversation) pair via a per-call cache.
    "AdminTask": {
        "bot_user": lambda tenant, suffix: _make_admin_task_pair(tenant, suffix)[0],
        "conversation": lambda tenant, suffix: _make_admin_task_pair(tenant, suffix)[1],
        "task_type": "handoff",
    },
    # Sprint 4 / A1: PromptVersion needs skill_name + body + version + created_by.
    # created_by is an auth.User FK — the scanner creates one per row.
    "PromptVersion": {
        "skill_name": "scanner-test",
        "body": "scanner body",
        "version": 1,
        "created_by": lambda tenant, suffix: _make_user_for_scanner(suffix),
    },
    # Sprint 4 / A2: ThresholdConfig — key + value + version + applied_to.
    # `applied_to` differs per row to dodge unique_together on the multi-tenant
    # variant.
    "ThresholdConfig": {
        "key": "scanner_threshold",
        # Callable so the scanner's suffix-append doesn't corrupt the
        # Decimal-parseable string.
        "value": lambda tenant, suffix: "0.5",
        "applied_to": lambda tenant, suffix: f"skill-{suffix or 'x'}",
        "version": 1,
    },
    # Sprint 4 / A3: DisclaimerLibrary needs category + risk_level + text.
    "DisclaimerLibrary": {
        "category": "general",
        "risk_level": "low",
        "text": "scanner disclaimer",
        "version": 1,
    },
    # Sprint 4 / B1: Experiment — slug name (per-row variant) + variants list.
    "Experiment": {
        "name": lambda tenant, suffix: f"scanner-exp-{suffix or 'x'}",
        "hypothesis": "scanner test",
        "primary_kpi": "test_kpi",
        "variants": [{"name": "control", "weight": 50}, {"name": "v2", "weight": 50}],
    },
    # Sprint 4 / B1: UserAssignment — needs a (bot_user, experiment) pair.
    "UserAssignment": {
        "bot_user": lambda tenant, suffix: _make_experiment_pair(tenant, suffix)[0],
        "experiment": lambda tenant, suffix: _make_experiment_pair(tenant, suffix)[1],
        "variant": "control",
    },
    # Sprint 4 / B1: Holdout — OneToOne bot_user. Need a fresh bot_user.
    "Holdout": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(
            tenant, f"holdout-{suffix or 'x'}"
        ),
    },
    # Sprint 5 / A1: ReplayTrace — trace_id + pipeline_steps + expires_at required.
    "ReplayTrace": {
        "trace_id": lambda tenant, suffix: f"scanner-trace-{suffix or 'x'}",
        "pipeline_steps": lambda tenant, suffix: [],
        "redaction_method": "regex_v1",
        "expires_at": lambda tenant, suffix: _future_datetime(),
    },
}


def _make_bot_user_for_scanner(tenant, suffix: str):
    """Inline BotUser factory for the Conversation row factory.

    Imported lazily so this module loads even before identity migrations
    have run in development. Uses ``all_tenants`` to bypass the
    tenant-scoped manager (the scanner sets up fixtures outside a
    tenant_scope).
    """

    from apps.identity.models import BotUser

    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"scanner-{suffix or 'x'}",
    )


def _make_conversation_for_scanner(tenant, suffix: str):
    """Inline Conversation factory for the Message row factory."""

    from apps.conversations.models import Conversation

    bot_user = _make_bot_user_for_scanner(tenant, suffix)
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


# Cache per (model __name__, tenant.id, suffix) → (bot_user, conversation).
# Scoped via the dict so the same FK target is reused inside a single
# `_create_row` call (which invokes both lambdas with the same suffix).
_ADMIN_TASK_PAIRS: dict[tuple[str, str], tuple[object, object]] = {}


def _make_user_for_scanner(suffix: str):
    """Inline auth.User factory for PromptVersion.created_by FK."""

    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa: N806
    return User.objects.create_user(username=f"scanner-user-{suffix or 'x'}")


def _future_datetime():
    """Helper: future timestamp for ReplayTrace.expires_at."""

    from datetime import timedelta

    from django.utils import timezone

    return timezone.now() + timedelta(days=30)


_EXPERIMENT_PAIRS: dict[tuple[str, str], tuple[object, object]] = {}


def _make_experiment_pair(tenant, suffix: str):
    """Return a shared (BotUser, Experiment) pair for a UserAssignment row.

    Cached per (tenant.id, suffix) so the (bot_user, experiment) unique
    constraint stays intact when the same suffix is used for both FK
    factories within the same _create_row call.
    """

    from apps.experiments.models import Experiment

    key = (str(tenant.id), suffix)
    cached = _EXPERIMENT_PAIRS.get(key)
    if cached is not None:
        return cached
    bot_user = _make_bot_user_for_scanner(tenant, f"ua-{suffix or 'x'}")
    experiment = Experiment.all_tenants.create(
        tenant=tenant,
        name=f"scanner-ua-{suffix or 'x'}",
        hypothesis="ua scanner",
        primary_kpi="test",
        variants=[{"name": "control", "weight": 100}],
    )
    _EXPERIMENT_PAIRS[key] = (bot_user, experiment)
    return bot_user, experiment


def _make_admin_task_pair(tenant, suffix: str):
    """Return a shared (BotUser, Conversation) pair for an AdminTask row.

    Both AdminTask FKs (bot_user + conversation) need to reference the
    *same* BotUser — Conversation already owns one, and creating a second
    one trips the (tenant, channel, channel_user_id) unique constraint.
    Cached per (tenant.id, suffix) for the duration of a single row build.
    """

    from apps.conversations.models import Conversation

    key = (str(tenant.id), suffix)
    cached = _ADMIN_TASK_PAIRS.get(key)
    if cached is not None:
        return cached
    bot_user = _make_bot_user_for_scanner(tenant, suffix or "at")
    conversation = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    _ADMIN_TASK_PAIRS[key] = (bot_user, conversation)
    return bot_user, conversation


def _create_row(model: type[models.Model], *, tenant, suffix: str = "") -> models.Model:
    """Create one row for ``model`` under ``tenant``, satisfying required fields.

    ``suffix`` disambiguates rows created in the same test (e.g. one per
    tenant) so unique_together constraints don't trip the scanner.
    """

    kwargs = {"tenant": tenant}
    extras = _MODEL_REQUIRED_FIELDS.get(model.__name__, {})
    for field_name, base_value in extras.items():
        if callable(base_value):
            kwargs[field_name] = base_value(tenant, suffix)
        elif isinstance(base_value, str) and suffix:
            kwargs[field_name] = f"{base_value}-{suffix}" if base_value else suffix
        else:
            kwargs[field_name] = base_value
    # ``all_tenants`` is the escape-hatch manager declared on every
    # TenantScopedManager-using model — verified at runtime by
    # ``_discover_tenant_scoped_models``. mypy can't see the attribute
    # on the generic ``type[Model]`` annotation, so suppress narrowly.
    return model.all_tenants.create(**kwargs)  # type: ignore[attr-defined]


def test_scanner_finds_expected_sprint1_models():
    """Sanity: Sprint 1 has at least AuditLog scoped through TenantScopedManager.

    Event, WebhookJournal, IdempotencyKey have tenant FK but use plain
    Manager (system-context writers); they're scoped at the caller layer
    via `.filter(tenant=...)`. The scanner correctly identifies AuditLog
    as the only Sprint 1 model wired to TenantScopedManager.
    """

    names = {m.__name__ for m in SCANNED_MODELS}
    assert "AuditLog" in names, (
        f"Expected AuditLog in scanned models; got {names}. "
        "If you've added new TenantScopedManager-using models, update "
        "this allowlist."
    )


def test_scanner_finds_expected_sprint2_models():
    """Sanity: Sprint 2 added BotUser + Conversation + Message — each
    must be picked up by the scanner via the TenantScopedManager
    isinstance check. Per Sprint 2 / G2 (DRF-450).

    If a new Sprint 2+ model with a tenant FK doesn't show up here,
    either:
      (a) its default manager isn't TenantScopedManager (Sprint 2
          design rule for domain models — see model docstrings), or
      (b) the FK doesn't point to apps.tenancy.Tenant.

    Either case is a leak waiting to happen — the parametrized
    contract tests below won't catch it because the model isn't
    enumerated. Pin the expected set here so a regression in either
    direction (forget the manager / forget the FK) trips this test.
    """

    names = {m.__name__ for m in SCANNED_MODELS}
    expected = {"AuditLog", "BotUser", "Conversation", "Message"}
    missing = expected - names
    assert not missing, (
        f"Sprint 2 expected scanner to find {expected}; missing: {missing}. Got: {names}."
    )


def test_scanner_finds_expected_sprint3_models():
    """Sanity: Sprint 3 lands ConsentRecord (A1) and AdminTask (C1).

    Per Sprint 3 / G2 (DRF-474). Both models carry tenant FK +
    TenantScopedManager, so they must be picked up by auto-discovery.
    The assertion grows as each ships: this test sets the floor at A1.
    """

    names = {m.__name__ for m in SCANNED_MODELS}
    sprint3_expected = {"ConsentRecord", "AdminTask"}  # C1 added 2026-05-11
    sprint3_missing = sprint3_expected - names
    assert not sprint3_missing, (
        f"Sprint 3 expected scanner to find {sprint3_expected}; "
        f"missing: {sprint3_missing}. Got: {names}."
    )


def test_scanner_finds_expected_sprint4_models():
    """Sanity: Sprint 4 lands 6 new tenant-scoped models.

    - PromptVersion / ThresholdConfig / DisclaimerLibrary (A1+A2+A3 promptreg)
    - Experiment / UserAssignment / Holdout (B1 experiments)

    Each carries tenant FK + TenantScopedManager, so auto-discovery
    must surface all 6. Per Sprint 4 / G2 (DRF-495).
    """

    names = {m.__name__ for m in SCANNED_MODELS}
    sprint4_expected = {
        "PromptVersion",
        "ThresholdConfig",
        "DisclaimerLibrary",
        "Experiment",
        "UserAssignment",
        "Holdout",
    }
    sprint4_missing = sprint4_expected - names
    assert not sprint4_missing, (
        f"Sprint 4 expected scanner to find {sprint4_expected}; "
        f"missing: {sprint4_missing}. Got: {names}."
    )


def test_scanner_finds_expected_sprint5_models():
    """Sanity: Sprint 5 lands ReplayTrace (A1) for replay infrastructure.

    Per Sprint 5 / G1 (DRF-523). Tenant FK + TenantScopedManager →
    auto-discovery.
    """

    names = {m.__name__ for m in SCANNED_MODELS}
    sprint5_expected = {"ReplayTrace"}
    sprint5_missing = sprint5_expected - names
    assert not sprint5_missing, (
        f"Sprint 5 expected scanner to find {sprint5_expected}; "
        f"missing: {sprint5_missing}. Got: {names}."
    )


@pytest.mark.parametrize("model", SCANNED_MODELS, ids=lambda m: m.__name__)
class TestEveryScopedModel:
    """Each tenant-scoped model honors the same three-mode contract."""

    def test_default_manager_filters_by_current_tenant(self, model, settings):
        settings.STRICT_TENANT_SCOPE = "strict"

        t1 = Tenant.objects.create(slug=f"leak-1-{model.__name__.lower()}", name="T1")
        t2 = Tenant.objects.create(slug=f"leak-2-{model.__name__.lower()}", name="T2")

        # Insert one row per tenant via .all_tenants (escape hatch — caller
        # explicitly says "I'm writing across tenants for setup").
        row1 = _create_row(model, tenant=t1, suffix=f"{model.__name__}-1")
        row2 = _create_row(model, tenant=t2, suffix=f"{model.__name__}-2")

        with tenant_scope(t1):
            visible_ids = set(model.objects.values_list("id", flat=True))
        # Tenant t1 sees its own row, NOT t2's.
        assert row1.id in visible_ids
        assert row2.id not in visible_ids

        # Symmetric: tenant t2 sees its own row only.
        with tenant_scope(t2):
            visible_ids = set(model.objects.values_list("id", flat=True))
        assert row2.id in visible_ids
        assert row1.id not in visible_ids

        # all_tenants escape hatch returns both rows.
        all_ids = set(model.all_tenants.values_list("id", flat=True))
        assert {row1.id, row2.id}.issubset(all_ids)

    def test_strict_mode_raises_without_context(self, model, settings):
        settings.STRICT_TENANT_SCOPE = "strict"

        # No tenant in scope → strict mode rejects the read.
        assert current_tenant() is None
        with pytest.raises(CrossTenantError):
            list(model.objects.all())

    def test_strict_mode_raises_on_explicit_cross_tenant_filter(self, model, settings):
        settings.STRICT_TENANT_SCOPE = "strict"

        t1 = Tenant.objects.create(slug=f"leak-strict-a-{model.__name__.lower()}", name="A")
        t2 = Tenant.objects.create(slug=f"leak-strict-b-{model.__name__.lower()}", name="B")

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            list(model.objects.filter(tenant_id=t2.id))

    def test_audit_mode_returns_empty_without_context(self, model, settings):
        settings.STRICT_TENANT_SCOPE = "audit"

        # Insert a row so an unfiltered query would return SOMETHING.
        t = Tenant.objects.create(slug=f"leak-audit-{model.__name__.lower()}", name="A")
        _create_row(model, tenant=t, suffix=f"audit-{model.__name__}")

        # No tenant context — audit mode returns empty + logs (we don't
        # assert the log here; B1's tests already do).
        result = list(model.objects.all())
        assert result == []

    def test_off_mode_returns_all_rows(self, model, settings):
        settings.STRICT_TENANT_SCOPE = "off"

        t1 = Tenant.objects.create(slug=f"leak-off-a-{model.__name__.lower()}", name="A")
        t2 = Tenant.objects.create(slug=f"leak-off-b-{model.__name__.lower()}", name="B")
        _create_row(model, tenant=t1, suffix=f"off-{model.__name__}-1")
        _create_row(model, tenant=t2, suffix=f"off-{model.__name__}-2")

        # No tenant scope, no filter — off mode returns everything.
        assert model.objects.count() >= 2


class TestNonScopedTenantModels:
    """Sanity: AuditLog is scoped; Event/WebhookJournal/IdempotencyKey are not.

    The latter three intentionally use plain ``models.Manager`` (not
    ``TenantScopedManager``) because writes happen from system contexts —
    worker boot, breaker rotation, ingest before tenant resolution.
    Callers scope explicitly via ``.filter(tenant=...)``.

    This test pins the *intent* — if someone in Sprint 2+ converts one of
    these to TenantScopedManager, the scanner picks it up automatically
    (and the per-model parametrized tests above run for it too); this
    test stays as a check that nobody silently regressed the other
    direction.
    """

    def test_event_uses_plain_manager(self):
        from apps.events.models import Event

        assert not isinstance(Event._default_manager, TenantScopedManager)

    def test_webhook_journal_uses_plain_manager(self):
        from apps.ingress.models import WebhookJournal

        assert not isinstance(WebhookJournal._default_manager, TenantScopedManager)

    def test_idempotency_key_uses_plain_manager(self):
        from apps.tools.models import IdempotencyKey

        assert not isinstance(IdempotencyKey._default_manager, TenantScopedManager)
