"""Catalog mirror models (DRF-572 / Sprint 7 / C1).

Read-only mirrors of the canonical `mysite/services_app/` rows. The
catalog-sync service (C-track DRF-573..579) fetches via
``/api/v1/catalog/*`` every 15 min and upserts into these tables. The
KB ingester (K-track DRF-558..568) reads from here to build chunks.

### Why mirror at all?

We could query mysite live on every retrieval. We don't because:

* **Latency**: HTTP round-trip to mysite from the platform process pool
  is 50-200ms per FAQ turn. A local Postgres index is sub-ms.
* **Resilience**: mysite outages must not silently degrade the platform.
  A 24-hour-old mirror is still useful for FAQ retrieval; a 5xx
  pipe-through is not.
* **Schema decoupling**: when mysite reshapes its `Service` table
  (Phase 1 multi-salon), the mirror is the contract surface — only the
  sync adapter (C3) has to change, not every consumer.

### Fields shared by all four mirrors

* ``tenant`` — CASCADE. Catalog mirrors are derived data; tenant
  removal drops them. Re-sync re-populates on first beat.
* ``external_id`` — mysite primary key (IntegerField). Sync stable.
* ``external_updated_at`` — ``Service.updated_at`` (etc.) at last sync.
  Used as the `?since=` cursor for incremental pulls AND as the
  last-writer-wins tiebreak when two beats race (C3 / DRF-574).
* ``synced_at`` — `auto_now=True`. When the platform last touched
  this row. Differs from ``external_updated_at`` (upstream timestamp)
  and from row ``updated_at`` semantics on other apps.

### Sprint 7 scope

Sprint 7 ships **read-only** mirrors — admin write paths are blocked
(see ``apps/catalog/admin.py``). Sprint 8 may add operator-write
overrides for staged content; that's out of scope here.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class _MirrorBase(models.Model):
    """Shared fields + Meta for every catalog mirror.

    Abstract base — concrete mirrors set their own
    ``verbose_name``/``indexes`` if they need more. Indexes that every
    mirror wants live here.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        help_text="Owning tenant. CASCADE — mirrors are derived from "
        "mysite via catalog sync; tenant delete also drops them.",
    )
    external_id = models.IntegerField(
        help_text="Primary key of the upstream mysite row. Stable; "
        "the sync upserter (C3 / DRF-574) matches on (tenant, external_id).",
    )
    external_updated_at = models.DateTimeField(
        help_text="Upstream `updated_at` at last sync. Drives the "
        "`?since=` cursor (C2) and last-writer-wins on concurrent beats "
        "(C4 Risk #5).",
    )
    synced_at = models.DateTimeField(
        auto_now=True,
        help_text="When the platform last touched this row. Differs "
        "from `external_updated_at` (upstream's timestamp).",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        abstract = True


class CatalogService(_MirrorBase):
    """Mirror of `mysite/services_app.Service`.

    Fields preserve mysite shape with `seo_*` collapsed into
    `seo_title`/`seo_description`. M2M relations (`related_services`,
    `options`) intentionally NOT mirrored — Sprint 7 retrieval only
    needs scalar fields.
    """

    slug = models.SlugField(max_length=100)
    name = models.CharField(max_length=200)
    short_description = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    price_from = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    duration_min = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_popular = models.BooleanField(default=False)
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.TextField(blank=True, default="")
    goals = models.JSONField(default=list, blank=True)
    requires_health_check = models.BooleanField(default=False)
    contraindications = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Catalog: service"
        verbose_name_plural = "Catalog: services"
        ordering = ["name"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "slug"]),
        ]

    def __str__(self) -> str:
        return f"CatalogService[{self.slug}@{self.external_id}]"


class _MasterManager(TenantScopedManager):
    """Tenant-scoped + ``bookable()`` filter for customer-facing reads.

    Per master-management handoff §3 line 164 — only ``is_active=True``
    AND ``invite_status='accepted'`` masters are bookable.
    """

    def bookable(self):
        return self.filter(
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )


