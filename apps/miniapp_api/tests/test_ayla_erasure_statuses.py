"""DRF-1950 — что Mini App слышит от сервера, когда удаление в Ayla запущено.

Три исхода отзыва хранения данных вместо двух:

* ``revoked`` — всё подтверждено, в том числе readback каталога: «…удалены.» законно;
* ``revoked_deletion_started`` — удаление в Ayla поставлено в задание и ещё не
  подтверждено: экран говорит «Удаление запущено. Оно завершится в установленный срок.»;
* ``revoked_partial_processing`` — не связан с Ayla / конфликт личности / сбой
  локального шага: удаления в Ayla не было и не будет — «запущено» здесь ложь.

``/me/personal-data/`` — то же: задание → 200 ``deletion_started``; not_linked —
прежний 502 ``partial``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from typing import Any
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-erasure-statuses"  # noqa: S105 — test fixture  # pragma: allowlist secret
CHANNEL_USER_ID = "737373"

CONFIRMED = {
    "erased": True,
    "identities": [{"kind": "account", "context_row": "tombstone", "erased": True}],
}
NOT_CONFIRMED = {
    "erased": False,
    "identities": [{"kind": "account", "context_row": "holds_values", "erased": False}],
}


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


class _Ayla:
    def __init__(self, statuses: list) -> None:
        self.statuses = statuses
        self.calls: list[str] = []

    def delete_personal_data(self, *, ayla_user_id: str, external_user_id: str) -> None:
        self.calls.append("delete")

    def get_erasure_status(self, *, ayla_user_id: str, external_user_id: str) -> dict:
        self.calls.append("status")
        return self.statuses[0]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_ERASURE_RETRY_ENABLED = True


@pytest.fixture(autouse=True)
def _no_ayla_link():
    with patch(
        "apps.integrations.ayla.identity_client.resolve_identity",
        side_effect=RuntimeError("ayla недоступна в тестах"),
    ):
        yield


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="erasure-statuses", name="Erasure Statuses", timezone="Europe/Moscow"
    )
    settings.MAX_BOT_TENANT_SLUG = "erasure-statuses"
    return t


def _user(tenant: Tenant, *, linked: bool) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
        display_name="Анна",
        ayla_user_id=uuid.uuid4() if linked else None,
    )
    record_global_consent(user, source="test:welcome")
    return user


@pytest.fixture
def auth() -> dict:
    return {"HTTP_AUTHORIZATION": _init_data_header(CHANNEL_USER_ID)}


def _revoke(client: Client, auth: dict):
    from apps.consent.customer import DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION

    return client.delete(
        reverse("miniapp_api:customer_data_storage_consent"),
        data=json.dumps(
            {
                "confirmation": DELETE_CONFIRMATION_TOKEN,
                "disclosure_version": DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION,
            }
        ),
        content_type="application/json",
        **auth,
    )


def _ayla_client(statuses: list) -> tuple[_Ayla, Any]:
    ayla = _Ayla(statuses)
    return ayla, patch(
        "apps.identity.services.privacy.PersonalContextHttpClient", return_value=ayla
    )


def test_revocation_with_the_deletion_queued_says_started(client: Client, tenant, auth) -> None:
    _user(tenant, linked=True)
    ayla, patched = _ayla_client([NOT_CONFIRMED])
    with patched:
        res = _revoke(client, auth)

    assert res.status_code == 200, res.content
    body = res.json()
    assert body["data_storage"]["granted"] is False
    assert body["revocation"]["status"] == "revoked_deletion_started"
    assert ayla.calls == ["delete", "status"]


def test_revocation_confirmed_by_readback_is_revoked(client: Client, tenant, auth) -> None:
    _user(tenant, linked=True)
    ayla, patched = _ayla_client([CONFIRMED])
    with patched:
        res = _revoke(client, auth)

    assert res.status_code == 200, res.content
    assert res.json()["revocation"]["status"] == "revoked"
    # «Удалены» законно только после readback: вызов чтения был.
    assert ayla.calls == ["delete", "status"]


def test_revocation_of_an_unlinked_person_never_says_started(client: Client, tenant, auth) -> None:
    """Сторож: у несвязанного удаления в Ayla не было и не будет — «запущено» было бы ложью."""
    _user(tenant, linked=False)
    ayla, patched = _ayla_client([NOT_CONFIRMED])
    with patched:
        res = _revoke(client, auth)

    assert res.status_code == 200, res.content
    revocation = res.json()["revocation"]
    assert revocation["status"] == "revoked_partial_processing"
    assert revocation["failed_details"]["ayla_delete"] == "not_linked"
    assert ayla.calls == []


def test_personal_data_delete_with_the_deletion_queued_is_200_started(
    client: Client, tenant, auth
) -> None:
    _user(tenant, linked=True)
    ayla, patched = _ayla_client([NOT_CONFIRMED])
    with patched:
        res = client.delete(
            reverse("miniapp_api:personal_data_delete"),
            data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN}),
            content_type="application/json",
            **auth,
        )

    assert res.status_code == 200, res.content
    assert res.json() == {"status": "deletion_started"}
    assert ayla.calls == ["delete", "status"]
