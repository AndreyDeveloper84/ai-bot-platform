"""Раздел «Ayla» мастерского Mini App — HTTP поверх готового ассистента.

Решение владельца (OD-7 от 21.08, повторено 05.09 в DRF-1180):

    «Раздел «Ayla» — это диалог мастера с Ayla, тот же, что в боте, но
    через Mini App. Не список клиентских переписок.»

Слово «тот же» здесь буквальное. Экран не заводит своей истории: он
читает и пишет ту же :class:`~apps.conversations.models.StaffAssistantThread`,
в которую пишет салонный бот
(`apps/channels/max/salon_handler.py::_handle_talk`). Мастер спрашивает
в боте по дороге и дочитывает ответ в приложении, а не начинает
разговор заново, потому что сменил окно.

### Три ручки

* ``GET  /assistant/history``  — что уже сказано, чтобы экран открылся
  не пустым.
* ``POST /assistant/ask``      — вопрос и ответ. Ответ может нести
  ``pending_action`` — предложение, которое НЕ выполнено.
* ``POST /assistant/confirm``  — исполнение предложения по талону.

### Почему исполнение — отдельная ручка, а не флаг в ответе

Эпик DRF-1180 требует показать, что именно будет сделано, получить
подтверждение и только потом выполнять. Ручка `ask` физически не умеет
писать: пишущее действие возвращается из
:mod:`apps.master_api.services.assistant_actions` предложением, а
исполнение живёт за вторым HTTP-запросом, который делает нажатие
человека. Немого пути от ответа модели к записи в базе здесь нет.

### Своя вьюха, а не строчка в ``views.py``

``apps/master_api/views.py`` в этот момент правит соседняя задача
(DRF-1507). Отдельный модуль — не стилистика, а способ не устроить
конфликт в файле на 1700 строк.

### Новый модуль под тем же PII-запретом

Сканер литералов в ``tests/test_pii_boundary.py`` читает весь пакет
``master_api``, включая этот файл: поле с телефоном клиента здесь так
же невозможно, как в ростере (DRF-1039 / DRF-1360). Расшифровки
дополнительно просматриваются в ``tests/test_assistant_api.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.master_api.auth import require_master_init_data

logger = logging.getLogger(__name__)

#: Сколько реплик отдаёт `history`. Совпадает с окном, которое видит
#: модель (`staff_assistant.DEFAULT_HISTORY_LIMIT` = 10), удвоенным:
#: человеку полезно видеть чуть больше, чем помнит собеседник.
DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 50

#: Длиннее вопроса на телефоне между клиентами не набирают, а длинный
#: ввод — это вставленный лог, за который платит tenant.
MAX_QUESTION_CHARS = 1000

ROLE_AT_OPEN = "master"


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
    """Рабочая нить этого человека — та же, что у салонного бота.

    Никогда не бросает: история — место, куда записывают ответ, а не
    условие, без которого на вопрос нельзя ответить.
    """

    from apps.conversations.staff_assistant import resolve_active_staff_thread

    try:
        return resolve_active_staff_thread(bot_user, role_at_open=ROLE_AT_OPEN)
    except Exception:  # noqa: BLE001 — история не должна стоить ответа
        logger.exception("master_api.assistant.thread_open_failed bot_user=%s", bot_user.id)
        return None


def _remember(thread, *, role: str, content: str, **telemetry):
    if thread is None:
        return None

    from apps.conversations.staff_assistant import record_staff_message

    try:
        return record_staff_message(thread, role=role, content=content, **telemetry)
    except Exception:  # noqa: BLE001
        logger.exception("master_api.assistant.thread_write_failed role=%s", role)
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
@require_master_init_data
def assistant_history(request: HttpRequest) -> HttpResponse:
    """Последние реплики диалога мастера с Ayla, старые первыми."""

    from apps.conversations.staff_assistant import recent_staff_history

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

    rows = recent_staff_history(thread, limit=limit)
    return JsonResponse({"messages": [_message_dict(r) for r in rows]})


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def assistant_ask(request: HttpRequest) -> HttpResponse:
    """Один вопрос — один ответ. Пишущее действие только предлагается."""

    from apps.master_api.services.assistant import answer_master_question

    master = request.master  # type: ignore[attr-defined]
    bot_user = request.bot_user  # type: ignore[attr-defined]

    body = _body(request)
    if isinstance(body, JsonResponse):
        return body

    text = str(body.get("text") or "").strip()
    if not text:
        return _error("bad_request", "text is required", 400)
    if len(text) > MAX_QUESTION_CHARS:
        return _error("bad_request", f"text must be ≤ {MAX_QUESTION_CHARS} chars", 400)

    from apps.conversations.staff_assistant import recent_staff_history

    thread = _thread(bot_user)
    inbound = _remember(thread, role="user", content=text)
    history = (
        recent_staff_history(thread, exclude_id=getattr(inbound, "id", None))
        if thread is not None
        else []
    )

    reply = answer_master_question(
        master=master,
        text=text,
        history=history,
        allow_actions=True,
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
@require_master_init_data
def assistant_confirm(request: HttpRequest) -> HttpResponse:
    """Выполнить предложенное действие — по талону и только по нему.

    Аргументы берутся ИЗ талона, а не из тела запроса. Иначе клиент мог
    бы показать одну сводку, а прислать на исполнение другие числа — и
    подтверждение стало бы формальностью.
    """

    from apps.master_api.services.assistant_actions import ActionError, execute

    master = request.master  # type: ignore[attr-defined]
    bot_user = request.bot_user  # type: ignore[attr-defined]

    body = _body(request)
    if isinstance(body, JsonResponse):
        return body

    token = str(body.get("token") or "").strip()
    if not token:
        return _error("bad_request", "token is required", 400)

    try:
        done = execute(token, master=master, actor=bot_user)
    except ActionError as exc:
        status = 403 if exc.slug == "action_not_yours" else 400
        return _error(exc.slug, exc.detail, status)

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


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "MAX_HISTORY_LIMIT",
    "MAX_QUESTION_CHARS",
    "assistant_ask",
    "assistant_confirm",
    "assistant_history",
]
