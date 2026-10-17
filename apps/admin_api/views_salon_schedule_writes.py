"""Отгул мастера и изменение на дату — запись от имени администратора (DRF-2607).

``POST   /api/v1/admin/masters/<id>/time-off/``            — отгул
``DELETE /api/v1/admin/masters/<id>/time-off/<pk>/``       — снять отгул
``PUT    /api/v1/admin/masters/<id>/date-exceptions/``     — изменение на дату
``DELETE /api/v1/admin/masters/<id>/date-exceptions/<d>/`` — вернуть к шаблону

# Чей ключ

Решение владельца 29.09: «(а) — подпись MAX, служебный ключ в записи не
участвует». Запись идёт на СОБСТВЕННОМ токене человека: его ``initData`` —
тот же, которым он только что прошёл ``require_admin_role``, — меняется в
каталоге на короткий токен, привязанный к нему и к этому салону
(``apps.integrations.ayla.person_token``). Служебного ключа здесь нет ни на
одном пути, и запасного пути к нему тоже нет: не выдан токен — записи нет.

§117 требовал трёх проверок до записи — все три у каталога: право
(``IsTenantAdmin`` + мастер этого салона), рамка салона (токен привязан к
нему) и авторство (журнал каталога называет человека и ``via=max_init_data``).

# Отказ — три уровня (главное окно, 29.09)

1. **Код ответа один** — ``403 salon_write_refused`` и на «нет прав», и на
   «токен не выдан»: снаружи их не различить, это подсказка перебирающему.
2. **Журнал разный** — причина из каталога (``NO_SALON_ROLE``,
   ``LINK_NOT_PROVEN``, ``INIT_DATA_STALE``, …) и признак ``ours``: сломаны
   ли мы (настройка, отказ каталога) — у этого случая другое лечение, и
   единый код не должен его прятать.
3. **Слова человеку** — не отсюда. Ответ несёт машинный код; фразу для
   экрана даёт владелец (вопрос вынесен в PR).

Недельный шаблон и закрытия салона здесь НЕ пишутся: шаблон ждёт сторожа
сжатия, закрытия не открывались.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.channels.bot_registry import SALON_STREAM, effective_registry, resolve_by_slug
from apps.catalog.models import CatalogMaster
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.integrations.ayla.person_token import (
    PersonTokenRefused,
    cached_person_token,
    forget_person_token,
    obtain_person_token,
)
from apps.integrations.ayla.salon_client import (
    SalonAPIError,
    SalonNotConfigured,
    SalonForbidden,
    SalonNotFound,
    SalonSlotTaken,
    SalonUnauthorized,
    SalonUnavailable,
    SalonValidationError,
    get_salon_client,
)
from apps.miniapp_api.auth import InitDataError, extract_init_data

logger = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: One machine code for every refusal of the write itself — see the module doc.
REFUSED = "salon_write_refused"


#: A write whose outcome we do not know (network cut after sending): the
#: salon must look before trying again — not the same as «nothing happened».
OUTCOME_UNKNOWN = "salon_write_outcome_unknown"


def _refused() -> JsonResponse:
    return JsonResponse({"error": REFUSED}, status=403)


def _signed_by_the_salon_bot(request: HttpRequest) -> bool:
    """The catalog accepts only the salon-master bot's signature. The bot's own
    gate accepts any registered bot — refuse here, before spending an exchange,
    when the person opened the Mini App from another bot."""
    verified = getattr(request, "verified_init_data", None)
    entry = resolve_by_slug(getattr(verified, "bot_slug", "") or "", effective_registry())
    return entry is not None and entry.stream == SALON_STREAM


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _master(master_id: str) -> CatalogMaster | None:
    """Мастер ЭТОГО салона (``tenant_scope`` действует); чужой и кривой id — одно «нет»."""
    try:
        mid = uuid.UUID(master_id)
    except (TypeError, ValueError):
        return None
    return CatalogMaster.objects.filter(id=mid).first()


def _text_fields_refusal(body: dict[str, Any], *fields: str) -> JsonResponse | None:
    """DRF-2666: свободный текст уходит в каталог как чужие данные.

    Нестроковое значение — отказ этой же двери (``validation``, 400) ДО
    обмена токена и вызова каталога, а не текст ``"{'a': 1}"`` в отгуле.
    """
    for field in fields:
        value = body.get(field)
        if value is not None and not isinstance(value, str):
            return JsonResponse(
                {"error": "validation", "detail": f"{field} must be a string"}, status=400
            )
    return None


def _body(request: HttpRequest) -> dict[str, Any] | None:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _write(
    request: HttpRequest,
    master_id: str,
    op: str,
    call: Callable[[Any, str, str, CatalogMaster], dict[str, Any]],
    *,
    success_status: int,
) -> HttpResponse:
    """Token → write → journal. Every refusal of the person answers the same."""

    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user.pk  # type: ignore[attr-defined]
    master = _master(master_id)
    if master is None:
        return JsonResponse({"error": "not_found"}, status=404)
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        # The master exists and is not set up in the catalog yet — the same
        # code and reason as every neighbour (DRF-2637); before any exchange.
        return JsonResponse(
            {
                "error": "catalog_profile_unresolved",
                "detail": "master is not set up in the catalog yet",
            },
            status=409,
        )

    if not _signed_by_the_salon_bot(request):
        logger.info(
            "admin_api.salon_write.refused op=%s actor=%s tenant=%s reason=WRONG_BOT ours=no",
            op,
            actor,
            tenant.slug,
        )
        return _refused()

    try:
        client = get_salon_client()
    except SalonNotConfigured:
        logger.error(
            "admin_api.salon_write.refused op=%s tenant=%s reason=CLIENT_NOT_CONFIGURED ours=yes",
            op,
            tenant.slug,
        )
        return _refused()

    verified = request.verified_init_data  # type: ignore[attr-defined]
    cache_key = (str(actor), tenant.slug, str(getattr(verified, "auth_date", "")))
    try:
        raw = extract_init_data(request.headers.get("Authorization"))
        token = cached_person_token(
            key=cache_key, init_data=raw, tenant_slug=tenant.slug, obtain=obtain_person_token
        )
    except InitDataError:
        # ``require_admin_role`` has already verified this header; reaching
        # here means it changed shape between the two reads — refuse.
        logger.warning(
            "admin_api.salon_write.refused op=%s actor=%s tenant=%s reason=NO_INIT_DATA ours=no",
            op,
            actor,
            tenant.slug,
        )
        return _refused()
    except PersonTokenRefused as exc:
        if exc.reason == "INIT_DATA_STALE":
            # Not «no rights»: the person HAS them, the launch is too old for a
            # write. A distinct code so the screen can say «reopen» — the words
            # are the owner's. Same slug the admin gate uses for its own
            # «too old» (``stale``, 401).
            logger.info(
                "admin_api.salon_write.refused op=%s actor=%s tenant=%s reason=TOKEN_NOT_ISSUED:INIT_DATA_STALE ours=no",
                op,
                actor,
                tenant.slug,
            )
            return JsonResponse({"error": "stale"}, status=401)
        log = logger.error if exc.ours_to_fix else logger.info
        log(
            "admin_api.salon_write.refused op=%s actor=%s tenant=%s reason=TOKEN_NOT_ISSUED:%s ours=%s",
            op,
            actor,
            tenant.slug,
            exc.reason,
            "yes" if exc.ours_to_fix else "no",
        )
        return _refused()

    try:
        # The master, not a pre-resolved id: each call resolves the catalog id
        # itself (``catalog_specialist_id`` in the call — the DRF-1933 guard
        # reads exactly that).
        result = call(client, token.access_token, tenant.slug, master)
    except SalonForbidden:
        forget_person_token(cache_key)
        # The person's fresh token, refused: their right went away between
        # issuance and the write.
        logger.info(
            "admin_api.salon_write.refused op=%s actor=%s tenant=%s reason=CATALOG_REFUSED ours=no",
            op,
            actor,
            tenant.slug,
        )
        return _refused()
    except SalonUnauthorized:
        forget_person_token(cache_key)
        # A token issued seconds ago and not recognised: a partial catalog
        # rollout, a tenant mismatch, a key rotation — ours.
        logger.error(
            "admin_api.salon_write.refused op=%s actor=%s tenant=%s reason=CATALOG_REJECTED_TOKEN ours=yes",
            op,
            actor,
            tenant.slug,
        )
        return _refused()
    except SalonValidationError as exc:
        return JsonResponse({"error": "validation", "detail": str(exc)}, status=400)
    except SalonNotFound:
        return JsonResponse({"error": "not_found"}, status=404)
    except SalonSlotTaken as exc:
        # 409 HAS_ACTIVE_APPOINTMENTS: live bookings sit in the period.
        return JsonResponse({"error": "has_active_appointments", "detail": str(exc)}, status=409)
    except SalonUnavailable:
        # Sent, answer lost: the write may have landed.
        logger.error(
            "admin_api.salon_write.outcome_unknown op=%s actor=%s tenant=%s", op, actor, tenant.slug
        )
        return JsonResponse({"error": OUTCOME_UNKNOWN}, status=503)
    except SalonAPIError as exc:
        logger.error(
            "admin_api.salon_write.failed op=%s tenant=%s err=%s",
            op,
            tenant.slug,
            type(exc).__name__,
        )
        return JsonResponse({"error": "salon_unavailable"}, status=503)

    logger.info(
        "admin_api.salon_write.done op=%s actor=%s tenant=%s via=person_token",
        op,
        actor,
        tenant.slug,
    )
    if success_status == 204:
        return HttpResponse(status=204)
    data = result.get("data", result) if isinstance(result, dict) else result
    return JsonResponse({"data": data}, status=success_status)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_time_off(request: HttpRequest, master_id: str) -> HttpResponse:
    body = _body(request)
    if body is None or not body.get("start_at") or not body.get("end_at"):
        return JsonResponse(
            {"error": "validation", "detail": "start_at and end_at are required"}, status=400
        )
    refused = _text_fields_refusal(body, "reason")
    if refused is not None:
        return refused
    return _write(
        request,
        master_id,
        "time_off_create",
        lambda client, token, slug, master: client.create_time_off(
            person_token=token,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            start_at=str(body["start_at"]),
            end_at=str(body["end_at"]),
            reason=str(body.get("reason") or ""),
        ),
        success_status=201,
    )


@csrf_exempt
@require_http_methods(["DELETE"])
@require_admin_role
def master_time_off_detail(request: HttpRequest, master_id: str, time_off_id: str) -> HttpResponse:
    if not _is_uuid(time_off_id):
        return JsonResponse({"error": "not_found"}, status=404)
    return _write(
        request,
        master_id,
        "time_off_delete",
        lambda client, token, slug, master: client.delete_time_off(
            person_token=token,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            time_off_id=time_off_id,
        ),
        success_status=204,
    )


@csrf_exempt
@require_http_methods(["PUT"])
@require_admin_role
def master_date_exception(request: HttpRequest, master_id: str) -> HttpResponse:
    body = _body(request)
    if body is None or not body.get("date") or not isinstance(body.get("is_working_day"), bool):
        return JsonResponse(
            {"error": "validation", "detail": "date and a boolean is_working_day are required"},
            status=400,
        )
    refused = _text_fields_refusal(body, "note")
    if refused is not None:
        return refused
    return _write(
        request,
        master_id,
        "date_exception_set",
        lambda client, token, slug, master: client.set_schedule_exception(
            person_token=token,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            date=str(body["date"]),
            is_working_day=body["is_working_day"],
            start_time=body.get("start_time"),
            end_time=body.get("end_time"),
            note=str(body.get("note") or ""),
        ),
        success_status=200,
    )


@csrf_exempt
@require_http_methods(["DELETE"])
@require_admin_role
def master_date_exception_detail(request: HttpRequest, master_id: str, date: str) -> HttpResponse:
    if not _ISO_DATE.fullmatch(date):
        return JsonResponse(
            {"error": "validation", "detail": "date must be YYYY-MM-DD"}, status=400
        )
    return _write(
        request,
        master_id,
        "date_exception_delete",
        lambda client, token, slug, master: client.delete_schedule_exception(
            person_token=token,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            date=date,
        ),
        success_status=204,
    )
