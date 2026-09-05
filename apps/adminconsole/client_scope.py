"""Переписку и профиль открывает пропуск, а не роль (DRF-1514).

### Что было

DRF-1495 выдал роли «смотрящий» право ``view_*`` на всё, что
зарегистрировано в админке. Экраны ``conversations`` и ``identity``
кросс-тенантные по построению (``get_queryset`` берёт ``all_tenants``),
поэтому право «смотреть» означало: открыть
``/admin/conversations/message/``, ввести слово в поиск — и читать
переписку клиентов всех салонов. Повод не требовался, и следа не
оставалось: ``LogEntry`` пишется на правку, а просмотр правкой не
является.

05.09.2026 защита среды дважды не дала главному окну прочитать
клиентскую переписку с боевого контура — и разбор инцидента от этого не
сорвался, диагноз собрался на метаданных и логах. У роли «смотрящий»
такой защиты не было вовсе.

### Что стало

Три правила, и каждое проверяется тестом.

1. **Общего списка переписок нет.** Не «пустой список», а честный отказ
   с объяснением и ссылкой на то, что делать дальше
   (:func:`_denied_listing`). Пустая страница врёт: она выглядит как
   «переписок нет», а не как «вам сюда нельзя».
2. **Переписка и профиль открываются по конкретному клиенту**, который
   найден через обращение (``handoff.AdminTask``). Пропуск выдаётся на
   клиента этого обращения — см. ``apps.adminconsole.client_access``.
3. **У каждого просмотра есть причина и след.** Причина вводится до
   открытия, в форме пропуска. След пишется в журнал доступа **до**
   того, как экран отдаст данные.

### Три вида экранов

``SCOPED_SCREENS`` — переписка и профиль. Список закрыт без пропуска,
а с пропуском сужен до его клиентов (``get_queryset``). Карточка
открывается только по клиенту пропуска.

``QUEUE_SCREEN`` — очередь обращений. Список **остаётся открытым**: без
него сотрудник не найдёт обращение, с которым работает, и задача
превратилась бы в «работать нельзя». В списке только метаданные —
тип, срочность, статус, салон, время. Карточка обращения несёт
``transcript_snapshot``, то есть переписку, — и потому закрыта тем же
пропуском, что и всё остальное.

``HIDDEN_FIELDS`` — поля, которых не видит никто, кроме владельца, ни
при какой причине:

* ``identity.botuser.phone`` — телефон клиента не показываем (DRF-1039);
* ``identity.botuser.context`` — там лежат настройки проактивных
  сообщений про питание (``apps.nutrition_proactive.prefs`` пишет в
  ``context["nutrition_proactive"]``), то есть данные о здоровье;
  152-ФЗ ст. 10 — не показывать ни при какой причине;
* сырые полезные нагрузки ``ingress.webhookjournal.raw_payload``,
  ``events.event.payload``, ``eventbus.domainevent.data`` /
  ``metadata`` / ``actor``, ``replay.replaytrace.pipeline_steps`` —
  это тексты сообщений клиента без всякой привязки к клиенту, сузить
  их пропуском невозможно. Списки этих экранов остаются открытыми
  (канал, идентификатор события, салон, trace_id, время) — на них и
  собирается разбор инцидента.

### Почему обёртки, а не правка чужих ``admin.py``

Ровно по той же причине, что и в
``apps.adminconsole.secrets_policy``: ``apps/conversations/``,
``apps/identity/``, ``apps/handoff/`` — территория соседних задач, и
трогать там файлы нельзя. Обёртывание методов на экземпляре
``ModelAdmin`` в ``AdminconsoleConfig.ready()`` даёт тот же результат,
не занимая ни строки в чужом файле, и снимается одной строкой.

### Владелец

``is_unrestricted`` пропускает суперпользователя без изменений. Роли
были заведены как надстройка над суперпользователем, а не как замена
ему; настройки тенанта и разбор на уровне платформы остаются у
владельца.
"""

from __future__ import annotations

import logging
from typing import Any

from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse

from apps.adminconsole.client_access import (
    active_grants,
    grant_for_client,
    granted_client_ids,
    is_unrestricted,
    record_denial,
    record_view,
)
from apps.adminconsole.secrets_policy import strip_fields

logger = logging.getLogger(__name__)

#: Экраны переписки и профиля: ``<app>.<model>`` → путь до id клиента.
SCOPED_SCREENS: dict[str, str] = {
    "conversations.conversation": "bot_user_id",
    "conversations.message": "conversation__bot_user_id",
    "identity.botuser": "id",
    "identity.clientprofile": "bot_user_id",
    "consent.consentrecord": "bot_user_id",
}

#: Очередь обращений: список открыт, карточка — по пропуску.
QUEUE_SCREEN = "handoff.admintask"
QUEUE_CLIENT_PATH = "bot_user_id"

#: Поля, которые не показываются никому, кроме владельца.
HIDDEN_FIELDS: dict[str, tuple[str, ...]] = {
    "identity.botuser": ("phone", "context"),
    "ingress.webhookjournal": ("raw_payload",),
    "events.event": ("payload",),
    "eventbus.domainevent": ("data", "metadata", "actor"),
    "replay.replaytrace": ("pipeline_steps",),
}

