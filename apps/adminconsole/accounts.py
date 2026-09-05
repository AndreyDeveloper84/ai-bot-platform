"""Жизненный цикл учётной записи админки (DRF-1495, эпик DRF-75).

Заводится, отзывается — и то и другое оставляет след в
``apps.audit.AuditLog``.

### Почему не через экран админки

Право заводить учётные записи — это право выдать себе любое другое
право. Поэтому ``auth`` закрыт для обеих ролей (см.
``apps.adminconsole.roles``), и записи заводит владелец командой
из-под суперпользователя. Экран пользователей остаётся суперпользователю
Django, каким и был.

### Почему пароль не приходит аргументом

Аргумент командной строки виден в ``ps`` и остаётся в истории оболочки.
Пароль читается из ``AYLA_ADMIN_PASSWORD`` — как ``createsuperuser``
читает ``DJANGO_SUPERUSER_PASSWORD``, и раннбук уже учит передавать такое
через ``docker compose exec -e``. Без переменной запись заводится с
непригодным паролем: войти нельзя, пока владелец не задаст пароль через
``manage.py changepassword``. Это рабочий сценарий, а не отказ.

### Почему отзыв не удаляет строку

Журнал ссылается на автора по id и по имени. Удалить пользователя —
значит превратить прошлые записи журнала в «кто-то». Отзыв гасит
доступ: ``is_active=False``, ``is_staff=False``, группы сняты, живые
сессии этого человека удалены. Строка остаётся, чтобы журнал остался
читаемым.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.adminconsole.roles import ROLE_GROUPS, sync_admin_roles
from apps.audit.services import write_audit

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from django.contrib.auth.models import AbstractUser

#: Переменная окружения с паролем новой записи. Не аргумент — см. docstring.
PASSWORD_ENV_VAR = "AYLA_ADMIN_PASSWORD"

#: Имена, под которыми обычно заводят «общую учётку на всех».
#:
#: Общая запись обессмысливает журнал: он честно скажет «изменил admin»,
#: а кто такой admin — не скажет никто. Границы задачи запрещают такие
#: записи, и запрет должен уметь сработать, а не только быть написанным.
SHARED_USERNAMES: frozenset[str] = frozenset(
    {
        "admin",
        "administrator",
        "ayla",
        "bot",
        "common",
        "demo",
        "manager",
        "operator",
        "ops",
        "owner",
        "root",
        "salon",
        "shared",
        "staff",
        "superuser",
        "support",
        "team",
        "test",
        "user",
    }
)

#: Короче этого имя не отличает человека от роли.
MIN_USERNAME_LENGTH = 3


class AccountError(Exception):
    """Учётную запись завести/отозвать нельзя, и вот почему."""


def _check_username(username: str) -> str:
    name = (username or "").strip()
    if not name:
        raise AccountError("Имя учётной записи пустое.")
    if len(name) < MIN_USERNAME_LENGTH:
        raise AccountError(
            f"Имя {name!r} короче {MIN_USERNAME_LENGTH} символов — "
            "по такому не опознать человека в журнале."
        )
    if name.casefold() in SHARED_USERNAMES:
        raise AccountError(
            f"{name!r} — общее имя. Общие учётные записи запрещены: журнал "
            "покажет, что правку сделал этот логин, и не покажет, кто это был. "
            "Заведите запись на человека."
        )
    return name


def _flush_sessions(user_pk: object) -> int:
    """Удалить живые сессии этого пользователя. Возвращает счётчик.

    Без этого отзыв не отзывает: ``is_active=False`` проверяется при
    входе, а у уже вошедшего в куке лежит валидный ключ сессии, и он
    продолжает работать до истечения срока.

    Сессии не индексированы по пользователю — Django хранит id внутри
    закодированного содержимого, — поэтому перебираем непросроченные.
    Для внутреннего инструмента с горсткой сессий это дешевле, чем
    заводить свою таблицу.
    """
    removed = 0
    target = str(user_pk)
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == target:
            session.delete()
            removed += 1
    return removed


def grant_admin_account(
    *,
    username: str,
    role: str,
    email: str = "",
    actor_username: str = "",
) -> tuple["AbstractUser", bool]:
    """Завести (или перевести в роль) учётную запись админки.

    Возвращает ``(пользователь, создан_ли)``.

    Никогда не выдаёт ``is_superuser`` — суперпользователь заводится
    только ``createsuperuser`` руками владельца.
    """
    name = _check_username(username)
    if role not in ROLE_GROUPS:
        known = ", ".join(sorted(ROLE_GROUPS))
        raise AccountError(f"Неизвестная роль {role!r}. Известные: {known}.")

    # Роли должны существовать до выдачи, и синхронизация идемпотентна —
    # дешевле выполнить её, чем требовать помнить про отдельный шаг.
    sync_admin_roles()

    from django.contrib.auth.models import Group

    group = Group.objects.get(name=ROLE_GROUPS[role])
    user_model = get_user_model()

    user, created = user_model.objects.get_or_create(
        username=name,
        defaults={"email": email},
    )
    if not created and user.is_superuser:
        raise AccountError(
            f"{name!r} — суперпользователь. Роли им не управляют: понизить "
            "суперпользователя до роли должен владелец осознанно, через shell."
        )

    if email:
        user.email = email
    user.is_staff = True
    user.is_superuser = False
    user.is_active = True

    password = os.environ.get(PASSWORD_ENV_VAR, "")
    password_set = False
    if password:
        user.set_password(password)
        password_set = True
    elif created:
        # Пароля нет — запись заводится нерабочей намеренно: владелец
        # задаст его через `manage.py changepassword`.
        user.set_unusable_password()
    user.save()
    user.groups.set([group])

    write_audit(
        "admin.account.granted",
        target="auth.User",
        payload={
            "username": name,
            "role": role,
            "group": group.name,
            "created": created,
            "password_set": password_set,
            "actor_username": actor_username,
        },
    )
    return user, created


def revoke_admin_account(*, username: str, actor_username: str = "") -> int:
    """Отозвать доступ. Возвращает число погашенных сессий.

    Строка пользователя остаётся — см. docstring модуля.
    """
    name = (username or "").strip()
    user_model = get_user_model()
    try:
        user = user_model.objects.get(username=name)
    except user_model.DoesNotExist as exc:
        raise AccountError(f"Учётной записи {name!r} нет.") from exc

    if user.is_superuser:
        raise AccountError(
            f"{name!r} — суперпользователь. Эта команда его не трогает: "
            "отозвать доступ владельца можно только осознанно, через shell, "
            "иначе первым же отзывом контур останется без администратора."
        )

    had_groups = sorted(user.groups.values_list("name", flat=True))
    was_active = user.is_active
    user.is_active = False
    user.is_staff = False
    user.save(update_fields=["is_active", "is_staff"])
    user.groups.clear()
    flushed = _flush_sessions(user.pk)

    write_audit(
        "admin.account.revoked",
        target="auth.User",
        payload={
            "username": name,
            "was_active": was_active,
            "groups_removed": had_groups,
            "sessions_flushed": flushed,
            "actor_username": actor_username,
        },
    )
    return flushed
