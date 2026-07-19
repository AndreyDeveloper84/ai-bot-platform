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
    # Sprint 6 / P1: ClientProfile — OneToOne(BotUser) PK + tenant FK PROTECT.
    # All other fields have defaults, so the scanner only needs to supply
    # the bot_user (which itself supplies the tenant).
    "ClientProfile": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(
            tenant, f"profile-{suffix or 'x'}"
        ),
    },
    # Phase 1 / B6 (DRF-842): Promotion needs a unique code per row +
    # an in-range discount_percent. Code is upper-cased on save (see
    # Promotion.save) so the suffix gets normalised consistently.
    "Promotion": {
        "code": lambda tenant, suffix: f"SCANNER-{suffix or 'X'}",
        "discount_percent": 10,
    },
    # ── Scanner-rot backfill ─────────────────────────────────────────
    # The entries below close the gap for models that landed without a
    # ``_MODEL_REQUIRED_FIELDS`` row. Same contract as above: only
    # NOT-NULL, no-default, non-auto fields are listed.
    # AI observability (#769): request_id + the two NOT-NULL ints +
    # outcome are the only no-default columns; bot_user and conversation
    # FKs are nullable (system-triggered calls).
    "AIRequestMetric": {
        "request_id": lambda tenant, suffix: _new_uuid(),
        "message_text_length": 42,
        "latency_total_ms": 120,
        "outcome": "success",
    },
    # M6: AiDraft needs conversation + master FKs and non-empty content.
    # Fresh conversation per row keeps the one-active-draft-per-
    # conversation partial unique intact (status defaults to ACTIVE).
    "AiDraft": {
        "conversation": lambda tenant, suffix: _make_conversation_for_scanner(
            tenant, f"draft-{suffix or 'x'}"
        ),
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"draft-{suffix or 'x'}"),
        "content": "scanner draft",
    },
    # B2: bot_user FK + chat_id snapshot + visit_at + scheduled_at + kind.
    # yclients_record_id stays NULL so unique_together (yclients_record_id,
    # kind) never fires — NULL ≠ NULL in Postgres/SQLite uniqueness.
    "BookingReminder": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(tenant, suffix),
        "chat_id": "scanner-chat",
        "visit_at": lambda tenant, suffix: _future_datetime(days=7),
        "kind": "day_before",
        "scheduled_at": lambda tenant, suffix: _future_datetime(),
    },
    # Sprint 7 / C1 catalog mirrors — each needs external_updated_at
    # (NOT NULL, no default) plus its own text fields. external_id stays
    # NULL → unique_together (tenant, external_id) is NULL-safe.
    "CatalogService": {
        "slug": "scanner-service",
        "name": "Scanner service",
        "external_updated_at": lambda tenant, suffix: _future_datetime(),
    },
    "CatalogMaster": {
        "name": "Scanner master",
        "external_updated_at": lambda tenant, suffix: _future_datetime(),
    },
    "CatalogFaq": {
        "question": "Scanner question?",
        "answer": "Scanner answer.",
        "external_updated_at": lambda tenant, suffix: _future_datetime(),
    },
    "CatalogHelpArticle": {
        "question": "Scanner help question?",
        "answer": "Scanner help answer.",
        "external_updated_at": lambda tenant, suffix: _future_datetime(),
    },
    # MM4: MasterService is the master↔service M2M — a fresh master +
    # service per row keeps unique_together (master, service) intact.
    "MasterService": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"ms-m-{suffix or 'x'}"),
        "service": lambda tenant, suffix: _make_service_for_scanner(
            tenant, f"ms-s-{suffix or 'x'}"
        ),
    },
    # Tier-A #3: signal needs a source AIRequestMetric (CASCADE FK) +
    # signal_type. Fresh metric per row keeps ifs_metric_type_unique intact.
    "ImplicitFeedbackSignal": {
        "ai_request_metric": lambda tenant, suffix: _make_ai_request_metric_for_scanner(
            tenant, suffix
        ),
        "signal_type": "abandoned_topic",
    },
    # Loyalty Phase 1: OneToOne customer — fresh BotUser per row (same
    # shape as Holdout / ClientProfile above). No post_save auto-create
    # exists for accounts, so a plain create is enough.
    "LoyaltyAccount": {
        "customer": lambda tenant, suffix: _make_bot_user_for_scanner(
            tenant, f"loyalty-{suffix or 'x'}"
        ),
    },
    # Append-only event: account FK + event_type + the two signed ints.
    # Picks manual_adjust so the earn_visit / refund_revoke partial-unique
    # constraints (booking IS NOT NULL) stay out of the way.
    "LoyaltyEvent": {
        "account": lambda tenant, suffix: _make_loyalty_account_for_scanner(tenant, suffix),
        "event_type": "manual_adjust",
        "points_delta": 5,
        "balance_after": 5,
    },
    # Phase 2.d: referrer + referee FKs; referee is unique per tenant, so
    # each side gets its own prefixed BotUser.
    "LoyaltyReferral": {
        "referrer_customer": lambda tenant, suffix: _make_bot_user_for_scanner(
            tenant, f"referrer-{suffix or 'x'}"
        ),
        "referee_customer": lambda tenant, suffix: _make_bot_user_for_scanner(
            tenant, f"referee-{suffix or 'x'}"
        ),
    },
    # Internal chat §6.1: master FK + topic + sla_due_at. Topic "general"
    # is NOT in SENSITIVE_TOPICS, so the is_sensitive CheckConstraint
    # accepts the default False.
    "MasterAdminThread": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"mat-{suffix or 'x'}"),
        "topic": "general",
        "sla_due_at": lambda tenant, suffix: _future_datetime(),
    },
    # M7: OneToOne master — fresh CatalogMaster per row. All toggles have
    # defaults; urgent defaults True which the CheckConstraint demands.
    "MasterNotificationPrefs": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"mnp-{suffix or 'x'}"),
    },
    # DRF-841 / B5: bot_user FK + kind + expires_at (10-min TTL column).
    "PendingBookingAction": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(tenant, suffix),
        "kind": "confirm",
        "expires_at": lambda tenant, suffix: _future_datetime(),
    },
    # ADR-0009 mirror: appointment_id is the PK *without* a default —
    # supply a fresh UUID per row. start/end + status are the other
    # NOT-NULL no-default columns; bot_user is nullable (orphan rows).
    "RemoteBookingProxy": {
        "appointment_id": lambda tenant, suffix: _new_uuid(),
        "start_at": lambda tenant, suffix: _future_datetime(days=1),
        "end_at": lambda tenant, suffix: _future_datetime(days=2),
        "status": "confirmed",
    },
    # Schedule-management §1: master FK + day_of_week. The
    # times-match-is_working CHECK forces start/end times when
    # is_working=True (the default) — supply a 09:00–18:00 block.
    "WorkingHours": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"wh-{suffix or 'x'}"),
        "day_of_week": 0,
        "start_time": lambda tenant, suffix: _scanner_time(9),
        "end_time": lambda tenant, suffix: _scanner_time(18),
    },
    # master FK + date + type. type=vacation is a full-day type, so the
    # times-match-type CHECK passes with NULL start/end (the defaults).
    "ScheduleException": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"se-{suffix or 'x'}"),
        "date": lambda tenant, suffix: _scanner_date(),
        "type": "vacation",
    },
    # master FK + start/end + reason; the end_after_start CHECK wants
    # distinct instants — separate offsets per field.
    "TimeBlock": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"tb-{suffix or 'x'}"),
        "start_at": lambda tenant, suffix: _future_datetime(days=1),
        "end_at": lambda tenant, suffix: _future_datetime(days=2),
        "reason": "scanner block",
    },
    # Q-M6: only the master FK is NOT-NULL without a default —
    # requested_change defaults to {} and the M3 typed columns are nullable.
    "ScheduleChangeRequest": {
        "master": lambda tenant, suffix: _make_master_for_scanner(tenant, f"scr-{suffix or 'x'}"),
    },
    # ADR-0008: bot_user FK + role. Role "admin" (not "owner") so the
    # one-active-owner-per-tenant partial unique never trips when the
    # scanner creates several rows in the same tenant.
    "TenantStaff": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(tenant, suffix),
        "role": "admin",
    },
    # Phase 3 / F4: OneToOne(BotUser) PK — same shape as ClientProfile,
    # but no post_save auto-create exists for preferences, so a plain
    # create with a fresh BotUser per row is enough.
    "UserPreferences": {
        "bot_user": lambda tenant, suffix: _make_bot_user_for_scanner(
            tenant, f"prefs-{suffix or 'x'}"
        ),
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


def _future_datetime(days: int = 30):
    """Helper: future timestamp for NOT-NULL DateTimeField columns."""

    from datetime import timedelta

    from django.utils import timezone

    return timezone.now() + timedelta(days=days)


def _new_uuid():
    """Helper: fresh UUID for scanner rows (PKs / correlation ids)."""

    import uuid

    return uuid.uuid4()


def _scanner_date():
    """Helper: fixed date for ScheduleException.date."""

    from datetime import date

    return date(2026, 1, 5)


def _scanner_time(hour: int):
    """Helper: fixed time-of-day for WorkingHours start/end."""

    from datetime import time

    return time(hour, 0)


def _make_master_for_scanner(tenant, suffix: str):
    """Inline CatalogMaster factory — FK target for master-owned models.

    Only ``name`` + ``external_updated_at`` are NOT-NULL without a
    default; ``external_id`` stays NULL so unique_together
    (tenant, external_id) never fires.
    """

    from django.utils import timezone

    from apps.catalog.models import CatalogMaster

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=f"scanner-master-{suffix or 'x'}",
        external_updated_at=timezone.now(),
    )


