"""Issued access codes in the admin Mini App: list, cancel, resend (DRF-2275).

The owner's decision CD §72 p.15–16 (21.09): the admin Mini App is the
salon owner's only workplace. Codes were issued there (``staff/invite/``)
and then vanished from sight: which are still waiting, which were taken,
and cancelling one before it expired needed the operator's Django admin.

### Who

Owner AND admin — whoever issues codes manages them (main window, 22.09).
The same rule draws the one line inside it: only the owner issues an owner
code (``views_staff_invite``), so only the owner cancels or re-issues one.
An admin sees owner codes in the list — they are part of «what is out
there» — and gets 403 on acting on them.

Master invite LINKS (``CatalogMaster.invite_token``) are a separate sheet;
this module is ``StaffInvite`` codes only.

### Status

One word per code, in this order, because the fields can overlap:

* ``accepted`` — ``used_at``; a used code cannot be cancelled (the access
  it gave lives in ``TenantStaff`` / the master link — revoke that);
* ``cancelled`` — ``revoked_at``;
* ``expired`` — ``expires_at`` passed;
* ``pending`` — none of the above.

### Resend

Only the hash is stored, so an issued code cannot be shown again. «Отправить
заново» issues a NEW code with the same role, note and master card, returns
it once — as ``staff/invite/`` does — and cancels the old one if it was
still pending, in one transaction: there is never a moment with two live
codes for one invitation. An expired or cancelled code may be re-issued
too (that is the usual reason to press the button); an accepted one may not.

Once only: a second resend of the same code answers 409
``invite_already_resent`` (the issue record carries ``resent_from``; the old
row is locked while asking). The new code is in the list — to replace it,
resend or cancel THAT one.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.views_staff_invite import _code_start_link
from apps.audit.models import AuditLog
from apps.audit.services import write_audit
from apps.events.vocabulary import STAFF_INVITE_ISSUED
from apps.identity.services.staff_invites import (
    InviteAlreadyUsed,
    issue_staff_invite,
    revoke_staff_invite,
)
from apps.tenancy.models import StaffInvite

logger = logging.getLogger(__name__)

#: The list is a screen, not an export. Newest first; the cap is said.
MAX_INVITES = 100


class _AlreadyResent(Exception):
    """This invitation already has a re-issued code — rolls the transaction back."""


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def invite_status(invite: StaffInvite, now: Any = None) -> str:
    """``accepted`` | ``cancelled`` | ``expired`` | ``pending`` — see the module doc."""

    if invite.used_at is not None:
        return "accepted"
    if invite.revoked_at is not None:
        return "cancelled"
    if invite.expires_at <= (now or timezone.now()):
        return "expired"
    return "pending"


def _find(request: HttpRequest, invite_id: str) -> StaffInvite | JsonResponse:
    """This salon's code, or the refusal to return. Owner codes are the owner's."""

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx = request.role_context  # type: ignore[attr-defined]
    try:
        invite = (
            StaffInvite.all_tenants.select_related("catalog_master")
            .filter(pk=invite_id, tenant_id=tenant.id)
            .first()
        )
    except (ValidationError, ValueError):
        return _error("not_found", "no such invite in this salon", 404)
    if invite is None:
        return _error("not_found", "no such invite in this salon", 404)
    if invite.role == StaffInvite.Role.OWNER and not role_ctx.is_owner:
        return _error("forbidden", "only the salon owner manages an owner invite", 403)
    return invite


def _actor_label(request: HttpRequest) -> str:
    return f"bot_user:{request.bot_user.pk}"  # type: ignore[attr-defined]


@require_http_methods(["GET"])
@require_admin_role
def staff_invites_list(request: HttpRequest) -> HttpResponse:
    """Every access code issued here, newest first.

    Returns 200 with ``{items: [{id, role, status, note, master_name,
    created_at, expires_at, used_at, revoked_at}], total_count, truncated}``.
    Never the code: only its hash exists.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    qs = StaffInvite.all_tenants.filter(tenant_id=tenant.id)
    total = qs.count()
    now = timezone.now()
    items = []
    for inv in qs.select_related("catalog_master").order_by("-created_at")[:MAX_INVITES]:
        items.append(
            {
                "id": str(inv.id),
                "role": inv.role,
                "status": invite_status(inv, now),
                "note": inv.note,
                "master_name": inv.catalog_master.name if inv.catalog_master else None,
                "created_at": inv.created_at.isoformat(),
                "expires_at": inv.expires_at.isoformat(),
                "used_at": inv.used_at.isoformat() if inv.used_at else None,
                "revoked_at": inv.revoked_at.isoformat() if inv.revoked_at else None,
            }
        )
    return JsonResponse({"items": items, "total_count": total, "truncated": total > len(items)})


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def staff_invite_cancel(request: HttpRequest, invite_id: str) -> HttpResponse:
    """Cancel a code before it is used. Repeat — 200 ``changed: false``."""

    found = _find(request, invite_id)
    if isinstance(found, JsonResponse):
        return found
    try:
        result = revoke_staff_invite(
            found,
            surface="salon_miniapp",
            actor_label=_actor_label(request),
            actor_id=request.bot_user.pk,  # type: ignore[attr-defined]
            reason="отменено в Admin Mini App",
        )
    except InviteAlreadyUsed as exc:
        return _error(exc.slug, str(exc), 409)
    return JsonResponse({"changed": result.changed, "status": "cancelled"}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def staff_invite_resend(request: HttpRequest, invite_id: str) -> HttpResponse:
    """Issue a fresh code for the same invitation; the old one stops working.

    Returns 200 with the ``staff/invite/`` shape: ``{invite_id, role, code,
    expires_at, code_is_shown_once, invite_link, resent_from}``.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]
    role_ctx = request.role_context  # type: ignore[attr-defined]

    found = _find(request, invite_id)
    if isinstance(found, JsonResponse):
        return found
    old = found
    status = invite_status(old)
    if status == "accepted":
        return _error(
            InviteAlreadyUsed.slug,
            "this code was already used — the person has the access; nothing to resend",
            409,
        )
    master = old.catalog_master
    if old.role == StaffInvite.Role.MASTER and (master is None or master.archived_at is not None):
        return _error("invite_master_missing", "the master card is gone or archived", 409)

    try:
        with transaction.atomic():
            # Serialise resends of ONE invitation on its row, then ask whether
            # it was resent already. Without this, «Повторить» after a lost
            # response (or two taps on two phones) issued a second live code,
            # the first of which nobody ever saw. Holds for expired and
            # cancelled codes too, which the revoke below never locks.
            locked = StaffInvite.all_tenants.select_for_update().get(pk=old.pk)
            if AuditLog.all_tenants.filter(
                tenant_id=tenant.id,
                action=STAFF_INVITE_ISSUED,
                payload__resent_from=str(old.id),
            ).exists():
                raise _AlreadyResent
            status = invite_status(locked)
            if status == "accepted":
                raise InviteAlreadyUsed("the code was used while it was being resent")
            if status == "pending":
                revoke_staff_invite(
                    old,
                    surface="salon_miniapp",
                    actor_label=_actor_label(request),
                    actor_id=actor.pk,
                    reason="перевыпущено в Admin Mini App",
                )
            invite, code = issue_staff_invite(
                tenant=tenant,
                role=old.role,
                catalog_master=master,
                created_by=actor,
                note=old.note,
            )
            write_audit(
                STAFF_INVITE_ISSUED,
                target="tenancy.StaffInvite",
                target_id=invite.id,
                # No code, no hash — as in staff/invite/.
                payload={
                    "role": invite.role,
                    "master_id": str(master.id) if master else None,
                    "actor_id": str(actor.id),
                    "actor_role": role_ctx.primary_role,
                    "expires_at": invite.expires_at.isoformat(),
                    "resent_from": str(old.id),
                },
                actor_id=actor.id,
            )
    except InviteAlreadyUsed as exc:
        # Used between the read and the lock — say so, issue nothing.
        return _error(exc.slug, str(exc), 409)
    except _AlreadyResent:
        return _error(
            "invite_already_resent",
            "a new code for this invitation was already issued — it is in the list",
            409,
        )

    logger.info(
        "admin_api.staff_invite.resent tenant=%s role=%s old=%s new=%s by=%s",
        tenant.slug,
        invite.role,
        old.id,
        invite.id,
        actor.id,
    )
    return JsonResponse(
        {
            "invite_id": str(invite.id),
            "role": invite.role,
            "code": code,
            "expires_at": invite.expires_at.isoformat(),
            "code_is_shown_once": True,
            "invite_link": _code_start_link(tenant, code),
            "resent_from": str(old.id),
        },
        status=200,
    )
