"""DRF-2254 — «чьё место и кто ведёт услуги»: один источник, ``Tenant.kind`` каталога.

Бот решал «соло» подсчётом людей (``is_solo_provider``) и по нему открывал
экраны самообслуживания мастера — место, услуги, выбор услуг; каталог
разрешал или отказывал по своему признаку ``Tenant.kind``. Расходились:

* A — один человек, в каталоге ``salon`` (соло до G4, однолюдный салон):
  экраны открыты, каталог отвечает 409 «ведёт владелец салона»;
* B — два человека, в каталоге ``solo``: бот — командная поверхность.

Теперь бот читает ``kind`` каталога (``GET internal/tenants/<id>/kind/``) и
отдаёт в ``/me`` как ``workspace_kind``; ``is_solo_provider`` — только
раскладка. Решения главного окна: таймаут чтения — доли фаз connect 0.5 /
read 1.0 / write 0.2 / pool 0.2 мимо общего breaker (как проба DRF-2225), кэш
10 минут на тенант, отказ каталога → ``null`` → «как сейчас».

* c* — клиент: ответ каталога, 404 → нет вида, таймаут не кормит breaker;
* s* — сервис: кэш, отказ не кэшируется, чужое значение — не вид, без
  каталога — ``None`` без вызова;
* m* — ``/me``: A и B;
* r* — готовность онбординга: в A пункты ``services``/``location`` —
  ``unavailable`` / ``managed_outside_app`` без ``deep_link``; ``ready``
  по-прежнему блокируют (достижимость — решение владельца); соло и ``null`` —
  как было.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache
from django.test import Client

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services import workspace_kind as wk
from apps.identity.tests.test_me_view import _init_data_header, _make_master, _url
from apps.identity.tests.test_me_view import (  # noqa: F401 — фикстуры по имени
    _bot_token,
    bot_user,
    tenant,
)
from apps.integrations.ayla import booking_client as bc
from apps.tenancy.models import Tenant, TenantStaff

TENANT_ID = uuid.UUID("22540000-0000-4000-8000-000000000001")


def _client_with(handler) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="t", transport=httpx.MockTransport(handler)
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# ─── клиент ──────────────────────────────────────────────────────────────────


class TestC1Client:
    def test_reads_kind_from_the_catalog(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"id": str(TENANT_ID), "kind": "solo"}})

        assert _client_with(handler).get_tenant_kind(tenant_id=TENANT_ID) == "solo"
        (req,) = seen
        assert req.method == "GET"
        assert req.url.path == f"/api/v1/internal/tenants/{TENANT_ID}/kind/"
        assert req.headers["Authorization"] == "Bearer t"

    def test_unknown_tenant_is_no_kind(self) -> None:
        client = _client_with(
            lambda r: httpx.Response(404, json={"error": {"code": "TENANT_NOT_FOUND"}})
        )
        assert client.get_tenant_kind(tenant_id=TENANT_ID) is None

    def test_a_timeout_does_not_feed_the_shared_breaker(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        client = _client_with(handler)
        with patch.object(client._circuit, "record_failure") as record:
            with pytest.raises(bc.BookingUnavailableError):
                client.get_tenant_kind(
                    tenant_id=TENANT_ID, timeout=wk.WORKSPACE_KIND_TIMEOUT, feeds_circuit=False
                )
        record.assert_not_called()

    def test_by_default_a_timeout_does_feed_the_breaker(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        client = _client_with(handler)
        with patch.object(client._circuit, "record_failure") as record:
            with pytest.raises(bc.BookingUnavailableError):
                client.get_tenant_kind(tenant_id=TENANT_ID, timeout=wk.WORKSPACE_KIND_TIMEOUT)
        record.assert_called_once()

    def test_the_per_call_timeout_reaches_the_request(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions["timeout"])
            return httpx.Response(200, json={"data": {"id": str(TENANT_ID), "kind": "salon"}})

        kind = _client_with(handler).get_tenant_kind(
            tenant_id=TENANT_ID, timeout=wk.WORKSPACE_KIND_TIMEOUT
        )
        assert kind == "salon"
        # Доли фаз — решение главного окна: бюджет на весь вызов, а не 1.5 с на каждую фазу.
        assert seen[0] == {"connect": 0.5, "read": 1.0, "write": 0.2, "pool": 0.2}


# ─── сервис ──────────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, answer=None, exc: Exception | None = None) -> None:
        self.answer = answer
        self.exc = exc
        self.calls: list[dict] = []

    def get_tenant_kind(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.answer


@pytest.fixture
def configured(settings):
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "t"  # noqa: S105  # pragma: allowlist secret


class TestS1Service:
    def test_decided_numbers_reach_the_client(self, configured) -> None:
        fake = _FakeClient("solo")
        with patch.object(wk, "get_ayla_booking_client", return_value=fake):
            assert wk.workspace_kind(TENANT_ID) == "solo"
        assert fake.calls == [
            {"tenant_id": TENANT_ID, "timeout": wk.WORKSPACE_KIND_TIMEOUT, "feeds_circuit": False}
        ]
        assert wk.WORKSPACE_KIND_TIMEOUT == httpx.Timeout(
            connect=0.5, read=1.0, write=0.2, pool=0.2
        )
        assert wk.WORKSPACE_KIND_CACHE_TTL_S == 600

    def test_a_read_is_cached_per_tenant(self, configured) -> None:
        fake = _FakeClient("salon")
        with patch.object(wk, "get_ayla_booking_client", return_value=fake):
            assert wk.workspace_kind(TENANT_ID) == "salon"
            assert wk.workspace_kind(TENANT_ID) == "salon"
        assert len(fake.calls) == 1

    def test_a_failure_is_null_and_not_cached(self, configured) -> None:
        down = _FakeClient(exc=bc.BookingUnavailableError("network: ReadTimeout"))
        with patch.object(wk, "get_ayla_booking_client", return_value=down):
            assert wk.workspace_kind(TENANT_ID) is None
        up = _FakeClient("solo")
        with patch.object(wk, "get_ayla_booking_client", return_value=up):
            assert wk.workspace_kind(TENANT_ID) == "solo"
        assert len(up.calls) == 1

    def test_an_unknown_tenant_is_null_and_not_cached(self, configured) -> None:
        fake = _FakeClient(None)
        with patch.object(wk, "get_ayla_booking_client", return_value=fake):
            assert wk.workspace_kind(TENANT_ID) is None
            assert wk.workspace_kind(TENANT_ID) is None
        assert len(fake.calls) == 2

    def test_a_value_outside_the_closed_list_is_not_a_kind(self, configured) -> None:
        fake = _FakeClient("franchise")
        with patch.object(wk, "get_ayla_booking_client", return_value=fake):
            assert wk.workspace_kind(TENANT_ID) is None

    def test_a_code_error_is_null_and_does_not_break_me(self, configured) -> None:
        broken = _FakeClient(exc=TypeError("signature drift"))
        with patch.object(wk, "get_ayla_booking_client", return_value=broken):
            assert wk.workspace_kind(TENANT_ID) is None
        assert len(broken.calls) == 1

    def test_without_a_catalog_there_is_no_call(self, settings) -> None:
        settings.AYLA_BASE_URL = ""
        fake = _FakeClient("solo")
        with patch.object(wk, "get_ayla_booking_client", return_value=fake):
            assert wk.workspace_kind(TENANT_ID) is None
        assert fake.calls == []


# ─── /me ─────────────────────────────────────────────────────────────────────


def _me(client: Client, bu: BotUser) -> dict:
    resp = client.get(_url(), HTTP_AUTHORIZATION=_init_data_header(bu.channel_user_id))
    assert resp.status_code == 200, resp.content
    return resp.json()


class TestM1Me:
    def test_case_a_one_person_salon_kind(
        self,
        client: Client,
        tenant: Tenant,  # noqa: F811
        bot_user: BotUser,  # noqa: F811
    ) -> None:
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER
        )
        _make_master(tenant, bot_user)
        with patch.object(wk, "workspace_kind", return_value="salon") as read:
            data = _me(client, bot_user)
        assert data["is_solo_provider"] is True  # раскладка — прежняя
        assert data["workspace_kind"] == "salon"
        read.assert_called_once_with(tenant.id)

    def test_case_b_two_people_solo_kind(
        self,
        client: Client,
        tenant: Tenant,  # noqa: F811
        bot_user: BotUser,  # noqa: F811
    ) -> None:
        other = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="55503", display_name="Мастер 2"
        )
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=other, role=TenantStaff.Role.ADMIN)
        with patch.object(wk, "workspace_kind", return_value="solo"):
            data = _me(client, bot_user)
        assert data["is_solo_provider"] is False
        assert data["workspace_kind"] == "solo"

    def test_staff_with_an_unknown_kind_gets_null(
        self,
        client: Client,
        tenant: Tenant,  # noqa: F811
        bot_user: BotUser,  # noqa: F811
    ) -> None:
        _make_master(tenant, bot_user)
        with patch.object(wk, "workspace_kind", return_value=None) as read:
            data = _me(client, bot_user)
        read.assert_called_once_with(tenant.id)
        assert "workspace_kind" in data
        assert data["workspace_kind"] is None

    def test_a_customer_gets_null_without_a_catalog_call(
        self,
        client: Client,
        bot_user: BotUser,  # noqa: F811
    ) -> None:
        with patch.object(wk, "workspace_kind", return_value="solo") as read:
            data = _me(client, bot_user)
        assert data["is_customer"] is True
        assert data["workspace_kind"] is None
        read.assert_not_called()


# ─── готовность онбординга ───────────────────────────────────────────────────


def _readiness(master: CatalogMaster, kind: str | None):
    from apps.master_api.services import onboarding_readiness as orr

    with patch.object(orr, "workspace_kind", return_value=kind):
        return orr.build_readiness(master)


@pytest.fixture
def master(tenant: Tenant, bot_user: BotUser) -> CatalogMaster:  # noqa: F811
    m = _make_master(tenant, bot_user)
    m.photo_url = "/media/master_photos/r.jpg"
    m.save(update_fields=["photo_url"])
    return m


def _states(readiness) -> dict[str, tuple[str, str | None]]:
    return {i.key: (i.state, i.reason) for i in readiness.items}


class TestR1Readiness:
    def test_case_a_services_and_location_are_managed_outside_and_no_longer_block(
        self, master, settings
    ) -> None:
        settings.BOOKING_VIA_AYLA_REST = False
        from apps.scheduling.models import WorkingHours

        WorkingHours.all_tenants.create(
            tenant=master.tenant,
            master=master,
            day_of_week=2,
            is_working=True,
            start_time="10:00",
            end_time="19:00",
        )
        r = _readiness(master, "salon")
        states = _states(r)
        assert states["services"] == ("unavailable", "managed_outside_app")
        assert states["location"] == ("unavailable", "managed_outside_app")
        assert states["hours"][0] == "done" and states["profile"][0] == "done"
        # Вести некуда — ссылки нет; у остальных пунктов она прежняя.
        links = {item["key"]: item["deep_link"] for item in r.as_dict()["items"]}
        assert links["hours"] == "/solo/working-hours"
        assert links["services"] is None and links["location"] is None
        # ПЕРЕВЁРНУТО DRF-2350 (§77 п. 1, 23.09.2026). Здесь стояло
        # «смысл ready не меняется: достижимость „готово“ — решение
        # владельца», и это было верно ровно до того, как владелец решил:
        # недоступные пункты отправку больше НЕ держат. Узел не удалён —
        # он показывает, что прежнее поведение было записано и отменено
        # решением, а не размыто правкой.
        assert r.blocking == []
        assert r.managed_elsewhere == [
            "services:managed_outside_app",
            "location:managed_outside_app",
        ]
        assert r.ready_to_submit is True

    @pytest.mark.parametrize("kind", ["solo", None])
    def test_solo_and_unknown_keep_their_states(self, master, kind, settings) -> None:
        settings.BOOKING_VIA_AYLA_REST = False
        r = _readiness(master, kind)
        states = _states(r)
        assert states["services"][0] == "missing"
        assert states["location"] == ("unavailable", "capability_not_built")
        # DRF-2350: недоступное ушло из blocking в managed_elsewhere, а
        # ненастроенное держит отправку как держало.
        assert "services:missing" in r.blocking
        assert "location:unavailable" not in r.blocking
        assert r.managed_elsewhere == ["location:capability_not_built"]
