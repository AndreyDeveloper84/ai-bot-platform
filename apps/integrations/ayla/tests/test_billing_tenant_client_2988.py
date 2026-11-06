"""DRF-2988 B3 — клиент подписки тенанта (C3).

Провод — ``httpx.MockTransport`` с ответами в форме обёртки каталога
(``{"data"}`` / ``{"error": {"code", "message", "details"}}``, C3 #733).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.integrations.ayla.billing_client import (
    AylaBillingClient,
    BillingChargesDisabledError,
    BillingConflictError,
    BillingDisabledError,
    BillingNotFoundError,
    BillingTransportError,
)

TENANT = "8f2a4c2e-0000-4000-8000-000000002988"
SUBJECT = "bot:max:2988001"


def _client(handler) -> tuple[AylaBillingClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client = AylaBillingClient(
        base_url="https://ayla.test",
        token="tok",
        retries=3,
        http_client=httpx.Client(transport=httpx.MockTransport(record)),
    )
    return client, seen


def _error(status: int, code: str, details: dict[str, Any] | None = None) -> httpx.Response:
    body: dict[str, Any] = {"error": {"code": code, "message": "x"}}
    if details is not None:
        body["error"]["details"] = details
    return httpx.Response(status, json=body)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch) -> None:
    monkeypatch.setattr("apps.integrations.ayla.billing_client.time.sleep", lambda _s: None)


def test_k1_second_factor_endpoints_carry_the_subject_others_do_not():
    client, seen = _client(lambda _r: httpx.Response(200, json={"data": {"ok": True}}))

    client.get_tenant_status(tenant_id=TENANT)
    client.tenant_pay_debt(tenant_id=TENANT, return_url="https://a.test/")
    client.tenant_card_setup(tenant_id=TENANT, external_user_id=SUBJECT, return_url="https://a/")
    client.tenant_cancel(tenant_id=TENANT, external_user_id=SUBJECT, idempotency_key="k")
    client.tenant_convert_to_salon(
        tenant_id=TENANT, external_user_id=SUBJECT, idempotency_key="k", confirmed_price="990.00"
    )

    paths = [r.url.path for r in seen]
    assert paths == [
        f"/api/v1/internal/billing/tenants/{TENANT}/status/",
        f"/api/v1/internal/billing/tenants/{TENANT}/pay-debt/",
        f"/api/v1/internal/billing/tenants/{TENANT}/card-setup/",
        f"/api/v1/internal/billing/tenants/{TENANT}/cancel/",
        f"/api/v1/internal/billing/tenants/{TENANT}/convert-to-salon/",
    ]
    subjects = [r.headers.get("X-External-User-ID") for r in seen]
    assert subjects == [None, None, SUBJECT, SUBJECT, SUBJECT]
    assert all(r.headers["Authorization"] == "Bearer tok" for r in seen)


def test_k2_convert_sends_the_price_as_a_string():
    """Каталог отвергает число (C3): ``confirmed_price`` уходит строкой."""

    client, seen = _client(lambda _r: httpx.Response(200, json={"data": {}}))

    client.tenant_convert_to_salon(
        tenant_id=TENANT, external_user_id=SUBJECT, idempotency_key="k", confirmed_price="990.00"
    )

    assert seen[0].read() == b'{"idempotency_key":"k","confirmed_price":"990.00"}'


def test_k3_billing_disabled_is_named_and_still_a_not_found():
    client, _ = _client(lambda _r: _error(404, "BILLING_DISABLED"))

    with pytest.raises(BillingDisabledError) as caught:
        client.get_tenant_status(tenant_id=TENANT)

    assert isinstance(caught.value, BillingNotFoundError)


def test_k4_charges_disabled_is_not_retried_but_a_plain_503_is():
    client, seen = _client(lambda _r: _error(503, "BILLING_CHARGES_DISABLED"))
    with pytest.raises(BillingChargesDisabledError):
        client.tenant_card_setup(
            tenant_id=TENANT, external_user_id=SUBJECT, return_url="https://a/"
        )
    assert len(seen) == 1

    client, seen = _client(lambda _r: _error(503, "SERVICE_UNAVAILABLE"))
    with pytest.raises(BillingTransportError) as caught:
        client.tenant_card_setup(
            tenant_id=TENANT, external_user_id=SUBJECT, return_url="https://a/"
        )
    # Точный тип: не подкласс «списания выключены», а обычный сбой — его повторяют.
    assert type(caught.value) is BillingTransportError
    assert len(seen) == 3


def _bad_gateway(_r: httpx.Request) -> httpx.Response:
    return _error(502, "BAD_GATEWAY")


def _lost_answer(r: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("lost answer", request=r)


@pytest.mark.parametrize("fail", [_bad_gateway, _lost_answer], ids=["502", "read-timeout"])
def test_k6_a_debt_charge_is_never_resent_once_it_may_have_left(fail):
    """Ответ потерян — платёж мог создаться: повтор списал бы второй раз."""

    client, seen = _client(fail)

    with pytest.raises(BillingTransportError):
        client.tenant_pay_debt(tenant_id=TENANT)
    assert len(seen) == 1

    client, seen = _client(fail)
    with pytest.raises(BillingTransportError):
        client.pay_debt(specialist_id="spec-1")
    assert len(seen) == 1


def test_k7_a_debt_charge_that_never_left_is_retried():
    def refuse(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=r)

    client, seen = _client(refuse)

    with pytest.raises(BillingTransportError):
        client.tenant_pay_debt(tenant_id=TENANT)
    assert len(seen) == 3


def test_k5_a_conflict_keeps_code_and_details():
    client, _ = _client(lambda _r: _error(409, "PRICE_MISMATCH", {"current_price": "1090.00"}))

    with pytest.raises(BillingConflictError) as caught:
        client.tenant_convert_to_salon(
            tenant_id=TENANT, external_user_id=SUBJECT, idempotency_key="k", confirmed_price="9"
        )

    assert (caught.value.code, caught.value.details) == (
        "PRICE_MISMATCH",
        {"current_price": "1090.00"},
    )
