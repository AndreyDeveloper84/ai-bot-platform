"""Человек написал название услуги — ищем её, а не прошлый интент (DRF-968).

Живой контур, 09.08.2026 (приёмка DRF-962, SHA ``699639c``):

    → Кавитация
    ← Могу помочь с записью на классический массаж …

и тем же ходом — ответ на «напишите название услуги» ушёл в ``show_masters``
не той строкой: «RF-лифтинг — Лицо/шея/декольте» при прямом вызове
``discover_masters`` даёт однозначное совпадение, а карточка вернулась без
``service_id``. Значит в tool-call уехала не эта строка, а интент предыдущего
хода.

Правило намеренно узкое, и половина этого файла — про то, почему широкое было
бы хуже болезни. «Услуга, названная в этом ходе, старше переноса из истории»
звучит правильно и ломается на живом каталоге: «на дому» — часть названия
«Массаж на дому», «вечер» — часть «Макияж вечерний». Человек, спросивший «а
можно на дому?» про уже обсуждаемый массаж, получил бы вместо ответа поиск по
выездным услугам любых категорий. Поэтому страж срабатывает только когда
реплика — это НАЗВАНИЕ услуги слово в слово, и все ворота падают в одну
сторону: оставить то, что просила модель.

Имена услуг в фикстуре несут стем в том же регистре, в каком его выделяет
парсер («кавитац» внутри «Ультразвуковая кавитация», «дому» внутри «Массаж на
дому»). Это не косметика: ILIKE на SQLite складывает регистр только для ASCII,
и суита с «Кавитация» в имени проходила бы на CI и молчала бы локально —
ровно тот класс проверки, который неотличим от проходящей.
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

# Название услуги, набранное человеком, и интент, залипший с прошлого хода.
SAID = "Ультразвуковая кавитация"
STUCK = "классический массаж"


def _ts() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=_ts(),
    )


def _service(tenant: Tenant, master: CatalogMaster, name: str, slug: str) -> CatalogService:
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=name,
        is_active=True,
        ayla_service_id=uuid4(),
        external_updated_at=_ts(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return service


@pytest.fixture
def contour() -> Tenant:
    """Пилотный контур в миниатюре, включая обе ловушки.

    Кавитация и массаж — у РАЗНЫХ мастеров: будь обе услуги у одного, карточка
    вернулась бы та же самая при любой из двух строк, и проверка не отличала
    бы починку от её отсутствия.

    «Массаж на дому» и «Макияж вечерний» — не декорация. Это те два названия,
    из-за которых уточняющий ход («а можно на дому?», «можно на вечер?») несёт
    слово, которое каталог действительно находит. Без них тест на ложное
    срабатывание проходил бы вакуумно — на данных, где ошибка физически
    невозможна.
    """
    tenant = Tenant.objects.create(slug="salon-penza-968", name="SPAtrium", city="Пенза")
    olga = _master(tenant, "Тихонова Ольга")
    denis = _master(tenant, "Архипкин Денис")
    _service(tenant, olga, "Ультразвуковая кавитация", "kav")
    _service(tenant, olga, "Макияж вечерний", "makiyazh")
    _service(tenant, denis, "Классический массаж", "klass")
    _service(tenant, denis, "Массаж на дому", "vyezd")
    return tenant


# ---------------------------------------------------------------------------
# Когда страж срабатывает
# ---------------------------------------------------------------------------


class TestTheNameWins:
    def test_a_typed_service_name_beats_the_carry_over(self, contour: Tenant) -> None:
        """Ход из тикета: человек написал название, модель искала прошлое."""
        assert (
            reground_specialization(message_text=SAID, city="Пенза", specialization=STUCK) == SAID
        )

    def test_the_name_is_recognised_through_inflection(self, contour: Tenant) -> None:
        """«Ультразвуковую кавитацию» — то же название, другой падеж.

        Стем режется до шести символов, и обе стороны идут через один и тот же
        разбор, так что падеж не должен решать, попадёт человек в запись или
        нет.
        """
        assert (
            reground_specialization(
                message_text="Ультразвуковую кавитацию", city="Пенза", specialization=STUCK
            )
            == "Ультразвуковую кавитацию"
        )


# ---------------------------------------------------------------------------
# Когда страж обязан промолчать
# ---------------------------------------------------------------------------


class TestTheCarryOverSurvives:
    def test_a_qualifier_does_not_replace_the_request(self, contour: Tenant) -> None:
        """«а можно на дому?» — уточнение к уже идущему запросу.

        «дому» каталог находит («Массаж на дому»), и правило вида «названа
        услуга — значит новый запрос» здесь бы сработало: человек, спросивший
        про выезд к уже обсуждаемому классическому массажу, получил бы вместо
        ответа поиск по выездным услугам любых категорий. Реплика несёт одно
        слово из двухсловного названия, то есть уточняет запрос, а не называет
        новый.
        """
        assert (
            reground_specialization(
                message_text="а можно на дому?", city="Пенза", specialization=STUCK
            )
            == STUCK
        )

    def test_a_qualifier_without_a_leading_particle_is_also_kept(self, contour: Tenant) -> None:
        """«можно на вечер?» — та же ловушка через «Макияж вечерний».

        Отдельным случаем, потому что здесь нет вводного «а»: правило не имеет
        права держаться на том, что уточнение начинается с частицы.
        """
        assert (
            reground_specialization(
                message_text="можно на вечер?", city="Пенза", specialization=STUCK
            )
            == STUCK
        )

    def test_part_of_a_name_is_not_the_name(self, contour: Tenant) -> None:
        """«кавитация» без «ультразвуковая» — заявленная консервативность.

        Слово однозначно указывает на одну услугу контура, и всё же страж
        молчит. Это не пробел, а цена ворот, которые держат два теста выше:
        отличить головное слово названия от его определения этот слой не
        может, а ошибиться в эту сторону — оставить то, что просила модель.
        """
        assert (
            reground_specialization(message_text="кавитация", city="Пенза", specialization=STUCK)
            == STUCK
        )

    def test_the_models_own_reading_of_this_turn_is_kept(self, contour: Tenant) -> None:
        """«кавитац» есть в строке модели — это прочтение ЭТОГО хода, не старого."""
        assert (
            reground_specialization(
                message_text="хочу ультразвуковую кавитацию",
                city="Пенза",
                specialization="ультразвуковая кавитация",
            )
            == "ультразвуковая кавитация"
        )

    def test_a_turn_that_names_no_service_leaves_the_criteria_alone(self, contour: Tenant) -> None:
        """«а в Пензе?» — город, а не услуга: критерии обязаны прийти из истории."""
        assert (
            reground_specialization(message_text="а в Пензе?", city=None, specialization=STUCK)
            == STUCK
        )

    def test_words_the_catalog_cannot_serve_do_not_override(self, contour: Tenant) -> None:
        """«а подешевле?» — длинное слово, но ни одна услуга им не называется."""
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
            seen["cards"] = sorted({card.name for card in cards})
            return cards

        monkeypatch.setattr(concierge_mod, "discover_masters", spy)

        bot_user, conversation = _bot_user_and_conversation()
        reply = concierge_mod.generate_concierge_reply(
            turn, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )
        seen["reply"] = reply.text
        return seen

    def test_the_catalog_is_searched_for_what_the_person_just_said(
        self, settings, monkeypatch, contour
    ) -> None:
        """Отрицание «массажиста не показали» прошло бы и на пустом ответе,
        поэтому рядом стоит положительное: показана та, кто делает кавитацию —
        на тех же данных."""
        settings.BOOKING_VIA_AYLA_REST = True
        seen = self._run(monkeypatch, SAID, {"city": "Пенза", "specialization": STUCK})

        assert seen["specialization"] == SAID
        assert seen["cards"] == ["Тихонова Ольга"]

    def test_the_stale_service_list_is_dropped_with_the_stale_intent(
        self, settings, monkeypatch, contour
    ) -> None:
        """`services` модели пришёл из того же залипшего чтения.

        Оставить его — значит сказать человеку, набравшему название услуги,
        «а маникюра у наших мастеров нет», хотя маникюра он не просил.
        """
        settings.BOOKING_VIA_AYLA_REST = True
        seen = self._run(
            monkeypatch,
            SAID,
            {
                "city": "Пенза",
                "specialization": STUCK,
                "services": ["классический массаж", "маникюр"],
            },
        )

        assert seen["specialization"] == SAID
        assert "маникюр" not in seen["reply"]
        assert seen["cards"] == ["Тихонова Ольга"]

    def test_a_grounded_call_reaches_the_catalog_untouched(
        self, settings, monkeypatch, contour
    ) -> None:
        """Нормализация модели («хочу массаж» → «массаж») — не залипание."""
        settings.BOOKING_VIA_AYLA_REST = True
        seen = self._run(monkeypatch, "хочу массаж", {"city": "Пенза", "specialization": "массаж"})

        assert seen["specialization"] == "массаж"
        assert seen["cards"] == ["Архипкин Денис"]
