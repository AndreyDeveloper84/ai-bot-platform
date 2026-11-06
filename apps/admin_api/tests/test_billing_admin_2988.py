"""DRF-2988 B3 — биллинг салона на админской поверхности.

Предмет: при заморозке владелец салона (часто не мастер) может заплатить из
админского Mini App; кто что может; что уходит в каталог и чьим субъектом;
как отказы каталога доходят до экрана. Каталог — стаб клиента.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from apps.admin_api import views_billing
from apps.admin_api.tests.conftest import init_data_header
from apps.integrations.ayla.billing_client import (
    BillingChargesDisabledError,
    BillingConflictError,
    BillingDisabledError,
    BillingNotFoundError,
)
from apps.tenancy.billing_gate import SALON_PRICE
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

OWNER, ADMIN, RECEPTION = "5001", "5002", "5003"
STATUS = {"tenant_id": "t", "subscription": {"status": "frozen", "tariff": "salon"}}


class _Catalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.exc: Exception | None = None

    def __enter__(self) -> "_Catalog":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _record(self, name: str, kwargs: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, kwargs))
        if self.exc is not None:
            raise self.exc
        return answer

    def get_tenant_status(self, **kw: Any) -> dict[str, Any]:
        return self._record("status", kw, STATUS)

    def tenant_card_setup(self, **kw: Any) -> dict[str, Any]:
        return self._record("card_setup", kw, {"confirmation_url": "https://pay.test/c"})

    def tenant_pay_debt(self, **kw: Any) -> dict[str, Any]:
        return self._record("pay_debt", kw, {"status": "succeeded"})

    def tenant_cancel(self, **kw: Any) -> dict[str, Any]:
        return self._record("cancel", kw, {"status": "canceled", "effective_at": "2026-11-01"})

    def tenant_convert_to_salon(self, **kw: Any) -> dict[str, Any]:
        return self._record("convert", kw, {"tariff": "salon", "effective_from": "2026-11-01"})


@pytest.fixture
def billing_on(settings) -> None:
    settings.BILLING_ENABLED = True


@pytest.fixture
def catalog(monkeypatch) -> _Catalog:
    stub = _Catalog()
    monkeypatch.setattr(views_billing, "_billing_client", lambda: stub)
    return stub


def _frozen(tenant: Tenant) -> None:
    Tenant.all_objects.filter(pk=tenant.pk).update(subscription_status="frozen")


def _get(client: Client, url: str, who: str = OWNER):
    return client.get(url, HTTP_AUTHORIZATION=init_data_header(who))


def _post(client: Client, url: str, body: dict[str, Any], who: str = OWNER):
    return client.post(
        url, data=body, content_type="application/json", HTTP_AUTHORIZATION=init_data_header(who)
    )


# ── выход из заморозки ─────────────────────────────────────────────────────


def test_z1_a_frozen_salon_owner_reaches_billing_while_the_rest_is_closed(
    billing_on, catalog, client: Client, tenant, owner_bot_user
):
    """Суть B3: остальное закрыто, а биллинг — открыт, иначе платить неоткуда."""

    _frozen(tenant)

    closed = _get(client, "/api/v1/admin/masters/")
    status = _get(client, "/api/v1/admin/billing/status/")
    pay = _post(client, "/api/v1/admin/billing/pay-debt/", {"return_url": "https://app.test/b"})

    assert closed.status_code == 403 and closed.json()["error"] == "subscription_frozen"
    assert status.status_code == 200, status.content
    assert status.json() == {"data": STATUS}
    assert pay.status_code == 200, pay.content
    assert [name for name, _ in catalog.calls] == ["status", "pay_debt"]
    assert catalog.calls[1][1] == {"tenant_id": str(tenant.pk), "return_url": "https://app.test/b"}


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/admin/billing/card-setup/",
        "/api/v1/admin/billing/cancel/",
        "/api/v1/admin/billing/convert-to-salon/",
    ],
)
def test_z2_every_billing_write_stays_open_when_frozen(
    billing_on, catalog, client: Client, tenant, owner_bot_user, url
):
    _frozen(tenant)

    resp = _post(client, url, {"return_url": "https://app.test/b", "confirmed_price": SALON_PRICE})

    assert resp.status_code == 200, resp.content


# ── кто что может ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("who", [OWNER, ADMIN])
def test_r1_owner_and_admin_can_unfreeze(
    billing_on, catalog, client: Client, owner_bot_user, admin_bot_user, who
):
    resp = _post(
        client, "/api/v1/admin/billing/card-setup/", {"return_url": "https://a.test/"}, who
    )

    assert resp.status_code == 200, resp.content
    assert resp.json()["data"]["confirmation_url"] == "https://pay.test/c"


def test_r2_reception_reads_and_pays_nothing(
    billing_on, catalog, client: Client, receptionist_bot_user
):
    status = _get(client, "/api/v1/admin/billing/status/", RECEPTION)
    pay = _post(client, "/api/v1/admin/billing/pay-debt/", {}, RECEPTION)

    assert (status.status_code, pay.status_code) == (403, 403)
    assert catalog.calls == []  # empty-assert-ok: r1 доказывает, что вызов у владельца/админа есть


@pytest.mark.parametrize(
    ("fixture", "who"), [("master_only_bot_user", "5004"), ("customer_bot_user", "5005")]
)
def test_r2b_a_master_or_customer_reaches_no_billing_door(
    billing_on, catalog, client: Client, request, fixture, who
):
    request.getfixturevalue(fixture)

    status = _get(client, "/api/v1/admin/billing/status/", who)
    pay = _post(client, "/api/v1/admin/billing/pay-debt/", {}, who)

    assert (status.status_code, pay.status_code) == (403, 403)
    assert catalog.calls == []  # empty-assert-ok: r1 доказывает, что вызов у владельца/админа есть


@pytest.mark.parametrize(
    "url", ["/api/v1/admin/billing/cancel/", "/api/v1/admin/billing/convert-to-salon/"]
)
def test_r3_cancel_and_plan_change_are_the_owners_alone(
    billing_on, catalog, client: Client, admin_bot_user, url
):
    resp = _post(client, url, {"confirmed_price": SALON_PRICE}, ADMIN)

    assert resp.status_code == 403
    assert resp.json()["error"] == "owner_only"
    assert catalog.calls == []  # empty-assert-ok: z2 доказывает вызов у владельца


# ── что уходит в каталог ───────────────────────────────────────────────────


def test_c1_second_factor_carries_the_caller_not_the_salon(
    billing_on, catalog, client: Client, tenant, owner_bot_user
):
    _post(client, "/api/v1/admin/billing/card-setup/", {"return_url": "https://a.test/"})
    _post(client, "/api/v1/admin/billing/convert-to-salon/", {"confirmed_price": SALON_PRICE})
    _post(client, "/api/v1/admin/billing/cancel/", {"idempotency_key": "k-1"})

    subject = f"bot:{owner_bot_user.channel}:{owner_bot_user.channel_user_id}"
    card, convert, cancel = (kw for _, kw in catalog.calls)
    assert card == {
        "tenant_id": str(tenant.pk),
        "external_user_id": subject,
        "return_url": "https://a.test/",
        "consent_version": "",
    }
    assert convert["external_user_id"] == subject
    assert convert["confirmed_price"] == "990.00"
    assert convert["idempotency_key"]
    assert cancel["idempotency_key"] == "k-1"


def test_c2_flag_off_never_reaches_the_catalog(catalog, client: Client, owner_bot_user):
    resp = _get(client, "/api/v1/admin/billing/status/")
    # Флаг — раньше разбора тела: кривое тело при выключенном биллинге — тоже 404.
    broken = client.post(
        "/api/v1/admin/billing/card-setup/",
        data="{not json",
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(OWNER),
    )

    assert (resp.status_code, broken.status_code) == (404, 404)
    assert resp.json()["error"] == broken.json()["error"] == "billing_disabled"
    assert catalog.calls == []  # empty-assert-ok: z1 доказывает вызов при включённом флаге


@pytest.mark.parametrize(
    "url", ["/api/v1/admin/billing/card-setup/", "/api/v1/admin/billing/pay-debt/"]
)
def test_c5_a_return_url_must_be_https(billing_on, catalog, client: Client, owner_bot_user, url):
    resp = _post(client, url, {"return_url": "http://evil.test/back"})

    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]
    assert catalog.calls == []  # empty-assert-ok: z1/r1 — с https вызов доходит


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"return_url": {"a": 1}}, "return_url"),
        ({"return_url": "https://a.test/", "consent_version": 5}, "consent_version"),
    ],
)
def test_c3_a_non_string_never_enters_the_money_path(
    billing_on, catalog, client: Client, owner_bot_user, body, field
):
    resp = _post(client, "/api/v1/admin/billing/card-setup/", body)

    assert resp.status_code == 400
    assert field in resp.json()["detail"]
    assert catalog.calls == []  # empty-assert-ok: r1 — тот же путь со строкой доходит


def test_c4_convert_without_a_confirmed_price_is_refused_here(
    billing_on, catalog, client: Client, owner_bot_user
):
    resp = _post(client, "/api/v1/admin/billing/convert-to-salon/", {})

    assert resp.status_code == 400
    assert catalog.calls == []  # empty-assert-ok: c1 — с ценой вызов есть


# ── отказы каталога ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "url", "http", "slug"),
    [
        (
            BillingNotFoundError("x"),
            "/api/v1/admin/billing/card-setup/",
            404,
            "billing_subject_not_admin",
        ),
        (BillingNotFoundError("x"), "/api/v1/admin/billing/status/", 404, "billing_not_found"),
        (
            BillingNotFoundError("x", code="SUBSCRIPTION_NOT_FOUND"),
            "/api/v1/admin/billing/cancel/",
            404,
            "billing_not_found",
        ),
        (
            BillingDisabledError("x"),
            "/api/v1/admin/billing/card-setup/",
            404,
            "billing_disabled",
        ),
        (
            BillingChargesDisabledError("x"),
            "/api/v1/admin/billing/pay-debt/",
            503,
            "billing_charges_disabled",
        ),
        (
            BillingConflictError("x", code="ALREADY_SALON"),
            "/api/v1/admin/billing/convert-to-salon/",
            409,
            "already_salon",
        ),
        (
            BillingConflictError("x", code="SOMETHING_NEW"),
            "/api/v1/admin/billing/pay-debt/",
            409,
            "billing_conflict",
        ),
    ],
)
def test_e1_catalog_refusals_keep_their_meaning(
    billing_on, catalog, client: Client, owner_bot_user, exc, url, http, slug
):
    catalog.exc = exc
    body = {"return_url": "https://a.test/", "confirmed_price": SALON_PRICE}

    resp = _get(client, url) if url.endswith("status/") else _post(client, url, body)

    assert resp.status_code == http
    assert resp.json()["error"] == slug


def test_e2_price_mismatch_shows_the_catalogs_price(
    billing_on, catalog, client: Client, owner_bot_user
):
    catalog.exc = BillingConflictError(
        "x", code="PRICE_MISMATCH", details={"current_price": "1090.00"}
    )

    resp = _post(client, "/api/v1/admin/billing/convert-to-salon/", {"confirmed_price": "990.00"})

    assert resp.status_code == 409
    assert resp.json()["error"] == "price_mismatch"
    assert resp.json()["details"] == {"current_price": "1090.00"}


# ── отказ гейта ведёт к переходу ───────────────────────────────────────────


def test_g1_plan_change_refusal_names_the_salon_price(
    billing_on, client: Client, tenant, owner_bot_user, master
):
    Tenant.all_objects.filter(pk=tenant.pk).update(subscription_status="active", plan="solo")

    resp = _post(
        client,
        "/api/v1/admin/masters/invite/",
        {
            "name": "Лера Новая",
            "contact_method": "max_username",
            "contact_value": "@lera_new_2988",
            "services": [],
            "schedule_preset": "default_mon_fri_10_19",
            "mode": "invite",
        },
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_change_required"
    assert resp.json()["details"] == {"salon_price": SALON_PRICE}
