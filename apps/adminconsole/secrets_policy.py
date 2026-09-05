"""Поля-секреты не попадают ни в одну форму админки (DRF-1495).

### Дыра, которую это закрывает

Первая версия задачи прятала токен и вебхук-секрет тенанта, подменив
виджеты формы на ``PasswordInput(render_value=False)``. Ревью показало,
что этого мало, и мало ровно для той аудитории, ради которой задача и
делается.

Django рисует форму изменения двумя разными способами. Если у зашедшего
есть право на правку — работает наша форма, и виджет решает, что попадёт
в разметку. Если права нет, ``ModelAdmin._changeform_view`` кладёт
**все** поля из ``get_fieldsets()`` в ``readonly_fields``, а
``AdminReadonlyField`` печатает значение из модели напрямую: виджет там
не спрашивают (кроме единственного случая ``read_only=True``, который
существует для ``ReadOnlyPasswordHashWidget``).

Обе новые роли — смотрящий и правящий — права на правку тенанта не
имеют: ``tenancy.tenant`` лежит в ``EDITOR_DENIED_MODELS`` именно
потому, что там секреты. Значит обе получали read-only отрисовку и
видели полный токен BotFather открытым текстом. Проверка задачи это не
поймала, потому что тест ходил суперпользователем — единственным, кому и
так ничего не грозило.

То же самое и без всякой формы-обёртки: ``CatalogMasterAdmin`` запрещает
правку всем (``_MirrorAdminBase.has_change_permission`` → ``False``),
поэтому его форму **каждый** видит read-only — вместе с
``invite_token``, одноразовым ключом привязки мастера.

### Что делает этот модуль

Держит один список «какие поля каких моделей — секреты» и снимает их с
формы у всех, кто не имеет права эту модель править. Владелец, который
секрет задаёт, право имеет — у него поля остаются, и там уже работает
``PasswordInput`` из ``apps/tenancy/admin.py``.

Политика ставится один раз в ``AdminconsoleConfig.ready()`` поверх уже
зарегистрированных ``ModelAdmin``. Экраны чужих приложений при этом не
редактируются: ``apps/catalog/**`` правится параллельной задачей
(DRF-1494), и трогать там файлы нельзя. Оборачивание ``get_fieldsets``
на экземпляре даёт тот же результат, ничего не занимая в чужом файле, и
снимается одной строкой, когда владелец каталога замаскирует
``invite_token`` у себя.

### Как добавить поле

Дописать в :data:`SECRET_FIELDS`. Экран может ещё не существовать —
запись про незарегистрированную модель просто не сработает и не
помешает. Тест ``test_secrets_policy.py`` проверяет, что всё
перечисленное действительно снимается.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: ``<app_label>.<model_name>`` → поля, которых не должно быть в форме
#: у того, кто не имеет права править эту модель.
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    # Токен BotFather и вебхук-секрет тенанта. Задаются владельцем через
    # форму с PasswordInput; всем остальным не показываются вовсе.
    "tenancy.tenant": ("telegram_bot_token", "telegram_webhook_secret"),
    # Одноразовый ключ привязки мастера: по нему клеймится приглашение
    # (``apps/master_api/auth.py`` → ``validate_invite_token``). Экран
    # каталога read-only для всех, поэтому его видели все.
    # Территория DRF-1494 — правится отсюда, а не в apps/catalog/.
    "catalog.catalogmaster": ("invite_token",),
}

#: Метка на обёртке, чтобы повторный ``ready()`` не обернул её дважды.
_WRAPPED_MARKER = "_ayla_secret_policy"


def install_secret_field_policy() -> list[str]:
    """Навесить политику на зарегистрированные экраны.

    Возвращает список ``<app_label>.<model>``, к которым она применилась, —
    команда и тест по нему видят, что политика не промахнулась мимо всех.

    Идемпотентна.
    """
    from django.contrib import admin

    applied: list[str] = []
    # ``_registry`` приватно по имени, но публичной замены нет; к моменту
    # ready() нашего приложения admin.autodiscover уже отработал
    # (django.contrib.admin стоит первым в INSTALLED_APPS).
    for model, model_admin in admin.site._registry.items():  # noqa: SLF001
        meta = model._meta  # noqa: SLF001
        label = f"{meta.app_label}.{meta.model_name}"
        secret_fields = SECRET_FIELDS.get(label)
        if not secret_fields:
            continue
        if _wrap_get_fieldsets(model_admin, secret_fields):
            applied.append(label)
    return sorted(applied)


def _wrap_get_fieldsets(model_admin: Any, secret_fields: tuple[str, ...]) -> bool:
    """Обернуть ``get_fieldsets`` экземпляра. ``False`` — уже обёрнут."""
    original = model_admin.get_fieldsets
    if getattr(original, _WRAPPED_MARKER, False):
        return False

    def get_fieldsets(request: Any, obj: Any = None) -> Any:
        fieldsets = original(request, obj)
        try:
            may_change = model_admin.has_change_permission(request, obj)
        except Exception:  # noqa: BLE001 — при сомнении прячем
            may_change = False
        if may_change:
            # Право править есть — работает форма экрана, а в ней
            # секретные поля уже под PasswordInput.
            return fieldsets
        return _strip_fields(fieldsets, secret_fields)

    setattr(get_fieldsets, _WRAPPED_MARKER, True)
    model_admin.get_fieldsets = get_fieldsets
    return True


def _strip_fields(fieldsets: Any, secret_fields: tuple[str, ...]) -> Any:
    """Вернуть fieldsets без перечисленных полей.

    Django допускает вложенные кортежи внутри ``fields`` (поля в одну
    строку), поэтому чистим рекурсивно. Секция, из которой всё вычистили,
    убирается целиком — пустой блок с заголовком выглядит как поломка.
    """
    dropped = set(secret_fields)
    result = []
    for name, options in fieldsets:
        fields = _strip_field_list(options.get("fields", ()), dropped)
        if not fields:
            continue
        new_options = dict(options)
        new_options["fields"] = fields
        result.append((name, new_options))
    return result


def _strip_field_list(fields: Any, dropped: set[str]) -> tuple[Any, ...]:
    kept: list[Any] = []
    for field in fields:
        if isinstance(field, (list, tuple)):
            nested = _strip_field_list(field, dropped)
            if nested:
                kept.append(nested)
            continue
        if field in dropped:
            continue
        kept.append(field)
    return tuple(kept)
