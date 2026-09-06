"""Экран очереди handoff — кто ждёт человека и сколько уже ждёт (DRF-1499).

### Замер, из которого вырос экран (04.09.2026)

``AdminTask.all_tenants`` — все 10 задач за историю пилота — ни разу не
показывались там, где их возраст читается как «человек ждёт». Одна
задача держала клиента без ответов 1 час 24 минуты, и обнаружилось это
только потому, что владелец сам написал боту. Пока задача открыта,
клиент не получает ответов ни от бота, ни от человека — и нигде не было
места, где это видно.

Этот экран — то самое место. Граница с DRF-1488 держится строго: там
механизм (назначение, предел ожидания, эскалация), здесь окно в него.
Никаких своих полей и своей механики экран не заводит: адресат,
``claimed_at`` и ``pickup_escalated_at`` читаются как есть, «взять»
идёт через :func:`apps.handoff.assignment.claim`, «закрыть» — через
:func:`apps.handoff.services.resolve_admin_task`.

### Что на экране

* счётчик наверху: сколько сейчас открыто и сколько ждёт самая старая;
* список открытых задач, **самые старые сверху**: возраст, салон, тип,
  адресат, состояние;
* замьюченные диалоги: задача заводится в салонном диалоге, а замолкает
  при этом и глобальный (DRF-1486) — мьют ходит за человеком
  (DRF-1015), поэтому показываются все диалоги той же канальной
  личности, а не только диалог-якорь.

### Границы

Переписка целиком здесь не показывается: ``transcript_snapshot``
остаётся на странице самой задачи. Телефона на экране нет (DRF-1039) —
клиент подписывается через :func:`apps.adminconsole.client_access.client_label`.

### Действия и след

«Взять» и «закрыть» — POST-действия через сервисный слой, не правка
полей (то же правило, что в DRF-980). Каждое действие пишет строку в
журнал админки (``LogEntry`` → ``apps.adminconsole.journal``) с автором:
``write_audit`` сервисов автора не знает, а журнал без автора бессмыслен.

Права: экран видит «смотрящий» (``view_admintask``), действия доступны
только «правящему» (``change_admintask``). Проверка в самом view —
Django не проверяет права за кастомные страницы.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone

from apps.adminconsole.client_access import client_label
from apps.handoff.assignment import claim
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.tenancy.context import tenant_scope

if TYPE_CHECKING:  # pragma: no cover — только для аннотаций
    from django.contrib.admin import ModelAdmin
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

#: Состояния, в которых задача держит клиента в мьюте.
OPEN_STATUSES = (AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS)

ACTION_CLAIM = "claim"
ACTION_RESOLVE = "resolve"

#: Имя URL, под которым экран регистрируется в AdminTaskAdmin.get_urls.
QUEUE_URL_NAME = "handoff_admintask_queue"


def queue_urlpattern(model_admin: "ModelAdmin") -> Any:
    """``path()`` для ``AdminTaskAdmin.get_urls`` — экран живёт в namespace админки."""
    from django.urls import path

    return path(
        "queue/",
        model_admin.admin_site.admin_view(partial(queue_view, model_admin)),
        name=QUEUE_URL_NAME,
    )


def format_age(start: datetime, end: datetime) -> str:
    """«N мин» / «N ч MM мин». Возраст считается от ``created_at``, всегда."""
    minutes = max(0, int((end - start).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest:02d} мин"


def open_tasks() -> list[AdminTask]:
    """Открытые задачи всех салонов, самые старые сверху.

    Админка кросс-тенантная (тот же принцип, что в
    ``AdminTaskAdmin.get_queryset``), поэтому ``all_tenants``.
    """
    return list(
        AdminTask.all_tenants.filter(status__in=OPEN_STATUSES)
        .select_related("tenant", "assigned_to", "bot_user")
        .order_by("created_at")
    )


def muted_dialogs(task: AdminTask, *, sentinel_id: Any) -> list[dict[str, Any]]:
    """Диалоги, которые эта задача держит в мьюте.

    Мьют ходит за человеком, а не за диалогом (DRF-1015): пока у
    канальной личности есть открытая задача, молчат ВСЕ её диалоги —
    салонный (в нём задача и заведена, он в HUMAN_HANDOFF) и глобальный
    (DRF-1486). Поэтому перечисляются все разговоры той же личности, а
    диалог-якорь помечается отдельно.
    """
    from apps.conversations.models import Conversation
    from apps.identity.models import BotUser

    bot_user = task.bot_user
    identity_ids = BotUser.all_tenants.filter(
        channel=bot_user.channel, channel_user_id=bot_user.channel_user_id
    ).values("id")
    conversations = (
        Conversation.all_tenants.filter(bot_user_id__in=identity_ids)
        .select_related("tenant")
        .order_by("created_at")
    )
    dialogs = []
    for conversation in conversations:
        if conversation.tenant_id == sentinel_id:
            label = "глобальный диалог"
        else:
            label = f"салон «{conversation.tenant.name}»"
        dialogs.append(
            {
                "label": label,
                "is_anchor": conversation.pk == task.conversation_id,
                "muted": True,  # открытая задача этой личности мьютит все её диалоги
            }
        )
    return dialogs


def build_rows(now: datetime) -> list[dict[str, Any]]:
    """Строки таблицы очереди. Пустая очередь — честный пустой список."""
    from apps.identity.services.global_tenant import get_global_bot_tenant

    tasks = open_tasks()
    if not tasks:
        return []
    sentinel_id = get_global_bot_tenant().id
    rows = []
    for task in tasks:
        rows.append(
            {
                "task": task,
                "age": format_age(task.created_at, now),
                "addressee": task.addressee or "НЕ НАЗНАЧЕН",
                "client": client_label(task.bot_user),
                "salon": (
                    "глобальный диалог" if task.tenant_id == sentinel_id else task.tenant.name
                ),
                "escalated": task.pickup_escalated_at is not None,
                "claimed": task.claimed_at is not None,
                "dialogs": muted_dialogs(task, sentinel_id=sentinel_id),
            }
        )
    return rows


def queue_view(model_admin: "ModelAdmin", request: HttpRequest) -> HttpResponse:
    """GET — экран очереди; POST — действие «взять» / «закрыть».

    Права проверяются здесь, а не доверяются обёртке: ``admin_view``
    требует лишь staff-статус, а «смотрящий» — тоже staff.
    """
    if not model_admin.has_view_permission(request):
        raise PermissionDenied
    if request.method == "POST":
        if not model_admin.has_change_permission(request):
            raise PermissionDenied
        _handle_action(request)
        return HttpResponseRedirect(request.path)

    now = timezone.now()
    rows = build_rows(now)
    context = {
        **model_admin.admin_site.each_context(request),
        "title": "Очередь handoff",
        "rows": rows,
        "open_count": len(rows),
        # Сортировка — самые старые сверху, так что самая старая — первая.
        "oldest": rows[0] if rows else None,
        "can_change": model_admin.has_change_permission(request),
    }
    return render(request, "adminconsole/handoff_queue.html", context)


def _handle_action(request: HttpRequest) -> None:
    """Разобрать POST и выполнить действие через сервисный слой."""
    action = (request.POST.get("action") or "").strip()
    task_id = (request.POST.get("task_id") or "").strip()
    task = AdminTask.all_tenants.filter(pk=task_id).first() if task_id else None
    if task is None:
        messages.error(request, "Задача не найдена — возможно, её уже закрыл кто-то другой.")
        return
    if action == ACTION_CLAIM:
        _claim(request, task)
    elif action == ACTION_RESOLVE:
        _resolve(request, task)
    else:
        messages.error(request, "Неизвестное действие с задачей.")


def _claim(request: HttpRequest, task: AdminTask) -> None:
    """«Взять на себя» — через :func:`claim`, не правкой ``assigned_to``."""
    if task.status not in OPEN_STATUSES:
        messages.info(request, "Задача уже закрыта — брать нечего.")
        return
    # Права проверены в queue_view: сюда аноним не доходит.
    if claim(task, cast("User", request.user)):
        _journal(request, task, "взял(а) задачу на себя")
        messages.success(request, f"Задача {str(task.id)[:8]} — теперь на вас.")
    else:
        messages.info(
            request,
            f"Задачу {str(task.id)[:8]} уже взял {task.addressee or 'другой оператор'}.",
        )


def _resolve(request: HttpRequest, task: AdminTask) -> None:
    """«Закрыть» — через сервис: статус, отметки, возврат диалога боту, аудит."""
    if task.status not in OPEN_STATUSES:
        messages.info(request, "Задача уже закрыта.")
        return
    note = (request.POST.get("resolution_note") or "").strip()[:2000]
    # Сервис требует tenant в scope; экран кросс-тенантный, поэтому scope
    # берётся из самой задачи — то же правило, что в AdminTaskAdmin.save_model.
    with tenant_scope(task.tenant):
        fresh = AdminTask.all_tenants.get(pk=task.pk)
        resolve_admin_task(fresh, resolution_note=note)
    _journal(request, fresh, "закрыл(а) задачу")
    messages.success(request, f"Задача {str(task.id)[:8]} закрыта — бот снова отвечает.")


def _journal(request: HttpRequest, task: AdminTask, verb: str) -> None:
    """Строка в журнале админки с автором действия.

    Идёт через ``LogEntryManager.log_actions`` — единственную точку,
    которую оборачивает :mod:`apps.adminconsole.journal`, — поэтому запись
    в ``AuditLog`` появляется в той же транзакции, что и само действие.
    """
    user_pk = request.user.pk
    if user_pk is None:
        # Не бывает: действия пропускает только вошедший правящий.
        return
    LogEntry.objects.log_actions(
        user_pk,
        [task],
        CHANGE,
        f"очередь handoff: {verb}",
        single_object=True,
    )
