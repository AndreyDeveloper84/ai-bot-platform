"""Клиент каталога — место работы и подсказки адреса (DRF-1811, M19).

Провод: ``GET/POST internal/specialists/{id}/service-locations/``,
``PATCH …/{item_id}/`` и ``POST …/geocoding/suggest/`` с Bearer и
``X-External-User-ID`` субъекта. Два свойства, ради которых файл заведён:

* строка адреса уходит в ТЕЛЕ запроса, не в URL (M12a, #476) — и не в лог;
* 503 подсказок (геокодер не настроен — постоянное состояние стенда) НЕ
  дёргает выключатель клиента: иначе один ввод адреса гасил бы бронирование.
  Положительный контроль рядом: настоящий 5xx на месте работы выключатель
  дёргает, как и везде.
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from apps.integrations.ayla import booking_client as bc

SPEC = "11111111-1111-1111-1111-111111111111"
ACTOR = "bot:max:777"


def _client_with(handler) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="secret-tok", transport=httpx.MockTransport(handler)
    )


def _state() -> dict:
    return {"data": {"specialist_id": SPEC, "city": "Москва", "places": [], "areas": []}}


class TestLocationsWire:
    def test_get_names_the_subject_and_unwraps(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(200, json=_state())

        data = _client_with(handler).get_service_locations(
            specialist_id=SPEC, external_user_id=ACTOR
        )
        req = seen[0]
        assert req.method == "GET"
        assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/service-locations/"
        assert req.headers["X-External-User-ID"] == ACTOR
        assert data["city"] == "Москва"

    def test_post_and_patch_send_the_fields_as_given(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(201 if req.method == "POST" else 200, json=_state())

        c = _client_with(handler)
        c.create_service_location(
            specialist_id=SPEC,
            external_user_id=ACTOR,
            fields={"kind": "mobile", "coverage": "later"},
        )
        c.patch_service_location(
            specialist_id=SPEC,
            external_user_id=ACTOR,
            item_id="abc",
            fields={"coverage": "whole_city"},
        )
        assert json.loads(seen[0].content) == {"kind": "mobile", "coverage": "later"}
        assert seen[1].method == "PATCH"
        assert seen[1].url.path == f"/api/v1/internal/specialists/{SPEC}/service-locations/abc/"
        assert json.loads(seen[1].content) == {"coverage": "whole_city"}

    def test_409_from_the_catalog_carries_its_code(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"error": {"code": "place_already_set", "message": "x"}}
            )

        try:
            _client_with(handler).create_service_location(
                specialist_id=SPEC,
                external_user_id=ACTOR,
                fields={"kind": "private_studio", "address": "x"},
            )
        except bc.BookingBadRequestError as exc:
            assert exc.status_code == 409 and exc.code == "place_already_set"
        else:
            raise AssertionError("409 must surface as BookingBadRequestError")


class TestSuggestWire:
    def test_query_goes_in_the_body_not_the_url_and_not_the_log(self, caplog) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "city": "Москва",
                        "suggestions": [
                            {"value": "Тверская 1", "unrestricted_value": "г Москва, Тверская 1"}
                        ],
                    }
                },
            )

        with caplog.at_level(logging.DEBUG):
            data = _client_with(handler).suggest_address(
                specialist_id=SPEC, external_user_id=ACTOR, q="Тверская 1, кв 7"
            )
        req = seen[0]
        assert req.method == "POST"
        assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/geocoding/suggest/"
        assert "Тверская" not in str(req.url)
        assert json.loads(req.content) == {"q": "Тверская 1, кв 7"}
        assert "Тверская" not in caplog.text
        assert data["available"] is True
        assert data["suggestions"][0]["value"] == "Тверская 1"

    def test_503_misconfigured_is_unavailable_without_tripping_the_breaker(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503,
                json={
                    "error": {
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "enter the address manually",
                        "details": {"reason": "misconfigured"},
                    }
                },
            )

        c = _client_with(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD + 1):  # больше порога — а выключатель молчит
            data = c.suggest_address(specialist_id=SPEC, external_user_id=ACTOR, q="x")
            assert data == {"available": False, "reason": "misconfigured", "suggestions": []}
        assert c._circuit.is_open(now=time.monotonic()) is False

    def test_a_real_5xx_on_locations_still_trips_the_breaker(self) -> None:
        """Положительный контроль: выключатель жив — не дёргается только 503 подсказок."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        c = _client_with(handler)
        tripped = 0
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD + 1):
            try:
                c.get_service_locations(specialist_id=SPEC, external_user_id=ACTOR)
            except bc.BookingUnavailableError:
                tripped += 1
        assert tripped >= 1
        assert c._circuit.is_open(now=time.monotonic()) is True

    def test_409_no_city_is_named(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"error": {"code": "CONFLICT", "details": {"reason": "no_city"}}}
            )

        data = _client_with(handler).suggest_address(
            specialist_id=SPEC, external_user_id=ACTOR, q="x"
        )
        assert data == {"available": False, "reason": "no_city", "suggestions": []}
