"""DRF-2766 — «Без чисел»: добровольный выбор человека, а не признак анкеты.

Решение владельца 04.10: человек сам прячет калории, БЖУ и числовые цели на
всех экранах; записи дневника режим не трогает. Прежнее автоматическое
скрытие по флагу расстройства пищевого поведения снято. Выбор хранится в
боте (настройки питания, тот же писатель ``write_prefs``), каталог о показе
не знает.

Что заперто:

- по умолчанию числа видны (``false``);
- ``POST {"numbers_hidden": true|false}`` — сохраняется и читается обратно;
- выбор и есть тот признак, который читают экраны Mini App
  (``wellness/today``, ``diary/days``);
- не булево — 400; без initData — 401;
- оболочке, которой закрыт личный контекст (§2.4), выбор не сохранить — 409,
  а не 200 с прежним состоянием;
- выбор одного человека не виден другому.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2766"  # noqa: S105 — test fixture  # pragma: allowlist secret
ME = "2766100"
OTHER = "2766200"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    settings.MAX_BOT_TENANT_SLUG = "display-2766"
    return Tenant.objects.create(slug="display-2766", name="Display 2766")


def _person(tenant: Tenant, channel_user_id: str, *, linked: bool = True) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=f"chat-{channel_user_id}",
        display_name="Анна",
        customer_status=(
            BotUser.CustomerStatus.LINKED if linked else BotUser.CustomerStatus.SHADOW
        ),
    )


def _url() -> str:
    return reverse("miniapp_api:customer_nutrition_display")


def _get(client: Client, who: str):
    return client.get(_url(), HTTP_AUTHORIZATION=_auth(who))


def _post(client: Client, who: str, body) -> object:
    return client.post(
        _url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(who),
    )


class TestTheChoice:
    def test_numbers_are_shown_by_default(self, client: Client, tenant: Tenant):
        _person(tenant, ME)

        resp = _get(client, ME)

        assert resp.status_code == 200
        assert resp.json() == {"numbers_hidden": False}

    def test_hiding_is_saved_and_read_back(self, client: Client, tenant: Tenant):
        me = _person(tenant, ME)

        resp = _post(client, ME, {"numbers_hidden": True})

        assert resp.status_code == 200
        assert resp.json() == {"numbers_hidden": True}
        assert _get(client, ME).json() == {"numbers_hidden": True}
        me.refresh_from_db()
        assert me.context["nutrition_proactive"]["numbers_hidden"] is True

    def test_showing_again_is_saved(self, client: Client, tenant: Tenant):
        _person(tenant, ME)
        _post(client, ME, {"numbers_hidden": True})

        resp = _post(client, ME, {"numbers_hidden": False})

        assert resp.json() == {"numbers_hidden": False}
        assert _get(client, ME).json() == {"numbers_hidden": False}

    def test_one_persons_choice_is_not_anothers(self, client: Client, tenant: Tenant):
        _person(tenant, ME)
        _person(tenant, OTHER)

        _post(client, ME, {"numbers_hidden": True})

        assert _get(client, ME).json() == {"numbers_hidden": True}
        assert _get(client, OTHER).json() == {"numbers_hidden": False}


class TestTheBorders:
    @pytest.mark.parametrize("body", [{}, {"numbers_hidden": "yes"}, {"numbers_hidden": 1}])
    def test_not_a_boolean_is_400(self, client: Client, tenant: Tenant, body):
        _person(tenant, ME)

        resp = _post(client, ME, body)

        assert resp.status_code == 400
        assert _get(client, ME).json() == {"numbers_hidden": False}

    def test_without_init_data_is_401(self, client: Client, tenant: Tenant):
        _person(tenant, ME)

        resp = client.post(
            _url(), data=json.dumps({"numbers_hidden": True}), content_type="application/json"
        )

        assert resp.status_code == 401

    def test_a_shell_without_person_context_gets_409_not_a_false_200(
        self, client: Client, tenant: Tenant
    ):
        _person(tenant, ME, linked=False)

        resp = _post(client, ME, {"numbers_hidden": True})

        assert resp.status_code == 409
        assert resp.json()["error"] == "nutrition_display_unavailable"
        assert _get(client, ME).json() == {"numbers_hidden": False}
