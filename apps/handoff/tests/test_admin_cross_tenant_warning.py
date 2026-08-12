"""DRF-1023 — cross-tenant warning banner in admin changelists.

The whole Django admin is cross-tenant (``get_queryset`` uses
``all_tenants`` in handoff / conversations / audit / booking / …), so any
account with admin access sees EVERY salon's data — including client
message text via MessageAdmin. Until tenant-restricted operator access
exists (DRF-1022), the changelist must carry a visible warning so the
next person handing out an account sees the blast radius first.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser_client(client: Client) -> Client:
    user = get_user_model().objects.create_superuser(
        username="root",
        email="root@example.com",
        password="x",  # pragma: allowlist secret
    )
    client.force_login(user)
    return client


class TestCrossTenantWarning:
    @pytest.mark.parametrize(
        "url",
        [
            "/admin/handoff/admintask/",
            "/admin/conversations/conversation/",
            "/admin/conversations/message/",
        ],
    )
    def test_changelist_shows_cross_tenant_warning(
        self, superuser_client: Client, url: str
    ) -> None:
        response = superuser_client.get(url)
        assert response.status_code == 200
        assert "кросс-тенант" in response.content.decode().lower()
