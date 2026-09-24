"""Сбой документа отличим от «плана нет» (DRF-2356, п. 1).

GET плана тянет весь документ wellness-context, а парсер был снисходителен
дважды: конверт не объект → «плана нет»; блок `plan_lite` битый → тоже
«плана нет». Снаружи это неотличимо: человеку показывают конструктор, как
будто плана у него нет, и он строит второй поверх первого.

«Плана нет» — ответ о человеке. «Не смогли собрать» — ответ о нас.

## Почему признаком, а не исключением

Снисходительность парсера нужна второму потребителю — запертой
проактивности (`apps/wellness_proactive`), которая читает тот же документ
и для которой «не разобралось» значит «пропустить тик». Если парсер начнёт
бросать, у неё молча поменяется поведение. Поэтому документ несёт признак
`unreadable`, а решение принимает вызывающий: ручка Mini App отвечает
отказом и пишет свою строку в журнал, проактивность — как прежде.

Новых слов экрану не нужно: состояние сбоя у него уже есть — строка «Не
получилось прочитать план…» и кнопка «Повторить», просто не подключённая к
этому пути (`refusalSlug !== "plan_lite_disabled"` → `kind: "error"`).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.wellness_context_client import WellnessContext, _context_from_wire
from apps.miniapp_api.tests.test_plan_lite_proxy_2101 import (  # noqa: F401 — _settings: autouse
    _auth,
    _FakeClient,
    _settings,
)
from apps.tenancy.models import Tenant


def _url() -> str:
    return reverse("miniapp_api:customer_plan_lite")


# Фикстуры свои, а не импортированные: импортированное имя, названное ещё и
# параметром теста, ruff читает как переопределение (F811). Прецедент —
# `test_offer_not_sellable_1989`: из соседнего модуля берут помощников,
# фикстуры заводят у себя.
@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="plan-lite-test", name="Plan Lite", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "plan-lite-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="21010", display_name="Анна"
    )


class TestTheParserTellsUnreadableFromEmpty:
    def test_a_document_that_is_not_an_object_is_unreadable(self):
        ctx = _context_from_wire({"data": "не объект"})

        assert ctx.unreadable is True
        assert ctx.plan_lite is None

    def test_a_broken_plan_block_is_unreadable(self):
        """Блок плана пришёл, но не объект — это не «плана нет»."""
        ctx = _context_from_wire({"data": {"plan_lite": "сломано"}})

        assert ctx.unreadable is True
        assert ctx.plan_lite is None

    def test_no_plan_is_not_unreadable(self):
        """`plan_lite: null` — честное «плана нет», и оно не ошибка."""
        ctx = _context_from_wire({"data": {"plan": None, "plan_lite": None}})

        assert ctx.unreadable is False
        assert ctx.plan_lite is None

    def test_a_whole_plan_is_read(self):
        ctx = _context_from_wire(
            {
                "data": {
                    "plan_lite": {
                        "plan_id": "p-1",
                        "goal_key": "tone_up",
                        "actions": [
                            {
                                "action_type": "log_water",
                                "cadence": "per_day",
                                "target_count": 7,
                                "done_count": 2,
                                "bucket": {"start": "2026-09-18", "end": "2026-09-19"},
                            }
                        ],
                    }
                }
            }
        )

        assert ctx.unreadable is False
        assert ctx.plan_lite is not None
        assert ctx.plan_lite.goal_key == "tone_up"


@pytest.mark.django_db
class TestTheHandleAnswersRefusalNotEmptiness:
    def test_an_unreadable_document_is_a_refusal(self, client: Client, bot_user: BotUser, caplog):
        fake = _FakeClient(ctx=WellnessContext(has_plan=False, plan_lite=None, unreadable=True))

        with patch("apps.miniapp_api.views_plan_lite.WellnessContextHttpClient", return_value=fake):
            resp = client.get(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))

        # Не 200 с «плана нет»: человек получает отказ, который экран уже
        # умеет показать строкой «Не получилось прочитать план…» с «Повторить».
        assert resp.status_code == 502, resp.content
        assert json.loads(resp.content)["error"] == "ayla_unavailable"
        # И своя строка в журнале — иначе «не собрался» неотличимо в логах.
        assert "unreadable" in caplog.text

    def test_a_document_without_a_plan_is_still_a_plain_answer(
        self, client: Client, bot_user: BotUser
    ):
        fake = _FakeClient(ctx=WellnessContext(has_plan=False, plan_lite=None))

        with patch("apps.miniapp_api.views_plan_lite.WellnessContextHttpClient", return_value=fake):
            resp = client.get(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))

        assert resp.status_code == 200, resp.content
        assert json.loads(resp.content) == {"plan_lite": None}
