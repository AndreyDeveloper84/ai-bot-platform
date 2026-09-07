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
from apps.orchestrator.discovery import CALLBACK_CATALOG_SALONS


def _callbacks(reply) -> list[str]:
    """The callbacks of a reply's keyboard, in render order (DRF-1492)."""
    attachments = (reply.action_data or {}).get("attachments") or []
    return [
        button["callback"]
        for att in attachments
        for button in (att.get("payload") or {}).get("buttons") or []
    ]


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
        # DRF-1492 — the offer used to be «могу подобрать мастера и записать
        # вас», with nothing to press. It is a chip now, and the chip opens
        # the ladder (салоны → услуги → мастер → запись) that ends in a
        # booking. Typing still works and is still invited by the text.
        assert "салон" in reply.text.lower()
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]

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
        """OD-H4 / §18-20 — a graceful alternative, never a system error.

        DRF-1492 — and the alternative is now TAPPABLE. Three of these four
        used to end in a yes/no question («поискать?», «рассказать, что
        есть?») under a message with no buttons: a question whose only
        possible answer is a typed «да» is homework, not an offer.
        """
        capability["repeat"] = RepeatResult(status=status, master_name="Инна")

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert expected in reply.text
        for slug in ("master_unavailable", "service_unavailable", "link_unavailable", "error"):
            assert slug not in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]

    @pytest.mark.parametrize(
        ("status", "expected"),
        [("master_unavailable", "не принимает"), ("link_unavailable", "больше не делает")],
    )
    def test_both_master_refusals_chip_the_service(
        self, capability, db, status: RepeatStatus, expected: str
    ) -> None:
        """Both wordings of «мастер отпал» take the service chip.

        The parametrised sweep above deliberately supplies no
        ``service_name``, so it only ever exercises the fallback branch.
        Without this pair, «больше не делает эту услугу» + чип-услуга would
        have no oracle at all.
        """
        capability["repeat"] = RepeatResult(
            status=status, master_name="Инна", service_name="Массаж спины"
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert expected in reply.text
        assert "Нажмите на услугу" in reply.text
        assert _callbacks(reply) == ["Массаж спины"]

    def test_master_refusal_chips_the_service_that_is_still_fine(self, capability, db) -> None:
        """The master went away, the service did not — so the chip is the
        service, and its tap is the search the sentence promises.

        The callback is the NAME, not an id: the id this layer holds is
        Ayla's canonical ``service_id``, while the catalog chips address
        ``CatalogService.pk`` — a different key space, and a chip built from
        the wrong one would answer «услуга не найдена».
        """
        capability["repeat"] = RepeatResult(
            status="master_unavailable",
            master_name="Инна",
            service_name="Массаж спины",
            entry=RepeatEntry(specialist_id="spec-1", service_id="svc-1"),
        )

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert "Нажмите на услугу" in reply.text
        assert _callbacks(reply) == ["Массаж спины"]
        assert "svc-1" not in str(reply.action_data)

    def test_outage_during_repeat_is_temporary_not_terminal(self, capability, db) -> None:
        capability["repeat"] = RepeatResult(status="backend_unavailable")

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )

        assert "позже" in reply.text.lower()
        assert reply.action_data is None

        # DRF-1492's paired positive, on the SAME capability and the same call
        # path: a refusal that HAS an action behind it does draw a keyboard.
        # Without this line «no buttons» above would also be satisfied by a
        # renderer that had quietly stopped drawing any.
        capability["repeat"] = RepeatResult(status="service_unavailable", master_name="Инна")
        answered = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:repeat:a1"
        )
        assert _callbacks(answered) == [CALLBACK_CATALOG_SALONS]


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


# --------------------------------------------------------------------------- #
# DRF-1547 / §37 п.1 — действия на карточке КОНКРЕТНОЙ записи                   #
# --------------------------------------------------------------------------- #

#: Заведомо будущая дата. Отдельная константа, а не литерал в каждом
#: тесте: «предстоящая запись» решается сравнением с часами, и один
#: забытый год превратил бы половину этих тестов в тесты про прошлое.
_FUTURE = "2099-01-15T09:30:00+00:00"

_UUID_A = "11111111-1111-4111-8111-111111111111"
_UUID_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def miniapp(settings):
    settings.MAX_BOT_WEB_APP = "aylabot"
    settings.MAX_MINIAPP_URL = ""
    return settings


