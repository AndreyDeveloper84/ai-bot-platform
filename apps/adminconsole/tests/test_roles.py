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


#: Ровно то, что «правящий» может править сегодня.
#:
#: Список правки — денилист (``EDITOR_DENIED_*``), а значит всё, чего в
#: денилисте нет, правится по умолчанию. Для ``view_*`` это удобно: новый
#: экран из подзадач 2-6 сразу виден смотрящему. Для записи то же
#: поведение означало бы, что следующее зарегистрированное приложение
#: становится правимым, и никто не был вынужден это решить.
#:
#: Поэтому набор закреплён. Новая регистрация в админке красит этот тест —
#: и тот, кто её добавил, решает осознанно: либо дописать модель сюда,
#: либо внести приложение в ``EDITOR_DENIED_APP_LABELS``.
EDITOR_WRITABLE_MODELS = {
    "booking.bookingreminder",
    "booking.bookingrequest",
    "catalog.catalogfaq",
    "catalog.cataloghelparticle",
    "catalog.catalogmaster",
    "catalog.catalogservice",
    "catalog.masterservice",
    "conversations.conversation",
    "experiments.experiment",
    "experiments.holdout",
    "experiments.userassignment",
    "handoff.admintask",
    "identity.botuser",
    "identity.clientprofile",
    "kb.kbdocument",
    "loyalty.loyaltyaccount",
    "loyalty.loyaltyevent",
    "persona.brandvoiceconfig",
    "promotions.promotion",
    "scheduling.schedulechangerequest",
    "scheduling.scheduleexception",
    "scheduling.slotconfig",
    "scheduling.timeblock",
    "scheduling.workinghours",
}


def test_editor_writable_set_is_pinned() -> None:
    """Что правит «правящий» — решается человеком, а не умолчанием."""
    from django.contrib import admin

    from apps.adminconsole.roles import model_label

    actual = {
        model_label(model)
        for model in admin.site._registry  # noqa: SLF001
        if is_editable_by_editor(model)
    }

    # Присутствие: набор не пуст. Пустой прошёл бы сравнение только с
    # пустым эталоном, а так — красит сразу.
    assert actual, "правящий не может править ничего — денилисты съели всё"

    added = sorted(actual - EDITOR_WRITABLE_MODELS)
    removed = sorted(EDITOR_WRITABLE_MODELS - actual)
    assert not added, (
        "новые экраны стали правимыми по умолчанию — впишите их сюда "
        f"или в EDITOR_DENIED_APP_LABELS: {added}"
    )
    assert not removed, f"экраны пропали из админки или попали в денилист: {removed}"


@pytest.mark.django_db
def test_platform_config_is_not_editable_by_a_role() -> None:
    """Промпты, пороги роутера и дисклеймеры — не «прикладные данные»."""
    sync_admin_roles()
    editor = _codenames(EDITOR_GROUP)
    viewer = _codenames(VIEWER_GROUP)

    # Присутствие: реестр промптов вообще в админке и обеим ролям виден.
    assert "promptreg.view_promptversion" in viewer
    assert "promptreg.view_disclaimerlibrary" in editor

    for codename in (
        "promptreg.change_promptversion",
        "promptreg.change_thresholdconfig",
        "promptreg.change_disclaimerlibrary",
    ):
        assert codename not in editor


@pytest.mark.django_db
def test_empty_sync_refuses_instead_of_stripping_everyone() -> None:
    """Пустой расчёт не должен молча разжаловать всех выданных."""
    from unittest.mock import patch

    from apps.adminconsole.roles import RolesSyncError

    granted = sync_admin_roles()
    assert granted[VIEWER_GROUP] > 0  # присутствие: права были выданы
    before = _codenames(VIEWER_GROUP)
    assert before

    with patch("django.contrib.admin.site._registry", {}), pytest.raises(RolesSyncError):
        sync_admin_roles()

    assert _codenames(VIEWER_GROUP) == before, "отказ всё равно снял права"
