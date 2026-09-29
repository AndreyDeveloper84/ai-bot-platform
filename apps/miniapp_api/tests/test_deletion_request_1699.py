"""Заявка на удаление аккаунта — бот-половина D1 (§7 свода, DRF-1699).

Две стороны одного требования, обе тестом (главное окно):
* заявка создана в каталоге → человек видит «принято», номер, срок, статус;
* каталог 5xx → «удаление не началось», строки нет, ничего не стёрто.

Каталог подменён на границе HTTP-клиента (``PersonalContextHttpClient``):
всё, что выше — резолв личности, вид, ручка, — настоящее.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.identity.models import BotUser
from apps.identity.services import deletion_request as svc
from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN
from apps.integrations.ayla.personal_context_client import (
    PersonalContextNotFoundError,
    PersonalContextTransportError,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-1699"  # noqa: S105 — test fixture  # pragma: allowlist secret
CHANNEL_USER_ID = "1699100"
AYLA_ID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
REQUEST_ID = "7a1b2c3d-0000-4000-8000-000000001699"


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


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _no_ayla_identity_network():
    """Резолв личности не ходит в сеть: несвязанный остаётся несвязанным."""
    with patch(
        "apps.integrations.ayla.identity_client.resolve_identity",
        side_effect=RuntimeError("ayla недоступна в тестах"),
    ):
        yield


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="deletion-1699", name="Удаление", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "deletion-1699"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
        display_name="Анна",
        ayla_user_id=AYLA_ID,
    )


@pytest.fixture
def auth() -> dict:
    return {"HTTP_AUTHORIZATION": _init_data_header(CHANNEL_USER_ID)}


@pytest.fixture
def url() -> str:
    return reverse("miniapp_api:deletion_request")


def _wire(**over) -> dict:
    base = {
        "created": True,
        "request_id": REQUEST_ID,
        "status": "DELETION_REQUESTED",
        "requested_at": "2026-09-11T14:00:00+00:00",
        "deadline_at": "2026-10-11T14:00:00+00:00",
        "completed_at": None,
        "is_open": True,
    }
    base.update(over)
    return base


class FakeCatalog:
    """``PersonalContextHttpClient`` на границе: только два метода D1."""

    def __init__(self, *, create=None, current=None, raise_on_create=None):
        self._create = create if create is not None else _wire()
        self._current = current
        self._raise = raise_on_create
        self.create_calls: list[dict] = []
        self.erasure_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None

    def create_deletion_request(self, *, ayla_user_id, external_user_id, initiator="bot"):
        self.create_calls.append(
            {
                "ayla_user_id": ayla_user_id,
                "external_user_id": external_user_id,
                "initiator": initiator,
            }
        )
        if self._raise is not None:
            raise self._raise
        return self._create

    def get_current_deletion_request(self, *, ayla_user_id, external_user_id):
        if self._current is None:
            raise PersonalContextNotFoundError("none")
        return self._current

    # Стирание — если кто-то его позовёт, счётчик это покажет.
    def delete_personal_data(self, **_kw):
        self.erasure_calls += 1


@pytest.fixture
def catalog():
    fake = FakeCatalog()
    with patch(
        "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
        lambda *a, **k: fake,
    ):
        yield fake


def _post(client: Client, url: str, auth: dict, body: dict | None = None):
    return client.post(
        url,
        data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN} if body is None else body),
        content_type="application/json",
        **auth,
    )


# ---------------------------------------------------------------------------
# Сторона первая: заявка создана → успех с номером, сроком, статусом
# ---------------------------------------------------------------------------


class TestAcceptedShowsTheRequest:
    def test_post_creates_and_returns_id_deadline_status(
        self, client, bot_user, catalog, url, auth
    ):
        res = _post(client, url, auth)

        assert res.status_code == 201, res.content
        body = res.json()
        assert body["status"] == "accepted"
        assert body["request"] == {k: v for k, v in _wire().items() if k != "created"}
        assert body["request"]["request_id"] == REQUEST_ID
        assert body["request"]["deadline_at"] == "2026-10-11T14:00:00+00:00"
        # В каталог ушёл ИМЕННО этот человек, тем же ключом, что при стирании.
        assert catalog.create_calls == [
            {
                "ayla_user_id": str(AYLA_ID),
                "external_user_id": f"bot:max:{CHANNEL_USER_ID}",
                "initiator": "bot",
            }
        ]

    def test_request_erases_nothing(self, client, bot_user, catalog, url, auth):
        """Заявка — запись, не действие: PII на месте, стирание не звалось."""
        res = _post(client, url, auth)

        assert res.status_code == 201, res.content
        bot_user.refresh_from_db()
        assert bot_user.display_name == "Анна"
        assert bot_user.ayla_user_id == AYLA_ID
        assert catalog.erasure_calls == 0
        assert not AuditLog.all_tenants.filter(action="privacy.personal_data_deleted").exists()

    def test_repeat_is_200_with_the_same_request(self, client, bot_user, url, auth):
        """Каталог идемпотентен; «уже принято» — 200, по ``created`` из тела."""
        fake = FakeCatalog(create=_wire(created=False))
        with patch(
            "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
            lambda *a, **k: fake,
        ):
            res = _post(client, url, auth)
        assert res.status_code == 200, res.content
        assert res.json()["status"] == "accepted"
        assert res.json()["request"]["request_id"] == REQUEST_ID

    def test_get_shows_current_or_none(self, client, bot_user, url, auth):
        fake = FakeCatalog(current=None)
        with patch(
            "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
            lambda *a, **k: fake,
        ):
            none = client.get(url, **auth)
        assert none.status_code == 200
        assert none.json() == {"status": "none", "request": None}

        fake = FakeCatalog(current=_wire(status="DELETION_PROCESSING"))
        with patch(
            "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
            lambda *a, **k: fake,
        ):
            found = client.get(url, **auth)
        assert found.status_code == 200
        assert found.json()["status"] == "found"
        assert found.json()["request"]["status"] == "DELETION_PROCESSING"


# ---------------------------------------------------------------------------
# Сторона вторая: не началось — и это сказано
# ---------------------------------------------------------------------------


class TestNotStartedIsSaidPlainly:
    def test_catalog_5xx_is_not_started_and_nothing_changed(self, client, bot_user, url, auth):
        fake = FakeCatalog(raise_on_create=PersonalContextTransportError("http_503"))
        with patch(
            "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
            lambda *a, **k: fake,
        ):
            res = _post(client, url, auth)

        assert res.status_code == 502, res.content
        body = res.json()
        assert body["status"] == "not_started"
        assert body["reason"] == svc.UPSTREAM_UNAVAILABLE
        assert body["retryable"] is True
        assert "не началось" in body["detail"]
        # Не «partial»: слово о состоянии данных одно, и оно правдиво.
        assert "partial" not in json.dumps(body)
        bot_user.refresh_from_db()
        assert bot_user.display_name == "Анна"
        assert fake.erasure_calls == 0

    def test_unlinked_person_is_not_started_and_not_retryable(
        self, client, tenant, catalog, url, auth
    ):
        BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=CHANNEL_USER_ID,
            chat_id=f"chat-{CHANNEL_USER_ID}",
            display_name="Анна",
            ayla_user_id=None,
        )

        res = _post(client, url, auth)

        assert res.status_code == 409, res.content
        assert res.json()["status"] == "not_started"
        assert res.json()["reason"] == svc.NOT_LINKED
        assert res.json()["retryable"] is False
        assert catalog.create_calls == []

    def test_identity_conflict_is_not_started_and_the_catalog_is_not_called(
        self, client, tenant, catalog, url, auth
    ):
        """Две связи — не угадываем, чью заявку заводить (fail-closed, как в privacy)."""
        BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=CHANNEL_USER_ID,
            chat_id=f"chat-{CHANNEL_USER_ID}",
            display_name="Анна",
            ayla_user_id=AYLA_ID,
        )
        other_tenant = Tenant.objects.create(slug="deletion-1699-b", name="Б")
        BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id=CHANNEL_USER_ID,
            chat_id="chat-b",
            display_name="Анна",
            ayla_user_id=uuid.uuid4(),
        )

        res = _post(client, url, auth)

        assert res.status_code == 409, res.content
        assert res.json()["reason"] == svc.IDENTITY_CONFLICT
        assert catalog.create_calls == []

    def test_a_2xx_without_a_request_id_is_not_started(self, client, bot_user, url, auth):
        """Ответ без номера — не заявка: «принято» без номера обещало бы то, чего нет."""
        fake = FakeCatalog(create={"status": "DELETION_REQUESTED"})
        with patch(
            "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
            lambda *a, **k: fake,
        ):
            res = _post(client, url, auth)
        assert res.status_code == 502, res.content
        assert res.json()["status"] == "not_started"

    def test_wrong_confirmation_is_400_and_the_catalog_is_not_called(
        self, client, bot_user, catalog, url, auth
    ):
        """Серверное подтверждение — то же, что у стирания (T-05)."""
        res = _post(client, url, auth, body={"confirmation": "да"})

        assert res.status_code == 400, res.content
        assert catalog.create_calls == []

        # Положительная стража: с верным словом та же ручка заводит.
        assert _post(client, url, auth).status_code == 201
        assert len(catalog.create_calls) == 1
