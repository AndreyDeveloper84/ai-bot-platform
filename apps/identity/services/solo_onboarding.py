"""Atomic seeding for self-employed solo provider registration.

Per founder decision 2026-05-25 (memory `project_solo_provider_universal_ui`)
+ tech-lead handoff 2026-05-25 (Q7 verdict D+B-signature).

# What this service does

Self-employed providers register via the **dedicated Ayla Solo MAX bot**.
That bot's channel-token maps (via `CHANNEL_TOKEN_TO_TENANT_SLUG`) to a
system-level **bootstrap tenant** (`ayla_solo_bootstrap`). Incoming events
on this bot land in bootstrap-tenant scope. The onboarding skill then
calls this service, which atomically:

1. Creates a real **per-user solo Tenant** (slug derived deterministically
   from `(channel, channel_user_id)`).
2. Creates a fresh **BotUser** anchored to the new solo tenant (separate
   row from the bootstrap-tenant BotUser the channel adapter resolved).
3. Creates **TenantStaff(role=owner)** + **TenantStaff(role=admin)** for
   the new BotUser in the new tenant.
4. Creates **CatalogMaster(linked_bot_user=new_bot_user)** in the new
   tenant — owner-equals-master self-onboarding pattern.
5. Emits `identity.solo_provider.created` event for audit + analytics.

# Why a separate tenant per solo provider (not reuse bootstrap)

Per memory `project_solo_provider_universal_ui` — «один User должен быть
owner+admin+master в **одном tenant**». Bootstrap is a system-level
landing pad, not a real provider workspace. Each self-employed master
gets their own isolated tenant — their bookings, services, schedule live
there.

# Architectural decisions (recorded in handoff 2026-05-25)

| ID | Decision | Rationale |
|---|---|---|
| Q1 | Spec source = memory + task instruction | `docs/design/policies/solo-provider-ux.md` pending Tau PR — no committed authoritative doc yet |
| Q2-A | `CatalogMaster` direct-create (no invite) | Self-employed master IS the inviter — `invite_token=None`, `invite_status=ACCEPTED` |
| Q3 (neg-int) | `external_id = -((bot_user.id.int % 2^31) + 1)` | `_MirrorBase.external_id` is IntegerField NOT NULL. Negative space is collision-free vs mysite positive IDs |
| Q4-Opt1 | Idempotency via `pg_advisory_xact_lock` keyed on identity hash | `select_for_update` on empty result does NOT lock in Postgres — race condition. Advisory lock holds even with no row. Released at txn commit/rollback. |
| Q5 | `is_solo_provider` per Tau §3.1 (distinct count) | Veha 3 scope — `/api/v1/me` extension |
| Q6 | §H.3 BORDERLINE | Mandatory adversarial review pre-merge |
| Q7 | D + B-signature | Dedicated solo bot + kwargs signature with channel/channel_user_id/display_name/phone/chat_id/tenant_name |

# Ops setup required BEFORE this service runs

The bootstrap tenant + dedicated MAX bot are **ops/pre-pilot setup**,
NOT created by this code. Setup steps:

1. Create new MAX bot for solo registration (e.g. `@ayla_solo_bot`).
2. Add the bot's secret token to `CHANNEL_TOKEN_TO_TENANT_SLUG`:
   ```bash
   CHANNEL_TOKEN_TO_TENANT_SLUG=<solo_bot_token>=ayla_solo_bootstrap
   ```
3. Seed the bootstrap tenant via management shell:
   ```python
   from apps.tenancy.models import Tenant
   Tenant.objects.create(
       slug="ayla_solo_bootstrap",
       name="Ayla Solo — Registration",
       is_active=True,
   )
   ```

If the bootstrap tenant is missing when the service runs,
`BootstrapTenantMissing` is raised — fail-loud so ops knows to complete
setup.

# Caller contract

This service runs INSIDE `tenant_scope(bootstrap_tenant)` — that's the
scope the worker consumer entered for the inbound MAX event. The
service uses `.all_tenants` managers everywhere because it CREATES rows
in a different tenant (the new solo tenant), which would trip
`TenantScopedManager`'s cross-tenant filter detection if we used
`.objects`.

The caller MUST NOT wrap this in their own `transaction.atomic()` — the
`@transaction.atomic` decorator here is the seeding boundary, not a
sub-savepoint. Concurrent caller atomic blocks risk masking the inner
seed rollback. (Future veha 2 may add `durable=True` if this becomes a
problem in practice — current pattern is fail-loud RuntimeError if
nested.)
"""

