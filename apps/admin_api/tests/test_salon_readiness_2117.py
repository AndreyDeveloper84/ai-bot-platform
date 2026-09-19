"""Салонная готовность поимённо в боте (DRF-2117, §50 п.4).

Красное листа: «Проверить готовность» первого приветствия вела на «Сегодня»
со строкой «появится позже» (DRF-2114); у владельца не было способа узнать,
кто из мастеров мешает записи.

* r1 — клиент: ``GET /internal/salons/<slug>/readiness/`` под общим Bearer +
  ``X-External-User-ID`` актора; 404 / 403 / сеть — по имени;
* r2 — сервис: проблемы каталога по коду с текстом бота, наложение зеркала
  (``sale_block``: ``schedule_unconfirmed`` только при включённом гейте §83,
  ``catalog_unlinked`` — всегда, ``catalog_missing``), UNKNOWN = проблема;
* r3 — рендер: «Салон готов принимать записи.» / «Салон пока не готов:» +
  строки поимённо / отказ источника;
* r4 — салонный бот: тап владельца → живой список; ресепшну — меню;
* r5 — прокси ``GET /api/v1/admin/readiness/``: owner / admin — 200, ресепшн
  и мастер — 403 (DRF-2115), источник недоступен — 200 с ``unknown``.

Сеть — ``pytest-httpx``; зеркало — реальные строки ``CatalogMaster``.
"""

from __future__ import annotations

import itertools
import uuid
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_httpx import HTTPXMock

from apps.admin_api.services import salon_readiness as svc
from apps.admin_api.tests.conftest import init_data_header
from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import (
    CatalogHttpClient,
    CatalogReadinessRefused,
    CatalogTransportError,
)
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BASE = "https://catalog.example"
TOKEN = "general-bearer-2117"  # noqa: S105


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = TOKEN


def _url(tenant: Tenant) -> str:
    return f"{BASE}/api/v1/internal/salons/{tenant.slug}/readiness/"


def _catalog_master(name: str, *, problems: list[dict] | None = None, checks: dict | None = None):
    mid = str(uuid.uuid4())
    base = {
        "publication": "ok",
        "schedule": "ok",
        "services": "ok",
        "catalog_link": "ok",
        "slots": "ok",
    }
    return {
        "id": mid,
        "user_id": str(uuid.uuid4()),
        "name": name,
        "checks": {**base, **(checks or {})},
        "problems": problems or [],
    }


def _catalog_body(masters: list[dict], *, ready: bool | None = None) -> dict:
    problems = [
        {"master": {"id": m["id"], "name": m["name"]}, **p} for m in masters for p in m["problems"]
    ]
    return {
        "data": {
            "salon": {"slug": "admin-api-test", "name": "Студия Карина"},
            "ready": (not problems) if ready is None else ready,
            "checked_at": "2026-09-20T09:00:00+00:00",
            "horizon_days": 7,
            "masters": masters,
            "problems": problems,
            "limits": ["slots: есть/нет"],
        }
    }


_external_ids = itertools.count(-2117001, -1)


def _mirror_row(tenant: Tenant, name: str, **over) -> CatalogMaster:
    fields = dict(
        tenant=tenant,
        external_id=next(_external_ids),
        external_updated_at=timezone.now(),
        name=name,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        accepted_at=timezone.now(),
        ayla_user_id=uuid.uuid4(),
        catalog_specialist_id=uuid.uuid4(),
    )
    fields.update(over)
    return CatalogMaster.all_tenants.create(**fields)


# ─── r1: клиент ─────────────────────────────────────────────────────────────


