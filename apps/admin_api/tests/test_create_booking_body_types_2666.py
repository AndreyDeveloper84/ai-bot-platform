"""DRF-2666 — словарь из тела записи не доезжает до каталога.

Доказано исполнением (``b2``, проба на eb353ff7): ``client_name={"a": 1}`` → 201,
и в вызов каталога ушло ``client_name="{'a': 1}"``. Узлы — по вызову каталога.
"""

from __future__ import annotations

import pytest

from apps.admin_api.tests.test_create_booking import (  # noqa: F401
    _post,
    _service,
    _StubSalon,
    stub_salon,
)

from .conftest import make_master

pytestmark = pytest.mark.django_db

DICT = {"a": 1}
RENDERED = "{'a': 1}"


@pytest.mark.parametrize("field", ["client_name", "client_phone", "idempotency_key"])
def test_a_dict_is_refused_and_the_catalog_is_not_called(
    client,
    tenant,
    owner_bot_user,
    stub_salon,  # noqa: F811
    field,
):
    stub = stub_salon(_StubSalon())
    master = make_master(tenant, name="Анна", external_id=1)

    resp = _post(client, tenant, master, _service(tenant), **{field: DICT})

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    assert stub.calls == []  # empty-assert-ok: пара ниже показывает, что прибор видит вызовы


def test_strings_reach_the_catalog_verbatim(
    client,
    tenant,
    owner_bot_user,
    stub_salon,  # noqa: F811
):
    stub = stub_salon(_StubSalon())
    master = make_master(tenant, name="Анна", external_id=1)

    resp = _post(
        client,
        tenant,
        master,
        _service(tenant),
        client_name="Мария",
        client_phone="+79990000000",
        idempotency_key="k-2666",
    )

    assert resp.status_code in (200, 201), resp.content
    (call,) = stub.calls
    assert (call["client_name"], call["client_phone"]) == ("Мария", "+79990000000")
    assert RENDERED not in repr(call)