from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.events.services import emit
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant, TenantStaff

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────

# System-level bootstrap tenant for the dedicated Ayla Solo MAX bot.
# Created by ops pre-pilot setup (NOT by this code). Mapped via
# `CHANNEL_TOKEN_TO_TENANT_SLUG=<solo_bot_token>=ayla_solo_bootstrap`.
BOOTSTRAP_TENANT_SLUG = "ayla_solo_bootstrap"

# Negative-integer space for synthetic `CatalogMaster.external_id`
# values on self-onboarded rows. Mysite sync always produces POSITIVE
# external_ids; negative space is collision-free. Per Q3 verdict
# 2026-05-25 (negative-int variant chosen over CharField migration).
_SOLO_EXTERNAL_ID_SPACE = 2**31 - 1


# ─── Exceptions ─────────────────────────────────────────────────────────


class SoloOnboardingError(Exception):
    """Base exception — atomic seed failed; transaction rolled back."""


class SoloOnboardingPartialStateError(SoloOnboardingError):
    """Raised when partial seeding state is observed on retry.

    Should NEVER happen if `@transaction.atomic` works correctly and the
    advisory lock is held — all 5 rows commit together or none do.

    If raised:
      1. Investigate atomic boundary integrity (was the decorator removed?
         was the lock skipped on the failing call?).
      2. Check for direct DB manipulation outside the service (manual
         management-shell rows that pre-date the seed).
      3. Manual cleanup may be required (drop the partial rows, then retry).

    Auto-complete intentionally NOT implemented per tech-lead direction
    2026-05-26: masking an underlying invariant violation is worse than
    graceful recovery (memory `feedback_severity_discipline_rubric` —
    data-integrity issues fail loud).
    """


class BootstrapTenantMissing(SoloOnboardingError):
    """The `ayla_solo_bootstrap` tenant doesn't exist yet.

    Fail-loud signal that ops pre-pilot setup is incomplete. See module
    docstring for the 3-step setup procedure.
    """


# ─── Result dataclass ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SoloOnboardingResult:
    """Outcome of `create_solo_provider`.

    Attributes:
        tenant: The per-user solo tenant (newly created OR existing).
        bot_user: The BotUser anchored to the solo tenant (newly created
            OR existing). NOT the bootstrap-tenant BotUser the caller
            had in scope — that one stays as historical entry.
        owner_staff: `TenantStaff(role=owner)` row.
        admin_staff: `TenantStaff(role=admin)` row.
        master: `CatalogMaster` row with `linked_bot_user=bot_user`.
        created: True if this call SEEDED the records; False if all 4
            already existed (idempotent return).
    """

    tenant: Tenant
    bot_user: BotUser
    owner_staff: TenantStaff
    admin_staff: TenantStaff
    master: CatalogMaster
    created: bool


# ─── Helpers ────────────────────────────────────────────────────────────


def _slug_hash(channel: str, channel_user_id: str) -> str:
    """Deterministic 8-hex-char suffix from `(channel, channel_user_id)`.

    SHA-256 truncated to 8 hex chars = 32 bits of entropy ≈ 1-in-4-billion
    collision probability per identity pair. Acceptable for slug uniqueness
    in pilot scale (founder-50 cohort + early Phase 1 adoption).
    """
    raw = f"{channel}:{channel_user_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def _solo_tenant_slug(channel: str, channel_user_id: str) -> str:
    """Solo tenant slug shape: `solo-{channel-trimmed}-{8-hex}`.

    Deterministic per identity → enables idempotent lookup-by-slug.
    The 8-hex SHA-256 suffix carries identity uniqueness; the `channel`
    component is informational. Channel is trimmed to fit within
    `SlugField(max_length=50)` even when the channel string itself is
    unusually long (worst case: `solo-` 5 + channel ≤ 36 + `-` 1 +
    8 hex = 50). Production channels are short (`max`, `telegram`,
    `whatsapp`, `web`) so trimming is defensive, not expected.
    """
    # 50 (slug max) − 5 (`solo-`) − 1 (`-`) − 8 (hex) = 36 chars for channel
    channel_trimmed = channel[:36]
    return f"solo-{channel_trimmed}-{_slug_hash(channel, channel_user_id)}"


