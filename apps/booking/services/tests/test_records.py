"""Tests for the visit-history / repeat capability (DRF-1032).

The capability is exercised through a fake Ayla client rather than the HTTP
transport: the wire contract already has its own tests
(``apps/integrations/ayla/tests/test_me_bookings_contract.py``), and what
matters here is the POLICY — which visits count, how many the customer gets,
what the repeat check decides, and what it refuses to decide.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.booking.services import records
from apps.integrations.ayla.booking_client import (
    AylaBookingPage,
    AylaRepeatIntent,
    AylaUserRecord,
    BookingBadRequestError,
    BookingUnavailableError,
    RepeatIntentUnusableError,
)


class _BotUser:
    """Minimal stand-in — the capability only reads the identity pair."""

    channel = "max"
    channel_user_id = "83146139"


def _record(
    *,
    appointment_id: str = "a1",
    derived_status: str | None = "completed",
    service_name: str = "Массаж спины",
    master: str = "Инна",
    start: str = "2026-08-12T09:30:00+00:00",
    price: float | None = 2500.0,
) -> AylaUserRecord:
    return AylaUserRecord(
        appointment_id=appointment_id,
        services=[{"id": "svc-1", "name": service_name}],
        master={"id": "spec-1", "display_name": master},
        datetime=start,
        duration_s=3600,
        raw={},
        derived_status=derived_status,
        price=price,
    )


class FakeClient:
    """Records calls and replays scripted answers."""

    def __init__(
        self,
        *,
        pages: list[AylaBookingPage] | None = None,
        detail: AylaUserRecord | None = None,
        intent: AylaRepeatIntent | None = None,
        slots_error: Exception | None = None,
        masters_error: Exception | None = None,
        edges: list[dict[str, Any]] | None = None,
        edges_error: Exception | None = None,
        page_error: Exception | None = None,
        intent_error: Exception | None = None,
    ) -> None:
        self._pages = pages or []
        self._detail = detail
        self._intent = intent
        self._slots_error = slots_error
        self._masters_error = masters_error
        self._edges = edges if edges is not None else []
        self._edges_error = edges_error
        self._page_error = page_error
        self._intent_error = intent_error
        self.calls: list[str] = []
        self.page_requests: list[dict[str, Any]] = []

    def get_user_bookings_page(self, **kwargs: Any) -> AylaBookingPage:
        self.calls.append("page")
        self.page_requests.append(kwargs)
        if self._page_error is not None:
            raise self._page_error
        return self._pages.pop(0)

    def get_booking_detail(self, **kwargs: Any) -> AylaUserRecord:
        self.calls.append("detail")
        if self._detail is None:
            raise BookingBadRequestError("http_404", status_code=404, code="NOT_FOUND")
        return self._detail

    def get_repeat_intent(self, **kwargs: Any) -> AylaRepeatIntent:
        self.calls.append("intent")
        if self._intent_error is not None:
            raise self._intent_error
        assert self._intent is not None
        return self._intent

    def get_available_times(self, **kwargs: Any) -> list[Any]:
        self.calls.append("slots")
        if self._slots_error is not None:
            raise self._slots_error
        return []

    def get_masters(self, **kwargs: Any) -> list[Any]:
        self.calls.append("masters")
        if self._masters_error is not None:
            raise self._masters_error
        return [object()]

    def get_specialist_service_edges(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append("edges")
        if self._edges_error is not None:
            raise self._edges_error
        return self._edges


@pytest.fixture
def patch_client(monkeypatch):
    def _install(client: FakeClient) -> FakeClient:
        monkeypatch.setattr(records, "get_ayla_booking_client", lambda: client)
        return client

    return _install


# ── list_visits ─────────────────────────────────────────────────────────────


class TestCompletedOnlyPolicy:
    """OD-H2 — the list answers "what actually happened to me"."""

    def test_cancellations_and_no_shows_are_filtered_out(self, patch_client) -> None:
        page = AylaBookingPage(
            records=[
                _record(appointment_id="done", derived_status="completed"),
                _record(appointment_id="gone", derived_status="cancelled"),
                _record(appointment_id="missed", derived_status="no_show"),
                _record(appointment_id="stale", derived_status="confirmed"),
            ]
        )
        patch_client(FakeClient(pages=[page]))

        result = records.list_visits(bot_user=_BotUser())

        assert result.status == "ok"
        assert [v.appointment_id for v in result.visits] == ["done"]

    def test_refunded_visits_still_count_as_visits(self, patch_client) -> None:
        """The refund describes the money, not whether the person came."""
        page = AylaBookingPage(
            records=[
                _record(appointment_id="r1", derived_status="refund_completed"),
                _record(appointment_id="r2", derived_status="partial_refund"),
            ]
        )
        patch_client(FakeClient(pages=[page]))

        result = records.list_visits(bot_user=_BotUser())

        assert [v.appointment_id for v in result.visits] == ["r1", "r2"]

    def test_policy_lives_in_one_constant(self) -> None:
        """C.7 — any DRF-1048 variant must be a one-line change."""
        assert records.COMPLETED_VISIT_STATUSES == frozenset(
            {"completed", "refund_completed", "partial_refund"}
        )


class TestDepthAndTopUp:
    def test_at_most_five_by_default(self, patch_client) -> None:
        page = AylaBookingPage(records=[_record(appointment_id=f"a{i}") for i in range(9)])
        patch_client(FakeClient(pages=[page]))

        result = records.list_visits(bot_user=_BotUser())

        assert len(result.visits) == records.DEFAULT_VISIT_LIMIT == 5

    def test_second_page_is_pulled_when_the_first_is_mostly_cancellations(
        self, patch_client
    ) -> None:
        """Five COMPLETED visits, not five rows — the sections are mixed."""
        first = AylaBookingPage(
            records=[
                _record(appointment_id="c1", derived_status="cancelled"),
                _record(appointment_id="v1"),
            ],
            next_cursor="2026-08-01T00:00:00+00:00",
        )
        second = AylaBookingPage(records=[_record(appointment_id=f"v{i}") for i in range(2, 6)])
        client = patch_client(FakeClient(pages=[first, second]))

        result = records.list_visits(bot_user=_BotUser())

        assert [v.appointment_id for v in result.visits] == ["v1", "v2", "v3", "v4", "v5"]
        assert client.page_requests[1]["cursor"] == "2026-08-01T00:00:00+00:00"

    def test_top_up_stops_at_the_ceiling(self, patch_client) -> None:
        """A long cancellation streak must not become an unbounded crawl."""
        pages = [
            AylaBookingPage(
                records=[_record(appointment_id=f"c{i}", derived_status="cancelled")],
                next_cursor=f"cursor-{i}",
            )
            for i in range(10)
        ]
        client = patch_client(FakeClient(pages=pages))

        result = records.list_visits(bot_user=_BotUser())

        # Not "empty": history is still unread beyond the ceiling, and telling
        # the customer they have never visited would be a confident falsehood.
        assert result.status == "backend_unavailable"
        assert client.calls.count("page") == records._MAX_PAGES

    def test_history_section_is_requested(self, patch_client) -> None:
        client = patch_client(FakeClient(pages=[AylaBookingPage(records=[])]))

        records.list_visits(bot_user=_BotUser())

        assert client.page_requests[0]["section"] == "history"
        assert client.page_requests[0]["external_user_id"] == "bot:max:83146139"


class TestEmptyAndOutage:
    def test_no_completed_visits_is_empty_not_ok(self, patch_client) -> None:
        patch_client(FakeClient(pages=[AylaBookingPage(records=[])]))

        assert records.list_visits(bot_user=_BotUser()).status == "empty"

    def test_backend_outage_is_never_dressed_as_empty(self, patch_client) -> None:
        """OD-H1/§30 — stale mirror truth must not fill in for an outage."""
        patch_client(FakeClient(page_error=BookingUnavailableError("http_503")))

        assert records.list_visits(bot_user=_BotUser()).status == "backend_unavailable"


class TestVisitShape:
    def test_only_customer_facing_fields_leave_the_capability(self, patch_client) -> None:
        patch_client(FakeClient(pages=[AylaBookingPage(records=[_record()])]))

        visit = records.list_visits(bot_user=_BotUser()).visits[0]

        assert visit.service_name == "Массаж спины"
        assert visit.master_name == "Инна"
        assert visit.price == Decimal("2500.0")
        assert set(vars(visit)) == {
            "appointment_id",
            "service_name",
            "master_name",
            "start_at",
            "price",
            "closed_by",
        }

    def test_close_source_is_reserved_not_assumed(self, patch_client) -> None:
        """OD-V1 — salon / client / auto-close must stay distinguishable."""
        patch_client(FakeClient(pages=[AylaBookingPage(records=[_record()])]))

        visit = records.list_visits(bot_user=_BotUser()).visits[0]

        assert visit.closed_by is None


# ── get_visit ───────────────────────────────────────────────────────────────


class TestVisitCard:
    def test_card_is_returned(self, patch_client) -> None:
        patch_client(FakeClient(detail=_record(appointment_id="a7")))

        visit = records.get_visit(bot_user=_BotUser(), appointment_id="a7")

        assert visit is not None
        assert visit.appointment_id == "a7"

    def test_someone_elses_booking_yields_nothing_to_disclose(self, patch_client) -> None:
        class _Refusing(FakeClient):
            def get_booking_detail(self, **kwargs: Any):
                raise BookingBadRequestError("http_404", status_code=404, code="NOT_FOUND")

        patch_client(_Refusing())

        assert records.get_visit(bot_user=_BotUser(), appointment_id="x") is None


# ── prepare_repeat ──────────────────────────────────────────────────────────


def _intent(price: float | None = 2500.0) -> AylaRepeatIntent:
    return AylaRepeatIntent(
        service_id="5a1e0000-0000-4000-8000-000000000002",
        specialist_id="7c2f0000-0000-4000-8000-000000000003",
        last_price=price,
        suggested_slots=[],
    )


class TestRepeatHappyPath:
    def test_valid_pair_returns_entry_for_the_existing_flow(self, patch_client) -> None:
        client = patch_client(FakeClient(intent=_intent(), edges=[{"price": "2900.00"}]))

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.status == "ok"
        assert result.entry is not None
        assert result.entry.specialist_id == "7c2f0000-0000-4000-8000-000000000003"
        assert result.entry.service_id == "5a1e0000-0000-4000-8000-000000000002"
        # Slots are consulted BEFORE anything else decides eligibility.
        assert client.calls.index("slots") < client.calls.index("edges")

    def test_empty_slot_list_is_not_a_refusal(self, patch_client) -> None:
        """No slots TODAY says nothing about tomorrow."""
        patch_client(FakeClient(intent=_intent(), detail=_record(), edges=[{"price": "2500.00"}]))

        assert records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status == "ok"

    def test_current_price_comes_from_the_edge_and_change_is_visible(self, patch_client) -> None:
        """OD-H4 — historical price is a fact, never a quote."""
        patch_client(
            FakeClient(intent=_intent(2500.0), detail=_record(), edges=[{"price": "2900.00"}])
        )

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.historical_price == Decimal("2500.0")
        assert result.current_price == Decimal("2900.00")
        assert result.price_changed is True

    def test_unchanged_price_is_not_flagged(self, patch_client) -> None:
        patch_client(
            FakeClient(intent=_intent(2500.0), detail=_record(), edges=[{"price": "2500.0"}])
        )

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").price_changed is False
        )


class TestRepeatRefusals:
    """Each refusal names WHAT went away — never a system error (OD-H4)."""

    def test_master_no_longer_bookable(self, patch_client) -> None:
        """404 without an error code = the specialist queryset refused."""
        patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_unknown", status_code=404, code="unknown"
                ),
                masters_error=BookingBadRequestError("http_404", status_code=404, code="unknown"),
            )
        )

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.status == "master_unavailable"
        assert result.entry is not None  # caller can still offer an alternative

    def test_service_withdrawn(self, patch_client) -> None:
        """Resolver refused AND the edge is still there ⇒ the service went."""
        patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_NOT_FOUND", status_code=404, code="NOT_FOUND"
                ),
                edges=[{"price": "2500.00"}],
            )
        )

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "service_unavailable"
        )

    def test_pair_dissolved(self, patch_client) -> None:
        """Resolver refused AND no active edge ⇒ the master dropped it."""
        patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_NOT_FOUND", status_code=404, code="NOT_FOUND"
                ),
                edges=[],
            )
        )

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "link_unavailable"
        )

    def test_unusable_prefill_is_named_not_swallowed(self, patch_client) -> None:
        """DRF-1049 guard: a malformed id must not reach the booking flow."""
        patch_client(FakeClient(intent_error=RepeatIntentUnusableError("service_id", value="None")))

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "prefill_unusable"
        )

    def test_backend_422_for_a_serviceless_booking(self, patch_client) -> None:
        """The fixed backend answers 422 SERVICE_NOT_FOUND instead of "None"."""
        patch_client(
            FakeClient(
                intent_error=BookingBadRequestError(
                    "http_422", status_code=422, code="SERVICE_NOT_FOUND"
                )
            )
        )

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "prefill_unusable"
        )

    def test_outage_is_reported_as_outage(self, patch_client) -> None:
        patch_client(FakeClient(intent_error=BookingUnavailableError("http_503")))

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "backend_unavailable"
        )

    def test_probe_outage_does_not_masquerade_as_unavailable_master(self, patch_client) -> None:
        """A 503 on the probe is OUR problem, not the master's."""
        patch_client(
            FakeClient(
                intent=_intent(), detail=_record(), slots_error=BookingUnavailableError("http_503")
            )
        )

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "backend_unavailable"
        )


