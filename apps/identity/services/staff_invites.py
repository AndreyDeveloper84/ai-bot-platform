"""Issue and redeem staff invite codes (DRF-1061).

### The door this opens

`resolve_role` answers *customer* for everyone on the pilot because
``TenantStaff`` is empty and no master has ``linked_bot_user`` set. Nothing
in the platform could write either. This module is the one path that can:
a person types a code into the salon bot (or taps a link carrying it) and
comes out the other side as staff, with the already-built admin Mini App
becoming reachable on their very next request — the role resolver has no
cache, so there is nothing to invalidate.

### Code format

``AYLA-7K3M`` — a fixed prefix plus four characters from a 31-symbol
alphabet with ``0/O`` and ``1/I/L`` removed, because these codes get read
out loud and written down. Case and dashes are insignificant: the code is
normalized before hashing, so ``ayla7k3m`` and ``AYLA-7K3M`` are the same
code.

31**4 = ~924k combinations is not cryptographic, and deliberately so — a code long
enough to be unguessable is a code nobody will type. The entropy budget is
spent on usability and the security comes from three other places: the code
is single-use, it expires in 7 days, and redemption is rate-limited per
person. Guessing at 5 attempts an hour needs ~14 years for a 50% chance.

### What redemption writes

* ``owner`` / ``admin`` / ``receptionist`` → a ``TenantStaff`` row;
* ``master`` → links ``CatalogMaster.linked_bot_user`` on the **existing**
  catalog row and flips it to accepted+active.

The master path never creates a catalog row. All four pilot masters already
exist, and a duplicate would be invisible to the booking mirror (whose
``specialist_id`` points at the original), leaving the master staring at an
empty day next to their real appointments.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.tenancy.models import StaffInvite, TenantStaff

logger = logging.getLogger(__name__)

#: No 0/O, no 1/I/L — these codes are read aloud and written down.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # pragma: allowlist secret
CODE_PREFIX = "AYLA"
CODE_BODY_LEN = 4
INVITE_TTL_DAYS = 7

#: Attempts per person per window. Short codes make this the real security
#: boundary, not the entropy — see the module docstring.
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 3600


class InviteError(Exception):
    """Base class for redemption failures."""

    #: Stable slug for the caller to branch on. Never shown raw to a user.
    slug = "invite_error"


class InviteNotFound(InviteError):
    """No outstanding invite matches this code.

    Deliberately also raised for expired and already-used codes: the person
    typing them cannot tell the cases apart, and neither should someone
    guessing. The real reason goes to the log.
    """

    slug = "invite_not_found"


class InviteRateLimited(InviteError):
    """Too many wrong attempts from this person."""

    slug = "invite_rate_limited"


class InviteMasterMissing(InviteError):
    """A master invite whose catalog row disappeared between issue and use."""

    slug = "invite_master_missing"


class OwnerAlreadyExists(InviteError):
    """The tenant already has an active owner.

    A partial unique index enforces one per tenant; handover is
    deactivate-then-invite, not a second row.
    """

    slug = "owner_already_exists"


@dataclass(frozen=True)
class RedeemResult:
    """Outcome of a successful redemption."""

    role: str
    tenant_id: str
    already_had_role: bool
    catalog_master_id: str | None = None


def normalize_code(raw: str) -> str:
    """Fold a typed code to its canonical form.

    Uppercases, strips every separator a human might insert, and drops the
    ``AYLA`` prefix if present. ``ayla-7k3m``, ``AYLA 7K3M`` and ``7k3m``
    all normalize identically — the person is trying to give us four
    characters and should not fail on punctuation.
    """

    cleaned = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    if cleaned.startswith(CODE_PREFIX):
        cleaned = cleaned[len(CODE_PREFIX) :]
    return cleaned


def looks_like_code(raw: str) -> bool:
    """Cheap shape test: is this message plausibly a code at all?

    Lets the bot try any message as a code without an FSM — no "now send me
    your code" state to get stuck in — while not treating «привет» as a
    failed attempt worth counting against the rate limit.
    """

    normalized = normalize_code(raw)
    return len(normalized) == CODE_BODY_LEN and all(c in CODE_ALPHABET for c in normalized)


def _hash_code(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def format_code(normalized: str) -> str:
    """Render for display: ``7K3M`` → ``AYLA-7K3M``."""

    return f"{CODE_PREFIX}-{normalized}"


def generate_code() -> str:
    """A fresh normalized code body. ``secrets``, not ``random``."""

    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_BODY_LEN))


def issue_staff_invite(
    *,
    tenant,
    role: str,
    catalog_master: CatalogMaster | None = None,
    created_by: BotUser | None = None,
    note: str = "",
    ttl_days: int = INVITE_TTL_DAYS,
) -> tuple[StaffInvite, str]:
    """Create an invite and return it together with the plaintext code.

    The code is returned exactly once — it is not recoverable afterwards,
    because only its hash is stored. Callers show it to the issuer and then
    forget it.

    Raises:
      ValueError: role=master without a catalog row, or vice versa.
    """

    if role == StaffInvite.Role.MASTER and catalog_master is None:
        raise ValueError("role='master' requires an existing catalog_master to link")
    if role != StaffInvite.Role.MASTER and catalog_master is not None:
        raise ValueError("catalog_master is only meaningful for role='master'")

    # Retry on the astronomically unlikely hash collision rather than
    # handing the issuer an IntegrityError.
    for _ in range(5):
        code = generate_code()
        code_hash = _hash_code(code)
        try:
            invite = StaffInvite.all_tenants.create(
                tenant=tenant,
                role=role,
                code_hash=code_hash,
                catalog_master=catalog_master,
                expires_at=timezone.now() + timedelta(days=ttl_days),
                created_by=created_by,
                note=note,
            )
        except IntegrityError:
            continue
        logger.info(
            "identity.staff_invite.issued tenant=%s role=%s invite=%s",
            tenant.slug,
            role,
            invite.id,
        )
        return invite, format_code(code)

    raise RuntimeError("could not generate a unique invite code after 5 attempts")


def _attempt_key(bot_user: BotUser) -> str:
    return f"staff_invite:attempts:{bot_user.channel}:{bot_user.channel_user_id}"


def _check_rate_limit(bot_user: BotUser) -> None:
    """Bound guessing, per person, per hour.

    Uses the Django cache — Redis in production, locmem in tests — which is
    the same primitive the AI-draft limiter uses, and for the same reason:
    the counter must be shared across workers or N gunicorn processes give
    an attacker N times the budget.

    ``add`` then ``incr`` rather than plain ``incr``: ``add`` sets the TTL
    exactly once, at the start of the window, so the window is fixed rather
    than sliding forward with every attempt (which would make it possible
    to keep a counter alive forever and never reset).

    Fail-open on cache trouble is deliberate. A cache outage must not lock
    the owner out of their own salon; the limit is a brute-force brake, and
    single-use codes plus a 7-day expiry are the actual bounds.
    """

    from django.core.cache import cache

    key = _attempt_key(bot_user)
    try:
        if cache.add(key, 1, timeout=ATTEMPT_WINDOW_SECONDS):
            return  # First attempt in this window.
        attempts = cache.incr(key)
    except ValueError:
        # incr on a key that expired between add and incr — treat as first.
        return
    except Exception as exc:  # noqa: BLE001 — brake, not a gate
        logger.warning("identity.staff_invite.rate_limit_unavailable exc=%s", exc)
        return

    if attempts > MAX_ATTEMPTS:
        logger.warning(
            "identity.staff_invite.rate_limited channel_user_id=%s attempts=%s",
            bot_user.channel_user_id,
            attempts,
        )
        raise InviteRateLimited("too many attempts")


def _clear_rate_limit(bot_user: BotUser) -> None:
    """Forget failed attempts once the person proves they had a real code."""

    from django.core.cache import cache

    try:
        cache.delete(_attempt_key(bot_user))
    except Exception:  # noqa: BLE001
        pass


def redeem_staff_invite(*, code: str, bot_user: BotUser, tenant) -> RedeemResult:
    """Turn a code into staff access for ``bot_user`` in ``tenant``.

    ``tenant`` is required, and it is the salon whose bot the person is
    talking to — not a hint taken from the invite. A code issued for
    another salon must not resolve here at all.

    Why that matters concretely: without the filter, an invite mistakenly
    issued for salon B (one wrong ``--tenant`` flag) and typed into salon
    A's bot would be *found*, *burned* (``used_at`` set, single-use gone)
    and would create a ``TenantStaff(tenant=B, bot_user=<A's row>)`` —
    which ``resolve_role`` never reads, because it filters by the bot
    user's own tenant. The person would be told "you are now the owner of
    A", still resolve as a customer on the next message, and their code
    would be spent. Recovering that needs SQL.

    Filtering at lookup means the code is simply not found: the person
    hears "this code did not work", the code stays valid, and the operator
    can re-issue it for the right salon.

    Idempotent in the way that matters to a human: someone who already
    holds the role gets a success answer rather than an error, because from
    their side "I am an admin" is already true and an error would be
    baffling. The code is still consumed once.

    Raises:
      InviteRateLimited, InviteNotFound, InviteMasterMissing,
      OwnerAlreadyExists — all with a stable ``.slug``.
    """

    _check_rate_limit(bot_user)

    normalized = normalize_code(code)
    code_hash = _hash_code(normalized)
    now = timezone.now()

    with transaction.atomic():
        invite = (
            StaffInvite.all_tenants.select_for_update()
            .filter(code_hash=code_hash, tenant=tenant)
            .select_related("tenant", "catalog_master")
            .first()
        )
        # One answer for "no such code", "already used" and "expired". The
        # person cannot distinguish them and neither should a guesser; the
        # log keeps the truth.
        if invite is None:
            logger.info("identity.staff_invite.miss channel_user_id=%s", bot_user.channel_user_id)
            raise InviteNotFound("no such invite")
        if invite.used_at is not None:
            logger.info("identity.staff_invite.already_used invite=%s", invite.id)
            raise InviteNotFound("already used")
        if invite.expires_at <= now:
            logger.info("identity.staff_invite.expired invite=%s", invite.id)
            raise InviteNotFound("expired")

        if invite.role == StaffInvite.Role.MASTER:
            result = _link_master(invite, bot_user)
        else:
            result = _grant_staff_role(invite, bot_user)

        invite.used_at = now
        invite.used_by = bot_user
        invite.save(update_fields=["used_at", "used_by"])

    _clear_rate_limit(bot_user)
    logger.info(
        "identity.staff_invite.redeemed invite=%s role=%s tenant=%s",
        invite.id,
        invite.role,
        invite.tenant.slug,
    )
    return result


def _grant_staff_role(invite: StaffInvite, bot_user: BotUser) -> RedeemResult:
    """Create (or find) the TenantStaff row this invite grants."""

    existing = TenantStaff.all_tenants.filter(
        tenant_id=invite.tenant_id,
        bot_user=bot_user,
        role=invite.role,
        deactivated_at__isnull=True,
    ).first()
    if existing is not None:
        return RedeemResult(
            role=invite.role,
            tenant_id=str(invite.tenant_id),
            already_had_role=True,
        )

    try:
        TenantStaff.all_tenants.create(
            tenant_id=invite.tenant_id,
            bot_user=bot_user,
            role=invite.role,
            created_by=invite.created_by,
        )
    except IntegrityError as exc:
        # The only constraint that can fire here is the partial unique
        # index guaranteeing one active owner per tenant. Surfacing it as a
        # 500 would be wrong: the operator issued a second owner code, and
        # that is an answerable situation, not a crash.
        if invite.role == StaffInvite.Role.OWNER:
            logger.warning(
                "identity.staff_invite.owner_conflict tenant=%s invite=%s",
                invite.tenant_id,
                invite.id,
            )
            raise OwnerAlreadyExists("tenant already has an active owner") from exc
        raise

    return RedeemResult(
        role=invite.role,
        tenant_id=str(invite.tenant_id),
        already_had_role=False,
    )


def _link_master(invite: StaffInvite, bot_user: BotUser) -> RedeemResult:
    """Attach a person to the master row that already exists.

    Sets ``is_active=True`` alongside the link on purpose. The pre-existing
    admin invite path leaves invited masters at ``is_active=False`` and
    nothing ever flips it, so ``resolve_role`` reports them as masters while
    every master endpoint answers 403 ``master_inactive`` (DRF-1080). A
    person who just proved they hold a valid code is active by definition.
    """

    # A CHECK constraint guarantees master invites carry a catalog row, but
    # the column is nullable for the other roles — narrow it explicitly
    # rather than asserting it away.
    master_id = invite.catalog_master_id
    master = (
        CatalogMaster.all_tenants.select_for_update()
        .filter(pk=master_id, tenant_id=invite.tenant_id)
        .first()
        if master_id is not None
        else None
    )
    if master is None or master.archived_at is not None:
        logger.warning("identity.staff_invite.master_missing invite=%s", invite.id)
        raise InviteMasterMissing("catalog master is gone or archived")

    if master.linked_bot_user_id == bot_user.id:
        return RedeemResult(
            role=StaffInvite.Role.MASTER,
            tenant_id=str(invite.tenant_id),
            already_had_role=True,
            catalog_master_id=str(master.id),
        )

    master.linked_bot_user = bot_user
    master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
    master.mode = CatalogMaster.Mode.INVITE
    master.invite_token = None
    master.is_active = True
    master.save(
        update_fields=[
            "linked_bot_user",
            "invite_status",
            "mode",
            "invite_token",
            "is_active",
        ]
    )

    return RedeemResult(
        role=StaffInvite.Role.MASTER,
        tenant_id=str(invite.tenant_id),
        already_had_role=False,
        catalog_master_id=str(master.id),
    )
