"""DRF-2919 п.2 — двери удаления спрашивают ``deletion_eligibility``.

``POST /me/deletion-request/``, ``POST /me/delete`` и признак
``account_deletion_available`` в ``/customer/me``: необратимое начинается,
только если удаление ПДн может начаться сейчас. Иначе — ``not_started`` с
оценкой по операциям, каталог не вызывается, флаг D2 не ставится.

Основание — обязательства, а не роль: брошенный соло-кабинет и владелец с
персоналом (база §5) удаляются; мастер со своей будущей записью — нет.

Каталог и биллинг Ayla подменены на границе, всё выше — настоящее.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_tz

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.deletion_gate import deletion_gate
from apps.identity.services.deletion_request import (
    OBLIGATIONS_UNSETTLED,
    VERIFICATION_REQUIRED,
)
from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN
from apps.identity.services.solo_onboarding import create_solo_provider
from apps.integrations.ayla.personal_context_client import PersonalContextNotFoundError
from apps.master_api.services.billing import BillingProxyResult, ProxyStatus
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-doors-2919"  # noqa: S105 — test fixture  # pragma: allowlist secret
PERSON_UID = "2919600"
PLAIN_UID = "2919700"
PERSON_AYLA_ID = uuid.UUID("3d4e5f6a-7b8c-4d9e-8f0a-b1c2d3e42919")
PLAIN_AYLA_ID = uuid.UUID("4e5f6a7b-8c9d-4e0f-9a1b-c2d3e4f52919")
REQUEST_ID = "8b2c3d4e-0000-4000-8000-000000002919"
BILLING = "apps.master_api.services.billing"


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
def billing():
    """Ayla C2/C3 мастера: по умолчанию долгов и выплат нет."""
    state = {
        "status": BillingProxyResult(
            status=ProxyStatus.OK,
            payload={"subscription": {"status": "active"}, "fees": {"pending_total": "0.00"}},
        )
    }
    with (
        patch(f"{BILLING}.billing_status_for_master", lambda card, **k: state["status"]),
        patch(
            f"{BILLING}.payout_preview_for_master",
            lambda card, **k: BillingProxyResult(
                status=ProxyStatus.OK, payload={"pending_amount": "0.00"}
            ),
        ),
    ):
        yield state


@pytest.fixture
def bot_tenant(db, settings) -> Tenant:
    tenant = Tenant.objects.create(slug="doors-2919", name="Клиентский", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "doors-2919"
    return tenant


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="doors-2919-work", name="Студия", timezone="Europe/Moscow")


def _row(tenant: Tenant, uid: str, ayla_id: uuid.UUID | None, name: str = "Анна") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=uid,
        chat_id=f"chat-{uid}-{tenant.slug}",
        display_name=name,
        ayla_user_id=ayla_id,
    )


@pytest.fixture
def plain_customer(bot_tenant) -> BotUser:
    return _row(bot_tenant, PLAIN_UID, PLAIN_AYLA_ID, "Клиент")


@pytest.fixture
def customer_row(bot_tenant) -> BotUser:
    """Клиентская строка человека, который в другом салоне — мастер."""
    return _row(bot_tenant, PERSON_UID, PERSON_AYLA_ID)


@pytest.fixture
def master_card(other_tenant) -> CatalogMaster:
    """Его рабочая строка в другом салоне: карточка мастера с ключом Ayla."""
    row = _row(other_tenant, PERSON_UID, None, "Анна Мастер")
    return CatalogMaster.all_tenants.create(
        tenant=other_tenant,
        external_id=2919,
        external_updated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        name="Анна",
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        mode=CatalogMaster.Mode.INVITE,
        linked_bot_user=row,
        ayla_user_id=PERSON_AYLA_ID,
    )


def _booking_for(card: CatalogMaster) -> RemoteBookingProxy:
    start = dj_tz.now() + timedelta(days=2)
    return RemoteBookingProxy.all_tenants.create(
        tenant=card.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=card.id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )


def _wire() -> dict:
    return {
        "created": True,
        "request_id": REQUEST_ID,
        "status": "DELETION_REQUESTED",
        "requested_at": "2026-10-19T14:00:00+00:00",
        "deadline_at": "2026-11-18T14:00:00+00:00",
        "completed_at": None,
        "is_open": True,
    }


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


def _erasure(body: dict) -> dict:
    return body["eligibility"]["operations"]["PERSONAL_DATA_ERASURE"]


class TestDeletionRequestDoor:
    def test_master_with_a_booking_is_not_started_and_the_catalog_is_not_called(
        self, client, plain_customer, customer_row, master_card, catalog, billing
    ):
        accepted = _request_deletion(client, PLAIN_UID)
        assert accepted.status_code == 201, accepted.content
        assert accepted.json()["eligibility"]["request_accepted"] is True
        assert catalog.create_calls == [str(PLAIN_AYLA_ID)]

        _booking_for(master_card)
        res = _request_deletion(client, PERSON_UID)

        assert res.status_code == 409, res.content
        body = res.json()
        assert body["status"] == "not_started"
        assert body["reason"] == OBLIGATIONS_UNSETTLED
        assert body["retryable"] is False
        assert body["eligibility"]["request_accepted"] is True
        assert _erasure(body)["outcome"] == "SETTLE_FIRST"
        assert _erasure(body)["reasons"] == ["ACTIVE_BOOKINGS"]
        assert _erasure(body)["next_actions"] == ["SETTLE_BOOKINGS"]
        assert catalog.create_calls == [str(PLAIN_AYLA_ID)]
        assert not deletion_gate(PERSON_AYLA_ID).blocked

    def test_the_same_master_without_obligations_is_accepted(
        self, client, customer_row, master_card, catalog, billing
    ):
        """Роль сама по себе не держит: мастер без записей и долгов удаляется."""
        res = _request_deletion(client, PERSON_UID)
        assert res.status_code == 201, res.content
        assert catalog.create_calls == [str(PERSON_AYLA_ID)]
        assert deletion_gate(PERSON_AYLA_ID).blocked

    def test_unverifiable_finances_are_not_started(
        self, client, customer_row, master_card, catalog, billing
    ):
        billing["status"] = BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR)
        res = _request_deletion(client, PERSON_UID)
        assert res.status_code == 409, res.content
        assert res.json()["reason"] == VERIFICATION_REQUIRED
        assert _erasure(res.json())["next_actions"] == ["CONTACT_SUPPORT"]
        assert catalog.create_calls == []

    def test_abandoned_solo_is_accepted(self, client, customer_row, catalog, billing):
        create_solo_provider(channel="max", channel_user_id=PERSON_UID, display_name="Соло")
        res = _request_deletion(client, PERSON_UID)
        assert res.status_code == 201, res.content
        closure = res.json()["eligibility"]["operations"]["CABINET_CLOSURE"]
        assert closure["next_actions"] == ["CLOSE_EMPTY_CABINET"]

    def test_owner_with_staff_is_accepted_with_the_transfer_named(
        self, client, customer_row, other_tenant, catalog, billing
    ):
        """База §5: обязательство кабинета не личное. Решение владельца — в одной политике."""
        owner_row = _row(other_tenant, PERSON_UID, None)
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=owner_row, role=TenantStaff.Role.OWNER
        )
        colleague = _row(other_tenant, "2919800", None, "Коллега")
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=colleague, role=TenantStaff.Role.ADMIN
        )
        res = _request_deletion(client, PERSON_UID)
        assert res.status_code == 201, res.content
        ops = res.json()["eligibility"]["operations"]
        assert ops["STAFF_EXIT"]["reasons"] == ["STAFF_TRANSFER_REQUIRED"]
        assert ops["PERSONAL_DATA_ERASURE"]["outcome"] == "PARTIAL_WITH_RETENTION"


def test_a_request_that_already_exists_stays_visible(client, customer_row, master_card, billing):
    _booking_for(master_card)
    fake = FakeCatalog(current=_wire())
    with patch(
        "apps.integrations.ayla.personal_context_client.PersonalContextHttpClient",
        lambda *a, **k: fake,
    ):
        res = client.get(reverse(URL), **_auth(PERSON_UID))
    assert res.status_code == 200, res.content
    assert res.json()["request"]["request_id"] == REQUEST_ID


class TestLegacySoftDelete:
    def _delete(self, client: Client, uid: str):
        return client.post(
            reverse("miniapp_api:delete_me"),
            data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN}),
            content_type="application/json",
            **_auth(uid),
        )

    def test_master_with_a_booking_keeps_the_row(
        self, client, plain_customer, customer_row, master_card, billing
    ):
        _booking_for(master_card)
        assert self._delete(client, PLAIN_UID).status_code == 200
        plain_customer.refresh_from_db()
        assert plain_customer.deleted_at is not None

        res = self._delete(client, PERSON_UID)

        assert res.status_code == 409, res.content
        assert res.json()["error"] == "obligations_unsettled"
        assert _erasure(res.json())["reasons"] == ["ACTIVE_BOOKINGS"]
        customer_row.refresh_from_db()
        assert customer_row.deleted_at is None
        assert customer_row.display_name == "Анна"


class TestImmediateErasureDoor:
    """``DELETE /me/personal-data/`` — каскад без окна заявки, необратим сразу."""

    @pytest.fixture
    def cascade(self):
        from apps.identity.services.privacy import DeleteCascadeResult

        calls: list[BotUser] = []

        def _run(bot_user, **_k):
            calls.append(bot_user)
            return DeleteCascadeResult()

        with patch("apps.identity.services.privacy.delete_personal_data", _run):
            yield calls

    def _erase(self, client: Client, uid: str):
        return client.delete(
            reverse("miniapp_api:personal_data_delete"),
            data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN}),
            content_type="application/json",
            **_auth(uid),
        )

    def test_master_with_a_booking_is_not_started_and_nothing_runs(
        self, client, plain_customer, customer_row, master_card, billing, cascade
    ):
        _booking_for(master_card)
        assert self._erase(client, PLAIN_UID).status_code == 200
        assert cascade == [plain_customer]

        res = self._erase(client, PERSON_UID)

        assert res.status_code == 409, res.content
        body = res.json()
        assert body["status"] == "not_started"
        assert body["reason"] == OBLIGATIONS_UNSETTLED
        assert body["retryable"] is False
        assert _erasure(body)["reasons"] == ["ACTIVE_BOOKINGS"]
        assert cascade == [plain_customer]

    def test_own_debt_unverifiable_is_not_started(
        self, client, customer_row, master_card, billing, cascade
    ):
        billing["status"] = BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR)
        res = self._erase(client, PERSON_UID)
        assert res.status_code == 409, res.content
        assert res.json()["reason"] == VERIFICATION_REQUIRED
        assert cascade == []

    def test_the_same_master_without_obligations_is_erased(
        self, client, customer_row, master_card, billing, cascade
    ):
        assert self._erase(client, PERSON_UID).status_code == 200
        assert cascade == [customer_row]

    def test_a_wrong_token_touches_nothing_before_the_assessment(
        self, client, customer_row, master_card, billing, cascade
    ):
        _booking_for(master_card)
        res = client.delete(
            reverse("miniapp_api:personal_data_delete"),
            data=json.dumps({"confirmation": "нет"}),
            content_type="application/json",
            **_auth(PERSON_UID),
        )
        assert res.status_code == 400
        assert cascade == []


class TestProfileSaysWhetherDeletionIsOffered:
    def _me(self, client: Client, uid: str) -> dict:
        res = client.get(reverse("miniapp_api:me"), **_auth(uid))
        assert res.status_code == 200, res.content
        return res.json()

    def test_follows_obligations_not_the_role(
        self, client, plain_customer, customer_row, master_card, billing
    ):
        assert self._me(client, PLAIN_UID)["account_deletion_available"] is True
        assert self._me(client, PERSON_UID)["account_deletion_available"] is True

        booking = _booking_for(master_card)
        assert self._me(client, PERSON_UID)["account_deletion_available"] is False

        RemoteBookingProxy.all_tenants.filter(pk=booking.pk).update(
            status=RemoteBookingProxy.Status.CANCELLED
        )
        assert self._me(client, PERSON_UID)["account_deletion_available"] is True

    def test_patch_answers_with_the_same_shape(self, client, customer_row, master_card, billing):
        _booking_for(master_card)
        res = client.patch(
            reverse("miniapp_api:me"),
            data=json.dumps({"timezone": "Europe/Samara"}),
            content_type="application/json",
            **_auth(PERSON_UID),
        )
        assert res.status_code == 200, res.content
        assert res.json()["timezone"] == "Europe/Samara"
        assert res.json()["account_deletion_available"] is False
