"""Журнал действий админки: кто что изменил и когда (DRF-1495).

### Зачем

Админка бота ходит по живым данным пилота. Правка без следа — это
расхождение, которое потом невозможно объяснить: строка изменилась,
никто не знает кем и зачем, и разбор упирается в догадки. Журнал
превращает такой разбор в чтение.

### Где перехватываем и почему именно там

Django на каждое действие через ``/admin/`` пишет
``django.contrib.admin.models.LogEntry``. Строки писались и до этой
задачи; их просто никто не читал, потому что ``LogEntry`` нигде не был
зарегистрирован в админке.

Единственная точка, через которую Django 5.2 создаёт эти строки, —
``LogEntryManager.log_actions``. Мы оборачиваем именно её.

Первая версия подписывалась на ``post_save`` у ``LogEntry``, и это была
ошибка, которую поймало ревью: ``log_actions`` вызывает
``instance.save()`` только когда в выборке ровно один объект, а на
нескольких уходит в ``bulk_create`` — сигнала нет. То есть журнал молчал
ровно на самой разрушительной операции («удалить выбранные»), и молчал
тем надёжнее, чем больше строк выделили. Обёртка вокруг ``log_actions``
покрывает оба пути одинаково.

Перехват на уровне менеджера, а не в каждой ``ModelAdmin``, выбран по
той же причине, что и раньше: экраны мастеров, пользователей и записей
приезжают подзадачами 2-6 эпика. Любая ``ModelAdmin``, которую там
заведут, попадёт в журнал сама, без строчки кода в ней.

### Чего журнал не ловит

Записи в базу мимо ``/admin/`` — команды, Celery-задачи, REST админки —
пишут свой аудит сами (``apps.admin_api``, ``apps.catalog.signals`` и
далее). Здесь только то, что сделал человек руками через экран.

Кастомное admin-действие, которое меняет данные и **не** зовёт
``log_deletions`` / ``log_changes``, в журнал не попадёт. Поэтому каждое
действие обязано объявлять ``permissions=`` — это проверяется разом для
всех зарегистрированных экранов в
``apps/adminconsole/tests/test_admin_actions.py``.

### Секретов в журнале нет

``change_message`` от Django перечисляет **имена** изменённых полей, а не
значения. ``object_repr`` — это ``str()`` объекта, подрезанный до
:data:`OBJECT_REPR_MAX_CHARS`; поля-секреты в формы админки не попадают
вовсе (``apps.adminconsole.secrets_policy``).

### Почему запись журнала синхронная

``write_audit`` вызывается внутри той же транзакции, в которой админка
сохраняет объект. Это осознанно: строка журнала и правка коммитятся
вместе, поэтому в журнале не бывает записей о правках, которых не
случилось, — и не бывает правок без записи. Обратная сторона: если сама
вставка в ``audit_auditlog`` не пройдёт, на Postgres транзакция окажется
испорчена и сохранение в админке откатится с ошибкой. Для инструмента,
который ходит по живым данным пилота, «не сохранилось и видно почему»
лучше, чем «сохранилось, а кто — неизвестно».

Всё, что делается **до** ``write_audit``, обёрнуто в ``try`` и уронить
сохранение не может: разыменование FK-ссылок на пользователя и content
type — это запросы, и они могут не найти строку
(``remove_stale_contenttypes`` между записью и чтением).
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from apps.audit.services import write_audit

logger = logging.getLogger(__name__)

#: На сколько символов подрезается ``object_repr``.
OBJECT_REPR_MAX_CHARS = 200

#: Имя константы ``LogEntry.action_flag`` → глагол журнала.
_FLAG_TO_ACTION_NAME = {
    "ADDITION": "admin.object.created",
    "CHANGE": "admin.object.updated",
    "DELETION": "admin.object.deleted",
}

#: Метка на обёртке, чтобы повторный ``ready()`` не обернул её дважды.
_WRAPPED_MARKER = "_ayla_admin_journal"


@functools.lru_cache(maxsize=1)
def build_action_map() -> dict[int, str]:
    """``{action_flag: имя действия}``.

    Кэшируется: карта из трёх пар не меняется за жизнь процесса, а
    спрашивается на каждое сохранение в админке.
    """
    from django.contrib.admin import models as admin_models

    return {
        getattr(admin_models, flag_name): action
        for flag_name, action in _FLAG_TO_ACTION_NAME.items()
    }


def install_admin_journal() -> None:
    """Обернуть ``LogEntryManager.log_actions`` записью в журнал.

    Идемпотентна: повторный вызов (перезагрузка реестра приложений в
    тестах) ничего не удваивает.
    """
    from django.contrib.admin.models import LogEntryManager

    original = LogEntryManager.log_actions
    if getattr(original, _WRAPPED_MARKER, False):
        return

    @functools.wraps(original)
    def log_actions(
        self: Any,
        user_id: Any,
        queryset: Any,
        action_flag: int,
        change_message: Any = "",
        *,
        single_object: bool = False,
    ) -> Any:
        # mypy сводит ``original`` к перегрузкам django-stubs и не узнаёт
        # позиционные аргументы обёртки; вызов повторяет сигнатуру
        # Django 5.2 один в один.
        entries = original(  # type: ignore[call-overload]
            self,
            user_id,
            queryset,
            action_flag,
            change_message,
            single_object=single_object,
        )
        record_admin_entries(entries)
        return entries

    setattr(log_actions, _WRAPPED_MARKER, True)
    LogEntryManager.log_actions = log_actions  # type: ignore[method-assign]


def record_admin_entries(entries: Any) -> None:
    """Записать в ``AuditLog`` каждую строку, созданную ``log_actions``.

    ``log_actions`` возвращает либо один ``LogEntry`` (``single_object``),
    либо список. Приводим к списку и идём по нему — иначе групповое
    удаление осталось бы неучтённым, как это и было в первой версии.
    """
    if entries is None:
        return
    batch = [entries] if not isinstance(entries, (list, tuple)) else list(entries)
    if not batch:
        return

    username = _resolve_username(batch[0].user_id)
    for entry in batch:
        _record_one(entry, username)


def _resolve_username(user_id: Any) -> str:
    """Имя автора одним запросом на всю пачку. Пусто — если не нашли."""
    if user_id is None:
        return ""
    try:
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        return (
            user_model.objects.filter(pk=user_id)
            .values_list(user_model.USERNAME_FIELD, flat=True)
            .first()
            or ""
        )
    except Exception:  # noqa: BLE001 — журнал не должен ронять сохранение
        logger.exception("adminconsole.journal.username_lookup_failed user_id=%s", user_id)
        return ""


def _record_one(entry: Any, username: str) -> None:
    """Одна строка ``LogEntry`` → одна строка ``AuditLog``."""
    try:
        action = build_action_map().get(entry.action_flag, "admin.object.changed")
        target = _resolve_target(entry)
        payload = {
            # Автор в двух видах: id для связи, имя — чтобы строка журнала
            # читалась без второго запроса, даже если запись потом отозвана.
            "actor_pk": entry.user_id,
            "actor_username": username,
            "model": target,
            "object_id": entry.object_id or "",
            "object_repr": (entry.object_repr or "")[:OBJECT_REPR_MAX_CHARS],
            # Перечень имён изменённых полей. Значений здесь нет — см. docstring.
            "change_message": entry.get_change_message(),
            "source": "django_admin",
        }
    except Exception:  # noqa: BLE001 — см. «Почему запись журнала синхронная»
        logger.exception(
            "adminconsole.journal.payload_build_failed entry=%s", getattr(entry, "pk", None)
        )
        return

    # ``actor_id`` намеренно не передаётся: колонка — UUIDField, а pk
    # пользователя Django целочисленный, так что write_audit всё равно
    # сложил бы его в payload['actor_id_raw'] дублем к actor_pk выше.
    # Искать автора надо по payload или на /admin/admin/logentry/.
    write_audit(action, target=target, target_id=entry.object_id or None, payload=payload)


def _resolve_target(entry: Any) -> str:
    """``<app_label>.<model>`` для строки журнала.

    ``content_type`` — ленивая FK-ссылка: строка типа могла исчезнуть
    (``remove_stale_contenttypes``), и разыменование бросит. Ловим здесь,
    чтобы не потерять всю запись из-за пропавшего ярлыка.
    """
    try:
        content_type = entry.content_type
    except Exception:  # noqa: BLE001
        return "unknown"
    if content_type is None:
        return "unknown"
    return f"{content_type.app_label}.{content_type.model}"
