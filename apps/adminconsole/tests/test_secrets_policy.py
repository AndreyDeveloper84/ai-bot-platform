"""Секреты не видит никто, кроме того, кто их задаёт (DRF-1495).

Эти проверки ходят **ролью**, а не суперпользователем, и в этом весь их
смысл. Первая версия задачи проверяла форму тенанта суперпользователем —
единственным принципалом, которому и так ничего не грозило, — и потому
не заметила, что обеим ролям Django рисует ту же форму read-only и
печатает в ней полный токен BotFather открытым текстом.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from django.test import Client
from django.utils import timezone

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, grant_admin_account
from apps.adminconsole.secrets_policy import SECRET_FIELDS, install_secret_field_policy
from apps.catalog.models import CatalogMaster
from apps.tenancy.models import Tenant

# Значения фиктивные и выглядят фиктивными нарочно. Форма при этом
# сохранена: у токена десять цифр, двоеточие и хвост, из которого
# маскировка берёт последние четыре символа, — иначе проверка
# маскировки была бы не про тот формат.
BOT_TOKEN = "0000000000:ci-fake-not-a-real-bot-token-xxxx"
# pragma ниже — потому что detect-secrets ловит присваивание по слову
# SECRET в имени константы, каким бы ни было значение. Это не секрет:
# строка придумана для теста и нигде, кроме него, не встречается.
WEBHOOK_SECRET = "ci-fake-not-a-real-webhook-role-test"  # pragma: allowlist secret


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(
        slug="policy-tenant",
        name="Policy tenant",
        telegram_bot_token=BOT_TOKEN,
        telegram_webhook_secret=WEBHOOK_SECRET,
    )


def _role_client(monkeypatch: pytest.MonkeyPatch, username: str, role: str) -> Client:
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv(PASSWORD_ENV_VAR, password)
    grant_admin_account(username=username, role=role)
    monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
    client = Client()
    assert client.login(username=username, password=password)
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_neither_role_sees_the_tenant_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tenant: Tenant,
    role: str,
) -> None:
    client = _role_client(monkeypatch, f"sp.{role}", role)

    response = client.get(f"/admin/tenancy/tenant/{tenant.pk}/change/")
    body = response.content.decode("utf-8")

    # Присутствие: страница действительно отрисовалась и это нужный тенант.
    # Без этого «токена в теле нет» было бы правдой про пустую страницу.
    assert response.status_code == 200
    assert "policy-tenant" in body
    assert "задан" in body, "строки состояния секретов на форме нет вовсе"

    assert BOT_TOKEN not in body
    assert WEBHOOK_SECRET not in body


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_neither_role_sees_a_master_invite_token(
    monkeypatch: pytest.MonkeyPatch,
    tenant: Tenant,
    role: str,
) -> None:
    """Экран каталога read-only для всех — значит его форму видят все."""
    token = uuid.uuid4()
    master = CatalogMaster.objects.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        external_id=990001,
        name="Мастер Политика",
        invite_token=token,
    )
    client = _role_client(monkeypatch, f"sp.cat.{role}", role)

    response = client.get(f"/admin/catalog/catalogmaster/{master.pk}/change/")
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "Мастер Политика" in body  # присутствие: это карточка мастера

    assert str(token) not in body


@pytest.mark.django_db
def test_the_owner_still_gets_the_fields_to_set_them(
    monkeypatch: pytest.MonkeyPatch,
    tenant: Tenant,
) -> None:
    """Политика прячет секрет, а не отбирает возможность его задать."""
    from django.contrib.auth import get_user_model

    password = secrets.token_urlsafe(24)
    get_user_model().objects.create_superuser(username="sp.owner", email="", password=password)
    client = Client()
    assert client.login(username="sp.owner", password=password)

    response = client.get(f"/admin/tenancy/tenant/{tenant.pk}/change/")
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    # Поля ввода на месте — владелец может задать новое значение...
    assert 'name="telegram_bot_token"' in body
    assert 'name="telegram_webhook_secret"' in body
    # ...но текущее значение в разметку не попадает и здесь.
    assert BOT_TOKEN not in body
    assert WEBHOOK_SECRET not in body


def test_policy_applies_to_everything_it_declares() -> None:
    """Запись в SECRET_FIELDS про незарегистрированный экран — опечатка."""
    from django.contrib import admin

    registered = {
        f"{model._meta.app_label}.{model._meta.model_name}"  # noqa: SLF001
        for model in admin.site._registry  # noqa: SLF001
    }

    # Присутствие: список не пуст и админка вообще собрана.
    assert SECRET_FIELDS
    assert registered

    missing = sorted(set(SECRET_FIELDS) - registered)
    assert missing == [], f"в SECRET_FIELDS есть незарегистрированные экраны: {missing}"


def test_install_is_idempotent() -> None:
    """ready() может выполниться дважды — обёртка не должна множиться."""
    first = install_secret_field_policy()
    second = install_secret_field_policy()

    # Присутствие: первый прогон (или тот, что случился при старте
    # приложения) уже покрыл объявленные экраны.
    assert sorted(SECRET_FIELDS) != []
    assert second == [], f"повторная установка обернула экраны заново: {second}"
    assert isinstance(first, list)