#: Шаблон честного отказа.
DENIED_TEMPLATE = "adminconsole/client_data_denied.html"

_SCOPE_MARKER = "_ayla_client_scope"
_QUEUE_MARKER = "_ayla_client_queue_scope"
_HIDE_MARKER = "_ayla_client_hidden_fields"

_LISTING_DETAIL = "Общий список переписок закрыт: пропуска нет."
_OTHER_CLIENT_DETAIL = "Открыт доступ к другому клиенту."
_NO_GRANT_DETAIL = "Пропуска нет."


def install_client_data_scope() -> list[str]:
    """Навесить политику на зарегистрированные экраны.

    Возвращает список ``<app>.<model>``, к которым она применилась, —
    по нему тест видит, что политика не промахнулась мимо всех.

    Идемпотентна: повторный ``ready()`` (перезагрузка реестра приложений
    в тестах) ничего не оборачивает дважды.
    """
    from django.contrib import admin

    applied: list[str] = []
    # ``_registry`` приватно по имени, но публичной замены нет; к моменту
    # нашего ready() admin.autodiscover уже отработал.
    for model, model_admin in admin.site._registry.items():  # noqa: SLF001
        meta = model._meta  # noqa: SLF001
        label = f"{meta.app_label}.{meta.model_name}"
        touched = False
        if label in SCOPED_SCREENS:
            touched |= _install_scoped(model_admin, label, SCOPED_SCREENS[label])
        elif label == QUEUE_SCREEN:
            touched |= _install_queue(model_admin, label)
        hidden = HIDDEN_FIELDS.get(label)
        if hidden:
            touched |= _install_hidden_fields(model_admin, hidden)
        if touched:
            applied.append(label)
    return sorted(applied)


# ── экраны переписки и профиля ────────────────────────────────────────


def _install_scoped(model_admin: Any, label: str, client_path: str) -> bool:
    """Список — по пропуску и только по его клиентам; карточка — тоже."""
    original_queryset = model_admin.get_queryset
    if getattr(original_queryset, _SCOPE_MARKER, False):
        return False
    original_changelist = model_admin.changelist_view

    def get_queryset(request: Any) -> Any:
        queryset = original_queryset(request)
        user = getattr(request, "user", None)
        if is_unrestricted(user):
            return queryset
        client_ids = granted_client_ids(user)
        if not client_ids:
            # Второй слой поверх отказа в changelist_view: даже если
            # экран позовут мимо него (autocomplete, действие, свой
            # url), строк не будет.
            return queryset.none()
        return queryset.filter(**{f"{client_path}__in": client_ids})

    def changelist_view(request: Any, extra_context: Any = None) -> Any:
        user = getattr(request, "user", None)
        if is_unrestricted(user):
            return original_changelist(request, extra_context)
        grants = list(active_grants(user))
        if not grants:
            record_denial(actor=user, screen=label, detail=_LISTING_DETAIL)
            return _denied_listing(request)
        for grant in grants:
            record_view(actor=user, grant=grant, screen=label)
        _announce_scope(request, grants)
        return original_changelist(request, extra_context)

    setattr(get_queryset, _SCOPE_MARKER, True)
    model_admin.get_queryset = get_queryset
    model_admin.changelist_view = changelist_view
    _wrap_object_views(model_admin, label, client_path, original_queryset)
    return True


def _install_queue(model_admin: Any, label: str) -> bool:
    """Очередь обращений: список открыт, карточка с перепиской — нет."""
    original_queryset = model_admin.get_queryset
    if getattr(model_admin, _QUEUE_MARKER, False):
        return False
    setattr(model_admin, _QUEUE_MARKER, True)
    _wrap_object_views(model_admin, label, QUEUE_CLIENT_PATH, original_queryset)
    return True


def _wrap_object_views(
    model_admin: Any,
    label: str,
    client_path: str,
    original_queryset: Any,
) -> None:
    """Закрыть карточку, историю и удаление одним и тем же пропуском."""
    original_change = model_admin.change_view
    original_history = model_admin.history_view
    original_delete = model_admin.delete_view

    def change_view(
        request: Any,
        object_id: Any,
        form_url: str = "",
        extra_context: Any = None,
    ) -> Any:
        denial = _guard_object(request, label, client_path, object_id, original_queryset)
        return denial or original_change(request, object_id, form_url, extra_context)

    def history_view(request: Any, object_id: Any, extra_context: Any = None) -> Any:
        denial = _guard_object(request, label, client_path, object_id, original_queryset)
        return denial or original_history(request, object_id, extra_context)

    def delete_view(request: Any, object_id: Any, extra_context: Any = None) -> Any:
        denial = _guard_object(request, label, client_path, object_id, original_queryset)
        return denial or original_delete(request, object_id, extra_context)

    model_admin.change_view = change_view
    model_admin.history_view = history_view
    model_admin.delete_view = delete_view


