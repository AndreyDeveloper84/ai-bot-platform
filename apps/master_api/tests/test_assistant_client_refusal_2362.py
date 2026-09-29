"""DRF-2362 — отказ «не удалось проверить клиентов» не запирает разговор.

Живой проход 23.09: «Записать клиента» → имя → отказ «Не удалось проверить
клиентов. Попробуйте снова.». Мастер отвечает на вопрос помощника — «Это
новый клиент» — и получает **ту же строку**. Разговор мёртв: человек
ответил, и ответ ничего не изменил.

### Что здесь чинится, а что нет

Чинится **выход**: под отказом появляется та же дверь, которую соседняя
ветка этого же разбора предлагает, когда клиент не найден, — карточка
«Добавить запись» с формой записи. Слов не прибавилось ни одного: и
подпись, и адрес живут в общем ``_new_client_door`` (§60 — формулировки
утверждает владелец, исполнителю не сочинять).

**Не чинится причина отказа.** Почему поиск ответил ``Refusal``, здесь не
известно — это отдельный разбор по логу стенда. Узлы держат ровно одно
свойство: у отказа есть куда пойти, кроме повтора той же фразы.

### Почему именно эта дверь

Форма записи умеет завести нового гостя с телефоном — ровно то, что мастер
и сказал словами «это новый клиент». Дверь существует, ведёт на работающий
экран и уже предлагается в соседнем случае: два исхода одного разбора
перестают вести себя по-разному.

### Почему узлы на двух высотах

Разбор (``_resolve_client``) доказывает, что дверь есть и что она **та
же**, что у соседней ветки. Но карточка доезжает до мастера только если
``assistant.py`` признает ошибку ``verbatim`` — иначе ветка ``else``
отдаёт текст вообще без карточек. Поэтому последний узел идёт через HTTP:
он проверяет не намерение кода, а то, что доехало до провода.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.services.booking import Refusal
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.master_api.services import assistant as assistant_mod
from apps.master_api.services.assistant_actions import (
    ActionError,
    _new_client_door,
    _resolve_client,
)
from apps.master_api.tests.conftest import init_data_header
from apps.master_api.tests.test_assistant_api import FakeResult, FakeToolCall

pytestmark = pytest.mark.django_db

#: Так отвечает поиск клиентов М-2, когда салон не отозвался: «не смогли
#: спросить», а не «такого клиента нет» (``search_customers_as``).
REFUSAL = Refusal("unavailable", "customer search is unavailable", 503)


def _answers(monkeypatch, reply: Any) -> None:
    monkeypatch.setattr(
        "apps.admin_api.services.booking.search_customers_as",
        lambda **kwargs: reply,
        raising=True,
    )


def _resolve(master: CatalogMaster, name: str) -> ActionError:
    from django.utils import timezone

    with pytest.raises(ActionError) as caught:
        _resolve_client(master, {"client_name": name}, tz=timezone.get_current_timezone())
    return caught.value


class TestTheRefusalCarriesAWayOut:
    def test_the_wall_has_a_door(self, accepted_master, monkeypatch) -> None:
        _answers(monkeypatch, REFUSAL)

        error = _resolve(accepted_master, "Мария")

        # Текст отказа не тронут — слова владельца (§60).
        assert error.detail == "Не удалось проверить клиентов. Попробуйте снова."
        assert error.cards == [_new_client_door(accepted_master)]

    def test_it_is_literally_the_door_the_neighbour_offers(
        self, accepted_master, monkeypatch
    ) -> None:
        """Два исхода одного разбора перестают вести себя по-разному.

        Положительная пара: у соседней ветки («клиента с таким именем нет»)
        дверь есть давно. Узел сравнивает карточки двух веток напрямую — а
        не подписи, — поэтому разойтись они уже не могут молча.
        """
        _answers(monkeypatch, REFUSAL)
        refused = _resolve(accepted_master, "Мария")

        _answers(monkeypatch, [])
        not_found = _resolve(accepted_master, "Мария")

        assert not_found.cards, "положительная пара: у соседней ветки дверь есть"
        assert refused.cards == not_found.cards
        assert refused.detail != not_found.detail  # причины названы разные — это верно


class _RefusingSalon:
    """Салон, который на поиск клиентов не отвечает."""

    def search_customers(self, **kwargs):
        raise SalonUnavailable("stand is down")


class TestTheDoorReachesTheMaster:
    def test_the_ask_response_carries_the_card(
        self, client: Client, accepted_master, monkeypatch
    ) -> None:
        """Сквозь ``ask``: карточка доезжает до провода, а не остаётся в коде.

        Ошибка попадает в ответ с карточками только по ветке ``verbatim``
        (``assistant.py``); соседняя ветка отдала бы «Не смог подготовить
        действие: …» вообще без них. Узел меряет то, что увидит мастер.
        """
        monkeypatch.setattr(
            "apps.integrations.ayla.salon_client.get_salon_client", lambda: _RefusingSalon()
        )
        scripted = FakeResult(
            tool_calls=[
                FakeToolCall(
                    name="prepare_booking",
                    arguments={
                        "client_name": "Мария",
                        "service": "массаж",
                        "start_at": "2030-01-01T12:30:00+03:00",
                    },
                )
            ]
        )
        monkeypatch.setattr(
            assistant_mod, "_complete", lambda messages, *, tenant, tools=None: scripted
        )

        response = client.post(
            reverse("master_api:assistant_ask"),
            data=json.dumps({"text": "Запиши Марию на массаж"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["answer"] == "Не удалось проверить клиентов. Попробуйте снова."
        assert _new_client_door(accepted_master) in body["cards"]
