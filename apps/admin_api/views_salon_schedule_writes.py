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

# Экран «Рабочее время» салона (DRF-2936)

* **Отгул в местной форме.** ``POST time-off/`` принимает и ``date`` +
  ``from`` + ``to`` в часах салона (``to=24:00`` — конец суток); смещение
  прикладывает сервер по поясу салона, как у предпросмотра
  (``services.salon_window``). Прежняя форма ``start_at``/``end_at``
  осталась; обе сразу — отказ. Ответ — в поясе салона, как у чтения.
* **Повтор не ставит второй отгул.** Перед созданием бот читает отгулы
  мастера; тот же интервал уже есть — ``200`` с ним и ``"existing": true``,
  без записи. Повтор после потерянного ответа (``salon_write_outcome_unknown``)
  иначе закрывал время дважды, а каталог сверяет новый отгул только с
  записями клиентов.
* **Изменение даты не стирает чужое.** PUT каталога заменяет строку даты
  целиком; бот дописывает перерыв дня недели из шаблона и прежнюю заметку
  даты (:func:`_prepare_date_exception`).
* **Можно ли писать** — один ответ с чтениями
  (``services.salon_write_access.schedule_writable``).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.salon_window import WindowRefusal, parse_local_window
from apps.admin_api.services.salon_write_access import signed_by_the_salon_bot
from apps.admin_api.views_master_exceptions import _time_off_row
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
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.services.schedule_frame import _ayla_read_actor
from apps.miniapp_api.auth import InitDataError, extract_init_data
from apps.tenancy.timezones import salon_zone

logger = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: One machine code for every refusal of the write itself — see the module doc.
REFUSED = "salon_write_refused"


#: A write whose outcome we do not know (network cut after sending): the
#: salon must look before trying again — not the same as «nothing happened».
OUTCOME_UNKNOWN = "salon_write_outcome_unknown"


def _refused() -> JsonResponse:
    return JsonResponse({"error": REFUSED}, status=403)


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


def salon_person_write(
    request: HttpRequest,
    master_id: str,
    op: str,
    call: Callable[[Any, str, str, CatalogMaster], dict[str, Any]],
    *,
    success_status: int,
    on_success: Callable[[CatalogMaster, dict[str, Any]], HttpResponse] | None = None,
    precheck: Callable[[Any, CatalogMaster], HttpResponse | None] | None = None,
) -> HttpResponse:
    """Token → write → journal. Every refusal of the person answers the same.

    ``on_success`` — for a write whose answer the bot must mirror before
    replying (DRF-2619: the master's photo address). Without it the catalog's
    ``data`` goes back as is.

    ``precheck`` — reads the write needs before it is sent (DRF-2936: a
    duplicate time off, the weekday's break). Runs after every check of the
    person and BEFORE the token exchange: an answer it returns ends the
    request without spending an exchange, and nothing is written.
    """

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

    if not signed_by_the_salon_bot(request):
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

    if precheck is not None:
        early = precheck(client, master)
        if early is not None:
            return early

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
        body: dict[str, Any] = {"error": "validation", "detail": str(exc)}
        if exc.details:
            # Which rule refused (``not_square`` …) — the screen's words hang on it.
            body["details"] = exc.details
        return JsonResponse(body, status=400)
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
    if on_success is not None:
        return on_success(master, data if isinstance(data, dict) else {})
    return JsonResponse({"data": data}, status=success_status)


_LOCAL_WINDOW_KEYS = ("date", "from", "to")
_MOMENT_KEYS = ("start_at", "end_at")


def _time_off_window(body: dict[str, Any], tenant: Any) -> tuple[str, str] | JsonResponse:
    """Окно отгула из тела: моменты ``start_at``/``end_at`` или местная форма.

    Местная форма (DRF-2936) — дата и часы САЛОНА, смещение прикладывает
    сервер (``services.salon_window``): экран смещение не считает.
    """

    local = any(key in body for key in _LOCAL_WINDOW_KEYS)
    moments = any(key in body for key in _MOMENT_KEYS)
    if local and moments:
        return JsonResponse(
            {
                "error": "validation",
                "detail": "send either start_at/end_at or date/from/to, not both",
            },
            status=400,
        )
    if local:
        parsed = parse_local_window(
            body.get("date"), body.get("from"), body.get("to"), salon_zone(tenant)
        )
        if isinstance(parsed, WindowRefusal):
            return JsonResponse({"error": "validation", "detail": parsed.detail}, status=400)
        return parsed
    if not body.get("start_at") or not body.get("end_at"):
        return JsonResponse(
            {"error": "validation", "detail": "start_at and end_at are required"}, status=400
        )
    return str(body["start_at"]), str(body["end_at"])


