"""Новый мастер по ссылке-приглашению под подписью САЛОННОГО бота (DRF-1503).

Дефект (подтверждён 13.11.2026 на dev 7318fd53): владелец приглашает нового
мастера ссылкой, человек открывает её в салонном боте → «Принять
приглашение» → Mini App пишет «Не удалось загрузить приглашение».
``/onboarding/claim`` и ``/accept`` отвечали 404 ``user_not_registered``:
бот, доставляя приглашение, строки человеку не заводит (D2→(б)), а с
DRF-1785 подпись салонного бота без РАБОЧЕЙ строки — «никто». Не спасал и
``/me``: он заводит клиентскую строку, а она не рабочая.

Все прежние тесты онбординга подписаны легаси-токеном (``bot_slug=""`` —
не поверхность сотрудников) и создают строку заранее, поэтому путь был
зелёным. Здесь — подпись салонного бота и человек, которого платформа ещё
не знает.

Правило правки: салон называет ТОКЕН (DRF-1784); строку человека в этом
салоне, если она есть, проверяют все прежние проверки; если нет — она
рождается только на accept, после всех отказов (как при погашении кода
сотрудника). Claim и «Это не я» строк не оставляют.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.master_api.tests.conftest import _sign, make_master
from apps.tenancy.billing_gate import MasterAddRefused, MasterAddVerdict
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PERSON = "777000111"


def _signed(uid: str, *, token: str, first: str = "Ольга", last: str = "Петрова") -> str:
    params = {
        "user": json.dumps({"id": int(uid), "first_name": first, "last_name": last}),
        "auth_date": str(int(time.time())),
    }
    return f"MaxInitData {_sign(params, token=token)}"


def salon_signed(uid: str = PERSON) -> str:
    return _signed(uid, token="tok-salon")


@pytest.fixture(autouse=True)
def _bots(settings, tenant: Tenant) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token="tok-salon",  # pragma: allowlist secret
            tenant_slug="",
            stream="max_salon",
        ),
        BotEntry(
            slug="client",
            webhook_secret="wh-client",  # pragma: allowlist secret
            api_token="tok-client",  # pragma: allowlist secret
            tenant_slug=tenant.slug,
            stream="max",
        ),
    )
    settings.MAX_BOT_TENANT_SLUG = tenant.slug


def _post(client: Client, name: str, token: object, header: str, **extra) -> Any:
    return client.post(
        reverse(name),
        data=json.dumps({"token": str(token)}),
        content_type="application/json",
        HTTP_AUTHORIZATION=header,
        **extra,
    )


def _rows() -> list[BotUser]:
    return list(BotUser.all_tenants.filter(channel="max", channel_user_id=PERSON))


class TestTheNewMasterGetsIn:
    def test_claim_accept_and_the_cabinet(self, client: Client, tenant: Tenant) -> None:
        # Как пишет приглашение прод (admin_api/views_invite.py): неактивной.
        master = make_master(tenant, is_active=False)
        assert _rows() == []

        claim = _post(client, "master_api:onboarding_claim", master.invite_token, salon_signed())
        assert claim.status_code == 200, claim.content
        assert claim.json()["master"]["id"] == str(master.id)
        assert claim.json()["max_user"]["first_name"] == "Ольга"
        # Claim — не погашение: строки не появилось (D2→(б)).
        assert _rows() == []

        accept = _post(client, "master_api:onboarding_accept", master.invite_token, salon_signed())
        assert accept.status_code == 200, accept.content
        assert accept.json()["master_id"] == str(master.id)

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.tenant_id == tenant.id
        assert row.display_name == "Ольга Петрова"
        master.refresh_from_db()
        assert master.linked_bot_user_id == row.id
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert master.is_active is True
        assert master.invite_token is None

        # Кабинет мастера под той же подписью — теперь строка рабочая.
        me = client.get(reverse("master_api:me"), HTTP_AUTHORIZATION=salon_signed())
        assert me.status_code == 200, me.content

    def test_a_second_accept_is_the_same_master(self, client: Client, tenant: Tenant) -> None:
        master = make_master(tenant)
        token = master.invite_token
        first = _post(client, "master_api:onboarding_accept", token, salon_signed())
        assert first.status_code == 200, first.content

        again = _post(client, "master_api:onboarding_accept", token, salon_signed())
        assert again.status_code == 200, again.content
        assert again.json()["master_id"] == str(master.id)
        assert len(_rows()) == 1


class TestTheRowFromMeIsReused:
    def test_me_first_then_accept_uses_that_row(self, client: Client, tenant: Tenant) -> None:
        # /me под салонной подписью заводит клиентскую строку в MAX_BOT_TENANT_SLUG —
        # это и есть салон приглашения: её и берём, без дубля.
        assert (
            client.get(reverse("identity:me"), HTTP_AUTHORIZATION=salon_signed()).status_code == 200
        )
        (customer_row,) = _rows()
        master = make_master(tenant)

        accept = _post(client, "master_api:onboarding_accept", master.invite_token, salon_signed())
        assert accept.status_code == 200, accept.content
        assert _rows() == [customer_row]
        master.refresh_from_db()
        assert master.linked_bot_user_id == customer_row.id

    def test_a_row_in_another_salon_stays_there(self, client: Client, tenant: Tenant) -> None:
        other = Tenant.objects.create(
            slug="other-salon", name="Другой салон", timezone="Europe/Moscow"
        )
        stranger_row = BotUser.all_tenants.create(
            tenant=other, channel="max", channel_user_id=PERSON, display_name="Ольга"
        )
        master = make_master(tenant)

        accept = _post(client, "master_api:onboarding_accept", master.invite_token, salon_signed())
        assert accept.status_code == 200, accept.content
        stranger_row.refresh_from_db()
        assert stranger_row.tenant_id == other.id
        landed = BotUser.all_tenants.get(tenant=tenant, channel_user_id=PERSON)
        master.refresh_from_db()
        assert master.linked_bot_user_id == landed.id


class TestRefusalsLeaveNothingBehind:
    @pytest.mark.parametrize(
        ("make", "status", "slug"),
        [
            (lambda t: None, 404, "invalid_invite_token"),
            (lambda t: make_master(t, expires_in_days=-1), 410, "invite_expired"),
        ],
    )
    def test_a_bad_token_creates_no_row(
        self, client: Client, tenant: Tenant, make, status, slug
    ) -> None:
        master = make(tenant)
        token = (
            master.invite_token if master is not None else "8b1f5c58-1c1e-4f3b-9d6c-6a0d2c0f3e11"
        )
        for name in ("master_api:onboarding_claim", "master_api:onboarding_accept"):
            response = _post(client, name, token, salon_signed())
            assert response.status_code == status, (name, response.content)
            assert response.json()["error"] == slug
        assert _rows() == []

    def test_a_used_invitation_creates_no_row(self, client: Client, tenant: Tenant) -> None:
        someone = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="555")
        master = make_master(tenant)
        token = master.invite_token
        CatalogMaster.all_tenants.filter(pk=master.pk).update(
            invite_status=CatalogMaster.InviteStatus.ACCEPTED, linked_bot_user=someone
        )

        response = _post(client, "master_api:onboarding_accept", token, salon_signed())
        assert response.status_code == 409, response.content
        assert response.json()["error"] == "invite_already_used"
        assert _rows() == []

    def test_billing_refusal_creates_no_row(self, client: Client, tenant: Tenant) -> None:
        master = make_master(tenant)
        refusal = MasterAddRefused(MasterAddVerdict.NEEDS_PLAN_CHANGE)
        with patch("apps.master_api.views.assert_can_add_master", side_effect=refusal):
            response = _post(
                client,
                "master_api:onboarding_accept",
                master.invite_token,
                salon_signed(),
            )
        assert response.status_code == 409, response.content
        assert response.json()["error"] == "plan_change_required"
        assert _rows() == []
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.invite_token is not None

    def test_reject_leaves_no_row_and_audits_in_the_salon(
        self, client: Client, tenant: Tenant
    ) -> None:
        master = make_master(tenant)
        response = _post(
            client, "master_api:onboarding_reject", master.invite_token, salon_signed()
        )
        assert response.status_code == 204, response.content
        assert _rows() == []
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.CANCELLED
        row = AuditLog.all_tenants.get(action="master.onboarding_rejected", target_id=master.id)
        assert row.tenant_id == tenant.id
        assert row.actor_id is None


class TestTheRowInTheSalonIsChecked:
    def test_a_deleted_account_is_refused_and_not_touched(
        self, client: Client, tenant: Tenant
    ) -> None:
        when = timezone.now() - timedelta(days=3)
        deleted = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=PERSON,
            display_name="",
            chat_id="",
        )
        BotUser.all_tenants.filter(pk=deleted.pk).update(deleted_at=when, last_seen=when)
        master = make_master(tenant)

        for name in ("master_api:onboarding_claim", "master_api:onboarding_accept"):
            response = _post(client, name, master.invite_token, salon_signed())
            assert response.status_code == 403, (name, response.content)
            assert response.json()["error"] == "user_deleted"
        deleted.refresh_from_db()
        assert deleted.display_name == ""  # стёртое по 152-ФЗ не дописано обратно
        assert deleted.last_seen == when
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_a_row_tied_to_an_archived_card_is_refused_not_500(
        self, client: Client, tenant: Tenant
    ) -> None:
        row = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id=PERSON)
        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            linked_bot_user=row,
            archived_at=timezone.now(),
        )
        master = make_master(tenant, name="Новая карточка")

        response = _post(
            client, "master_api:onboarding_accept", master.invite_token, salon_signed()
        )
        assert response.status_code == 403, response.content
        assert response.json()["error"] == "wrong_recipient"
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.linked_bot_user_id is None


class TestTheTokenNotTheHeaderNamesTheSalon:
    def test_a_foreign_x_tenant_does_not_move_the_audit_or_the_row(
        self, client: Client, tenant: Tenant
    ) -> None:
        other = Tenant.objects.create(slug="x-tenant-salon", name="Чужой", timezone="Europe/Moscow")
        master = make_master(tenant)

        accept = _post(
            client,
            "master_api:onboarding_accept",
            master.invite_token,
            salon_signed(),
            HTTP_X_TENANT=other.slug,
        )
        assert accept.status_code == 200, accept.content
        (row,) = _rows()
        assert row.tenant_id == tenant.id
        audit = AuditLog.all_tenants.get(action="master.onboarding_accepted", target_id=master.id)
        assert audit.tenant_id == tenant.id
        events = Event.objects.filter(event_name="master.onboarding_accepted")
        assert events.exists()  # присутствие
        assert set(events.values_list("tenant_id", flat=True)) == {tenant.id}

    def test_claim_and_reject_under_a_foreign_x_tenant_audit_in_the_salon(
        self, client: Client, tenant: Tenant
    ) -> None:
        other = Tenant.objects.create(slug="x-tenant-2", name="Чужой-2", timezone="Europe/Moscow")
        master = make_master(tenant)
        for name in ("master_api:onboarding_claim", "master_api:onboarding_reject"):
            response = _post(
                client, name, master.invite_token, salon_signed(), HTTP_X_TENANT=other.slug
            )
            assert response.status_code in (200, 204), (name, response.content)
        rows = AuditLog.all_tenants.filter(target_id=master.id)
        assert {r.action for r in rows} == {
            "master.onboarding_started",
            "master.onboarding_rejected",
        }
        assert {r.tenant_id for r in rows} == {tenant.id}


class TestTheRowIsBornInTheLandingTransaction:
    def test_a_crash_after_the_row_takes_the_row_back(self, client: Client, tenant: Tenant) -> None:
        master = make_master(tenant)
        client.raise_request_exception = False
        with patch("apps.master_api.views.write_audit", side_effect=RuntimeError("boom")):
            response = _post(
                client, "master_api:onboarding_accept", master.invite_token, salon_signed()
            )
        assert response.status_code == 500
        assert _rows() == []  # строка создана в транзакции приземления — и откатилась с ней
        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.invite_token is not None


class TestTheGlueStillWorksForTheRowFromMe:
    """DRF-1507: строка человека в салоне с настоящим ключом Ayla — склейка с синхронизированной карточкой."""

    def _customer_with_key(self, tenant: Tenant, key: uuid.UUID) -> BotUser:
        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=PERSON,
            ayla_user_id=key,
            ayla_user_id_is_proxy=False,
        )

    def test_the_invitation_lands_on_the_synced_card(self, client: Client, tenant: Tenant) -> None:
        key = uuid.uuid4()
        row = self._customer_with_key(tenant, key)
        synced = make_master(
            tenant,
            name="Анна (из каталога)",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
        )
        CatalogMaster.all_tenants.filter(pk=synced.pk).update(ayla_user_id=key)
        invite = make_master(tenant)

        response = _post(
            client, "master_api:onboarding_accept", invite.invite_token, salon_signed()
        )
        assert response.status_code == 200, response.content
        assert response.json()["master_id"] == str(synced.id)
        synced.refresh_from_db()
        invite.refresh_from_db()
        assert synced.linked_bot_user_id == row.id
        assert invite.invite_status == CatalogMaster.InviteStatus.CANCELLED
        assert invite.raw["superseded_by_master_id"] == str(synced.id)
        assert _rows() == [row]

    def test_a_synced_card_of_someone_else_is_refused(self, client: Client, tenant: Tenant) -> None:
        key = uuid.uuid4()
        self._customer_with_key(tenant, key)
        someone = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="555")
        synced = make_master(
            tenant,
            name="Анна (из каталога)",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=someone,
        )
        CatalogMaster.all_tenants.filter(pk=synced.pk).update(ayla_user_id=key)
        invite = make_master(tenant)

        response = _post(
            client, "master_api:onboarding_accept", invite.invite_token, salon_signed()
        )
        assert response.status_code == 403, response.content
        assert response.json()["error"] == "wrong_recipient"
        invite.refresh_from_db()
        assert invite.invite_status == CatalogMaster.InviteStatus.PENDING


def test_only_the_three_onboarding_routes_let_an_invitee_in() -> None:
    """Режим «без строки» — только у claim/accept/reject: четвёртая ручка с тем же
    декоратором молча получила бы ``bot_user=None``."""
    from apps.master_api import urls as master_urls
    from apps.miniapp_api.transport_refusal import GUARD_ATTR

    marked = sorted(
        str(pattern.name)
        for pattern in master_urls.urlpatterns
        if getattr(getattr(pattern, "callback", None), GUARD_ATTR, None) == "master_onboarding"
    )
    assert marked == ["onboarding_accept", "onboarding_claim", "onboarding_reject"]


class TestOnlyTheSalonSignatureIsLetIn:
    def test_a_legacy_signature_without_a_row_is_still_404(
        self, client: Client, tenant: Tenant
    ) -> None:
        master = make_master(tenant)
        legacy = _signed(PERSON, token="test-bot-token-xyz")  # conftest MAX_BOT_TOKEN
        response = _post(client, "master_api:onboarding_claim", master.invite_token, legacy)
        assert response.status_code == 404, response.content
        assert response.json()["error"] == "user_not_registered"

    def test_the_client_bot_without_a_row_is_still_404(
        self, client: Client, tenant: Tenant
    ) -> None:
        master = make_master(tenant)
        response = _post(
            client,
            "master_api:onboarding_accept",
            master.invite_token,
            _signed(PERSON, token="tok-client"),
        )
        assert response.status_code == 404, response.content
        assert _rows() == []