def _guard_object(
    request: Any,
    label: str,
    client_path: str,
    object_id: Any,
    original_queryset: Any,
) -> Any:
    """``None`` — пускаем (и уже записали просмотр). Иначе — отказ."""
    user = getattr(request, "user", None)
    if is_unrestricted(user):
        return None

    client_pk = _client_of(original_queryset(request), client_path, object_id)
    if client_pk is None:
        # Строки нет (или её клиент не читается) — пусть Django ответит
        # своим «объект не найден». Придумывать здесь отказ значило бы
        # подтверждать существование того, чего мы не нашли.
        return None

    grant = grant_for_client(user, client_pk)
    if grant is None:
        detail = _OTHER_CLIENT_DETAIL if granted_client_ids(user) else _NO_GRANT_DETAIL
        record_denial(actor=user, screen=label, object_id=str(object_id), detail=detail)
        return _denied_object(request, label, object_id, detail)

    record_view(actor=user, grant=grant, screen=label, object_id=str(object_id))
    return None


def _client_of(queryset: Any, client_path: str, object_id: Any) -> Any:
    """id клиента этой строки — или ``None``, если строки/клиента нет."""
    try:
        return queryset.filter(pk=object_id).values_list(client_path, flat=True).first()
    except Exception:  # noqa: BLE001 — кривой pk в url; ведём себя как «нет строки»
        logger.debug("adminconsole.client_scope.bad_object_id object_id=%r", object_id)
        return None


# ── поля, которых не видит никто, кроме владельца ─────────────────────


def _install_hidden_fields(model_admin: Any, hidden: tuple[str, ...]) -> bool:
    """Снять поля с формы у всех, кроме суперпользователя."""
    original = model_admin.get_fieldsets
    if getattr(original, _HIDE_MARKER, False):
        return False

    def get_fieldsets(request: Any, obj: Any = None) -> Any:
        fieldsets = original(request, obj)
        if is_unrestricted(getattr(request, "user", None)):
            return fieldsets
        return strip_fields(fieldsets, hidden)

    setattr(get_fieldsets, _HIDE_MARKER, True)
    model_admin.get_fieldsets = get_fieldsets
    return True


# ── честный отказ ─────────────────────────────────────────────────────


def _announce_scope(request: Any, grants: list[Any]) -> None:
    """Сказать в списке, чей он. Молчаливое сужение читается как «пусто»."""
    from django.contrib import messages

    for grant in grants[:5]:
        messages.info(
            request,
            f"Показан только клиент {grant.client_label or grant.client_id} "
            f"(салон {grant.tenant_slug or '—'}), доступ открыт до "
            f"{grant.expires_at:%H:%M %d.%m} по причине: {grant.reason}",
        )


def _denied_listing(request: Any) -> TemplateResponse:
    return _denied(
        request,
        headline="Общий список переписок закрыт",
        paragraphs=[
            "Листать разговоры клиентов всех салонов нельзя — ни по одному "
            "поводу. Это не настройка фильтра: списка чужих переписок в "
            "админке больше нет.",
            "Переписка и профиль открываются по одному клиенту. Найдите в "
            "очереди обращение, с которым вы работаете, укажите причину "
            "просмотра — и данные этого клиента откроются, включая этот "
            "самый список.",
        ],
    )


def _denied_object(request: Any, label: str, object_id: Any, detail: str) -> TemplateResponse:
    if detail == _OTHER_CLIENT_DETAIL:
        paragraphs = [
            "У вас открыт доступ к другому клиенту. Один пропуск — один "
            "клиент и одно обращение; так в журнале доступа видно, что "
            "именно вы смотрели и зачем.",
            "Если это обращение тоже ваше — откройте по нему доступ и укажите причину.",
        ]
    else:
        paragraphs = [
            "Здесь переписка клиента, а причина просмотра не указана. "
            "Причина вводится до открытия, а не после: журнал доступа "
            "должен объяснять просмотр, а не фиксировать его задним числом.",
        ]
    return _denied(
        request,
        headline="Нужен доступ по обращению",
        paragraphs=paragraphs,
        task_id=object_id if label == QUEUE_SCREEN else None,
    )


def _denied(
    request: Any,
    *,
    headline: str,
    paragraphs: list[str],
    task_id: Any = None,
) -> TemplateResponse:
    """Страница отказа внутри админки. 403 и объяснение, а не пустой список."""
    from django.contrib import admin

    context = dict(admin.site.each_context(request))
    context.update(
        title=headline,
        headline=headline,
        paragraphs=paragraphs,
        queue_url=_reverse("admin:handoff_admintask_changelist"),
        grant_url=_grant_url(task_id),
        journal_url=_reverse("admin:adminconsole_clientdataaccesslog_changelist"),
    )
    return TemplateResponse(request, DENIED_TEMPLATE, context, status=403)


def _grant_url(task_id: Any = None) -> str:
    """Ссылка на форму пропуска; с обращением — уже подставленным."""
    url = _reverse("admin:adminconsole_clientdataaccessgrant_add")
    if url and task_id:
        return f"{url}?admin_task={task_id}"
    return url


def _reverse(name: str) -> str:
    try:
        return reverse(name)
    except NoReverseMatch:  # pragma: no cover — экран снят с регистрации
        return ""
