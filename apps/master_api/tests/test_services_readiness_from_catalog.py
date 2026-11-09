"""Пункт «Услуги» готовности отвечает по правилу каталога — как пункт «Место работы».

Живой прогон регистрации соло-мастера на пилоте (09.11.2026): услуга выбрана,
цена и длительность заданы, каталог отвечает ``READY`` и отправку профиля
принимает — а чек-лист бота держит ``services: missing`` и после синхронизации
зеркала. ``ready`` ложно, и кнопку «Отправить профиль на проверку» (экран 01
включает её по ``ready``; другого входа на экран отправки нет) мастер не видит.

Причина — два определения «настроено». Каталог: активная услуга + активное
предложение мастера с длительностью (``users/publication.py``). Зеркало: цена и
длительность на строке ``CatalogService`` — а у соло-мастера цена живёт на
предложении, и эти поля услуги пусты. На пилоте так во всех соло-кабинетах.

Узлы:

* форма пилота (в зеркале услуга без цены и длительности, каталог говорит
  «настроено») → ``done``; пара — тот же мастер без субъекта → прежнее ``missing``;
* каталог впереди зеркала (зеркало ещё пусто) → ``done``, без ожидания синхронизации;
* каталог сказал «не настроено» → ``missing``, даже если зеркало считает иначе;
* предложение дешевле 1 ₽ настроенным не считается (DRF-1989 остаётся);
* каталог молчит или отказывает (не тот токен, субъект, устаревший id) →
  ``unknown``, не ``missing``; положительный факт зеркала остаётся;
* отказ «выбор услуг не для этого кабинета» (409 ``SERVICE_SELECTION_REFUSED``)
  или ответ не той формы → правило зеркала, как раньше;
* у строки зеркала нет id каталога → ``unknown``, каталог не спрошен;
* каталог спрошен под этим мастером и этим субъектом; без субъекта не спрошен;
* вся проекция с формой пилота → ``ready`` истинно; ручка отдаёт счётчики каталога.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.services.onboarding_readiness import ReadinessItem, build_readiness
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

CLIENT = "apps.master_api.services.onboarding_readiness.get_ayla_booking_client"
ACTOR = "bot:max:12345"


def _mirror_service(
    master: CatalogMaster, *, ext: int, price: Decimal | None, duration: int | None
) -> None:
    """Строка зеркала. ``price=None, duration=None`` — так она выглядит у соло на пилоте."""
    service = CatalogService.all_tenants.create(
        tenant=master.tenant,
        external_id=ext,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug=f"selection-{ext}",
        name=f"Услуга {ext}",
        duration_min=duration,
        price_from=price,
        is_active=True,
    )
    MasterService.all_tenants.create(tenant=master.tenant, master=master, service=service)


def _row(*, configured: bool, price: str | None = "1500.00") -> dict[str, Any]:
    offer = None if price is None else {"id": "o", "price": price, "duration_minutes": 60}
    return {"salon_service_id": "s", "is_active": True, "offer": offer, "configured": configured}


def _selection(*rows: dict[str, Any]) -> dict[str, Any]:
    """Ответ ``services/selection`` каталога: счётчики — его, не посчитанные здесь."""
    return {
        "specialist_id": "sp",
        "selected": len(rows),
        "configured": sum(1 for r in rows if r["configured"]),
        "services": list(rows),
    }


def _catalog(*, selection: Any = None, error: Exception | None = None) -> MagicMock:
    fake = MagicMock()
    fake.get_service_locations.return_value = {"places": [], "areas": []}
    if error is not None:
        fake.get_service_selection.side_effect = error
    else:
        fake.get_service_selection.return_value = selection
    return fake


def _services(
    master: CatalogMaster, fake: MagicMock, *, actor: str | None = ACTOR
) -> ReadinessItem:
    with patch(CLIENT, return_value=fake):
        readiness = build_readiness(master, actor=actor)
    return next(item for item in readiness.items if item.key == "services")


# ─── форма пилота: каталог «настроено», зеркало без цены ─────────────────────


def test_a_solo_offer_configured_in_the_catalog_closes_services(accepted_master):
    """Красный до правки: ``missing`` при настроенной в каталоге услуге."""
    _mirror_service(accepted_master, ext=9101, price=None, duration=None)

    item = _services(accepted_master, _catalog(selection=_selection(_row(configured=True))))

    assert item.state == "done"
    assert item.reason is None
    assert item.detail == {"selected": 1, "configured": 1}


def test_the_same_master_without_a_subject_still_reads_the_mirror(accepted_master):
    """Пара: без субъекта каталог не спросить — ответ прежний, по зеркалу."""
    _mirror_service(accepted_master, ext=9102, price=None, duration=None)
    fake = _catalog(selection=_selection(_row(configured=True)))

    item = _services(accepted_master, fake, actor=None)

    assert item.state == "missing"
    assert item.detail == {"selected": 1, "configured": 0}
    fake.get_service_selection.assert_not_called()


def test_the_catalog_is_ahead_of_the_mirror(accepted_master):
    """Услугу только что настроили: зеркало ещё пусто, ждать синхронизацию не нужно."""
    item = _services(accepted_master, _catalog(selection=_selection(_row(configured=True))))

    assert item.state == "done"
    assert item.detail == {"selected": 1, "configured": 1}


# ─── каталог говорит «нет» ───────────────────────────────────────────────────


def test_selected_but_not_configured_is_missing_and_leads_to_prices(accepted_master):
    item = _services(
        accepted_master, _catalog(selection=_selection(_row(configured=False, price=None)))
    )

    assert item.state == "missing"
    assert item.reason is None
    assert item.detail == {"selected": 1, "configured": 0}
    assert item.deep_link == "/solo/services"


def test_nothing_selected_is_missing_and_leads_to_directions(accepted_master):
    item = _services(accepted_master, _catalog(selection=_selection()))

    assert item.state == "missing"
    assert item.detail == {"selected": 0, "configured": 0}
    assert item.deep_link == "/solo/directions"


def test_the_catalogs_no_wins_over_a_stale_mirror(accepted_master):
    """Услугу убрали: зеркало ещё считает её настроенной, а отправку каталог не примет."""
    _mirror_service(accepted_master, ext=9103, price=Decimal("1500"), duration=60)

    item = _services(accepted_master, _catalog(selection=_selection()))

    assert item.state == "missing"
    assert item.detail == {"selected": 0, "configured": 0}


def test_an_offer_below_one_rouble_is_not_configured(accepted_master):
    """DRF-1989 остаётся: каталог считает такое предложение настроенным, продажа — нет."""
    item = _services(
        accepted_master, _catalog(selection=_selection(_row(configured=True, price="0.50")))
    )

    assert item.state == "missing"
    assert item.reason == "offer_not_sellable"
    assert item.detail == {"selected": 1, "configured": 0}


def test_one_priced_offer_is_enough_next_to_an_unpriced_one(accepted_master):
    item = _services(
        accepted_master,
        _catalog(
            selection=_selection(
                _row(configured=True, price="0.50"), _row(configured=True, price="900.00")
            )
        ),
    )

    assert item.state == "done"
    assert item.detail == {"selected": 2, "configured": 1}


# ─── каталог не ответил на вопрос ────────────────────────────────────────────


def test_a_silent_catalog_is_unknown_not_missing(accepted_master):
    _mirror_service(accepted_master, ext=9104, price=None, duration=None)

    item = _services(accepted_master, _catalog(error=BookingUnavailableError("down")))

    assert item.state == "unknown"
    assert item.reason == "BookingUnavailableError"
    assert item.detail == {"selected": 1, "configured": 0}


def test_a_silent_catalog_does_not_cancel_what_the_mirror_knows(accepted_master):
    _mirror_service(accepted_master, ext=9105, price=Decimal("1500"), duration=60)

    item = _services(accepted_master, _catalog(error=BookingUnavailableError("down")))

    assert item.state == "done"
    assert item.detail == {"selected": 1, "configured": 1}


@pytest.mark.parametrize(
    ("price", "duration", "expected"),
    [(Decimal("1500"), 60, "done"), (None, None, "missing")],
    ids=["mirror-configured", "mirror-not-configured"],
)
def test_selection_not_for_this_workspace_leaves_the_mirror_rule(
    accepted_master, price, duration, expected
):
    """Выбор услуг — только у соло; кабинету салона каталог отвечает 409. Это не «неизвестно»."""
    _mirror_service(accepted_master, ext=9106, price=price, duration=duration)
    refusal = BookingBadRequestError(
        "refused",
        status_code=409,
        code="SERVICE_SELECTION_REFUSED",
        details={"reason": "salon_catalog_owner_managed"},
    )

    item = _services(accepted_master, _catalog(error=refusal))

    assert item.state == expected
    assert item.reason is None


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (404, "NOT_FOUND"), (400, None)],
    ids=["wrong-token", "subject-not-allowed", "stale-id", "no-code"],
)
def test_any_other_refusal_is_unknown_not_missing(accepted_master, status_code, code):
    """Иначе соло-мастер увидел бы «услуги не настроены» из-за сбоя доступа."""
    _mirror_service(accepted_master, ext=9110, price=None, duration=None)
    refusal = BookingBadRequestError("no", status_code=status_code, code=code)

    item = _services(accepted_master, _catalog(error=refusal))

    assert item.state == "unknown"
    assert item.reason == "BookingBadRequestError"
    assert item.detail == {"selected": 1, "configured": 0}


def test_any_other_refusal_does_not_cancel_what_the_mirror_knows(accepted_master):
    _mirror_service(accepted_master, ext=9111, price=Decimal("1500"), duration=60)
    refusal = BookingBadRequestError("no", status_code=403, code="FORBIDDEN")

    item = _services(accepted_master, _catalog(error=refusal))

    assert item.state == "done"
    assert item.detail == {"selected": 1, "configured": 1}


def test_a_master_without_a_catalog_id_is_unknown_and_the_catalog_is_not_asked(accepted_master):
    """Звать каталог не с чем — это «не спросить», а не «услуг нет»."""
    accepted_master.catalog_specialist_id = None
    fake = _catalog(selection=_selection(_row(configured=True)))

    item = _services(accepted_master, fake)

    assert item.state == "unknown"
    assert item.reason == "CatalogSpecialistUnresolved"
    fake.get_service_selection.assert_not_called()


@pytest.mark.parametrize(
    "answer",
    [
        None,
        [],
        {"services": []},
        {"selected": "1", "configured": 1},
        {"selected": True, "configured": 0},
        {"selected": -1, "configured": 0},
    ],
    ids=["none", "list", "no-counters", "text-counter", "bool-counter", "negative-counter"],
)
def test_an_answer_of_the_wrong_shape_leaves_the_mirror_rule(accepted_master, answer):
    _mirror_service(accepted_master, ext=9107, price=Decimal("1500"), duration=60)

    item = _services(accepted_master, _catalog(selection=answer))

    assert item.state == "done"
    assert item.detail == {"selected": 1, "configured": 1}


def test_an_unconfigured_client_leaves_the_mirror_rule(accepted_master, settings):
    settings.AYLA_BASE_URL = ""
    _mirror_service(accepted_master, ext=9108, price=Decimal("1500"), duration=60)

    readiness = build_readiness(accepted_master, actor=ACTOR)
    item = next(i for i in readiness.items if i.key == "services")

    assert item.state == "done"


# ─── кого и под кем спрашиваем ───────────────────────────────────────────────


def test_the_catalog_is_asked_as_this_master(accepted_master):
    from apps.catalog.specialist_ref import catalog_specialist_id

    fake = _catalog(selection=_selection(_row(configured=True)))

    _services(accepted_master, fake)

    fake.get_service_selection.assert_called_once_with(
        specialist_id=catalog_specialist_id(accepted_master), external_user_id=ACTOR
    )


# ─── всё вместе: состояние пилота наконец «готово» ───────────────────────────


def test_the_pilot_shape_is_ready_end_to_end(tenant, accepted_master, settings):
    """Красный до правки: ``blocking == ["services:missing"]`` — и кнопки отправки нет."""
    from apps.scheduling.models import WorkingHours

    settings.BOOKING_VIA_AYLA_REST = False
    _mirror_service(accepted_master, ext=9109, price=None, duration=None)
    WorkingHours.all_tenants.create(
        tenant=tenant,
        master=accepted_master,
        day_of_week=2,
        is_working=True,
        start_time="10:00",
        end_time="19:00",
    )
    accepted_master.photo_url = "/media/master_photos/y.jpg"
    accepted_master.save(update_fields=["photo_url"])
    fake = _catalog(selection=_selection(_row(configured=True)))
    fake.get_service_locations.return_value = {
        "places": [{"id": "p", "status": "review_required"}],
        "areas": [],
    }

    with patch(CLIENT, return_value=fake):
        readiness = build_readiness(accepted_master, actor=ACTOR)

    assert {item.key: item.state for item in readiness.items} == {
        "services": "done",
        "location": "done",
        "hours": "done",
        "profile": "done",
    }
    assert readiness.blocking == []
    assert readiness.ready is True


def test_the_endpoint_answers_with_the_catalogs_counters(client: Client, bot_user, accepted_master):
    """Ручка — не только функция: субъект обязан доехать и до вопроса об услугах."""
    fake = _catalog(selection=_selection(_row(configured=True), _row(configured=False, price=None)))

    with patch(CLIENT, return_value=fake):
        resp = client.get(
            reverse("master_api:onboarding_readiness"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

    assert resp.status_code == 200, resp.content
    services = next(i for i in resp.json()["items"] if i["key"] == "services")
    assert services["state"] == "done"
    assert services["detail"] == {"selected": 2, "configured": 1}
    assert services["deep_link"] == "/solo/services"
    assert fake.get_service_selection.call_count == 1
    asked_as = fake.get_service_selection.call_args.kwargs["external_user_id"]
    assert asked_as == external_user_id_for(bot_user)