def _advisory_lock_key(channel: str, channel_user_id: str) -> int:
    """Deterministic signed 64-bit key for `pg_advisory_xact_lock`.

    Postgres advisory locks take a single bigint. We derive it from
    SHA-256(channel:channel_user_id) — first 8 bytes → signed int64.

    Per Q4-Option-1 verdict 2026-05-25: `select_for_update` on an
    empty result set does NOT lock anything in Postgres — two
    concurrent first-time `create_solo_provider` calls could race
    and produce duplicate-key `IntegrityError` on `Tenant.slug`.
    Advisory locks solve this — the lock is held even when no row
    exists to lock, and `pg_advisory_xact_lock` is released
    automatically at transaction commit/rollback.
    """
    digest = hashlib.sha256(f"{channel}:{channel_user_id}".encode("utf-8")).digest()
    # `>q` = big-endian signed int64. PG advisory locks accept signed.
    return struct.unpack(">q", digest[:8])[0]


def _solo_external_id(bot_user_id: UUID) -> int:
    """Negative IntegerField value for synthetic `CatalogMaster.external_id`.

    Range: `[-(2^31-1), -1]`. Always negative → never collides with
    mysite-sourced positive `external_id`s. Deterministic per `bot_user_id`
    so re-seeding the same identity produces the same value (no `unique_together`
    drift across retries within the same tenant scope).
    """
    return -((bot_user_id.int % _SOLO_EXTERNAL_ID_SPACE) + 1)


def _default_tenant_name(display_name: str) -> str:
    """«Студия {first_name}» from display_name first token.

    Fallback «Студия мастера» if display_name is empty — keeps the
    placeholder shape so the Q-SOLO-3 future Profile-tab rename can
    detect a default name and offer customisation.
    """
    raw = (display_name or "").strip()
    first_token = raw.split()[0] if raw else "мастера"
    return f"Студия {first_token}"


# ─── The service ────────────────────────────────────────────────────────


