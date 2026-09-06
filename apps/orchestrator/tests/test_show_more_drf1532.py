"""«Показать ещё» — кнопка, её payload и следующая страница (DRF-1532).

Решение владельца §29.6: «Показать ещё» должна позволять увидеть остальных
кандидатов — никто не должен исчезать из выдачи навсегда. До этой задачи срез
в пять происходил в SQL, то есть шестого кандидата не прятали, а не
запрашивали, и никакая кнопка его достать не могла.

Рендер-часть модуля базы не касается вовсе — карточки собираются из DTO, как
и в остальных сюитах рендера.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.dto import MasterCard
from apps.orchestrator.discovery import (
    CALLBACK_DISCOVER_BOOK_PREFIX,
    CALLBACK_DISCOVER_MORE_PREFIX,
    SHOW_MORE_LABEL,
    SHOW_MORE_STALE_TEXT,
    _render_master_cards,
    decode_more_ref,
    encode_more_ref,
    execute_show_more,
    fetch_master_page,
    resolve_discover_tap,
    rotation_seed,
)
from apps.tenancy.models import Tenant


def _card(name: str) -> MasterCard:
    return MasterCard(
        tenant_id=uuid4(),
        master_id=uuid4(),
        name=name,
        specialization="",
        rating=Decimal("0.00"),
        photo_url="",
        city="Пенза",
    )


def _buttons(reply) -> list[dict[str, str]]:
    data = reply.action_data or {}
    return data["attachments"][0]["payload"]["buttons"]


def _listed(reply) -> list[str]:
    """Master names read back off the rendered card lines.

    A card line is «• {имя}{специализация}{услуга}{рейтинг}{город}»; only the
    name is stable across fixtures, so everything from the first « · » on is
    dropped.
    """
    return [
        line[2:].split(" · ")[0]
        for line in (reply.text or "").splitlines()
        if line.startswith("• ")
    ]


class TestMoreRef:
    def test_roundtrip(self) -> None:
        ref = encode_more_ref(offset=5, city="Пенза", specialization="спортивный массаж")

        assert ref
        assert decode_more_ref(ref) == (5, "Пенза", "спортивный массаж")

    def test_empty_city_comes_back_as_none(self) -> None:
        ref = encode_more_ref(offset=5, city=None, specialization="массаж")

        assert decode_more_ref(ref) == (5, None, "массаж")

    @pytest.mark.parametrize("forged", ["", "!!!!", "x" * 400, "YWJj"])
    def test_forged_ref_decodes_to_none(self, forged: str) -> None:
        # Positive guard on the same encoder (DRF-1411): a ref this function
        # itself produced DOES decode, so the None below is a verdict about
        # the forged input and not about a broken decoder.
        assert decode_more_ref(encode_more_ref(offset=0, city=None, specialization="массаж"))
        assert decode_more_ref(forged) is None

    def test_payload_survives_the_discover_tap_resolver(self) -> None:
        """The handler must read this as a TAP, not as something a person said.

        ``resolve_discover_tap`` guards the history write for the whole
        ``cb:discover:`` family; a payload it does not recognise would be
        persisted verbatim as the user's own words.
        """
        ref = encode_more_ref(offset=5, city="Пенза", specialization="спортивный массаж")

        assert resolve_discover_tap(f"{CALLBACK_DISCOVER_MORE_PREFIX}{ref}") is not None


class TestRotationSeed:
    def test_seed_is_the_conversation_id(self) -> None:
        class _Conversation:
            id = "abc-123"

        assert rotation_seed(_Conversation()) == "abc-123"

    def test_no_conversation_means_no_rotation(self) -> None:
        assert rotation_seed(None) is None


class TestShowMoreButton:
    def test_button_is_rendered_last_when_a_next_page_exists(self) -> None:
        reply = _render_master_cards(
            [_card("Анна"), _card("Борис")],
            specialization="массаж",
            more_offset=5,
        )

        buttons = _buttons(reply)
        # Positive: the booking buttons are all still there and still first.
        assert len(buttons) == 3
        assert all(b["callback"].startswith(CALLBACK_DISCOVER_BOOK_PREFIX) for b in buttons[:2])
        assert buttons[-1]["label"] == SHOW_MORE_LABEL
        assert buttons[-1]["callback"].startswith(CALLBACK_DISCOVER_MORE_PREFIX)

    def test_no_button_when_that_was_everybody(self) -> None:
        reply = _render_master_cards([_card("Анна"), _card("Борис")], specialization="массаж")

        buttons = _buttons(reply)
        # Positive: the cards и их кнопки записи на месте…
        assert [b["label"] for b in buttons] == ["Записаться к Анна", "Записаться к Борис"]
        # …и лишней кнопки, ведущей в пустоту, среди них нет.
        assert not any(b["callback"].startswith(CALLBACK_DISCOVER_MORE_PREFIX) for b in buttons)

    def test_button_carries_the_query_that_produced_the_page(self) -> None:
        reply = _render_master_cards(
            [_card("Анна")], city="Пенза", specialization="массаж", more_offset=5
        )

        callback = _buttons(reply)[-1]["callback"]
        payload = callback[len(CALLBACK_DISCOVER_MORE_PREFIX) :]
        assert decode_more_ref(payload) == (5, "Пенза", "массаж")


class TestExecuteShowMoreStale:
    def test_forged_callback_gets_an_honest_sentence(self) -> None:
        reply = execute_show_more(f"{CALLBACK_DISCOVER_MORE_PREFIX}!!!!")

        assert reply.text == SHOW_MORE_STALE_TEXT
        assert reply.action_data is None


# ─── the end-to-end half: a real catalog behind the button ─────────────────

pg_only = pytest.mark.skipif(
    "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
    reason="Cyrillic ILIKE folding requires Postgres; on SQLite nothing matches.",
)


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _offers(tenant: Tenant, name: str, service_name: str) -> CatalogMaster:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        slug=name.lower().replace(" ", "-"),
        name=service_name,
        is_active=True,
        external_updated_at=_ts(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


class _Conversation:
    def __init__(self, identifier: str) -> None:
        self.id = identifier


@pytest.mark.django_db
@pg_only
class TestShowMoreReachesEverybody:
    @staticmethod
    def _eight() -> set[str]:
        tenant = Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")
        names = {f"Мастер {index:02d}" for index in range(1, 9)}
        for name in names:
            _offers(tenant, name, "Классический массаж")
        return names

    def test_tapping_show_more_yields_the_rest(self) -> None:
        expected = self._eight()
        conversation = _Conversation("conv-42")

        page1, more_offset = fetch_master_page(
            city=None, specialization="массаж", conversation=conversation
        )
        rendered = _render_master_cards(page1, specialization="массаж", more_offset=more_offset)
        callback = _buttons(rendered)[-1]["callback"]

        second = execute_show_more(callback, conversation=conversation)

        shown = _listed(second)
        page1_names = [card.name for card in page1]
        # Пять на первой странице, три на второй — все восемь достижимы…
        assert len(page1_names) == 5
        assert len(shown) == 3
        assert set(page1_names) | set(shown) == expected
        # …и ни один не показан дважды.
        assert not set(page1_names) & set(shown)
        # На последней странице три кнопки записи — по одной на мастера…
        tail_buttons = _buttons(second)
        assert len(tail_buttons) == 3
        assert all(b["callback"].startswith(CALLBACK_DISCOVER_BOOK_PREFIX) for b in tail_buttons)
        # …и среди них нет кнопки «ещё»: вести ей больше некуда.
        assert not any(
            b["callback"].startswith(CALLBACK_DISCOVER_MORE_PREFIX) for b in tail_buttons
        )

    def test_the_second_page_belongs_to_the_same_conversation(self) -> None:
        """Сид берётся из разговора, а не из кнопки.

        Тот же callback в ДРУГОМ разговоре даёт другую тройку — потому что
        первая страница у того разговора тоже была другой.
        """
        self._eight()
        mine = _Conversation("conv-A")
        theirs = _Conversation("conv-B")
        page1, more_offset = fetch_master_page(
            city=None, specialization="массаж", conversation=mine
        )
        callback = _buttons(
            _render_master_cards(page1, specialization="массаж", more_offset=more_offset)
        )[-1]["callback"]

        as_mine = execute_show_more(callback, conversation=mine)
        as_theirs = execute_show_more(callback, conversation=theirs)

        # Positive: обе страницы непустые и обе — по три мастера.
        assert len(_listed(as_mine)) == 3
        assert len(_listed(as_theirs)) == 3
        # Мой хвост — это мой хвост: ни один из него не был на моей первой
        # странице. Для чужого разговора это не выполняется, и именно поэтому
        # сид не кладут в callback.
        assert not set(_listed(as_mine)) & {card.name for card in page1}
