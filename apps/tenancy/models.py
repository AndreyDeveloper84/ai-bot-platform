"""Tenant model — multi-tenant foundation (DRF-417 / Sprint 1 / A1).

Ported from Ayla `origin/dev:tenants/models.py` (blob fc2078de). See
`docs/adr/ADR-0001-multi-tenant-ready.md` and `docs/adr/ADR-0003-tenant-context-via-contextvar.md`
for the strategic context. Sprint 1 ships the registry table only; scoping
managers (A4 / `apps.tenancy.managers`), middleware (A3 / `apps.tenancy.middleware`),
and the `create_tenant` management command (A2) land in follow-up sub-issues.

Why a dedicated app instead of inlining into another:
- Tenant is a cross-cutting domain concept that every other app depends on.
- Future fields (TenantSubscription, TenantBilling, TenantFeatureFlag) land
  here without polluting unrelated models.
- Independent migration history simplifies rollback if multi-tenant rollout
  has to pause mid-flight.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from django.db import models

from apps.tenancy.managers import TenantScopedManager

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,49}$")


class _ActiveTenantManager(models.Manager):
    """Default manager — hides ``is_active=False`` tenants from app code.

    Admin and billing surfaces that need to see deactivated rows use
    ``Tenant.all_objects`` instead. Matches the pattern used in the source
    Ayla codebase (see `users.User`, `ai.Conversation`).
    """

    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Tenant(models.Model):
    """A logical isolation boundary for platform data (ADR-0001).

    The MVP shape is deliberately minimal — just enough to scope foreign
    keys later. Pricing, feature flags, branding, etc. land in future
    fields or sibling tables.

    Slug is the wire identifier (URL paths, ``X-Tenant`` header values).
    Name is human-readable for admin / billing UI. ``id`` is the canonical
    FK target across the platform.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(
        max_length=50,
        unique=True,
        help_text=(
            "Lowercase identifier used in URLs and the X-Tenant header. "
            "Letters, digits, hyphen, underscore. Must start with a letter "
            "or digit. Cannot be changed after creation."
        ),
    )
    name = models.CharField(
        max_length=200,
        help_text="Human-readable name shown in admin and billing.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "False hides the tenant from default queries. Soft-disable a "
            "tenant without dropping data — billing freezes, scoping "
            "middleware returns 403 (strict mode) or routes to None (audit "
            "mode)."
        ),
    )
    # Sprint 2 / E1 — Sprint-1-debt fields per PHASE0_DESIGN.md §3.1.
    # All have safe defaults so the migration is backward-compatible
    # for the single existing tenant (formula-tela).
    features = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-tenant feature flags JSON. Sprint 4+ consumers "
        "read entries like {'voice_input': true}; missing keys default "
        "to False at the call site.",
    )
    plan = models.CharField(
        max_length=32,
        default="free",
        help_text="Billing plan tier. Free during Phase 0; Sprint 9+ "
        "billing module will gate features by this value.",
    )
    timezone = models.CharField(
        max_length=64,
        default="Europe/Moscow",
        help_text="IANA timezone for tenant-local rendering of times in "
        "messages (e.g. 'завтра в 10:00'). Falls back to UTC if invalid.",
    )
    locale = models.CharField(
        max_length=16,
        default="ru-RU",
        help_text="BCP-47 locale tag for tenant-default language. "
        "Per-BotUser overrides land alongside personalisation work.",
    )

    # Sprint 8 / S1 (DRF-716) — shadow-mode flag.
    # When True the orchestrator persists Conversation/Message rows with
    # ``is_shadow=True`` and short-circuits step 19 (outbound). Used during
    # the Sprint 8 shadow-mode soak before the canary cutover; flips back to
    # False per-tenant on cutover. The boolean is per-tenant (not global)
    # because the catalog cohort flips first; production tenant flips only
    # after the delta dashboard hits ≥95% intent agreement.
    shadow_mode = models.BooleanField(
        default=False,
        help_text=(
            "When True the orchestrator writes shadow rows but does NOT send "
            "outbound messages to the user. Used during shadow-mode soak "
            "before canary cutover. See docs/runbooks/shadow-mode-launch.md."
        ),
    )

    # Phase 1 / R2 (DRF-845) — destination chat for stale-reminder
    # escalation. The hourly escalate_stale_reminders beat posts a
    # plain-text alert here when a T-24h DAY_BEFORE reminder is in
    # SENT_NO_REPLY state and the visit is < 12h away (the client never
    # tapped a button and time is running out for the salon manager to
    # phone them). ``blank=True`` because existing tenants pre-date the
    # feature and the beat tolerates an empty value as a soft no-op
    # (status still flips to ESCALATED — terminal regardless — but no
    # MAX message is sent and a WARN is logged).
    manager_chat_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=(
            "MAX chat_id of the salon manager who receives escalation "
            "alerts for T-24h reminders the client never replied to. "
            "Empty disables escalation for the tenant; status still "
            "flips to ESCALATED but no outbound message is dispatched."
        ),
    )

    # Phase 5 / KB-RAG Sub-1 (GH #114) — marks service tenants that hold
    # shared infrastructure data (e.g. ``global_kb`` for the cross-salon
    # knowledge-base corpus) rather than a real customer. ``TenantAdmin``
    # refuses to delete rows with ``is_system=True``; real salons remain
    # freely deletable. Sub-3's retriever extension reads the global tenant
    # via slug lookup, not this flag — the flag's role is admin safety.
    is_system = models.BooleanField(
        default=False,
        help_text=(
            "Service tenant — not a real salon. Holds shared infrastructure "
            "data (e.g. cross-salon KB corpus). Cannot be deleted from admin."
        ),
    )

    # Phase 1 / CH1 (DRF-848) — per-tenant Telegram bot credentials.
    #
    # The platform's Telegram channel adapter is strictly per-tenant: each
    # tenant registers its own BotFather token here and a webhook secret
    # the operator generates with ``secrets.token_urlsafe(32)`` and passes
    # to Telegram's ``setWebhook`` as ``secret_token``. Telegram then echoes
    # the secret back on every inbound POST in the
    # ``X-Telegram-Bot-Api-Secret-Token`` header; the webhook view uses
    # ``hmac.compare_digest`` to verify before dispatching to the handler.
    #
    # SECURITY: ``telegram_bot_token`` is a Telegram-side credential — it
    # MUST NOT be logged, included in audit / event payloads, or surfaced
    # in unmasked admin list views. :meth:`__repr__` redacts it; the admin
    # registration shows only the last 4 characters in list_display. See
    # ``docs/runbooks/telegram-bot-onboarding.md`` for the operator flow.
    #
    # ``blank=True`` + empty defaults keep existing tenants migration-safe;
    # the webhook view treats an empty pair as "Telegram not configured
    # for this tenant" and returns 404 (hygiene: don't leak which slugs
    # exist but lack credentials).
    telegram_bot_token = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=(
            "BotFather access token for this tenant's Telegram bot. NEVER "
            "log this value — masked in __repr__ and admin list views. "
            "See docs/runbooks/telegram-bot-onboarding.md."
        ),
    )
    telegram_webhook_secret = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Per-tenant secret the operator generates (e.g. "
            "secrets.token_urlsafe(32)) and registers with Telegram's "
            "setWebhook ?secret_token=…. Telegram sends it back in "
            "X-Telegram-Bot-Api-Secret-Token on every webhook POST; the "
            "webhook view verifies with hmac.compare_digest."
        ),
    )

    # Phase 1 / PI9 (DRF-860) — per-tenant daily LLM cost ceiling.
    #
    # Two independent budgets enforced at the LLM call boundary
    # (apps.llm.cost_tracker.enforce_caps): a raw token count and a
    # USD spend. Either can trip; orchestrator catches
    # TenantQuotaExceeded and serves the static
    # "лимит исчерпан" Russian fallback. Caps reset at 00:00 UTC via
    # the natural TTL expiry of the Redis day-key.
    #
    # Operational defaults (1M tokens / $50) match Sprint 7 / L7's
    # org-wide Anthropic cap so existing tenants migrate without an
    # observable budget change. Per-tenant overrides are mandatory
    # operational guardrails — null=False, default= provided so the
    # migration is backward-compatible for the existing rows.
    daily_token_cap = models.BigIntegerField(
        default=1_000_000,
        help_text=(
            "Daily LLM token budget (input + output, completion + "
            "embedding). Reset at 00:00 UTC. The pre-call gate in "
            "apps.llm.cost_tracker rejects with TenantQuotaExceeded "
            "once the counter reaches this value."
        ),
    )
    daily_cost_cap_usd = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("50.00"),
        help_text=(
            "Daily USD spend budget. Cost per call is computed from "
            "apps.llm.pricing.compute_cost; once the day's accumulator "
            "crosses this value the cost-tracker rejects further LLM "
            "calls via TenantQuotaExceeded. Reset at 00:00 UTC."
        ),
    )

    # Sprint 7 / C4 (DRF-575), re-labelled in DRF-1494.
    #
    # This is NOT a run timestamp and never was one, whatever its name
    # suggests. `apps.catalog.services.sync` writes `max(external_updated_at)`
    # across the rows it pulled — an UPSTREAM CONTENT WATERMARK. The `?since=`
    # filter the original help_text described does not exist: Ayla's internal
    # catalog exposes no such parameter (see the module docstring of
    # apps/catalog/services/sync.py, § "No incremental cursor"), so every run
    # is a full fetch and this value drives nothing.
    #
    # The distinction is the whole of DRF-1494. A salon whose catalog nobody
    # has edited for three weeks shows a three-week-old value here on a
    # perfectly healthy contour; a salon whose sync has been failing for
    # three weeks shows exactly the same thing. The field cannot tell the two
    # apart, so no alarm can be built on it — and for twelve days none was.
    # `last_catalog_sync_ok_at` below is the field that answers "did it run".
    last_catalog_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Upstream content watermark — max(updated_at) over the rows the "
        "last successful pull returned. NOT a run timestamp: a static catalog "
        "freezes this value on a healthy contour. Use last_catalog_sync_ok_at to "
        "judge freshness of the SYNC.",
    )

    # DRF-1494 — when the catalog sync last completed successfully for this
    # tenant. Wall-clock at completion, written by
    # `apps.catalog.services.sync.CatalogSyncService` only on a run whose
    # salon-services fetch succeeded.
    #
    # Separate column rather than a repurposed `last_catalog_sync_at`: the
    # watermark above is read by anything that wants to know how fresh the
    # CONTENT is, and collapsing the two would trade one blind spot for
    # another. NULL means this tenant has never had a successful sync — which
    # the staleness alarm reports as loudly as a stale one, because "never"
    # and "not lately" are the same outage to the client on the other end.
    last_catalog_sync_ok_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Wall-clock of the last SUCCESSFUL catalog sync run for this "
        "tenant. NULL → never synced. Age above CATALOG_SYNC_STALE_AFTER_SECONDS "
        "pages the on-call channel (apps.catalog.tasks.alert_stale_catalog_sync).",
    )

    # P1 marketplace (#1018) — the salon's city, used to filter nationwide
    # cross-tenant discovery (apps.marketplace). Minimal geo for the Penza
    # pilot: a plain city string, no PostGIS. Blank until backfilled.
    city = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Salon city for marketplace discovery (e.g. «Пенза»). "
        "Blank = not yet set; minimal geo for the pilot (no lat/lng).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = _ActiveTenantManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "slug"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    def __repr__(self) -> str:
        """Repr that masks the Telegram bot token (Phase 1 / CH1).

        ``telegram_bot_token`` is a credential — the default
        ``Model.__repr__`` reveals all fields, so we override and emit
        only the last 4 characters (mirrors banking convention for card
        numbers). Tests assert that the full token does NOT appear in
        repr output.
        """
        masked = self._mask_telegram_token()
        return (
            f"<Tenant id={self.id} slug={self.slug!r} name={self.name!r} "
            f"telegram_bot_token={masked!r}>"
        )

    def _mask_telegram_token(self) -> str:
        """Return the last 4 chars of the Telegram bot token, prefixed by '…'.

        Returns the empty string when no token is set. Used by ``__repr__``
        AND by the admin list display, so both surfaces stay in sync.
        """
        token = self.telegram_bot_token or ""
        if not token:
            return ""
        if len(token) <= 4:
            return "…" + "*" * len(token)
        return "…" + token[-4:]

    def clean(self) -> None:
        """Validate slug shape at the model layer.

        Django's `SlugField` allows uppercase letters and lone hyphens
        (e.g. ``-foo-``). The platform's `X-Tenant` header and URL paths
        require a stricter shape so slugs round-trip cleanly.
        """
        from django.core.exceptions import ValidationError

        super().clean()
        if self.slug and not _SLUG_RE.match(self.slug):
            raise ValidationError(
                {
                    "slug": (
                        "Slug must be lowercase alphanumeric (with - or _), "
                        "2–50 chars, and start with a letter or digit."
                    ),
                }
            )

    def save(self, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
        """Enforce slug immutability post-creation.

        Tenancy retro B3: the field docstring above promises «Cannot be
        changed after creation» but nothing enforced it at save() time.
        A renamed slug would let the middleware resolver rebind a known
        slug to a different tenant — cross-tenant attack vector for
        stale links / cached webhook URLs / bookmarked customer pages
        pointing at the old slug.

        Lookup uses ``_base_manager`` so it sees soft-deleted (``is_active
        =False``) rows too — otherwise a deactivated tenant's slug
        rename would slip past via the ``_ActiveTenantManager`` default.
        Raises ``ValueError`` on rename. Admin tooling that genuinely
        needs to rename should bypass through a documented data migration.
        """

        if self.pk is not None and self.slug:
            prior_slug = (
                type(self)._base_manager.filter(pk=self.pk).values_list("slug", flat=True).first()
            )
            if prior_slug is not None and prior_slug != self.slug:
                raise ValueError(
                    f"Tenant.slug is immutable after creation "
                    f"(was {prior_slug!r}, attempted {self.slug!r})"
                )
        super().save(*args, **kwargs)


class TenantStaff(models.Model):
    """Per-tenant staff role assignment (PR 1.5 / ADR-0008).

    The admin-side half of the platform's 5-role model. Carries roles that
    grant *admin chrome* — Owner, Admin, Receptionist — without trying to
    describe the person's service-delivery profile (that's
    :class:`apps.catalog.models.CatalogMaster`).

    Master detection lives on ``CatalogMaster.linked_bot_user`` (PR #203)
    and is deliberately NOT duplicated here. A master can ALSO hold a
    ``TenantStaff`` row if they have admin privileges in addition (rare —
    a salon's owner-master, for instance); the role resolver in
    :mod:`apps.identity.services.role_resolver` reads both tables and
    combines them per ADR-0008's additive-roles decision.

    Customer is NEVER a row in this table — customer access is the implicit
    baseline for every BotUser regardless of staff status (ADR-0008
    decision 6).

    See ``docs/adr/ADR-0008-role-detection-and-staff-model.md`` for the
    storage choice rationale and ``docs/design/policies/conversation-ownership-policy.md``
    §4 for the capability matrix this table makes enforceable.
    """

    class Role(models.TextChoices):
        """Admin-side roles. Listed in increasing privilege per ADR-0008.

        Master is intentionally absent — it lives on ``CatalogMaster``.
        Customer is intentionally absent — it's the implicit baseline.
        """

        RECEPTIONIST = "receptionist", "Receptionist"
        ADMIN = "admin", "Admin"
        OWNER = "owner", "Owner"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="staff",
        help_text="Owning tenant. PROTECT — dropping a tenant must not "
        "silently nuke the audit trail of who held what role.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.PROTECT,
        related_name="staff_assignments",
        help_text="The person holding this role, identified by their "
        "channel-scoped BotUser. PROTECT mirrors the tenant FK — a "
        "delete must not silently drop role history.",
    )
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        db_index=True,
        help_text="One of owner / admin / receptionist (ADR-0008). "
        "Owner uniqueness is enforced per-tenant via partial unique "
        "constraint below.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the staff row was created.",
    )
    created_by = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="BotUser who created this assignment — audit trail for "
        "operator-led promotions. NULL allowed for system-created rows "
        "(seed data, management commands, migrations).",
    )
    deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the role was revoked. NULL = active. Soft "
        "deactivation preserves the historical assignment for audit; "
        "the role resolver ignores rows with this set, and the partial "
        "unique-Owner constraint only counts active rows so an Owner "
        "handover can deactivate the old row then create the new one.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Tenant staff"
        verbose_name_plural = "Tenant staff"
        constraints = [
            # ADR-0008 decision 5: each tenant has exactly one active
            # Owner. Partial unique index lets handover work as
            # deactivate-then-create without a transient state where two
            # rows briefly satisfy uniqueness because the old one is
            # being soft-archived.
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(role="owner", deactivated_at__isnull=True),
                name="unique_active_owner_per_tenant",
            ),
            # DRF-1227: one ACTIVE row per (tenant, person, role) — the
            # same partial shape as the Owner rule above, for the same
            # reason. This replaces a plain unique_together, which
            # contradicted the field it sat next to: `deactivated_at`
            # promises that "soft deactivation preserves the historical
            # assignment for audit", but under a total unique index the
            # history could hold exactly one entry, so re-hiring somebody
            # you had revoked failed with an IntegrityError instead of
            # granting the role back. Found by the revoke tests before it
            # reached anyone.
            models.UniqueConstraint(
                fields=["tenant", "bot_user", "role"],
                condition=models.Q(deactivated_at__isnull=True),
                name="unique_active_staff_role",
            ),
        ]
        indexes = [
            # Resolver hot path: "all active staff rows for this BotUser
            # in this tenant" — we filter by (tenant, bot_user) and then
            # rely on the active-vs-deactivated check in Python. A
            # composite index on (tenant, role, deactivated_at) also
            # serves admin filters ("list active admins of tenant X").
            models.Index(fields=["tenant", "role", "deactivated_at"]),
            models.Index(fields=["bot_user"]),
        ]

    def __str__(self) -> str:
        suffix = " (deactivated)" if self.deactivated_at is not None else ""
        return f"TenantStaff[{self.tenant_id}/{self.bot_user_id}={self.role}]{suffix}"

    @property
    def is_active(self) -> bool:
        """True if this row is the live assignment (not soft-deactivated)."""
        return self.deactivated_at is None


