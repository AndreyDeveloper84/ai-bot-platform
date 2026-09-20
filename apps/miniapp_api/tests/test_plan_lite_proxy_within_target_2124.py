"""DRF-2124 — ``within_target_count`` проходит прокси ``/customer/plan-lite``.

Ключи ответа экрана перечислены в ``plan_lite_payload`` руками (В-5: экран
получает ровно то, что несёт DTO), поэтому новый факт надо провести явно —
и провести как в каталоге: у ``log_food`` ключ есть всегда (целое или
``null``), у воды и брони его нет.

* p1 — целое едет целым;
* p2 — ``None`` едет ``null``, не 0 и не пропадает (§103 — «ориентира нет»
  и «ни одного дня в ориентире» — разные факты);
* p3 — у ``log_water`` / ``book_service`` ключа нет.
"""

from __future__ import annotations

from django.test import Client

from apps.identity.models import BotUser
from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    WellnessContext,
)
from apps.miniapp_api.tests.test_plan_lite_proxy_2101 import (  # noqa: F401 — фикстуры
    EXT,
    _auth,
    _FakeClient,
    _patch,
    _settings,
    _url,
    bot_user,
    tenant,
)

_BUCKET = ("2026-09-14", "2026-09-21")


def _get(client: Client, bot_user: BotUser, plan: PlanLite) -> list[dict]:
    fake = _FakeClient(ctx=WellnessContext(has_plan=False, gated=True, plan_lite=plan))
    with _patch(fake):
        resp = client.get(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
    assert resp.status_code == 200, resp.content
    assert fake.calls == [("get", EXT)]
    return resp.json()["plan_lite"]["actions"]


def _plan(*actions: PlanLiteAction) -> PlanLite:
    return PlanLite(plan_id="p-2124", goal_key="tone_up", actions=actions)


class TestWithinTargetThroughTheProxy:
    def test_p1_integer_travels(self, client: Client, bot_user: BotUser) -> None:
        actions = _get(
            client,
            bot_user,
            _plan(PlanLiteAction("log_food", "per_week", 5, 4, *_BUCKET, within_target_count=3)),
        )
        assert actions[0]["within_target_count"] == 3

    def test_p2_none_travels_as_null_not_zero(self, client: Client, bot_user: BotUser) -> None:
        actions = _get(
            client,
            bot_user,
            _plan(PlanLiteAction("log_food", "per_week", 5, 4, *_BUCKET)),
        )
        assert "within_target_count" in actions[0]
        assert actions[0]["within_target_count"] is None

    def test_p3_other_actions_have_no_key(self, client: Client, bot_user: BotUser) -> None:
        actions = _get(
            client,
            bot_user,
            _plan(
                PlanLiteAction("log_water", "per_day", 7, 4, "2026-09-18", "2026-09-19"),
                PlanLiteAction("book_service", "per_week", 1, 1, *_BUCKET),
            ),
        )
        assert all("within_target_count" not in a for a in actions)
