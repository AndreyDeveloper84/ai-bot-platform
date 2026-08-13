"""Contract tests for ``me/bookings`` — the REAL backend response shape (DRF-1032).

Why this file exists
--------------------

``AylaBookingHTTPClient.get_user_appointments`` was written against a response
shape Ayla never returned: ``{"upcoming": [...], "history": [...]}``. The
canonical backend (``appointments/records_api.py:346-349``) answers with::

    {"data": {"items": [...], "next_cursor": "<iso8601>|null"}}

After the ``{"data": ...}`` envelope is stripped, the client looks for
``upcoming``/``history``, finds neither, falls through to ``_as_rows`` — which
receives a *dict*, not a list, and returns ``[]``. The failure mode is a
**silent empty list, not an error**, which reads exactly like "this customer
has no bookings".

The existing suite never caught it because ``test_booking_client.py`` mocks the
same invented shape. These tests mock what the backend actually sends, taken
field-for-field from ``records_api.py:147-185`` (``_build_item``).

Two more contract facts locked down here:

* ``section`` is a REQUIRED query param for history reads — omitting it makes
  the backend default to ``upcoming`` (``records_api.py:295``), so a history
  request silently returns future bookings;
* ``price``/``last_price`` arrive as JSON **numbers**, not strings: the views
  return plain dicts through ``success_response``, so DRF's serializers never
  run and its encoder maps ``Decimal`` → float. The declared schema says
  string, so the parser must tolerate both.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc


@pytest.fixture(autouse=True)
def _clear_django_cache() -> None:
    from django.core.cache import cache

    cache.clear()


def _client_with(handler) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="secret-tok",
        transport=httpx.MockTransport(handler),
    )


# A single history item, verbatim in the shape ``_build_item`` produces.
_ITEM: dict[str, Any] = {
    "id": "4d67c3cd-0000-4000-8000-000000000001",
    "status": "completed",
    "derived_status": "completed",
    "start_datetime": "2026-08-12T09:30:00+00:00",
    "end_datetime": "2026-08-12T10:30:00+00:00",
    "price": 2500.0,
    "service": {
        "id": "5a1e0000-0000-4000-8000-000000000002",
        "name": "Массаж спины",
        "duration_minutes": 60,
    },
    "specialist": {
        "id": "7c2f0000-0000-4000-8000-000000000003",
        "display_name": "Инна",
    },
    "tenant": {
        "id": "9e3a0000-0000-4000-8000-000000000004",
        "slug": "formula-tela",
        "name": "Формула тела",
    },
    "is_first_visit": False,
    "original_datetime": None,
}


def _envelope(items: list[dict[str, Any]], next_cursor: str | None = None) -> dict:
    """The real backend envelope — ``success_response({items, next_cursor})``."""
    return {"data": {"items": items, "next_cursor": next_cursor}}


class TestRealBackendShapeIsParsed:
    """The bug, stated as a test: the canonical shape must not read as empty."""

    def test_history_items_are_returned(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([_ITEM]))

        out = _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="history",
        )

        assert [r.appointment_id for r in out] == [_ITEM["id"]]

    def test_empty_page_is_empty_list_not_error(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([]))

        out = _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="history",
        )

        assert out == []


class TestSectionIsSent:
    """A history read that omits ``section`` silently returns FUTURE bookings."""

    def test_section_history_reaches_the_wire(self, db) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=_envelope([_ITEM]))

        _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="history",
        )

        assert captured[0].url.path == "/api/v1/internal/me/bookings/"
        assert captured[0].url.params.get("section") == "history"
        assert captured[0].headers["X-External-User-ID"] == "bot:max:83146139"

    def test_section_upcoming_reaches_the_wire(self, db) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=_envelope([]))

        _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="upcoming",
        )

        assert captured[0].url.params.get("section") == "upcoming"


class TestFieldsSurvivesTheMapping:
    """DRF-1032 needs the display policy fields the old DTO dropped."""

    def test_derived_status_and_price_are_preserved(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([_ITEM]))

        row = _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="history",
        )[0]

        # The completed-only policy (OD-H2) keys off derived_status, and the
        # visit line shows the historical price — both must reach the caller.
        assert row.derived_status == "completed"
        assert row.price == 2500.0
        assert row.master == _ITEM["specialist"]
        assert row.services == [_ITEM["service"]]

    def test_price_as_string_is_tolerated(self, db) -> None:
        """Schema declares a string; the running code emits a number."""
        item = {**_ITEM, "price": "2500.00"}

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([item]))

        row = _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="history",
        )[0]

        assert row.price == 2500.0

    def test_zero_price_stays_distinguishable_from_unknown(self, db) -> None:
        """Payment-free pilot bookings really do cost 0 — not the same as absent."""
        priced_zero = {**_ITEM, "price": 0}
        no_price = {k: v for k, v in _ITEM.items() if k != "price"}

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([priced_zero, no_price]))

        rows = _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
            section="history",
        )

        assert rows[0].price == 0.0
        assert rows[1].price is None


class TestCursorIsExposed:
    """Client-side filtering may need a second page to reach five visits."""

    def test_next_cursor_reaches_the_caller(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([_ITEM], "2026-08-12T09:30:00+00:00"))

        page = _client_with(handler).get_user_bookings_page(
            external_user_id="bot:max:83146139",
            section="history",
        )

        assert page.next_cursor == "2026-08-12T09:30:00+00:00"
        assert len(page.records) == 1

    def test_last_page_has_no_cursor(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope([_ITEM], None))

        page = _client_with(handler).get_user_bookings_page(
            external_user_id="bot:max:83146139",
            section="history",
        )

        assert page.next_cursor is None

    def test_limit_and_cursor_reach_the_wire(self, db) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=_envelope([]))

        _client_with(handler).get_user_bookings_page(
            external_user_id="bot:max:83146139",
            section="history",
            limit=20,
            cursor="2026-08-12T09:30:00+00:00",
        )

        assert captured[0].url.params.get("limit") == "20"
        assert captured[0].url.params.get("cursor") == "2026-08-12T09:30:00+00:00"

    def test_unknown_section_fails_at_the_call_site(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not reach the wire")

        with pytest.raises(ValueError, match="section must be one of"):
            _client_with(handler).get_user_bookings_page(
                external_user_id="bot:max:83146139",
                section="past",
            )


class TestExistingCallerUnchanged:
    """The only production caller today must behave byte-for-byte as before.

    ``AylaYClientsAdapter.get_user_records`` (``provider.py:273-276``) looks up
    the cancel/reschedule handle and calls without a section — it must keep
    reading UPCOMING bookings and keep getting a plain list.
    """

    def test_default_section_is_upcoming_and_return_is_a_list(self, db) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=_envelope([_ITEM]))

        out = _client_with(handler).get_user_appointments(
            external_user_id="bot:max:83146139",
        )

        assert captured[0].url.params.get("section") == "upcoming"
        assert isinstance(out, list)
        assert out[0].appointment_id == _ITEM["id"]


class TestBookingDetail:
    def test_detail_is_fetched_by_id(self, db) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json={"data": {**_ITEM, "notes": "личное"}})

        row = _client_with(handler).get_booking_detail(
            external_user_id="bot:max:83146139",
            booking_id=_ITEM["id"],
        )

        assert captured[0].method == "GET"
        assert captured[0].url.path == f"/api/v1/internal/me/bookings/{_ITEM['id']}/"
        assert row.appointment_id == _ITEM["id"]
        # Sensitive extras stay in raw for the display layer to ignore.
        assert row.raw["notes"] == "личное"

    def test_someone_elses_booking_is_a_4xx_not_an_empty_card(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

        with pytest.raises(bc.BookingAPIError):
            _client_with(handler).get_booking_detail(
                external_user_id="bot:max:83146139",
                booking_id=_ITEM["id"],
            )


class TestRepeatIntent:
    def test_prefill_is_parsed(self, db) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "service_id": _ITEM["service"]["id"],
                        "specialist_id": _ITEM["specialist"]["id"],
                        "last_price": 2500.0,
                        "suggested_slots": [],
                    }
                },
            )

        intent = _client_with(handler).get_repeat_intent(
            external_user_id="bot:max:83146139",
            booking_id=_ITEM["id"],
        )

        assert captured[0].method == "POST"
        assert captured[0].url.path == f"/api/v1/internal/me/bookings/{_ITEM['id']}/repeat-intent/"
        assert intent.service_id == _ITEM["service"]["id"]
        assert intent.specialist_id == _ITEM["specialist"]["id"]
        assert intent.last_price == 2500.0
        assert intent.suggested_slots == []

    def test_literal_none_service_id_is_rejected(self, db) -> None:
        """DRF-1049: salon bookings come back with the string "None", HTTP 200."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "service_id": "None",
                        "specialist_id": _ITEM["specialist"]["id"],
                        "last_price": 2500.0,
                        "suggested_slots": [],
                    }
                },
            )

        with pytest.raises(bc.RepeatIntentUnusableError, match="service_id"):
            _client_with(handler).get_repeat_intent(
                external_user_id="bot:max:83146139",
                booking_id=_ITEM["id"],
            )

    def test_garbage_specialist_id_is_rejected(self, db) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "service_id": _ITEM["service"]["id"],
                        "specialist_id": "",
                        "last_price": None,
                        "suggested_slots": [],
                    }
                },
            )

        with pytest.raises(bc.RepeatIntentUnusableError, match="specialist_id"):
            _client_with(handler).get_repeat_intent(
                external_user_id="bot:max:83146139",
                booking_id=_ITEM["id"],
            )
