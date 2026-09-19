"""DRF-2123 (План-A) — предложение Plan Lite из шаблона в клиенте wellness-context.

Провод фейкуется ``httpx.MockTransport``. Пиннится:

* ``get_plan_lite_proposal`` — GET ``internal/me/plan-lite/proposal/`` под тем
  же Bearer + ``X-External-User-ID``; 200 → :class:`PlanLiteProposal`
  (``goal_key``, ``why``, ``template_version``, действия — тип, каденс,
  target); лишние ключи ответа в DTO не попадают конструкцией;
* отказы по имени: 404 ``details.reason=no_active_goal`` →
  :class:`PlanLiteGoalNotFoundError`, 404 ``no_template`` →
  :class:`PlanLiteNoTemplateError`, 404 ``PLAN_LITE_DISABLED`` →
  :class:`PlanLiteDisabledError`; 5xx → :class:`WellnessContextUnavailableError`;
* ``create_plan_lite(template_version=N)`` кладёт ``template_version`` в тело;
  без него ключа в теле нет (не ``null``);
* каденс ``per_2_weeks`` проходит через парсер документа как есть;
* тел ответов в логах нет — сторож на аргументах логгера (DRF-2009).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from apps.integrations.ayla.wellness_context_client import (
    PlanLiteAction,
    PlanLiteDisabledError,
    PlanLiteGoalNotFoundError,
    PlanLiteNoTemplateError,
    PlanLiteProposal,
    PlanLiteProposalAction,
    WellnessContextHttpClient,
    WellnessContextUnavailableError,
)

_BASE = "https://ayla.test"
_TOKEN = "TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_EXT = "bot:max:pl-2123"

PROPOSAL_WIRE = {
    "goal_key": "self_care",
    "why": "Забота о себе — это регулярность, а не подвиг.",
    "template_version": 2,
    "actions": [
        {"action_type": "book_service", "cadence": "per_2_weeks", "target_count": 1},
        {"action_type": "log_food", "cadence": "per_week", "target_count": 3},
        {"action_type": "log_water", "cadence": "per_day", "target_count": 6},
    ],
}


def _client(handler) -> WellnessContextHttpClient:
    return WellnessContextHttpClient(
        base_url=_BASE,
        token=_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _refusal(status: int, code: str, reason: str | None = None) -> httpx.Response:
    error: dict = {"code": code, "message": "x"}
    if reason is not None:
        error["details"] = {"reason": reason}
    return httpx.Response(status, json={"error": error})


class TestProposalRead:
    def test_get_is_a_get_on_the_proposal_path_under_the_subject(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["ext"] = request.headers.get("x-external-user-id")
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = request.content
            return httpx.Response(200, json={"data": PROPOSAL_WIRE})

        proposal = _client(handler).get_plan_lite_proposal(external_user_id=_EXT)

        assert (seen["method"], seen["path"]) == ("GET", "/api/v1/internal/me/plan-lite/proposal/")
        assert seen["ext"] == _EXT and seen["auth"] == f"Bearer {_TOKEN}"
        assert seen["body"] == b""
        assert proposal == PlanLiteProposal(
            goal_key="self_care",
            why="Забота о себе — это регулярность, а не подвиг.",
            template_version=2,
            actions=(
                PlanLiteProposalAction("book_service", "per_2_weeks", 1),
                PlanLiteProposalAction("log_food", "per_week", 3),
                PlanLiteProposalAction("log_water", "per_day", 6),
            ),
        )

    def test_extra_keys_do_not_enter_the_dto(self) -> None:
        wire = {
            **PROPOSAL_WIRE,
            "progress_percent": 42,
            "actions": [
                {
                    "action_type": "log_water",
                    "cadence": "per_day",
                    "target_count": 6,
                    "done_count": 5,
                    "achieved": True,
                }
            ],
        }
        proposal = _client(
            lambda _r: httpx.Response(200, json={"data": wire})
        ).get_plan_lite_proposal(external_user_id=_EXT)

        assert proposal.actions == (PlanLiteProposalAction("log_water", "per_day", 6),)
        # У DTO нет полей под результат — их некуда положить (В-5).
        assert "progress_percent" not in PlanLiteProposal.__dataclass_fields__
        assert "achieved" not in PlanLiteProposalAction.__dataclass_fields__
        assert "done_count" not in PlanLiteProposalAction.__dataclass_fields__

    def test_malformed_body_is_unavailable(self) -> None:
        with pytest.raises(WellnessContextUnavailableError):
            _client(lambda _r: httpx.Response(200, json={"data": None})).get_plan_lite_proposal(
                external_user_id=_EXT
            )
        with pytest.raises(WellnessContextUnavailableError):
            _client(lambda _r: httpx.Response(200, content=b"<html>")).get_plan_lite_proposal(
                external_user_id=_EXT
            )

    @pytest.mark.parametrize(
        ("status", "code", "reason", "exc"),
        [
            (404, "NOT_FOUND", "no_active_goal", PlanLiteGoalNotFoundError),
            (404, "NOT_FOUND", "no_template", PlanLiteNoTemplateError),
            (404, "PLAN_LITE_DISABLED", None, PlanLiteDisabledError),
        ],
        ids=["no-active-goal", "no-template", "disabled"],
    )
    def test_refusals_by_name(self, status, code, reason, exc) -> None:
        with pytest.raises(exc):
            _client(lambda _r: _refusal(status, code, reason)).get_plan_lite_proposal(
                external_user_id=_EXT
            )

    def test_no_template_is_not_a_goal_not_found(self) -> None:
        """Два разных 404 — два разных класса: экран на них расходится."""
        with pytest.raises(PlanLiteNoTemplateError) as info:
            _client(lambda _r: _refusal(404, "NOT_FOUND", "no_template")).get_plan_lite_proposal(
                external_user_id=_EXT
            )
        assert isinstance(info.value, PlanLiteNoTemplateError)  # класс — ровно этот
        assert not isinstance(info.value, PlanLiteGoalNotFoundError)

    def test_5xx_is_unavailable_and_the_body_is_not_logged(self, caplog) -> None:
        secret = "SECRET-BODY-2123"
        with caplog.at_level(
            logging.WARNING, logger="apps.integrations.ayla.wellness_context_client"
        ):
            with pytest.raises(WellnessContextUnavailableError):
                _client(
                    lambda _r: httpx.Response(503, json={"error": {"code": "X", "message": secret}})
                ).get_plan_lite_proposal(external_user_id=_EXT)
        # Всё, что попало в лог: сообщение и сырые аргументы (DRF-2009 —
        # сторож на аргументах, а не только на отрендеренной строке).
        logged = "\n".join(
            [r.getMessage() for r in caplog.records]
            + [str(a) for r in caplog.records for a in (r.args or ())]
        )
        assert "wellness_context.plan_lite.server_error" in logged  # предупреждение есть
        assert "status=503" in logged
        assert secret not in logged


class TestCreateWithTemplateVersion:
    def test_template_version_is_posted_in_the_body(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "data": {
                        "plan_id": "p-1",
                        "goal_key": "self_care",
                        "actions": [
                            {
                                "action_type": "book_service",
                                "cadence": "per_2_weeks",
                                "target_count": 1,
                                "done_count": 0,
                                "bucket": {"start": "2026-09-19", "end": "2026-10-03"},
                            }
                        ],
                    }
                },
            )

        plan = _client(handler).create_plan_lite(
            external_user_id=_EXT,
            actions=[{"action_type": "book_service", "cadence": "per_2_weeks", "target_count": 1}],
            template_version=2,
        )

        assert seen["body"] == {
            "actions": [
                {"action_type": "book_service", "cadence": "per_2_weeks", "target_count": 1}
            ],
            "template_version": 2,
        }
        # per_2_weeks проходит через парсер как есть: DTO не валидирует каталог кодов.
        assert plan.actions == (
            PlanLiteAction("book_service", "per_2_weeks", 1, 0, "2026-09-19", "2026-10-03"),
        )

    def test_without_template_version_the_key_is_absent_not_null(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                201, json={"data": {"plan_id": "p", "goal_key": "g", "actions": []}}
            )

        _client(handler).create_plan_lite(
            external_user_id=_EXT,
            actions=[{"action_type": "log_water", "cadence": "per_day", "target_count": 6}],
        )

        assert seen["body"]["actions"], "тело ушло и в нём есть actions"
        assert "template_version" not in seen["body"]
        assert set(seen["body"]) == {"actions"}