class StaffInvite(models.Model):
    """A one-shot code that turns a person into salon staff (DRF-1061).

    ### Why this exists

    Nothing in the platform could create a ``TenantStaff`` row. The master
    invite flow (``apps/admin_api/views_invite.py``) covers exactly one
    role and rejects the others outright — «only role='master' is accepted
    in this PR; admin/receptionist invites land in a separate ticket
    (TenantStaff model)». This is that ticket. The consequence of the gap
    was concrete: the pilot salon had zero staff rows, so ``resolve_role``
    answered *customer* for all 14 bot users, and the fully-built admin
    Mini App was unreachable by anyone including the owner.

    ### Why a code and not just a link

    The salon bot is a chat. The person receives a link
    (``max://bot/<bot>?start=inv_<code>``) and taps it — no typing. But the
    code must also work typed, because a link cannot be read out over the
    phone and does not survive being forwarded around. Both paths carry the
    same secret; see ``redeem_staff_invite``.

    ### Why the code is hashed

    A code short enough to type is short enough to guess: the pilot format
    has ~614k combinations. Storage is therefore SHA-256 and never the code
    itself — a database dump does not hand out staff access — and guessing
    is bounded by the redeem-side attempt limit rather than by entropy.

    ### Master invites bind to an existing row

    ``catalog_master`` is set for ``role="master"`` and points at the
    master that already exists in the catalog mirror. On the pilot all four
    masters are already there (``invite_status=accepted``, ``is_active``,
    only ``linked_bot_user`` empty), so an invite that CREATED a master
    would produce a duplicate — and the duplicate would be invisible to the
    booking mirror, whose ``specialist_id`` points at the original row. The
    master would then see an empty day next to their real appointments.
    """

    class Role(models.TextChoices):
        """Roles an invite can grant.

        Superset of :class:`TenantStaff.Role`: it adds ``master``, which is
        not a ``TenantStaff`` row at all but a link on ``CatalogMaster``.
        The invite is the single door; what it writes on the other side
        differs per role.
        """

        RECEPTIONIST = "receptionist", "Receptionist"
        ADMIN = "admin", "Admin"
        OWNER = "owner", "Owner"
        MASTER = "master", "Master"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="staff_invites",
    )
    role = models.CharField(max_length=16, choices=Role.choices, db_index=True)
    code_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 of the normalized code. The code itself is shown "
        "once at issue time and never stored.",
    )
    catalog_master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_invites",
        help_text="Required for role=master: the EXISTING catalog row this "
        "invite links a person to. Never used to create a master.",
    )
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="redeemed_staff_invites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Who issued it. NULL for invites issued by a management "
        "command — the pilot's first owner has no one to be invited by.",
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Free-form label for the issuer: who this code was meant "
        "for. Not shown to the recipient.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Staff invite"
        verbose_name_plural = "Staff invites"
        ordering = ["-created_at"]
        indexes = [
            # Redeem hot path is the unique code_hash lookup above. This one
            # serves the issuer's view: "outstanding invites for my salon".
            models.Index(fields=["tenant", "used_at", "expires_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role="master", catalog_master__isnull=False)
                | ~models.Q(role="master") & models.Q(catalog_master__isnull=True),
                name="staff_invite_master_requires_catalog_master",
            ),
        ]

    def __str__(self) -> str:
        state = "used" if self.used_at else "outstanding"
        return f"StaffInvite[{self.tenant_id}/{self.role}/{state}]"

    @property
    def is_used(self) -> bool:
        return self.used_at is not None
