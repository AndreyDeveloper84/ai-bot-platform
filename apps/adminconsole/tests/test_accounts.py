"""Как заводится и как отзывается учётная запись (DRF-1495)."""

from __future__ import annotations

import secrets

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.adminconsole.accounts import (
    PASSWORD_ENV_VAR,
    SHARED_USERNAMES,
    AccountError,
    grant_admin_account,
    revoke_admin_account,
)


@pytest.mark.django_db
def test_grant_creates_a_staff_account_that_is_not_a_superuser() -> None:
    user, created = grant_admin_account(username="p.ivanov", role="viewer")

    assert created
    assert user.is_staff, "без is_staff в админку не пускают вовсе"
    assert not user.is_superuser, "роль не должна выдавать суперпользователя"
    assert sorted(user.groups.values_list("name", flat=True)) == ["ayla-viewer"]


@pytest.mark.django_db
def test_grant_without_password_leaves_the_account_unusable() -> None:
    """Заводится, но войти нельзя, пока владелец не задаст пароль."""
    user, _ = grant_admin_account(username="p.silent", role="viewer")

    assert not user.has_usable_password()


@pytest.mark.django_db
def test_grant_reads_the_password_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv(PASSWORD_ENV_VAR, password)

    user, _ = grant_admin_account(username="p.env", role="editor")

    assert user.check_password(password)


@pytest.mark.django_db
def test_grant_moves_an_existing_account_between_roles() -> None:
    grant_admin_account(username="p.moved", role="viewer")

    user, created = grant_admin_account(username="p.moved", role="editor")

    assert not created
    # set(), не add(): прежняя роль снята, а не накоплена поверх новой.
    assert sorted(user.groups.values_list("name", flat=True)) == ["ayla-editor"]


@pytest.mark.django_db
@pytest.mark.parametrize("shared", ["admin", "Salon", "TEAM", "ops"])
def test_shared_usernames_are_refused(shared: str) -> None:
    """Общая учётка на всех обессмысливает журнал — потому и запрещена."""
    assert shared.casefold() in SHARED_USERNAMES  # присутствие: имя из списка

    with pytest.raises(AccountError, match="Общие учётные записи"):
        grant_admin_account(username=shared, role="viewer")

    assert not get_user_model().objects.filter(username=shared).exists()


@pytest.mark.django_db
def test_unknown_role_is_refused() -> None:
    with pytest.raises(AccountError, match="Неизвестная роль"):
        grant_admin_account(username="p.oops", role="superadmin")


@pytest.mark.django_db
def test_revoke_kills_access_but_keeps_the_row() -> None:
    before, _ = grant_admin_account(username="p.leaving", role="editor")
    # Присутствие: до отзыва запись рабочая и в роли — иначе проверки
    # ниже прошли бы над записью, которой и так ничего не было дано.
    assert before.is_active
    assert before.is_staff
    assert list(before.groups.values_list("name", flat=True)) == ["ayla-editor"]

    revoke_admin_account(username="p.leaving")

    user = get_user_model().objects.get(username="p.leaving")
    assert not user.is_active
    assert not user.is_staff
    assert list(user.groups.all()) == []


@pytest.mark.django_db
def test_revoke_refuses_to_touch_a_superuser() -> None:
    """Иначе первый же отзыв оставит контур без администратора."""
    get_user_model().objects.create_superuser(
        username="owner.real",
        email="",
        password=secrets.token_urlsafe(24),
    )

    with pytest.raises(AccountError, match="суперпользователь"):
        revoke_admin_account(username="owner.real")

    assert get_user_model().objects.get(username="owner.real").is_active


@pytest.mark.django_db
def test_grant_refuses_to_demote_a_superuser() -> None:
    get_user_model().objects.create_superuser(
        username="owner.two",
        email="",
        password=secrets.token_urlsafe(24),
    )

    with pytest.raises(AccountError, match="суперпользователь"):
        grant_admin_account(username="owner.two", role="viewer")

    assert get_user_model().objects.get(username="owner.two").is_superuser


@pytest.mark.django_db
def test_commands_wire_the_same_services() -> None:
    call_command("sync_admin_roles")
    call_command("admin_account_grant", "--username", "c.ivanova", "--role", "viewer")

    user = get_user_model().objects.get(username="c.ivanova")
    assert sorted(user.groups.values_list("name", flat=True)) == ["ayla-viewer"]

    call_command("admin_account_revoke", "--username", "c.ivanova")

    user.refresh_from_db()
    assert not user.is_active

    with pytest.raises(CommandError, match="нет"):
        call_command("admin_account_revoke", "--username", "c.nobody")
