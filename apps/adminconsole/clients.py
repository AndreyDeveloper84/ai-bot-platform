"""Карточка клиента: поиск, метаданные, блокировка (DRF-1497).

### Зачем отдельный экран, а не правка ``identity/admin.py``

Карточке нужен был свой контракт, который у стандартного ``BotUserAdmin``
получить нельзя, не сломав соседние решения:

* владелец по-прежнему видит в сыром экране всё — это закреплено
  тестами DRF-1514, и эта задача того решения не отменяет;
* здесь — рабочий инструмент разбора «почему человек молчит»: телефона
  нет никому (DRF-1039), текстов переписки нет вовсе, есть метаданные.

### Что на экране и почему этого хватает

Поиск — по идентификатору канала, имени, салону. **Не по телефону**:
телефон не показываем и не передаём (DRF-1039), а поиск — та же передача.

Карточка — всё, что отвечает на вопрос инцидента 04.09.2026 за минуту:
когда человек пришёл, в каких салонах он есть, какие согласия выдал и
когда (ворота половины поверхностей), есть ли активная цель, в каком
состоянии его диалоги и сколько в них сообщений. Текстов сообщений здесь
нет и не будет: доступ к ним — отдельное решение владельца через
пропуск по обращению (DRF-1514), и карточка на тот механизм ссылается,
но не дублирует.

Блокировка и снятие — с обязательной причиной и строкой в журнале
(``apps.identity.services.blocking``). Согласия из карточки не ставятся:
показывать — да, выдавать за человека — нет.

### Как экран попадает в админку

Тем же способом, что политики DRF-1495/DRF-1514: обёртка
``admin.site.get_urls`` в ``AdminconsoleConfig.ready()``. Ни одной
строки в чужих файлах, снимается одной строкой.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.adminconsole.client_access import is_platform_operator, record_denial, record_view
from apps.identity.services.blocking import block_user, is_blocked, unblock_user

logger = logging.getLogger(__name__)

#: Имя экрана в журнале доступа для межсалонных блоков карточки (S2-4).
CROSS_SALON_SCREEN = "client_card.cross_salon"

_MARKER = "_ayla_client_card"

_SEARCH_LIMIT = 50


def install_client_card() -> None:
    """Повесить экраны карточки на ``admin.site``. Идемпотентна."""
    from django.contrib import admin

    original = admin.site.get_urls
    if getattr(original, _MARKER, False):
        return

    def get_urls() -> Any:
        from django.urls import path

        view = admin.site.admin_view
        custom = [
            path(
                "console/clients/",
                view(clients_search_view),
                name="adminconsole_clients_search",
            ),
            path(
                "console/clients/<uuid:bot_user_id>/",
                view(client_card_view),
                name="adminconsole_client_card",
            ),
            path(
                "console/clients/<uuid:bot_user_id>/block/",
                view(client_block_view),
                name="adminconsole_client_block",
            ),
            path(
                "console/clients/<uuid:bot_user_id>/unblock/",
                view(client_unblock_view),
                name="adminconsole_client_unblock",
            ),
            path(
                "console/identity/",
                view(identity_queue_view),
                name="adminconsole_identity_queue",
            ),
        ]
        return custom + original()

    setattr(get_urls, _MARKER, True)
    admin.site.get_urls = get_urls  # type: ignore[method-assign]


# ── поиск ─────────────────────────────────────────────────────────────


def clients_search_view(request: HttpRequest) -> HttpResponse:
    """Поиск человека: по идентификатору канала, имени, салону. Не по телефону."""
    denial = _require_perm(request, "identity.view_botuser")
    if denial is not None:
        return denial

    from apps.identity.models import BotUser

    query = (request.GET.get("q") or "").strip()
    results: list[Any] = []
    if query:
        # ``phone`` здесь нет и не появится: поиск по телефону — это та же
        # передача телефона, только медленнее (DRF-1039).
        results = list(
            BotUser.all_tenants.filter(
                Q(channel_user_id__icontains=query)
                | Q(display_name__icontains=query)
                | Q(client_name__icontains=query)
                | Q(tenant__name__icontains=query)
                | Q(tenant__slug__icontains=query)
            ).select_related("tenant")[:_SEARCH_LIMIT]
        )
    return render(
        request,
        "adminconsole/clients_search.html",
        {
            **_admin_context(request),
            "query": query,
            "results": results,
            "searched": bool(query),
        },
    )


# ── карточка ──────────────────────────────────────────────────────────


def client_card_view(request: HttpRequest, bot_user_id: Any) -> HttpResponse:
    """Карточка человека. Метаданные — да; тексты, телефон, медданные — нет."""
    denial = _require_perm(request, "identity.view_botuser")
    if denial is not None:
        return denial

    bot_user = _get_bot_user(bot_user_id)
    if bot_user is None:
        return _not_found(request)

    # S2-4: межсалонное присутствие и факт цели — только с пропуском
    # платформы, и с записью в журнале доступа ДО показа (как у
    # переписки, DRF-1514): просмотр без следа хуже отказа.
    platform = is_platform_operator(request.user)
    if platform:
        record_view(
            actor=request.user, grant=None, screen=CROSS_SALON_SCREEN, object_id=str(bot_user.pk)
        )
        salons = _salons_of(bot_user)
        active_goal = _active_goal_fact(bot_user)
    else:
        record_denial(
            actor=request.user,
            screen=CROSS_SALON_SCREEN,
            object_id=str(bot_user.pk),
            detail="platform pass required",
        )
        salons = []
        active_goal = ""

    return render(
        request,
        "adminconsole/client_card.html",
        {
            **_admin_context(request),
            "client": bot_user,
            "blocked": is_blocked(bot_user),
            "can_block": request.user.has_perm("identity.change_botuser"),
            "platform_operator": platform,
            "salons": salons,
            "consents": _consents_of(bot_user),
            "dialogs": _dialogs_of(bot_user),
            "open_tasks": _open_tasks_of(bot_user),
            "active_goal": active_goal,
            "deletion": _deletion_request_of(bot_user),
            "block_url": reverse("admin:adminconsole_client_block", args=[bot_user.pk]),
            "unblock_url": reverse("admin:adminconsole_client_unblock", args=[bot_user.pk]),
            "search_url": reverse("admin:adminconsole_clients_search"),
            "queue_url": reverse("admin:handoff_admintask_changelist"),
            "identity_queue_url": reverse("admin:adminconsole_identity_queue"),
        },
    )


# ── блокировка ────────────────────────────────────────────────────────


def client_block_view(request: HttpRequest, bot_user_id: Any) -> HttpResponse:
    """Форма блокировки. Без причины не проходит — см. сервис."""
    return _block_form(request, bot_user_id, action="block")


def client_unblock_view(request: HttpRequest, bot_user_id: Any) -> HttpResponse:
    """Форма снятия блокировки. Причина обязательна и здесь."""
    return _block_form(request, bot_user_id, action="unblock")


def _block_form(request: HttpRequest, bot_user_id: Any, *, action: str) -> HttpResponse:
    denial = _require_perm(request, "identity.change_botuser")
    if denial is not None:
        return denial

    bot_user = _get_bot_user(bot_user_id)
    if bot_user is None:
        return _not_found(request)

    card_url = reverse("admin:adminconsole_client_card", args=[bot_user.pk])
    if request.method == "POST":
        reason = request.POST.get("reason", "")
        try:
            if action == "block":
                block_user(actor=request.user, bot_user=bot_user, reason=reason)
                messages.success(request, "Клиент заблокирован. Запись в журнале — с вашим именем.")
            else:
                unblock_user(actor=request.user, bot_user=bot_user, reason=reason)
                messages.success(request, "Блокировка снята. Запись в журнале — с вашим именем.")
        except ValidationError as exc:
            return render(
                request,
                "adminconsole/client_block_form.html",
                {
                    **_admin_context(request),
                    "client": bot_user,
                    "action": action,
                    "reason": reason,
                    "errors": exc.messages,
                    "card_url": card_url,
                },
                status=400,
            )
        return HttpResponseRedirect(card_url)

    return render(
        request,
        "adminconsole/client_block_form.html",
        {
            **_admin_context(request),
            "client": bot_user,
            "action": action,
            "reason": "",
            "errors": [],
            "card_url": card_url,
        },
    )


# ── данные карточки ───────────────────────────────────────────────────


def _get_bot_user(bot_user_id: Any) -> Any:
    from apps.identity.models import BotUser

    return BotUser.all_tenants.filter(pk=bot_user_id).select_related("tenant").first()


def _salons_of(bot_user: Any) -> list[Any]:
    """Салоны, где есть этот человек — та же личность канала, другие тенанты."""
    from apps.identity.models import BotUser

    return list(
        BotUser.all_tenants.filter(
            channel=bot_user.channel,
            channel_user_id=bot_user.channel_user_id,
        )
        .select_related("tenant")
        .order_by("tenant__name")
    )


def _consents_of(bot_user: Any) -> list[Any]:
    """Все строки согласий с датами. Показывать — да, ставить — нет."""
    from apps.consent.models import ConsentRecord

    return list(
        ConsentRecord.all_tenants.filter(bot_user=bot_user).order_by("consent_type", "-captured_at")
    )


def _dialogs_of(bot_user: Any) -> list[Any]:
    """Состояние диалогов: сколько сообщений, когда последнее. Текстов нет."""
    from apps.conversations.models import Conversation

    return list(
        Conversation.all_tenants.filter(bot_user=bot_user, deleted_at__isnull=True)
        .annotate(message_count=Count("messages"))
        .order_by("-last_message_at")[:20]
    )


def _open_tasks_of(bot_user: Any) -> list[Any]:
    """Открытые обращения по человеку — это и есть «передан оператору»."""
    from apps.handoff.models import AdminTask

    return list(
        AdminTask.all_tenants.filter(
            bot_user=bot_user,
            status__in=(AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS),
        ).order_by("-created_at")
    )


def _active_goal_fact(bot_user: Any) -> str:
    """Есть ли активная цель: «есть» / «нет» / «нет данных».

    Сам текст цели не показываем: формулировка может касаться здоровья,
    а задача ставит в карточку только факт. Слой целей живёт в Ayla и
    спрашивается на живую — любой сбой честно превращается в «нет
    данных», а не в молчаливое «нет».
    """
    try:
        from apps.integrations.ayla.goals_client import fetch_decision_context

        doc = fetch_decision_context(
            external_user_id=f"bot:{bot_user.channel}:{bot_user.channel_user_id}"
        )
    except Exception:  # noqa: BLE001 — карточка важнее одного блока
        logger.info(
            "adminconsole.clients.goal_unavailable bot_user=%s",
            getattr(bot_user, "pk", None),
            exc_info=True,
        )
        return "нет данных"
    known = doc.get("known") if isinstance(doc, dict) else None
    goal = known.get("goal") if isinstance(known, dict) else None
    if isinstance(goal, dict) and (goal.get("goal_key") or goal.get("goal_text")):
        return "есть"
    return "нет"


def _deletion_request_of(bot_user: Any) -> dict[str, Any] | None:
    """Живая заявка на удаление у этого человека (§7, D2): номер и с какого
    момента персонализация остановлена. Носитель заявки — каталог; здесь
    флаг бота по ``ayla_user_id`` человека (все его оболочки)."""
    from apps.identity.models import UserPersonalContext
    from apps.identity.services.privacy import resolve_person_link

    link = resolve_person_link(bot_user)
    if link.conflict or link.ayla_user_id is None:
        return None
    row = (
        UserPersonalContext.objects.filter(
            user_id=link.ayla_user_id, deletion_requested_at__isnull=False
        )
        .values("deletion_requested_at", "deletion_request_id")
        .first()
    )
    if row is None:
        return None
    return {
        "request_id": str(row["deletion_request_id"] or ""),
        "since": row["deletion_requested_at"],
    }


# ── очередь идентичности (S2-4, §6) ───────────────────────────────────


def identity_queue_view(request: HttpRequest) -> HttpResponse:
    """Всё, что ждёт оператора по личности, в одном месте: PENDING-привязки
    соло-мастеров (§6, решаются действиями на карточке мастера) и живые
    заявки на удаление (§7; исполняет каталог, здесь — кто и с какого
    момента закрыт). Метаданные; телефона нет (DRF-1039)."""
    denial = _require_perm(request, "identity.view_botuser")
    if denial is not None:
        return denial

    can_see_links = request.user.has_perm("identity.view_soloidentitylink")
    return render(
        request,
        "adminconsole/identity_queue.html",
        {
            **_admin_context(request),
            "pending_links": _pending_links() if can_see_links else None,
            "deletion_flags": _deletion_flags(),
            "search_url": reverse("admin:adminconsole_clients_search"),
            "queue_url": reverse("admin:handoff_admintask_changelist"),
        },
    )


def _pending_links() -> list[dict[str, Any]]:
    from apps.identity.models import SoloIdentityLink

    rows = []
    for link in (
        SoloIdentityLink.objects.filter(status=SoloIdentityLink.Status.PENDING)
        .select_related("master")
        .order_by("requested_at")
    ):
        rows.append(
            {
                "link": link,
                "master_url": reverse("admin:catalog_catalogmaster_change", args=[link.master_id]),
            }
        )
    return rows


def _deletion_flags() -> list[dict[str, Any]]:
    from apps.identity.models import BotUser, UserPersonalContext

    rows = []
    flagged = UserPersonalContext.objects.filter(deletion_requested_at__isnull=False).order_by(
        "deletion_requested_at"
    )
    for upc in flagged:
        shells = list(
            BotUser.all_tenants.filter(ayla_user_id=upc.user_id)
            .select_related("tenant")
            .order_by("tenant__name")
        )
        rows.append(
            {
                "ayla_user_id": str(upc.user_id),
                "request_id": str(upc.deletion_request_id or ""),
                "since": upc.deletion_requested_at,
                "shells": [
                    {
                        "shell": shell,
                        "card_url": reverse("admin:adminconsole_client_card", args=[shell.pk]),
                    }
                    for shell in shells
                ],
            }
        )
    return rows


# ── служебное ─────────────────────────────────────────────────────────


def _require_perm(request: HttpRequest, perm: str) -> HttpResponse | None:
    """403 честным текстом. ``admin_view`` уже гарантирует staff."""
    if request.user.has_perm(perm):
        return None
    return render(
        request,
        "adminconsole/client_data_denied.html",
        {
            **_admin_context(request),
            "title": "Недостаточно прав",
            "headline": "Недостаточно прав",
            "paragraphs": [
                "Карточка клиента открывается с правом просмотра клиентов, "
                "блокировка — с правом изменения. Права выдаёт владелец "
                "поимённо, обратитесь к нему.",
            ],
            "queue_url": "",
            "grant_url": "",
            "journal_url": "",
        },
        status=403,
    )


def _not_found(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "adminconsole/client_data_denied.html",
        {
            **_admin_context(request),
            "title": "Клиент не найден",
            "headline": "Клиент не найден",
            "paragraphs": ["Такой записи нет — возможно, ссылка устарела."],
            "queue_url": "",
            "grant_url": "",
            "journal_url": "",
        },
        status=404,
    )


def _admin_context(request: HttpRequest) -> dict[str, Any]:
    from django.contrib import admin

    return dict(admin.site.each_context(request))