def _same_window_time_off(
    client: Any, master: CatalogMaster, start_at: str, end_at: str
) -> dict[str, Any] | None:
    """Отгул с ровно этим интервалом у мастера уже есть — его строка.

    Страховка от дубля, а не условие записи: не прочиталось — дубля не знаем,
    и запись идёт. Окно чтения — сутки до и после по поясу салона: каталог
    режет диапазон по датам в СВОЁМ поясе, и отгул у полуночи салона иначе
    выпал бы из него.
    """

    try:
        start = datetime.fromisoformat(start_at)
        end = datetime.fromisoformat(end_at)
    except ValueError:
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    tenant = master.tenant
    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        return None
    zone = salon_zone(tenant)
    try:
        rows = client.list_time_off(
            actor_external_id=external_user_id_for(actor_user),
            tenant_slug=tenant.slug,
            specialist_id=catalog_specialist_id(master),
            date_from=(start.astimezone(zone).date() - timedelta(days=1)).isoformat(),
            date_to=(end.astimezone(zone).date() + timedelta(days=1)).isoformat(),
        )
    except SalonAPIError as exc:
        logger.warning(
            "admin_api.salon_write.dedupe_unread tenant=%s err=%s", tenant.slug, type(exc).__name__
        )
        return None
    for row in rows:
        try:
            same = (
                datetime.fromisoformat(str(row.get("start_at"))) == start
                and datetime.fromisoformat(str(row.get("end_at"))) == end
            )
        except (TypeError, ValueError):
            continue
        if same:
            return row
    return None


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_time_off(request: HttpRequest, master_id: str) -> HttpResponse:
    body = _body(request)
    if body is None:
        return JsonResponse(
            {"error": "validation", "detail": "a JSON object is required"}, status=400
        )
    refused = _text_fields_refusal(body, "reason")
    if refused is not None:
        return refused
    window = _time_off_window(body, request.tenant)  # type: ignore[attr-defined]
    if isinstance(window, JsonResponse):
        return window
    start_at, end_at = window

    def existing(client: Any, master: CatalogMaster) -> HttpResponse | None:
        row = _same_window_time_off(client, master, start_at, end_at)
        if row is None:
            return None
        logger.info(
            "admin_api.salon_write.deduplicated op=time_off_create tenant=%s", master.tenant.slug
        )
        mirrored = _time_off_row(row, salon_zone(master.tenant))
        return JsonResponse({"data": {**row, **(mirrored or {})}, "existing": True}, status=200)

    def in_salon_zone(master: CatalogMaster, data: dict[str, Any]) -> HttpResponse:
        # The catalog answers in UTC; the screen reads the salon's clock (the
        # same rule as the read, ``views_master_exceptions._in_zone``).
        mirrored = _time_off_row(data, salon_zone(master.tenant))
        return JsonResponse({"data": {**data, **(mirrored or {})}}, status=201)

    return salon_person_write(
        request,
        master_id,
        "time_off_create",
        lambda client, token, slug, master: client.create_time_off(
            person_token=token,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            start_at=start_at,
            end_at=end_at,
            reason=str(body.get("reason") or ""),
        ),
        success_status=201,
        on_success=in_salon_zone,
        precheck=existing,
    )


@csrf_exempt
@require_http_methods(["DELETE"])
@require_admin_role
def master_time_off_detail(request: HttpRequest, master_id: str, time_off_id: str) -> HttpResponse:
    if not _is_uuid(time_off_id):
        return JsonResponse({"error": "not_found"}, status=404)
    return salon_person_write(
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
    kept: dict[str, Any] = {"break_start": None, "break_end": None, "note": body.get("note")}
    return salon_person_write(
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
            break_start=kept["break_start"],
            break_end=kept["break_end"],
            note=str(kept["note"] or ""),
        ),
        success_status=200,
        precheck=lambda client, master: _prepare_date_exception(client, master, body, kept),
    )


_HHMM_WIRE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?")