class TestActionsMovedOntoTheBookingCard:
    """Владелец: «так меньше риск отменить не тот визит» (§37 п.1)."""

    def test_each_upcoming_booking_gets_all_three_actions_named_by_service(
        self, capability, db
    ) -> None:
        capability["upcoming"] = VisitsResult(
            status="ok",
            visits=(
                _visit(appointment_id=_UUID_A, service="Маникюр", start=_FUTURE),
                _visit(appointment_id=_UUID_B, service="Массаж спины", start=_FUTURE),
            ),
        )

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert [(b["label"], b["callback"]) for b in buttons] == [
            ("Подробнее: Маникюр", f"cb:visit:card:{_UUID_A}"),
            ("Перенести: Маникюр", f"cb:visit:move:{_UUID_A}"),
            ("Отменить: Маникюр", f"cb:visit:cancel:{_UUID_A}"),
            ("Подробнее: Массаж спины", f"cb:visit:card:{_UUID_B}"),
            ("Перенести: Массаж спины", f"cb:visit:move:{_UUID_B}"),
            ("Отменить: Массаж спины", f"cb:visit:cancel:{_UUID_B}"),
        ]

    def test_the_label_names_the_booking_so_two_cannot_be_confused(self, capability, db) -> None:
        """Довод владельца целиком: без имени услуги выбор был бы вслепую."""
        capability["upcoming"] = VisitsResult(
            status="ok",
            visits=(
                _visit(appointment_id=_UUID_A, service="Маникюр", start=_FUTURE),
                _visit(appointment_id=_UUID_B, service="Массаж спины", start=_FUTURE),
            ),
        )

        reply = visits_mod.route_visits(global_bot_user=_BotUser())
        cancels = [
            b
            for b in reply.action_data["attachments"][0]["payload"]["buttons"]
            if b["callback"].startswith("cb:visit:cancel:")
        ]
        # Стража: кнопок отмены ровно две и подписи у них разные.
        assert len(cancels) == 2
        assert cancels[0]["label"] != cancels[1]["label"]
        assert cancels[0]["callback"] != cancels[1]["callback"]

    def test_past_visits_keep_the_card_button_they_always_had(self, capability, db) -> None:
        """Парная положительная стража DRF-1411: ничего не отнято."""
        capability["upcoming"] = VisitsResult(
            status="ok", visits=(_visit(appointment_id=_UUID_A, start=_FUTURE),)
        )
        capability["visits"] = VisitsResult(status="ok", visits=(_visit(appointment_id="p1"),))

        reply = visits_mod.route_visits(global_bot_user=_BotUser())

        callbacks = _callbacks(reply)
        # Стража: новые действия на месте.
        assert f"cb:visit:cancel:{_UUID_A}" in callbacks
        # И старая карточка визита никуда не делась.
        assert "cb:visit:card:p1" in callbacks

    def test_an_upcoming_card_offers_move_and_cancel_not_repeat(self, capability, db) -> None:
        capability["visit"] = _visit(appointment_id=_UUID_A, start=_FUTURE)

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:card:{_UUID_A}"
        )

        callbacks = _callbacks(reply)
        # Стража: карточка построена и действия на ней есть.
        assert f"cb:visit:move:{_UUID_A}" in callbacks
        assert f"cb:visit:cancel:{_UUID_A}" in callbacks
        # И только теперь отрицание: «Записаться ещё» над несостоявшимся
        # визитом было бы верной фразой про неверный глагол.
        assert f"cb:visit:repeat:{_UUID_A}" not in callbacks

    def test_a_past_card_still_offers_repeat(self, capability, db) -> None:
        """Парная положительная стража к предыдущему тесту."""
        capability["visit"] = _visit(appointment_id="p1")

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:card:p1"
        )

        assert _callbacks(reply) == ["cb:visit:repeat:p1"]


