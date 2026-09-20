"""Тройка ассистента администратора — история, вопрос, подтверждение (DRF-2119).

``GET  /api/v1/admin/assistant/history`` — что уже сказано, старые первыми;
``POST /api/v1/admin/assistant/ask``     — один вопрос → ответ; пишущее действие
только ПРЕДЛАГАЕТСЯ (``pending_action``: ``confirm_kind`` = ``token`` — талон на
подтверждение здесь, ``open`` — ссылка в Mini App, сервер ничего не делает);
``POST /api/v1/admin/assistant/confirm`` — выполнить по талону, и только по нему.

Зеркало ``master_api/views_assistant.py`` под ``require_admin_role``:
владелец / админ; ресепшн и мастер — 403 (DRF-2115 — у ресепшна раздела
«Ayla» нет). Нить — та же ``StaffAssistantThread`` человека, что у салонного
бота, ``role_at_open="admin"``; лимиты — по ``bot_user``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 50
MAX_QUESTION_CHARS = 1000
ROLE_AT_OPEN = "admin"


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _body(request: HttpRequest) -> dict[str, Any] | JsonResponse:
    if not request.body:
        return {}
    try:
        parsed = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("bad_request", "body must be valid JSON", 400)
    if not isinstance(parsed, dict):
        return _error("bad_request", "body must be a JSON object", 400)
    return parsed


def _thread(bot_user):
    """Рабочая нить этого человека. Никогда не бросает — история не условие ответа."""

    from apps.conversations.staff_assistant import resolve_active_staff_thread

    try:
        return resolve_active_staff_thread(bot_user, role_at_open=ROLE_AT_OPEN)
    except Exception:  # noqa: BLE001
        logger.exception("admin_api.assistant.thread_open_failed bot_user=%s", bot_user.id)
        return None


def _remember(thread, *, role: str, content: str, **telemetry):
    if thread is None:
        return None

    from apps.conversations.staff_assistant import record_staff_message

    try:
        return record_staff_message(thread, role=role, content=content, **telemetry)
    except Exception:  # noqa: BLE001
        logger.exception("admin_api.assistant.thread_write_failed role=%s", role)
        return None


def _message_dict(row) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "role": row.role,
        "content": row.content or "",
        "tool": row.tool_name or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@csrf_exempt
@require_http_methods(["GET"])
@require_admin_role
def assistant_history(request: HttpRequest) -> HttpResponse:
    """Последние реплики администратора с Ayla, старые первыми."""

    from apps.conversations.staff_assistant import visible_staff_history

    bot_user = request.bot_user  # type: ignore[attr-defined]
    raw_limit = request.GET.get("limit", "")
    try:
        limit = int(raw_limit) if raw_limit else DEFAULT_HISTORY_LIMIT
    except ValueError:
        return _error("bad_request", "limit must be an integer", 400)
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))

    thread = _thread(bot_user)
    if thread is None:
        return JsonResponse({"messages": []})
    # DRF-2151: тот же фильтр, что у мастера — команды/токены/tool-строки не на экране.
    rows = visible_staff_history(thread, limit=limit)
    return JsonResponse({"messages": [_message_dict(r) for r in rows]})


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def assistant_ask(request: HttpRequest) -> HttpResponse:
    """Один вопрос — один ответ. Пишущее действие только предлагается."""

    from apps.admin_api.services.assistant import answer_admin_question
    from apps.conversations.staff_assistant import recent_staff_history

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user = request.bot_user  # type: ignore[attr-defined]
    role_ctx = request.role_context  # type: ignore[attr-defined]

    body = _body(request)
    if isinstance(body, JsonResponse):
        return body
    text = str(body.get("text") or "").strip()
    if not text:
        return _error("bad_request", "text is required", 400)
    if len(text) > MAX_QUESTION_CHARS:
        return _error("bad_request", f"text must be ≤ {MAX_QUESTION_CHARS} chars", 400)

    thread = _thread(bot_user)
    from apps.conversations.staff_assistant import is_hidden_staff_turn

    # DRF-2151: команда / токен приглашения в нить не пишется.
    inbound = (
        None if is_hidden_staff_turn("user", text) else _remember(thread, role="user", content=text)
    )
    history = (
        recent_staff_history(thread, exclude_id=getattr(inbound, "id", None))
        if thread is not None
        else []
    )

    reply = answer_admin_question(
        tenant=tenant, bot_user=bot_user, role_ctx=role_ctx, text=text, history=history
    )
    outbound = _remember(
        thread,
        role="assistant",
        content=reply.text,
        tool_name=reply.tool_name,
        tokens_in=reply.tokens_in,
        tokens_out=reply.tokens_out,
        llm_provider=reply.llm_provider,
        llm_model=reply.llm_model,
        llm_cost_usd=reply.llm_cost_usd,
    )
    return JsonResponse(
        {
            "answer": reply.text,
            "tool": reply.tool_name,
            "pending_action": reply.pending_action,
            "message_id": str(outbound.id) if outbound is not None else "",
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def assistant_confirm(request: HttpRequest) -> HttpResponse:
    """Выполнить предложенное действие — по талону и только по нему.

    Аргументы берутся ИЗ талона, а не из тела: иначе экран мог бы показать
    одну сводку, а прислать другие числа. Действие «открыть форму» сюда не
    приходит — у него нет талона, форму открывает сам Mini App.
    """

    from apps.admin_api.services.assistant import ActionError, execute_admin_action

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user = request.bot_user  # type: ignore[attr-defined]
    role_ctx = request.role_context  # type: ignore[attr-defined]

    body = _body(request)
    if isinstance(body, JsonResponse):
        return body
    token = str(body.get("token") or "").strip()
    if not token:
        return _error("bad_request", "token is required", 400)

    role = "owner" if getattr(role_ctx, "is_owner", False) else "admin"
    try:
        done = execute_admin_action(token, tenant=tenant, bot_user=bot_user, role=role)
    except ActionError as exc:
        status = 403 if exc.slug == "action_not_yours" else 400
        return _error(exc.slug or "action_failed", exc.detail, status)

    thread = _thread(bot_user)
    outbound = _remember(thread, role="assistant", content=done.text, tool_name=done.name)
    return JsonResponse(
        {
            "answer": done.text,
            "action": done.name,
            "executed": True,
            "message_id": str(outbound.id) if outbound is not None else "",
        }
    )


__all__ = ["assistant_ask", "assistant_confirm", "assistant_history"]
