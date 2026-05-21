"""Master Mini App auth + invite-token helpers (PR 1 / M0).

Layered on top of :mod:`apps.miniapp_api.auth` (HMAC verification of
MAX ``initData``). The master surface adds:

1. **Master identity resolution**. The decorator
   :func:`require_master_init_data` verifies init-data, looks up the
   ``BotUser`` for the tenant, then resolves the linked
   :class:`apps.catalog.models.CatalogMaster` via the
   ``master_identity`` reverse relation. Anyone who hits the master
   surface without a linked + active master record is rejected.

2. **Invite token validation**. :func:`validate_invite_token` is the
   single atomic guard used by the three ``/onboarding/*`` endpoints.
   It uses ``select_for_update`` so concurrent claim/accept/reject
   taps can't race past the PENDING gate.

3. **Session token issuance**. :func:`issue_master_session_token` +
   :func:`decode_master_session_token` wrap Django's signed-payload
   utilities. PR 1 only ISSUES — the consumption path lands when the
   dashboard endpoints do (PR 7+).

### Why django.core.signing instead of PyJWT

PyJWT is NOT in ``pyproject.toml``. The spec is intentionally JWT-
compatible at the payload level so a future migration is a flip of
this module without touching callers. ``TimestampSigner`` provides
the same security properties (HMAC + freshness) with zero new deps.
The header shape ``{alg, typ}`` is encoded in the payload so the
wire format still round-trips through any JWT decoder.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.miniapp_api.auth import (
    InitDataBadSignature,
    InitDataError,
    InitDataMalformed,
    InitDataNotConfigured,
    InitDataStale,
    extract_init_data,
    verify_init_data,
)
from apps.miniapp_api.dev_bypass import try_dev_bypass
from apps.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)


SESSION_TOKEN_HEADER = '{"alg": "HS256", "typ": "MASTER"}'
"""Canonical JWT-style header — embedded into the signed payload so the
on-wire token round-trips through any future JWT decoder."""

SESSION_TOKEN_SALT = "master_api.session"
"""Per-token salt distinguishing this signer from other Django signed
payloads (CSRF, session cookies, password reset). Compromising one MUST
NOT compromise the others."""


# --- exception hierarchy ---------------------------------------------------


class InviteTokenError(Exception):
    """Base — carries a stable ``slug`` for the HTTP error envelope."""

    slug: str = "invite_token_error"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.slug)
        self.detail = detail or self.slug


class InvalidInviteToken(InviteTokenError):
    """Token not found OR not visible in the current tenant scope.

    Maps to 404 deliberately — surfacing 'wrong tenant' as a distinct
    error would leak the existence of a master row across tenants.
    """

    slug = "invalid_invite_token"


class InviteAlreadyUsed(InviteTokenError):
    """Token exists but invite_status != PENDING — already consumed.

    Maps to 409. Idempotent re-accept by the SAME linked BotUser is
    handled at the endpoint layer, not here.
    """

    slug = "invite_already_used"


class InviteExpired(InviteTokenError):
    """Token exists, status PENDING, but invite_expires_at < now()."""

    slug = "invite_expired"


# --- session-token primitives ---------------------------------------------


@dataclass(frozen=True)
class MasterSessionPayload:
    """Decoded master session token payload.

    Mirrors a JWT payload field-for-field so a future PyJWT migration is
    a one-line wire-format swap (the same dict is what jwt.encode would
    serialize).
    """

    master_id: uuid.UUID
    tenant_id: uuid.UUID
    bot_user_id: uuid.UUID
    iat: int
    exp: int


def _session_signer() -> TimestampSigner:
    """Build the signer with the configured secret (or fall back to SECRET_KEY).

    Per spec: ``MASTER_SESSION_SECRET`` overrides ``SECRET_KEY`` when
    set. Same pattern as Django session-cookie signing.
    """

    key = getattr(settings, "MASTER_SESSION_SECRET", "") or settings.SECRET_KEY
    return TimestampSigner(key=key, salt=SESSION_TOKEN_SALT)


def issue_master_session_token(
    *,
    master_id: uuid.UUID,
    tenant_id: uuid.UUID,
    bot_user_id: uuid.UUID,
    now: int | None = None,
) -> tuple[str, int]:
    """Issue a signed session token for a freshly-accepted master.

    Returns ``(token, exp_unix_seconds)``. The token's wire format is
    ``<base64(payload)>:<timestamp>:<signature>`` — Django's standard
    TimestampSigner output. The payload itself is a JSON dict with the
    JWT-style header embedded as ``_h``; consumers (PR 7+) MUST treat
    the payload as opaque except for the documented fields.

    TTL is ``settings.MASTER_SESSION_TTL_DAYS`` (default 30 days). PR 1
    only issues — consumption / refresh / revoke endpoints land later.
    """

    ttl_days = int(getattr(settings, "MASTER_SESSION_TTL_DAYS", 30))
    iat = now if now is not None else int(timezone.now().timestamp())
    exp = iat + ttl_days * 24 * 3600
    payload = {
        "_h": SESSION_TOKEN_HEADER,
        "master_id": str(master_id),
        "tenant_id": str(tenant_id),
        "bot_user_id": str(bot_user_id),
        "iat": iat,
        "exp": exp,
    }
    token = _session_signer().sign(json.dumps(payload, separators=(",", ":")))
    return token, exp


def decode_master_session_token(token: str) -> MasterSessionPayload:
    """Verify + decode a master session token. Raises on tampering or expiry.

    NOT called from PR 1 view code — added now so PR 7+ middleware can
    import the canonical decoder without a second round of API design.
    """

    ttl_days = int(getattr(settings, "MASTER_SESSION_TTL_DAYS", 30))
    max_age_seconds = ttl_days * 24 * 3600
    try:
        raw = _session_signer().unsign(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise BadSignature("session token expired") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadSignature("session token payload not JSON") from exc

    try:
        return MasterSessionPayload(
            master_id=uuid.UUID(payload["master_id"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            bot_user_id=uuid.UUID(payload["bot_user_id"]),
            iat=int(payload["iat"]),
            exp=int(payload["exp"]),
        )
    except (KeyError, ValueError) as exc:
        raise BadSignature("session token payload malformed") from exc


# --- invite-token helpers --------------------------------------------------


def generate_invite_token() -> uuid.UUID:
    """Pure helper — returns a fresh UUIDv4. No DB write.

    Lives here (not in catalog/) so the MM2 invite-create endpoint (a
    separate PR) imports from one canonical place along with the
    validate counterpart.
    """

    return uuid.uuid4()


def validate_invite_token(
    token: str | uuid.UUID,
    tenant: Any,
) -> CatalogMaster:
    """Atomic invite-token validation with row lock.

    Returns the :class:`CatalogMaster` row when the token is:

    * resolvable in the given tenant
    * status PENDING
    * not past ``invite_expires_at``

    Caller is responsible for being inside ``transaction.atomic()`` —
    we ``select_for_update`` to serialize concurrent accept/reject
    taps. For idempotent reads (``/onboarding/claim``) callers can
    wrap a separate ``transaction.atomic`` around the call.

    Raises:
      InvalidInviteToken — bad UUID, no row, or wrong tenant.
      InviteAlreadyUsed — token exists but status != PENDING.
      InviteExpired — token exists, status PENDING, past expiry.
    """

    try:
        token_uuid = uuid.UUID(str(token))
    except (TypeError, ValueError) as exc:
        raise InvalidInviteToken("token is not a valid UUID") from exc

    # ``select_for_update`` locks the row for the duration of the enclosing
    # transaction. Combined with the unique constraint on invite_token, this
    # serializes any concurrent accept/reject/claim against the same token.
    qs = CatalogMaster.all_tenants.select_for_update().filter(
        invite_token=token_uuid,
        tenant=tenant,
    )
    master = qs.first()
    if master is None:
        raise InvalidInviteToken("token not found for this tenant")

    if master.invite_status != CatalogMaster.InviteStatus.PENDING:
        raise InviteAlreadyUsed(f"invite already in state {master.invite_status!r}")

    if master.invite_expires_at is not None and master.invite_expires_at < timezone.now():
        raise InviteExpired("invite expired")

    return master


# --- decorator: require linked + active master ----------------------------


INVITE_TOKEN_SLUG_TO_STATUS = {
    "invalid_invite_token": 404,
    "invite_already_used": 409,
    "invite_expired": 410,
}
"""Stable mapping for view code → HTTP status."""


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def require_master_init_data(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Master analogue of :func:`apps.miniapp_api.auth.require_init_data`.

    On success, attaches:

    * ``request.verified_init_data`` — :class:`VerifiedInitData`
    * ``request.bot_user`` — the calling MAX user's BotUser
    * ``request.tenant`` — the BotUser's owning Tenant
    * ``request.master`` — the linked :class:`CatalogMaster`

    Failure modes:
    * 400 malformed init-data
    * 401 bad signature / stale init-data / no linked master / inactive
      master / archived master
    * 404 BotUser not registered (never opened the salon bot)
    * 500 server not configured (no MAX_BOT_TOKEN)
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Dev-bypass (DEBUG-only, header-opt-in) — see apps/miniapp_api/dev_bypass.py.
        # Prod-safe: try_dev_bypass returns None unconditionally when DEBUG=False.
        bypass = try_dev_bypass(request)
        verified = None
        bot_user: BotUser | None
        if bypass is not None:
            bot_user, _bypass_tenant = bypass
        else:
            header = request.headers.get("Authorization", "")
            try:
                raw = extract_init_data(header)
                verified = verify_init_data(raw)
            except InitDataNotConfigured:
                logger.error("master_api.auth.not_configured")
                return _error("server_misconfigured", "MAX bot token not configured", 500)
            except InitDataBadSignature:
                return _error("bad_signature", "initData signature mismatch", 401)
            except InitDataStale:
                return _error("stale", "initData expired — reopen the Mini App", 401)
            except InitDataMalformed as exc:
                return _error("malformed", str(exc), 400)
            except InitDataError as exc:
                return _error("unauthorized", str(exc), 401)

            bot_user = (
                BotUser.all_tenants.filter(channel="max", channel_user_id=verified.user_id)
                .select_related("tenant")
                .order_by("-last_seen")
                .first()
            )
        if bot_user is None:
            return _error(
                "user_not_registered",
                "first interact with the bot before opening the Mini App",
                404,
            )

        # Resolve the linked master via the OneToOne reverse relation.
        # ``all_tenants`` so cross-tenant resolution is impossible (the
        # BotUser FK already constrains tenant) AND the default manager's
        # current_tenant() lookup isn't required.
        master = (
            CatalogMaster.all_tenants.filter(linked_bot_user=bot_user)
            .select_related("tenant")
            .first()
        )
        if master is None:
            return _error(
                "not_a_master",
                "this account is not linked to a master",
                401,
            )
        if not master.is_active or master.archived_at is not None:
            return _error(
                "master_inactive",
                "master account is inactive or archived",
                403,
            )

        request.verified_init_data = verified  # type: ignore[attr-defined]
        request.bot_user = bot_user  # type: ignore[attr-defined]
        request.tenant = bot_user.tenant  # type: ignore[attr-defined]
        request.master = master  # type: ignore[attr-defined]
        with tenant_scope(bot_user.tenant):
            return view_func(request, *args, **kwargs)

    return wrapper


def require_init_data_only(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Init-data verification WITHOUT requiring a linked master.

    Used by the ``/onboarding/*`` endpoints — at claim/accept time, the
    BotUser may not yet be linked to any master. The decorator still
    resolves + attaches the BotUser + Tenant + enters tenant_scope so
    downstream queries are scoped correctly.

    On failure: same envelope as :func:`require_master_init_data` minus
    the master-linkage checks.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Dev-bypass (DEBUG-only, header-opt-in). Critical for the M0
        # /onboarding/claim bootstrap when no real MAX session exists.
        bypass = try_dev_bypass(request)
        verified = None
        bot_user: BotUser | None
        if bypass is not None:
            bot_user, _bypass_tenant = bypass
        else:
            header = request.headers.get("Authorization", "")
            try:
                raw = extract_init_data(header)
                verified = verify_init_data(raw)
            except InitDataNotConfigured:
                logger.error("master_api.auth.not_configured")
                return _error("server_misconfigured", "MAX bot token not configured", 500)
            except InitDataBadSignature:
                return _error("bad_signature", "initData signature mismatch", 401)
            except InitDataStale:
                return _error("stale", "initData expired — reopen the Mini App", 401)
            except InitDataMalformed as exc:
                return _error("malformed", str(exc), 400)
            except InitDataError as exc:
                return _error("unauthorized", str(exc), 401)

            bot_user = (
                BotUser.all_tenants.filter(channel="max", channel_user_id=verified.user_id)
                .select_related("tenant")
                .order_by("-last_seen")
                .first()
            )
        if bot_user is None:
            return _error(
                "user_not_registered",
                "first interact with the bot before opening the Mini App",
                404,
            )

        request.verified_init_data = verified  # type: ignore[attr-defined]
        request.bot_user = bot_user  # type: ignore[attr-defined]
        request.tenant = bot_user.tenant  # type: ignore[attr-defined]
        with tenant_scope(bot_user.tenant):
            return view_func(request, *args, **kwargs)

    return wrapper


__all__ = [
    "INVITE_TOKEN_SLUG_TO_STATUS",
    "InvalidInviteToken",
    "InviteAlreadyUsed",
    "InviteExpired",
    "InviteTokenError",
    "MasterSessionPayload",
    "decode_master_session_token",
    "generate_invite_token",
    "issue_master_session_token",
    "require_init_data_only",
    "require_master_init_data",
    "validate_invite_token",
]
