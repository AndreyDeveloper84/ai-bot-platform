"""HTTP views for the master↔admin internal chat (PR 6).

Two role-gated surfaces:

* Master endpoints (``/api/v1/internal-chat/master/...``) — gated by
  :func:`apps.master_api.auth.require_master_init_data`. A master
  only ever sees the threads where ``thread.master.linked_bot_user``
  is the requester's own BotUser; cross-master access returns 404
  (per handoff §2.7 — surfacing 403 would leak existence).

* Admin endpoints (``/api/v1/internal-chat/admin/...``) — gated by
  :func:`apps.admin_api.auth.require_admin_role`. Owner / Admin see
  every thread for the tenant. Receptionist is *also* visible
  through this surface (PR 6 lets receptionists see the thread list)
  but for sensitive threads the subject is redacted in the list
  response and the detail endpoint returns 403 unless the
  receptionist is the ``assigned_admin``.

Customer-only callers can't reach either surface — the decorators
reject them with 403 / 401 before any view code runs.

### Sensitive-thread redaction (handoff §2.7)

Receptionists must SEE that a sensitive thread exists in their queue
(for SLA awareness + capacity planning) but cannot read message
content. The list endpoint replaces ``subject`` with the sentinel
string «(чувствительный разговор)» and zeroes the message counters
for these rows. The detail endpoint returns 403 with the
``sensitive_restricted`` slug unless the caller is the
``assigned_admin``.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import RoleContext
from apps.internal_chat import services as chat_services
from apps.internal_chat.models import (
    MasterAdminMessage,
    MasterAdminThread,
    SenderRoleChoices,
    StatusChoices,
    TopicChoices,
)
from apps.master_api.auth import require_master_init_data

logger = logging.getLogger(__name__)


# --- constants -----------------------------------------------------------


DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 50

MAX_BODY_LEN = 4000
MAX_SUBJECT_LEN = 200
MAX_REASON_LEN = 1000

SENSITIVE_REDACTED_SUBJECT = "(чувствительный разговор)"


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


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _parse_limit(raw: str | None, default: int = DEFAULT_LIST_LIMIT) -> int | JsonResponse:
    if raw is None:
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _error("bad_request", "limit must be an integer", 400)
    if n < 1:
        return _error("bad_request", "limit must be positive", 400)
    return min(n, MAX_LIST_LIMIT)


def _parse_offset(raw: str | None) -> int | JsonResponse:
    if raw is None:
        return 0
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _error("bad_request", "offset must be an integer", 400)
    if n < 0:
        return _error("bad_request", "offset must be non-negative", 400)
    return n


def _serialize_thread_summary(
    thread: MasterAdminThread,
    *,
    redact_subject: bool = False,
) -> dict[str, Any]:
    """Compact payload for list rendering — no message bodies."""

    subject = thread.subject
    if redact_subject:
        subject = SENSITIVE_REDACTED_SUBJECT
    return {
        "id": str(thread.id),
        "topic": thread.topic,
        "topic_display": thread.get_topic_display(),
        "subject": subject,
        "status": thread.status,
        "is_sensitive": thread.is_sensitive,
        "linked_artifact_type": thread.linked_artifact_type,
        "linked_artifact_id": (
            str(thread.linked_artifact_id) if thread.linked_artifact_id else None
        ),
        "assigned_admin_id": (str(thread.assigned_admin_id) if thread.assigned_admin_id else None),
        "founder_added_at": (
            thread.founder_added_at.isoformat() if thread.founder_added_at else None
        ),
        "sla_due_at": thread.sla_due_at.isoformat(),
        "created_at": thread.created_at.isoformat(),
        "last_activity_at": thread.last_activity_at.isoformat(),
        "resolved_at": thread.resolved_at.isoformat() if thread.resolved_at else None,
    }


def _serialize_thread_detail(
    thread: MasterAdminThread,
    *,
    include_messages: bool = True,
) -> dict[str, Any]:
    """Full thread payload — summary + master pointer + optionally messages."""

    payload = _serialize_thread_summary(thread)
    payload["master"] = {
        "id": str(thread.master_id),
        "name": thread.master.name,
    }
    if include_messages:
        payload["messages"] = [
            _serialize_message(m)
            for m in thread.messages.all().order_by("sent_at").select_related("sender_user")
        ]
    return payload


def _serialize_message(message: MasterAdminMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "sender_role": message.sender_role,
        "sender_user_id": (str(message.sender_user_id) if message.sender_user_id else None),
        "sender_admin_signed_name": message.sender_admin_signed_name,
        "body": message.body,
        "is_system_message": message.is_system_message,
        "sent_at": message.sent_at.isoformat(),
        "read_by_master_at": (
            message.read_by_master_at.isoformat() if message.read_by_master_at else None
        ),
        "read_by_admin_at": (
            message.read_by_admin_at.isoformat() if message.read_by_admin_at else None
        ),
        "read_by_founder_at": (
            message.read_by_founder_at.isoformat() if message.read_by_founder_at else None
        ),
    }


def _validate_body(raw: Any) -> str | JsonResponse:
    if not isinstance(raw, str):
        return _error("bad_request", "body must be a string", 400)
    body = raw.strip()
    if not body:
        return _error("bad_request", "body must not be empty", 400)
    if len(body) > MAX_BODY_LEN:
        return _error(
            "bad_request",
            f"body exceeds {MAX_BODY_LEN} characters",
            400,
        )
    return body


def _validate_subject(raw: Any) -> str | JsonResponse:
    if not isinstance(raw, str):
        return _error("bad_request", "subject must be a string", 400)
    if len(raw) > MAX_SUBJECT_LEN:
        return _error(
            "bad_request",
            f"subject exceeds {MAX_SUBJECT_LEN} characters",
            400,
        )
    return raw


# =========================================================================
# Master-side endpoints
# =========================================================================


@require_http_methods(["GET", "POST"])
@csrf_exempt
@require_master_init_data
def master_threads_collection(request: HttpRequest) -> HttpResponse:
    """List own threads (GET) / create a new thread (POST)."""

    if request.method == "POST":
        return _master_create_thread(request)
    return _master_list_threads(request)


def _master_list_threads(request: HttpRequest) -> HttpResponse:
    master = request.master  # type: ignore[attr-defined]

    limit = _parse_limit(request.GET.get("limit"))
    if isinstance(limit, JsonResponse):
        return limit
    offset = _parse_offset(request.GET.get("offset"))
    if isinstance(offset, JsonResponse):
        return offset

    qs = MasterAdminThread.objects.filter(master=master).order_by("-last_activity_at")
    total = qs.count()
    rows = list(qs[offset : offset + limit])
    items = [_serialize_thread_summary(t) for t in rows]
    return JsonResponse(
        {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
        }
    )


def _master_create_thread(request: HttpRequest) -> HttpResponse:
    master = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    topic = body.get("topic")
    if topic not in TopicChoices.values:
        return _error(
            "bad_request",
            f"topic must be one of {sorted(TopicChoices.values)}",
            400,
        )

    first_message_body = body.get("first_message_body")
    if first_message_body is None:
        return _error(
            "bad_request",
            "first_message_body is required when a master creates a thread",
            400,
        )
    body_or_err = _validate_body(first_message_body)
    if isinstance(body_or_err, JsonResponse):
        return body_or_err
    first_message = body_or_err

    subject = body.get("subject", "")
    subject_or_err = _validate_subject(subject)
    if isinstance(subject_or_err, JsonResponse):
        return subject_or_err

    linked_artifact_type = body.get("linked_artifact_type", "")
    if not isinstance(linked_artifact_type, str):
        return _error("bad_request", "linked_artifact_type must be a string", 400)
    if len(linked_artifact_type) > 32:
        return _error("bad_request", "linked_artifact_type too long", 400)

    raw_artifact_id = body.get("linked_artifact_id")
    linked_artifact_id: uuid.UUID | None = None
    if raw_artifact_id:
        linked_artifact_id = _coerce_uuid(raw_artifact_id)
        if linked_artifact_id is None:
            return _error(
                "bad_request",
                "linked_artifact_id must be a UUID",
                400,
            )

    thread = chat_services.create_thread(
        tenant=tenant,
        master=master,
        topic=topic,
        subject=subject_or_err,
        actor=bot_user,
        first_message_body=first_message,
        sender_role=SenderRoleChoices.MASTER,
        linked_artifact_type=linked_artifact_type,
        linked_artifact_id=linked_artifact_id,
    )

    return JsonResponse(
        {"thread": _serialize_thread_detail(thread)},
        status=201,
    )


def _get_master_thread_or_404(master_id: Any, thread_id: str) -> MasterAdminThread | None:
    tid = _coerce_uuid(thread_id)
    if tid is None:
        return None
    return (
        MasterAdminThread.objects.filter(master_id=master_id, id=tid)
        .select_related("master")
        .first()
    )


@require_http_methods(["GET"])
@csrf_exempt
@require_master_init_data
def master_thread_detail(request: HttpRequest, thread_id: str) -> HttpResponse:
    master = request.master  # type: ignore[attr-defined]
    thread = _get_master_thread_or_404(master.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)
    return JsonResponse({"thread": _serialize_thread_detail(thread)})


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def master_send_message(request: HttpRequest, thread_id: str) -> HttpResponse:
    master = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    thread = _get_master_thread_or_404(master.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)
    if thread.status in (
        StatusChoices.RESOLVED,
        StatusChoices.AUTO_CLOSED_INACTIVE,
    ):
        return _error(
            "thread_closed",
            "thread is closed — start a new one to continue",
            409,
        )

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body
    body_or_err = _validate_body(body.get("body"))
    if isinstance(body_or_err, JsonResponse):
        return body_or_err

    message = chat_services.send_message(
        thread=thread,
        sender_role=SenderRoleChoices.MASTER,
        sender_user=bot_user,
        body=body_or_err,
    )
    thread.refresh_from_db()
    return JsonResponse(
        {"message": _serialize_message(message), "thread": _serialize_thread_summary(thread)},
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def master_mark_read(request: HttpRequest, thread_id: str) -> HttpResponse:
    master = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    thread = _get_master_thread_or_404(master.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)

    count = chat_services.mark_read(
        thread=thread,
        reader_role="master",
        reader_user=bot_user,
    )
    return JsonResponse({"marked": count})


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def master_escalate_to_founder(request: HttpRequest, thread_id: str) -> HttpResponse:
    master = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    thread = _get_master_thread_or_404(master.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body
    reason_raw = body.get("reason", "")
    if not isinstance(reason_raw, str):
        return _error("bad_request", "reason must be a string", 400)
    if len(reason_raw) > MAX_REASON_LEN:
        return _error(
            "bad_request",
            f"reason exceeds {MAX_REASON_LEN} characters",
            400,
        )

    thread = chat_services.escalate_to_founder(thread=thread, actor=bot_user, reason=reason_raw)
    return JsonResponse({"thread": _serialize_thread_summary(thread)})


# =========================================================================
# Admin-side endpoints
# =========================================================================


def _admin_can_view_sensitive_detail(
    *, thread: MasterAdminThread, role_ctx: RoleContext, bot_user_id: uuid.UUID
) -> bool:
    """Sensitive-thread detail gate per handoff §2.7.

    Owner + Admin see every sensitive thread. Receptionist sees the row
    in the LIST (subject redacted) but the DETAIL endpoint refuses
    unless the receptionist is the thread's ``assigned_admin``.
    """

    if not thread.is_sensitive:
        return True
    if role_ctx.is_owner or role_ctx.is_admin:
        return True
    # Receptionist branch.
    return thread.assigned_admin_id == bot_user_id


@require_http_methods(["GET"])
@csrf_exempt
@require_admin_role
def admin_threads_collection(request: HttpRequest) -> HttpResponse:
    """List ALL threads in the tenant (admin queue)."""

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    limit = _parse_limit(request.GET.get("limit"))
    if isinstance(limit, JsonResponse):
        return limit
    offset = _parse_offset(request.GET.get("offset"))
    if isinstance(offset, JsonResponse):
        return offset

    qs = MasterAdminThread.objects.filter(tenant=tenant).select_related("master")

    status_filter = request.GET.get("status")
    if status_filter:
        if status_filter not in StatusChoices.values:
            return _error(
                "bad_request",
                f"status must be one of {sorted(StatusChoices.values)}",
                400,
            )
        qs = qs.filter(status=status_filter)

    topic_filter = request.GET.get("topic")
    if topic_filter:
        if topic_filter not in TopicChoices.values:
            return _error(
                "bad_request",
                f"topic must be one of {sorted(TopicChoices.values)}",
                400,
            )
        qs = qs.filter(topic=topic_filter)

    master_id_filter = request.GET.get("master_id")
    if master_id_filter:
        m_uuid = _coerce_uuid(master_id_filter)
        if m_uuid is None:
            return _error("bad_request", "master_id must be a UUID", 400)
        qs = qs.filter(master_id=m_uuid)

    assigned_filter = request.GET.get("assigned_admin_id")
    if assigned_filter:
        a_uuid = _coerce_uuid(assigned_filter)
        if a_uuid is None:
            return _error("bad_request", "assigned_admin_id must be a UUID", 400)
        qs = qs.filter(assigned_admin_id=a_uuid)

    search = (request.GET.get("search") or "").strip()
    if search:
        qs = qs.filter(subject__icontains=search)

    qs = qs.order_by("-last_activity_at")
    total = qs.count()
    rows = list(qs[offset : offset + limit])

    # Apply sensitive-thread redaction per role.
    items: list[dict[str, Any]] = []
    for thread in rows:
        redact = thread.is_sensitive and not _admin_can_view_sensitive_detail(
            thread=thread, role_ctx=role_ctx, bot_user_id=bot_user.id
        )
        payload = _serialize_thread_summary(thread, redact_subject=redact)
        payload["master"] = {
            "id": str(thread.master_id),
            "name": "" if redact else thread.master.name,
        }
        items.append(payload)

    return JsonResponse(
        {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
        }
    )


def _get_tenant_thread_or_404(tenant_id: Any, thread_id: str) -> MasterAdminThread | None:
    tid = _coerce_uuid(thread_id)
    if tid is None:
        return None
    return (
        MasterAdminThread.objects.filter(tenant_id=tenant_id, id=tid)
        .select_related("master")
        .first()
    )


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_admin_role
def admin_thread_detail(request: HttpRequest, thread_id: str) -> HttpResponse:
    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    thread = _get_tenant_thread_or_404(tenant.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)

    if not _admin_can_view_sensitive_detail(
        thread=thread, role_ctx=role_ctx, bot_user_id=bot_user.id
    ):
        return _error(
            "sensitive_restricted",
            "thread is flagged sensitive; only assigned admin can view detail",
            403,
        )

    if request.method == "PATCH":
        return _admin_patch_thread(request, thread)

    return JsonResponse({"thread": _serialize_thread_detail(thread)})


def _admin_patch_thread(request: HttpRequest, thread: MasterAdminThread) -> HttpResponse:
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    # Pull out the optional fields first; the service layer ignores
    # ``None`` for each.
    topic = body.get("topic")
    if topic is not None and topic not in TopicChoices.values:
        return _error(
            "bad_request",
            f"topic must be one of {sorted(TopicChoices.values)}",
            400,
        )

    subject = body.get("subject")
    if subject is not None:
        s_or_err = _validate_subject(subject)
        if isinstance(s_or_err, JsonResponse):
            return s_or_err
        subject = s_or_err

    status_val = body.get("status")
    if status_val is not None and status_val not in StatusChoices.values:
        return _error(
            "bad_request",
            f"status must be one of {sorted(StatusChoices.values)}",
            400,
        )

    is_sensitive = body.get("is_sensitive")
    if is_sensitive is not None and not isinstance(is_sensitive, bool):
        return _error("bad_request", "is_sensitive must be a boolean", 400)

    # assigned_admin_id is a separate concern from the other field
    # patches — it has its own audit event. Handle inline.
    assigned_admin_raw = body.get("assigned_admin_id")
    assigned_admin: BotUser | None = None
    apply_assignment = False
    if "assigned_admin_id" in body:
        apply_assignment = True
        if assigned_admin_raw is None:
            assigned_admin = None
        else:
            a_uuid = _coerce_uuid(assigned_admin_raw)
            if a_uuid is None:
                return _error("bad_request", "assigned_admin_id must be a UUID", 400)
            # Validate the user exists + is staff in this tenant. Use
            # all_tenants then explicit tenant filter so cross-tenant
            # is impossible.
            assigned_admin = BotUser.all_tenants.filter(
                tenant_id=thread.tenant_id, id=a_uuid
            ).first()
            if assigned_admin is None:
                return _error(
                    "bad_request",
                    "assigned_admin_id not found in this tenant",
                    400,
                )

    try:
        with transaction.atomic():
            if any(v is not None for v in (topic, subject, status_val, is_sensitive)):
                thread = chat_services.patch_thread_fields(
                    thread=thread,
                    actor=bot_user,
                    topic=topic,
                    subject=subject,
                    status=status_val,
                    is_sensitive=is_sensitive,
                )
            if apply_assignment:
                thread = chat_services.assign_admin(
                    thread=thread,
                    assigned_admin=assigned_admin,
                    actor=bot_user,
                )
    except chat_services.InternalChatServiceError as exc:
        return _error(exc.slug, str(exc), 400)

    return JsonResponse({"thread": _serialize_thread_detail(thread)})


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def admin_send_message(request: HttpRequest, thread_id: str) -> HttpResponse:
    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]

    thread = _get_tenant_thread_or_404(tenant.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)
    if not _admin_can_view_sensitive_detail(
        thread=thread, role_ctx=role_ctx, bot_user_id=bot_user.id
    ):
        return _error(
            "sensitive_restricted",
            "thread is flagged sensitive; only assigned admin can reply",
            403,
        )
    if thread.status in (
        StatusChoices.RESOLVED,
        StatusChoices.AUTO_CLOSED_INACTIVE,
    ):
        return _error(
            "thread_closed",
            "thread is closed",
            409,
        )

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body
    body_or_err = _validate_body(body.get("body"))
    if isinstance(body_or_err, JsonResponse):
        return body_or_err

    signed_name = body.get("sender_admin_signed_name", "")
    if not isinstance(signed_name, str):
        return _error("bad_request", "sender_admin_signed_name must be a string", 400)
    if len(signed_name) > 64:
        return _error("bad_request", "sender_admin_signed_name too long", 400)

    message = chat_services.send_message(
        thread=thread,
        sender_role=SenderRoleChoices.ADMIN,
        sender_user=bot_user,
        body=body_or_err,
        sender_admin_signed_name=signed_name,
    )
    thread.refresh_from_db()
    return JsonResponse(
        {"message": _serialize_message(message), "thread": _serialize_thread_summary(thread)},
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def admin_mark_read(request: HttpRequest, thread_id: str) -> HttpResponse:
    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]

    thread = _get_tenant_thread_or_404(tenant.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)
    if not _admin_can_view_sensitive_detail(
        thread=thread, role_ctx=role_ctx, bot_user_id=bot_user.id
    ):
        return _error(
            "sensitive_restricted",
            "thread is flagged sensitive; only assigned admin can mark read",
            403,
        )

    count = chat_services.mark_read(
        thread=thread,
        reader_role="admin",
        reader_user=bot_user,
    )
    return JsonResponse({"marked": count})


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def admin_close_thread(request: HttpRequest, thread_id: str) -> HttpResponse:
    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]

    thread = _get_tenant_thread_or_404(tenant.id, thread_id)
    if thread is None:
        return _error("not_found", "thread not found", 404)
    if not _admin_can_view_sensitive_detail(
        thread=thread, role_ctx=role_ctx, bot_user_id=bot_user.id
    ):
        return _error(
            "sensitive_restricted",
            "thread is flagged sensitive; only assigned admin can close",
            403,
        )

    thread = chat_services.close_thread(thread=thread, actor=bot_user, status="resolved")
    return JsonResponse({"thread": _serialize_thread_summary(thread)})


__all__ = [
    "admin_close_thread",
    "admin_mark_read",
    "admin_send_message",
    "admin_thread_detail",
    "admin_threads_collection",
    "master_escalate_to_founder",
    "master_mark_read",
    "master_send_message",
    "master_thread_detail",
    "master_threads_collection",
]