class TestClient:
    def test_reads_with_bearer_and_actor(self, httpx_mock: HTTPXMock, tenant: Tenant) -> None:
        anna = _catalog_master(
            "Анна", problems=[{"code": "schedule_missing", "text": "Анна — нет графика"}]
        )
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        with CatalogHttpClient() as http:
            dto = http.fetch_salon_readiness(
                tenant_slug=tenant.slug, actor_external_id="bot:max:5001"
            )
        (req,) = httpx_mock.get_requests()
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"
        assert req.headers["X-External-User-ID"] == "bot:max:5001"
        assert dto.ready is False and dto.horizon_days == 7
        assert dto.masters[0].name == "Анна"
        assert dto.masters[0].problems == (
            {"code": "schedule_missing", "text": "Анна — нет графика"},
        )
        assert dto.masters[0].checks["schedule"] == "ok"  # как прислал каталог — не переписывается

    @pytest.mark.parametrize(
        ("status", "reason"),
        [(404, "salon_not_confirmed"), (403, "credential_refused"), (401, "credential_refused")],
    )
    def test_refusals_are_named(
        self, httpx_mock: HTTPXMock, tenant: Tenant, status, reason
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), status_code=status, json={})
        with CatalogHttpClient() as http, pytest.raises(CatalogReadinessRefused) as exc:
            http.fetch_salon_readiness(tenant_slug=tenant.slug, actor_external_id="bot:max:5001")
        assert exc.value.reason == reason and exc.value.status_code == status

    def test_5xx_and_bad_shape_are_transport(self, httpx_mock: HTTPXMock, tenant: Tenant) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), status_code=502, json={})
        with CatalogHttpClient() as http, pytest.raises(CatalogTransportError):
            http.fetch_salon_readiness(tenant_slug=tenant.slug, actor_external_id="bot:max:5001")
        httpx_mock.add_response(method="GET", url=_url(tenant), json={"data": {"masters": "нет"}})
        with CatalogHttpClient() as http, pytest.raises(CatalogTransportError):
            http.fetch_salon_readiness(tenant_slug=tenant.slug, actor_external_id="bot:max:5001")

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_missing_token_fails_closed_without_a_request(
        self, httpx_mock: HTTPXMock, tenant: Tenant, settings
    ) -> None:
        settings.AYLA_INTERNAL_API_TOKEN = ""
        with (
            CatalogHttpClient() as http,
            pytest.raises(CatalogTransportError, match="not configured"),
        ):
            http.fetch_salon_readiness(tenant_slug=tenant.slug, actor_external_id="bot:max:5001")
        assert httpx_mock.get_requests() == []


# ─── r2: сервис — каталог + зеркало ─────────────────────────────────────────


