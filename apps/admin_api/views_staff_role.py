"""Change a person's role, or give back a revoked one, from the admin Mini App.

DRF-2273 (``staff/role/``) and DRF-2274 (``staff/restore/``).

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

### Restoring is not granting (DRF-2274)

``staff/restore/`` gives back the role the person HELD and a REVOKE took —
never a new one; that is what an invite code is for. Owner only, although
an admin may revoke: restoring is granting a role, and granting roles is
the owner's (accepted by the main window 22.09). What counts as «held and
revoked» lives in ``services/staff_restore.py``, shared with the roster so
the button and the endpoint cannot disagree. In short: a staff role must be
in the latest ``staff.access_revoked`` row for the person while they hold
no live staff role (a role change closes rows too, and is not a revoke);
the master card's holder comes from the latest revoke row naming the card,
unless the card was linked to anybody since.

Restoring what is already back answers 200 ``changed: false`` — as a
second revoke does.

The journal outlives the person on purpose: «забудь всё» does not touch
the audit log, and deleting the account (``account_reset``) keeps
``AuditLog.target_id`` by design (``KEPT_BY_DESIGN``). So the row can name
somebody who no longer exists — that answers 409 ``person_gone``, never
``role_not_previously_held``: the role was held, there is nobody to give
it back to.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.staff_restore import (
    latest_revoke_names,
    restorable_masters,
    restorable_staff_roles,
    revokes,
)
from apps.catalog.models import CatalogMaster
from apps.events.vocabulary import STAFF_ROLE_GRANTED
from apps.identity.models import BotUser
from apps.identity.services.salon_admin_link import CatalogAdminLinkRefused
from apps.identity.services.specialist_onboarding import OnboardingActor
from apps.identity.services.staff_invites import InviteError, link_master_to_person
from apps.identity.services.staff_roles import (
    GRANTABLE_ROLES,
    PersonInOtherTenant,
    StaffRoleError,
    change_staff_role,
    grant_role_by_operator,
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


#: Roles ``staff/restore/`` can give back. ``owner`` is refused before this.
RESTORABLE_ROLES: tuple[str, ...] = ("admin", "receptionist", "master")


def _not_held(role: str) -> JsonResponse:
    return _error(
        "role_not_previously_held",
        f"a revoke did not take the {role} role from this person here, "
        "or something has replaced it since",
        409,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def staff_restore(request: HttpRequest) -> HttpResponse:
    """Give back a role a revoke took from this person here.

    Body:
      ``role`` — ``admin`` | ``receptionist`` | ``master`` (required)
      staff role: ``bot_user_id`` (required), no ``master_id``
      master:     ``master_id`` (required), ``bot_user_id`` (optional check)

    Returns 200 with ``{changed, role}``.
    """

    role_ctx = request.role_context  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error("forbidden", "only the salon owner can give access back", 403)

    try:
        body: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    role = str(body.get("role") or "").strip()
    master_id = str(body.get("master_id") or "").strip()
    bot_user_id = str(body.get("bot_user_id") or "").strip()
    if role == TenantStaff.Role.OWNER:
        return _error(
            "forbidden",
            "ownership is handed over separately, not by restoring a role",
            403,
        )
    if role not in RESTORABLE_ROLES:
        return _error("unknown_role", f"role must be one of {list(RESTORABLE_ROLES)}", 400)

    if role == "master":
        if not master_id:
            return _error("bad_request", "restoring a master needs master_id", 400)
        return _restore_master(request, master_id=master_id, bot_user_id=bot_user_id)
    if master_id:
        return _error("bad_request", "a staff role is restored by bot_user_id only", 400)
    if not bot_user_id:
        return _error("bad_request", "bot_user_id is required", 400)

    try:
        person = BotUser.objects.filter(pk=bot_user_id).first()
    except (ValidationError, ValueError):
        return _error("bad_request", "bot_user_id is not a valid id", 400)
    if person is None:
        return _error("not_found", "no such person in this salon", 404)
    if person.id == actor.id:
        return _error("forbidden", "you cannot restore your own access", 403)

    live = set(
        TenantStaff.all_tenants.filter(
            tenant_id=tenant.id, bot_user=person, deactivated_at__isnull=True
        ).values_list("role", flat=True)
    )
    if role in live:
        # Already back — calm, as a second revoke is. Only if a revoke took
        # it: a role that was never taken is not «restored».
        if latest_revoke_names(tenant.id, person.id, role):
            return JsonResponse({"changed": False, "role": role}, status=200)
        return _not_held(role)
    if live:
        return _error(
            "holds_another_role",
            "the person holds another role here now; change it instead",
            409,
        )
    if role not in restorable_staff_roles(tenant.id, [person.id]).get(person.id, set()):
        return _not_held(role)

    try:
        result = grant_role_by_operator(
            tenant=tenant, bot_user=person, role=role, actor=salon_actor(request)
        )
    except PersonInOtherTenant:
        return _error("not_found", "no such person in this salon", 404)
    except CatalogAdminLinkRefused as exc:
        return _error(exc.slug, exc.reason, 409, details={"hint": exc.hint})
    except (StaffRoleError, InviteError) as exc:
        return _error(exc.slug, str(exc), 409)

    changed = not result.already_had_role
    logger.info(
        "admin_api.staff_restore tenant=%s person=%s by=%s role=%s changed=%s",
        tenant.slug,
        person.id,
        actor.id,
        role,
        changed,
    )
    return JsonResponse({"changed": changed, "role": role}, status=200)


def _restore_master(request: HttpRequest, *, master_id: str, bot_user_id: str) -> HttpResponse:
    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]

    try:
        master = CatalogMaster.objects.filter(pk=master_id).first()
    except (ValidationError, ValueError):
        return _error("bad_request", "master_id is not a valid id", 400)
    if master is None:
        return _error("not_found", "no such master in this salon", 404)
    named = None
    if bot_user_id:
        try:
            named = BotUser.objects.filter(pk=bot_user_id).first()
        except (ValidationError, ValueError):
            return _error("bad_request", "bot_user_id is not a valid id", 400)

    # Already back: the card is linked to the person the latest revoke took
    # it from. Asked before «restorable», which is only about unlinked cards.
    if master.linked_bot_user_id is not None:
        last = (
            revokes(tenant.id)
            .filter(payload__master_id=str(master.id))
            .order_by("-created_at")
            .first()
        )
        if (
            last is not None
            and last.target_id == master.linked_bot_user_id
            and (named is None or named.id == last.target_id)
        ):
            return JsonResponse({"changed": False, "role": "master"}, status=200)
        return _error("wrong_recipient", "the card is linked to somebody else now", 409)
    if master.archived_at is not None:
        return _error("invite_master_missing", "the card is archived", 409)

    person_id = restorable_masters(tenant.id, [master]).get(str(master.id))
    if person_id is None:
        return _not_held("master")
    if bot_user_id and (named is None or named.id != person_id):
        return _not_held("master")

    # Tenant-scoped manager: somebody from another salon is «gone» here too.
    person = BotUser.objects.filter(pk=person_id).first()
    if person is None or getattr(person, "tenant_id", None) != tenant.id:
        return _error(
            "person_gone",
            "the person who held this card no longer has an account here",
            409,
        )
    if person.id == actor.id:
        return _error("forbidden", "you cannot restore your own access", 403)

    from apps.audit.services import write_audit
    from apps.tenancy.context import tenant_scope

    try:
        # The link and its record commit together — as revoke does. A link
        # without its audit row could never be recorded later: a retry
        # would answer «already back».
        with transaction.atomic():
            link_master_to_person(master_id=master.id, tenant_id=tenant.id, bot_user=person)
            with tenant_scope(tenant):
                write_audit(
                    STAFF_ROLE_GRANTED,
                    target="identity.BotUser",
                    target_id=person.pk,
                    payload={
                        "surface": "salon_miniapp",
                        "actor_label": f"bot_user:{actor.pk}",
                        "person_id": str(person.pk),
                        "role": "master",
                        "master_id": str(master.id),
                        "restored": True,
                    },
                    actor_id=actor.pk,
                )
    except InviteError as exc:
        return _error(exc.slug, str(exc), 409)

    logger.info(
        "admin_api.staff_restore tenant=%s person=%s by=%s role=master master=%s",
        tenant.slug,
        person.id,
        actor.id,
        master.id,
    )
    return JsonResponse({"changed": True, "role": "master"}, status=200)
