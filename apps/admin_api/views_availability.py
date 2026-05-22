"""Admin REST views — M3-admin approve/reject availability requests (Bundle B).

Three endpoints under ``/api/v1/admin/availability-requests/``:

* ``GET    /``                 — paginated list of requests for review.
* ``POST   /<request_id>/approve/`` — approve a PENDING request →
                                  materialise into ScheduleException
                                  day_off rows + DM the master.
* ``POST   /<request_id>/reject/``  — reject a PENDING request with a
                                  non-blank ≤500-char reason + DM the
                                  master.

All views gated via :func:`apps.admin_api.auth.require_admin_role`
(Owner OR Admin). Master / receptionist / customer roles return 403.

The actual business logic lives in
:mod:`apps.admin_api.services.availability` — these views are thin
HTTP shells: parse params/JSON body, call the service, map dataclass
results / exceptions to JSON envelopes.

See ``apps.admin_api.services.availability`` for atomicity guarantees,
materialisation rules, and idempotency semantics.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import RoleContext, require_admin_role
from apps.admin_api.services.availability import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    AvailabilityDecisionError,
    approve_availability_request,
    list_pending_for_admin,
    reject_availability_request,
)
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


# --- helpers --------------------------------------------------------------


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _parse_json_body(request: HttpRequest) -> dict[str, Any] | JsonResponse:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(data, dict):
        return _error("bad_request", "JSON body must be an object", 400)
    return data


def _resolve_actor_role(role_ctx: RoleContext) -> str:
    """Pick a string for the audit payload's ``actor_role`` field.

    Owner takes precedence over admin (same precedence as
    ``role_ctx.primary_role`` but resilient to future role additions
    that might shift the primary).
    """

    if role_ctx.is_owner:
        return "owner"
    if role_ctx.is_admin:
        return "admin"
    return role_ctx.primary_role or "unknown"


def _resolve_django_user(bot_user: BotUser) -> Any:
    """Find the Django User row backing this BotUser, if any.

    BotUser ↔ User linkage isn't a model-level FK in this codebase
    (admin Mini App auth threads through BotUser only). The Phase 2
    bridge between BotUser and AUTH_USER_MODEL will land alongside
    proper staff-account introspection; until then we return ``None``.

    The authoritative provenance handle for «who decided this request»
    today lives in :attr:`ScheduleChangeRequest.resolved_by_bot_user_id`
    (a direct UUIDField, set by the service layer from the
    ``actor_bot_user_id`` kwarg). The audit row also carries the BotUser
    id in its payload. PR #521 adversarial blocker #3 added the
    BotUser-id column so the list endpoint can render a non-empty
    ``decided_by_name`` even with ``resolved_by`` still NULL.

    Returns: a Django User instance or ``None``.
    """

    return None


def _serialise_request(req: Any, materialised_dates: list | None = None) -> dict[str, Any]:
    """Marshal a ScheduleChangeRequest row to the JSON response shape."""

    return {
        "request_id": str(req.id),
        "master_id": str(req.master_id) if req.master_id else None,
        "status": req.status,
        "requested_start": (req.requested_start.isoformat() if req.requested_start else None),
        "requested_end": (req.requested_end.isoformat() if req.requested_end else None),
        "reason_class": req.reason_class or "",
        "reason_text": req.reason_text or "",
        "created_at": req.created_at.isoformat(),
        "decided_at": req.resolved_at.isoformat() if req.resolved_at else None,
        "resolution_note": req.resolution_note or "",
        "materialised_dates": (
            [d.isoformat() for d in materialised_dates] if materialised_dates else []
        ),
    }


def _parse_uuid_or_404(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError):
        return None


# --- GET /availability-requests/ ------------------------------------------


@require_http_methods(["GET"])
@require_admin_role
def availability_requests_list(request: HttpRequest) -> HttpResponse:
    """Paginated list of availability requests scoped to the tenant.

    Query parameters (all optional):

    * ``status`` — ``pending`` (default) / ``decided`` / ``all``.
    * ``master_id`` — filter to a single master's requests.
    * ``cursor`` — opaque cursor from previous page.
    * ``limit`` — int, 1..50. Default 25.

    Response shape::

        {
          "items": [{
            "request_id", "master_id", "master_name",
            "requested_start", "requested_end",
            "reason_class", "reason_text", "status",
            "created_at", "decided_at", "decided_by_name",
            "resolution_note"
          }, ...],
          "next_cursor": str | None
        }
    """

    tenant = request.tenant  # type: ignore[attr-defined]

    status = (request.GET.get("status") or "pending").strip().lower()
    master_id_raw = (request.GET.get("master_id") or "").strip()
    cursor = (request.GET.get("cursor") or "").strip() or None
    raw_limit = (request.GET.get("limit") or "").strip()

    if raw_limit:
        try:
            limit = int(raw_limit)
        except ValueError:
            return _error("bad_request", "limit must be an integer", 400)
    else:
        limit = DEFAULT_LIST_LIMIT
    if limit < 1:
        return _error("bad_request", "limit must be positive", 400)
    limit = min(limit, MAX_LIST_LIMIT)

    master_id: uuid.UUID | None = None
    if master_id_raw:
        master_id = _parse_uuid_or_404(master_id_raw)
        if master_id is None:
            return _error("bad_request", "master_id must be a UUID", 400)

    try:
        result = list_pending_for_admin(
            tenant_id=tenant.id,
            status=status,
            master_id=master_id,
            cursor=cursor,
            limit=limit,
        )
    except AvailabilityDecisionError as exc:
        return _error(exc.slug, exc.detail, exc.status)

    return JsonResponse(result)


# --- POST /availability-requests/<id>/approve/ ----------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def availability_request_approve(request: HttpRequest, request_id: str) -> HttpResponse:
    """Approve a PENDING availability request.

    Body: empty / ``{}`` accepted.

    Response 200 on success::

        {"request": {...}}

    Status codes:
      * 200 — approved + materialised + DM scheduled.
      * 400 — bad request id / legacy row missing typed window.
      * 404 — request not found / cross-tenant.
      * 409 — ``already_decided`` (status != PENDING) or
              ``overlap_conflict`` (a conflicting non-day_off
              exception exists on a covered date).
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    parsed_id = _parse_uuid_or_404(request_id)
    if parsed_id is None:
        return _error("not_found", "availability request not found", 404)

    # Body MAY be empty; only validate if present (defence against odd
    # clients sending non-object JSON).
    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    try:
        result = approve_availability_request(
            request_id=parsed_id,
            tenant_id=tenant.id,
            actor=_resolve_django_user(bot_user),
            actor_bot_user_id=bot_user.id,
            actor_role=_resolve_actor_role(role_ctx),
        )
    except AvailabilityDecisionError as exc:
        return _error(exc.slug, exc.detail, exc.status)

    return JsonResponse({"request": _serialise_request(result.request, result.materialised_dates)})