class TestNamesReachTheCaller:
    """Every branch must name the service and the master.

    The gap these tests close: the producer's own boundary test asserted the
    fields carried no Cyrillic, which was vacuously true while they were
    always empty — so a refusal read «Мастер сейчас не принимает» instead of
    naming Инна, and nothing went red.
    """

    def test_happy_path_names_service_and_master(self, patch_client) -> None:
        patch_client(FakeClient(intent=_intent(), detail=_record(), edges=[{"price": "2500"}]))

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.service_name == "Массаж спины"
        assert result.master_name == "Инна"

    def test_refusal_names_them_too(self, patch_client) -> None:
        patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_NOT_FOUND", status_code=404, code="NOT_FOUND"
                ),
                edges=[],
            )
        )

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.status == "link_unavailable"
        assert result.service_name == "Массаж спины"
        assert result.master_name == "Инна"


class TestInfrastructureIsNeverStatedAsFact:
    """OD-H1 — an outage must not be rendered as news about the salon."""

    @pytest.mark.parametrize(
        "failure",
        [
            BookingUnavailableError("circuit_open"),
            BookingUnavailableError("catalog_incomplete"),
            BookingBadRequestError("http_403", status_code=403, code="FORBIDDEN"),
        ],
    )
    def test_edge_lookup_failure_is_not_a_dissolved_link(
        self, patch_client, failure: Exception
    ) -> None:
        """A blip while asking about the edge used to read as «мастер больше
        не делает эту услугу» — a claim about the catalog we never verified."""
        patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_NOT_FOUND", status_code=404, code="NOT_FOUND"
                ),
                edges_error=failure,
            )
        )

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.status == "backend_unavailable"

    def test_master_absence_is_confirmed_before_it_is_claimed(self, patch_client) -> None:
        """A code-less 404 can also be an nginx page or a renamed route.

        The specialist endpoint answering 200 means the master is bookable,
        so the refusal belongs to the service side, not to them.
        """
        client = patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_unknown", status_code=404, code="unknown"
                ),
            )
        )

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert "masters" in client.calls
        assert result.status == "service_unavailable"

    def test_unreadable_master_probe_is_an_outage(self, patch_client) -> None:
        patch_client(
            FakeClient(
                intent=_intent(),
                detail=_record(),
                slots_error=BookingBadRequestError(
                    "http_404_unknown", status_code=404, code="unknown"
                ),
                masters_error=BookingUnavailableError("http_503"),
            )
        )

        assert (
            records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1").status
            == "backend_unavailable"
        )

    def test_missing_status_field_is_not_an_empty_history(self, patch_client) -> None:
        """The field the policy keys off vanishing is a contract change."""
        page = AylaBookingPage(records=[_record(derived_status=None)])
        patch_client(FakeClient(pages=[page]))

        assert records.list_visits(bot_user=_BotUser()).status == "backend_unavailable"

    def test_status_case_does_not_hide_a_visit(self, patch_client) -> None:
        page = AylaBookingPage(records=[_record(derived_status="COMPLETED")])
        patch_client(FakeClient(pages=[page]))

        assert records.list_visits(bot_user=_BotUser()).status == "ok"


class TestCapabilityBoundary:
    def test_no_presentation_leaks_into_the_result(self, patch_client) -> None:
        """OD-IR3 — callers render; the capability decides.

        If a Russian sentence ever appears in a result field, the split has
        been broken and the concierge/Mini App callers would inherit MAX
        wording they cannot use.
        """
        patch_client(FakeClient(intent=_intent(), detail=_record(), edges=[{"price": "2500.00"}]))

        result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

        assert result.status in {
            "ok",
            "master_unavailable",
            "service_unavailable",
            "link_unavailable",
            "prefill_unusable",
            "backend_unavailable",
        }
        # The names ARE Russian — they are data, not presentation. What must
        # never appear is a rendered sentence: the status stays a machine slug
        # so a concierge or Mini App caller can word it their own way.
        assert result.status == "ok"
        assert " " not in result.status
