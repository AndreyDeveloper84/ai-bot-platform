"""Журнал действий админки: кто что изменил и когда (DRF-1495).

### Зачем

Админка бота ходит по живым данным пилота. Правка без следа — это
расхождение, которое потом невозможно объяснить: строка изменилась,
никто не знает кем и зачем, и разбор упирается в догадки. Журнал
превращает такой разбор в чтение.

### Как

Django на каждое добавление, изменение и удаление, сделанное через
``/admin/``, уже пишет ``django.contrib.admin.models.LogEntry`` — это
делает сама ``ModelAdmin`` в ``log_addition`` / ``log_change`` /
``log_deletion``. Строки писались и до этой задачи; их просто никто не
читал, потому что ``LogEntry`` нигде не был зарегистрирован в админке.

Здесь мы подписываемся на появление ``LogEntry`` и переливаем событие в
``apps.audit.AuditLog`` — общий журнал платформы, у которого уже есть
срок хранения, экран, фильтры и поиск по payload.

Подписка на модель, а не на каждую ``ModelAdmin``, выбрана по одной
причине: экраны мастеров, пользователей и записей приезжают
подзадачами 2-6 эпика. Любая ``ModelAdmin``, которую там заведут,
попадёт в журнал сама, без строчки кода в ней и без шанса про неё
забыть.

### Чего журнал не ловит

Записи в базу мимо ``/admin/`` — команды, Celery-задачи, REST админки —
пишут свой аудит сами (``apps.admin_api``, ``apps.catalog.signals`` и
далее). Здесь только то, что сделал человек руками через экран.

### Секретов в журнале нет

``change_message`` от Django перечисляет **имена** изменённых полей, а не
значения. ``object_repr`` — это ``str()`` объекта; на всякий случай он
подрезан до :data:`OBJECT_REPR_MAX_CHARS`, а модели, чей ``__str__``
показывал бы секрет, до админки не доходят (``Tenant.__repr__``
маскирует токен, а форма тенанта его больше не отдаёт — см.
``apps/tenancy/admin.py``).
"""

from __future__ import annotations

from typing import Any

from apps.audit.services import write_audit

#: На сколько символов подрезается ``object_repr``.
OBJECT_REPR_MAX_CHARS = 200

#: Имя константы ``LogEntry.action_flag`` → глагол журнала.
#:
#: Константы ``ADDITION`` / ``CHANGE`` / ``DELETION`` живут в
#: ``django.contrib.admin.models``; импортируются лениво в
#: :func:`build_action_map`, чтобы модуль был импортируемым до готовности
#: реестра приложений.
_FLAG_TO_ACTION_NAME = {
    "ADDITION": "admin.object.created",
    "CHANGE": "admin.object.updated",
    "DELETION": "admin.object.deleted",
}


def build_action_map() -> dict[int, str]:
    """Вернуть ``{action_flag: имя действия}``."""
    from django.contrib.admin import models as admin_models

    return {
        getattr(admin_models, flag_name): action
        for flag_name, action in _FLAG_TO_ACTION_NAME.items()
    }


def record_admin_action(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    """Перелить свежий ``LogEntry`` в ``AuditLog``.

    Подписан на ``post_save`` в
    :meth:`apps.adminconsole.apps.AdminconsoleConfig.ready`. Реагирует
    только на ``created=True``: ``LogEntry`` в норме не редактируется, а
    если кто-то его тронет, дубль в журнале — шум.

    Исключений не выпускает: ``write_audit`` по контракту их глотает, а
    сам обработчик не делает ничего, что могло бы упасть до вызова.
    Уронить сохранение объекта в админке из-за журнала было бы хуже, чем
    потерять строку журнала, — и потеря видна в Sentry через
    ``audit.write_failed``.
    """
    if not created:
        return

    action = build_action_map().get(instance.action_flag, "admin.object.changed")

    content_type = instance.content_type
    if content_type is not None:
        target = f"{content_type.app_label}.{content_type.model}"
    else:
        target = "unknown"

    object_repr = (instance.object_repr or "")[:OBJECT_REPR_MAX_CHARS]

    payload = {
        # Автор в двух видах: id для связи, имя — чтобы строка журнала
        # читалась без второго запроса, даже если запись потом отозвана.
        "actor_pk": instance.user_id,
        "actor_username": getattr(instance.user, "username", ""),
        "model": target,
        "object_id": instance.object_id or "",
        "object_repr": object_repr,
        # Перечень имён изменённых полей. Значений здесь нет — см. docstring.
        "change_message": instance.get_change_message(),
        "source": "django_admin",
    }

    write_audit(
        action,
        target=target,
        target_id=instance.object_id or None,
        payload=payload,
        actor_id=instance.user_id,
    )
