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

from django.db import models

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

    # Sprint 7 / C4 (DRF-575) — cursor for the catalog sync orchestrator.
    # NULL = full-resync on the next beat (initial bootstrap or admin
    # force-clear). Sync service writes the upstream `updated_at` of the
    # most recent row pulled, so the next run's `?since=` filter only
    # picks up rows mysite has touched since.
    last_catalog_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Cursor — `?since=` filter for the next catalog sync run. "
        "NULL → full resync. Written by apps.catalog.services.sync after "
        "a successful pull-upsert cycle.",
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