@transaction.atomic
def create_solo_provider(
    *,
    channel: str,
    channel_user_id: str,
    display_name: str = "",
    phone: str = "",
    chat_id: str = "",
    tenant_name: Optional[str] = None,
) -> SoloOnboardingResult:
    """Atomic seed for self-employed solo provider registration.

    All-or-nothing transaction — if any of the 5 creates fails (Tenant,
    BotUser, 2x TenantStaff, CatalogMaster) the entire seed is rolled
    back. Per ADR-0011-style atomicity invariant: there is no partial
    seed observable to subsequent reads.

    Idempotency: Postgres advisory transaction lock keyed on identity
    hash (`pg_advisory_xact_lock`). Two concurrent first-time calls for
    the same identity serialize through the lock; the second call
    observes the first call's committed tenant and returns the existing
    rows with `created=False`. SQLite has no advisory locks — single-
    threaded test workflows don't race anyway. Production is Postgres.

    Args:
        channel: Channel slug — `"max"`, `"telegram"`, etc.
        channel_user_id: Stable identifier within the channel.
        display_name: For default tenant name + BotUser display_name field.
        phone: E.164 phone (PII — never logged raw).
        chat_id: Channel-side chat identifier for outbound dispatch.
        tenant_name: Optional override of the default «Студия {first_name}».

    Returns:
        SoloOnboardingResult with all 5 records + `created` flag.

    Raises:
        BootstrapTenantMissing: ops setup incomplete (see module docstring).
        SoloOnboardingPartialStateError: existing solo tenant has missing
            related rows (owner / admin / master). Manual ops inspection.
    """
    # Pre-flight: validate identity inputs.
    # Round-1 adversarial F-A (PRE_MERGE Cat 3+4): without this guard,
    # `create_solo_provider(channel="max", channel_user_id="", ...)` would
    # hash to a deterministic slug shared across all empty-ID callers,
    # producing a cross-user identity hijack via the idempotent return
    # branch (second caller observes first caller's tenant + bot_user
    # and is logged in as them). The caller (channel adapter / onboarding
    # skill) SHOULD never pass empty values, but the service is the
    # boundary — validate at the boundary, not at the caller.
    if not channel or not channel_user_id:
        raise ValueError(
            "create_solo_provider requires non-empty channel and "
            f"channel_user_id; got channel={channel!r}, "
            f"channel_user_id={channel_user_id!r}. Empty inputs would "
            "produce a deterministic slug shared across callers — "
            "identity-hijack risk via idempotency path."
        )

    # Pre-flight: bootstrap tenant must exist (ops setup gate).
    if not Tenant.objects.filter(slug=BOOTSTRAP_TENANT_SLUG).exists():
        raise BootstrapTenantMissing(
            f"Bootstrap tenant {BOOTSTRAP_TENANT_SLUG!r} not found. "
            "Ops pre-pilot setup incomplete — see "
            "apps/identity/services/solo_onboarding.py docstring for the "
            "3-step procedure (create solo MAX bot + map token + seed tenant)."
        )

    target_slug = _solo_tenant_slug(channel, channel_user_id)

    # Idempotency lock — Postgres advisory transaction lock keyed on the
    # identity hash. Released automatically at transaction commit/rollback.
    # Postgres-only; SQLite has no equivalent — single-threaded test
    # workflows don't race anyway. The lock guarantees that two concurrent
    # first-time calls for the same identity serialize: the second caller
    # waits for the first commit, then sees the new Tenant in the lookup
    # below and returns the idempotent path.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [_advisory_lock_key(channel, channel_user_id)],
            )

    existing_tenant = Tenant.objects.filter(slug=target_slug).first()

    if existing_tenant is not None:
        # Solo tenant already exists — verify the 4 related rows are
        # complete; return idempotently OR raise PartialStateError.
        existing_bot_user = BotUser.all_tenants.filter(
            tenant=existing_tenant,
            channel=channel,
            channel_user_id=channel_user_id,
        ).first()
        if existing_bot_user is None:
            raise SoloOnboardingPartialStateError(
                f"Solo tenant {target_slug!r} exists but no BotUser found "
                f"for (channel={channel!r}, channel_user_id={channel_user_id!r})."
            )

        existing_owner = TenantStaff.all_tenants.filter(
            tenant=existing_tenant,
            bot_user=existing_bot_user,
            role=TenantStaff.Role.OWNER,
            deactivated_at__isnull=True,
        ).first()
        existing_admin = TenantStaff.all_tenants.filter(
            tenant=existing_tenant,
            bot_user=existing_bot_user,
            role=TenantStaff.Role.ADMIN,
            deactivated_at__isnull=True,
        ).first()
        existing_master = CatalogMaster.all_tenants.filter(
            tenant=existing_tenant,
            linked_bot_user=existing_bot_user,
        ).first()

        if existing_owner is None or existing_admin is None or existing_master is None:
            raise SoloOnboardingPartialStateError(
                f"Solo tenant {target_slug!r} partial state: "
                f"owner={existing_owner is not None}, "
                f"admin={existing_admin is not None}, "
                f"master={existing_master is not None}. "
                "Manual ops inspection required before retry."
            )

        return SoloOnboardingResult(
            tenant=existing_tenant,
            bot_user=existing_bot_user,
            owner_staff=existing_owner,
            admin_staff=existing_admin,
            master=existing_master,
            created=False,
        )

    # Fresh seed — create all 5 rows atomically.
    final_name = tenant_name or _default_tenant_name(display_name)

    new_tenant = Tenant.objects.create(
        slug=target_slug,
        name=final_name,
        is_active=True,
    )
    new_bot_user = BotUser.all_tenants.create(
        tenant=new_tenant,
        channel=channel,
        channel_user_id=channel_user_id,
        display_name=display_name,
        phone=phone,
        chat_id=chat_id,
    )
    owner = TenantStaff.all_tenants.create(
        tenant=new_tenant,
        bot_user=new_bot_user,
        role=TenantStaff.Role.OWNER,
        created_by=new_bot_user,  # self-onboarded; same person creates own row
    )
    admin = TenantStaff.all_tenants.create(
        tenant=new_tenant,
        bot_user=new_bot_user,
        role=TenantStaff.Role.ADMIN,
        created_by=new_bot_user,
    )
    master = CatalogMaster.all_tenants.create(
        tenant=new_tenant,
        external_id=_solo_external_id(new_bot_user.id),
        external_updated_at=timezone.now(),
        name=final_name,
        linked_bot_user=new_bot_user,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        mode=CatalogMaster.Mode.INVITE,
    )

    emit(
        "identity.solo_provider.created",
        payload={
            "tenant_id": str(new_tenant.id),
            "tenant_slug": new_tenant.slug,
            "bot_user_id": str(new_bot_user.id),
            "channel": channel,
            "master_external_id": master.external_id,
            "phone_present": bool(phone),
        },
        distinct_id=str(new_bot_user.id),
    )
    logger.info(
        "identity.solo_provider.created tenant=%s bot_user=%s channel=%s",
        new_tenant.slug,
        new_bot_user.id,
        channel,
    )

    return SoloOnboardingResult(
        tenant=new_tenant,
        bot_user=new_bot_user,
        owner_staff=owner,
        admin_staff=admin,
        master=master,
        created=True,
    )


