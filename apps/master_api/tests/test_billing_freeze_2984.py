"""DRF-2984 B2 — заморозка подписки закрывает Mini App мастера, кроме выхода.

В-4 (владелец 20.10): заморозка — статус подписки, не ``Tenant.is_active``;
«Биллинг», профиль и вход остаются, иначе замороженный не сможет заплатить.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import get_resolver

from apps.catalog.models import CatalogMaster
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.billing_gate import OPEN_WHEN_FROZEN
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def master(tenant, bot_user) -> CatalogMaster:
    return make_master(
        tenant,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        invite_token=None,
        expires_in_days=None,
        linked_bot_user=bot_user,
    )


def _status(tenant: Tenant, value: str) -> None:
    Tenant.all_objects.filter(pk=tenant.pk).update(subscription_status=value)


def _get(client: Client, path: str):
    return client.get(path, HTTP_AUTHORIZATION=init_data_header("12345"))


def test_z1_frozen_closes_an_ordinary_screen(settings, client, tenant, master):
    settings.BILLING_ENABLED = True
    _status(tenant, "frozen")

    resp = _get(client, "/api/v1/master/notification-prefs/")

    assert resp.status_code == 403
    assert resp.json()["error"] == "subscription_frozen"
    assert resp.json()["details"]["subscription_status"] == "frozen"


def test_z2_canceled_closes_it_too(settings, client, tenant, master):
    settings.BILLING_ENABLED = True
    _status(tenant, "canceled")

    assert (
        _get(client, "/api/v1/master/notification-prefs/").json()["error"] == "subscription_frozen"
    )


def test_z3_billing_stays_open_while_frozen(settings, client, tenant, master):
    """Выход из заморозки: до «Биллинга» доходит; дальше его собственный ответ."""

    settings.BILLING_ENABLED = True
    _status(tenant, "frozen")

    resp = _get(client, "/api/v1/master/billing/status")

    assert resp.status_code == 503  # зазор сопоставления id — ответ самой ручки, не заморозки
    assert resp.json()["error"] == "specialist_mapping_unavailable"


def test_z4_flag_off_keeps_a_frozen_salon_open(client, tenant, master):
    _status(tenant, "frozen")

    resp = _get(client, "/api/v1/master/notification-prefs/")

    assert resp.json().get("error") != "subscription_frozen"
    assert resp.status_code == 200


def test_z5_an_active_subscription_is_open(settings, client, tenant, master):
    settings.BILLING_ENABLED = True
    _status(tenant, "active")

    assert _get(client, "/api/v1/master/notification-prefs/").status_code == 200


@pytest.mark.parametrize(("namespace", "name"), sorted(OPEN_WHEN_FROZEN))
def test_z6_every_open_route_exists(namespace, name):
    """Опечатка в имени тихо закрыла бы выход — каждое имя обязано быть маршрутом."""

    resolver = get_resolver().namespace_dict[namespace][1]

    assert name in {p.name for p in resolver.url_patterns if getattr(p, "name", None)}
