"""DRF-1453 — GET/POST/DELETE /customer/me/health-consent/.

Ручка выдачи согласия на медданные (152-ФЗ ст. 10). Проверяется ровно то,
что делает согласие согласием, а не галочкой:

* по умолчанию его нет — база ст. 6 не открывает особую категорию;
* выдать можно только под ту версию раскрытия, которую человеку показали;
* отзыв возвращает состояние обратно и идемпотентен;
* оба перехода видны читающему предикату, которым ходит сторож питания.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent import health as health_consent
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-health"  # noqa: S105 — test fixture  # pragma: allowlist secret
CHANNEL_USER_ID = "1453100"


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


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="hc-api", name="HC API", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "hc-api"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        display_name="Анна",
    )
    record_global_consent(user, source="test:welcome")
    return user


@pytest.fixture
def url() -> str:
    return reverse("miniapp_api:health_consent")


@pytest.fixture
def auth() -> dict:
    return {"HTTP_AUTHORIZATION": _init_data_header(CHANNEL_USER_ID)}


def _post(client: Client, url: str, auth: dict, version: str):
    return client.post(
        url,
        data=json.dumps({"document_version": version}),
        content_type="application/json",
        **auth,
    )


def test_default_state_is_not_granted(client: Client, bot_user, url, auth) -> None:
    res = client.get(url, **auth)

    assert res.status_code == 200
    body = res.json()
    assert body["granted"] is False
    assert body["granted_at"] is None
    # Экран должен знать, какую версию раскрытия показывать.
    assert body["current_document_version"] == health_consent.HEALTH_CONSENT_DOCUMENT_VERSION


def test_grant_then_read_back(client: Client, bot_user, url, auth) -> None:
    res = _post(client, url, auth, health_consent.HEALTH_CONSENT_DOCUMENT_VERSION)

    assert res.status_code == 200
    assert res.json()["granted"] is True
    assert res.json()["granted_at"] is not None
    assert client.get(url, **auth).json()["granted"] is True
    # Тот же предикат, которым ходит сторож нутриционной поверхности.
    assert health_consent.is_granted(bot_user) is True


def test_grant_records_the_version_the_person_was_shown(
    client: Client, bot_user, url, auth
) -> None:
    _post(client, url, auth, health_consent.HEALTH_CONSENT_DOCUMENT_VERSION)

    row = ConsentRecord.all_tenants.get(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.HEALTH
    )
    assert row.document_version == health_consent.HEALTH_CONSENT_DOCUMENT_VERSION
    assert row.granted is True


def test_stale_disclosure_version_is_refused(client: Client, bot_user, url, auth) -> None:
    """Согласие на текст, которого сервер не знает, не записывается."""
    res = _post(client, url, auth, "health-data-v0")

    assert res.status_code == 409
    assert res.json()["error"] == "stale_disclosure"
    assert health_consent.is_granted(bot_user) is False


def test_version_is_required(client: Client, bot_user, url, auth) -> None:
    res = client.post(url, data="{}", content_type="application/json", **auth)

    assert res.status_code == 400
    assert res.json()["error"] == "bad_request"
    assert health_consent.is_granted(bot_user) is False


def test_withdraw_returns_to_not_granted(client: Client, bot_user, url, auth) -> None:
    _post(client, url, auth, health_consent.HEALTH_CONSENT_DOCUMENT_VERSION)

    res = client.delete(url, **auth)

    assert res.status_code == 200
    assert res.json()["granted"] is False
    assert health_consent.is_granted(bot_user) is False


def test_withdraw_is_idempotent_and_keeps_the_trail(client: Client, bot_user, url, auth) -> None:
    _post(client, url, auth, health_consent.HEALTH_CONSENT_DOCUMENT_VERSION)
    client.delete(url, **auth)

    res = client.delete(url, **auth)

    assert res.status_code == 200
    assert res.json()["granted"] is False
    # Строка не удалена — она датирована (append-only контракт ConsentRecord).
    row = ConsentRecord.all_tenants.get(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.HEALTH
    )
    assert row.withdrawn_at is not None


def test_grant_after_withdraw_appends_a_fresh_row(client: Client, bot_user, url, auth) -> None:
    version = health_consent.HEALTH_CONSENT_DOCUMENT_VERSION
    _post(client, url, auth, version)
    client.delete(url, **auth)

    res = _post(client, url, auth, version)

    assert res.json()["granted"] is True
    rows = ConsentRecord.all_tenants.filter(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.HEALTH
    )
    assert rows.count() == 2  # отозванная + новая
    assert rows.filter(withdrawn_at__isnull=True).count() == 1


def test_unauthenticated_is_rejected(client: Client, bot_user, url, auth) -> None:
    """Без initData ручка не отвечает состоянием согласия ничьего человека.

    Сначала — что этот же GET С аутентификацией состояние отдаёт: иначе
    "granted" not in ... прошло бы и на пустой заглушке ручки.
    """
    assert "granted" in client.get(url, **auth).json()

    res = client.get(url)

    assert res.status_code != 200  # платформенный слог отказа — 400 (require_init_data)
    assert "granted" not in res.json()
