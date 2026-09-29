"""One unreadable row must not cost the salon its catalog (DRF-1494).

The pilot mirrored 94 services against 265 upstream for twelve days, with
none of the 14 active manicure rows. The fetchers parsed their pages in a
bare list comprehension, so a single row Ayla served with an unparseable
`base_price` or `duration_minutes` raised out of the whole fetch;
`_run_locked` turned that into a silent `SyncResult(ran=True, error=...)`
and the mirror kept the shape it had before.

Each "the bad row is gone" assertion here is paired with an assertion that
the GOOD rows arrived on the same call. "Nothing bad came back" is equally
true of a fetch that returned nothing at all, which is the failure mode
under test.
"""

from __future__ import annotations

import httpx
import pytest

from apps.catalog.services.http_client import CatalogHttpClient

BASE = "https://ayla.example"
TENANT = "11111111-1111-1111-1111-111111111111"


def _page(*rows: dict) -> dict:
    return {"count": len(rows), "next": None, "previous": None, "results": list(rows)}


def _service(sid: str, **over) -> dict:
    row = {
        "id": sid,
        "updated_at": "2026-09-04T10:00:00Z",
        "name": f"Service {sid}",
        "is_active": True,
        "base_price": "1500.00",
        "duration_minutes": 60,
    }
    row.update(over)
    return row


def _client(monkeypatch, transport: httpx.MockTransport) -> CatalogHttpClient:
    return CatalogHttpClient(
        base_url=BASE,
        token="t0ken",  # pragma: allowlist secret
        http_client=httpx.Client(transport=transport),
    )


def _serving(payload: dict) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _req: httpx.Response(200, json=payload))


class TestSalonServices:
    @pytest.mark.parametrize(
        ("label", "poison"),
        [
            ("price the parser cannot read", {"base_price": "от 1500"}),
            ("duration carrying its unit", {"duration_minutes": "60 мин"}),
            ("updated_at missing entirely", {"updated_at": None}),
            ("updated_at not ISO 8601", {"updated_at": "04.09.2026"}),
        ],
    )
    def test_one_poison_row_costs_only_itself(self, monkeypatch, label, poison) -> None:
        page = _page(_service("good-1"), _service("poison", **poison), _service("good-2"))
        client = _client(monkeypatch, _serving(page))

        dtos = client.fetch_salon_services(tenant_id=TENANT)

        ids = [d.ayla_service_id for d in dtos]
        # Presence: the healthy rows came back. Before this fix the list was
        # not merely missing the poison row, it did not exist — the call
        # raised and the salon's whole catalog stayed frozen.
        assert ids == ["good-1", "good-2"], label
        assert "poison" not in ids, label

    def test_a_clean_page_loses_nothing(self, monkeypatch) -> None:
        # The positive guard for the parametrisation above: isolation must not
        # be paid for by dropping rows that were fine all along.
        page = _page(_service("a"), _service("b"), _service("c"))
        client = _client(monkeypatch, _serving(page))

        dtos = client.fetch_salon_services(tenant_id=TENANT)

        assert [d.ayla_service_id for d in dtos] == ["a", "b", "c"]

    def test_the_dropped_row_is_named_at_error_level(self, monkeypatch, caplog) -> None:
        # A silently dropped row would trade one invisible failure for
        # another. ERROR is the level Sentry captures, and the id is what
        # makes the report actionable on the Ayla side.
        page = _page(_service("good"), _service("bad-row", base_price="от 1500"))
        client = _client(monkeypatch, _serving(page))

        with caplog.at_level("ERROR"):
            dtos = client.fetch_salon_services(tenant_id=TENANT)

        assert [d.ayla_service_id for d in dtos] == ["good"]
        assert "catalog.http.row_unparseable" in caplog.text
        assert "bad-row" in caplog.text


class TestEdgeSnapshot:
    def test_unreadable_edge_downgrades_the_snapshot(self, monkeypatch) -> None:
        # Reconciliation DELETES edges absent from a complete snapshot. An
        # edge this side could not read is not an edge upstream deleted, so
        # dropping one must cost the run its delete authority — otherwise
        # isolating a malformed row would unbook a real master.
        good = {
            "id": "e1",
            "salon_service": "s1",
            "specialist": "m1",
            "updated_at": "2026-09-04T10:00:00Z",
        }
        bad = {"id": "e2", "specialist": "m1", "updated_at": "2026-09-04T10:00:00Z"}
        client = _client(monkeypatch, _serving(_page(good, bad)))

        snapshot = client.fetch_specialist_services(tenant_id=TENANT)

        assert [e.ayla_specialist_service_id for e in snapshot.edges] == ["e1"]
        assert snapshot.complete is False

    def test_a_clean_edge_page_keeps_delete_authority(self, monkeypatch) -> None:
        good = {
            "id": "e1",
            "salon_service": "s1",
            "specialist": "m1",
            "updated_at": "2026-09-04T10:00:00Z",
        }
        client = _client(monkeypatch, _serving(_page(good)))

        snapshot = client.fetch_specialist_services(tenant_id=TENANT)

        assert [e.ayla_specialist_service_id for e in snapshot.edges] == ["e1"]
        assert snapshot.complete is True