def _make_service_for_scanner(tenant, suffix: str):
    """Inline CatalogService factory for the MasterService row factory."""

    from django.utils import timezone

    from apps.catalog.models import CatalogService

    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=f"scanner-service-{suffix or 'x'}",
        name=f"Scanner Service {suffix or 'x'}",
        external_updated_at=timezone.now(),
    )


def _make_ai_request_metric_for_scanner(tenant, suffix: str):
    """Inline AIRequestMetric factory for ImplicitFeedbackSignal."""

    from apps.observability.models import AIRequestMetric

    return AIRequestMetric.all_tenants.create(
        tenant=tenant,
        request_id=_new_uuid(),
        message_text_length=42,
        latency_total_ms=120,
        outcome="success",
    )


def _make_loyalty_account_for_scanner(tenant, suffix: str):
    """Inline LoyaltyAccount factory for the LoyaltyEvent row factory.

    ``customer`` is a OneToOne — a fresh prefixed BotUser per call keeps
    the UNIQUE constraint intact across rows.
    """

    from apps.loyalty.models import LoyaltyAccount

    customer = _make_bot_user_for_scanner(tenant, f"loyalty-acct-{suffix or 'x'}")
    return LoyaltyAccount.all_tenants.create(tenant=tenant, customer=customer)


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
    # ClientProfile is auto-created by a BotUser post_save signal (P1).
    # The scanner's factory creates the BotUser via `bot_user` lambda above,
    # which already triggers the signal — `all_tenants.create()` would then
    # collide on the OneToOne UNIQUE. Use update_or_create scoped on
    # bot_user to satisfy the scanner's "one row per tenant" expectation.
    if model.__name__ == "ClientProfile":
        bot_user = kwargs.pop("bot_user")
        kwargs.pop("tenant", None)  # bot_user already carries tenant via FK
        row, _ = model.all_tenants.update_or_create(  # type: ignore[attr-defined]
            bot_user=bot_user,
            defaults={"tenant": bot_user.tenant, **kwargs},
        )
        return row
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


