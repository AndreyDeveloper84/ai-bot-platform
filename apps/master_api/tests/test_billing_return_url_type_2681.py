"""DRF-2681 — словарь вместо адреса возврата не уходит в платёж мастера.

``billing/internal_api.py`` каталога читает ``return_url`` сырым и проверяет
только непустоту, поэтому текст ``"{'a': 1}"`` дошёл бы до провайдера как адрес
возврата. Было ``str(body.get("return_url"))`` у обеих дверей.

Узел смотрит, что получил сервис платежа (он и ходит в каталог), — через ту же
подмену, что у существующих узлов дверей. Пара: строка доезжает дословно.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client as DjangoClient

from apps.master_api.services import billing as billing_svc
from apps.master_api.services.billing import ProxyStatus
from apps.master_api.tests.conftest import init_data_header
from apps.master_api.tests.test_billing import _SID, master  # noqa: F401

pytestmark = pytest.mark.django_db

DICT = {"a": 1}


@pytest.fixture
def linked_master(master):  # noqa: F811
    master.ayla_user_id = uuid.UUID(_SID)
    master.save(update_fields=["ayla_user_id"])
    return master


@pytest.fixture
def seen(monkeypatch) -> list[str]:
    urls: list[str] = []

    def _ok(_master, *, return_url, **_kw):
        urls.append(return_url)
        return billing_svc.BillingProxyResult(status=ProxyStatus.OK, payload={})

    monkeypatch.setattr("apps.master_api.views.card_setup_for_master", _ok)
    monkeypatch.setattr("apps.master_api.views.pay_debt_for_master", _ok)
    return urls


def _post(client: DjangoClient, path: str, body: dict):
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


DOORS = [
    ("/api/v1/master/billing/card-setup", {"tariff": "solo"}, "validation_error"),
    ("/api/v1/master/billing/pay-debt", {}, "malformed"),
]


@pytest.mark.parametrize(("path", "extra", "slug"), DOORS, ids=["card-setup", "pay-debt"])
def test_a_dict_return_url_is_refused_and_nothing_reaches_the_payment(
    client: DjangoClient, linked_master, seen, path, extra, slug
):
    resp = _post(client, path, {**extra, "return_url": DICT})

    assert resp.status_code == 400
    assert resp.json()["error"] == slug
    assert seen == []  # empty-assert-ok: пара ниже — прибор видит вызов


@pytest.mark.parametrize(("path", "extra", "slug"), DOORS, ids=["card-setup", "pay-debt"])
def test_a_string_return_url_reaches_the_payment_verbatim(
    client: DjangoClient, linked_master, seen, path, extra, slug
):
    resp = _post(client, path, {**extra, "return_url": "https://x.test/back"})

    assert resp.status_code == 200, resp.content
    assert seen == ["https://x.test/back"]
