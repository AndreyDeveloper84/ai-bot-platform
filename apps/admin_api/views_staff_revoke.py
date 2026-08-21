"""Revoke a person's salon access from the admin Mini App (DRF-1227).

The other half of ``views_staff_invite``. Access was grantable and not
removable: ``TenantStaff.deactivated_at`` had no writer outside tests, and
``CatalogMaster.linked_bot_user`` was only ever set. Every code ever issued
stayed live for the lifetime of the tenant.

### Naming the person

Two ways in, exactly one per call:

* ``master_id`` — the catalog row, which is what the team screen already
  has in hand. Resolves to whoever is linked to it.
* ``bot_user_id`` — the person directly, for staff who are not masters.

Two identifiers rather than one because the admin surface knows masters by
their catalog id and nothing else; requiring a ``bot_user_id`` would mean
the UI could not call this endpoint at all today.

### Two refusals worth explaining

* **The owner** — refused in the service, because a tenant has one active
  owner and only an owner can issue an owner code. Revoking it leaves a
  salon nobody can ever re-enter.
* **Yourself** — refused here. An admin who revokes their own access cannot
  restore it; they would need the owner to issue a fresh code. The button
  that does that by accident is worth not having.

Revoking twice answers 200 with ``changed: false``. A person who is not
sure the first attempt worked will try again, and an error would tell them
it did not.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.staff_revoke import RevokeError, revoke_staff_access

logger = logging.getLogger(__name__)

MAX_REASON_LEN = 200


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def staff_revoke(request: HttpRequest) -> HttpResponse:
    """Take away every salon-side capability one person holds here.

    Body:
      ``master_id`` OR ``bot_user_id`` — exactly one (required)
      ``reason`` — optional free-form note, recorded in the audit row

    Returns 200 with ``{changed, roles_revoked, master_unlinked}``.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]

    try:
        body: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    master_id = str(body.get("master_id") or "").strip()
    bot_user_id = str(body.get("bot_user_id") or "").strip()
    if bool(master_id) == bool(bot_user_id):
        return _error(
            "bad_request",
            "name the person with exactly one of master_id / bot_user_id",
            400,
        )

    person: BotUser | None = None
    if master_id:
        # `.objects` is tenant-scoped by require_admin_role's tenant_scope,
        # so another salon's master cannot be named here even deliberately.
        try:
            master = CatalogMaster.objects.filter(pk=master_id).first()
        except (ValidationError, ValueError):
            return _error("bad_request", "master_id is not a valid id", 400)
        if master is None:
            return _error("not_found", "no such master in this salon", 404)
        person = master.linked_bot_user
        if person is None:
            # Nothing to revoke, and saying so plainly beats a 404 — the
            # master exists, they just have no account attached.
            return JsonResponse(
                {"changed": False, "roles_revoked": [], "master_unlinked": False},
                status=200,
            )
    else:
        try:
            person = BotUser.objects.filter(pk=bot_user_id).first()
        except (ValidationError, ValueError):
            return _error("bad_request", "bot_user_id is not a valid id", 400)
        if person is None:
            return _error("not_found", "no such person in this salon", 404)

    if person.id == actor.id:
        return _error(
            "forbidden",
            "you cannot revoke your own access — ask the salon owner",
            403,
        )

    reason = str(body.get("reason") or "").strip()[:MAX_REASON_LEN]

    try:
        result = revoke_staff_access(
            tenant=tenant,
            bot_user=person,
            actor=actor,
            reason=reason,
        )
    except RevokeError as exc:
        return _error(exc.slug, str(exc), 409)

    logger.info(
        "admin_api.staff_revoke tenant=%s person=%s by=%s changed=%s",
        tenant.slug,
        person.id,
        actor.id,
        result.changed,
    )
    return JsonResponse(
        {
            "changed": result.changed,
            "roles_revoked": list(result.roles_revoked),
            "master_unlinked": result.master_unlinked,
        },
        status=200,
    )
