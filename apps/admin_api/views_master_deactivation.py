"""Admin REST views — MM5 master deactivation cascade (PR Tier1.1).

Three endpoints under ``/api/v1/admin/masters/<master_id>/``:

* ``POST /deactivation-preview/`` — read-only inventory + fallback
  ranking. Both Owner AND Admin may call (read-only).
* ``POST /deactivate/`` — atomic reassign + cancel + archive cascade.
  **Owner-only** per spec §10 + Q-MM4.
* ``POST /reactivate/`` — flip ``is_active=False → True``.
  **Owner-only** (deactivation symmetry).

All gating goes through :func:`apps.admin_api.auth.require_admin_role`
(which covers Owner OR Admin); the destructive endpoints add an extra
Owner-only check on top, returning 403 ``forbidden`` for Admin/lower.

The actual business logic lives in
:mod:`apps.admin_api.services.master_deactivation` — these views are a
thin HTTP shell: parse JSON body, call the service, map dataclasses /
exceptions to JSON envelopes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import RoleContext, require_admin_role
from apps.admin_api.services.master_deactivation import (
    BookingAction,
    DeactivationError,
    DeactivationPreview,
    DeactivationResult,
    FutureBookingPreview,
    ReactivationResult,
    execute_deactivation,
    preview_deactivation,
    reactivate_master,
)
from apps.admin_api.views import _get_master_or_404
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


def _preview_to_payload(preview: DeactivationPreview) -> dict[str, Any]:
    def _booking(p: FutureBookingPreview) -> dict[str, Any]:
        return {
            "booking_id": p.booking_id,
            "visit_at": p.visit_at.isoformat() if p.visit_at else None,
            "service_name": p.service_name,
            "service_id": p.service_id,
            "client_first_name": p.client_first_name,
            "client_last_initial": p.client_last_initial,
            "duration_min": p.duration_min,
            "fallback_masters": [
                {
                    "master_id": fb.master_id,
                    "name": fb.name,
                    "does_this_service": fb.does_this_service,
                    "is_free_at_slot": fb.is_free_at_slot,
                    "match_score": fb.match_score,
                }
                for fb in p.fallback_masters
            ],
        }

    return {
        "master": {
            "id": preview.master_id,
            "name": preview.master_name,
            "is_active": preview.is_active,
            "archived_at": preview.archived_at.isoformat() if preview.archived_at else None,
        },
        "future_bookings": [_booking(p) for p in preview.future_bookings],
        "summary": {
            "total_future_bookings": preview.total_future_bookings,
            "bookings_with_fallback": preview.bookings_with_fallback,
            "bookings_without_fallback": preview.bookings_without_fallback,
        },
    }


def _deact_result_to_payload(r: DeactivationResult) -> dict[str, Any]:
    return {
        "master_id": r.master_id,
        "is_active": r.is_active,
        "archived_at": r.archived_at.isoformat() if r.archived_at else None,
        "summary": {
            "reassigned_count": r.reassigned_count,
            "cancelled_count": r.cancelled_count,
            "customer_notifications_dispatched": r.customer_notifications_dispatched,
            "master_notifications_dispatched": r.master_notifications_dispatched,
        },
    }


def _reactivation_to_payload(r: ReactivationResult) -> dict[str, Any]:
    return {
        "master_id": r.master_id,
        "is_active": r.is_active,
        "archived_at": r.archived_at.isoformat() if r.archived_at else None,
    }


def _parse_plan(raw: Any) -> list[BookingAction] | JsonResponse:
    if not isinstance(raw, list):
        return _error("bad_request", "bookings_plan must be a list", 400)
    out: list[BookingAction] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            return _error(
                "bad_request",
                f"bookings_plan[{idx}] must be an object",
                400,
            )
        booking_id = entry.get("booking_id")
        action = entry.get("action")
        to_master_id = entry.get("to_master_id")
        if not isinstance(booking_id, str) or not booking_id:
            return _error(
                "bad_request",
                f"bookings_plan[{idx}].booking_id must be a non-empty string",
                400,
            )
        if action not in ("reassign", "cancel"):
            return _error(
                "bad_request",
                f"bookings_plan[{idx}].action must be 'reassign' or 'cancel'",
                400,
            )
        if action == "reassign":
            if not isinstance(to_master_id, str) or not to_master_id:
                return _error(
                    "bad_request",
                    f"bookings_plan[{idx}].to_master_id required for reassign",
                    400,
                )
        out.append(
            BookingAction(
                booking_id=booking_id,
                action=action,
                to_master_id=to_master_id if isinstance(to_master_id, str) else None,
            )
        )
    return out


# --- POST /api/v1/admin/masters/<id>/deactivation-preview/ ---------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_deactivation_preview(request: HttpRequest, master_id: str) -> HttpResponse:
    """Read-only inventory + fallback ranking for the MM5 Step 1 sheet.

    Both Owner AND Admin may call (no destructive side-effect).
    Cross-tenant masters fall through to 404 (no info leak).
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    preview = preview_deactivation(
        master,
        actor=bot_user,
        actor_role=role_ctx.primary_role,
    )
    return JsonResponse(_preview_to_payload(preview))


# --- POST /api/v1/admin/masters/<id>/deactivate/ -------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_deactivate(request: HttpRequest, master_id: str) -> HttpResponse:
    """Owner-only — execute the deactivation cascade per the plan."""

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error(
            "forbidden",
            "only the salon owner can deactivate a master",
            403,
        )

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    plan_raw = body.get("bookings_plan")
    if plan_raw is None:
        return _error("bad_request", "bookings_plan is required", 400)
    parsed = _parse_plan(plan_raw)
    if isinstance(parsed, JsonResponse):
        return parsed

    reason = body.get("reason") or ""
    if not isinstance(reason, str):
        return _error("bad_request", "reason must be a string", 400)
    if len(reason) > 1000:
        return _error("bad_request", "reason exceeds 1000 chars", 400)

    notify_reassigned_masters = bool(body.get("notify_reassigned_masters", True))

    custom_template = body.get("custom_notification_template")
    if custom_template is not None and not isinstance(custom_template, str):
        return _error(
            "bad_request",
            "custom_notification_template must be a string or null",
            400,
        )
    if isinstance(custom_template, str) and len(custom_template) > 4000:
        return _error(
            "bad_request",
            "custom_notification_template exceeds 4000 chars",
            400,
        )

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    try:
        result = execute_deactivation(
            master,
            plan=parsed,
            reason=reason,
            notify_reassigned_masters=notify_reassigned_masters,
            custom_template=custom_template,
            actor=bot_user,
            actor_role=role_ctx.primary_role,
        )
    except DeactivationError as exc:
        return _error(exc.slug, exc.detail, exc.status)

    return JsonResponse(_deact_result_to_payload(result))


# --- POST /api/v1/admin/masters/<id>/reactivate/ -------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_reactivate(request: HttpRequest, master_id: str) -> HttpResponse:
    """Owner-only — flip an archived master back to active."""

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error(
            "forbidden",
            "only the salon owner can reactivate a master",
            403,
        )

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    notify_master = bool(body.get("notify_master", True))

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    try:
        result = reactivate_master(
            master,
            notify_master=notify_master,
            actor=bot_user,
            actor_role=role_ctx.primary_role,
        )
    except DeactivationError as exc:
        return _error(exc.slug, exc.detail, exc.status)

    return JsonResponse(_reactivation_to_payload(result))


__all__ = [
    "master_deactivate",
    "master_deactivation_preview",
    "master_reactivate",
]