class CatalogMaster(_MirrorBase):
    """Master record — originally a mysite mirror, now first-class with
    platform-side state (invite flow, soft-archive, MAX handle).

    See ``docs/design/handoffs/2026-05-18-master-management-handoff.md``.

    Sync (``upserter._master_fields``) overwrites: name, specialization,
    bio, experience, rating, is_active, yclients_staff_id, raw.
    Platform fields NEVER touched by sync: invite_status, mode,
    photo_url, archived_at, invited_at, max_handle.
    """

    class InviteStatus(models.TextChoices):
        PENDING = "pending", "Pending invite"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    class Mode(models.TextChoices):
        INVITE = "invite", "Invite-based access"
        CATALOG_ONLY = "catalog_only", "Catalog only (no login)"

    name = models.CharField(max_length=200)
    specialization = models.CharField(max_length=255, blank=True, default="")
    bio = models.TextField(blank=True, default="")
    experience = models.CharField(max_length=255, blank=True, default="")
    rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    yclients_staff_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="YClients staff id — pre-populated from mysite so the "
        "Phase 1 booking flow can dispatch without a second lookup.",
    )
    raw = models.JSONField(default=dict, blank=True)

    invite_status = models.CharField(
        max_length=16,
        choices=InviteStatus.choices,
        default=InviteStatus.ACCEPTED,
        db_index=True,
        help_text="Default ACCEPTED so backfilled/sync masters are "
        "bookable. Invite create-path writes PENDING.",
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.CATALOG_ONLY,
    )
    photo_url = models.URLField(max_length=500, blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True)
    invited_at = models.DateTimeField(null=True, blank=True)
    max_handle = models.CharField(max_length=64, blank=True, default="")

    objects = _MasterManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Catalog: master"
        verbose_name_plural = "Catalog: masters"
        ordering = ["name"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "yclients_staff_id"]),
            models.Index(fields=["tenant", "is_active", "invite_status"]),
        ]

    def __str__(self) -> str:
        return f"CatalogMaster[{self.name}@{self.external_id}]"


class MasterService(models.Model):
    """Master ↔ Service M2M (which services this master performs).

    Per master-management handoff §MM4 — admin maintains via matrix UI.
    Customer booking endpoints MUST check this mapping before assigning
    ``BookingRequest.master_id``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="master_services",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="services_offered",
    )
    service = models.ForeignKey(
        "catalog.CatalogService",
        on_delete=models.CASCADE,
        related_name="masters_offering",
    )
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Catalog: master-service mapping"
        verbose_name_plural = "Catalog: master-service mappings"
        ordering = ["master_id", "service_id"]
        unique_together = (("master", "service"),)
        indexes = [
            models.Index(fields=["tenant", "master"]),
            models.Index(fields=["tenant", "service"]),
        ]

    def __str__(self) -> str:
        return f"MasterService[{self.master_id} → {self.service_id}]"


class CatalogFaq(_MirrorBase):
    """Mirror of `mysite/services_app.FAQ`."""

    question = models.CharField(max_length=500)
    answer = models.TextField()
    category_slug = models.SlugField(max_length=100, blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Catalog: FAQ"
        verbose_name_plural = "Catalog: FAQs"
        ordering = ["question"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "category_slug"]),
        ]

    def __str__(self) -> str:
        return f"CatalogFaq[{self.question[:40]}@{self.external_id}]"


class CatalogHelpArticle(_MirrorBase):
    """Mirror of `mysite/services_app.HelpArticle`.

    Bot-side help articles (KB-style — "как записаться", "что взять с
    собой", "цены"). Different from `CatalogFaq` which is the per-service
    FAQ on the public site.
    """

    question = models.CharField(max_length=500)
    answer = models.TextField()
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Catalog: help article"
        verbose_name_plural = "Catalog: help articles"
        ordering = ["order", "question"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "is_active", "order"]),
        ]

    def __str__(self) -> str:
        return f"CatalogHelpArticle[{self.question[:40]}@{self.external_id}]"
