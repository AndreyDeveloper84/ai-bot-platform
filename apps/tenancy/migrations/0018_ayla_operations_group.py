"""Группа «Ayla Operations» с правом операций платформы.

Решение владельца: право выдаётся ЧЕРЕЗ ГРУППУ, а не поштучно. Поштучная
выдача не переживает второго оператора — про неё забывают ровно тогда,
когда человек нужен срочно.

Почему миграцией, а не командой. В брифе задачи четыре шага уже требуют
похода на сервер, и это названо болью. Ручная команда стала бы пятым:
группа появлялась бы не везде, где применены миграции, и «почему у меня
нет группы» превратилось бы в вопрос к дежурному.

Обратная операция НЕ удаляет группу. Откат миграции — про схему, а группа
к моменту отката может уже нести выданные людям членства и правки
оператора; снести её значило бы отобрать доступы у тех, кого миграция не
заводила. Снимается только само право — ровно то, что эта миграция
выдала.
"""

from __future__ import annotations

from django.db import migrations

GROUP_NAME = "Ayla Operations"
PERM_CODENAME = "platform_operations"
APP_LABEL = "tenancy"
MODEL = "tenant"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type, _ = ContentType.objects.get_or_create(app_label=APP_LABEL, model=MODEL)
    # ``get_or_create``, а не ``get``: порядок создания прав Django
    # относительно этой миграции не гарантирован, и падение здесь
    # означало бы «миграции не применяются» вместо «права ещё нет».
    permission, _ = Permission.objects.get_or_create(
        codename=PERM_CODENAME,
        content_type=content_type,
        defaults={"name": "Операции платформы: действия поперёк салонов"},
    )
    group, _ = Group.objects.get_or_create(name=GROUP_NAME)
    group.permissions.add(permission)


def drop_permission_from_group(apps, schema_editor):
    """Снять право, но НЕ удалять группу — см. докстринг модуля."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name=GROUP_NAME).first()
    if group is None:
        return
    permission = Permission.objects.filter(
        codename=PERM_CODENAME, content_type__app_label=APP_LABEL
    ).first()
    if permission is not None:
        group.permissions.remove(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0017_alter_tenant_options"),
        # Право живёт в ``auth``, и без её таблиц эта миграция не имеет
        # предмета. Зависимость объявлена явно, а не оставлена на
        # везение порядка приложений.
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(create_group, drop_permission_from_group),
    ]