# --- POST /availability-requests/<id>/reject/ -----------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def availability_request_reject(request: HttpRequest, request_id: str) -> HttpResponse:
    """Reject a PENDING availability request with a free-form reason.

    Body::

        {"rejection_reason": "<non-blank ≤500-char string>"}

    Status codes:
      * 200 — rejected + DM scheduled.
      * 400 — missing/invalid body, blank or oversize ``rejection_reason``.
      * 404 — request not found / cross-tenant.
      * 409 — ``already_decided``.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    parsed_id = _parse_uuid_or_404(request_id)
    if parsed_id is None:
        return _error("not_found", "availability request not found", 404)

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    rejection_reason = body.get("rejection_reason")
    if rejection_reason is None:
        return _error("bad_request", "rejection_reason is required", 400)
    if not isinstance(rejection_reason, str):
        return _error("bad_request", "rejection_reason must be a string", 400)

    try:
        result = reject_availability_request(
            request_id=parsed_id,
            tenant_id=tenant.id,
            actor=_resolve_django_user(bot_user),
            actor_bot_user_id=bot_user.id,
            actor_role=_resolve_actor_role(role_ctx),
            rejection_reason=rejection_reason,
        )
    except AvailabilityDecisionError as exc:
        return _error(exc.slug, exc.detail, exc.status)

    return JsonResponse({"request": _serialise_request(result.request)})


__all__ = [
    "availability_request_approve",
    "availability_request_reject",
    "availability_requests_list",
]
