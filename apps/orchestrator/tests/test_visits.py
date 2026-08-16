"""Tests for the visits channel adapter (DRF-1032).

The capability has its own tests (``apps/booking/services/tests/test_records.py``);
these cover the half that faces a human: what the reply says, which buttons it
carries, and what it refuses to say.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.booking.services.records import (
    RepeatEntry,
    RepeatResult,
    RepeatStatus,
    Visit,
    VisitsResult,
)
from apps.orchestrator import visits as visits_mod


class _BotUser:
    id = "11111111-2222-3333-4444-555555555555"
    channel = "max"
    channel_user_id = "83146139"


def _visit(
    *,
    appointment_id: str = "a1",
    service: str = "Массаж спины",
    master: str = "Инна",
    start: str = "2026-08-12T09:30:00+00:00",
    price: Decimal | None = Decimal("2500"),
) -> Visit:
    return Visit(
        appointment_id=appointment_id,
        service_name=service,
        master_name=master,
        start_at=start,
        price=price,
    )


@pytest.fixture
def capability(monkeypatch):
    """Script the capability; the adapter must not reach the network."""

    state: dict = {
        "upcoming": VisitsResult(status="empty"),
        "visits": VisitsResult(status="empty"),
        "visit": None,
        "repeat": RepeatResult(status="backend_unavailable"),
    }

    monkeypatch.setattr(visits_mod, "list_upcoming", lambda **_: state["upcoming"])
    monkeypatch.setattr(visits_mod, "list_visits", lambda **_: state["visits"])
    monkeypatch.setattr(visits_mod, "prepare_repeat", lambda **_: state["repeat"])
    monkeypatch.setattr("apps.booking.services.records.get_visit", lambda **_: state["visit"])
    return state


class TestVisitsList:
    def test_past_visits_are_listed_with_service_master_date_and_price(
        self, capability, db
    ) -> None:
        capability["visits"] = VisitsResult(status="ok", visits=(_visit(),))

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert "Ваши последние визиты:" in reply.text
        assert "Массаж спины" in reply.text
        # No «у {имя}»: the name arrives nominative and Russian would need the
        # genitive. A separator cannot decline a name wrongly.
        assert "· Инна ·" in reply.text
        assert "12 августа" in reply.text
        assert "2500 ₽" in reply.text

    def test_upcoming_and_past_answer_the_same_question(self, capability, db) -> None:
        """H-1 — one detector, one reply, one source."""
        capability["upcoming"] = VisitsResult(
            status="ok",
            visits=(
                _visit(appointment_id="u1", service="Маникюр", start="2026-09-01T12:00:00+00:00"),
            ),
        )
        capability["visits"] = VisitsResult(status="ok", visits=(_visit(),))

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert "Ваши предстоящие записи:" in reply.text
        assert "Ваши последние визиты:" in reply.text
        assert reply.text.index("предстоящие") < reply.text.index("последние")

    def test_each_past_visit_gets_a_card_button(self, capability, db) -> None:
        capability["visits"] = VisitsResult(
            status="ok", visits=(_visit(appointment_id="a1"), _visit(appointment_id="a2"))
        )

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert reply.action_data is not None
        payload = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert [b["callback"] for b in payload] == [
            "cb:visit:card:a1",
            "cb:visit:card:a2",
        ]

    def test_empty_state_offers_a_next_step(self, capability, db) -> None:
        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert "пока нет завершённых визитов" in reply.text
        assert "подобрать" in reply.text.lower()
        assert reply.action_data is None

    def test_backend_outage_is_admitted_not_papered_over(self, capability, db) -> None:
        """§30 — the mirror never fills in for an unreachable source."""
        capability["visits"] = VisitsResult(status="backend_unavailable")

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert "попробуйте" in reply.text.lower()
        assert "визит" not in reply.text.lower().replace("записи", "")

    def test_half_an_answer_is_not_served_as_a_whole_one(self, capability, db) -> None:
        """Upcoming read succeeded, history failed — the list would look complete."""
        capability["upcoming"] = VisitsResult(status="ok", visits=(_visit(),))
        capability["visits"] = VisitsResult(status="backend_unavailable")

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert "Ваши предстоящие записи" not in reply.text

    def test_internal_fields_never_reach_the_customer(self, capability, db) -> None:
        capability["visits"] = VisitsResult(status="ok", visits=(_visit(),))

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        for leak in ("a1", "tenant", "proxy", "completed", "uuid"):
            assert leak not in reply.text.lower()


class TestVisitCard:
    def test_card_shows_what_happened_and_offers_repeat(self, capability, db) -> None:
        capability["visit"] = _visit()

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:card:a1"
        )

        assert "Массаж спины" in reply.text
        assert "Мастер: Инна" in reply.text
        assert "Стоил: 2500 ₽" in reply.text
        assert reply.action_data is not None
        button = reply.action_data["attachments"][0]["payload"]["buttons"][0]
        assert button["label"] == "Записаться ещё"
        assert button["callback"] == "cb:visit:repeat:a1"

    def test_unknown_booking_says_so_without_disclosing(self, capability, db) -> None:
        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:card:someone-elses"
        )

        assert "попробуйте" in reply.text.lower()


class TestRepeat:
    def test_valid_repeat_enters_the_existing_booking_flow(self, capability, db) -> None:
        """AC-17 — no second state machine; the payload is the existing one."""
        capability["repeat"] = RepeatResult(
            status="ok",
            entry=RepeatEntry(specialist_id="spec-1", service_id="svc-1"),
            service_name="Массаж спины",
            master_name="Инна",
            historical_price=Decimal("2500"),
            current_price=Decimal("2500"),
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert reply.action_data is not None
        assert reply.action_data["buttons"][0]["callback"] == ("cb:book:pick_master:spec-1:svc-1")
        assert "Повторим" in reply.text

    def test_price_change_is_shown_not_swallowed(self, capability, db) -> None:
        """OD-H4 — the old number must never pass for the current one."""
        capability["repeat"] = RepeatResult(
            status="ok",
            entry=RepeatEntry(specialist_id="spec-1", service_id="svc-1"),
            service_name="Массаж",
            master_name="Инна",
            historical_price=Decimal("2500"),
            current_price=Decimal("2900"),
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert "2500 ₽" in reply.text
        assert "2900 ₽" in reply.text

    def test_equal_price_is_not_mentioned(self, capability, db) -> None:
        capability["repeat"] = RepeatResult(
            status="ok",
            entry=RepeatEntry(specialist_id="spec-1", service_id="svc-1"),
            service_name="Массаж",
            historical_price=Decimal("2500"),
            current_price=Decimal("2500"),
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert "В прошлый раз" not in reply.text

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("master_unavailable", "не принимает"),
            ("service_unavailable", "не оказывают"),
            ("link_unavailable", "больше не делает"),
            ("prefill_unusable", "подберём заново"),
        ],
    )
    def test_every_refusal_offers_a_way_forward(
        self, capability, db, status: RepeatStatus, expected: str
    ) -> None:
        """OD-H4 / §18-20 — a graceful alternative, never a system error."""
        capability["repeat"] = RepeatResult(status=status, master_name="Инна")

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert expected in reply.text
        assert "?" in reply.text or "заново" in reply.text
        for slug in ("master_unavailable", "service_unavailable", "link_unavailable", "error"):
            assert slug not in reply.text
        assert reply.action_data is None

    def test_outage_during_repeat_is_temporary_not_terminal(self, capability, db) -> None:
        capability["repeat"] = RepeatResult(status="backend_unavailable")

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert "позже" in reply.text.lower()


class TestFormatting:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The shape the wire actually carries: UTC with a trailing Z.
            # DRF-1071 — a visit at 14:00 Moscow must not read as 11:00.
            ("2026-08-19T11:00:00Z", "19 августа, среда, 14:00"),
            ("2026-08-12T09:30:00+00:00", "12 августа, среда, 12:30"),
            # Already in Moscow time — the conversion is a no-op, not a shift.
            ("2026-01-05T18:00:00+03:00", "5 января, понедельник, 18:00"),
            # Winter: Moscow has no DST, so the offset stays +03:00.
            ("2026-01-05T15:00:00Z", "5 января, понедельник, 18:00"),
            ("", ""),
            ("not-a-date", ""),
        ],
    )
    def test_dates_read_as_russian_local_time_not_utc(self, raw: str, expected: str) -> None:
        assert visits_mod._format_when(raw) == expected

    def test_naive_timestamp_is_not_given_an_invented_offset(self) -> None:
        """No timezone on the wire means we do not know it — do not guess."""
        assert visits_mod._format_when("2026-08-19T14:00:00") == "19 августа, среда, 14:00"

    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            (Decimal("2500"), "2500 ₽"),
            (Decimal("2500.00"), "2500 ₽"),
            (Decimal("2500.50"), "2500.50 ₽"),
            (None, ""),
        ],
    )
    def test_money_has_no_stray_decimals(self, amount, expected: str) -> None:
        assert visits_mod._format_money(amount) == expected


class TestCapabilityAndAdapterTogether:
    """The seam both suites stopped talking across.

    Everywhere else the adapter is driven by hand-built results and the
    capability by a fake client — so a field the capability never fills can
    look correct on one side and be asserted on the other. These tests drive
    ONE fake HTTP client through the real capability into the real adapter,
    which is the only shape that catches that class of defect.
    """

    @pytest.fixture
    def wired(self, monkeypatch):
        from apps.booking.services import records as records_mod
        from apps.booking.services.tests.test_records import FakeClient, _intent, _record

        def _install(**kwargs) -> FakeClient:
            client = FakeClient(**kwargs)
            monkeypatch.setattr(records_mod, "get_ayla_booking_client", lambda: client)
            # Undo the module-level stubs the other tests rely on.
            monkeypatch.setattr(visits_mod, "prepare_repeat", records_mod.prepare_repeat)
            monkeypatch.setattr(visits_mod, "list_visits", records_mod.list_visits)
            monkeypatch.setattr(visits_mod, "list_upcoming", records_mod.list_upcoming)
            return client

        _install.intent = _intent  # type: ignore[attr-defined]
        _install.record = _record  # type: ignore[attr-defined]
        return _install

    def test_repeat_reply_names_the_service_and_the_master(self, wired, db) -> None:
        wired(
            intent=wired.intent(2500.0),
            detail=wired.record(),
            edges=[{"price": "2900.00"}],
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert "Массаж спины" in reply.text
        assert "Инна" in reply.text
        assert "ту же услугу" not in reply.text
        # And the price change survives the whole chain.
        assert "2500 ₽" in reply.text
        assert "2900 ₽" in reply.text

    def test_refusal_reply_names_the_master(self, wired, db) -> None:
        from apps.integrations.ayla.booking_client import BookingBadRequestError

        wired(
            intent=wired.intent(),
            detail=wired.record(),
            slots_error=BookingBadRequestError(
                "http_404_NOT_FOUND", status_code=404, code="NOT_FOUND"
            ),
            edges=[],
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert reply.text.startswith("Инна")
        assert "больше не делает" in reply.text

    def test_visits_list_survives_the_whole_chain(self, wired, db) -> None:
        from apps.integrations.ayla.booking_client import AylaBookingPage

        wired(
            pages=[
                AylaBookingPage(records=[wired.record()]),  # upcoming
                AylaBookingPage(records=[wired.record(appointment_id="past")]),  # history
            ]
        )

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        assert "Массаж спины" in reply.text
        assert "12 августа" in reply.text
