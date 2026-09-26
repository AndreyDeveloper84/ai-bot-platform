"""DRF-2436 часть A — Mini App проверяет владение записью ЧЕЛОВЕКОМ, а не одной личностью.

Личность бота — «channel-scoped identity inside a tenant»: переход к записи в
салон T заводит личность в T. Mini App узнаёт человека под ОДНИМ салоном
(``MAX_BOT_TENANT_SLUG``; на пилоте — formula-tela), и деталь, отмена и оплата
искали зеркало по этой одной личности. Запись в другом салоне лежит под
личностью того салона, поэтому она была «не найдена» — 24.09 так ответил экран
переноса на живую запись в «Люмине».

Теперь владение — все личности того же подписанного аккаунта (тот же канал и
внешний id; Ayla видит человека так же — ``bot:max:<id>``). Множество выводится
только из личности, полученной из подписи ``initData``.

Узлы в обе стороны:

* запись в ДРУГОМ салоне находится, отменяется и оплачивается;
* запись ДРУГОГО человека не находится — с положительной парой: своя в том же
  салоне находится (иначе «не находится» проходило бы и у ручки, которая не
  находит ничего);
* запись под личностью настроенного салона работает, как раньше;
* перенос отвечает 409 ОДИНАКОВО для своего и чужого салона: починка детали не
  маскирует отсутствие шва переноса (DRF-2561).

Список «Мои записи» и «ближайшая запись» — часть B (§6-омега-бис), здесь не
трогаются.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_ayla_read_model import (
    BOT_TOKEN,
    _init_data_header,
    _StubCancelClient,
)
from apps.miniapp_api.tests.test_c7_payments import _StubC7Client
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

ME = "12345"
STRANGER = "777"
AYLA_UID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "home-2436"  # салон, под которым Mini App узнаёт человека
    settings.BOOKING_VIA_AYLA_REST = True
    settings.AYLA_CLIENT_PAYMENTS_RETURN_URL = "https://miniapp.test/return"


@pytest.fixture
def home(db) -> Tenant:
    return Tenant.objects.create(slug="home-2436", name="Home", address="ул. Домашняя, 1")


@pytest.fixture
def lumina(db) -> Tenant:
    return Tenant.objects.create(slug="lumina-2436", name="Люмина", address="ул. Светлая, 7")


def _identity(tenant: Tenant, channel_user_id: str, ayla_user_id=None) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=channel_user_id,
        ayla_user_id=ayla_user_id or uuid.uuid4(),
    )


@pytest.fixture
def me_home(home) -> BotUser:
    return _identity(home, ME, AYLA_UID)


@pytest.fixture
def me_lumina(lumina) -> BotUser:
    # Та же подпись (канал + внешний id), другой салон — как handoff.py заводит её.
    return _identity(lumina, ME, AYLA_UID)


@pytest.fixture
def stranger_lumina(lumina) -> BotUser:
    return _identity(lumina, STRANGER)


def _proxy(tenant: Tenant, bot_user: BotUser) -> RemoteBookingProxy:
    start = timezone.now() + timedelta(days=5)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
    )


def _get(client: DjangoClient, url: str):
    return client.get(url, HTTP_AUTHORIZATION=_init_data_header(ME))


def _post(client: DjangoClient, url: str, body: dict | None = None):
    return client.post(
        url,
        data=json.dumps(body or {}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(ME),
    )


def _detail(proxy: RemoteBookingProxy) -> str:
    return f"/api/v1/customer/bookings/{proxy.appointment_id}"


@pytest.fixture
def stub_cancel(monkeypatch) -> _StubCancelClient:
    stub = _StubCancelClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
    )
    return stub


@pytest.fixture
def stub_pay(monkeypatch) -> _StubC7Client:
    stub = _StubC7Client()
    monkeypatch.setattr("apps.miniapp_api.views.AylaClientPaymentsClient", lambda: stub)
    return stub


class TestDetail:
    def test_booking_in_another_salon_is_found(self, client, me_home, me_lumina, lumina) -> None:
        proxy = _proxy(lumina, me_lumina)

        resp = _get(client, _detail(proxy))

        assert resp.status_code == 200, resp.content
        booking = resp.json()["booking"]
        assert booking["id"] == str(proxy.appointment_id)
        # Адрес — салона записи, а не того, под которым Mini App узнал человека.
        assert booking["address"] == "ул. Светлая, 7"

    def test_another_persons_booking_is_not_found(
        self, client, me_home, me_lumina, stranger_lumina, lumina
    ) -> None:
        mine = _proxy(lumina, me_lumina)
        theirs = _proxy(lumina, stranger_lumina)

        # Положительная пара впереди: своя в том же салоне находится.
        assert _get(client, _detail(mine)).status_code == 200
        resp = _get(client, _detail(theirs))
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_booking_under_the_configured_salon_works_as_before(
        self, client, me_home, home
    ) -> None:
        proxy = _proxy(home, me_home)

        resp = _get(client, _detail(proxy))

        assert resp.status_code == 200
        assert resp.json()["booking"]["address"] == "ул. Домашняя, 1"


class TestCancel:
    def test_booking_in_another_salon_is_cancelled(
        self, client, me_home, me_lumina, lumina, stub_cancel
    ) -> None:
        proxy = _proxy(lumina, me_lumina)

        resp = _post(client, f"{_detail(proxy)}/cancel")

        assert resp.status_code == 200, resp.content
        assert [c["appointment_id"] for c in stub_cancel.calls] == [str(proxy.appointment_id)]
        # Ayla видит человека так же: внешний id одинаков у всех его личностей.
        assert stub_cancel.calls[0]["external_user_id"] == f"bot:max:{ME}"

    def test_another_persons_booking_is_not_cancelled(
        self, client, me_home, me_lumina, stranger_lumina, lumina, stub_cancel
    ) -> None:
        mine = _proxy(lumina, me_lumina)
        theirs = _proxy(lumina, stranger_lumina)

        assert _post(client, f"{_detail(mine)}/cancel").status_code == 200
        resp = _post(client, f"{_detail(theirs)}/cancel")
        assert resp.status_code == 404
        # До шва чужая отмена не дошла: вызов один — своей записи.
        assert [c["appointment_id"] for c in stub_cancel.calls] == [str(mine.appointment_id)]


class TestPayment:
    URL = "/api/v1/customer/me/payments/"

    def test_booking_in_another_salon_is_payable(
        self, client, me_home, me_lumina, lumina, stub_pay
    ) -> None:
        proxy = _proxy(lumina, me_lumina)

        resp = _post(client, self.URL, {"appointment_id": str(proxy.appointment_id)})

        assert resp.status_code == 200, resp.content
        assert stub_pay.calls[0][1]["appointment_id"] == str(proxy.appointment_id)

    def test_another_persons_booking_is_not_payable(
        self, client, me_home, me_lumina, stranger_lumina, lumina, stub_pay
    ) -> None:
        mine = _proxy(lumina, me_lumina)
        theirs = _proxy(lumina, stranger_lumina)

        assert (
            _post(client, self.URL, {"appointment_id": str(mine.appointment_id)}).status_code == 200
        )
        resp = _post(client, self.URL, {"appointment_id": str(theirs.appointment_id)})
        assert resp.status_code == 404
        assert resp.json()["error"] == "appointment_not_found"
        assert len(stub_pay.calls) == 1


class TestRescheduleStillHasNoSeam:
    def test_reschedule_answers_409_for_own_and_other_salon_alike(
        self, client, me_home, me_lumina, home, lumina
    ) -> None:
        # DRF-2561: шва переноса на пути Ayla нет. После починки детали ответ
        # не должен стать «не найдено» для одной из записей и «409» для другой.
        answers = []
        for proxy in (_proxy(home, me_home), _proxy(lumina, me_lumina)):
            resp = _post(client, f"{_detail(proxy)}/reschedule", {})
            answers.append((resp.status_code, resp.json()["error"]))
        assert answers == [(409, "invalid_state"), (409, "invalid_state")]
