"""DRF-2709 — согласие на хранение данных выдаётся заново из профиля.

Решение владельца 04.10: «согласие должно быть везде, но показываться только
один раз клиенту». Отозвать согласие в приложении было можно, выдать заново —
нет: ``me/consents/data-storage/`` принимал только ``DELETE``, и человек,
передумавший после отзыва, упирался в тупик.

* r — ``POST`` с версией документа выдаёт ``personal_data`` и ``memory_green``
  той же версией, что приветствие, по всем оболочкам, что снял отзыв;
* p — версия обязательна: пустая или чужая — 409, и не пишется ничего
  (доказательство, под каким текстом человек нажал, — 152-ФЗ);
* h — подсказки выдача не включает (§47.3), но включить их снова можно;
* i — идемпотентно; соседа не трогает; след в аудите.

Человек, подпись initData и заглушка сети резолва личности — помощниками
``test_customer_consents`` (DRF-1520), чтобы это был тот же человек, что в
узлах отзыва.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.consent import customer as customer_consents
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent
from apps.miniapp_api.tests import test_customer_consents as _consents
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_revoke = _consents._revoke


@pytest.fixture(autouse=True)
def _wired(settings):
    """Подпись initData и резолв личности без сети — как у соседнего набора."""
    settings.MAX_BOT_TOKEN = _consents.BOT_TOKEN
    with patch(
        "apps.integrations.ayla.identity_client.resolve_identity",
        side_effect=RuntimeError("ayla недоступна в тестах"),
    ):
        yield


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="consents-api", name="Consents API", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "consents-api"
    return t


@pytest.fixture
def bot_user(tenant):
    return _consents._make_user(tenant, _consents.CHANNEL_USER_ID)


@pytest.fixture
def other_user(tenant):
    return _consents._make_user(tenant, _consents.OTHER_CHANNEL_USER_ID)


@pytest.fixture
def auth() -> dict:
    return {"HTTP_AUTHORIZATION": _consents._init_data_header(_consents.CHANNEL_USER_ID)}


@pytest.fixture
def url() -> str:
    return reverse("miniapp_api:customer_consents")


@pytest.fixture
def hints_url() -> str:
    return reverse("miniapp_api:customer_proactive_hints")


@pytest.fixture
def revoke_url() -> str:
    return reverse("miniapp_api:customer_data_storage_consent")


PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value
MEMORY_GREEN = ConsentRecord.ConsentType.MEMORY_GREEN.value


def _regrant(client: Client, regrant_url: str, auth: dict, body: dict | None = None):
    payload = (
        {"document_version": customer_consents.DATA_STORAGE_REGRANT_DOCUMENT_VERSION}
        if body is None
        else body
    )
    return client.post(
        regrant_url, data=json.dumps(payload), content_type="application/json", **auth
    )


def _active(bot_user, consent_type: str):
    return ConsentRecord.all_tenants.filter(
        bot_user=bot_user, consent_type=consent_type, granted=True, withdrawn_at__isnull=True
    )


@pytest.fixture
def revoked(client: Client, bot_user, revoke_url, auth):
    """Человек, который отозвал согласие в профиле — исходное состояние листа."""
    res = _revoke(client, revoke_url, auth)
    assert res.status_code == 200, res.content
    assert not has_global_consent(bot_user, PERSONAL_DATA)
    return bot_user


# ── r: the re-grant itself ───────────────────────────────────────────────────


def test_a_revoked_person_can_grant_again(client: Client, revoked, revoke_url, auth) -> None:
    res = _regrant(client, revoke_url, auth)

    assert res.status_code == 200, res.content
    body = res.json()
    assert body["data_storage"]["granted"] is True
    assert has_global_consent(revoked, PERSONAL_DATA)
    rows = list(_active(revoked, PERSONAL_DATA))
    assert len(rows) == 1
    assert rows[0].source == "miniapp:profile_regrant"
    assert rows[0].document_version == "welcome-s2-v1"


def test_the_same_scope_as_the_welcome_memory_green_too(
    client: Client, revoked, revoke_url, auth
) -> None:
    _regrant(client, revoke_url, auth)

    green = list(_active(revoked, MEMORY_GREEN))
    assert len(green) == 1
    assert green[0].document_version == "welcome-s2-v1"


def test_the_document_names_the_version_the_client_must_send(
    client: Client, bot_user, url, auth
) -> None:
    body = client.get(url, **auth).json()

    assert body["data_storage"]["regrant"] == {"document_version": "welcome-s2-v1"}


def test_the_version_is_the_welcome_one_literally() -> None:
    """Дрейф: выдача из профиля и приветствие записывают одну версию. Литералом —
    число в узле, взятое из той же константы, смену константы не поймало бы."""
    from apps.channels.max.global_onboarding import CONSENT_DOCUMENT_VERSION

    assert customer_consents.DATA_STORAGE_REGRANT_DOCUMENT_VERSION == "welcome-s2-v1"
    assert CONSENT_DOCUMENT_VERSION == "welcome-s2-v1"


# ── p: no proof of what was shown — nothing is written ──────────────────────


@pytest.mark.parametrize(
    "body",
    [{}, {"document_version": ""}, {"document_version": "welcome-s1-v0"}],
    ids=["no-version", "empty-version", "foreign-version"],
)
def test_without_the_document_version_nothing_is_written(
    client: Client, revoked, revoke_url, auth, body
) -> None:
    before = ConsentRecord.all_tenants.filter(bot_user=revoked).count()

    res = _regrant(client, revoke_url, auth, body)

    assert res.status_code == 409
    assert res.json()["error"] == "stale_document"
    assert ConsentRecord.all_tenants.filter(bot_user=revoked).count() == before
    assert not has_global_consent(revoked, PERSONAL_DATA)


# ── h: hints stay off, but may be turned on again ────────────────────────────


def test_the_grant_does_not_turn_the_hints_on(client: Client, revoked, revoke_url, auth) -> None:
    body = _regrant(client, revoke_url, auth).json()

    # Presence first: the consent IS back — the hints staying off is the rule, not a failure.
    assert body["data_storage"]["granted"] is True
    assert body["proactive_hints"]["enabled"] is False
    assert body["proactive_hints"]["can_enable"] is True
    assert body["proactive_hints"]["blocked_reason"] == ""
    revoked.refresh_from_db()
    assert revoked.proactive_messages_opt_out is True


def test_after_the_grant_the_hints_can_be_turned_on(
    client: Client, revoked, revoke_url, hints_url, auth
) -> None:
    refused = client.post(
        hints_url, data=json.dumps({"enabled": True}), content_type="application/json", **auth
    )
    assert refused.status_code == 409  # the §47.3 lock, before the grant

    _regrant(client, revoke_url, auth)
    res = client.post(
        hints_url, data=json.dumps({"enabled": True}), content_type="application/json", **auth
    )

    assert res.status_code == 200, res.content
    assert res.json()["proactive_hints"]["enabled"] is True


# ── i: idempotent, the neighbour untouched, audited ─────────────────────────


def test_a_second_grant_adds_no_rows(client: Client, revoked, revoke_url, auth) -> None:
    _regrant(client, revoke_url, auth)
    after_first = ConsentRecord.all_tenants.filter(bot_user=revoked).count()

    res = _regrant(client, revoke_url, auth)

    assert res.status_code == 200
    assert ConsentRecord.all_tenants.filter(bot_user=revoked).count() == after_first


def test_a_grant_over_an_active_consent_changes_nothing(
    client: Client, bot_user, revoke_url, auth
) -> None:
    before = ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=PERSONAL_DATA).count()
    assert has_global_consent(bot_user, PERSONAL_DATA)

    res = _regrant(client, revoke_url, auth)

    assert res.status_code == 200
    assert (
        ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=PERSONAL_DATA).count()
        == before
    )


def test_the_neighbour_is_untouched(client: Client, revoked, other_user, revoke_url, auth) -> None:
    neighbour_rows = ConsentRecord.all_tenants.filter(bot_user=other_user).count()
    assert neighbour_rows > 0

    _regrant(client, revoke_url, auth)

    assert ConsentRecord.all_tenants.filter(bot_user=other_user).count() == neighbour_rows


def test_the_grant_is_audited(client: Client, revoked, revoke_url, auth) -> None:
    _regrant(client, revoke_url, auth)

    rows = list(AuditLog.all_tenants.filter(action="consent.data_storage_regranted"))
    assert len(rows) == 1
    assert rows[0].payload["document_version"] == "welcome-s2-v1"
    assert rows[0].payload["proactive_hints_enabled"] is False
