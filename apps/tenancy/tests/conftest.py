"""Общие условия тестов ``apps.tenancy``."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _provisioning_token_is_configured(settings):
    """Контур настроен: провижининг-токен задан и отличается от общего Bearer.

    С DRF-1613 экран «Подключить салон» отказывает ДО формы, когда в
    настройках бота ``AYLA_TENANT_PROVISIONING_TOKEN`` пуст. Тесты экрана
    о других его свойствах (409 от каталога, SETUP_PENDING по 403, кнопка
    верификации) подменяют HTTP-клиент и о токене не думают — без этого
    условия они краснели бы по чужой причине. Тесты о пустом или
    совпадающем токене выставляют своё значение сами, поверх этого.
    """
    settings.AYLA_TENANT_PROVISIONING_TOKEN = "test-provisioning-token"  # pragma: allowlist secret
