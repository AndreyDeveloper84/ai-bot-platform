# ruff: noqa: F811 — фикстуры берутся по имени из соседних наборов узлов
"""DRF-2885 — шаг плана → услуга → время → запись сквозь НАСТОЯЩИЙ ход чата.

Приёмка владельца, действие 5. Узлы обработчика
(``apps/orchestrator/tests/test_plan_step_card_2885.py``) зовут его напрямую;
здесь путь идёт так, как его проходит человек: ручка чата Mini App → ход
глобального бота → ворота безопасности → ревизия хода → разбор нажатия.
Подменены только каталог (план и запись) и связка человека с каталогом.
Согласие человека, гейт согласия и основание обработки — настоящие.

* e1 — «мой план» показывает кнопку на каждый шаг; нажатия ведут до записи,
  и каждое действие несёт вердикт и ревизию СВОЕГО хода;
* e2 — основание обработки, вычисленное из настоящего согласия, едет в
  кандидатах, в выборе услуги и в записи;
* e3 — после отзыва согласия ни одно из трёх нажатий не доходит до каталога;
* e4 — повторное нажатие времени шлёт тот же ключ идемпотентности.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.integrations.ayla import booking_client as booking_mod
from apps.integrations.ayla import plan_engine_client as client_mod
from apps.miniapp_api.tests.test_customer_assistant_2799 import (  # noqa: F401 — fixtures
    _ask,
    _bot_token,
    _concierge,
    _global_threads,
    _no_ayla_link,
    _no_intent_llm,
    _redis,
    tenant,
    wire,
)
from apps.miniapp_api.tests.test_plan_from_chat_e2e_2885 import _person_shell
from apps.orchestrator import plan_gate
from apps.orchestrator import plan_step_card as step
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.tests.test_plan_engine_card_2885 import FakeCatalog

pytestmark = pytest.mark.django_db

PLAN_ID = "5a5a5a5a-1111-4222-8333-999999999999"
PLAN8 = "5a5a5a5a"
SEARCH_ID = "c0ffee00-aaaa-4bbb-8ccc-ddddeeeeffff"
SEARCH8 = "c0ffee00"
OFFER = "0ffe0000-1111-4222-8333-444455556666"
CANON = "ca000000-1111-4222-8333-444455556666"
MASTER = "aa000000-1111-4222-8333-444455556666"
AYLA_USER = "bb000000-1111-4222-8333-444455556666"
SLOT = "2026-10-12T10:00:00+03:00"

#: Счётчик людей: у ручки чата лимит вопросов на человека в минуту.
_PEOPLE = iter(range(2885301, 2885399))


class StepCatalog(FakeCatalog):
    """Каталог плана с сохранённым планом и услугами для его шагов."""

    def __init__(self) -> None:
        super().__init__()
        self.saved_plan = {
            "plan_id": PLAN_ID,
            "status": "active",
            "revision": {
                "steps": [
                    {"step_id": "s-sleep", "capability_ref": "cap.sleep_routine"},
                    {"step_id": "s-walk", "capability_ref": "cap.evening_walk"},
                ]
            },
        }
        self.asked: list[dict[str, Any]] = []
        self.resolved: list[dict[str, Any]] = []

    def step_candidates(self, **kwargs: Any) -> dict[str, Any]:
        self.asked.append(kwargs)
        return {
            "candidates": [
                {
                    "tenant_offer_ref": OFFER,
                    "canonical_service_ref": CANON,
                    "health_check": "not_required",
                    "synthetic": True,
                    "display": {
                        "service_name": "Консультация по режиму",
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
            ],
            "search_id": SEARCH_ID,
            "nothing_because": None,
        }

    def resolve_step(self, **kwargs: Any) -> dict[str, Any]:
        self.resolved.append(kwargs)
        return {"plan": {}, "created": True}


class Booking:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def get_available_times(self, *, specialist_id: str, date: str, service_id: str) -> list[Any]:
        return [SimpleNamespace(datetime=SLOT)] if date == "2026-10-12" else []

    def create_appointment(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        return SimpleNamespace(appointment_id="a-1", raw={})


@pytest.fixture(autouse=True)
def catalog(monkeypatch: pytest.MonkeyPatch) -> StepCatalog:
    fake = StepCatalog()
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: fake)
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
def person(tenant, settings) -> str:
    value = str(next(_PEOPLE))
    # Замок приёмки — настоящий: человек узла назван в списке.
    settings.PLAN_ACCEPTANCE_ACCOUNTS = (f"max:{value}",)
    _person_shell(tenant, value)
    return value


def _labels(answer: Any) -> list[str]:
    return [b["label"] for b in answer.json()["buttons"]]


def _payload(answer: Any, label: str) -> str:
    return next(b["payload"] for b in answer.json()["buttons"] if b["label"] == label)


def _walk_to_booking(client: Client, person: str) -> dict[str, Any]:
    """Пройти путь кнопками, как человек; вернуть ответы каждого хода."""
    plan = _ask(client, "мой план", as_user=person)
    assert plan.status_code == 200, plan.content[:300]
    offers = _ask(client, _payload(plan, "Режим сна"), as_user=person)
    slots = _ask(client, _payload(offers, "Консультация по режиму · Анна"), as_user=person)
    booked = _ask(client, _payload(slots, "12.10 10:00"), as_user=person)
    return {"plan": plan, "offers": offers, "slots": slots, "booked": booked}


def test_e1_from_my_plan_to_a_booking_each_action_with_its_own_turns_verdict(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    seen = _walk_to_booking(client, person)

    # Под планом — кнопка на каждый шаг, затем «Обсудить» и «Меню».
    assert _labels(seen["plan"])[:3] == ["Режим сна", "Вечерняя прогулка", "Обсудить"]
    assert "Консультация по режиму — Медный ковш, Пенза" in seen["offers"].json()["answer"]
    assert "Допущено для теста · синтетические данные" in seen["offers"].json()["answer"]
    assert seen["booked"].json()["answer"].endswith("PLAN_STEP_BOOKED · тест")
    assert "12.10 10:00" in seen["booked"].json()["answer"]

    asked, resolved, created = catalog.asked[0], catalog.resolved[0], booking.created[0]
    assert (asked["plan_id"], asked["step_id"]) == (PLAN_ID, "s-sleep")
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
    # Три действия — три хода: ревизия вердикта растёт от нажатия к нажатию.
    revisions = [
        asked["evaluated_at_revision"],
        resolved["evaluated_at_revision"],
        block["evaluated_at_revision"],
    ]
    assert revisions == sorted(set(revisions)), revisions
    assert {asked["safety_state"], resolved["safety_state"], block["safety_state"]} == {"NORMAL"}
    assert {asked["s1_restriction"], resolved["s1_restriction"], block["s1_restriction"]} == {
        "none"
    }


def test_e2_the_basis_from_the_real_consent_rides_with_every_writing_call(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    from apps.identity.models import BotUser

    _walk_to_booking(client, person)

    shell = BotUser.all_tenants.filter(channel="max", channel_user_id=person).first()
    expected = plan_gate.plan_consent_basis(shell)
    assert expected is not None  # у человека настоящее согласие
    assert catalog.asked[0]["consent"] == expected
    assert catalog.resolved[0]["consent"] == expected
    assert booking.created[0]["consent"] == expected
    assert "consent" not in booking.created[0]["provenance"]


def test_e3_after_a_withdrawal_no_tap_reaches_the_catalog(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    from apps.consent.models import ConsentRecord
    from apps.identity.models import BotUser

    plan = _ask(client, "мой план", as_user=person)
    offers = _ask(client, _payload(plan, "Режим сна"), as_user=person)
    slots = _ask(client, _payload(offers, "Консультация по режиму · Анна"), as_user=person)
    before = (len(catalog.asked), len(catalog.resolved), len(booking.created))
    assert before == (1, 1, 0)  # до отзыва путь действительно шёл

    ConsentRecord.all_tenants.filter(
        bot_user__in=BotUser.all_tenants.filter(channel="max", channel_user_id=person),
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
    ).update(withdrawn_at=timezone.now())

    answers = [
        _ask(client, _payload(plan, "Режим сна"), as_user=person),
        _ask(client, _payload(offers, "Консультация по режиму · Анна"), as_user=person),
        _ask(client, _payload(slots, "12.10 10:00"), as_user=person),
    ]

    assert [a.json()["answer"] for a in answers] == ["PLAN_CONSENT_REQUIRED · тест"] * 3
    assert (len(catalog.asked), len(catalog.resolved), len(booking.created)) == before


def test_e4_a_second_tap_on_the_time_sends_the_same_idempotency_key(
    client: Client, tenant, wire, person: str, catalog: StepCatalog, booking: Booking
) -> None:
    seen = _walk_to_booking(client, person)

    again = _ask(client, _payload(seen["slots"], "12.10 10:00"), as_user=person)

    assert again.json()["answer"].endswith("PLAN_STEP_BOOKED · тест")
    assert len(booking.created) == 2
    assert booking.created[0]["idempotency_key"] == booking.created[1]["idempotency_key"]
    # Вердикт при этом — уже другого хода: повтор не переиспользует прежний.
    assert (
        booking.created[1]["provenance"]["evaluated_at_revision"]
        > booking.created[0]["provenance"]["evaluated_at_revision"]
    )
