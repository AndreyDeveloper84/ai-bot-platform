"""Identity HTTP views — unified /api/v1/me endpoint (PR 1.5 / ADR-0008).

The Mini App calls ``GET /api/v1/me`` on launch to learn who's holding
the device. The response shape is the canonical "whoami" for every
surface (customer / master / admin Mini Apps share this endpoint) and
the source of truth the frontend uses to pick a landing screen and a
role chip.

The endpoint is read-only:

- Auth: ``Authorization: MaxInitData <raw>`` header (reuses
  :func:`apps.miniapp_api.views.require_init_data`).
- DB: at most three queries (BotUser lookup is in the auth decorator,
  master detection is one reverse-OneToOne lookup, staff detection is
  one indexed filter).
- No audit row — a read-on-launch is a normal request log entry, not a
  privileged action. The audit slug catalogue intentionally does NOT
  carry an ``identity.me`` event (see ADR-0008 "Migration / rollout
  impact").

The response carries the same :class:`RoleContext` fields the resolver
produces, plus a small ``user`` and ``tenant`` block for display. Phone
is ALWAYS masked — even the user looking at their own profile sees
``+• ••• ••• ••67``. The unmasked phone is reachable via a separate,
audited endpoint (not in scope here).
"""

from __future__ import annotations

import logging
import re

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.miniapp_api.views import require_init_data

logger = logging.getLogger(__name__)


_PHONE_TAIL_RE = re.compile(r"(\d{2})$")


def _mask_phone(phone: str) -> str:
    """E.164-style phone → ``+• ••• ••• ••67`` (last two digits visible).

    Mirrors the helper in :mod:`apps.master_api.views` — copy kept local
    so :mod:`apps.identity` doesn't reach into the master app for a
    one-liner utility. Defensive: empty input returns empty string;
    short input returns ``•••``.
    """

    if not phone:
        return ""
    tail = _PHONE_TAIL_RE.search(phone)
    if tail is None:
        return "•••"
    return f"+• ••• ••• ••{tail.group(1)}"


@require_http_methods(["GET"])
@require_init_data
def me_view(request: HttpRequest) -> HttpResponse:
    """Return the resolved identity + role + capabilities for the caller.

    Auth: ``Authorization: MaxInitData <raw>`` via
    :func:`require_init_data`. Returns 401 / 400 / 500 from the
    decorator on auth failure; on success returns 200 with the shape
    documented in ADR-0008 / module docstring.

    The response carries:

    - ``user`` — ``{id, name, phone_masked}``. ``name`` falls back
      through ``client_name → display_name → channel_user_id`` so the
      frontend always has something non-empty to display.
    - ``tenant`` — ``{id, name, slug}``.
    - ``role`` — primary role (highest privilege).
    - ``capabilities`` — sorted list of slug strings the caller
      qualifies for under ANY of their roles. The frontend uses this
      for menu visibility; the server re-checks every privileged
      endpoint.
    - ``is_customer`` / ``is_master`` / ``is_receptionist`` /
      ``is_admin`` / ``is_owner`` — the five role flags.
    - ``master_id`` — present (non-null) only when ``is_master``.
    - ``landing_path`` — Mini App landing destination per ADR-0008.

    Cross-tenant safety: the auth decorator resolves the BotUser within
    the bot's configured tenant; the resolver only looks at that tenant.
    A forged init-data for a user in another tenant cannot reach a
    different tenant's role rows.
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = bot_user.tenant

    role_ctx = resolve_role(bot_user)

    # Name fallback chain — never return an empty string for ``name``,
    # the frontend renders it directly without a null-check today. Order:
    # client-typed name (most personal) → channel display name → a
    # last-resort placeholder built from the channel id so the value
    # remains stable per user.
    name = bot_user.client_name or bot_user.display_name or f"@{bot_user.channel_user_id}"

    return JsonResponse(
        {
            "user": {
                "id": str(bot_user.id),
                "name": name,
                "phone_masked": _mask_phone(bot_user.phone),
            },
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.name,
                "slug": tenant.slug,
            },
            "role": role_ctx.primary_role,
            "capabilities": sorted(role_ctx.capabilities),
            "is_customer": role_ctx.is_customer,
            "is_master": role_ctx.is_master,
            "is_receptionist": role_ctx.is_receptionist,
            "is_admin": role_ctx.is_admin,
            "is_owner": role_ctx.is_owner,
            "master_id": str(role_ctx.master_id) if role_ctx.master_id else None,
            "landing_path": role_ctx.landing_path,
        }
    )


__all__ = ["me_view"]
