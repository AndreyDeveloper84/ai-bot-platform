"""DRF-2362 — отказ «не удалось проверить клиентов» не запирает разговор.

Живой проход 23.09: «Записать клиента» → имя → отказ «Не удалось проверить
клиентов. Попробуйте снова.». Мастер отвечает на вопрос помощника — «Это
новый клиент» — и получает **ту же строку**. Разговор мёртв: человек
ответил, и ответ ничего не изменил.

### Что здесь чинится, а что нет

Чинится **выход**: под отказом появляется та же дверь, которую соседняя
ветка этого же разбора уже предлагает, когда клиент не найден, — карточка
«Добавить запись» с формой записи. Слов не прибавилось ни одного: и
подпись, и адрес взяты у существующей ветки (§60 — формулировки утверждает
владелец, исполнителю не сочинять).

**Не чинится причина отказа.** Почему поиск ответил ``Refusal``, здесь не
известно — это отдельный разбор по логу стенда. Узлы держат ровно одно
свойство: что бы мастер ни ответил дальше, у него есть куда пойти, кроме
повтора той же фразы.

### Почему именно эта дверь

Форма записи умеет завести нового гостя с телефоном — ровно то, что мастер
и сказал словами «это новый клиент». Дверь существует, ведёт на работающий
экран и уже предлагается в соседнем случае: два отказа одного разбора
перестают вести себя по-разному.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone

from apps.admin_api.services.booking import Refusal
from apps.catalog.models import CatalogMaster
from apps.master_api.services.assistant_actions import ActionError, _resolve_client
from apps.master_api.services.assistant_cards import booking_form_url
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="refusal-2362", name="Формула тела", timezone="Europe/Moscow")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


def _answers(monkeypatch, reply: Any) -> None:
    """Чем отвечает поиск клиентов М-2 на этом проходе."""

    monkeypatch.setattr(
        "apps.admin_api.services.booking.search_customers_as",
        lambda **kwargs: reply,
        raising=True,
    )


def _resolve(master: CatalogMaster, name: str) -> ActionError:
    with pytest.raises(ActionError) as caught:
        _resolve_client(master, {"client_name": name}, tz=timezone.get_current_timezone())
    return caught.value


REFUSAL = Refusal(slug="upstream_unavailable", detail="", status=502)


class TestTheRefusalCarriesAWayOut:
    def test_the_wall_has_a_door(self, master, monkeypatch) -> None:
        _answers(monkeypatch, REFUSAL)

        error = _resolve(master, "Мария")

        # Текст отказа не тронут — слова владельца (§60).
        assert error.detail == "Не удалось проверить клиентов. Попробуйте снова."
        cards: list[dict[str, Any]] = list(error.cards or [])
        assert cards, "мастер остался без единого выхода — это и есть петля"
        assert cards[0] == {
            "kind": "open",
            "url": booking_form_url(master),
            "label": "Добавить запись",
        }

    def test_it_is_literally_the_door_the_neighbour_offers(self, master, monkeypatch) -> None:
        """Две ветки одного разбора перестают вести себя по-разному.

        Положительная пара: у соседней ветки («клиента с таким именем нет»)
        дверь есть давно. Узел сравнивает карточки двух веток напрямую —
        если дверь отказа однажды разойдётся с соседней, это увидят здесь,
        а не на стенде.
        """
        _answers(monkeypatch, REFUSAL)
        refused = _resolve(master, "Мария")

        _answers(monkeypatch, [])
        not_found = _resolve(master, "Мария")

        assert not_found.cards, "положительная пара: у соседней ветки дверь есть"
        assert refused.cards == not_found.cards
        assert refused.detail != not_found.detail  # причина названа разная — это верно


class TestAnAnswerDoesNotMeetABareWall:
    def test_the_answer_the_owner_typed_still_finds_the_exit(self, master, monkeypatch) -> None:
        """«Это новый клиент» — то, что мастер ответил в живом проходе.

        Поиск так же недоступен, отказ тот же: причина не лечилась. Меняется
        одно — ответ больше не упирается в голую стену.
        """
        _answers(monkeypatch, REFUSAL)

        first = _resolve(master, "Мария")
        second = _resolve(master, "Это новый клиент")

        assert first.detail == second.detail
        assert second.cards == first.cards
        assert second.cards[0]["kind"] == "open"
