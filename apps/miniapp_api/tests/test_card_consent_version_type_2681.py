"""DRF-2681 — словарь вместо версии согласия не уходит в привязку карты.

``consent_version`` двери ``/customer/me/cards/setup/`` уходит в каталог
(C7.2 ``cards_setup``), каталог принимает любую непустую строку до 64
(``payments/views.py`` ``_InternalCardSetupSerializer``) и через метаданные
провайдера сохраняет её в ``UserPaymentMethod.consent_version`` — запись о том,
на что согласился человек. Было ``str(body.get("consent_version"))``: словарь
уходил туда текстом ``"{'a': 1}"``.

Узел смотрит то, что ушло в каталог (аргументы ``cards_setup``), — запись в
``UserPaymentMethod`` живёт в каталоге и из бота не видна. Пара: строка уходит
дословно.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client as DjangoClient

from apps.miniapp_api.tests.test_c7_payments import (  # noqa: F401
    _bot_token,
    _init_data_header,
    bot_user,
    stub_client,
    tenant,
)

pytestmark = pytest.mark.django_db

URL = "/api/v1/customer/me/cards/setup/"
DICT = {"a": 1}


def _setup(client: DjangoClient, consent_version):
    return client.post(
        URL,
        data=json.dumps({"consent_version": consent_version}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header("12345"),
    )


def test_a_dict_version_is_refused_and_nothing_leaves_for_the_catalog(
    client: DjangoClient,
    bot_user,  # noqa: F811
    stub_client,  # noqa: F811
):
    resp = _setup(client, DICT)

    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
    assert stub_client.calls == []  # empty-assert-ok: пара ниже — прибор видит вызов


def test_a_string_version_leaves_for_the_catalog_verbatim(
    client: DjangoClient,
    bot_user,  # noqa: F811
    stub_client,  # noqa: F811
):
    resp = _setup(client, "offer-1.0")

    assert resp.status_code == 200, resp.content
    ((name, kwargs),) = stub_client.calls
    assert name == "cards_setup"
    assert kwargs["consent_version"] == "offer-1.0"
    assert kwargs["consent_version"] not in ("{'a': 1}", "None")
