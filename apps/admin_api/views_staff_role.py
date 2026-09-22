"""Change a person's role from the admin Mini App (DRF-2273).

The owner's decision CD §72 p.15–16 (21.09): the admin Mini App is the
salon owner's only workplace; the technical admins are for operators. The
role change already existed — ``identity.services.staff_roles.
change_staff_role``, reached until now only from the Django admin. This is
the salon-side door onto the same service, not a second implementation.

### Owner only

``require_admin_role`` admits owner and admin; this view narrows to owner.
The owner ruled that *changing* roles is owner-only (recorded in
``views_staff_roster``), and the roster this button sits on is owner-only
for the same reason.

### Three refusals made here, before the service

* **Yourself** — 403, as in ``staff/revoke/``. The only person who reaches
  this view is the salon's single active owner, so «your own role» and
  «the owner's role» are one case here; the service's own lock
  (``owner_role_locked``) stays as the rule for every other door.
* **Making somebody owner** — 403. Ownership moves by deactivate-then-
  invite (``OwnerAlreadyExists``), never by a role edit.
* **A role outside the vocabulary** — 400, not a service error: it is a
  malformed request, not a state conflict.

Everything else the service refuses — nothing to change, the catalog
declined the admin half (DRF-2085) — answers 409 with the service's slug.
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
from apps.identity.models import BotUser
from apps.identity.services.salon_admin_link import CatalogAdminLinkRefused
from apps.identity.services.specialist_onboarding import OnboardingActor
from apps.identity.services.staff_invites import InviteError
from apps.identity.services.staff_roles import (
    GRANTABLE_ROLES,
    PersonInOtherTenant,
    StaffRoleError,
    change_staff_role,
)
from apps.tenancy.models import TenantStaff

logger = logging.getLogger(__name__)


def _error(
    slug: str, detail: str, status: int, details: dict[str, Any] | None = None
) -> JsonResponse:
    body: dict[str, Any] = {"error": slug, "detail": detail}
    if details:
        body["details"] = details
    return JsonResponse(body, status=status)


def salon_actor(request: HttpRequest) -> OnboardingActor:
    """The salon owner acting from the Mini App — one shape for every door here."""

    actor = request.bot_user  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]
    return OnboardingActor(
        surface="salon_miniapp",
        audit_label=f"bot_user:{actor.pk}",
        cross_tenant=False,
        current_tenant_id=tenant.id,
        actor_id=actor.pk,
        capability="owner",
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def staff_role_change(request: HttpRequest) -> HttpResponse:
    """Replace every active role one person holds here with ``role``.

    Body:
      ``bot_user_id`` — the person (required)
      ``role`` — ``admin`` | ``receptionist`` (required)

    Returns 200 with ``{role, previous_roles}``.
    """

    role_ctx = request.role_context  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error("forbidden", "only the salon owner can change roles", 403)

    try:
        body: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    bot_user_id = str(body.get("bot_user_id") or "").strip()
    role = str(body.get("role") or "").strip()
    if not bot_user_id:
        return _error("bad_request", "bot_user_id is required", 400)
    if role == TenantStaff.Role.OWNER:
        return _error(
            "forbidden",
            "ownership is handed over separately, not by changing a role",
            403,
        )
    if role not in GRANTABLE_ROLES:
        return _error("unknown_role", f"role must be one of {list(GRANTABLE_ROLES)}", 400)

    # `.objects` is tenant-scoped by require_admin_role's tenant_scope, so a
    # person from another salon is simply not found.
    try:
        person = BotUser.objects.filter(pk=bot_user_id).first()
    except (ValidationError, ValueError):
        return _error("bad_request", "bot_user_id is not a valid id", 400)
    if person is None:
        return _error("not_found", "no such person in this salon", 404)

    if person.id == actor.id:
        return _error("forbidden", "you cannot change your own role", 403)

    try:
        change = change_staff_role(
            tenant=tenant, bot_user=person, role=role, actor=salon_actor(request)
        )
    except PersonInOtherTenant:
        return _error("not_found", "no such person in this salon", 404)
    except CatalogAdminLinkRefused as exc:
        # `details.hint` is the service's own «что сделать» — already Russian,
        # already the words the operator sees in the Django admin.
        return _error(exc.slug, exc.reason, 409, details={"hint": exc.hint})
    except (StaffRoleError, InviteError) as exc:
        return _error(exc.slug, str(exc), 409)

    logger.info(
        "admin_api.staff_role_change tenant=%s person=%s by=%s from=%s to=%s",
        tenant.slug,
        person.id,
        actor.id,
        ",".join(change.previous_roles),
        change.role,
    )
    return JsonResponse(
        {
            "role": change.role,
            "previous_roles": list(change.previous_roles),
        },
        status=200,
    )
