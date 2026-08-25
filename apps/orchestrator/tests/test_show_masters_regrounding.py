"""Свежая реплика перебивает залипший интент (DRF-968, вторая половина).

Живой контур, 09.08.2026 (приёмка DRF-962, SHA ``699639c``):

    → Кавитация
    ← Могу помочь с записью на классический массаж …

и тем же ходом — ответ на «напишите название услуги» ушёл в ``show_masters``
не той строкой: «RF-лифтинг — Лицо/шея/декольте» при прямом вызове
``discover_masters`` даёт однозначное совпадение, а карточка вернулась без
``service_id``. Значит в tool-call уехала не эта строка, а интент предыдущего
хода.

Чинится не просьбой к модели, а стражем платформы: услуга, названная В ЭТОМ
ходе, старше того, что модель принесла из истории — и решает это КАТАЛОГ
(AYLA-DEC-0045 / OD-9), а не список стемов и не сама модель.

Имена услуг в фикстуре несут стем в том же регистре, в каком его выделяет
парсер («кавитац» внутри «Ультразвуковая кавитация»). Это не косметика: ILIKE
на SQLite складывает регистр только для ASCII, и суита с «Кавитация» в имени
проходила бы на CI и молчала бы локально — ровно тот класс проверки, который
неотличим от проходящей.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge as concierge_mod
from apps.orchestrator.discovery import reground_specialization
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TRACE_ID = str(uuid.uuid4())

# Ровно те две строки из живого диалога.
SAID = "Кавитация"
STUCK = "классический массаж"


def _ts() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _master(tenant: Tenant, name: str, service_name: str, slug: str) -> CatalogMaster:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=_ts(),
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=service_name,
        is_active=True,
        ayla_service_id=uuid4(),
        external_updated_at=_ts(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


@pytest.fixture
def contour() -> Tenant:
    """Пилотный контур в миниатюре: кавитация и массаж у РАЗНЫХ мастеров.

    Разные — в этом весь смысл. Будь обе услуги у одного человека, карточка
    вернулась бы та же самая при любой из двух строк, и проверка не отличала
    бы починку от её отсутствия.
    """
    tenant = Tenant.objects.create(slug="salon-penza-968", name="SPAtrium", city="Пенза")
    _master(tenant, "Тихонова Ольга", "Ультразвуковая кавитация", "kav")
    _master(tenant, "Архипкин Денис", "Классический массаж", "klass")
    return tenant


# ---------------------------------------------------------------------------
# Само правило
# ---------------------------------------------------------------------------


class TestTheRule:
    def test_a_service_named_this_turn_beats_the_carry_over(self, contour: Tenant) -> None:
        """Тот самый ход: человек сказал «Кавитация», модель искала массаж."""
        assert (
            reground_specialization(message_text=SAID, city="Пенза", specialization=STUCK) == SAID
        )

    def test_the_models_own_reading_of_this_turn_is_kept(self, contour: Tenant) -> None:
        """«кавитац» есть в строке модели — это прочтение ЭТОГО хода, не старого."""
        assert (
            reground_specialization(
                message_text="хочу кавитацию", city="Пенза", specialization="кавитация"
            )
            == "кавитация"
        )

    def test_a_turn_that_names_no_service_leaves_the_criteria_alone(self, contour: Tenant) -> None:
        """«а в Пензе?» — город, а не услуга: критерии обязаны прийти из истории."""
        assert (
            reground_specialization(message_text="а в Пензе?", city=None, specialization=STUCK)
            == STUCK
        )

    def test_words_the_catalog_cannot_serve_do_not_override(self, contour: Tenant) -> None:
        """Страж от обратной ошибки: «а подешевле?» — длинное слово, но не услуга.

        Без каталога в роли судьи правило превратилось бы в «последняя реплика
        всегда права» и снесло бы каждый законный перенос критериев.
        """
        assert (
            reground_specialization(message_text="а подешевле?", city="Пенза", specialization=STUCK)
            == STUCK
        )

    def test_an_empty_specialization_is_passed_through(self, contour: Tenant) -> None:
        """Пустую строку разбирает ``has_discovery_criteria``, а не это правило."""
        assert reground_specialization(message_text=SAID, city=None, specialization=None) is None
        assert reground_specialization(message_text=SAID, city=None, specialization="") == ""

    def test_an_empty_turn_changes_nothing(self, contour: Tenant) -> None:
        assert reground_specialization(message_text="", city=None, specialization=STUCK) == STUCK


# ---------------------------------------------------------------------------
# Живой путь: консьерж
# ---------------------------------------------------------------------------


def _bot_user_and_conversation():
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="drf968-uid", chat_id="drf968-chat"
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _show_masters(args: dict) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="show_masters", arguments=args)],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _text(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


# ``transaction=True`` — та же причина, что и в суите DRF-1312: консьерж
# крутит ход через ``asyncio.run`` и ходит в БД через ``sync_to_async``,
# который встаёт в клинч с atomic-блоком обычного ``django_db``.
@pytest.mark.django_db(transaction=True)
class TestTheLivePath:
    def _run(self, monkeypatch, turn: str, args: dict) -> dict:
        provider = AsyncMock()
        provider.complete = AsyncMock(
            side_effect=[_show_masters(args), _text("Вот кто может подойти.")]
        )
        provider.default_completion_model = "gpt-4o-mini"
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge_mod, "get_router", lambda: router)

        # Шпион ПОВЕРХ настоящего поиска, а не вместо него: спор идёт о том,
        # что каталог получил на вход и кого он на это вернул, и подменённый
        # каталог не сказал бы ни о том, ни о другом.
        seen: dict = {}
        real = concierge_mod.discover_masters

        def spy(**kwargs):
            seen.update(kwargs)
            cards = real(**kwargs)
            seen["cards"] = [card.name for card in cards]
            return cards

        monkeypatch.setattr(concierge_mod, "discover_masters", spy)

        bot_user, conversation = _bot_user_and_conversation()
        concierge_mod.generate_concierge_reply(
            turn, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )
        return seen

    def test_the_catalog_is_searched_for_what_the_person_just_said(
        self, settings, monkeypatch, contour
    ) -> None:
        """Отрицание «массажиста не показали» прошло бы и на пустом ответе,
        поэтому рядом стоит положительное: показан тот, кто делает кавитацию —
        на тех же данных."""
        settings.BOOKING_VIA_AYLA_REST = True
        seen = self._run(monkeypatch, SAID, {"city": "Пенза", "specialization": STUCK})

        assert seen["specialization"] == SAID
        assert seen["cards"] == ["Тихонова Ольга"]

    def test_a_grounded_call_reaches_the_catalog_untouched(
        self, settings, monkeypatch, contour
    ) -> None:
        """Нормализация модели («хочу массаж» → «массаж») — не залипание."""
        settings.BOOKING_VIA_AYLA_REST = True
        seen = self._run(monkeypatch, "хочу массаж", {"city": "Пенза", "specialization": "массаж"})

        assert seen["specialization"] == "массаж"
        assert seen["cards"] == ["Архипкин Денис"]
