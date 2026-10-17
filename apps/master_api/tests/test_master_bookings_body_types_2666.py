"""DRF-2666 — словарь из тела записи мастера не доезжает до каталога (зеркало admin)."""

from __future__ import annotations

import pytest

from apps.master_api.tests.test_master_bookings_2154 import (  # noqa: F401
    _post,
    _StubSalon,
    bridged_service,
    stub_salon,
)

pytestmark = pytest.mark.django_db

DICT = {"a": 1}
RENDERED = "{'a': 1}"


def _creates(stub) -> list[dict]:
    return [c["create_appointment"] for c in stub.calls if "create_appointment" in c]


@pytest.mark.parametrize("field", ["client_name", "client_phone", "idempotency_key"])
def test_a_dict_is_refused_and_the_catalog_is_not_called(
    client,
    accepted_master,
    bot_user,
    bridged_service,  # noqa: F811
    stub_salon,  # noqa: F811
    field,
):
    stub = stub_salon(_StubSalon())

    resp = _post(client, bridged_service, **{field: DICT})

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    assert _creates(stub) == []  # empty-assert-ok: пара ниже показывает, что прибор видит вызовы


def test_strings_reach_the_catalog_verbatim(
    client,
    accepted_master,
    bot_user,
    bridged_service,  # noqa: F811
    stub_salon,  # noqa: F811
):
    stub = stub_salon(_StubSalon())

    resp = _post(
        client,
        bridged_service,
        client_name="Мария",
        client_phone="+79990000000",
        idempotency_key="k-2666",
    )

    assert resp.status_code in (200, 201), resp.content
    (call,) = _creates(stub)
    assert (call["client_name"], call["client_phone"]) == ("Мария", "+79990000000")
    assert RENDERED not in repr(call)
