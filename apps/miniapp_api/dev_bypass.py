"""Dev-only init-data HMAC bypass — DEBUG-gated, header-opt-in, loudly logged.

Purpose
-------
Local browser dev: open ``/onboarding/master?token=...``, ``/master/dashboard``,
customer screens etc. in a regular Chrome tab without spinning up a real MAX
bot session. The MAX ``initData`` HMAC requires a real MAX wrapper to mint;
during dev that's painful. This helper offers an opt-in, per-request bypass
that is **DEAD CODE in production** because ``settings.DEBUG`` is the gate.

Security model
--------------

| ``settings.DEBUG`` | ``X-Dev-Bypass: 1`` | Other dev headers           | Result                                  |
|--------------------|---------------------|-----------------------------|-----------------------------------------|
| False (prod)       | any                 | any                         | bypass ignored, HMAC enforced (no log)  |
| True (dev)         | absent              | n/a                         | HMAC enforced (real init-data path)     |
| True (dev)         | ``1``               | valid user + tenant headers | BYPASS + WARNING log                    |
| True (dev)         | ``1``               | missing / invalid           | no bypass + WARNING log                 |

Rationale: in prod the bypass code path is unreachable — the ``if not settings.DEBUG``
guard exits before any header is consulted. In dev the bypass is opt-in per request
(must send three headers) and every use emits a WARNING log so it can never be
"quietly" running.

Wiring
------
Called BEFORE HMAC verification by:

* :func:`apps.miniapp_api.views.require_init_data` (customer surface)
* :func:`apps.master_api.auth.require_master_init_data` (master surface, linked)
* :func:`apps.master_api.auth.require_init_data_only` (master surface, M0 claim)

NOT called from the admin surface (``apps.admin_api.auth``) — admin endpoints
keep strict HMAC even in dev to avoid confusion when iterating on owner /
admin role checks.

Precedence rule
---------------
When ``X-Dev-Bypass: 1`` is set AND a valid init-data Authorization header is
ALSO present, **the bypass takes precedence** (caller explicitly opted into
dev mode). This is the simpler mental model — the caller is signalling "I am
running in dev, skip HMAC". The opposite policy (init-data wins) would mean
dev headers are silently ignored when init-data happens to be valid, which
breaks the "headers always work in dev" invariant developers expect.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


DEV_BYPASS_HEADER = "HTTP_X_DEV_BYPASS"
"""WSGI/Django META key for ``X-Dev-Bypass`` header."""

DEV_BYPASS_USER_ID_HEADER = "HTTP_X_DEV_USER_ID"
"""WSGI/Django META key for ``X-Dev-User-Id`` header."""

DEV_BYPASS_TENANT_SLUG_HEADER = "HTTP_X_DEV_TENANT_SLUG"
"""WSGI/Django META key for ``X-Dev-Tenant-Slug`` header."""


def try_dev_bypass(request: "HttpRequest") -> tuple[BotUser, Tenant] | None:
    """Resolve a (BotUser, Tenant) pair from dev headers, or None.

    Returns ``None`` (caller falls through to HMAC verification) when:

    * ``settings.DEBUG is False`` — dead code in prod, never logs.
    * ``X-Dev-Bypass`` header is absent or not exactly ``"1"``.
    * ``X-Dev-User-Id`` or ``X-Dev-Tenant-Slug`` are missing (logs WARNING).
    * The referenced tenant / user does not exist or is cross-tenant
      (logs WARNING).

    Returns ``(bot_user, tenant)`` on success (logs WARNING).

    NEVER raises — bypass failure always means "fall through to HMAC".
    """

    # PROD guard — runs FIRST. In production this exits before reading
    # any header, so no log lines leak even if an attacker probes with
    # dev headers. Coverage: test_prod_mode_ignores_bypass.
    if not settings.DEBUG:
        return None

    if request.META.get(DEV_BYPASS_HEADER) != "1":
        return None

    user_id = request.META.get(DEV_BYPASS_USER_ID_HEADER, "")
    tenant_slug = request.META.get(DEV_BYPASS_TENANT_SLUG_HEADER, "")

    if not user_id or not tenant_slug:
        logger.warning(
            "[DEV-BYPASS] rejected: missing X-Dev-User-Id or X-Dev-Tenant-Slug "
            "(user_id=%r tenant_slug=%r url=%s)",
            user_id,
            tenant_slug,
            request.path,
        )
        return None

    try:
        tenant = Tenant.objects.get(slug=tenant_slug)
    except Tenant.DoesNotExist:
        logger.warning(
            "[DEV-BYPASS] rejected: tenant not found (tenant_slug=%r url=%s)",
            tenant_slug,
            request.path,
        )
        return None

    try:
        bot_user = BotUser.all_tenants.select_related("tenant").get(
            id=user_id,
            tenant=tenant,
        )
    except (BotUser.DoesNotExist, ValidationError, ValueError, TypeError):
        # ValidationError: ``user_id`` is not a valid UUID (Django raises
        #   ValidationError from UUIDField.to_python).
        # ValueError / TypeError: defensive — unexpected non-string input.
        logger.warning(
            "[DEV-BYPASS] rejected: bot_user not found (tenant_slug=%s user_id=%r url=%s)",
            tenant_slug,
            user_id,
            request.path,
        )
        return None

    logger.warning(
        "[DEV-BYPASS] init-data validation skipped: tenant=%s user=%s url=%s",
        tenant_slug,
        user_id,
        request.path,
    )
    return bot_user, tenant


__all__ = [
    "DEV_BYPASS_HEADER",
    "DEV_BYPASS_TENANT_SLUG_HEADER",
    "DEV_BYPASS_USER_ID_HEADER",
    "try_dev_bypass",
]
