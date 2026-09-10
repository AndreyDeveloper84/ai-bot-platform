"""Просмотр рабочих часов мастера и кнопка «Расписание верно».

§83 (решение владельца 09.09.2026), правило 2: владелец салона ВИДИТ
рабочие часы мастера и подтверждает их. Здесь обе половины — показ и
подтверждение, — и они читают ОДИН источник.

### Почему не ``working_hours_summary`` из карточки мастера

Карточка отдавала строку, собранную из локальной ``scheduling.WorkingHours``,
и её собственный докстринг это признавал: «this line describes the bot's
local mirror, not what a customer is offered». Под ``BOOKING_VIA_AYLA_REST``
(на пилоте включён, замер 09.09.2026) часы клиенту продаёт Ayla, а
локальная таблица — зеркало, которого не читает никто.

Подтверждать по такому экрану нельзя: владелица заверила бы одни часы, а
клиенту продавались бы другие. Поэтому показ переведён на тот же источник,
с которого снимается отпечаток, а строка-сводка из карточки убрана — два
разных расписания на одном экране, одно из которых неправда, хуже одного.

### Почему это отдельная ручка, а не поле карточки мастера

Чтение ходит в Ayla по сети. Вшей мы его в ``master_detail``, недоступность
Ayla уронила бы всю карточку — имя, услуги, состояние, — хотя ни одно из
этих полей от Ayla не зависит. Отдельная ручка означает, что при недоступном
источнике карточка открывается, а на месте расписания стоит названная
причина.

### Подтверждает владелица, смотрят владелица и админ

``POST`` — только ``role_context.is_owner``, по тому же доводу, что и у
подтверждения приглашения (``views_master_verify``): подтверждение делает
человека продаваемым клиенту, и ответственным за своих людей владелец
пилота назвал владелицу салона. ``GET`` открыт и админу: знать, что у
мастера с расписанием, полезнее, чем прятать это от того, кто и так видит
ростер.

### Подтверждают ТО, ЧТО ВИДЕЛИ

``POST`` требует в теле ``fingerprint`` — тот, что пришёл с ``GET``.
Расхождение означает, что часы изменились между показом и нажатием, и
ответом будет ``stale_view``, а не молчаливое подтверждение новых часов.

Без этого правило 2 исполнялось бы наполовину: сервис читает источник
заново в момент нажатия (иначе подписывался бы вчерашний экран), и без
сверки владелица заверила бы расписание, которого не видела. Обе половины
нужны вместе — живое чтение отвечает за свежесть, отпечаток с экрана за
то, что заверено именно увиденное.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.catalog.models import CatalogMaster
from apps.catalog.services import schedule_confirmation as sc
from apps.identity.services.role_resolver import RoleContext
from apps.integrations.ayla.salon_client import SalonNotConfigured, SalonUnavailable

logger = logging.getLogger(__name__)


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _master_or_none(master_id: str) -> CatalogMaster | None:
    """Мастер этого салона. Скоуп наложен ``require_admin_role``.

    Кривой UUID отвечает тем же ``None``, что и чужой салон: наружу это
    одинаковое «нет такого», и различать их в ответе значило бы
    подтверждать существование строки, которую спрашивающему не видно.
    """

    try:
        mid = uuid.UUID(master_id)
    except (TypeError, ValueError):
        return None
    return CatalogMaster.objects.filter(id=mid).select_related("schedule_confirmed_by").first()


def _confirmation_payload(master: CatalogMaster, template: sc.WeeklyTemplate) -> dict[str, Any]:
    """Состояние подтверждения относительно ИМЕННО ЭТИХ часов.

    ``is_current`` считается сравнением с только что прочитанным
    отпечатком, а не по наличию даты: «подтверждено когда-то» и
    «подтверждено для этой версии часов» — разные утверждения, и путать
    их значит вернуть подтверждение к состоянию «кто-то когда-то нажал».
    """

    by = master.schedule_confirmed_by
    confirmed_at = master.schedule_confirmed_at
    return {
        "confirmed_at": confirmed_at.isoformat() if confirmed_at is not None else None,
        "confirmed_by": (
            {"id": str(by.id), "name": by.display_name or by.client_name or ""}
            if by is not None
            else None
        ),
        # Подтверждение есть И оно про эти часы. Одного первого мало.
        "is_current": (
            confirmed_at is not None and master.schedule_fingerprint == template.fingerprint
        ),
        "fingerprint": template.fingerprint,
        # Что помешает нажать кнопку — словом, а не пустотой. ``None``
        # значит «нажимать можно».
        "block": None if template.has_working_day else "no_working_day",
    }


def _schedule_payload(master: CatalogMaster, template: sc.WeeklyTemplate) -> dict[str, Any]:
    return {
        "source": template.source,
        "days": [
            {field: row.get(field) for field in sc.FINGERPRINTED_FIELDS} for row in template.rows
        ],
        "has_working_day": template.has_working_day,
        "confirmation": _confirmation_payload(master, template),
    }


def _read_or_error(master: CatalogMaster) -> tuple[sc.WeeklyTemplate | None, JsonResponse | None]:
    """Читает недельный шаблон и переводит отказы источника в ответ.

    Недоступный источник — 503 с именем, а НЕ пустое расписание: пустой
    список читался бы как «мастер не работает никогда», и владелица
    пошла бы чинить график, с которым всё в порядке.
    """

    try:
        return sc.read_weekly_template(master), None
    except sc.ScheduleConfirmationError as exc:
        # Правило 8: провод отдал не семь дней. Это сбой у нас, не у неё.
        return None, _error(exc.slug, exc.detail, 502)
    except SalonNotConfigured as exc:
        return None, _error("schedule_source_not_configured", str(exc), 503)
    except SalonUnavailable as exc:
        return None, _error("schedule_unavailable", str(exc), 503)


@csrf_exempt
@require_http_methods(["GET"])
@require_admin_role
def master_schedule(request: HttpRequest, master_id: str) -> HttpResponse:
    """Рабочие часы мастера из того источника, по которому его продают."""

    master = _master_or_none(master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    template, failure = _read_or_error(master)
    if failure is not None:
        return failure
    assert template is not None
    return JsonResponse({"schedule": _schedule_payload(master, template)})


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_schedule_confirm(request: HttpRequest, master_id: str) -> HttpResponse:
    """«Расписание верно» — подтвердить увиденные часы. Только владелица."""

    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user = request.bot_user  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error(
            "forbidden",
            "confirming a master's schedule admits them to sale; owner only",
            403,
        )

    master = _master_or_none(master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _error("bad_request", "body must be JSON", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    seen = body.get("fingerprint")
    if not isinstance(seen, str) or not seen:
        # Умолчания здесь нет намеренно: подтверждение без указания того,
        # ЧТО подтверждают, — это и есть «кто-то когда-то нажал».
        return _error(
            "bad_request",
            "fingerprint of the schedule you saw is required",
            400,
        )

    template, failure = _read_or_error(master)
    if failure is not None:
        return failure
    assert template is not None

    if template.fingerprint != seen:
        return _error(
            "stale_view",
            "the schedule changed since it was shown; reload before confirming",
            409,
        )

    try:
        sc.confirm_schedule(master, by=bot_user)
    except sc.ScheduleConfirmationError as exc:
        # Правило 7 приезжает сюда: подтверждать нечего, пока нет ни
        # одного рабочего дня. 409 — состояние предмета, не ошибка запроса.
        return _error(exc.slug, exc.detail, 409)

    master.refresh_from_db()
    logger.info(
        "admin_api.master_schedule.confirmed master=%s actor=%s source=%s",
        master.pk,
        bot_user.id,
        template.source,
    )
    return JsonResponse({"schedule": _schedule_payload(master, template)})


# ─── день мастера для салона (DRF-1237, срез A1) ─────────────────────────────


@csrf_exempt
@require_http_methods(["GET"])
@require_admin_role
def master_day_schedule(request: HttpRequest, master_id: str) -> HttpResponse:
    """Рабочий день мастера глазами салона: часы, записи, окна, конфликты.

    # Почему это тонкий вид, а не свой расчёт

    Способность посчитать доступность уже построена —
    :func:`apps.master_api.services.schedule.build_schedule`: она отдаёт по
    каждой дате ``is_off_day``, ``working_hours``, ``bookings``, ``blocks``,
    ``free_windows`` и ``conflicts``, и считает это **сервер** из
    авторитетного источника (внутри ``load_day_frame``, который ветвится по
    ``BOOKING_VIA_AYLA_REST``).

    Посчитать то же самое здесь — значит завести **четвёртый** вычислитель
    «свободного времени» в одном продукте: сегодня их уже три (резолвер на
    пути записи, ``build_schedule`` на экране мастера, слоты каталога), и они
    между собой расходятся. Расхождение разбирается отдельно (§117, контракт
    доступности); наша обязанность — не добавить к нему пятого.

    Считать окна на клиенте нельзя тем более: это «клиент выдумывает
    доступность» (§17), и запрет записан в ``admin-api.ts`` рядом с полем,
    ради которого он там оказался.

    # Кого пускаем — и что здесь НЕ решено

    ``require_admin_role`` открывает тенантный скоуп, и :func:`_master_or_none`
    ищет мастера **внутри него**: мастер чужого салона отвечает 404, как
    несуществующий. Это обязательно, а не осторожность: докстринг
    ``build_schedule`` прямо говорит «cross-master scoping happens at the auth
    layer; this helper trusts the input» — то есть сервис на скоуп не смотрит
    и смотреть не должен.

    **Второй вопрос авторизации здесь НЕ решён, и это намеренно.** «Мастер
    принадлежит салону» и «этот сотрудник вправе видеть график мастера» —
    разные вопросы. Ресепшн решением владельца (§35 п. 1, DRF-1552) видит день
    салона, а график мастера ей никто не открывал и не закрывал. Ответить на
    это тем, какой декоратор я поставил, значило бы принять продуктовое
    решение молча; вопрос вынесен владельцу — **DRF-1640**. Пока действует
    более узкое правило поверхности: владелец и администратор.

    # Известная слепота, которую этот вид наследует

    **Обеденный перерыв показывается свободным временем.** Провод недельного
    шаблона Ayla несёт ``break_start`` / ``break_end``, но
    ``schedule_frame.FrameHours`` их не несёт, и до
    ``_compute_free_windows`` они не доезжают: окна вычитают записи и блоки,
    а перерыв не то и не другое.

    Цепь замкнута с обеих сторон и на ЖИВОЙ ветке: каталог считает перерыв
    занятым и на чтении, и на записи (``slot_builder``), то есть экран
    предложит время, которое запись отклонит.

    Экспозиция замерена на пилоте 10.09.2026: 63 строки часов у девяти
    мастеров, перерыв не заполнен **ни у одного**, флаг включён. То есть
    расхождение не спит — оно ждёт первой строки: первый салон, поставивший
    мастеру обед, получит его показанным свободным.

    Мы это **наследуем осознанно**, а не по незнанию: чинить разбор кадра —
    предмет **DRF-1638** (эпик контракта доступности DRF-1637), а не салонной
    вкладки, и четвёртый вычислитель ради обхода был бы хуже дефекта. Слепота
    закреплена тестом ``test_the_lunch_break_is_known_to_leak_into_free_windows``:
    он фиксирует НЕ то, что перерыв обрабатывается, а то, что не обрабатывается,
    и обязан покраснеть в день, когда обработку добавят.
    """

    master = _master_or_none(master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    from datetime import date as date_cls
    from datetime import timedelta

    from apps.master_api.services.schedule import (
        DEFAULT_RANGE_DAYS,
        MAX_RANGE_DAYS,
        build_schedule,
    )

    def _parse(name: str) -> date_cls | None:
        raw = request.GET.get(name)
        if not raw:
            return None
        try:
            return date_cls.fromisoformat(raw)
        except ValueError:
            return None

    from_date = _parse("from")
    if from_date is None and request.GET.get("from"):
        return _error("bad_request", "'from' must be YYYY-MM-DD", 400)
    if from_date is None:
        from_date = timezone.localdate()

    to_date = _parse("to")
    if to_date is None and request.GET.get("to"):
        return _error("bad_request", "'to' must be YYYY-MM-DD", 400)
    if to_date is None:
        to_date = from_date + timedelta(days=DEFAULT_RANGE_DAYS - 1)

    # Границы диапазона берутся ИЗ ТОГО ЖЕ МОДУЛЯ, что и расчёт, а не
    # объявляются здесь своими числами: два предела на один расчёт разъехались
    # бы молча, и салон однажды запросил бы диапазон, который кабинет считает
    # недопустимым.
    if from_date > to_date:
        return _error("bad_request", "'from' must be <= 'to'", 400)
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        return _error("bad_request", f"range exceeds {MAX_RANGE_DAYS} days", 400)

    try:
        payload = build_schedule(master, from_date=from_date, to_date=to_date)
    except SalonNotConfigured as exc:
        return _error("schedule_source_not_configured", str(exc), 503)
    except SalonUnavailable as exc:
        return _error("schedule_unavailable", str(exc), 503)

    return JsonResponse(payload.to_dict())
