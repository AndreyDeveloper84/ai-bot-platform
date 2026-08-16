"""Admin Mini App / dashboard auth — owner+admin gate (PR 2 / MM1-MM3).

Layered on :mod:`apps.miniapp_api.auth` (HMAC verification of MAX
``initData``) + :mod:`apps.identity.services.role_resolver` (role
detection from ADR-0008's two-table model).

The :func:`require_admin_role` decorator:

1. Verifies the ``Authorization: MaxInitData <raw>`` header (HMAC bound
   to ``MAX_BOT_TOKEN``).
2. Resolves the calling :class:`BotUser` via
   :func:`apps.identity.services.bot_user_resolver.resolve_bot_user` —
   the tenant of the bot whose token signed the initData first,
   ``MAX_BOT_TENANT_SLUG`` second (DRF-1150). We do NOT lazy-create a
   BotUser here — every admin caller has DM'd the tenant's manager bot
   at least once during onboarding.
3. Calls :func:`apps.identity.services.role_resolver.resolve_role` to
   build a :class:`RoleContext`.
4. Rejects with 403 unless ``is_owner`` OR ``is_admin``. Receptionist,
   master, and customer-only callers cannot reach admin endpoints by
   construction.
5. Attaches ``request.role_context`` + ``request.bot_user`` +
   ``request.tenant`` for downstream views and enters
   :func:`tenant_scope` so default-manager queries are tenant-bound.

Error envelope mirrors the master_api / miniapp_api shape:
``{"error": "<slug>", "detail": "<msg>"}`` with stable slugs:

* ``server_misconfigured`` (500) — ``MAX_BOT_TOKEN`` missing.
* ``malformed`` (400) — init-data unparseable.
* ``bad_signature`` (401) — HMAC mismatch.
* ``stale`` (401) — init-data ``auth_date`` expired.
* ``user_not_registered`` (404) — no BotUser row for this MAX user.
* ``forbidden`` (403) — caller is not Owner / Admin.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.identity.services.bot_user_resolver import (
    resolve_bot_user,
    resolve_tenant_slug_for_init_data,
)
from apps.identity.services.role_resolver import RoleContext, resolve_role
from apps.miniapp_api.auth import (
    InitDataBadSignature,
    InitDataError,
    InitDataMalformed,
    InitDataNotConfigured,
    InitDataStale,
    extract_init_data,
    verify_init_data,
)
from apps.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def require_admin_role(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Gate a view to Owner / Admin callers only.

    On success, attaches to the request:

    * ``request.verified_init_data`` — :class:`VerifiedInitData`
    * ``request.bot_user`` — the calling MAX user's :class:`BotUser`
    * ``request.tenant`` — the BotUser's owning :class:`Tenant`
    * ``request.role_context`` — :class:`RoleContext` from the resolver

    Then enters :func:`tenant_scope` for the duration of the view call
    so default-manager queries (``CatalogMaster.objects...``) are
    tenant-bound.

    Failure modes:
    * 400 malformed init-data.
    * 401 bad signature / stale init-data.
    * 404 BotUser not registered (never opened the salon bot).
    * 403 caller lacks the owner/admin capability.
    * 500 server misconfigured (no ``MAX_BOT_TOKEN`` /
      ``MAX_BOT_TENANT_SLUG``).
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        header = request.headers.get("Authorization", "")
        try:
            raw = extract_init_data(header)
            verified = verify_init_data(raw)
        except InitDataNotConfigured:
            logger.error("admin_api.auth.not_configured")
            return _error("server_misconfigured", "MAX bot token not configured", 500)
        except InitDataBadSignature:
            return _error("bad_signature", "initData signature mismatch", 401)
        except InitDataStale:
            return _error("stale", "initData expired — reopen the Mini App", 401)
        except InitDataMalformed as exc:
            return _error("malformed", str(exc), 400)
        except InitDataError as exc:
            return _error("unauthorized", str(exc), 401)

        # DRF-1150 — resolve identity by the bot that signed, not by the
        # bot-tenant setting alone. Shared with the master surface (the
        # rule was written there for DRF-1083); see
        # apps.identity.services.bot_user_resolver for why recency is the
        # wrong tie-breaker and what it cost on the pilot.
        #
        # This surface used to treat MAX_BOT_TENANT_SLUG as the whole
        # answer. That works only while the setting happens to name the
        # salon's tenant, which it does today — the pilot owner has two
        # BotUser rows (global_bot and formula-tela) and only the tenant
        # filter kept the admin endpoints on the right one. The moment a
        # second bot points elsewhere, every admin endpoint answers 404
        # for a legitimate owner.
        #
        # The «no tenant configured at all» 500 is kept: that is a real
        # deployment misconfiguration and should stay loud. The old
        # «tenant row not found» 500 is gone — with a bot registry the
        # setting may legitimately name a tenant this request is not for,
        # and the resolver's fall-through plus the role gate below cover
        # it. Cross-tenant escalation is not a risk here: resolve_role
        # reads TenantStaff within the resolved row's OWN tenant, so a
        # wrong pick can only under-privilege (403), never over.
        if not resolve_tenant_slug_for_init_data(verified):
            logger.error("admin_api.auth.no_tenant_slug")
            return _error(
                "server_misconfigured",
                "Bot tenant not configured — contact support.",
                500,
            )

        bot_user = resolve_bot_user(verified, surface="admin_api")
        if bot_user is None:
            return _error(
                "user_not_registered",
                "first interact with the bot before opening the admin Mini App",
                404,
            )

        role_ctx = resolve_role(bot_user)
        if not (role_ctx.is_owner or role_ctx.is_admin):
            return _error(
                "forbidden",
                "admin or owner role required",
                403,
            )

        request.verified_init_data = verified  # type: ignore[attr-defined]
        request.bot_user = bot_user  # type: ignore[attr-defined]
        request.tenant = bot_user.tenant  # type: ignore[attr-defined]
        request.role_context = role_ctx  # type: ignore[attr-defined]
        with tenant_scope(bot_user.tenant):
            return view_func(request, *args, **kwargs)

    return wrapper


__all__ = ["RoleContext", "require_admin_role"]
