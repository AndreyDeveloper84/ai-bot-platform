"""Очередь «ждут подтверждения» и само подтверждение — из Mini App салона.

DRF-1597, решение владельца 08.09.2026: «надо дать возможность
пользователю самому выставлять статусы».

### Зачем этот эндпойнт существует

Мастер, заведённый через админку Ayla, доезжает до зеркала бота
синхронизацией и рождается ``invite_status=pending`` — умолчание модели
после DRF-1496. Синхронизация это поле не пишет никогда
(``catalog/services/upserter.upsert_specialists``: ``invite_status`` нет
в словаре ``mirror``), поэтому строка остаётся ``pending`` навсегда, а
гейт продажи (``catalog/master_state.AVAILABLE``) требует ``accepted``.
Клиент такого мастера не видит.

Подтвердить его до этой задачи мог только человек с доступом к
Django-админке: действие в админке каталога и кнопка на экране
подключения салона, второй — вообще суперюзерный. Владелица салона,
которая мастера и завела, не могла ничего и НЕ УЗНАВАЛА, что нужен
второй шаг: подсказка на форме заведения (DRF-1596) называет шаг, но не
даёт его сделать.

Здесь этот шаг даётся ей — очередью и кнопкой, на том же экране
«Команда», где она мастеров и видит.

### Очередь, а не «подтвердить этого»

``GET`` отдаёт СПИСОК. Заведя десять мастеров, владелица должна увидеть
десять строк в одном месте, а не находить каждую поштучно — иначе
невидимый мастер снова теряется молча (та же беда, ради которой заведён
экран «Кого видит клиент», DRF-1585).

Выборка — :data:`~apps.catalog.master_state.AWAITING_VERIFICATION`, тот
же предикат, что считает экран подключения. Он равен ``AVAILABLE`` с
одним перевёрнутым условием — приглашением, — поэтому очередь даёт
владелице ЧЕСТНОЕ обещание: подтвердишь этих — ровно эти и станут
продаваться. Мастер в архиве или снятый с активности сюда не попадает:
подтверждение его продаваемым не сделает, и строка в очереди обещала бы
исход, которого не будет.

### Чего очередь НЕ содержит и почему

Строки с живым персональным приглашением — ``invite_token`` выписан и не
протух. Такому человеку приглашение реально отправлено, и ждёт оно
ИМЕННО его нажатия; подтвердить за него — обход согласия (тот же класс,
что обход HEALTH в §25 п.6). Граница держится в сервисе
(``catalog/services/verification``), а не здесь: она одна на все экраны,
и нажатие владелицы её не двигает. Здесь очередь только повторяет тот же
``Q``, чтобы не обещать строк, которых кнопка не тронет.

Замер 08.09.2026: на пилоте таких строк нет — приглашение не уходит ни
по одному каналу (см. ``views_invite`` §44.4 и сторону Ayla), а путь
``masters/invite/`` заводит строку с ``is_active=False`` и без
``ayla_user_id``, то есть в ``AWAITING_VERIFICATION`` она и так не
входит. Запрет стоит не от сегодняшних данных, а от завтрашней склейки:
``upsert_specialists`` умеет находить приглашённую строку по
``ayla_user_id`` (DRF-1507) и проставить ей и активность, и связь — и
тогда живое приглашение окажется в очереди.

### Подтверждает владелица, читают владелица и админ

``POST`` — только владелица (``role_context.is_owner``). Это не копия
права на деактивацию «за компанию»: подтверждение делает человека
продаваемым клиенту, и владелец пилота назвал ответственным за своих
людей именно владелицу салона. ``GET`` открыт и админу — знать, кто
ждёт, полезнее, чем прятать список от того, кто всё равно видит ростер.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.catalog.master_state import AWAITING_VERIFICATION, is_available
from apps.catalog.models import CatalogMaster
from apps.catalog.services import verification
from apps.identity.services.role_resolver import RoleContext

logger = logging.getLogger(__name__)

#: Потолок на разовое подтверждение. Не про нагрузку — про жест:
#: подтверждение делает людей продаваемыми, и «выделить всё» на тысяче
#: строк не должно быть одним нечаянным нажатием.
MAX_BATCH = 100


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _awaiting_qs():  # type: ignore[no-untyped-def]
    """Строки, которые ждут подтверждения владелицей.

    ``AWAITING_VERIFICATION`` минус живые персональные приглашения —
    ровно то, что тронет кнопка. Скоуп тенанта уже наложен декоратором
    (``require_admin_role`` открывает ``tenant_scope``), поэтому здесь
    ``objects``, а не ``all_tenants``.
    """

    return CatalogMaster.objects.filter(AWAITING_VERIFICATION).exclude(verification.live_invite_q())


def _named_qs():  # type: ignore[no-untyped-def]
    """Строки, которые владелица назвала поимённо.

    Отличается от :func:`_awaiting_qs` ровно одним: живые персональные
    приглашения НЕ вычитаются здесь запросом. Не потому, что их можно
    подтвердить, — их не подтвердит сервис, — а потому, что отказ обязан
    БЫТЬ НАЗВАН. Вычти их фильтром, и владелица, указавшая такого
    мастера, получила бы ``verified: 0, blocked: 0`` — «ничего не
    произошло» вместо «за этого человека решать нельзя».

    Архив и снятая активность вычитаются и здесь: подтверждение таких
    строк продаваемости не даёт, и кнопка обещала бы исход, которого не
    будет — то же правило, что у очереди.
    """

    return CatalogMaster.objects.filter(AWAITING_VERIFICATION)


def _row(master: CatalogMaster) -> dict[str, Any]:
    """Строка очереди. Ровно то, по чему владелица узнаёт человека."""

    return {
        "id": str(master.id),
        "name": master.name,
        "specialization": master.specialization,
        "photo_url": master.photo_url,
        "invite_status": master.invite_status,
    }


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_admin_role
def masters_awaiting_verification(request: HttpRequest) -> HttpResponse:
    """``GET`` — очередь, ``POST`` — подтверждение.

    ``GET`` → ``{"items": [...], "count": N}``.

    ``POST`` body:
      ``master_ids`` — список id (необязательный). Без него
      подтверждаются ВСЕ строки очереди этого салона.

    ``POST`` → ``{"verified", "skipped", "blocked", "not_eligible",
    "still_hidden", "remaining"}``.

    Шесть чисел, а не одно «ок», потому что у каждого пропуска обязано
    быть имя (OPEN_DECISIONS §78 — та же форма дефекта, что у формы
    заведения в PR #300: приняла POST, ответила «сохранено» и молча
    потеряла привязку).
    """

    if request.method == "GET":
        qs = _awaiting_qs().order_by("name", "id")
        # ``count`` считается по всей очереди, а не по отданной странице:
        # число на карточке «Ждут подтверждения» — это сколько мастеров
        # не видит клиент, и обрезать его до размера страницы значило бы
        # успокоить владелицу неправдой.
        return JsonResponse(
            {"items": [_row(m) for m in qs[:MAX_BATCH]], "count": qs.count()},
            status=200,
        )
    return _verify(request)


def _verify(request: HttpRequest) -> HttpResponse:
    tenant = request.tenant  # type: ignore[attr-defined]
    actor = request.bot_user  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error(
            "forbidden",
            "подтвердить мастера может только владелец салона",
            403,
        )

    try:
        body: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    raw_ids = body.get("master_ids")
    if raw_ids is None:
        # Вся очередь. Читается ЗАНОВО, а не берётся из ответа ``GET``:
        # между показом списка и нажатием мастер мог принять приглашение
        # сам или уехать в архив.
        selected = list(_awaiting_qs()[:MAX_BATCH])
    else:
        if not isinstance(raw_ids, list):
            return _error("bad_request", "master_ids must be a list", 400)
        if not raw_ids:
            return _error("bad_request", "master_ids cannot be empty", 400)
        if len(raw_ids) > MAX_BATCH:
            return _error("bad_request", f"master_ids exceeds {MAX_BATCH}", 400)
        try:
            selected = list(_named_qs().filter(pk__in=[str(i) for i in raw_ids]))
        except (ValidationError, ValueError):
            return _error("bad_request", "master_ids contains an invalid id", 400)

    # Названные, но не отданные очереди — в архиве, снятые с активности,
    # чужого салона или несуществующие. Их НЕЛЬЗЯ проглотить молча
    # (§78): владелица назвала имя и обязана узнать, что оно не было
    # обработано, а не увидеть «подтверждено: 0» без объяснения.
    not_eligible = 0 if raw_ids is None else len({str(i) for i in raw_ids}) - len(selected)

    outcome = verification.verify_masters_by_salon(selected, actor=actor)

    # Обещание кнопки проверяется ЗАМЕРОМ, а не выводится из предиката.
    #
    # Сегодня ``AWAITING_VERIFICATION`` — это ``AVAILABLE`` с одним
    # перевёрнутым условием, поэтому подтверждённая строка обязана пройти
    # гейт. «Обязана» — не «прошла»: гейт уже ужесточали (DRF-1521, и в
    # докстринге ``master_state`` прямо сказано, что будут ещё), и в тот
    # день эндпойнт стал бы отвечать «подтверждено: N» о мастерах,
    # которых по-прежнему не видно. Здесь это не молчит.
    #
    # Считается ТОЛЬКО по строкам, которые действительно перешли в
    # ``accepted``. Заблокированная строка тоже лежит в ``selected`` и
    # тоже не продаётся — но она не продаётся ПО ЗАМЫСЛУ, и попади она
    # сюда, эндпойнт кричал бы о расхождении гейта на штатной границе
    # согласия. Именно так эта проверка и была написана сначала.
    still_hidden = [
        m.name
        for m in CatalogMaster.objects.filter(
            pk__in=[m.pk for m in selected],
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        if not is_available(m)
    ]
    remaining = _awaiting_qs().count()

    if still_hidden:
        logger.error(
            "admin_api.masters_verified.still_hidden tenant=%s masters=%s — "
            "строки подтверждены, но гейт продажи их не пускает. Значит "
            "AWAITING_VERIFICATION разошёлся с AVAILABLE (DRF-1597).",
            tenant.slug,
            still_hidden,
        )

    logger.info(
        "admin_api.masters_verified tenant=%s by=%s verified=%s skipped=%s "
        "blocked=%s not_eligible=%s still_hidden=%s remaining=%s",
        tenant.slug,
        actor.id,
        outcome.verified,
        outcome.skipped,
        outcome.blocked,
        not_eligible,
        len(still_hidden),
        remaining,
    )
    return JsonResponse(
        {
            "verified": outcome.verified,
            "skipped": outcome.skipped,
            "blocked": outcome.blocked,
            "not_eligible": not_eligible,
            "still_hidden": still_hidden,
            "remaining": remaining,
        },
        status=200,
    )
