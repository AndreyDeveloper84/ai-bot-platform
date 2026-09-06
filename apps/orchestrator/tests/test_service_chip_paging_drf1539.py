"""«Показать ещё» за чипом услуги — DRF-1539, поверхность тапа.

Каталожная половина в `apps/marketplace/tests/test_service_chip_masters_drf1539.py`.
Здесь — сама кнопка: несёт ли она услугу и смещение, доводит ли до всех, и
одинаков ли порядок при двух тапах в одном разговоре.

Замер, который воспроизводит первый тест: по чипу популярной услуги находится
восемь мастеров, показывается пять, и до DRF-1539 остальные трое были
недостижимы — кто именно, решала фамилия.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.orchestrator.discovery import (
    CALLBACK_CATALOG_MASTERS_PREFIX,
    CATALOG_STALE_CARD_TEXT,
    SHOW_MORE_LABEL,
    SHOW_MORE_STALE_TEXT,
    execute_catalog_callback,
)
from apps.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db]


@dataclass(frozen=True)
class _Conversation:
    """Ровно то, что читает `rotation_seed` — идентификатор, и ничего больше."""

    id: str


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def popular() -> CatalogService:
    tenant = Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        slug="klassicheskiy-massazh",
        name="Классический массаж",
        is_active=True,
        external_updated_at=_ts(),
    )
    for index in range(1, 9):
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_updated_at=_ts(),
            name=f"Мастер {index:02d}",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return service


_ALL_EIGHT = {f"Мастер {index:02d}" for index in range(1, 9)}


def _buttons(reply: Any) -> list[dict[str, str]]:
    return (reply.action_data or {})["attachments"][0]["payload"]["buttons"]


def _more(reply: Any) -> str | None:
    for button in _buttons(reply):
        if button["label"] == SHOW_MORE_LABEL:
            return button["callback"]
    return None


def _named(reply: Any) -> set[str]:
    return {line[2:].split(" · ")[0] for line in reply.text.splitlines() if line.startswith("• ")}


class TestTheTapReachesEverybody:
    """§29.6 — «никто не должен исчезать из выдачи навсегда»."""

    def test_first_tap_shows_five_and_offers_more(self, popular: CatalogService) -> None:
        reply = execute_catalog_callback(
            f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}",
            conversation=_Conversation("conv-1"),
        )

        assert reply is not None
        shown = _named(reply)
        print(f"\nпоказано по чипу: {len(shown)} из 8 -> {sorted(shown)}")
        assert len(shown) == 5
        assert _more(reply) == f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}:5"

    def test_the_second_tap_finishes_the_list(self, popular: CatalogService) -> None:
        conversation = _Conversation("conv-1")
        first = execute_catalog_callback(
            f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}", conversation=conversation
        )
        assert first is not None
        more = _more(first)
        assert more is not None

        second = execute_catalog_callback(more, conversation=conversation)

        assert second is not None
        page1, page2 = _named(first), _named(second)
        print(f"вторая страница: {sorted(page2)}")
        assert len(page2) == 3
        # Nobody repeats…
        assert not page1 & page2
        # …and nobody falls between the pages.
        assert page1 | page2 == _ALL_EIGHT
        # That was everybody, so no button leading nowhere.
        assert _more(second) is None

    def test_the_cards_still_carry_the_service(self, popular: CatalogService) -> None:
        """Тап входит в запись с услугой — на обеих страницах."""
        conversation = _Conversation("conv-1")
        first = execute_catalog_callback(
            f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}", conversation=conversation
        )
        assert first is not None
        more = _more(first)
        assert more is not None
        second = execute_catalog_callback(more, conversation=conversation)
        assert second is not None

        booking = [b["callback"] for b in _buttons(second) if b["label"].startswith("Записаться")]
        assert booking
        assert all(str(popular.id) in callback for callback in booking)


class TestOrderIsStableWithinOneDialogue:
    """§29.6 — человек не видит, как список перетасовывается между репликами."""

    def test_two_taps_on_the_same_chip_give_the_same_order(self, popular: CatalogService) -> None:
        conversation = _Conversation("conv-42")
        callback = f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}"

        first = execute_catalog_callback(callback, conversation=conversation)
        second = execute_catalog_callback(callback, conversation=conversation)

        assert first is not None and second is not None
        assert first.text == second.text

    def test_two_dialogues_do_not_have_to_agree(self, popular: CatalogService) -> None:
        """Между клиентами первые места распределяются, а не достаются фамилии."""
        callback = f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}"

        leaders = set()
        for index in range(40):
            reply = execute_catalog_callback(callback, conversation=_Conversation(f"c-{index}"))
            assert reply is not None
            leaders.add(reply.text.splitlines()[1])

        print(f"\nразных мастеров первыми за 40 разговоров: {len(leaders)}")
        assert len(leaders) > 1

    def test_without_a_conversation_nothing_changes(self, popular: CatalogService) -> None:
        """Позитивная стража обратной совместимости: старый вызов работает."""
        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}")

        assert reply is not None
        assert len(_named(reply)) == 5
        # Deterministic ranked order — the alphabet, since the candidates tie.
        assert _named(reply) == {f"Мастер {i:02d}" for i in range(1, 6)}


class TestDegradations:
    """Тап приходит из канала и не считается тем, что мы нарисовали."""

    def test_a_ref_without_an_offset_still_means_page_one(self, popular: CatalogService) -> None:
        """Кнопки, выложенные до DRF-1539, значат ровно то, что значили."""
        without = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}")
        explicit = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}:0")

        assert without is not None and explicit is not None
        assert without.text == explicit.text

    @pytest.mark.parametrize(
        "tail",
        ["-1", "abc", "1.5", "", "5:5"],
    )
    def test_a_malformed_offset_is_a_stale_card(self, popular: CatalogService, tail: str) -> None:
        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}:{tail}")

        assert reply is not None
        assert reply.text == CATALOG_STALE_CARD_TEXT

    def test_an_offset_past_the_end_says_so(self, popular: CatalogService) -> None:
        """Не «записаться не к кому» — услуга-то бронируемая."""
        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{popular.id}:99")

        assert reply is not None
        assert reply.text == SHOW_MORE_STALE_TEXT

    def test_an_unknown_service_keeps_the_wording_it_had(self) -> None:
        """Поведение с `dev`, не тронутое этой задачей.

        Неизвестный id и «услугу никто не выполняет» дают один и тот же ответ:
        обе ситуации значат «записаться не к кому», и различать их для
        человека нечем. Зафиксировано здесь, чтобы правка страниц не сдвинула
        текст молча.
        """
        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{uuid4()}")

        assert reply is not None
        assert reply.text.startswith("На эту услугу сейчас записаться не к кому")

    def test_a_service_nobody_performs_keeps_its_own_wording(self) -> None:
        tenant = Tenant.objects.create(slug="pusto", name="Пусто", city="Пенза")
        orphan = CatalogService.all_tenants.create(
            tenant=tenant,
            slug="nikto",
            name="Никем не выполняется",
            is_active=True,
            external_updated_at=_ts(),
        )

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{orphan.id}")

        assert reply is not None
        assert reply.text.startswith("На эту услугу сейчас записаться не к кому")


def test_settings_module_is_loaded() -> None:
    """Sanity: the suite really ran against a configured Django."""
    assert settings.configured
