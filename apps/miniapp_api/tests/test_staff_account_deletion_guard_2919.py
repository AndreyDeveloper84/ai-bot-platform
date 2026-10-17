"""DRF-2919 — сотрудник не удаляет аккаунт через клиентский профиль.

Удаление адресуется человеку, а не строке: заявка из клиентского профиля
уходит в каталог, и его исполнитель снимает с человека роли, отвязывает
карточку мастера и обезличивает рабочую сторону. Клиентский экран об этом не
предупреждает. Поэтому человеку с действующей рабочей ролью — в любом салоне
— заявка отсюда не заводится: ``staff_account``, повтор не поможет.

Клиентский Mini App подписан клиентским ботом, и строка, с которой пришёл
запрос, у мастера другого салона — клиентская. Отсюда главный случай набора:
«рабочая строка в другом салоне».

Каталог подменён на границе HTTP-клиента, всё выше — настоящее.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_tz

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.bot_user_resolver import (
    SalonChoiceRequired,
    person_holds_working_role,
    resolve_working_bot_user,
)
from apps.identity.services.deletion_gate import deletion_gate
from apps.identity.services.deletion_request import STAFF_ACCOUNT
from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN
from apps.identity.services.role_resolver import resolve_role
from apps.integrations.ayla.personal_context_client import PersonalContextNotFoundError
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2919"  # noqa: S105 — test fixture  # pragma: allowlist secret
STAFF_UID = "2919100"
PLAIN_UID = "2919200"
STAFF_AYLA_ID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d22919")
PLAIN_AYLA_ID = uuid.UUID("1a2b3c4d-5e6f-4a8b-9c0d-e1f2a3b42919")
REQUEST_ID = "7a1b2c3d-0000-4000-8000-000000002919"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str) -> dict:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return {"HTTP_AUTHORIZATION": f"MaxInitData {_sign(params)}"}


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _no_ayla_identity_network():
    with patch(
        "apps.integrations.ayla.identity_client.resolve_identity",
        side_effect=RuntimeError("ayla недоступна в тестах"),
    ):
        yield


@pytest.fixture
def bot_tenant(db, settings) -> Tenant:
    """Салон, чьим ботом подписан клиентский Mini App."""
    tenant = Tenant.objects.create(slug="guard-2919", name="Клиентский", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "guard-2919"
    return tenant


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="guard-2919-solo", name="Студия", timezone="Europe/Moscow")


def _row(tenant: Tenant, uid: str, ayla_id: uuid.UUID, name: str = "Анна") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=uid,
        chat_id=f"chat-{uid}-{tenant.slug}",
        display_name=name,
        ayla_user_id=ayla_id,
    )


def _master_card(tenant: Tenant, row: BotUser, external_id: int = 2919) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        name="Анна",
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        mode=CatalogMaster.Mode.INVITE,
        linked_bot_user=row,
    )


@pytest.fixture
def plain_customer(bot_tenant) -> BotUser:
    return _row(bot_tenant, PLAIN_UID, PLAIN_AYLA_ID, "Клиент")


@pytest.fixture
def customer_row(bot_tenant) -> BotUser:
    """Клиентская строка человека, который где-то ещё — сотрудник."""
    return _row(bot_tenant, STAFF_UID, STAFF_AYLA_ID)


@pytest.fixture
def working_row(other_tenant) -> BotUser:
    """Его рабочая строка в ДРУГОМ салоне: владелец с карточкой мастера."""
    row = _row(other_tenant, STAFF_UID, STAFF_AYLA_ID, "Анна Мастер")
    TenantStaff.all_tenants.create(tenant=other_tenant, bot_user=row, role=TenantStaff.Role.OWNER)
    _master_card(other_tenant, row)
    return row


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
    def __init__(self, *, current=None):
        self._current = current
        self.create_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None

    def create_deletion_request(self, *, ayla_user_id, external_user_id, initiator="bot"):
        self.create_calls.append(ayla_user_id)
        return _wire()

    def get_current_deletion_request(self, *, ayla_user_id, external_user_id):
        if self._current is None:
            raise PersonalContextNotFoundError("none")
        return self._current


@pytest.fixture
def catalog():
    fake = FakeCatalog()
    with patch(
        "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
        lambda *a, **k: fake,
    ):
        yield fake


URL = "miniapp_api:deletion_request"


def _request_deletion(client: Client, uid: str):
    return client.post(
        reverse(URL),
        data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN}),
        content_type="application/json",
        **_auth(uid),
    )


def _assert_refused_as_staff(res) -> None:
    assert res.status_code == 409, res.content
    body = res.json()
    assert body["status"] == "not_started"
    assert body["reason"] == STAFF_ACCOUNT
    assert body["retryable"] is False


class TestDeletionRequestIsRefusedForStaff:
    def test_staff_of_another_salon_is_refused_and_the_catalog_is_not_called(
        self, client, plain_customer, customer_row, working_row, catalog
    ):
        # Положительная пара: тот же каталог, та же ручка — обычному клиенту
        # заявка заводится, и вызов каталога виден.
        accepted = _request_deletion(client, PLAIN_UID)
        assert accepted.status_code == 201, accepted.content
        assert catalog.create_calls == [str(PLAIN_AYLA_ID)]
        assert deletion_gate(PLAIN_AYLA_ID).blocked

        _assert_refused_as_staff(_request_deletion(client, STAFF_UID))

        assert catalog.create_calls == [str(PLAIN_AYLA_ID)]
        # D2: персонализация не выключается заявкой, которой нет.
        assert not deletion_gate(STAFF_AYLA_ID).blocked

    def test_staff_of_the_bot_salon_itself_is_refused(self, client, customer_row, catalog):
        TenantStaff.all_tenants.create(
            tenant=customer_row.tenant, bot_user=customer_row, role=TenantStaff.Role.ADMIN
        )
        _assert_refused_as_staff(_request_deletion(client, STAFF_UID))

    def test_master_by_card_alone_is_refused(self, client, customer_row, other_tenant, catalog):
        row = _row(other_tenant, STAFF_UID, STAFF_AYLA_ID)
        _master_card(other_tenant, row)
        assert resolve_role(row).primary_role == "master"

        _assert_refused_as_staff(_request_deletion(client, STAFF_UID))

    def test_two_working_rows_are_a_refusal_not_an_error(
        self, client, customer_row, working_row, catalog
    ):
        TenantStaff.all_tenants.create(
            tenant=customer_row.tenant, bot_user=customer_row, role=TenantStaff.Role.RECEPTIONIST
        )
        with pytest.raises(SalonChoiceRequired):
            resolve_working_bot_user(STAFF_UID)

        _assert_refused_as_staff(_request_deletion(client, STAFF_UID))

    def test_not_linked_staff_is_refused_as_staff(self, client, bot_tenant, other_tenant, catalog):
        # Причина «сотрудник» важнее причины «не связан с Ayla»: до Ayla дело не доходит.
        BotUser.all_tenants.create(
            tenant=bot_tenant, channel="max", channel_user_id=STAFF_UID, chat_id="c-1"
        )
        row = BotUser.all_tenants.create(
            tenant=other_tenant, channel="max", channel_user_id=STAFF_UID, chat_id="c-2"
        )
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=row, role=TenantStaff.Role.ADMIN
        )
        _assert_refused_as_staff(_request_deletion(client, STAFF_UID))


class TestFormerStaffIsACustomer:
    def test_revoked_staff_with_an_archived_card_may_delete(
        self, client, customer_row, working_row, catalog
    ):
        _assert_refused_as_staff(_request_deletion(client, STAFF_UID))

        TenantStaff.all_tenants.filter(bot_user=working_row).update(deactivated_at=dj_tz.now())
        CatalogMaster.all_tenants.filter(linked_bot_user=working_row).update(
            archived_at=dj_tz.now()
        )

        res = _request_deletion(client, STAFF_UID)
        assert res.status_code == 201, res.content
        assert catalog.create_calls == [str(STAFF_AYLA_ID)]

    def test_a_soft_deleted_working_row_does_not_count(
        self, client, customer_row, working_row, catalog
    ):
        assert person_holds_working_role(customer_row) is True
        BotUser.all_tenants.filter(pk=working_row.pk).update(deleted_at=dj_tz.now())

        assert person_holds_working_role(customer_row) is False
        assert _request_deletion(client, STAFF_UID).status_code == 201


def test_staff_still_sees_a_request_that_already_exists(client, customer_row, working_row):
    """``GET`` открыт: заявку, заведённую до стража или поддержкой, человек видит."""
    fake = FakeCatalog(current=_wire())
    with patch(
        "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
        lambda *a, **k: fake,
    ):
        res = client.get(reverse(URL), **_auth(STAFF_UID))
    assert res.status_code == 200, res.content
    assert res.json()["status"] == "found"
    assert res.json()["request"]["request_id"] == REQUEST_ID


class TestLegacySoftDelete:
    """``POST /me/delete`` экран не зовёт, но ручка открыта."""

    def _delete(self, client: Client, uid: str):
        return client.post(
            reverse("miniapp_api:delete_me"),
            data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN}),
            content_type="application/json",
            **_auth(uid),
        )

    def test_staff_is_refused_and_keeps_the_row(self, client, plain_customer, customer_row):
        TenantStaff.all_tenants.create(
            tenant=customer_row.tenant, bot_user=customer_row, role=TenantStaff.Role.ADMIN
        )
        # Положительная пара: обычного клиента та же ручка удаляет.
        assert self._delete(client, PLAIN_UID).status_code == 200
        plain_customer.refresh_from_db()
        assert plain_customer.deleted_at is not None

        res = self._delete(client, STAFF_UID)

        assert res.status_code == 409, res.content
        assert res.json()["error"] == "staff_account"
        customer_row.refresh_from_db()
        assert customer_row.deleted_at is None
        assert customer_row.display_name == "Анна"
        # Кабинет на месте: до стража строка переставала быть рабочей при живом TenantStaff.
        assert resolve_working_bot_user(STAFF_UID) == customer_row


class TestProfileSaysWhetherDeletionIsOffered:
    def _me(self, client: Client, uid: str) -> dict:
        res = client.get(reverse("miniapp_api:me"), **_auth(uid))
        assert res.status_code == 200, res.content
        return res.json()

    def test_customer_true_staff_false_former_staff_true(
        self, client, plain_customer, customer_row, working_row
    ):
        assert self._me(client, PLAIN_UID)["account_deletion_available"] is True
        assert self._me(client, STAFF_UID)["account_deletion_available"] is False

        TenantStaff.all_tenants.filter(bot_user=working_row).update(deactivated_at=dj_tz.now())
        CatalogMaster.all_tenants.filter(linked_bot_user=working_row).update(
            archived_at=dj_tz.now()
        )
        assert self._me(client, STAFF_UID)["account_deletion_available"] is True

    def test_patch_answers_with_the_same_shape(self, client, customer_row, working_row):
        res = client.patch(
            reverse("miniapp_api:me"),
            data=json.dumps({"timezone": "Europe/Samara"}),
            content_type="application/json",
            **_auth(STAFF_UID),
        )
        assert res.status_code == 200, res.content
        assert res.json()["timezone"] == "Europe/Samara"
        assert res.json()["account_deletion_available"] is False


class TestOneDefinitionOfWorking:
    """Страж и резолвер кабинета отвечают на вопрос «рабочая ли строка» одинаково."""

    def _resolver_says_working(self, uid: str) -> bool:
        try:
            return resolve_working_bot_user(uid) is not None
        except SalonChoiceRequired:
            return True

    def test_the_guard_agrees_with_the_cabinet_resolver(
        self, customer_row, working_row, plain_customer
    ):
        assert person_holds_working_role(plain_customer) is False
        assert self._resolver_says_working(PLAIN_UID) is False

        for step in ("owner with a card", "second working row", "revoked", "archived"):
            if step == "second working row":
                TenantStaff.all_tenants.create(
                    tenant=customer_row.tenant, bot_user=customer_row, role=TenantStaff.Role.ADMIN
                )
            elif step == "revoked":
                TenantStaff.all_tenants.all().update(deactivated_at=dj_tz.now())
            elif step == "archived":
                CatalogMaster.all_tenants.all().update(archived_at=dj_tz.now())
            for row in (customer_row, working_row):
                assert person_holds_working_role(row) is self._resolver_says_working(STAFF_UID), (
                    step
                )
        # Последний шаг дошёл до «не рабочая»: сравнение было не на одном значении.
        assert person_holds_working_role(customer_row) is False

    def test_a_row_without_a_messenger_id_is_judged_alone(self, bot_tenant, working_row):
        lonely = BotUser.all_tenants.create(
            tenant=bot_tenant, channel="max", channel_user_id="", chat_id="lonely"
        )
        assert person_holds_working_role(working_row) is True
        assert person_holds_working_role(lonely) is False


def test_storage_revocation_is_not_a_way_around(customer_row, working_row):
    """«Отозвать хранение» роль не снимает — это не удаление аккаунта, страж его не трогает."""
    from apps.identity.services.privacy import delete_personal_data

    class _Ayla:
        def __init__(self):
            self.deleted: list[str] = []

        def delete_personal_data(self, *, ayla_user_id, external_user_id):
            self.deleted.append(str(ayla_user_id))

        def close(self):
            return None

    ayla = _Ayla()
    delete_personal_data(customer_row, client=ayla)

    assert ayla.deleted, "каскад отзыва должен был дойти до Ayla"
    working_row.refresh_from_db()
    assert working_row.deleted_at is None
    assert resolve_role(working_row).primary_role == "owner"
    assert person_holds_working_role(customer_row) is True
