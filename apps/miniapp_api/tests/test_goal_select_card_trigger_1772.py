"""Прокси `customer_goal_select` — триггер карточки C04 (DRF-1772, К-3).

Единственное место, где бот узнаёт «контекст собран»: ответ КАТАЛОГА с
`next.id == return_to_chat` (тело запроса клиента не читается — клиент
может прислать что угодно). Здесь заперто: (1) собранный документ →
`maybe_send_card` вызван с этим документом; (2) документ с вопросами →
не вызван; (3) отказ диспетчера не портит ответ экрану.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.goals_client import reset_goals_circuit
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-goals-1772"  # noqa: S105 — test fixture  # pragma: allowlist secret

COLLECTED = {
    "version": 2,
    "known": {
        "goal": {
            "id": "goal-1",
            "goal_key": "relax",
            "label": "Расслабиться",
            "direction": None,
            "answers": [],
        }
    },
    "missing": [],
    "suggestions": [],
    "intents": [],
    "next": {"id": "return_to_chat", "label": "Вернуться в чат"},
}
ASKING = {
    **COLLECTED,
    "missing": [{"kind": "goal_anketa", "prompt": "?", "step": "area", "options": []}],
    "next": None,
}


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ольга"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret
    reset_goals_circuit()
    yield
    reset_goals_circuit()


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="goals-1772", name="Goals 1772", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "goals-1772"
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="97772", display_name="Ольга"
    )


def _post(client: Client, bot_user: BotUser, ayla_doc: dict):
    with (
        patch("apps.integrations.ayla.goals_client.post_goal_select", return_value=ayla_doc),
        patch("apps.recommendation.dispatch.maybe_send_card") as send,
    ):
        resp = client.post(
            reverse("miniapp_api:customer_goal_select"),
            data=json.dumps(
                {
                    "answer": {"step": "feeling", "option_keys": ["rested"]},
                    "source_channel": "miniapp",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
    return resp, send


@pytest.mark.django_db
class TestTrigger:
    def test_collected_document_triggers_the_card_with_the_catalog_answer(self, client, bot_user):
        resp, send = _post(client, bot_user, COLLECTED)
        assert resp.status_code == 200
        assert resp.json() == {"data": COLLECTED}
        send.assert_called_once()
        called_user, called_doc = send.call_args.args
        assert called_user.id == bot_user.id
        assert called_doc == COLLECTED

    def test_document_with_questions_still_calls_and_the_dispatcher_says_no(self, client, bot_user):
        # Решение «собран или нет» — у диспетчера, по документу каталога;
        # прокси не выводит его сам второй раз.
        resp, send = _post(client, bot_user, ASKING)
        assert resp.status_code == 200
        send.assert_called_once()
        assert send.call_args.args[1] == ASKING

    def test_dispatcher_failure_never_breaks_the_screen(self, client, bot_user):
        """Отказ внутри диспетчера (MAX, база) — в журнал; экрану — его документ."""
        with (
            patch("apps.integrations.ayla.goals_client.post_goal_select", return_value=COLLECTED),
            patch("apps.recommendation.dispatch._is_global", return_value=True),
            patch("apps.recommendation.dispatch._send_once", side_effect=RuntimeError("boom")),
        ):
            resp = client.post(
                reverse("miniapp_api:customer_goal_select"),
                data=json.dumps({"goal_key": "relax", "source_channel": "miniapp"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 200
        assert resp.json() == {"data": COLLECTED}
