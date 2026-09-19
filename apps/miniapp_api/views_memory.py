"""«Что Ayla помнит» — память человека на экране Mini App (DRF-2133, Память-2).

    GET    /customer/memory/               → {green: [...], health: [...], status}
    DELETE /customer/memory/{entry_id}/    → своя запись забыта; чужая — 404
    POST   /customer/memory/forget-all/    → то же, что «забудь всё» в чате

Экран — второе окно в ту же память, не вторая память. Читатели те же, что
у чата: зелёные факты — ``memory_reader.read_green_entries`` под
``memory_key_policy.select_current_facts`` (текущее значение ключа, не
история — DRF-1262), подпись — ``memory_surface.describe_green_content``
(«помню, что ты…» читается одинаково в чате и на экране); раздел
«Здоровье» — только через ``RedZoneReader`` (лог доступа на каждую строку;
до Память-1 / DRF-2132 раздел пуст, путь построен). Удалитель —
``memory_deleter`` для зелёных, ``RedZoneReader.soft_delete_for_subject`` для
red; причина ``user_request_miniapp`` отличает экран от команды в чате.

«Забыть всё» — те же три обязательства, что у чата (OD_MEMORY.md §4):
``request_forget_all`` + обезличивание переписки + стирание профиля у Ayla.
Шаги переиспользуются из ``persona.memory_commands``, не копируются.

Ключ памяти — ``BotUser.ayla_user_id``, не ``BotUser.id`` (это разные
идентификаторы; спутать их — прочитать чужую строку). Без него — пустой
ответ, не ошибка. Гейт §2.4 (``person_context_access``) — как у чата.

Отдельный модуль, а не :mod:`apps.miniapp_api.views`: ворота и отказы
берутся оттуда импортом.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.identity.models import BotUser, MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.miniapp_api.views import _error, require_init_data

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_DELETION_PENDING = "deletion_pending"

PROVENANCE_SAID = "said"
PROVENANCE_INFERRED = "inferred"

_PURPOSE_SCREEN = "miniapp_memory_screen"
_PURPOSE_FORGET = "miniapp_memory_forget"


def _memory_user_id(bot_user: BotUser) -> uuid.UUID | None:
    raw = getattr(bot_user, "ayla_user_id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        logger.warning("miniapp_api.memory.bad_ayla_user_id bot_user=%s", bot_user.pk)
        return None


def _gate_closed(bot_user: BotUser) -> bool:
    """§2.4: a shell the gate refuses gets the honest empty answer, never a fact."""
    from apps.identity.services.person_context_gate import person_context_access

    return person_context_access(bot_user) is not None


def _provenance(entry: MemoryEntry) -> str:
    """«ты сказал(а)» only for what the person said; every guess is marked."""
    return PROVENANCE_SAID if entry.source == MemoryEntry.SOURCE_EXPLICIT else PROVENANCE_INFERRED


def _green_payload(entry: MemoryEntry) -> dict[str, Any]:
    from apps.persona.memory_surface import describe_green_content

    content = entry.content if isinstance(entry.content, dict) else {}
    return {
        "id": str(entry.id),
        "key": content.get("key"),
        "label": describe_green_content(content),
        "value": content.get("value"),
        "said_at": entry.created_at.isoformat(),
        "provenance": _provenance(entry),
    }


def _health_payload(entry: MemoryEntry) -> dict[str, Any]:
    content = entry.content if isinstance(entry.content, dict) else {}
    return {
        "id": str(entry.id),
        "kind": entry.kind,
        "value": content.get("display") or content.get("value"),
        "said_at": entry.created_at.isoformat(),
    }


def _status(user_id: uuid.UUID) -> str:
    row = (
        UserPersonalContext.objects.filter(user_id=user_id)
        .values_list("forget_all_requested_at", "deletion_requested_at")
        .first()
    )
    if row is None:
        return STATUS_ACTIVE
    return STATUS_DELETION_PENDING if any(row) else STATUS_ACTIVE


def _empty(status: str = STATUS_ACTIVE) -> JsonResponse:
    return JsonResponse({"green": [], "health": [], "status": status})


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_memory(request: HttpRequest) -> HttpResponse:
    """Что Ayla помнит о звонящем: зелёные факты + раздел «Здоровье» + статус."""
    from apps.identity.services.memory_key_policy import select_current_facts
    from apps.identity.services.memory_reader import read_green_entries
    from apps.identity.services.red_zone_reader import RedZoneReader

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    user_id = _memory_user_id(bot_user)
    if user_id is None or _gate_closed(bot_user):
        return _empty()

    status = _status(user_id)
    if status != STATUS_ACTIVE:
        # The read gate already hides everything; say why the list is empty.
        return _empty(status)

    green = [_green_payload(e) for e in select_current_facts(read_green_entries(user_id))]
    health = [
        _health_payload(e)
        for e in RedZoneReader.list_live_for_subject(
            user_id=user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
            request_id=uuid.uuid4(),
            purpose=_PURPOSE_SCREEN,
            accessor_principal=f"bot_user:{bot_user.pk}",
        )
    ]
    return JsonResponse({"green": green, "health": health, "status": status})


@csrf_exempt
@require_http_methods(["DELETE"])
@require_init_data
def customer_memory_entry(request: HttpRequest, entry_id: uuid.UUID) -> HttpResponse:
    """«Забыть» одну запись. Чужая, несуществующая, уже забытая — 404."""
    from apps.identity.services.memory_deleter import soft_delete_green_entries
    from apps.identity.services.red_zone_reader import RedZoneReader

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    user_id = _memory_user_id(bot_user)
    if user_id is None or _gate_closed(bot_user):
        return _error("not_found", "Такой записи нет.", 404)

    # Green first (the common case), then the audited red path. Both scope
    # to (user_id, live) themselves — a foreign id moves nothing.
    deleted = soft_delete_green_entries(
        user_id, [entry_id], reason=MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP
    )
    if not deleted:
        deleted = RedZoneReader.soft_delete_for_subject(
            entry_id=entry_id,
            user_id=user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
            request_id=uuid.uuid4(),
            purpose=_PURPOSE_FORGET,
            reason=MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
            accessor_principal=f"bot_user:{bot_user.pk}",
        )
    if not deleted:
        return _error("not_found", "Такой записи нет.", 404)
    return JsonResponse({"id": str(entry_id), "deleted": True})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_memory_forget_all(request: HttpRequest) -> HttpResponse:
    """«Забыть всё» — три обязательства чата, тем же кодом; 202 и статус."""
    from apps.identity.services.memory_deleter import request_forget_all
    from apps.persona import memory_commands

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    user_id = _memory_user_id(bot_user)
    if user_id is None:
        # Nothing was ever remembered under this shell; the request is honoured trivially.
        return JsonResponse({"status": STATUS_DELETION_PENDING}, status=202)

    request_forget_all(user_id)
    # Same order as the chat confirmation branch: the person hears «сейчас».
    memory_commands._anonymize_dialogue(bot_user)
    erased = memory_commands._bridge_erase(bot_user)
    if erased != "erased":
        # Best-effort upstream; the sweep retries. Named in the log, not hidden.
        logger.warning("miniapp_api.memory.forget_all.bridge_%s bot_user=%s", erased, bot_user.pk)
    return JsonResponse({"status": STATUS_DELETION_PENDING}, status=202)
