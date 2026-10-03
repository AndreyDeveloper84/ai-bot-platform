"""DRF-2755 / DRF-2762 — ветка одного мастера передаёт координаты каталогу.

До листа ``get_masters(specialist_id=…, lat=…, lon=…)`` звал
``specialists/{id}/`` без ``lat``/``lon`` — координаты молча терялись, и
карточка мастера не могла получить расстояние. Ответы каталога — из
``fixtures/specialist_detail_distance_live_2762.json``: сняты окном ayla-7a
с настоящей ручки каталога, а не написаны от руки.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

_LIVE = json.loads(
    (Path(__file__).parent / "fixtures" / "specialist_detail_distance_live_2762.json").read_text(
        encoding="utf-8"
    )
)["responses"]

MASTER_ID = "488acaba-8190-4390-b6bb-81a48e8b6a0b"


@pytest.fixture(autouse=True)
def _clear_django_cache() -> None:
    from django.core.cache import cache

    cache.clear()


def _client_answering(case: str, captured: list[httpx.Request]) -> bc.AylaBookingHTTPClient:
    live = _LIVE[case]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(live["status"], json=live["body"])

    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="secret-tok", transport=httpx.MockTransport(handler)
    )


class TestTheDetailBranchCarriesTheCoordinates:
    def test_d1_coordinates_reach_the_catalog_and_the_distance_comes_back(self) -> None:
        captured: list[httpx.Request] = []
        client = _client_answering("with_point", captured)

        masters = client.get_masters(specialist_id=MASTER_ID, lat=53.2, lon=45.0)

        assert len(captured) == 1
        assert captured[0].url.path == f"/api/v1/internal/specialists/{MASTER_ID}/"
        assert dict(captured[0].url.params) == _LIVE["with_point"]["query"]
        assert dict(captured[0].url.params) == {"lat": "53.200000", "lon": "45.000000"}
        assert [(m.id, m.distance_meters) for m in masters] == [(MASTER_ID, 1303)]

    def test_d2_without_coordinates_the_query_stays_empty(self) -> None:
        captured: list[httpx.Request] = []
        client = _client_answering("without_point", captured)

        masters = client.get_masters(specialist_id=MASTER_ID)

        assert dict(captured[0].url.params) == {}
        assert [(m.id, m.distance_meters) for m in masters] == [(MASTER_ID, None)]

    def test_d3_one_coordinate_alone_is_not_sent(self) -> None:
        captured: list[httpx.Request] = []
        client = _client_answering("without_point", captured)

        client.get_masters(specialist_id=MASTER_ID, lat=53.2)

        assert dict(captured[0].url.params) == {}

    def test_d4_a_master_without_a_place_has_no_distance(self) -> None:
        captured: list[httpx.Request] = []
        client = _client_answering("no_place_with_point", captured)

        masters = client.get_masters(
            specialist_id="d71f498b-b2a8-4c88-a15d-1960cc649c30", lat=53.2, lon=45.0
        )

        assert dict(captured[0].url.params) == {"lat": "53.200000", "lon": "45.000000"}
        assert [m.distance_meters for m in masters] == [None]
