"""Экран «Подключить салон» называет невозможность ДО формы, по факту (DRF-1613).

Дверь «Add Tenant» уже закрыта (#1521): единственный путь — ``connect/``.
Но и этот путь на пилоте сегодня закрыт по факту: ``ensure_tenant`` — один
идемпотентный ``POST /internal/tenants/`` под ``AYLA_TENANT_PROVISIONING_TOKEN``,
и без токена подключить нельзя НИЧЕГО — ни новый салон, ни существующий.
Экран узнавал об этом только после нажатия (``SETUP_PENDING``), а до нажатия
показывал форму как рабочую: оператор заполняет три поля и получает
«подождите». Форма, которая заведомо не сработает, обязана сказать это до
того, как её заполнят, — и не догадкой, а фактом из настроек.

Проверяется по факту два условия на стороне бота: токен пуст; токен равен
``AYLA_INTERNAL_API_TOKEN`` (правило каталога ``users.E002`` — такой токен
каталог отвергнет). Отказ каталога (403) остаётся фактом момента нажатия и
показывается как прежде — его без вызова не узнать, и это названо.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.tenancy.onboarding import (
    PREFLIGHT_TOKEN_EQUALS_INTERNAL,
    PREFLIGHT_TOKEN_MISSING,
    connect_preflight,
)

pytestmark = pytest.mark.django_db

CONNECT_URL = "admin:tenancy_tenant_connect"


@pytest.fixture
def owner_client(django_user_model) -> Client:
    user = django_user_model.objects.create_superuser(
        username="vladelets-1613",
        email="vladelets1613@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(user)
    return client


class TestTheFactIsComputedNotGuessed:
    def test_empty_token_is_named(self, settings):
        settings.AYLA_TENANT_PROVISIONING_TOKEN = ""
        settings.AYLA_INTERNAL_API_TOKEN = "internal-x"  # pragma: allowlist secret

        verdict = connect_preflight()

        assert verdict.ok is False
        assert verdict.reason == PREFLIGHT_TOKEN_MISSING
        assert "AYLA_TENANT_PROVISIONING_TOKEN" in verdict.label

    def test_token_equal_to_the_internal_one_is_named(self, settings):
        settings.AYLA_TENANT_PROVISIONING_TOKEN = "same"  # pragma: allowlist secret
        settings.AYLA_INTERNAL_API_TOKEN = "same"  # pragma: allowlist secret

        verdict = connect_preflight()

        assert verdict.ok is False
        assert verdict.reason == PREFLIGHT_TOKEN_EQUALS_INTERNAL

    def test_a_distinct_token_passes_preflight(self, settings):
        """Положительный контроль: при настроенном токене экран не отговаривает.

        Отказ каталога (403) отсюда не виден и не выдумывается — он
        остаётся фактом момента нажатия.
        """
        settings.AYLA_TENANT_PROVISIONING_TOKEN = "prov-y"  # pragma: allowlist secret
        settings.AYLA_INTERNAL_API_TOKEN = "internal-x"  # pragma: allowlist secret

        verdict = connect_preflight()

        assert verdict.ok is True
        assert verdict.reason == ""


class TestTheScreenSaysItBeforeTheForm:
    def test_pilot_state_shows_the_reason_and_no_form(self, owner_client, settings):
        """Красный до правки: форма показывалась как рабочая, причины не было."""
        settings.AYLA_TENANT_PROVISIONING_TOKEN = ""
        settings.AYLA_INTERNAL_API_TOKEN = "internal-x"  # pragma: allowlist secret

        response = owner_client.get(reverse(CONNECT_URL))

        assert response.status_code == 200
        html = response.content.decode()
        assert "Подключение сейчас невозможно" in html
        assert "AYLA_TENANT_PROVISIONING_TOKEN" in html
        assert 'value="Подключить и проверить исход"' not in html
        # Верификация уже подключённых салонов живёт на их карточках и не
        # зависит от токена — экран должен сказать, куда идти.
        assert "карточк" in html

    def test_configured_state_shows_the_form_and_no_warning(self, owner_client, settings):
        """Контроль: настроенный токен — форма как раньше, предупреждения нет."""
        settings.AYLA_TENANT_PROVISIONING_TOKEN = "prov-y"  # pragma: allowlist secret
        settings.AYLA_INTERNAL_API_TOKEN = "internal-x"  # pragma: allowlist secret

        response = owner_client.get(reverse(CONNECT_URL))

        html = response.content.decode()
        assert 'value="Подключить и проверить исход"' in html
        assert "Подключение сейчас невозможно" not in html

    def test_a_post_under_the_pilot_state_is_refused_by_the_same_name(self, owner_client, settings):
        """Кнопки нет, но POST руками возможен: тот же факт, тот же текст, строки нет."""
        from apps.tenancy.models import Tenant

        settings.AYLA_TENANT_PROVISIONING_TOKEN = ""
        settings.AYLA_INTERNAL_API_TOKEN = "internal-x"  # pragma: allowlist secret

        response = owner_client.post(
            reverse(CONNECT_URL), {"slug": "novyi-salon", "name": "Новый", "city": "Пенза"}
        )

        assert response.status_code == 200
        assert "Подключение сейчас невозможно" in response.content.decode()
        assert not Tenant.all_objects.filter(slug="novyi-salon").exists()
