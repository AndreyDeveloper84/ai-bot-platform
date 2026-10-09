"""DRF-2885 — шаг плана → услуга → время → запись, в чате.

Приёмка владельца, действие 5: «перейти от разрешённого шага к услуге и
создать тестовую запись». Выбор услуги и времени — нажатие человека на
конкретную кнопку; зеркало каталога в этой ветке не участвует.

Шаг → услуги
* o1 — каталог спрошен о ЭТОМ шаге с четвёркой безопасности и основанием;
* o2 — варианты показаны словами каталога, кнопка на «услугу × мастера»;
* o3 — пометка синтетики — слова владельца; адрес — только если он есть;
* o4 — услуг нет — причина каталога по имени;
* o5 — кнопка от другого плана, шаг вне плана — «устарело», каталог о
  кандидатах не спрошен;
* o6 — без тройки хода каталог не спрошен; o7 — гейт согласия первым;
* o8 — длительное ограничение S1 уходит каталогу как есть; сбой чтения — «стоп».

Услуга → время
* c1 — выбор фиксируется в каталоге с идентификатором подбора; затем время;
* c2 — время ищется по идентификаторам из ответа каталога, не из зеркала;
* c3 — ближайший день со свободным временем, не первый попавшийся пустой;
* c4 — услуге нужен расспрос о здоровье — выбор не фиксируется, записи нет;
* c5 — устаревшая карточка ничего не фиксирует.

Время → запись
* b1 — запись несёт блок шага плана, четвёрку внутри него и основание рядом;
* b2 — синтетическая услуга — без предоплаты; b3 — настоящая — записи нет;
* b4 — повторное нажатие шлёт тот же ключ идемпотентности;
* b5 — отказ каталога показан его именем; b6 — нет связки с каталогом — записи нет.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from apps.integrations.ayla import booking_client as booking_mod
from apps.integrations.ayla import plan_engine_client as client_mod
from apps.orchestrator import plan_gate
from apps.orchestrator import plan_step_card as step
from apps.orchestrator.safety.plan_turn import PlanTurnSafety

PLAN_ID = "5a5a5a5a-1111-4222-8333-999999999999"
PLAN8 = "5a5a5a5a"
SEARCH_ID = "c0ffee00-aaaa-4bbb-8ccc-ddddeeeeffff"
SEARCH8 = "c0ffee00"
OFFER = "0ffe0000-1111-4222-8333-444455556666"
CANON = "ca000000-1111-4222-8333-444455556666"
MASTER = "aa000000-1111-4222-8333-444455556666"
AYLA_USER = "bb000000-1111-4222-8333-444455556666"
BASIS = {
    "type": "personal_data",
    "document_version": "v1",
    "granted_at": "2026-10-09T10:00:00+00:00",
}
SLOT_A = "2026-10-12T10:00:00+03:00"
SLOT_B = "2026-10-12T11:30:00+03:00"


def _candidate(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_offer_ref": OFFER,
        "canonical_service_ref": CANON,
        "health_check": "not_required",
        "synthetic": True,
        "display": {
            "service_name": "Укладка",
            "salon": {"name": "Медный ковш", "city": "Пенза"},
            "masters": [
                {
                    "specialist_ref": MASTER,
                    "name": "Анна",
                    "price": "2000.00",
                    "duration_minutes": 60,
                    "place_address": None,
                }
            ],
        },
    }
    base.update(over)
    return base


class FakePlan:
    def __init__(self) -> None:
        self.plan: dict[str, Any] | None = {
            "plan_id": PLAN_ID,
            "revision": {"steps": [{"step_id": "s-hair"}, {"step_id": "s-hands"}]},
        }
        self.found: dict[str, Any] = {
            "candidates": [_candidate()],
            "search_id": SEARCH_ID,
            "nothing_because": None,
        }
        self.asked: list[dict[str, Any]] = []
        self.resolved: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def get_plan(self, *, external_user_id: str) -> dict[str, Any] | None:
        return self.plan

    def step_candidates(self, **kwargs: Any) -> dict[str, Any]:
        self.asked.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.found

    def resolve_step(self, **kwargs: Any) -> dict[str, Any]:
        self.resolved.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"plan": {}, "created": True}


class FakeBooking:
    def __init__(self) -> None:
        #: день → времена; по умолчанию свободно только 12.10.
        self.days: dict[str, list[str]] = {"2026-10-12": [SLOT_A, SLOT_B]}
        self.slot_calls: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def get_available_times(self, *, specialist_id: str, date: str, service_id: str) -> list[Any]:
        self.slot_calls.append(
            {"specialist_id": specialist_id, "date": date, "service_id": service_id}
        )
        return [SimpleNamespace(datetime=iso) for iso in self.days.get(date, [])]

    def create_appointment(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(appointment_id="a-1", raw={})


@pytest.fixture(autouse=True)
def plan(monkeypatch: pytest.MonkeyPatch) -> FakePlan:
    fake = FakePlan()
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def booking(monkeypatch: pytest.MonkeyPatch) -> FakeBooking:
    fake = FakeBooking()
    monkeypatch.setattr(booking_mod, "get_ayla_booking_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _world(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from apps.identity.services import ayla_link
    from apps.orchestrator.safety import s1_restriction

    settings.PLAN_ENGINE_ENABLED = True
    monkeypatch.setattr(plan_gate, "plan_processing_refusal", lambda bot_user: None)
    monkeypatch.setattr(plan_gate, "plan_consent_basis", lambda bot_user: dict(BASIS))
    monkeypatch.setattr(s1_restriction, "restriction", lambda bot_user: None)
    monkeypatch.setattr(ayla_link, "ensure_ayla_link", lambda bot_user, trigger="": AYLA_USER)
    monkeypatch.setattr(step, "_today", lambda: date(2026, 10, 10))
    monkeypatch.setattr(
        "apps.orchestrator.open_question.write_conversation_state",
        lambda conversation, key, value: (
            conversation.skill_state.pop(key, None)
            if value is None
            else conversation.skill_state.__setitem__(key, value)
        ),
    )


def _bot_user() -> SimpleNamespace:
    return SimpleNamespace(pk=1, id=1, channel="max", channel_user_id="770001", context={})


def _conversation() -> SimpleNamespace:
    return SimpleNamespace(id="conv-step", skill_state={}, bot_user=_bot_user())


def _safety(state: str = "NORMAL", revision: int = 7) -> PlanTurnSafety:
    return PlanTurnSafety(
        safety_state=state, safety_policy_version="pre_check-abc", evaluated_at_revision=revision
    )


def _tap(text: str, conversation: Any, *, safety: Any = "default") -> Any:
    provider = (lambda: _safety()) if safety == "default" else (lambda: safety)
    return step.try_handle_plan_step(
        text=text,
        bot_user=conversation.bot_user,
        conversation=conversation,
        trace_id="t",
        turn_safety=provider,
    )


STEP0 = f"cb:plan:step:{PLAN8}:0"
OFFER0 = f"cb:plan:offer:{SEARCH8}:0"
SLOT0 = f"cb:plan:slot:{SEARCH8}:0"


def _buttons(result: Any) -> list[dict[str, str]]:
    return result.action_data["attachments"][0]["payload"]["buttons"]


def _at_offers() -> SimpleNamespace:
    conversation = _conversation()
    assert _tap(STEP0, conversation).meta["plan_outcome"] == "PLAN_STEP_OFFERS"
    return conversation


def _at_slots() -> SimpleNamespace:
    conversation = _at_offers()
    assert _tap(OFFER0, conversation).meta["plan_outcome"] == "PLAN_STEP_SLOTS"
    return conversation


# ─── шаг → услуги ────────────────────────────────────────────────────────


class TestFromAStepToItsServices:
    def test_o1_the_catalog_is_asked_about_this_step_with_the_four_and_the_basis(
        self, plan: FakePlan
    ) -> None:
        _tap(f"cb:plan:step:{PLAN8}:1", _conversation(), safety=_safety("NORMAL", 12))

        asked = plan.asked[0]
        assert (asked["plan_id"], asked["step_id"]) == (PLAN_ID, "s-hands")
        assert asked["safety_state"] == "NORMAL"
        assert asked["safety_policy_version"] == "pre_check-abc"
        assert asked["evaluated_at_revision"] == 12
        assert asked["s1_restriction"] == "none"
        assert asked["consent"] == BASIS

    def test_o2_options_are_shown_in_the_catalogs_words_one_button_each(
        self, plan: FakePlan
    ) -> None:
        plan.found["candidates"][0]["display"]["masters"].append(
            {
                "specialist_ref": "ab" + MASTER[2:],
                "name": "Вера",
                "price": "2500.00",
                "duration_minutes": 90,
            }
        )

        result = _tap(STEP0, _conversation())

        assert "Укладка — Медный ковш, Пенза" in result.reply_text
        assert "Анна · 2000.00 ₽ · 60 мин" in result.reply_text
        assert "Вера · 2500.00 ₽ · 90 мин" in result.reply_text
        assert _buttons(result) == [
            {"label": "Укладка · Анна", "callback": f"cb:plan:offer:{SEARCH8}:0"},
            {"label": "Укладка · Вера", "callback": f"cb:plan:offer:{SEARCH8}:1"},
        ]

    def test_o3_the_synthetic_mark_is_the_owners_and_the_address_only_when_given(
        self, plan: FakePlan
    ) -> None:
        with_mark = _tap(STEP0, _conversation()).reply_text
        assert "Допущено для теста · синтетические данные" in with_mark
        assert "Адрес" not in with_mark

        plan.found["candidates"] = [_candidate(synthetic=False)]
        plan.found["candidates"][0]["display"]["masters"][0]["place_address"] = "ул. Мира, 1"
        real = _tap(STEP0, _conversation()).reply_text

        assert "ул. Мира, 1" in real
        assert "синтетические" not in real

    @pytest.mark.parametrize("reason", ["NO_OFFER", "NOT_ADMITTED", "ACROSS_THE_TEST_BOUNDARY"])
    def test_o4_no_services_is_the_catalogs_reason_by_name(
        self, plan: FakePlan, reason: str
    ) -> None:
        plan.found = {"candidates": [], "search_id": SEARCH_ID, "nothing_because": reason}
        conversation = _conversation()

        result = _tap(STEP0, conversation)

        assert result.reply_text == f"{reason} · тест"
        assert step.STATE_KEY not in conversation.skill_state

    @pytest.mark.parametrize("tap", ["cb:plan:step:ffffffff:0", f"cb:plan:step:{PLAN8}:7"])
    def test_o5_a_button_of_another_plan_or_a_step_outside_it_is_stale(
        self, plan: FakePlan, tap: str
    ) -> None:
        result = _tap(tap, _conversation())

        assert result.reply_text == "PLAN_STEP_EXPIRED · тест"
        assert plan.asked == []

    def test_o6_no_turn_triple_no_request(self, plan: FakePlan) -> None:
        result = _tap(STEP0, _conversation(), safety=None)

        assert result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"
        assert plan.asked == []

    @pytest.mark.parametrize("tap", [STEP0, OFFER0, SLOT0])
    def test_o7_the_consent_gate_comes_first_on_every_door(
        self, plan: FakePlan, booking: FakeBooking, monkeypatch: pytest.MonkeyPatch, tap: str
    ) -> None:
        conversation = _at_slots()
        before = (len(plan.asked), len(plan.resolved), len(booking.created))
        monkeypatch.setattr(
            plan_gate, "plan_processing_refusal", lambda bot_user: "PLAN_CONSENT_REQUIRED"
        )

        result = _tap(tap, conversation)

        assert result.reply_text == "PLAN_CONSENT_REQUIRED · тест"
        assert (len(plan.asked), len(plan.resolved), len(booking.created)) == before

    @pytest.mark.parametrize(("status", "sent"), [("open", "open"), ("stop", "stop")])
    def test_o8_the_long_lived_restriction_goes_to_the_catalog_as_it_is(
        self, plan: FakePlan, monkeypatch: pytest.MonkeyPatch, status: str, sent: str
    ) -> None:
        from apps.orchestrator.safety import s1_restriction

        monkeypatch.setattr(
            s1_restriction, "restriction", lambda bot_user: SimpleNamespace(status=status)
        )

        _tap(STEP0, _conversation())

        assert plan.asked[0]["s1_restriction"] == sent

    def test_o8_a_failed_read_of_the_restriction_is_sent_as_stop(
        self, plan: FakePlan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.orchestrator.safety import s1_restriction

        def _boom(bot_user: Any) -> Any:
            raise RuntimeError("context unreadable")

        monkeypatch.setattr(s1_restriction, "restriction", _boom)

        _tap(STEP0, _conversation())

        assert plan.asked[0]["s1_restriction"] == "stop"

    def test_o9_a_catalog_refusal_keeps_its_reason(self, plan: FakePlan) -> None:
        plan.error = client_mod.PlanStepNotExecutableError("s1_restriction_stop")

        result = _tap(STEP0, _conversation())

        assert result.reply_text == "PLAN_STEP_NOT_EXECUTABLE:s1_restriction_stop · тест"

    def test_o10_the_engine_switched_off_is_not_answered(self, plan: FakePlan, settings) -> None:
        settings.PLAN_ENGINE_ENABLED = False

        assert _tap(STEP0, _conversation()) is None
        assert plan.asked == []

    def test_o11_an_option_that_cannot_be_named_or_booked_is_not_offered(
        self, plan: FakePlan
    ) -> None:
        nameless = _candidate()
        nameless["display"]["masters"][0]["name"] = ""
        plan.found["candidates"] = [nameless, _candidate(tenant_offer_ref=None)]

        result = _tap(STEP0, _conversation())

        assert result.reply_text == "PLAN_STEP_OFFER_UNSHOWABLE · тест"


# ─── услуга → время ──────────────────────────────────────────────────────


class TestFromAServiceToATime:
    def test_c1_the_choice_is_fixed_in_the_catalog_with_the_search_id(self, plan: FakePlan) -> None:
        conversation = _at_offers()

        _tap(OFFER0, conversation, safety=_safety("NORMAL", 13))

        sent = plan.resolved[0]
        assert (sent["plan_id"], sent["step_id"]) == (PLAN_ID, "s-hair")
        assert (sent["canonical_service_ref"], sent["tenant_offer_ref"]) == (CANON, OFFER)
        assert sent["resolver_decision_id"] == SEARCH_ID
        assert (sent["evaluated_at_revision"], sent["s1_restriction"]) == (13, "none")
        assert sent["consent"] == BASIS

    def test_c2_times_are_read_by_the_catalogs_ids(self, booking: FakeBooking) -> None:
        _at_slots()

        assert {(c["specialist_id"], c["service_id"]) for c in booking.slot_calls} == {
            (MASTER, OFFER)
        }

    def test_c3_the_nearest_day_with_free_time_is_shown(self, booking: FakeBooking) -> None:
        conversation = _at_offers()

        result = _tap(OFFER0, conversation)

        # 10 и 11 октября пусты — показан первый день, где время есть.
        assert [c["date"] for c in booking.slot_calls] == ["2026-10-10", "2026-10-11", "2026-10-12"]
        assert _buttons(result) == [
            {"label": "12.10 10:00", "callback": f"cb:plan:slot:{SEARCH8}:0"},
            {"label": "12.10 11:30", "callback": f"cb:plan:slot:{SEARCH8}:1"},
        ]

    def test_c3_no_free_time_in_the_horizon_is_said_by_name(self, booking: FakeBooking) -> None:
        booking.days = {}
        conversation = _at_offers()

        result = _tap(OFFER0, conversation)

        assert result.reply_text == "PLAN_STEP_NO_SLOTS · тест"
        assert len(booking.slot_calls) == step.SLOT_HORIZON_DAYS

    @pytest.mark.parametrize("health", ["required", None, "что-то новое"])
    def test_c4_a_service_that_needs_a_health_check_is_neither_fixed_nor_booked(
        self, plan: FakePlan, booking: FakeBooking, health: Any
    ) -> None:
        plan.found["candidates"] = [_candidate(health_check=health)]
        conversation = _at_offers()

        result = _tap(OFFER0, conversation)

        assert result.reply_text == "HEALTH_CHECK_REQUIRED · тест"
        assert plan.resolved == []
        assert booking.slot_calls == []

    @pytest.mark.parametrize("tap", ["cb:plan:offer:ffffffff:0", f"cb:plan:offer:{SEARCH8}:5"])
    def test_c5_a_stale_card_fixes_nothing(self, plan: FakePlan, tap: str) -> None:
        conversation = _at_offers()

        result = _tap(tap, conversation)

        assert result.reply_text == "PLAN_STEP_EXPIRED · тест"
        assert plan.resolved == []

    def test_c6_a_refused_choice_shows_no_times(self, plan: FakePlan, booking: FakeBooking) -> None:
        conversation = _at_offers()
        plan.error = client_mod.PlanStepResolutionRefusedError("offer_not_a_candidate")

        result = _tap(OFFER0, conversation)

        assert result.reply_text == "PLAN_STEP_RESOLUTION_REFUSED:offer_not_a_candidate · тест"
        assert booking.slot_calls == []


# ─── время → запись ──────────────────────────────────────────────────────


class TestFromATimeToABooking:
    def test_b1_the_booking_carries_the_step_block_the_four_inside_and_the_basis_beside(
        self, booking: FakeBooking
    ) -> None:
        conversation = _at_slots()

        result = _tap(SLOT0, conversation, safety=_safety("NORMAL", 15))

        assert result.meta["plan_outcome"] == "PLAN_STEP_BOOKED"
        sent = booking.created[0]
        assert (sent["client_id"], sent["specialist_id"], sent["service_id"]) == (
            AYLA_USER,
            MASTER,
            OFFER,
        )
        assert sent["start_datetime"] == SLOT_A
        assert sent["provenance"] == {
            "entry_point": "PLAN_STEP",
            "plan_id": PLAN_ID,
            "step_id": "s-hair",
            "safety_state": "NORMAL",
            "safety_policy_version": "pre_check-abc",
            "evaluated_at_revision": 15,
            "s1_restriction": "none",
        }
        assert sent["consent"] == BASIS
        assert "consent" not in sent["provenance"]
        assert (sent["quoted_price"], sent["quoted_duration_minutes"]) == ("2000.00", 60)

    def test_b1_the_confirmation_is_the_catalogs_words_and_the_chosen_time(
        self, booking: FakeBooking
    ) -> None:
        conversation = _at_slots()

        result = _tap(f"cb:plan:slot:{SEARCH8}:1", conversation)

        assert "Укладка — Медный ковш, Пенза" in result.reply_text
        assert "12.10 11:30" in result.reply_text
        assert booking.created[0]["start_datetime"] == SLOT_B

    def test_b2_a_synthetic_service_is_booked_without_a_prepayment(
        self, booking: FakeBooking
    ) -> None:
        _tap(SLOT0, _at_slots())

        assert booking.created[0]["payment_required"] is False

    def test_b3_a_real_service_is_not_booked_from_a_step(
        self, plan: FakePlan, booking: FakeBooking
    ) -> None:
        plan.found["candidates"] = [_candidate(synthetic=False)]
        conversation = _at_slots()

        result = _tap(SLOT0, conversation)

        assert result.reply_text == "PLAN_STEP_BOOKING_NOT_AVAILABLE · тест"
        assert booking.created == []

    def test_b4_a_second_tap_sends_the_same_idempotency_key(self, booking: FakeBooking) -> None:
        conversation = _at_slots()

        _tap(SLOT0, conversation, safety=_safety("NORMAL", 15))
        _tap(SLOT0, conversation, safety=_safety("NORMAL", 16))
        _tap(f"cb:plan:slot:{SEARCH8}:1", conversation, safety=_safety("NORMAL", 17))

        keys = [c["idempotency_key"] for c in booking.created]
        assert keys[0] == keys[1]
        assert keys[2] != keys[0]

    @pytest.mark.parametrize(
        ("code", "details", "shown"),
        [
            ("QUOTE_CHANGED", None, "QUOTE_CHANGED"),
            (
                "PLAN_STEP_NOT_EXECUTABLE",
                {"reason": "s1_restriction_open"},
                "PLAN_STEP_NOT_EXECUTABLE:s1_restriction_open",
            ),
            ("HEALTH_CHECK_REQUIRED", None, "HEALTH_CHECK_REQUIRED"),
            (None, None, "PLAN_STEP_BOOKING_FAILED"),
        ],
    )
    def test_b5_a_catalog_refusal_is_shown_by_its_own_name(
        self, booking: FakeBooking, code: Any, details: Any, shown: str
    ) -> None:
        conversation = _at_slots()
        booking.error = booking_mod.BookingBadRequestError(
            "refused", status_code=409, code=code, details=details
        )

        result = _tap(SLOT0, conversation)

        assert result.reply_text == f"{shown} · тест"

    def test_b6_without_a_link_to_the_catalog_person_nothing_is_booked(
        self, booking: FakeBooking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.identity.services import ayla_link

        conversation = _at_slots()
        monkeypatch.setattr(ayla_link, "ensure_ayla_link", lambda bot_user, trigger="": None)

        result = _tap(SLOT0, conversation)

        assert result.reply_text == "AYLA_LINK_UNAVAILABLE · тест"
        assert booking.created == []

    @pytest.mark.parametrize("tap", ["cb:plan:slot:ffffffff:0", f"cb:plan:slot:{SEARCH8}:9"])
    def test_b7_a_stale_time_books_nothing(self, booking: FakeBooking, tap: str) -> None:
        conversation = _at_slots()

        result = _tap(tap, conversation)

        assert result.reply_text == "PLAN_STEP_EXPIRED · тест"
        assert booking.created == []

    def test_b8_a_time_tap_before_a_service_is_chosen_books_nothing(
        self, booking: FakeBooking
    ) -> None:
        conversation = _at_offers()

        result = _tap(SLOT0, conversation)

        assert result.reply_text == "PLAN_STEP_EXPIRED · тест"
        assert booking.created == []

    @pytest.mark.parametrize(
        ("code", "shown"),
        [
            ("DELETION_IN_PROGRESS", "PLAN_DELETION_REQUESTED"),
            ("CONSENT_REQUIRED", "PLAN_CONSENT_REQUIRED"),
        ],
    )
    def test_b9_a_refusal_on_the_basis_of_processing_has_the_gates_name(
        self, booking: FakeBooking, code: str, shown: str
    ) -> None:
        """Одна причина для человека — одно имя: то же, что у гейта плана."""
        conversation = _at_slots()
        booking.error = booking_mod.BookingBadRequestError(
            "refused", status_code=423, code=code, details={"reason": "withdrawn"}
        )

        result = _tap(SLOT0, conversation)

        assert result.reply_text == f"{shown} · тест"
        assert plan_gate.PLAN_DELETION_REQUESTED == "PLAN_DELETION_REQUESTED"
        assert plan_gate.PLAN_CONSENT_REQUIRED == "PLAN_CONSENT_REQUIRED"

    @pytest.mark.parametrize("tap", [OFFER0, SLOT0])
    def test_b10_a_tap_with_no_state_at_all_is_stale_and_asks_no_one(
        self, plan: FakePlan, booking: FakeBooking, tap: str
    ) -> None:
        """Состояние стёрто (например, отзывом согласия) — старая кнопка
        услуги или времени ничего не делает."""
        conversation = _at_slots()
        before = (len(plan.resolved), len(booking.slot_calls), len(booking.created))
        conversation.skill_state.pop(step.STATE_KEY)

        result = _tap(tap, conversation)

        assert result.reply_text == "PLAN_STEP_EXPIRED · тест"
        assert (len(plan.resolved), len(booking.slot_calls), len(booking.created)) == before


# ─── кнопки под планом и семейство нажатий ───────────────────────────────


def test_k1_step_buttons_carry_the_catalogs_labels_and_the_plan_token() -> None:
    plan = {
        "plan_id": PLAN_ID,
        "revision": {
            "steps": [
                {"step_id": "s-hair", "capability_ref": "cap.hair"},
                {"step_id": "s-x", "capability_ref": "cap.unlabelled"},
                {"step_id": "s-hands", "capability_ref": "cap.hands"},
            ]
        },
    }

    buttons = step.step_buttons(plan, {"cap.hair": "Причёска", "cap.hands": "Руки"})

    # Номер в кнопке — место шага в плане, а не в списке кнопок.
    assert buttons == [
        {"label": "Причёска", "callback": f"cb:plan:step:{PLAN8}:0"},
        {"label": "Руки", "callback": f"cb:plan:step:{PLAN8}:2"},
    ]


def test_k2_the_taps_belong_to_the_plan_family_and_leave_no_words_in_history() -> None:
    from apps.orchestrator.plan_lite_card import is_plan_callback, tap_history_text

    for tap in (STEP0, OFFER0, SLOT0):
        assert is_plan_callback(tap) is True
        assert tap_history_text(tap) is None