def _hhmm(value: Any) -> str | None:
    """``"HH:MM"`` / ``"HH:MM:SS"`` → ``"HH:MM"``; иначе ``None``."""

    if isinstance(value, str) and _HHMM_WIRE.fullmatch(value):
        return value[:5]
    return None


def _prepare_date_exception(
    client: Any, master: CatalogMaster, body: dict[str, Any], kept: dict[str, Any]
) -> HttpResponse | None:
    """Что сохранить вместе с изменением даты, чтобы не стереть чужое (DRF-2936).

    PUT каталога заменяет строку даты целиком: всё, чего нет в теле, станет
    пустым. Два поля бот поэтому дописывает сам, в ``kept``.

    * **Перерыв дня недели.** Рабочий день на дату без перерыва — это обед,
      ставший временем для записи. Перерыв из шаблона переносится, если он
      целиком внутри новых часов; целиком снаружи — не нужен; поперёк —
      отказ с названной причиной: обрезать его или выбросить значило бы
      решить за салон. Шаблон не прочитан — не пишем: неизвестный перерыв
      не то же, что «перерыва нет».
    * **Заметка даты.** Экран её не правит; тело без ``note`` сохраняет
      прежнюю. Не прочиталась — пишется пустая: заметка не время, и запрет
      записи из-за неё был бы хуже её потери.
    """

    tenant = master.tenant
    actor_user = _ayla_read_actor(tenant)
    actor = external_user_id_for(actor_user) if actor_user is not None else None
    specialist = catalog_specialist_id(master)
    day = str(body["date"])

    if "note" not in body and actor is not None:
        try:
            rows = client.list_schedule_exceptions(
                actor_external_id=actor,
                tenant_slug=tenant.slug,
                specialist_id=specialist,
                date_from=day,
                date_to=day,
            )
        except (SalonAPIError, ValueError) as exc:
            logger.warning(
                "admin_api.salon_write.note_unread tenant=%s err=%s",
                tenant.slug,
                type(exc).__name__,
            )
        else:
            for row in rows:
                if str(row.get("date")) == day:
                    kept["note"] = row.get("note") or ""
                    break

    start = _hhmm(body.get("start_time"))
    end = _hhmm(body.get("end_time"))
    if not body["is_working_day"] or start is None or end is None:
        # Выходной перерыва не несёт; кривые часы отклонит каталог (400).
        return None
    try:
        weekday = date_cls.fromisoformat(day).weekday()
    except ValueError:
        return None
    if actor is None:
        return JsonResponse(
            {
                "error": "schedule_source_not_configured",
                "detail": f"no active owner/admin staff to read the weekly template for {tenant.slug}; nothing was written",
            },
            status=503,
        )
    try:
        template = client.get_master_schedule(
            actor_external_id=actor, tenant_slug=tenant.slug, specialist_id=specialist
        )
    except SalonAPIError as exc:
        logger.warning(
            "admin_api.salon_write.template_unread tenant=%s err=%s",
            tenant.slug,
            type(exc).__name__,
        )
        return JsonResponse(
            {
                "error": "schedule_unavailable",
                "detail": "could not read the weekly template to keep its break; nothing was written",
            },
            status=503,
        )
    row = next((r for r in template if r.get("day_of_week") == weekday), None)
    if row is None:
        return JsonResponse(
            {
                "error": "schedule_unavailable",
                "detail": f"the weekly template has no row for weekday {weekday}; nothing was written",
            },
            status=503,
        )
    break_start = _hhmm(row.get("break_start"))
    break_end = _hhmm(row.get("break_end"))
    if not row.get("is_working_day") or break_start is None or break_end is None:
        return None
    if start <= break_start and break_end <= end:
        kept["break_start"], kept["break_end"] = break_start, break_end
        return None
    if break_end <= start or break_start >= end:
        return None
    return JsonResponse(
        {
            "error": "validation",
            "detail": "the weekday's break crosses the new hours",
            "details": {
                "reason": "template_break_crosses_hours",
                "break_start": break_start,
                "break_end": break_end,
            },
        },
        status=400,
    )


@csrf_exempt
@require_http_methods(["DELETE"])
@require_admin_role
def master_date_exception_detail(request: HttpRequest, master_id: str, date: str) -> HttpResponse:
    if not _ISO_DATE.fullmatch(date):
        return JsonResponse(
            {"error": "validation", "detail": "date must be YYYY-MM-DD"}, status=400
        )
    return salon_person_write(
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
