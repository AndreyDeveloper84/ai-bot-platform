"""DRF-2101 — Plan Lite в клиенте wellness-context: чтение поля и два писателя.

Провод фейкуется ``httpx.MockTransport``. Пиннится: ``plan_lite`` из
документа читается как ФАКТЫ действий (тип, каденс, target, done, ведро) —
и ничего больше; отсутствие поля / null — ``None``; ``create_plan_lite`` —
POST ``internal/me/plan-lite/`` с тем же телом, 201 → документ; отказы по
имени: 404 ``PLAN_LITE_DISABLED`` → :class:`PlanLiteDisabledError`, 409 →
:class:`PlanLiteAlreadyActiveError`, 404 ``NOT_FOUND`` →
:class:`PlanLiteGoalNotFoundError`; ``close_plan_lite`` — DELETE, 200.
Тел ответов в логах нет — сторож DRF-2009 на аргументах логгера.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    PlanLiteAlreadyActiveError,
    PlanLiteDisabledError,
    PlanLiteGoalNotFoundError,
    WellnessContextClientError,
    WellnessContextHttpClient,
)

_BASE = "https://ayla.test"
_TOKEN = "TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_EXT = "bot:max:pl-1"
_GOAL = "0b6f3c2e-9d1a-4c55-8e2f-2101aaaa0001"

PLAN_LITE_WIRE = {
    "plan_id": "0b6f3c2e-9d1a-4c55-8e2f-2101bbbb0001",
    "goal_key": "tone_up",
    "actions": [
        {
            "action_type": "log_food",
            "cadence": "per_week",
            "target_count": 3,
            "done_count": 1,
            "bucket": {"start": "2026-09-14", "end": "2026-09-21"},
        },
        {
            "action_type": "book_service",
            "cadence": "per_week",
            "target_count": 1,
            "done_count": 0,
            "bucket": {"start": "2026-09-14", "end": "2026-09-21"},
        },
    ],
}


def _client(handler) -> WellnessContextHttpClient:
    return WellnessContextHttpClient(
        base_url=_BASE,
        token=_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _doc(plan_lite):
    return {
        "data": {"plan": None, "outcomes": [], "gated": {"gate_d": "x"}, "plan_lite": plan_lite}
    }


class TestRead:
    def test_plan_lite_parsed_from_the_document_facts_only(self) -> None:
        ctx = _client(
            lambda _r: httpx.Response(200, json=_doc(PLAN_LITE_WIRE))
        ).get_wellness_context(external_user_id=_EXT)

        assert ctx.plan_lite == PlanLite(
            plan_id="0b6f3c2e-9d1a-4c55-8e2f-2101bbbb0001",
            goal_key="tone_up",
            actions=(
                PlanLiteAction("log_food", "per_week", 3, 1, "2026-09-14", "2026-09-21"),
                PlanLiteAction("book_service", "per_week", 1, 0, "2026-09-14", "2026-09-21"),
            ),
        )
        # Presence-only остальное не сломано: gated читается как факт отказа.
        assert ctx.gated is True and ctx.has_plan is False

    def test_absent_or_null_plan_lite_reads_as_none(self) -> None:
        assert (
            _client(lambda _r: httpx.Response(200, json=_doc(None)))
            .get_wellness_context(external_user_id=_EXT)
            .plan_lite
            is None
        )
        legacy: dict[str, Any] = {"data": {"plan": None, "outcomes": [], "gated": None}}
        assert (
            _client(lambda _r: httpx.Response(200, json=legacy))
            .get_wellness_context(external_user_id=_EXT)
            .plan_lite
            is None
        )

    def test_a_result_looking_key_in_an_action_does_not_enter_the_dto(self) -> None:
        """В-5 на стороне бота: сколько бы каталог ни прислал, DTO несёт
        только поля обязательства и факт — «percent» умирает в парсере."""
        wire = json.loads(json.dumps(PLAN_LITE_WIRE))
        wire["actions"][0]["percent"] = 33
        wire["achieved"] = True
        ctx = _client(lambda _r: httpx.Response(200, json=_doc(wire))).get_wellness_context(
            external_user_id=_EXT
        )
        assert ctx.plan_lite is not None
        assert "percent" not in str(ctx.plan_lite) and "achieved" not in str(ctx.plan_lite)


class TestWriters:
    def test_create_posts_goal_and_actions_and_reads_201(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["ext"] = request.headers.get("x-external-user-id")
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"data": PLAN_LITE_WIRE})

        plan = _client(handler).create_plan_lite(
            external_user_id=_EXT,
            goal_id=_GOAL,
            actions=[{"action_type": "log_food", "cadence": "per_week", "target_count": 3}],
        )

        assert (seen["method"], seen["path"]) == ("POST", "/api/v1/internal/me/plan-lite/")
        assert seen["ext"] == _EXT and seen["auth"] == f"Bearer {_TOKEN}"
        assert seen["body"] == {
            "goal_id": _GOAL,
            "actions": [{"action_type": "log_food", "cadence": "per_week", "target_count": 3}],
        }
        assert plan.goal_key == "tone_up" and len(plan.actions) == 2

    @pytest.mark.parametrize(
        ("status", "code", "exc"),
        [
            (404, "PLAN_LITE_DISABLED", PlanLiteDisabledError),
            (409, "PLAN_LITE_ALREADY_ACTIVE", PlanLiteAlreadyActiveError),
            (404, "NOT_FOUND", PlanLiteGoalNotFoundError),
            (400, "VALIDATION_ERROR", WellnessContextClientError),
        ],
        ids=["disabled", "already-active", "goal-not-found", "bad-request"],
    )
    def test_create_maps_refusals_by_code(self, status, code, exc) -> None:
        handler = lambda _r: httpx.Response(  # noqa: E731
            status, json={"error": {"code": code, "message": "x"}}
        )
        with pytest.raises(exc):
            _client(handler).create_plan_lite(
                external_user_id=_EXT,
                goal_id=_GOAL,
                actions=[{"action_type": "log_food", "cadence": "per_week", "target_count": 3}],
            )

    def test_close_is_a_delete_and_reads_200(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"data": {"plan_id": "p", "status": "closed_by_user"}})

        _client(handler).close_plan_lite(external_user_id=_EXT)

        assert (seen["method"], seen["path"]) == ("DELETE", "/api/v1/internal/me/plan-lite/")

    def test_close_without_an_active_plan_is_goal_not_found_class(self) -> None:
        handler = lambda _r: httpx.Response(  # noqa: E731
            404, json={"error": {"code": "NOT_FOUND", "message": "нет активного"}}
        )
        with pytest.raises(PlanLiteGoalNotFoundError):
            _client(handler).close_plan_lite(external_user_id=_EXT)


class TestGoalIdOptional:
    def test_create_without_goal_id_posts_actions_only(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"data": PLAN_LITE_WIRE})

        _client(handler).create_plan_lite(
            external_user_id=_EXT,
            actions=[{"action_type": "log_food", "cadence": "per_week", "target_count": 3}],
        )

        assert seen["body"] == {
            "actions": [{"action_type": "log_food", "cadence": "per_week", "target_count": 3}]
        }
