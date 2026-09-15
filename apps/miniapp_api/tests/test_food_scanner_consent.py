"""Согласие на дневник/сканер доезжает до гейта навыка — через реестр (DRF-1564, DRF-1963).

DRF-1564 разомкнул петлю «экран согласие принял, бот просит открыть Mini App»:
ручка стала писать то, что читает гейт. DRF-1963 (M1, владелец 15.09) перенёс
само согласие из колонки ``BotUser.food_scanner_consent_at`` в единый реестр:
строка ``food_diary_processing`` с версией текста, источником и отзывом,
который не стирает факт выдачи.

Тесты держат обе половины на настоящих строках ``ConsentRecord``: запись
доезжает до реестра, и гейт после записи открывается; отзыв закрывает его.
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

from apps.consent.models import ConsentRecord
from apps.consent.nutrition import DIARY, FOOD_DIARY_CONSENT_DOCUMENT_VERSION, diary_is_granted
from apps.identity.models import BotUser
from apps.identity.services.profile import LEGACY_ME_CONSENT_KEY
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-scanner"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
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
    t = Tenant.objects.create(slug="scanner-test", name="Scanner Test", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "scanner-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="91777",
        display_name="Анна",
        client_name="Анна К.",
    )


def _url() -> str:
    return reverse("miniapp_api:food_scanner_consent")


def _grant(
    client: Client, bot_user: BotUser, version: str | None = FOOD_DIARY_CONSENT_DOCUMENT_VERSION
):
    body = {} if version is None else {"document_version": version}
    return client.post(
        _url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
    )


def _active_rows(bot_user: BotUser):
    return ConsentRecord.all_tenants.filter(
        bot_user=bot_user, consent_type=DIARY, granted=True, withdrawn_at__isnull=True
    )


@pytest.mark.django_db
class TestFoodScannerConsentEndpoint:
    def test_grant_writes_a_versioned_registry_row(self, client: Client, bot_user: BotUser):
        # Присутствие ВПЕРЕДИ: до запроса строк нет — иначе тест зеленел бы на
        # строке, где согласие уже стояло.
        assert _active_rows(bot_user).count() == 0

        resp = _grant(client, bot_user)
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is True
        assert body["granted_at"]
        assert body["document_version"] == FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        assert body["current_document_version"] == FOOD_DIARY_CONSENT_DOCUMENT_VERSION

        rows = list(_active_rows(bot_user))
        assert len(rows) == 1
        assert rows[0].document_version == FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        assert rows[0].source == "miniapp:food_scanner_consent"
        assert body["granted_at"] == rows[0].captured_at.isoformat()

    def test_grant_without_a_version_is_refused_and_writes_nothing(
        self, client: Client, bot_user: BotUser
    ):
        resp = _grant(client, bot_user, version=None)
        assert resp.status_code == 400
        assert _active_rows(bot_user).count() == 0

    def test_grant_under_an_unknown_version_is_refused_and_writes_nothing(
        self, client: Client, bot_user: BotUser
    ):
        resp = _grant(client, bot_user, version="food-diary-v999")
        assert resp.status_code == 409
        assert _active_rows(bot_user).count() == 0

    def test_the_skill_gate_opens_after_the_grant(
        self, client: Client, bot_user: BotUser, settings
    ):
        """Половина, ради которой ручка написана: петля разомкнулась.

        Гейт спрашивает по порядку: рубильник питания, гейт фото,
        PERSONAL_DATA (DRF-1948) и согласие дневника. Первые три здесь
        подняты намеренно — иначе тест зеленел бы на чужом отказе и ничего
        не говорил бы про согласие дневника.
        """
        settings.NUTRITION_ENABLED = True

        from unittest.mock import Mock

        from apps.consent.services import record_global_consent
        from apps.skills.base import SkillContext
        from apps.skills.food_scanner.skill import _check_gates

        def _ctx(bu: BotUser) -> SkillContext:
            return SkillContext(conversation=Mock(id="conv-consent"), bot_user=bu, message_text="")

        record_global_consent(bot_user, source="test:scanner-gate")
        # ДО: гейт отказывает и просит открыть мини-приложение.
        before = _check_gates(_ctx(bot_user), require_photo_scan=False, kind="callback")
        assert before is not None
        assert before.meta["reply_kind"] == "food_scanner_consent_required"

        _grant(client, bot_user)

        # ПОСЛЕ: отказа нет.
        assert _check_gates(_ctx(bot_user), require_photo_scan=False, kind="callback") is None

    def test_withdraw_closes_the_gate_and_keeps_the_grant_on_record(
        self, client: Client, bot_user: BotUser
    ):
        _grant(client, bot_user)
        # Положительная стража впереди: согласие действительно стояло.
        assert diary_is_granted(bot_user) is True

        resp = client.delete(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        assert resp.status_code == 200
        assert resp.json()["granted"] is False

        assert diary_is_granted(bot_user) is False
        # Колонка стирала факт выдачи — реестр его хранит, отзыв проставлен.
        rows = list(ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=DIARY))
        assert len(rows) == 1
        assert rows[0].withdrawn_at is not None

    def test_grant_is_idempotent(self, client: Client, bot_user: BotUser):
        first = _grant(client, bot_user).json()
        second = _grant(client, bot_user).json()
        assert first["granted"] is True
        assert second["granted"] is True
        assert second["granted_at"] == first["granted_at"]
        assert _active_rows(bot_user).count() == 1

    def test_get_reports_the_state_without_changing_it(self, client: Client, bot_user: BotUser):
        hdr = _init_data_header(bot_user.channel_user_id)
        body = client.get(_url(), HTTP_AUTHORIZATION=hdr).json()
        assert (
            body["current_document_version"] == FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        )  # ответ пришёл
        assert body["granted"] is False
        # Чтение не выдаёт согласия — иначе «посмотреть» значило бы «дать».
        assert ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=DIARY).count() == 0

    def test_the_profile_carries_the_same_value(self, client: Client, bot_user: BotUser):
        """D5: ``/me`` отдаёт дату той же строки реестра — под прежним ключом."""
        hdr = _init_data_header(bot_user.channel_user_id)
        granted_at = _grant(client, bot_user).json()["granted_at"]

        me = client.get(reverse("miniapp_api:me"), HTTP_AUTHORIZATION=hdr).json()
        assert me[LEGACY_ME_CONSENT_KEY] == granted_at

    def test_no_consent_reaches_the_profile_as_null_not_as_a_missing_key(
        self, client: Client, bot_user: BotUser
    ):
        hdr = _init_data_header(bot_user.channel_user_id)
        me = client.get(reverse("miniapp_api:me"), HTTP_AUTHORIZATION=hdr).json()
        # Присутствие впереди: ответ профиля пришёл и он не пуст.
        assert me["bot_user_id"]
        # Ключ ЕСТЬ и он `null` — «спросили, согласия нет».
        assert LEGACY_ME_CONSENT_KEY in me
        assert me[LEGACY_ME_CONSENT_KEY] is None


@pytest.mark.django_db
class TestFoodScannerConsentAcrossShells:
    """Согласие даётся человеком, а не строкой в таблице.

    Чат и мини-приложение — разные ``BotUser`` одного человека. Согласие,
    записанное на одну строку, не открыло бы гейт, читающий другую.
    """

    def test_the_grant_and_the_withdrawal_reach_every_shell(
        self, client: Client, bot_user: BotUser
    ):
        from apps.consent.services import _person_shell_bot_users

        shells = _person_shell_bot_users(bot_user)
        assert len(shells) >= 1
        assert not any(diary_is_granted(s) for s in shells)

        _grant(client, bot_user)
        assert all(diary_is_granted(s) for s in shells)

        client.delete(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        assert not any(diary_is_granted(s) for s in shells)


@pytest.mark.django_db
class TestFoodScannerConsentAudit:
    def test_the_grant_leaves_a_trace_in_the_consent_journal(
        self, client: Client, bot_user: BotUser, django_capture_on_commit_callbacks
    ):
        """Согласие — юридический факт: «кто и когда» обязано остаться.

        Раньше след был отдельной audit-строкой ``consent.food_scanner_changed``
        мимо ``ConsentRecord``. Теперь это общий журнал согласий: строка реестра и
        её audit ``consent.granted`` с типом. Audit пишется ``on_commit`` —
        колбэки исполняются явно, иначе тест зеленел бы на НУЛЕ строк.
        """
        from apps.audit.models import AuditLog

        journal = AuditLog.all_tenants.filter(action="consent.granted")
        before = journal.count()
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            _grant(client, bot_user)
        assert len(callbacks) >= 1  # presence first: the grant scheduled its trace

        new_rows = list(journal.order_by("-created_at")[: journal.count() - before])
        assert len(new_rows) == 1
        assert new_rows[0].payload["consent_type"] == DIARY
        assert new_rows[0].payload["document_version"] == FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        assert AuditLog.all_tenants.filter(action="consent.food_scanner_changed").count() == 0
