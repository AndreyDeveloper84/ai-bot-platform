"""Experiments registry (DRF-484 / Sprint 4 / B1).

PHASE0_DESIGN §3.6 — three models that ship the A/B framework:

* :class:`Experiment` — operator-defined hypothesis with weighted
  variants; ``primary_kpi`` + ``guardrails`` drive the dashboard.
* :class:`UserAssignment` — sticky bucketing record: which variant a
  bot_user is locked into for an experiment. Idempotent via the
  ``(bot_user, experiment)`` unique constraint.
* :class:`Holdout` — global 5% holdout. Users in here NEVER receive
  any experiment treatment; they're the long-term baseline cohort.

### Why tenant FK on UserAssignment + Holdout (denormalised)

Both could be reached via ``bot_user.tenant`` join, but two reasons:
  1. The E1 cross-tenant leakage scanner auto-discovers anything with
     a direct tenant FK + TenantScopedManager. Denormalised FK = free
     scanner coverage.
  2. Per-tenant analytics queries (e.g. "how many users in holdout
     for tenant X?") need an index-friendly path.

### Cascade rules

- Experiment.tenant: PROTECT — accidental tenant delete must not
  silently drop running experiments.
- UserAssignment.bot_user / .experiment: CASCADE — if the user is
  hard-deleted via privacy.data_delete OR the experiment row is
  removed, assignments go with them.
- Holdout.bot_user: CASCADE — user delete → holdout entry gone.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class Experiment(models.Model):
    """Operator-defined hypothesis with weighted variants."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="experiments",
        help_text="Owning tenant. PROTECT — running experiments must "
        "survive accidental tenant delete.",
    )
    name = models.SlugField(
        max_length=64,
        help_text="Operator-assigned identifier (e.g. 'router_threshold_v2').",
    )
    hypothesis = models.TextField(
        help_text="One paragraph: what we expect to learn + why this variant should win.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    primary_kpi = models.CharField(
        max_length=64,
        help_text="The metric this experiment optimises for "
        "(e.g. 'booking_conversion_rate', 'handoff_rate').",
    )
    guardrails = models.JSONField(
        default=list,
        blank=True,
        help_text="List of {metric, max_delta} guardrails. Example: "
        '[{"metric":"handoff_rate","max_delta":0.05}].',
    )
    variants = models.JSONField(
        default=list,
        help_text="List of {name, weight} dicts. Weights are integers; "
        "sticky bucketing buckets users into them proportionally. Example: "
        '[{"name":"control","weight":50},{"name":"v2","weight":50}].',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Experiment"
        verbose_name_plural = "Experiments"
        ordering = ["-started_at", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="experiments_experiment_unique_tenant_name",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"Experiment[{self.name}/{self.status}]"


class UserAssignment(models.Model):
    """One row per (bot_user, experiment) — sticky variant assignment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="user_assignments",
        help_text="Denormalised tenant for scanner + analytics.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="experiment_assignments",
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    variant = models.CharField(
        max_length=64,
        help_text="Variant name picked by sticky bucketing. Must match "
        "one of `experiment.variants[].name`.",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "User assignment"
        verbose_name_plural = "User assignments"
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["bot_user", "experiment"],
                name="experiments_userassignment_unique_user_experiment",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "experiment", "variant"]),
        ]

    def __str__(self) -> str:
        return f"UserAssignment[{self.bot_user_id}/{self.experiment_id}={self.variant}]"


class Holdout(models.Model):
    """Global 5% holdout — users here never receive experiment treatment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="holdouts",
        help_text="Denormalised tenant for scanner + analytics.",
    )
    bot_user = models.OneToOneField(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="holdout",
        help_text="The user excluded from experiments. OneToOne — a user "
        "is either in the holdout or not.",
    )
    since = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Holdout"
        verbose_name_plural = "Holdouts"
        ordering = ["-since"]
        indexes = [
            models.Index(fields=["tenant", "since"]),
        ]

    def __str__(self) -> str:
        return f"Holdout[{self.bot_user_id}]"
