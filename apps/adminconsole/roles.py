"""Роли админки бота: смотрящий и правящий (DRF-1495, эпик DRF-75).

До этой задачи ролей не было ни одной. `docs/runbooks/admin-access.md`
знал единственный способ завести учётную запись — `createsuperuser`, то
есть каждая заведённая запись была суперпользователем. Просмотр очереди
handoff и правка мастера были одним и тем же правом, потому что права
были одно.

Здесь их два.

``ayla-viewer`` (смотрящий)
    Право ``view_*`` на всё, что зарегистрировано в админке. Открыть
    очередь handoff, прочитать диалог, посмотреть журнал — да. Нажать
    «сохранить» — нет нигде.

``ayla-editor`` (правящий)
    Всё, что видит смотрящий, плюс ``add`` / ``change`` / ``delete`` на
    прикладных данных: каталог, брони, задачи handoff, база знаний,
    персона, промо, расписание.

Обе роли — это ``django.contrib.auth.Group``. Ничего своего: Django
проверяет их сам в ``ModelAdmin.has_*_permission``, и любой новый
``ModelAdmin`` попадает под них без единой строки в нём.

### Что не достаётся никому из ролей

Два списка ниже — не гигиена, а граница.

``ROLE_DENIED_APP_LABELS`` — приложения, которых роли не видят вовсе.
``auth`` в этом списке по двум причинам, и каждой хватило бы отдельно:

* правящий с правом ``auth.change_user`` дописывает себя в группу
  суперпользователей за один запрос — роль, умеющая себя повысить, не
  роль;
* форма пользователя Django показывает хеш пароля
  (``ReadOnlyPasswordHashField``), а секретам в интерфейсе места нет.

Учётные записи заводит и отзывает владелец — командами
``admin_account_grant`` / ``admin_account_revoke`` из-под суперпользователя,
не через экран админки.

``EDITOR_DENIED_APP_LABELS`` / ``EDITOR_DENIED_MODELS`` — то, что
смотрится, но не правится никем, кроме суперпользователя:

* журналы (``audit``, ``admin.logentry``, ``events``, ``eventbus``,
  ``ingress``, ``replay``, ``consent``) — правка следа обессмысливает
  след;
* ``django_celery_beat`` — расписание воркеров это эксплуатация, а не
  прикладные данные;
* ``tenancy.tenant`` — там лежат токен бота и вебхук-секрет тенанта;
* ``conversations.message`` — переписка клиента правке не подлежит,
  только чтению.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from django.db.models import Model

VIEWER_GROUP = "ayla-viewer"
EDITOR_GROUP = "ayla-editor"

#: Имя роли в командах (`--role viewer`) → имя группы в базе.
ROLE_GROUPS: dict[str, str] = {
    "viewer": VIEWER_GROUP,
    "editor": EDITOR_GROUP,
}

#: Приложения, которых не видит ни одна роль (см. модуль-docstring).
ROLE_DENIED_APP_LABELS: frozenset[str] = frozenset({"auth", "sessions"})

#: Приложения, которые смотрящий и правящий видят, но не правят.
EDITOR_DENIED_APP_LABELS: frozenset[str] = frozenset(
    {
        "admin",
        "audit",
        "consent",
        "django_celery_beat",
        "eventbus",
        "events",
        "ingress",
        "replay",
    }
)

#: Отдельные модели, которые смотрящий и правящий видят, но не правят.
EDITOR_DENIED_MODELS: frozenset[str] = frozenset(
    {
        "conversations.message",
        "tenancy.tenant",
    }
)

_WRITE_VERBS = ("add", "change", "delete")


def model_label(model: type["Model"]) -> str:
    """``<app_label>.<model_name>`` — ключ обоих списков-исключений."""
    meta = model._meta  # noqa: SLF001 — Django's own public-by-convention API
    return f"{meta.app_label}.{meta.model_name}"


def is_visible_to_roles(model: type["Model"]) -> bool:
    """Видит ли модель хоть одна роль."""
    return model._meta.app_label not in ROLE_DENIED_APP_LABELS  # noqa: SLF001


def is_editable_by_editor(model: type["Model"]) -> bool:
    """Правит ли модель правящий."""
    if not is_visible_to_roles(model):
        return False
    if model._meta.app_label in EDITOR_DENIED_APP_LABELS:  # noqa: SLF001
        return False
    return model_label(model) not in EDITOR_DENIED_MODELS


def sync_admin_roles() -> dict[str, int]:
    """Привести группы ролей в соответствие с тем, что сейчас в админке.

    Идемпотентна и полна: права выставляются через ``set()``, а не
    ``add()``, — модель, снятая с регистрации в админке, теряет права у
    обеих ролей на следующем прогоне, а не остаётся висеть.

    Обходит ``admin.site`` вместо списка моделей в коде: новая
    ``ModelAdmin``, которую заведёт подзадача 2-6, попадёт под роли сама.

    Возвращает ``{имя группы: сколько прав выдано}`` — команда печатает
    это, чтобы отличить «синхронизировано» от «молча не нашло прав».
    """
    from django.contrib import admin
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType

    viewer, _ = Group.objects.get_or_create(name=VIEWER_GROUP)
    editor, _ = Group.objects.get_or_create(name=EDITOR_GROUP)

    viewer_perms: list[Permission] = []
    editor_perms: list[Permission] = []

    # ``_registry`` приватно по имени, но это единственный способ
    # спросить у AdminSite «что в тебе зарегистрировано», и он стабилен
    # с Django 1.x. Публичной замены нет.
    for model in admin.site._registry:  # noqa: SLF001
        if not is_visible_to_roles(model):
            continue
        meta = model._meta  # noqa: SLF001
        content_type = ContentType.objects.get_for_model(model)
        by_codename = {
            perm.codename: perm for perm in Permission.objects.filter(content_type=content_type)
        }

        view_perm = by_codename.get(f"view_{meta.model_name}")
        if view_perm is not None:
            viewer_perms.append(view_perm)
            editor_perms.append(view_perm)

        if not is_editable_by_editor(model):
            continue
        for verb in _WRITE_VERBS:
            write_perm = by_codename.get(f"{verb}_{meta.model_name}")
            if write_perm is not None:
                editor_perms.append(write_perm)

    viewer.permissions.set(viewer_perms)
    editor.permissions.set(editor_perms)
    return {VIEWER_GROUP: len(viewer_perms), EDITOR_GROUP: len(editor_perms)}
