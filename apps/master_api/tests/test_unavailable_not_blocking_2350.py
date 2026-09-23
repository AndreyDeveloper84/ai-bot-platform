"""Недоступный пункт больше не держит отправку профиля (DRF-2350, §77 п. 1).

Решение владельца 23.09.2026. До него недоступный пункт (`unavailable`)
блокировал `ready` наравне с ненастроенным, и салонный мастер, закрывший
расписание и профиль, упирался в тупик: полная полоса готовности, ни одной
кнопки действия, заголовок «всё готово» — и ни слова о том, почему профиль
не отправить (замер DRF-2326). DRF-2254 прямо оставил этот вопрос владельцу:
«достижимость „готово“ при ведении вне приложения — отдельное решение».

Смысл признака меняется, поэтому он назван заново: `ready_to_submit` —
«всё, что мастер может закрыть САМ, закрыто». Недоступное не исчезает из
ответа: оно переезжает в `managed_elsewhere`, иначе «готов» читался бы как
«настроено всё», а это неправда — часть шагов просто ведётся не здесь.

* c1 — недоступный пункт не в `blocking`, а в `managed_elsewhere`;
* c2 — ненастроенное и «не удалось прочитать» держат отправку по-прежнему:
  снято ровно одно основание, а не проверка целиком;
* c3 — салонный мастер с закрытыми расписанием и профилем готов отправлять;
* c4 — `managed_elsewhere` называет причину, а не только имя пункта;
* c5 — пункт вне `REQUIRED_ITEMS` не попадает ни в один из списков;
* c6 — в ответе ручки есть оба поля, и они не пересекаются.
"""

from __future__ import annotations

import pytest

from apps.master_api.services.onboarding_readiness import (
    MANAGED_OUTSIDE_APP,
    Readiness,
    ReadinessItem,
)


def _readiness(*items: ReadinessItem, identity: str = "linked") -> Readiness:
    return Readiness(
        items=items,
        identity={"state": identity, "link_status": None},
        sale_block=None,
    )


def _item(key: str, state: str, reason: str | None = None) -> ReadinessItem:
    return ReadinessItem(key=key, state=state, detail={}, reason=reason)


class TestC1UnavailableDoesNotBlock:
    def test_it_moves_from_blocking_to_managed_elsewhere(self) -> None:
        r = _readiness(
            _item("services", "unavailable", MANAGED_OUTSIDE_APP),
            _item("location", "unavailable", "capability_not_built"),
            _item("hours", "done"),
            _item("profile", "done"),
        )

        assert r.managed_elsewhere == [  # наличие: недоступное названо
            f"services:{MANAGED_OUTSIDE_APP}",
            "location:capability_not_built",
        ]
        assert r.blocking == []
        assert r.ready_to_submit is True


class TestC2WhatStillBlocks:
    @pytest.mark.parametrize("state", ["missing", "unknown"])
    def test_missing_and_unknown_hold_the_submission(self, state: str) -> None:
        """Снято одно основание, а не проверка целиком."""
        r = _readiness(
            _item("services", "unavailable", MANAGED_OUTSIDE_APP),
            _item("location", "unavailable", MANAGED_OUTSIDE_APP),
            _item("hours", state),
            _item("profile", "done"),
        )

        assert r.blocking == [f"hours:{state}"]
        assert r.ready_to_submit is False


class TestC3TheSalonMasterCanSubmit:
    def test_both_items_managed_outside_and_the_rest_done(self) -> None:
        r = _readiness(
            _item("services", "unavailable", MANAGED_OUTSIDE_APP),
            _item("location", "unavailable", MANAGED_OUTSIDE_APP),
            _item("hours", "done"),
            _item("profile", "done"),
        )

        body = r.as_dict()

        assert body["ready"] is True
        assert body["blocking"] == []


class TestC4TheReasonTravelsWithTheItem:
    def test_managed_elsewhere_names_why_not_just_what(self) -> None:
        r = _readiness(
            _item("services", "unavailable", MANAGED_OUTSIDE_APP),
            _item("location", "unavailable", "capability_not_built"),
            _item("hours", "done"),
            _item("profile", "done"),
        )

        assert r.managed_elsewhere == [
            f"services:{MANAGED_OUTSIDE_APP}",
            "location:capability_not_built",
        ]


class TestC5NonRequiredItemsAreInNeitherList:
    def test_a_key_outside_REQUIRED_ITEMS_is_not_counted(self) -> None:
        """Названный предел: списки отвечают за ТРЕБУЕМЫЕ пункты. Пункт вне
        `REQUIRED_ITEMS` не держит отправку и в «ведётся не здесь» не
        попадает — сегодня таких пунктов не строит никто, и узел стоит,
        чтобы появление первого было видно, а не молчаливо."""
        r = _readiness(
            _item("services", "done"),
            _item("location", "done"),
            _item("hours", "done"),
            _item("profile", "done"),
            _item("portfolio", "unavailable", MANAGED_OUTSIDE_APP),
            _item("banner", "missing"),
        )

        assert r.ready_to_submit is True
        assert r.blocking == []
        assert r.managed_elsewhere == []


class TestC6TheAnswerCarriesBothLists:
    def test_they_are_disjoint_and_both_present(self) -> None:
        r = _readiness(
            _item("services", "unavailable", MANAGED_OUTSIDE_APP),
            _item("location", "missing"),
            _item("hours", "done"),
            _item("profile", "missing"),
        )

        body = r.as_dict()

        assert body["managed_elsewhere"] == [f"services:{MANAGED_OUTSIDE_APP}"]
        assert set(body["blocking"]) == {"location:missing", "profile:missing"}
        keys_blocking = {row.split(":")[0] for row in body["blocking"]}
        keys_elsewhere = {row.split(":")[0] for row in body["managed_elsewhere"]}
        assert keys_blocking & keys_elsewhere == set()
