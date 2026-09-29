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

### ``invite_link`` — the code without dictating it (DRF-1505)

Four characters read aloud over a phone is how the pilot was expected to
onboard its staff. ``invite_link`` is the same credential in a form that
can be pasted: ``https://max.ru/<salon bot>?start=inv_<code>``. MAX
delivers ``?start=`` as ``bot_started.payload``, the parser folds it into
the synthetic text «/start inv_<code>», and
:func:`apps.channels.max.salon_handler._extract_code` already reads
exactly that shape — the redemption path is the one that was there
before, reached without typing.

It is the same builder the master invitation uses
(:mod:`apps.channels.max.start_links`), and it names the salon bot for
the same reason: only ``ingress:max_salon`` reaches the handler that
redeems codes.

**The link is the code.** Anyone who opens it redeems it, exactly as
anyone who is told the four characters can. It is shown once, beside the
code, under the same warning — never mailed, never logged, never put in
an audit row.
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


def _code_start_link(tenant, code: str) -> str:
    """``https://max.ru/<salon bot>?start=inv_<code>``, or ``""``.

    ``DEEPLINK_PREFIX`` is imported from the handler that reads it rather
    than restated here. The prefix is a contract between two modules in
    the same repository, and a second spelling of it is free to drift out
    from under the one that matters — the reader's link would keep
    looking right and stop being redeemed.

    The import is lazy for cost, not for a cycle. ``salon_handler`` pulls
    in the parser, outbound, the staff menu and the identity services at
    module scope, and nothing here needs any of that until somebody
    actually issues a code — while every request to this module's other
    views would pay for it. (An earlier version of this comment claimed a
    cycle: ``salon_handler`` does import ``views_invite``, but lazily,
    inside ``_invite_prefix``, so a module-scope import here would close
    nothing.)

    ``code`` arrives formatted (``AYLA-7K3M``); the payload carries the
    flat form (``AYLA7K3M``). ``normalize_code`` folds both to the same
    four characters, so either would redeem — the flat one is used
    because ``issue_staff_invite`` the management command has been
    printing that shape since DRF-1061, and one credential written two
    ways by two producers is the kind of difference that survives right
    up until somebody compares a link to a code and concludes one of
    them is wrong.

    Empty string when there is no salon bot with a Mini App name: see
    :mod:`apps.channels.max.start_links` for why a missing link beats one
    that opens an apology.

    Nothing raises out of here, and that is deliberate rather than
    defensive habit. This runs AFTER ``issue_staff_invite`` has committed
    the row, and the plaintext code exists in exactly one place: the
    response being built. A malformed registry entry escaping as a 500
    would destroy a live, unrecoverable credential — the issuer would see
    a failure, the invite would exist, and the only exit would be waiting
    out its 7-day TTL. ``views_invite`` carries the same reasoning at its
    own post-commit dispatch. A missing link costs a paste; a lost code
    costs the person's access.
    """

    from apps.channels.max.salon_handler import DEEPLINK_PREFIX
    from apps.channels.max.start_links import salon_start_link

    try:
        return salon_start_link(tenant, f"{DEEPLINK_PREFIX}{code.replace('-', '')}")
    except Exception:  # noqa: BLE001 — see the docstring: the code must survive
        logger.exception(
            "admin_api.staff_invite.link_failed tenant=%s — the code was issued and "
            "is returned without a shareable link.",
            getattr(tenant, "slug", "?"),
        )
        return ""


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

    Returns 201 with ``{code, role, expires_at, invite_id,
    code_is_shown_once, invite_link}``.
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
            "invite_link": _code_start_link(tenant, code),
        },
        status=201,
    )
