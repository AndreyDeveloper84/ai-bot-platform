"""Чип с названием услуги: поиск идёт по нему, и слова над списком не врут.

Пилот, 09.11.2026, трасса ``fdb64f3a``. Бот спросил «Что именно хочешь
выбрать?» и предложил чипы. Человек нажал «Биоэнергетический массаж».

1. Быстрый путь отдал ход модели: слова «биоэнергетический» нет в его словаре.
2. Модель вызвала ``show_masters`` с услугой «массаж».
3. Страж DRF-968 промолчал: у строки модели и реплики общее слово «массаж».
4. Модель написала над пятью массажистами «нашёл мастеров, которые предлагают
   биоэнергетический массаж». Услугу оказывали двое из пяти.

Два дефекта, и этот файл держит оба:

* **поиск потерял названную услугу** — быстрый путь, страж DRF-968 и срез
  выдачи (``named_service_only``) теперь отвечают на название услуги каталога
  мастерами этой услуги;
* **слова модели утверждали услугу, по которой поиск не шёл** —
  ``cards_prose_guard`` заменяет такой текст детерминированным показом.

Каждое отрицание стоит рядом с положительной проверкой на тех же данных
(DRF-1411).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from django.conf import settings as django_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import cards_prose_guard, fast_path
from apps.orchestrator import concierge as concierge_mod
from apps.orchestrator import discovery as discovery_mod
from apps.orchestrator.cards_prose_guard import unverified_service_words
from apps.orchestrator.discovery import (
    CLARIFY_SERVICE_QUESTION,
    clarifying_question,
    named_bookable_service,
    reground_specialization,
)
from apps.orchestrator.fast_path import decide
from apps.orchestrator.search_recap import service_fragment
from apps.tenancy.models import Tenant

# Только Postgres: название из двух слов должно совпасть обоими словами, а
# регистр кириллицы в ILIKE складывает база, не SQLite.
_POSTGRES_ONLY = pytest.mark.skipif(
    "postgresql" not in str(django_settings.DATABASES["default"]["ENGINE"]),
    reason="Cyrillic ILIKE folding requires Postgres.",
)
pytestmark = pytest.mark.django_db

TRACE_ID = str(uuid.uuid4())

CHIP = "Биоэнергетический массаж"
#: То, что модель передала в инструмент на пилоте.
DROPPED = "массаж"

PERFORMS = ["Био второй", "Био первый"]

SHOWN_HEADER = "Вот мастера, которые могут подойти:"


def _ts() -> datetime:
    return datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _offers(tenant: Tenant, master_name: str, *service_names: str) -> CatalogMaster:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=master_name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=_ts(),
        ayla_user_id=uuid4(),
    )
    for index, service_name in enumerate(service_names):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=f"{master_name}-{index}".lower().replace(" ", "-"),
            name=service_name,
            is_active=True,
            ayla_service_id=uuid4(),
            external_updated_at=_ts(),
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


@pytest.fixture
def contour(settings) -> Tenant:
    """Контур 09.11 в миниатюре: двое оказывают названную услугу, четверо — другой массаж.

    Пять двухсловных названий массажа дают ярус, на котором бот задаёт вопрос
    с чипами, — тот самый ход, с которого началась трасса.
    """
    settings.BOOKING_VIA_AYLA_REST = True
    settings.DISCOVERY_CLARIFY_MIN_TIER = 4
    tenant = Tenant.objects.create(slug="formula-tela-chip", name="Формула тела", city="Пенза")
    other = Tenant.objects.create(slug="other-salon-chip", name="Другой салон", city="Пенза")
    _offers(tenant, "Био первый", CHIP)
    _offers(tenant, "Био второй", CHIP)
    _offers(other, "Классик", "Классический массаж")
    _offers(other, "Спортсмен", "Спортивный массаж")
    _offers(other, "Таец", "Тайский массаж")
    _offers(other, "Лицо и шея", "Массаж лица и шеи")
    return tenant


def _labels(reply: Any) -> list[str]:
    rows = reply.action_data["attachments"][0]["payload"]["buttons"]
    flat = [button for row in rows for button in (row if isinstance(row, list) else [row])]
    return [str(button.get("label") or button.get("text") or "") for button in flat]


# ---------------------------------------------------------------------------
# Название услуги каталога — один ответ для обоих путей
# ---------------------------------------------------------------------------


@_POSTGRES_ONLY
class TestNamedBookableService:
    def test_the_chip_is_a_service_name(self, contour: Tenant) -> None:
        assert named_bookable_service(CHIP) == CHIP

    def test_inflection_and_filler_do_not_hide_the_name(self, contour: Tenant) -> None:
        assert named_bookable_service("хочу биоэнергетического массажа") == CHIP

    def test_one_word_of_the_name_is_not_the_name(self, contour: Tenant) -> None:
        """Положительная проверка выше, на тех же данных: целое название узнаётся."""
        assert named_bookable_service("биоэнергетический") is None
        assert named_bookable_service("массаж") is None

    def test_a_name_nobody_can_book_is_not_offered(self, contour: Tenant) -> None:
        """Услуга есть в зеркале, но мастера с ней нет — записать некуда."""
        CatalogService.all_tenants.create(
            tenant=contour,
            slug="orphan",
            name="Парный массаж",
            is_active=True,
            ayla_service_id=uuid4(),
            external_updated_at=_ts(),
        )

        assert named_bookable_service(CHIP) == CHIP
        assert named_bookable_service("Парный массаж") is None


# ---------------------------------------------------------------------------
# Быстрый путь
# ---------------------------------------------------------------------------


@_POSTGRES_ONLY
class TestFastPathClaimsACatalogName:
    def test_the_chip_turn_is_answered_without_the_model(self, contour: Tenant) -> None:
        decision = decide(CHIP)

        assert decision.claimed is True
        assert decision.reason == "catalog_service_name"

    def test_the_name_in_another_case_is_claimed_too(self, contour: Tenant) -> None:
        assert decide("запиши меня на биоэнергетический массаж").claimed is True
        assert decide("биоэнергетического массажа").claimed is True

    def test_every_chip_of_the_question_comes_back_to_this_branch(self, contour: Tenant) -> None:
        """Чип — это название услуги, а нажатие приходит как набранный текст.

        Вопрос задаёт детерминированная ветка, и ответ на него обязан
        вернуться в неё же: иначе названную услугу будет искать модель,
        своими словами.
        """
        question = clarifying_question(specialization="массаж")

        assert question is not None
        assert question.text == CLARIFY_SERVICE_QUESTION
        chips = _labels(question)
        assert CHIP in chips
        assert [chip for chip in chips if not decide(chip).claimed] == []

    def test_a_name_with_a_conjunction_is_one_service(self, contour: Tenant) -> None:
        """«и» — разделитель перечисления и обычное слово названия. Без
        каталога их не различить; с ним «Массаж лица и шеи» — одна услуга, а
        «массаж и маникюр» по-прежнему две."""
        named = decide("Массаж лица и шеи")
        enumerated = decide("массаж и маникюр")

        assert named.claimed is True
        assert named.reason == "catalog_service_name"
        assert enumerated.claimed is False
        assert enumerated.reason == "composite_request"

    def test_a_word_of_another_tool_still_hands_the_turn_over(self, contour: Tenant) -> None:
        """«салон» поиск отбрасывает как связку, и название узнаётся — но слово
        принадлежит ``show_salons``, и быстрый путь обязан отступить."""
        assert decide(CHIP).claimed is True

        decision = decide(f"салон {CHIP}")

        assert decision.claimed is False
        assert decision.reason == "unaccounted_words"
        assert "салон" in decision.residue

    def test_an_unknown_qualifier_that_names_no_service_goes_to_the_model(
        self, contour: Tenant
    ) -> None:
        """«Парный массаж» в этом контуре никто не оказывает."""
        assert decide(CHIP).claimed is True

        decision = decide("парный массаж")

        assert decision.claimed is False
        assert decision.residue == ("парный",)

    def test_a_question_about_the_service_is_not_a_master_request(self, contour: Tenant) -> None:
        assert decide(CHIP).claimed is True
        assert decide(f"сколько стоит {CHIP}").claimed is False

    def test_a_broken_catalog_read_hands_the_turn_over(
        self, contour: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert decide(CHIP).claimed is True

        def _explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("catalog is down")

        monkeypatch.setattr(discovery_mod, "named_bookable_service", _explode)

        assert decide(CHIP).claimed is False

    def test_the_plain_turn_still_reads_no_catalog(
        self, contour: Tenant, django_assert_num_queries
    ) -> None:
        """«хочу массаж» решается словарём, как и раньше."""
        with django_assert_num_queries(0):
            assert fast_path.claims_direct_show_masters("хочу массаж") is True


@_POSTGRES_ONLY
class TestTheDeterministicReply:
    def test_a_name_with_a_glue_word_gets_its_own_masters_too(self, contour: Tenant) -> None:
        """«Массаж лица и шеи»: запрос отбрасывает «и», название — нет."""
        name = "Массаж лица и шеи"

        reply = concierge_mod.generate_direct_show_masters_reply(name, trace_id=TRACE_ID)

        assert reply is not None
        lines = reply.text.split("\n")
        # Строка делит реплику на клаузы по «и» (так она отсекает клаузу про
        # здоровье) и склеивает их запятой: слова человека, его же порядок.
        assert lines[0] == "Искала по твоим словам: Массаж лица, шеи"
        assert [line.split(" · ")[0] for line in lines[2:]] == ["• Лицо и шея"]

    def test_the_chip_gets_the_masters_of_that_service(self, contour: Tenant) -> None:
        reply = concierge_mod.generate_direct_show_masters_reply(CHIP, trace_id=TRACE_ID)

        assert reply is not None
        lines = reply.text.split("\n")
        assert lines[0] == f"Искала по твоим словам: {CHIP}"
        assert lines[1] == SHOWN_HEADER
        shown = sorted(line for line in lines[2:] if line.startswith("• "))
        assert [line.split(" · ")[0] for line in shown] == [f"• {name}" for name in PERFORMS]
        assert all(CHIP in line for line in shown)

    def test_no_show_more_button_leads_into_other_massages(self, contour: Tenant) -> None:
        """Двое — это все. Кнопки «Показать ещё» быть не должно: за ней стояли
        бы массажисты, которые названную услугу не оказывают."""
        reply = concierge_mod.generate_direct_show_masters_reply(CHIP, trace_id=TRACE_ID)

        assert reply is not None
        assert _labels(reply) == [f"Записаться к {name}" for name in sorted(PERFORMS)]


# ---------------------------------------------------------------------------
# Страж DRF-968: строка модели потеряла слово названия
# ---------------------------------------------------------------------------


@_POSTGRES_ONLY
class TestRegroundWhenTheModelDropsAWord:
    def test_the_dropped_qualifier_is_restored(self, contour: Tenant) -> None:
        """Ход 09.11: названа услуга, модель искала её родовое слово."""
        assert reground_specialization(message_text=CHIP, city=None, specialization=DROPPED) == CHIP

    def test_another_service_sharing_a_word_does_not_stand_either(self, contour: Tenant) -> None:
        assert (
            reground_specialization(
                message_text=CHIP, city=None, specialization="классический массаж"
            )
            == CHIP
        )

    def test_the_catalog_spelling_comes_back_not_the_turn(self, contour: Tenant) -> None:
        """Строка поиска едет в журнал и в ссылки кнопок — текста человека в
        ней быть не должно. Цифры из трёх знаков разбор отбрасывает как
        короткие слова, и реплика с ними проходит ворота названия."""
        turn = "905 123 биоэнергетического массажа"

        assert named_bookable_service(turn) == CHIP
        assert reground_specialization(message_text=turn, city=None, specialization=DROPPED) == CHIP
        assert reground_specialization(message_text=turn, city=None, specialization=CHIP) == CHIP

    def test_a_city_named_in_the_turn_is_kept_in_the_catalogs_spelling(
        self, contour: Tenant
    ) -> None:
        turn = "биоэнергетический массаж в пензе"

        assert (
            reground_specialization(message_text=turn, city=None, specialization=DROPPED)
            == f"{CHIP} Пенза"
        )
        # Город уже назван моделью — второй раз в строку он не идёт.
        assert (
            reground_specialization(message_text=turn, city="Пенза", specialization=DROPPED) == CHIP
        )

    def test_an_ordinary_normalization_is_left_alone(self, contour: Tenant) -> None:
        """«хочу массаж завтра» → «массаж»: слово потеряно, но «массаж завтра» —
        не название услуги. Положительная проверка — два теста выше."""
        assert (
            reground_specialization(
                message_text="хочу массаж завтра", city=None, specialization=DROPPED
            )
            == DROPPED
        )

    def test_a_reading_that_keeps_every_word_reads_no_catalog(
        self, contour: Tenant, django_assert_num_queries
    ) -> None:
        """Модель вправе сузить запрос; пока все слова реплики на месте, каталог
        не читается вовсе."""
        with django_assert_num_queries(0):
            assert (
                reground_specialization(
                    message_text="массаж", city=None, specialization="классический массаж"
                )
                == "классический массаж"
            )

    def test_a_broken_catalog_read_keeps_the_models_string(
        self, contour: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert reground_specialization(message_text=CHIP, city=None, specialization=DROPPED) == CHIP

        def _explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("catalog is down")

        monkeypatch.setattr(discovery_mod, "_named_service_and_cities", _explode)

        assert (
            reground_specialization(message_text=CHIP, city=None, specialization=DROPPED) == DROPPED
        )


# ---------------------------------------------------------------------------
# Слова модели над карточками
# ---------------------------------------------------------------------------


@_POSTGRES_ONLY
class TestUnverifiedServiceWords:
    PROSE = "Нашёл мастеров, которые предлагают биоэнергетический массаж."

    def test_a_service_word_the_search_dropped_is_caught(self, contour: Tenant) -> None:
        assert unverified_service_words(CHIP, self.PROSE, searched=[DROPPED]) == ["биоэне"]

    def test_the_same_prose_over_the_right_search_stands(
        self, contour: Tenant, django_assert_num_queries
    ) -> None:
        """Искали названное — каталог не читается, текст остаётся."""
        assert unverified_service_words(CHIP, self.PROSE, searched=[DROPPED]) == ["биоэне"]
        with django_assert_num_queries(0):
            assert unverified_service_words(CHIP, self.PROSE, searched=[CHIP, "Пенза"]) == []

    def test_prose_that_does_not_repeat_the_word_stands(self, contour: Tenant) -> None:
        assert unverified_service_words(CHIP, self.PROSE, searched=[DROPPED]) == ["биоэне"]
        assert unverified_service_words(CHIP, "Вот кто может подойти.", searched=[DROPPED]) == []

    def test_a_long_turn_is_read_to_its_first_word(self, contour: Tenant) -> None:
        """Услуга названа в начале длинной реплики — поиск режет фразу до
        последних пяти слов, этот страж не режет."""
        turn = "запишите на биоэнергетический массаж на завтра после шести вечера, если можно"

        assert unverified_service_words(turn, self.PROSE, searched=[DROPPED]) == ["биоэне"]

    def test_words_that_name_no_service_are_not_judged(self, contour: Tenant) -> None:
        """«завтра» и город в падеже модель вправе повторить: услугу ими не пообещать."""
        assert unverified_service_words(CHIP, self.PROSE, searched=[DROPPED]) == ["биоэне"]
        assert (
            unverified_service_words(
                "массаж в Пензе завтра",
                "Завтра в Пензе тебя ждут мастера массажа.",
                searched=[DROPPED, "Пенза"],
            )
            == []
        )

    def test_a_broken_catalog_read_keeps_the_prose(
        self, contour: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import apps.marketplace.discovery as marketplace_discovery

        assert unverified_service_words(CHIP, self.PROSE, searched=[DROPPED]) == ["биоэне"]

        def _explode(_stems: object) -> None:
            raise RuntimeError("catalog is down")

        monkeypatch.setattr(marketplace_discovery, "stems_used_in_service_names", _explode)

        assert (
            cards_prose_guard.unverified_service_words(CHIP, self.PROSE, searched=[DROPPED]) == []
        )


class TestRecapNamesTheServiceOnTheCards:
    """Строка «по твоим словам» — без базы: это чистая функция реплики и карточек."""

    def test_the_named_service_is_quoted_in_full(self) -> None:
        assert service_fragment(CHIP, shown_services=[CHIP, CHIP]) == CHIP

    def test_without_cards_of_that_service_the_dictionary_decides(self) -> None:
        assert service_fragment(CHIP) == "массаж"
        assert service_fragment(CHIP, shown_services=["Классический массаж"]) == "массаж"

    def test_the_line_retells_the_search_not_one_card(self) -> None:
        """Модель искала «массаж», одна карточка из пяти несёт названную услугу.
        Строка говорит, по чему искали; назвать услугу целиком значило бы
        пообещать её остальным четверым."""
        turn = "хочу биоэнергетический массаж в субботу"

        assert service_fragment(turn, shown_services=[CHIP], searched=CHIP) == (
            "биоэнергетический массаж"
        )
        assert service_fragment(turn, shown_services=[CHIP], searched="массаж") == "массаж"

    def test_words_the_person_did_not_say_never_enter_the_line(self) -> None:
        """Карточка «Массаж лица и шеи» под репликой «массаж» ничего не добавляет."""
        assert service_fragment("хочу массаж", shown_services=["Массаж лица и шеи"]) == "массаж"

    def test_a_specialist_word_stays_out_whatever_the_cards_say(self) -> None:
        """«массажистов» называет человека, а не услугу (ayla-4c, 15.09)."""
        assert service_fragment("хочу массаж", shown_services=["Массаж"]) == "массаж"
        assert service_fragment("покажи массажистов", shown_services=["Массаж"]) is None


# ---------------------------------------------------------------------------
# Живой путь: консьерж
# ---------------------------------------------------------------------------


def _bot_user_and_conversation():
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="chip-0911-uid", chat_id="chip-0911-chat"
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


# ``transaction=True`` — как в суите DRF-968: консьерж крутит ход через
# ``asyncio.run`` и ходит в БД через ``sync_to_async``.
@_POSTGRES_ONLY
@pytest.mark.django_db(transaction=True)
class TestTheLivePath:
    def _run(self, monkeypatch, turn: str, args: dict, prose: str) -> dict:
        provider = AsyncMock()
        provider.complete = AsyncMock(side_effect=[_show_masters(args), _text(prose)])
        provider.default_completion_model = "gpt-4o-mini"
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge_mod, "get_router", lambda: router)

        seen: dict = {}
        real_search = concierge_mod.discover_masters

        def search_spy(**kwargs):
            seen["search"] = dict(kwargs)
            cards = real_search(**kwargs)
            seen["cards"] = sorted(card.name for card in cards)
            return cards

        monkeypatch.setattr(concierge_mod, "discover_masters", search_spy)

        real_message = concierge_mod._build_tool_result_message

        def message_spy(user_text, cards, tool_args, **kwargs):
            seen["told_the_model"] = real_message(user_text, cards, tool_args, **kwargs)
            return seen["told_the_model"]

        monkeypatch.setattr(concierge_mod, "_build_tool_result_message", message_spy)

        bot_user, conversation = _bot_user_and_conversation()
        reply = concierge_mod.generate_concierge_reply(
            turn, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )
        seen["reply"] = reply
        return seen

    def test_the_chip_reaches_the_catalog_whole(self, monkeypatch, contour) -> None:
        """Ход 09.11 целиком: модель потеряла слово, каталог получил название."""
        prose = "Нашёл мастеров, которые предлагают биоэнергетический массаж."
        seen = self._run(monkeypatch, CHIP, {"city": "Пенза", "specialization": DROPPED}, prose)

        assert seen["search"]["specialization"] == CHIP
        assert seen["search"]["named_service_only"] is True
        assert seen["cards"] == PERFORMS
        # Второму проходу сказано, что искали названную услугу, — и его слова
        # о ней теперь правда, поэтому остаются.
        assert f"услуга: {CHIP}" in seen["told_the_model"]
        text = seen["reply"].text
        assert text.startswith(prose)
        assert text.endswith(f"Искала по твоим словам: {CHIP}")
        assert sorted(_labels(seen["reply"])) == [f"Записаться к {name}" for name in PERFORMS]

    def test_prose_naming_a_service_the_search_dropped_is_replaced(
        self, monkeypatch, contour
    ) -> None:
        """«массаж лица» — не название услуги контура, модель вправе искать
        «массаж». Но написать над этим списком «массаж лица» она не вправе."""
        prose = "Нашла мастеров, которые делают массаж лица."
        seen = self._run(
            monkeypatch, "массаж лица", {"city": "Пенза", "specialization": DROPPED}, prose
        )

        assert seen["search"]["specialization"] == DROPPED
        text = seen["reply"].text
        assert SHOWN_HEADER in text
        assert text.split("\n")[0] == "Искала по твоим словам: массаж"
        assert "делают массаж лица" not in text
        assert len(_labels(seen["reply"])) >= 5

    def test_prose_that_claims_nothing_keeps_the_models_words(self, monkeypatch, contour) -> None:
        """Положительная пара к замене: те же реплика и поиск, текст без
        непроверенной услуги — остаётся как есть."""
        prose = "Вот кто может подойти."
        seen = self._run(
            monkeypatch, "массаж лица", {"city": "Пенза", "specialization": DROPPED}, prose
        )

        assert seen["reply"].text.startswith(prose)

    def test_the_first_page_and_show_more_run_the_same_search(self, monkeypatch, contour) -> None:
        """«Показать ещё» перезапускает поиск из ``fetch_master_page``. Флаг
        среза обязан совпадать на обеих страницах, иначе вторая считает сдвиг
        по другому списку."""
        seen = self._run(
            monkeypatch,
            "массаж и маникюр",
            {
                "city": "Пенза",
                "specialization": "массаж",
                "services": ["массаж", "маникюр"],
            },
            "Вот кто может подойти.",
        )
        first_page_flag = seen["search"]["named_service_only"]

        paged: dict = {}

        def page_spy(**kwargs):
            paged.update(kwargs)
            return []

        monkeypatch.setattr(discovery_mod, "discover_masters", page_spy)
        discovery_mod.fetch_master_page(city="Пенза", specialization="массаж", offset=5)

        assert first_page_flag is True
        assert paged["named_service_only"] is first_page_flag
