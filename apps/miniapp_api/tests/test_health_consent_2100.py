"""The profile consent handle grants the diary consent v1, never HEALTH (DRF-2100).

``/me/health-consent/`` keeps its path and its shape (an old bundle still
reads it), but what it WRITES is ``food_diary_processing`` under
``food-diary-v1`` — the same row the food scanner grants through
``/me/food-scanner-consent/``. A legacy HEALTH row is read and withdrawn
here, and is never created.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent import health as legacy_health
from apps.consent import nutrition
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.consent.tests.legacy_health import seed_legacy_health
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-2100"  # noqa: S105 — test fixture  # pragma: allowlist secret
CHANNEL_USER_ID = "2100100"
V1 = nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION
HEALTH = ConsentRecord.ConsentType.HEALTH.value
DIARY = nutrition.DIARY


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth() -> dict:
    params = {
        "user": json.dumps({"id": int(CHANNEL_USER_ID), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return {"HTTP_AUTHORIZATION": f"MaxInitData {_sign(params)}"}


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="hc-2100", name="HC 2100", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = tenant.slug
    user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID, display_name="Анна"
    )
    record_global_consent(user, source="test:welcome")
    return user


@pytest.fixture
def url() -> str:
    return reverse("miniapp_api:health_consent")


def _post(client: Client, url: str, version: str):
    return client.post(
        url,
        data=json.dumps({"document_version": version}),
        content_type="application/json",
        **_auth(),
    )


def _rows(bot_user: BotUser, consent_type: str, *, active: bool = True):
    qs = ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=consent_type)
    return qs.filter(granted=True, withdrawn_at__isnull=True) if active else qs


class TestGrantWritesTheDiaryConsent:
    def test_post_v1_writes_food_diary_processing_and_no_health_row(
        self, client, bot_user, url
    ) -> None:
        res = _post(client, url, V1)
        assert res.status_code == 200, res.content
        assert _rows(bot_user, DIARY).count() == 1
        assert _rows(bot_user, HEALTH, active=False).count() == 0, "a new HEALTH row was created"
        body = res.json()
        assert body["granted"] is True
        assert body["document_version"] == V1
        assert body["current_document_version"] == V1
        assert nutrition.diary_is_granted(bot_user) is True
        assert legacy_health.is_granted(bot_user) is False

    def test_the_profile_path_and_the_scanner_path_share_one_row(
        self, client, bot_user, url
    ) -> None:
        """Two grant sources, one consent: the registry holds a single active diary row."""

        scanner = reverse("miniapp_api:food_scanner_consent")
        assert _post(client, scanner, V1).status_code == 200
        assert _post(client, url, V1).status_code == 200
        rows = _rows(bot_user, DIARY)
        assert rows.count() == 1, "a second «diary consent» appeared under the profile source"
        assert rows.get().document_version == V1
        # And the other order: profile first, scanner second — still one row.
        nutrition.withdraw_diary(bot_user)
        assert _post(client, url, V1).status_code == 200
        assert _post(client, scanner, V1).status_code == 200
        assert _rows(bot_user, DIARY).count() == 1

    def test_the_old_health_version_from_a_cached_bundle_is_refused_and_writes_nothing(
        self, client, bot_user, url
    ) -> None:
        res = _post(client, url, legacy_health.HEALTH_CONSENT_DOCUMENT_VERSION)
        assert res.status_code == 409
        assert res.json()["error"] == "stale_disclosure"
        assert _rows(bot_user, HEALTH, active=False).count() == 0
        assert _rows(bot_user, DIARY, active=False).count() == 0

    def test_granting_still_resumes_the_chat(self, client, bot_user, url) -> None:
        with patch("apps.orchestrator.health_return.resume_after_health_consent") as resume:
            assert _post(client, url, V1).status_code == 200
        resume.assert_called_once()


class TestLegacyHealthIsReadAndWithdrawnHere:
    def test_get_reports_a_legacy_row_as_granted_with_its_own_version(
        self, client, bot_user, url
    ) -> None:
        seed_legacy_health(bot_user)
        body = client.get(url, **_auth()).json()
        assert body["granted"] is True
        assert body["granted_at"] is not None
        assert body["document_version"] == legacy_health.HEALTH_CONSENT_DOCUMENT_VERSION
        assert body["current_document_version"] == V1  # the text that is issued today

    def test_delete_withdraws_both_the_legacy_row_and_the_diary_row(
        self, client, bot_user, url
    ) -> None:
        seed_legacy_health(bot_user)
        assert _post(client, url, V1).status_code == 200
        res = client.delete(url, **_auth())
        assert res.status_code == 200
        assert res.json()["granted"] is False
        assert _rows(bot_user, HEALTH).count() == 0
        assert _rows(bot_user, DIARY).count() == 0
        # Trail kept: rows are withdrawn, not deleted.
        assert _rows(bot_user, HEALTH, active=False).count() == 1
        assert _rows(bot_user, DIARY, active=False).count() == 1

    def test_the_consents_document_shows_the_diary_row_and_no_new_health(
        self, client, bot_user, url
    ) -> None:
        assert _post(client, url, V1).status_code == 200
        doc = client.get(reverse("miniapp_api:customer_consents"), **_auth()).json()["consents"]
        assert doc[DIARY]["granted"] is True
        assert doc[DIARY]["document_version"] == V1
        assert doc[HEALTH]["granted"] is False