class TestCancelNamesWhatDisappears:
    """Подтверждение, не называющее запись, риска не снимает."""

    def test_the_question_names_the_service_and_the_time(self, capability, db) -> None:
        capability["visit"] = _visit(appointment_id=_UUID_A, service="Маникюр", start=_FUTURE)

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:cancel:{_UUID_A}"
        )

        assert "Маникюр" in reply.text
        assert "15 января" in reply.text
        assert _callbacks(reply) == [
            f"cb:visit:drop:{_UUID_A}",
            f"cb:visit:card:{_UUID_A}",
        ]

    def test_asking_cancels_nothing(self, capability, db, monkeypatch) -> None:
        """Между вопросом и действием не должно быть ни одного вызова."""
        calls: list[str] = []

        def _cancel(*, bot_user, appointment_id):
            calls.append(appointment_id)
            return "ok"

        monkeypatch.setattr("apps.booking.services.records.cancel_booking", _cancel)
        capability["visit"] = _visit(appointment_id=_UUID_A, start=_FUTURE)

        visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:cancel:{_UUID_A}"
        )

        assert calls == []

    def test_confirming_cancels_exactly_the_chosen_booking(
        self, capability, db, monkeypatch
    ) -> None:
        """Главная проверка §37 п.1: отменяется ИМЕННО выбранная."""
        cancelled: list[str] = []

        def _cancel(*, bot_user, appointment_id):
            cancelled.append(appointment_id)
            return "ok"

        monkeypatch.setattr("apps.booking.services.records.cancel_booking", _cancel)
        capability["visit"] = _visit(appointment_id=_UUID_B, service="Массаж спины", start=_FUTURE)

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:drop:{_UUID_B}"
        )

        assert cancelled == [_UUID_B]
        assert "Отменила: Массаж спины" in reply.text

    @pytest.mark.parametrize(
        ("status", "fragment"),
        [
            ("already_gone", "уже нет"),
            ("not_found", "уже нет"),
            ("refused", "оператор"),
            ("backend_unavailable", "не отвечает"),
        ],
    )
    def test_every_refusal_says_what_actually_happened(
        self, capability, db, monkeypatch, status, fragment
    ) -> None:
        """Ни один исход не обещает того, чего не произошло (DRF-1492)."""
        monkeypatch.setattr("apps.booking.services.records.cancel_booking", lambda **_: status)
        capability["visit"] = _visit(appointment_id=_UUID_A, start=_FUTURE)

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:drop:{_UUID_A}"
        )

        # Стража: ответ есть и он про эту запись.
        assert reply.text
        assert fragment in reply.text.lower()
        # И только теперь отрицание: «отменила» не сказано.
        assert "Отменила" not in reply.text


class TestMoveWarnsBeforeOpeningTheSchedule:
    """§37 п.6, формулировка владельца дословно."""

    def test_the_warning_is_the_owner_s_sentence(self, capability, db, miniapp) -> None:
        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:move:{_UUID_A}"
        )

        assert reply.text == "Для выбора времени открою расписание."

    def test_the_button_opens_the_schedule_of_this_very_booking(
        self, capability, db, miniapp
    ) -> None:
        """Человек уже выбрал запись — выбирать её второй раз он не должен."""
        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:move:{_UUID_A}"
        )

        buttons = reply.action_data["buttons"]
        assert len(buttons) == 1
        assert buttons[0]["web_app"] == "aylabot"
        assert buttons[0]["callback"] == f"reschedule_{_UUID_A}"

    def test_the_payload_is_one_max_will_accept(self, capability, db, miniapp) -> None:
        """MAX отвечает 400 на всё, что не подходит под форму, и уносит ВЕСЬ ответ."""
        from apps.channels.max.outbound import OPEN_APP_PAYLOAD_RE

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:move:{_UUID_A}"
        )
        payload = reply.action_data["buttons"][0]["callback"]
        assert OPEN_APP_PAYLOAD_RE.fullmatch(payload), payload

    def test_the_link_fallback_builds_the_declared_route(self, capability, db, settings) -> None:
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://app.example"

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:move:{_UUID_A}"
        )

        assert (
            reply.action_data["buttons"][0]["url"]
            == f"https://app.example/customer/records/{_UUID_A}/reschedule"
        )

    def test_a_forged_id_never_becomes_an_open_app_payload(self, capability, db, miniapp) -> None:
        """Строгая форма проверяется ЗДЕСЬ, а не только в SPA."""
        capability["visit"] = _visit(appointment_id="not-a-uuid", start=_FUTURE)

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text="cb:visit:move:not-a-uuid"
        )

        # Стража: ход не потерян, человек получил ответ и действия карточки.
        assert reply.text
        assert "cb:visit:cancel:not-a-uuid" in _callbacks(reply)
        # И только теперь отрицание: payload'а с битым id никто не строил.
        assert "reschedule_" not in str(reply.action_data)

    def test_without_a_miniapp_move_says_so_and_cancel_still_works(
        self, capability, db, settings
    ) -> None:
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        capability["visit"] = _visit(appointment_id=_UUID_A, start=_FUTURE)

        reply = visits_mod.route_visit_callback(
            global_bot_user=_BotUser(), callback_text=f"cb:visit:move:{_UUID_A}"
        )

        # Стража: отмена — ботовая и осталась доступной.
        assert f"cb:visit:cancel:{_UUID_A}" in _callbacks(reply)
        assert "Отменить запись я могу прямо здесь" in reply.text
