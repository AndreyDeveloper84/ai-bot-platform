"""Форма тенанта не отдаёт секреты Telegram (DRF-1495).

До этой правки ``TenantAdmin`` маскировал токен только в колонке списка,
а форма изменения печатала и токен бота, и вебхук-секрет обычными
текстовыми полями — полные значения уезжали в HTML каждому, кто открыл
страницу.
"""

from __future__ import annotations

import secrets

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.tenancy.admin import TenantAdminForm
from apps.tenancy.models import Tenant

BOT_TOKEN = "1234567890:AAaaBBbbCCccDDddEEeeFFggHHiiJJkkLL"  # noqa: S105 — вымышленный
WEBHOOK_SECRET = "s3cr3t-webhook-value-for-the-test"  # noqa: S105 — вымышленный


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(
        slug="secret-tenant",
        name="Secret tenant",
        telegram_bot_token=BOT_TOKEN,
        telegram_webhook_secret=WEBHOOK_SECRET,
    )


@pytest.fixture
def superuser_client(db) -> Client:  # noqa: ANN001
    password = secrets.token_urlsafe(24)
    get_user_model().objects.create_superuser(
        username="owner.secrets",
        email="",
        password=password,
    )
    client = Client()
    assert client.login(username="owner.secrets", password=password)
    return client


@pytest.mark.django_db
def test_change_form_html_never_contains_the_secrets(
    superuser_client: Client,
    tenant: Tenant,
) -> None:
    response = superuser_client.get(f"/admin/tenancy/tenant/{tenant.pk}/change/")
    body = response.content.decode("utf-8")

    # Присутствие: это действительно форма нужного тенанта, и она отрисовалась.
    assert response.status_code == 200
    assert "secret-tenant" in body
    assert "telegram_bot_token" in body, "поля ввода токена на форме нет вовсе"

    assert BOT_TOKEN not in body
    assert WEBHOOK_SECRET not in body
    # Что настроено — видно, но только хвостом токена.
    assert "задан" in body


def _form_data(tenant: Tenant, **overrides: str) -> dict[str, str]:
    """Тело формы, повторяющее текущее состояние тенанта.

    Пересобирается из самого объекта, а не из списка полей в тесте: новое
    обязательное поле у ``Tenant`` не должно ронять проверку про секреты.
    """
    data: dict[str, str] = {}
    for field in Tenant._meta.fields:  # noqa: SLF001
        if not field.editable or field.primary_key:
            continue
        value = getattr(tenant, field.attname)
        data[field.name] = "" if value is None else str(value)
    # Секреты форма всегда получает пустыми — так их шлёт браузер.
    data["telegram_bot_token"] = ""
    data["telegram_webhook_secret"] = ""
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_empty_submission_keeps_the_stored_secrets(tenant: Tenant) -> None:
    """Пустая отправка означает «не менять», а не «стереть»."""
    form = TenantAdminForm(data=_form_data(tenant), instance=tenant)

    assert form.is_valid(), form.errors
    saved = form.save()

    assert saved.telegram_bot_token == BOT_TOKEN
    assert saved.telegram_webhook_secret == WEBHOOK_SECRET


@pytest.mark.django_db
def test_a_submitted_value_replaces_the_stored_one(tenant: Tenant) -> None:
    """Задать новый секрет по-прежнему можно — иначе поле бесполезно."""
    new_token = "9876543210:ZZzzYYyyXXxxWWvvUUttSSrrQQppOO"  # noqa: S105 — вымышленный

    form = TenantAdminForm(
        data=_form_data(tenant, telegram_bot_token=new_token),
        instance=tenant,
    )

    assert form.is_valid(), form.errors
    saved = form.save()

    assert saved.telegram_bot_token == new_token
    # Второй секрет не тронут — поля независимы.
    assert saved.telegram_webhook_secret == WEBHOOK_SECRET


@pytest.mark.django_db
def test_widgets_do_not_render_the_value(tenant: Tenant) -> None:
    form = TenantAdminForm(instance=tenant)

    for field_name in ("telegram_bot_token", "telegram_webhook_secret"):
        widget = form.fields[field_name].widget
        # Присутствие: поле на форме есть.
        assert widget is not None
        assert widget.input_type == "password"
        assert widget.render_value is False
