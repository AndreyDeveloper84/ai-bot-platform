"""Issue staff invite codes from the admin Mini App (DRF-1061, block 2.4).

### Why this exists

Until now the only way to make a salon employee was a management command
on the pilot host — i.e. a person with SSH. That does not scale and it puts
the platform team on the critical path of every hiring decision. The owner
should be able to add an administrator without asking anyone.

### Why a separate endpoint and not `masters/invite/`

``master_invite_create`` refused non-master roles with «admin/receptionist
invites land in a separate ticket (TenantStaff model)». This is that
ticket, and the separation turns out to be right rather than incidental:
the two flows write different things and mean different things.

* ``masters/invite/`` **creates a catalog master** — a person who does not
  exist in the salon yet, with services, a card, a profile.
* This endpoint **grants access to a person** — a ``TenantStaff`` row, or a
  link from an existing catalog master to a MAX account.

Folding them together would have meant one endpoint whose required fields
depend on a role flag, and whose "create" is sometimes a create and
sometimes a link. The frontend never sent a role anyway (checked
``admin-api.ts``), so nothing is broken by leaving that contract alone.

### Who may issue what

``require_admin_role`` already limits callers to owner and admin. On top:

* **owner** codes may only be issued by an owner. A tenant has exactly one
  active owner (partial unique index); letting an admin mint owner access
  would be a privilege escalation with a database constraint as the only
  backstop.
* **master** codes bind to an EXISTING catalog row and never create one —
  see ``apps.identity.services.staff_invites``.

### The code is shown once

The response carries the plaintext code because only its hash is stored and
there is no way to recover it later. The caller shows it to the person and
forgets it. That is stated in the response too, so a UI written against
this cannot quietly assume it can re-read the code.
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
from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster
from apps.events.vocabulary import STAFF_INVITE_ISSUED
from apps.identity.services.staff_invites import issue_staff_invite
from apps.tenancy.models import StaffInvite

logger = logging.getLogger(__name__)

MAX_NOTE_LEN = 200

#: Roles this endpoint can grant. Mirrors StaffInvite.Role.
ALLOWED_ROLES = {r[0] for r in StaffInvite.Role.choices}


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def staff_invite_create(request: HttpRequest) -> HttpResponse:
    """Create a one-shot invite code and return it once.

    Body:
      ``role`` — owner | admin | receptionist | master (required)
      ``master_id`` — required for role=master: the EXISTING catalog row
      ``note`` — optional free-form label for the issuer's own records

    Returns 201 with ``{code, role, expires_at, invite_id}``.
    """

    role_ctx = request.role_context  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]

    try:
        body: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    role = str(body.get("role") or "").strip()
    if role not in ALLOWED_ROLES:
        return _error(
            "bad_request",
            f"role must be one of {sorted(ALLOWED_ROLES)}",
            400,
        )

    # Only an owner may mint owner access. A tenant has one active owner
    # (partial unique index), and letting an admin issue owner codes would
    # make that constraint the only thing standing between an admin and
    # taking over the salon.
    if role == StaffInvite.Role.OWNER and not role_ctx.is_owner:
        return _error(
            "forbidden",
            "only the salon owner can issue an owner invite",
            403,
        )

    note = str(body.get("note") or "").strip()[:MAX_NOTE_LEN]

    catalog_master = None
    if role == StaffInvite.Role.MASTER:
        master_id = str(body.get("master_id") or "").strip()
        if not master_id:
            return _error(
                "bad_request",
                "role='master' requires master_id — the existing catalog row to link",
                400,
            )
        # `.objects` is tenant-scoped by require_admin_role's tenant_scope,
        # so another salon's master cannot be named here even deliberately.
        try:
            catalog_master = CatalogMaster.objects.filter(
                pk=master_id, archived_at__isnull=True
            ).first()
        except (ValidationError, ValueError):
            return _error("bad_request", "master_id is not a valid id", 400)
        if catalog_master is None:
            return _error("not_found", "no such active master in this salon", 404)

    invite, code = issue_staff_invite(
        tenant=tenant,
        role=role,
        catalog_master=catalog_master,
        created_by=actor,
        note=note,
    )

    write_audit(
        STAFF_INVITE_ISSUED,
        target="tenancy.StaffInvite",
        target_id=invite.id,
        # Deliberately no code, no hash: an audit row is a place people
        # look, and a credential that grants staff access does not belong
        # in one.
        payload={
            "role": role,
            "master_id": str(catalog_master.id) if catalog_master else None,
            "actor_id": str(actor.id),
            "actor_role": role_ctx.primary_role,
            "expires_at": invite.expires_at.isoformat(),
        },
        actor_id=actor.id,
    )
    logger.info(
        "admin_api.staff_invite.issued tenant=%s role=%s invite=%s by=%s",
        tenant.slug,
        role,
        invite.id,
        actor.id,
    )

    return JsonResponse(
        {
            "invite_id": str(invite.id),
            "role": role,
            "code": code,
            "expires_at": invite.expires_at.isoformat(),
            # Said in the payload so a UI cannot assume it can re-read it.
            "code_is_shown_once": True,
        },
        status=201,
    )
