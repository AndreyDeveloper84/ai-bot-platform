# ruff: noqa: F811 — фикстуры берутся по имени из соседних наборов узлов
"""DRF-2876 — от шага сохранённого плана к услуге, времени и записи с экрана.

    POST /customer/plan/step   {action: offers | choose | book, token, index}
    GET  /customer/plan/current → у шага поле ``booked_at``

Тот же путь, что кнопками в чате (ядро общее, состояние разговора общее), —
без сообщений в чат. Здесь настоящие: ход чата Mini App (он даёт вердикт
последнего хода), согласие человека, гейт согласия и основание обработки.
Подменены каталог (план и запись) и связка человека с каталогом.

* p1 — услуги для шага: слова каталога, без идентификаторов услуги и мастера;
* p2 — выбор услуги → времена → запись; каждый вызов каталога несёт вердикт
  ПОСЛЕДНЕГО хода, длительное ограничение и основание из настоящего согласия;
* p3 — хода в чате не было: каталог не спрошен;
* p3b — разговор есть, вердикта нет: каталог не спрошен;
* p4 — после отзыва согласия ни одно действие до каталога не доходит;
* p4b — гейт отвечает раньше разбора тела;
* p5 — чужой или устаревший опознаватель подбора ничего не делает;
* p6 — тело не по форме: 400, каталог не спрошен;
* p7 — флаг выключен: 404;
* p8 — причина каталога приходит экрану своим именем;
* p9 — настоящая услуга не записывается; услуга с расспросом не выбирается;
* p10 — начатое на экране продолжается в чате;
* p11 — повторное нажатие времени шлёт тот же ключ идемпотентности;
* y1–y3 — выбор другого дня: его время, запись на него, день без времени;
* b1–b3 — время действующей записи у шага в чтении плана.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.integrations.ayla import booking_client as booking_mod
from apps.integrations.ayla import plan_engine_client as client_mod
from apps.miniapp_api import views_plan_engine as views
from apps.miniapp_api.tests.test_customer_assistant_2799 import (  # noqa: F401 — fixtures
    _ask,
    _bot_token,
    _concierge,
    _global_threads,
    _init_data_header,
    _no_ayla_link,
    _no_intent_llm,
    _redis,
    tenant,
    wire,
)
from apps.miniapp_api.tests.test_plan_from_chat_e2e_2885 import _person_shell
from apps.miniapp_api.tests.test_plan_step_booking_e2e_2885 import (
    AYLA_USER,
    CANON,
    MASTER,
    OFFER,
    PLAN8,
    PLAN_ID,
    SEARCH8,
    SEARCH_ID,
    SLOT,
    Booking,
    StepCatalog,
)
from apps.orchestrator import plan_gate
from apps.orchestrator import plan_step_card as step
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis

pytestmark = pytest.mark.django_db

#: Счётчик людей: у ручки чата лимит вопросов на человека в минуту.
_PEOPLE = iter(range(2876401, 2876999))

OPTION = {
    "service_name": "Консультация по режиму",
    "salon_name": "Медный ковш",
    "salon_city": "Пенза",
    "master_name": "Анна",
    "price": "2000.00",
    "duration_minutes": 60,
    "place_address": None,
    "synthetic": True,
}


@pytest.fixture(autouse=True)
def catalog(monkeypatch: pytest.MonkeyPatch) -> StepCatalog:
    fake = StepCatalog()
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: fake)
    monkeypatch.setattr(views, "PlanEngineHttpClient", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def booking(monkeypatch: pytest.MonkeyPatch) -> Booking:
    fake = Booking()
    monkeypatch.setattr(booking_mod, "get_ayla_booking_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _world(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    from apps.identity.services import ayla_link

    settings.PLAN_ENGINE_ENABLED = True
    settings.DRE_SHADOW_ENABLED = False
    store = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: store)
    monkeypatch.setattr(ayla_link, "ensure_ayla_link", lambda bot_user, trigger="": AYLA_USER)
    monkeypatch.setattr(step, "_today", lambda: date(2026, 10, 10))


@pytest.fixture
def person(tenant) -> str:
    value = str(next(_PEOPLE))
    _person_shell(tenant, value)
    return value


def _turn(client: Client, person: str) -> None:
    """Любой ход в чате — он и даёт экрану вердикт последнего хода."""
    answer = _ask(client, "мой план", as_user=person)
    assert answer.status_code == 200, answer.content[:300]


def _step(client: Client, person: str, action: Any, token: Any, index: Any):
    return client.post(
        reverse("miniapp_api:customer_plan_step"),
        data=json.dumps({"action": action, "token": token, "index": index}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(person),
    )


def _calls(catalog: StepCatalog, booking: Booking) -> tuple[int, int, int]:
    return (len(catalog.asked), len(catalog.resolved), len(booking.created))


def _withdraw(person: str) -> None:
    from apps.consent.models import ConsentRecord
    from apps.identity.models import BotUser

    ConsentRecord.all_tenants.filter(
        bot_user__in=BotUser.all_tenants.filter(channel="max", channel_user_id=person),
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
    ).update(withdrawn_at=timezone.now())


# ─── путь ────────────────────────────────────────────────────────────────


def test_p1_the_services_for_a_step_come_in_catalog_words_without_ids(
    client: Client, tenant, wire, person: str, catalog: StepCatalog
) -> None:
    _turn(client, person)

    resp = _step(client, person, "offers", PLAN8, 0)

    assert resp.status_code == 200, resp.content[:300]
    assert resp.json() == {"token": SEARCH8, "options": [OPTION]}
    assert (catalog.asked[0]["plan_id"], catalog.asked[0]["step_id"]) == (PLAN_ID, "s-sleep")
    body = resp.content.decode()
    for hidden in (OFFER, CANON, MASTER, SEARCH_ID, PLAN_ID):
        assert hidden not in body


def test_p2_choose_then_book_each_call_with_the_last_turns_verdict_and_the_basis(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    from apps.identity.models import BotUser

    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)

    chosen = _step(client, person, "choose", SEARCH8, 0)
    booked = _step(client, person, "book", SEARCH8, 0)

    assert chosen.status_code == 200, chosen.content[:300]
    assert chosen.json() == {
        "token": SEARCH8,
        "option": OPTION,
        "days": ["2026-10-12"],
        "day": "2026-10-12",
        "slots": [SLOT],
    }
    assert booked.status_code == 200, booked.content[:300]
    assert booked.json() == {"booked_at": SLOT}

    asked, resolved, created = catalog.asked[0], catalog.resolved[0], booking.created[0]
    assert resolved["resolver_decision_id"] == SEARCH_ID
    assert (created["specialist_id"], created["service_id"], created["start_datetime"]) == (
        MASTER,
        OFFER,
        SLOT,
    )
    assert created["client_id"] == AYLA_USER
    assert created["payment_required"] is False
    block = created["provenance"]
    assert (block["entry_point"], block["plan_id"], block["step_id"]) == (
        "PLAN_STEP",
        PLAN_ID,
        "s-sleep",
    )
    # Ход в чате был один — вердикт у всех трёх действий экрана один и тот же.
    shell = BotUser.all_tenants.filter(channel="max", channel_user_id=person).first()
    assert shell is not None
    last = views.last_turn_safety_for(shell)
    assert last is not None
    for sent in (asked, resolved, block):
        assert sent["evaluated_at_revision"] == last.evaluated_at_revision
        assert sent["safety_state"] == last.safety_state == "NORMAL"
        assert sent["s1_restriction"] == "none"
    expected = plan_gate.plan_consent_basis(shell)
    assert expected is not None  # у человека настоящее согласие
    assert asked["consent"] == resolved["consent"] == created["consent"] == expected
    assert "consent" not in block


def test_p2b_the_time_that_was_pressed_is_the_time_that_is_booked(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    later = "2026-10-12T11:30:00+03:00"
    monkeypatch.setattr(
        booking,
        "get_available_times",
        lambda **kwargs: (
            [SimpleNamespace(datetime=SLOT), SimpleNamespace(datetime=later)]
            if kwargs["date"] == "2026-10-12"
            else []
        ),
    )
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    chosen = _step(client, person, "choose", SEARCH8, 0)
    assert chosen.json()["slots"] == [SLOT, later]

    booked = _step(client, person, "book", SEARCH8, 1)

    assert booked.json() == {"booked_at": later}
    assert booking.created[0]["start_datetime"] == later


def test_p3_without_a_chat_turn_the_catalog_is_not_asked(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    resp = _step(client, person, "offers", PLAN8, 0)

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_safety_unavailable"
    assert _calls(catalog, booking) == (0, 0, 0)


def test_p3b_a_conversation_without_a_verdict_does_not_reach_the_catalog(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    """Разговор есть, а вердикта последнего хода нет (истёк, не записан)."""
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    _step(client, person, "choose", SEARCH8, 0)
    before = _calls(catalog, booking)
    assert before == (1, 1, 0)  # с вердиктом путь шёл
    monkeypatch.setattr(views, "last_turn_safety_for", lambda bot_user: None)

    answers = [
        _step(client, person, "offers", PLAN8, 0),
        _step(client, person, "choose", SEARCH8, 0),
        _step(client, person, "book", SEARCH8, 0),
    ]

    assert [(a.status_code, a.json()["error"]) for a in answers] == [
        (409, "plan_safety_unavailable")
    ] * 3
    assert _calls(catalog, booking) == before


def test_p4_after_a_withdrawal_no_action_reaches_the_catalog(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    _step(client, person, "choose", SEARCH8, 0)
    before = _calls(catalog, booking)
    assert before == (1, 1, 0)  # до отзыва путь действительно шёл

    _withdraw(person)
    answers = [
        _step(client, person, "offers", PLAN8, 0),
        _step(client, person, "choose", SEARCH8, 0),
        _step(client, person, "book", SEARCH8, 0),
    ]

    assert [(a.status_code, a.json()["error"]) for a in answers] == [
        (403, "plan_consent_required")
    ] * 3
    assert _calls(catalog, booking) == before


def test_p4b_the_gate_answers_before_the_body_is_read(
    client: Client, tenant, wire, person: str, catalog: StepCatalog
) -> None:
    """Под отзывом ручка не разбирает тело вовсе: отказ гейта, а не «не по форме»."""
    _turn(client, person)
    assert _step(client, person, "drop", PLAN8, 0).status_code == 400  # до отзыва — «не по форме»
    _withdraw(person)

    resp = _step(client, person, "drop", PLAN8, 0)

    assert (resp.status_code, resp.json()["error"]) == (403, "plan_consent_required")


@pytest.mark.parametrize("action", ["choose", "book"])
def test_p5_a_stale_search_token_does_nothing(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, action: str
) -> None:
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    _step(client, person, "choose", SEARCH8, 0)
    before = _calls(catalog, booking)

    resp = _step(client, person, action, "deadbeef", 0)

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_step_expired"
    assert _calls(catalog, booking) == before


@pytest.mark.parametrize(
    ("action", "token", "index"),
    [
        ("drop", PLAN8, 0),
        (None, PLAN8, 0),
        ("offers", PLAN_ID, 0),
        ("offers", "5A5A5A5A", 0),
        ("offers", 12345678, 0),
        ("offers", PLAN8, "0"),
        ("offers", PLAN8, True),
        ("offers", PLAN8, -1),
        ("offers", PLAN8, 100),
        ("offers", PLAN8, None),
    ],
)
def test_p6_a_body_out_of_shape_is_refused_before_the_catalog(
    client: Client,
    tenant,
    wire,
    person: str,
    catalog: StepCatalog,
    booking: Booking,
    action: Any,
    token: Any,
    index: Any,
) -> None:
    _turn(client, person)

    resp = _step(client, person, action, token, index)

    assert resp.status_code == 400
    assert resp.json()["error"] == "malformed"
    assert _calls(catalog, booking) == (0, 0, 0)


def test_p7_with_the_flag_off_the_route_answers_404(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, settings
) -> None:
    _turn(client, person)
    settings.PLAN_ENGINE_ENABLED = False

    resp = _step(client, person, "offers", PLAN8, 0)

    assert resp.status_code == 404
    assert catalog.asked == []


def test_p8_a_reason_of_the_catalog_reaches_the_screen_by_its_own_name(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, monkeypatch
) -> None:
    _turn(client, person)
    monkeypatch.setattr(
        catalog,
        "step_candidates",
        lambda **kwargs: {"candidates": [], "search_id": SEARCH_ID, "nothing_because": "NO_OFFER"},
    )

    resp = _step(client, person, "offers", PLAN8, 0)

    assert resp.status_code == 409
    assert resp.json()["error"] == "no_offer"


def _one_candidate(catalog: StepCatalog, monkeypatch: pytest.MonkeyPatch, **changed: Any) -> None:
    found = StepCatalog().step_candidates()
    found["candidates"][0].update(changed)
    monkeypatch.setattr(catalog, "step_candidates", lambda **kwargs: found)


def test_p9a_a_real_service_is_not_booked_from_a_step(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    _one_candidate(catalog, monkeypatch, synthetic=False)
    _turn(client, person)
    offers = _step(client, person, "offers", PLAN8, 0)
    assert offers.json()["options"][0]["synthetic"] is False
    _step(client, person, "choose", SEARCH8, 0)

    resp = _step(client, person, "book", SEARCH8, 0)

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_step_booking_not_available"
    assert booking.created == []


def test_p9b_a_service_that_needs_a_health_check_is_not_chosen(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    _one_candidate(catalog, monkeypatch, health_check="required")
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)

    resp = _step(client, person, "choose", SEARCH8, 0)

    assert resp.status_code == 409
    assert resp.json()["error"] == "health_check_required"
    assert catalog.resolved == []


def test_p10_what_was_started_on_the_screen_continues_in_the_chat(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)

    answer = _ask(client, f"cb:plan:offer:{SEARCH8}:0", as_user=person)

    assert answer.json()["answer"].endswith("PLAN_STEP_SLOTS · тест")
    assert len(catalog.resolved) == 1


def test_p11_a_second_press_on_the_time_sends_the_same_idempotency_key(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    _step(client, person, "choose", SEARCH8, 0)

    first = _step(client, person, "book", SEARCH8, 0)
    again = _step(client, person, "book", SEARCH8, 0)

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.json() == {"booked_at": SLOT}
    assert len(booking.created) == 2
    assert booking.created[0]["idempotency_key"] == booking.created[1]["idempotency_key"]


# ─── другой день ─────────────────────────────────────────────────────────

LATER_DAY = "2026-10-14"
LATER_SLOT = "2026-10-14T15:00:00+03:00"


def _two_days(booking: Booking, monkeypatch: pytest.MonkeyPatch, later: list[str]) -> None:
    free = {"2026-10-12": [SLOT], LATER_DAY: later}
    monkeypatch.setattr(
        booking,
        "get_available_times",
        lambda **kwargs: [SimpleNamespace(datetime=iso) for iso in free.get(kwargs["date"], [])],
    )


def test_y1_another_day_is_shown_with_its_times_and_then_booked(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    _two_days(booking, monkeypatch, [LATER_SLOT])
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    chosen = _step(client, person, "choose", SEARCH8, 0)
    assert (chosen.json()["days"], chosen.json()["day"]) == (
        ["2026-10-12", LATER_DAY],
        "2026-10-12",
    )

    day = _step(client, person, "day", SEARCH8, 1)
    booked = _step(client, person, "book", SEARCH8, 0)

    assert day.status_code == 200, day.content[:300]
    assert day.json() == {
        "token": SEARCH8,
        "option": OPTION,
        "days": ["2026-10-12", LATER_DAY],
        "day": LATER_DAY,
        "slots": [LATER_SLOT],
    }
    assert booked.json() == {"booked_at": LATER_SLOT}
    assert booking.created[0]["start_datetime"] == LATER_SLOT
    assert len(catalog.resolved) == 1  # выбор услуги не повторялся


def test_y2_a_day_whose_time_is_gone_still_offers_the_other_days(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    _two_days(booking, monkeypatch, [LATER_SLOT])
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    _step(client, person, "choose", SEARCH8, 0)
    _two_days(booking, monkeypatch, [])

    day = _step(client, person, "day", SEARCH8, 1)

    assert day.status_code == 200, day.content[:300]
    assert (day.json()["day"], day.json()["slots"]) == (LATER_DAY, [])
    assert day.json()["days"] == ["2026-10-12", LATER_DAY]


def test_y3_a_day_action_is_gated_and_an_unknown_day_is_stale(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking, monkeypatch
) -> None:
    _two_days(booking, monkeypatch, [LATER_SLOT])
    _turn(client, person)
    _step(client, person, "offers", PLAN8, 0)
    _step(client, person, "choose", SEARCH8, 0)

    unknown = _step(client, person, "day", SEARCH8, 5)
    _withdraw(person)
    gated = _step(client, person, "day", SEARCH8, 1)

    assert (unknown.status_code, unknown.json()["error"]) == (409, "plan_step_expired")
    assert (gated.status_code, gated.json()["error"]) == (403, "plan_consent_required")


# ─── время записи у шага в чтении плана ──────────────────────────────────


def _current(client: Client, person: str):
    return client.get(
        reverse("miniapp_api:customer_plan_current"), HTTP_AUTHORIZATION=_init_data_header(person)
    )


APPOINTMENT = "dd000000-1111-4222-8333-444455556666"


def _state(catalog: StepCatalog) -> dict[str, Any]:
    assert catalog.saved_plan is not None
    return catalog.saved_plan


def _booked(client: Client, person: str) -> list[Any]:
    resp = _current(client, person)
    assert resp.status_code == 200, resp.content[:300]
    return [s["booked_at"] for s in resp.json()["plan"]["steps"]]


def test_b1_a_step_with_a_live_booking_carries_its_time_and_nothing_else_about_it(
    client: Client, tenant, wire, person: str, catalog: StepCatalog
) -> None:
    _state(catalog)["step_state"] = {
        "s-sleep": {
            "tenant_offer_ref": OFFER,
            "bookings": [
                {"appointment_id": APPOINTMENT, "status": "confirmed", "start_datetime": SLOT}
            ],
        },
        "s-walk": {"bookings": []},
    }

    resp = _current(client, person)

    assert [s["booked_at"] for s in resp.json()["plan"]["steps"]] == [SLOT, None]
    assert OFFER not in resp.content.decode()
    assert APPOINTMENT not in resp.content.decode()


@pytest.mark.parametrize("status", ["cancelled", "completed", "no_show", "", "something_new", None])
def test_b2_a_booking_that_is_not_live_is_not_shown_as_one(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, status: Any
) -> None:
    _state(catalog)["step_state"] = {
        "s-sleep": {"bookings": [{"status": "pending", "start_datetime": SLOT}]},
        "s-walk": {"bookings": [{"status": status, "start_datetime": SLOT}]},
    }

    assert _booked(client, person) == [SLOT, None]


def test_b3_of_several_bookings_the_latest_live_one_is_shown(
    client: Client, tenant, wire, person: str, catalog: StepCatalog
) -> None:
    later = "2026-10-19T12:00:00+03:00"
    _state(catalog)["step_state"] = {
        "s-sleep": {
            "bookings": [
                {"status": "confirmed", "start_datetime": SLOT},
                {"status": "awaiting_payment", "start_datetime": later},
                {"status": "cancelled", "start_datetime": "2026-10-20T12:00:00+03:00"},
            ]
        },
        "s-walk": {"bookings": "not a list"},
    }

    assert _booked(client, person) == [later, None]


def test_b4_the_catalogs_utc_time_reaches_the_screen_as_the_hours_of_the_salon(
    client: Client, tenant, wire, person: str, catalog: StepCatalog
) -> None:
    """Экран берёт часы из строки (DRF-2589): 07:00 UTC — это 10:00 в салоне."""
    _state(catalog)["step_state"] = {
        "s-sleep": {
            "bookings": [{"status": "confirmed", "start_datetime": "2026-10-12T07:00:00+00:00"}]
        },
        "s-walk": {"bookings": [{"status": "confirmed", "start_datetime": "послезавтра"}]},
    }

    assert _booked(client, person) == [SLOT, None]


def test_b5_the_hours_are_those_of_the_bookings_own_zone(
    client: Client, tenant, wire, person: str, catalog: StepCatalog
) -> None:
    """Пояс записи — снимок пояса мастера, каталог отдаёт его рядом с временем:
    07:00 UTC — это 12:00 в Екатеринбурге, а не 10:00 по запасному поясу."""
    utc = "2026-10-12T07:00:00+00:00"
    _state(catalog)["step_state"] = {
        "s-sleep": {
            "bookings": [
                {"status": "confirmed", "start_datetime": utc, "timezone": "Asia/Yekaterinburg"}
            ]
        },
        "s-walk": {
            "bookings": [
                {"status": "confirmed", "start_datetime": utc, "timezone": "Europe/Moscow"}
            ]
        },
    }

    assert _booked(client, person) == ["2026-10-12T12:00:00+05:00", SLOT]


@pytest.mark.parametrize("zone", ["", None, "Mars/Olympus", "+05:00", 5, ["Asia/Yekaterinburg"]])
def test_b6_a_zone_that_is_missing_or_unreadable_falls_back_to_the_named_default(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, zone: Any
) -> None:
    utc = "2026-10-12T07:00:00+00:00"
    _state(catalog)["step_state"] = {
        "s-sleep": {"bookings": [{"status": "confirmed", "start_datetime": utc, "timezone": zone}]},
        "s-walk": {"bookings": [{"status": "confirmed", "start_datetime": utc}]},
    }

    assert _booked(client, person) == [SLOT, SLOT]
