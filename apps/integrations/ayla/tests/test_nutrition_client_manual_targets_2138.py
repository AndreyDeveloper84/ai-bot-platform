"""DRF-2138 — ``set_manual_targets``: ручной ориентир по калориям (режим 3 §82).

Каталог: ``POST /nutrition/internal/profile/targets/manual/`` —
единственный писатель источника ``user_entered``. Клиент шлёт ТОЛЬКО
``calories_kcal`` (и ``confirm_deviation`` — когда человек подтвердил
отклонение); воду и белок не шлёт: белка в контракте каталога нет
(DRF-2186), вода — не этот лист.

* c1 — тело ровно ``{"calories_kcal": N}``; заголовок субъекта; 200 →
  профиль ``user_entered`` и отчёт ``manual_targets`` (``warnings``);
* c2 — ``confirm_deviation=True`` → в теле ``confirm_deviation: true``;
  без него ключа НЕТ (не ``false``);
* c3 — 422 ``CALORIES_BELOW_FLOOR`` → :class:`ManualTargetsRefusedError`
  с ``floor_kcal`` из ответа — порог каталога, не бота;
* c4 — 409 ``CONFIRMATION_REQUIRED`` → :class:`ManualTargetsConfirmationRequiredError`
  с ``kind`` и ``maintenance_kcal``;
* c5 — 5xx / сеть → :class:`NutritionUnavailableError`; прочие 4xx —
  :class:`NutritionAPIError`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc
from apps.integrations.ayla.tests.test_nutrition_client import (  # noqa: F401 — фикстура транспорта
    _client_with_handler,
    _patch_async_client,
    _profile_body,
    _set_transport,
)


def _ok_body(kcal: int, warnings: list[str] | None = None) -> dict[str, Any]:
    body = _profile_body(
        norms={"daily_kcal": kcal, "daily_water_ml": 2200},
        targets_provenance={
            "source": "user_entered",
            "method_versions": {},
            "input_snapshot": {},
            "confirmed_at": "2026-09-20T10:00:00.000Z",
        },
    )
    body["manual_targets"] = {
        "set": ["daily_kcal"],
        "warnings": list(warnings or []),
        "deviation": {"deviation_check": "unavailable"},
    }
    return body


def _error(status: int, code: str, details: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status,
        json={"success": False, "error": {"code": code, "message": "x", "details": details}},
    )


class TestC1PostsCaloriesOnly:
    @pytest.mark.asyncio
    async def test_body_subject_and_report(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": _ok_body(1800)})

        client, transport = _client_with_handler(handler)
        _set_transport(transport)
        profile, report = await client.set_manual_targets(
            external_user_id="bot:max:1", calories_kcal=1800
        )
        req = seen[0]
        assert req.method == "POST"
        assert req.url.path.endswith("/nutrition/internal/profile/targets/manual/")
        assert req.headers["X-External-User-ID"] == "bot:max:1"
        assert json.loads(req.content) == {"calories_kcal": 1800}
        assert profile.targets_source == "user_entered"
        assert profile.daily_kcal == 1800
        assert report["warnings"] == []

    @pytest.mark.asyncio
    async def test_calories_low_warning_travels(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": _ok_body(1100, ["calories_low"])})

        client, transport = _client_with_handler(handler)
        _set_transport(transport)
        _, report = await client.set_manual_targets(
            external_user_id="bot:max:1", calories_kcal=1100
        )
        assert report["warnings"] == ["calories_low"]


class TestC2ConfirmDeviationFlag:
    @pytest.mark.asyncio
    async def test_flag_present_only_when_confirmed(self) -> None:
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"data": _ok_body(1200)})

        client, transport = _client_with_handler(handler)
        _set_transport(transport)
        await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=1200)
        await client.set_manual_targets(
            external_user_id="bot:max:1", calories_kcal=1200, confirm_deviation=True
        )
        assert bodies == [
            {"calories_kcal": 1200},
            {"calories_kcal": 1200, "confirm_deviation": True},
        ]


class TestC3BelowFloorIsARefusal:
    @pytest.mark.asyncio
    async def test_422_carries_the_catalogue_floor(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return _error(422, "CALORIES_BELOW_FLOOR", {"calories_kcal": 900, "floor_kcal": 1000})

        client, transport = _client_with_handler(handler)
        _set_transport(transport)
        with pytest.raises(nc.ManualTargetsRefusedError) as ei:
            await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=900)
        assert ei.value.code == "CALORIES_BELOW_FLOOR"
        assert ei.value.details["floor_kcal"] == 1000


class TestC4ConfirmationRequired:
    @pytest.mark.asyncio
    async def test_409_carries_kind_and_maintenance(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return _error(
                409,
                "CONFIRMATION_REQUIRED",
                {
                    "kind": "calories_deviation",
                    "calories_kcal": 1200,
                    "maintenance_kcal": 2100,
                    "deviation_ratio": 0.429,
                    "limit_ratio": 0.3,
                },
            )

        client, transport = _client_with_handler(handler)
        _set_transport(transport)
        with pytest.raises(nc.ManualTargetsConfirmationRequiredError) as ei:
            await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=1200)
        assert ei.value.kind == "calories_deviation"
        assert ei.value.details["maintenance_kcal"] == 2100


class TestC4bForeign409IsNotAQuestion:
    @pytest.mark.asyncio
    async def test_other_409_codes_are_api_errors(self) -> None:
        """Ревью #1907: чужой 409 не должен превращаться в «подтверждаешь?» —
        «Да» гоняло бы вопрос по кругу."""
        client, transport = _client_with_handler(
            lambda _r: _error(409, "SOMETHING_ELSE", {"kind": "calories_deviation"})
        )
        _set_transport(transport)
        with pytest.raises(nc.NutritionAPIError) as ei:
            await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=1200)
        assert not isinstance(ei.value, nc.ManualTargetsConfirmationRequiredError)

    @pytest.mark.asyncio
    async def test_non_object_json_body_is_handled(self) -> None:
        client, transport = _client_with_handler(lambda _r: httpx.Response(422, json=["x"]))
        _set_transport(transport)
        with pytest.raises(nc.ManualTargetsRefusedError):
            await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=900)


class TestC5OtherFailures:
    @pytest.mark.asyncio
    async def test_5xx_is_unavailable(self) -> None:
        client, transport = _client_with_handler(lambda _r: httpx.Response(503, json={}))
        _set_transport(transport)
        with pytest.raises(nc.NutritionUnavailableError):
            await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=1800)

    @pytest.mark.asyncio
    async def test_400_is_an_api_error_not_a_refusal(self) -> None:
        client, transport = _client_with_handler(
            lambda _r: _error(400, "VALIDATION_ERROR", {"fields": ["calories_kcal"]})
        )
        _set_transport(transport)
        with pytest.raises(nc.NutritionAPIError) as ei:
            await client.set_manual_targets(external_user_id="bot:max:1", calories_kcal=1800)
        assert not isinstance(ei.value, nc.ManualTargetsRefusedError)
        assert not isinstance(ei.value, nc.ManualTargetsConfirmationRequiredError)