class TestService:
    def test_catalog_problems_get_the_bots_texts_by_code(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master(
            "Анна Иванова",
            problems=[{"code": "schedule_missing", "text": "каталожный текст"}],
            checks={"schedule": "problem", "slots": "skipped"},
        )
        ivan = _catalog_master(
            "Иван",
            problems=[{"code": "services_missing", "text": "…"}],
            checks={"services": "problem", "slots": "skipped"},
        )
        maria = _catalog_master(
            "Мария",
            problems=[{"code": "identity_not_linked", "text": "…"}],
            checks={"catalog_link": "problem"},
        )
        httpx_mock.add_response(
            method="GET", url=_url(tenant), json=_catalog_body([anna, ivan, maria])
        )
        for m in (anna, ivan, maria):
            _mirror_row(tenant, m["name"], catalog_specialist_id=uuid.UUID(m["id"]))

        r = svc.check_salon_readiness(tenant)
        (req,) = httpx_mock.get_requests()
        assert req.headers["X-External-User-ID"] == "bot:max:5001"  # владелец — актор чтения
        assert r.ready is False and r.unknown is False and r.source_problem is None
        assert [(p.master_name, p.code, p.origin) for p in r.problems] == [
            ("Анна", "schedule_missing", "catalog"),
            ("Иван", "services_missing", "catalog"),
            ("Мария", "identity_not_linked", "catalog"),
        ]
        assert [p.text for p in r.problems] == [
            "Анна — не настроен график",
            "Иван — не назначены услуги",
            "Мария — не привязана личность MAX",
        ]
        assert r.masters_total == 3

    def test_unknown_code_keeps_the_catalog_text(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master(
            "Анна", problems=[{"code": "brand_new_code", "text": "Анна — новое"}]
        )
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        r = svc.check_salon_readiness(tenant)
        assert [p.text for p in r.problems] == ["Анна — новое"]

    def test_unknown_check_is_a_problem_not_an_ok(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master(
            "Анна", problems=[{"code": "slots_unknown", "text": "…"}], checks={"slots": "unknown"}
        )
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        r = svc.check_salon_readiness(tenant)
        assert r.unknown is True and r.ready is False
        assert [p.code for p in r.problems] == ["slots_unknown"]

    def test_mirror_overlay_catalog_unlinked_and_missing(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master("Анна")
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        _mirror_row(tenant, "Мария Смирнова", catalog_specialist_id=None)  # не связана с каталогом
        _mirror_row(tenant, "Ольга", catalog_specialist_id=uuid.uuid4())  # каталог её не знает
        _mirror_row(
            tenant, "Пенди", invite_status=CatalogMaster.InviteStatus.PENDING, accepted_at=None
        )
        _mirror_row(tenant, "Архив", archived_at=timezone.now())

        r = svc.check_salon_readiness(tenant)
        assert [(p.master_name, p.code, p.origin) for p in r.problems] == [
            ("Мария", "catalog_unlinked", "mirror"),
            ("Ольга", "catalog_missing", "mirror"),
        ]
        assert [p.text for p in r.problems] == [
            "Мария — не связана с каталогом",
            "Ольга — не найдена в каталоге",
        ]
        assert (
            r.masters_total == 3
        )  # Анна (каталог) + Мария (без ключа) + Ольга (ключ, каталог не вернул)

    def test_owner_deactivated_row_is_not_a_problem(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        """Снятая владельцем строка (``is_active=False``) — решение, не препятствие."""
        anna = _catalog_master("Анна")
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        _mirror_row(tenant, "Снятая", catalog_specialist_id=None, is_active=False)
        r = svc.check_salon_readiness(tenant)
        assert r.ready is True and r.masters_total == 1

    def test_empty_salon_from_the_contract_is_not_ready(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        """Контракт §2b: ``masters: []``, ``ready: false``, одна проблема с ``master: null``."""
        body = _catalog_body([], ready=False)
        body["data"]["problems"] = [
            {"master": None, "code": "no_masters", "text": "В салоне нет ни одного мастера"}
        ]
        httpx_mock.add_response(method="GET", url=_url(tenant), json=body)
        r = svc.check_salon_readiness(tenant)
        assert r.ready is False and r.unknown is False
        assert [(p.master_id, p.code, p.origin) for p in r.problems] == [
            (None, "no_masters", "catalog")
        ]
        assert svc.render(r) == "Салон пока не готов:\nВ салоне нет ни одного мастера."
        assert r.masters_total == 0

    def test_catalog_not_ready_without_a_reason_is_unknown(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([], ready=False))
        r = svc.check_salon_readiness(tenant)
        assert r.ready is False and r.unknown is True

    def test_mirror_failure_is_unknown_not_a_crash(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser, monkeypatch
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([]))

        def boom(tenant):
            raise RuntimeError("db down")

        monkeypatch.setattr(svc, "_mirror_rows", boom)
        r = svc.check_salon_readiness(tenant)
        assert r.ready is False and r.source_problem == svc.SOURCE_UNAVAILABLE

    def test_schedule_unconfirmed_only_behind_the_gate(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser, settings
    ) -> None:
        """§83: без гейта «не настроен график» = нет рабочего дня в каталоге; с гейтом — своя строка."""
        anna = _catalog_master("Анна")
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(
            tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]), schedule_confirmed_at=None
        )

        settings.MASTER_SCHEDULE_CONFIRMATION_REQUIRED = False
        assert svc.check_salon_readiness(tenant).ready is True

        settings.MASTER_SCHEDULE_CONFIRMATION_REQUIRED = True
        r = svc.check_salon_readiness(tenant)
        assert [(p.code, p.text) for p in r.problems] == [
            ("schedule_unconfirmed", "Анна — график не подтверждён"),
        ]

    def test_ayla_unlinked_row_is_named(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master("Анна")
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]), ayla_user_id=None)
        r = svc.check_salon_readiness(tenant)
        assert [p.code for p in r.problems] == ["ayla_unlinked"]

    def test_clean_salon_is_ready(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master("Анна")
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        r = svc.check_salon_readiness(tenant)
        assert r.ready is True and r.problems == () and r.checked_at == "2026-09-20T09:00:00+00:00"
        assert svc.render(r) == "Салон готов принимать записи."

    @pytest.mark.parametrize(
        ("status", "code", "fragment"),
        [
            (404, svc.SOURCE_REFUSED, "не подтвердил доступ салона"),
            (403, svc.SOURCE_REFUSED, "не подтвердил доступ салона"),
            (503, svc.SOURCE_UNAVAILABLE, "каталог не ответил"),
        ],
    )
    def test_source_failure_is_unknown_never_ready(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser, status, code, fragment
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), status_code=status, json={})
        r = svc.check_salon_readiness(tenant)
        assert r.ready is False and r.unknown is True and r.source_problem == code
        assert fragment in svc.render(r)
        assert "готов принимать" not in svc.render(r)

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_no_owner_or_admin_means_not_configured(
        self, httpx_mock: HTTPXMock, tenant: Tenant
    ) -> None:
        r = svc.check_salon_readiness(tenant)
        assert r.source_problem == svc.SOURCE_NOT_CONFIGURED and r.ready is False
        assert httpx_mock.get_requests() == []

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_empty_token_is_not_configured_not_retry(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser, settings
    ) -> None:
        settings.AYLA_INTERNAL_API_TOKEN = ""
        r = svc.check_salon_readiness(tenant)
        assert r.source_problem == svc.SOURCE_NOT_CONFIGURED
        assert httpx_mock.get_requests() == []

    def test_admin_is_the_actor_when_there_is_no_owner(
        self, httpx_mock: HTTPXMock, tenant: Tenant, admin_bot_user: BotUser
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([]))
        svc.check_salon_readiness(tenant)
        (req,) = httpx_mock.get_requests()
        assert req.headers["X-External-User-ID"] == "bot:max:5002"


# ─── r3: рендер ────────────────────────────────────────────────────────────


class TestRender:
    def test_not_ready_lists_by_name(self) -> None:
        r = svc.Readiness(
            problems=(
                svc.Problem(
                    "1", "Анна", "schedule_missing", "Анна — не настроен график", "catalog"
                ),
                svc.Problem(
                    "2", "Иван", "services_missing", "Иван — не назначены услуги", "catalog"
                ),
                svc.Problem(
                    None, "Мария", "catalog_unlinked", "Мария — не связана с каталогом", "mirror"
                ),
            )
        )
        assert svc.render(r) == (
            "Салон пока не готов:\n"
            "Анна — не настроен график;\n"
            "Иван — не назначены услуги;\n"
            "Мария — не связана с каталогом."
        )

    def test_every_code_has_a_text_with_a_name(self) -> None:
        for code, text in svc.TEXTS.items():
            assert "{name}" in text, code
        assert set(svc.SOURCE_TEXTS) == {
            svc.SOURCE_UNAVAILABLE,
            svc.SOURCE_REFUSED,
            svc.SOURCE_NOT_CONFIGURED,
        }


# ─── r5: прокси для admin Mini App ─────────────────────────────────────────


def _admin_url() -> str:
    return reverse("admin_api:salon_readiness")


class TestAdminProxy:
    def test_owner_reads_the_sheet(
        self, client: Client, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        anna = _catalog_master(
            "Анна",
            problems=[{"code": "schedule_missing", "text": "…"}],
            checks={"schedule": "problem"},
        )
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        resp = client.get(_admin_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert (
            body["ready"] is False and body["unknown"] is False and body["source_problem"] is None
        )
        assert body["masters_total"] == 1
        assert body["problems"] == [
            {
                "master": {"id": anna["id"], "name": "Анна"},
                "code": "schedule_missing",
                "text": "Анна — не настроен график",
                "origin": "catalog",
            }
        ]
        assert body["limits"] == ["slots: есть/нет"]

    def test_source_failure_is_200_with_unknown(
        self, client: Client, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), status_code=503, json={})
        body = client.get(_admin_url(), HTTP_AUTHORIZATION=init_data_header("5001")).json()
        assert body["ready"] is False and body["unknown"] is True
        assert body["source_problem"] == svc.SOURCE_UNAVAILABLE
        assert body["problems"][0]["origin"] == "source"

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_admin_allowed_receptionist_and_master_refused(
        self,
        client: Client,
        httpx_mock: HTTPXMock,
        tenant: Tenant,
        admin_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        master_only_bot_user: BotUser,
    ) -> None:
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([]))
        assert (
            client.get(_admin_url(), HTTP_AUTHORIZATION=init_data_header("5002")).status_code == 200
        )
        # DRF-2115: ресепшну тройка закрыта — и готовность с ней (поправка главного окна).
        assert (
            client.get(_admin_url(), HTTP_AUTHORIZATION=init_data_header("5003")).status_code == 403
        )
        assert (
            client.get(_admin_url(), HTTP_AUTHORIZATION=init_data_header("5004")).status_code == 403
        )
        assert len(httpx_mock.get_requests()) == 1  # отказанные — без единого чтения каталога

    def test_post_is_not_allowed(self, client: Client, owner_bot_user: BotUser) -> None:
        assert (
            client.post(_admin_url(), HTTP_AUTHORIZATION=init_data_header("5001")).status_code
            == 405
        )


# ─── r4: салонный бот — тап «Проверить готовность» ─────────────────────────


class TestSalonBotButton:
    def test_owner_tap_gets_the_live_sheet_and_receptionist_gets_the_menu(
        self, tenant: Tenant, owner_bot_user: BotUser, receptionist_bot_user: BotUser
    ) -> None:
        from apps.channels.max import salon_handler, staff_actions
        from apps.identity.services.role_resolver import resolve_role
        from apps.tenancy.context import tenant_scope

        entry = None
        sheet = "Салон пока не готов:\nАнна — не настроен график."
        with (
            patch.object(salon_handler, "_reply") as reply,
            patch.object(staff_actions, "salon_readiness", return_value=sheet) as action,
            tenant_scope(tenant),
        ):
            event = type("E", (), {"text": "cb:staff:readiness"})()
            salon_handler._handle_button(
                event, resolve_role(owner_bot_user), owner_bot_user, tenant, entry
            )
            assert action.call_count == 1
            assert reply.call_args.args[1] == sheet

            reply.reset_mock()
            action.reset_mock()
            salon_handler._handle_button(
                event, resolve_role(receptionist_bot_user), receptionist_bot_user, tenant, entry
            )
            assert action.call_count == 0  # ресепшну — не готовность, а меню

    def test_staff_action_renders_the_service_result(
        self, httpx_mock: HTTPXMock, tenant: Tenant, owner_bot_user: BotUser
    ) -> None:
        from apps.channels.max import staff_actions

        anna = _catalog_master(
            "Анна",
            problems=[{"code": "schedule_missing", "text": "…"}],
            checks={"schedule": "problem"},
        )
        httpx_mock.add_response(method="GET", url=_url(tenant), json=_catalog_body([anna]))
        _mirror_row(tenant, "Анна", catalog_specialist_id=uuid.UUID(anna["id"]))
        assert (
            staff_actions.salon_readiness(tenant)
            == "Салон пока не готов:\nАнна — не настроен график."
        )

    def test_first_greeting_button_is_the_callback(self) -> None:
        from apps.channels.max import salon_greeting
        from apps.channels.max.staff_menu import CB_READINESS

        buttons = salon_greeting.first_buttons(None)  # без Mini App — одна кнопка, и она живая
        assert buttons == [{"label": "Проверить готовность", "callback": CB_READINESS}]
        assert CB_READINESS == "cb:staff:readiness"