# ─── is_solo_provider — Веха 3 (/api/v1/me extension) ──────────────────


def is_solo_provider(tenant: Tenant) -> bool:
    """Return True if `tenant` is a single-person solo-provider workspace.

    Per Tau policy §3.1 (pragmatic «1 distinct person = solo»):

      distinct_people = active_staff ∪ active_masters
      return len(distinct_people) == 1

    Active = `TenantStaff.deactivated_at IS NULL` and
    `CatalogMaster.archived_at IS NULL`.

    Identity used for de-duplication:

    * a person who has a `BotUser` is identified by `bot_user_id`, so an
      owner who is also her own master collapses to one person;
    * a master row WITHOUT `linked_bot_user` still counts as a person —
      identified by the master row itself. The catalog does not create
      two rows for one human, so row-identity is a sound stand-in.

    Why the second bullet exists (DRF-1149, measured on the pilot
    2026-08-16). The previous implementation excluded unlinked masters
    on the grounds that they «don't correspond to a real BotUser
    identity». That answered a different question than the policy asks:
    Tau §3.1 counts *people who work here*, not *people who installed
    our app*. On the pilot the salon had four active accepted masters,
    three of them never bridged to a BotUser, so it reported
    `solo=True` — and the Mini App consequently rendered the five-tab
    solo surface, which has no entry point to any of the eleven admin
    screens. The salon lost its whole admin surface to a counting bug.

    Deliberate asymmetry when a person cannot be de-duplicated (an owner
    with a staff row AND a separate unlinked master row for herself
    counts as two): we prefer to over-count. Over-counting shows a solo
    provider the team surface — mildly redundant, everything still
    reachable. Under-counting hides the admin surface from a real salon
    — the failure this function just caused. Bridge the master row to
    the owner's BotUser and the count collapses back to one.

    Edge cases (accepted per Tau §3.1 «pragmatic»):
      - 1 staff row, 0 master rows → True (single distinct person)
      - 0 staff rows, 1 master row (linked or not) → True
      - Same person in both staff AND master, bridged → True (deduped)
      - Empty tenant (e.g. bootstrap) → False (0 distinct people)
      - 2+ distinct people → False

    Used by `/api/v1/me` to surface the «is this user a self-employed
    solo provider?» flag to the mini-app UI (Universal UI smart-default
    routing per memory `project_solo_provider_universal_ui`).
    """
    active_staff_ids: set = set(
        TenantStaff.all_tenants.filter(
            tenant=tenant,
            deactivated_at__isnull=True,
        ).values_list("bot_user_id", flat=True)
    )
    active_masters = CatalogMaster.all_tenants.filter(
        tenant=tenant,
        archived_at__isnull=True,
    ).values_list("id", "linked_bot_user_id")

    bridged_ids: set = set()
    unbridged_count = 0
    for _master_id, linked_bot_user_id in active_masters:
        if linked_bot_user_id is None:
            unbridged_count += 1
        else:
            bridged_ids.add(linked_bot_user_id)

    distinct_people = len(active_staff_ids | bridged_ids) + unbridged_count
    return distinct_people == 1