def test_scanner_finds_expected_sprint6_models():
    """Sanity: Sprint 6 lands ClientProfile (P1) for RFM/LTV/tier infra.

    Per Sprint 6 / G1 (DRF-550). OneToOne(BotUser, primary_key=True) +
    tenant FK PROTECT + TenantScopedManager → auto-discovery.
    """

    names = {m.__name__ for m in SCANNED_MODELS}
    sprint6_expected = {"ClientProfile"}
    sprint6_missing = sprint6_expected - names
    assert not sprint6_missing, (
        f"Sprint 6 expected scanner to find {sprint6_expected}; "
        f"missing: {sprint6_missing}. Got: {names}."
    )


def test_scanner_finds_expected_sprint7_models():
    """Sprint 7 / G1 (DRF-596) — KB document + 4 catalog mirrors.

    K1 (KbDocument) + C1 (CatalogService/Master/Faq/HelpArticle) all
    declare tenant FK + TenantScopedManager → auto-discovery picks
    them up. This test pins the expectation explicitly so a future
    refactor that swaps a default manager (or drops the tenant FK)
    fails CI loudly instead of silently exempting a model from the
    tenant contract.
    """
    names = {m.__name__ for m in SCANNED_MODELS}
    sprint7_expected = {
        "KbDocument",
        "CatalogService",
        "CatalogMaster",
        "CatalogFaq",
        "CatalogHelpArticle",
    }
    sprint7_missing = sprint7_expected - names
    assert not sprint7_missing, (
        f"Sprint 7 expected scanner to find {sprint7_expected}; "
        f"missing: {sprint7_missing}. Got: {names}."
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
            visible_ids = set(model.objects.values_list("pk", flat=True))
        # Tenant t1 sees its own row, NOT t2's.
        assert row1.pk in visible_ids
        assert row2.pk not in visible_ids

        # Symmetric: tenant t2 sees its own row only.
        with tenant_scope(t2):
            visible_ids = set(model.objects.values_list("pk", flat=True))
        assert row2.pk in visible_ids
        assert row1.pk not in visible_ids

        # all_tenants escape hatch returns both rows.
        all_pks = set(model.all_tenants.values_list("pk", flat=True))
        assert {row1.pk, row2.pk}.issubset(all_pks)

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
