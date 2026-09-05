"""Роли: две штуки, и они действительно разные (DRF-1495).

Смысл эпика DRF-75 в одной фразе: «просмотр очереди handoff и правка
мастера — разные права». Здесь это проверяется как утверждение о правах,
а не о рендере: тест смотрит, что лежит в группе.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group, Permission

from apps.adminconsole.roles import (
    EDITOR_GROUP,
    VIEWER_GROUP,
    is_editable_by_editor,
    is_visible_to_roles,
    sync_admin_roles,
)


def _codenames(group_name: str) -> set[str]:
    group = Group.objects.get(name=group_name)
    return {
        f"{perm.content_type.app_label}.{perm.codename}"
        for perm in group.permissions.select_related("content_type")
    }


@pytest.mark.django_db
def test_sync_creates_both_roles_with_permissions() -> None:
    granted = sync_admin_roles()

    # Присутствие: обе группы существуют и непусты. Без этой проверки
    # все утверждения ниже про «нет такого права» прошли бы на пустой
    # группе и ничего не значили бы.
    assert Group.objects.filter(name=VIEWER_GROUP).exists()
    assert Group.objects.filter(name=EDITOR_GROUP).exists()
    assert granted[VIEWER_GROUP] > 0
    assert granted[EDITOR_GROUP] > granted[VIEWER_GROUP]


@pytest.mark.django_db
def test_viewer_sees_handoff_queue_but_cannot_edit_a_master() -> None:
    """Та самая фраза эпика, проверенная на правах."""
    sync_admin_roles()
    viewer = _codenames(VIEWER_GROUP)

    # Присутствие: смотрящий действительно видит очередь handoff.
    assert "handoff.view_admintask" in viewer
    assert "catalog.view_catalogmaster" in viewer

    # И на этом всё: ни одного права на запись, ни по мастеру, ни вообще.
    assert "catalog.change_catalogmaster" not in viewer
    assert not {code for code in viewer if ".view_" not in code}


@pytest.mark.django_db
def test_editor_edits_domain_data_and_keeps_everything_viewer_had() -> None:
    sync_admin_roles()
    viewer = _codenames(VIEWER_GROUP)
    editor = _codenames(EDITOR_GROUP)

    assert viewer  # присутствие — см. тест выше
    assert viewer <= editor, "правящий обязан видеть всё, что видит смотрящий"

    # Прикладные данные правятся.
    assert "handoff.change_admintask" in editor
    assert "catalog.change_masterservice" in editor
    assert "kb.change_kbdocument" in editor


@pytest.mark.django_db
def test_no_role_can_touch_accounts_or_sessions() -> None:
    """Роль, умеющая себя повысить, — не роль."""
    sync_admin_roles()
    viewer = _codenames(VIEWER_GROUP)
    editor = _codenames(EDITOR_GROUP)

    # Присутствие: права на пользователей вообще существуют в базе —
    # иначе «их нет в группе» было бы правдой ни о чём.
    assert Permission.objects.filter(content_type__app_label="auth").exists()

    for codenames in (viewer, editor):
        assert not {code for code in codenames if code.startswith("auth.")}
        assert not {code for code in codenames if code.startswith("sessions.")}


@pytest.mark.django_db
def test_journals_are_readable_but_not_editable_by_either_role() -> None:
    sync_admin_roles()
    viewer = _codenames(VIEWER_GROUP)
    editor = _codenames(EDITOR_GROUP)

    # Присутствие: журнал виден обеим ролям — на то он и журнал.
    assert "audit.view_auditlog" in viewer
    assert "admin.view_logentry" in viewer
    assert "audit.view_auditlog" in editor

    for verb in ("add", "change", "delete"):
        assert f"audit.{verb}_auditlog" not in editor
        assert f"admin.{verb}_logentry" not in editor


@pytest.mark.django_db
def test_tenant_secrets_are_not_editable_through_a_role() -> None:
    """В тенанте лежат токен бота и вебхук-секрет — правит только владелец."""
    sync_admin_roles()
    editor = _codenames(EDITOR_GROUP)

    assert "tenancy.view_tenant" in editor  # присутствие
    assert "tenancy.change_tenant" not in editor


def test_predicates_agree_with_the_denylists() -> None:
    """Предикаты — единственный источник правды для sync_admin_roles."""
    from django.contrib.admin.models import LogEntry
    from django.contrib.auth.models import User

    from apps.audit.models import AuditLog
    from apps.handoff.models import AdminTask
    from apps.tenancy.models import Tenant

    # Присутствие: предикат вообще умеет говорить «да».
    assert is_visible_to_roles(AdminTask)
    assert is_editable_by_editor(AdminTask)

    assert not is_visible_to_roles(User)
    assert not is_editable_by_editor(User)
    assert is_visible_to_roles(AuditLog)
    assert not is_editable_by_editor(AuditLog)
    assert is_visible_to_roles(LogEntry)
    assert not is_editable_by_editor(LogEntry)
    assert is_visible_to_roles(Tenant)
    assert not is_editable_by_editor(Tenant)
