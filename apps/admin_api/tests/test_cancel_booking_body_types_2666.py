"""DRF-2666 — словарь в причине отмены не доезжает до каталога."""

from __future__ import annotations

import pytest

from apps.admin_api.tests.test_cancel_booking import (  # noqa: F401
    _post,
    _proxy,
    _StubSalon,
    stub_salon,
)

pytestmark = pytest.mark.django_db

DICT = {"a": 1}
RENDERED = "{'a': 1}"


def test_a_dict_reason_is_refused_and_the_catalog_is_not_called(
    client,
    tenant,
    owner_bot_user,
    stub_salon,  # noqa: F811
):
    stub = stub_salon(_StubSalon())

    resp = _post(client, _proxy(tenant), reason=DICT)

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    assert stub.calls == []  # empty-assert-ok: пара ниже показывает, что прибор видит вызовы


def test_a_string_reason_reaches_the_catalog_verbatim(
    client,
    tenant,
    owner_bot_user,
    stub_salon,  # noqa: F811
):
    stub = stub_salon(_StubSalon())

    resp = _post(client, _proxy(tenant), reason="салон закрыт")

    assert resp.status_code in (200, 201), resp.content
    (call,) = stub.calls
    assert call["reason"] == "салон закрыт"
    assert RENDERED not in repr(call)
