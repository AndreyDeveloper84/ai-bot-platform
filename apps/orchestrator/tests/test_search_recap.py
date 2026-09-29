"""Строка «Искала по твоим словам» над показом мастеров в DM (DRF-1908).

Условия (владелец 24.08 OD_C04_GROUNDED_WHY; форма и место — ayla-4c 15.09),
каждое своим тестом:
* только слова человека: услуга дословно, город — названный им в этом пути,
  «когда удобно» — закрытым словарём — ``TestRecapParts``;
* без смысла здоровья и без цифр — ``TestNothingElseGetsIn``;
* объяснять нечем → строки нет — ``TestEmptyMeansNoLine``;
* строка — прямо над списком мастеров, на обоих путях — ``TestPlacement``.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.conversations.models import Message
from apps.conversations.services import record_global_message, resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator import concierge, search_recap
from apps.orchestrator.search_recap import RECAP_PREFIX, render_search_recap, service_fragment

# transaction=True: консьерж исполняет модель через asyncio в отдельном потоке,
# и нетранзакционный тест держит блокировку таблицы на sqlite (как у
# test_concierge_multipass).
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _known_cities(monkeypatch):
    monkeypatch.setattr("apps.marketplace.discovery._known_cities", lambda: ["Пенза", "Самара"])


def _conversation():
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="drf1908", chat_id="drf1908"
    )
    return resolve_active_global_conversation(bot_user)


def _said(conversation, text: str, *, hours_ago: float = 0) -> None:
    from apps.tenancy.context import tenant_scope

    with tenant_scope(conversation.tenant):
        row = record_global_message(conversation, role="user", content=text)
    if hours_ago:
        Message.all_tenants.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(hours=hours_ago)
        )


def _card():
    return SimpleNamespace(
        tenant_id="t1",
        master_id="m1",
        name="Анна",
        specialization="Массаж",
        rating=None,
        city="Пенза",
        service_id=None,
        service_name="",
    )


class TestRecapParts:
    def test_owner_like_turn_gives_service_city_and_when(self):
        line = render_search_recap(
            "хочу массаж в Пензе после работы", city="Пенза", specialization="массаж"
        )
        assert line == "Искала по твоим словам: массаж · Пенза · после работы"

    def test_city_the_model_chose_itself_is_not_his_words(self):
        line = render_search_recap("хочу массаж", city="Пенза", specialization="массаж")
        assert line == "Искала по твоим словам: массаж"

    def test_city_named_earlier_on_this_path_counts(self):
        conversation = _conversation()
        _said(conversation, "я в Пензе")
        line = render_search_recap(
            "хочу массаж", conversation, city="Пенза", specialization="массаж"
        )
        assert line == "Искала по твоим словам: массаж · Пенза"

    def test_city_named_outside_the_two_hour_path_does_not(self):
        conversation = _conversation()
        _said(conversation, "я в Пензе", hours_ago=3)
        line = render_search_recap(
            "хочу массаж", conversation, city="Пенза", specialization="массаж"
        )
        assert line == "Искала по твоим словам: массаж"

    def test_service_the_model_did_not_search_for_is_not_named(self):
        line = render_search_recap("хочу маникюр в Пензе", city="Пенза", specialization="массаж")
        assert line == "Искала по твоим словам: Пенза"

    def test_fast_path_takes_the_city_from_the_turn_itself(self):
        line = render_search_recap("массаж в пензе", specialization="массаж в пензе")
        assert line == "Искала по твоим словам: массаж · Пенза"


class TestNothingElseGetsIn:
    def test_health_clause_gives_no_word(self):
        line = render_search_recap(
            "ноет спина, хочу массаж в Пензе", city="Пенза", specialization="массаж"
        )
        assert line == "Искала по твоим словам: массаж · Пенза"
        assert "спин" not in line and "ноет" not in line

    def test_service_word_inside_a_health_clause_is_dropped_with_the_clause(self):
        """Здесь в клаузе со здоровьем ЕСТЬ слово услуги — фильтр виден по исходу."""
        assert service_fragment("после массажа болит спина, хочу стрижку") == "стрижку"

    @pytest.mark.parametrize(
        "turn",
        [
            "покажи массажистов",
            "нужен косметолог",
            "найди маникюрщицу",
            "хочу к массажистке",
            # «парикмахера» в словаре услуг нет вовсе — фрагмента нет и без отсева;
            # стоит здесь, чтобы отсутствие было названо, а не подразумевалось.
            "нужен парикмахер",
        ],
    )
    def test_a_specialist_is_not_a_service(self, turn):
        """ayla-4c 15.09: название человека не даёт фрагмента и не перефразируется."""
        # empty-assert-ok: реплики-люди по построению без фрагмента; соседний параметризованный тест доказывает, что услуги фрагмент дают
        assert service_fragment(turn) is None

    @pytest.mark.parametrize(
        ("turn", "fragment"),
        [
            ("массаж после работы", "массаж"),
            ("хочу маникюр", "маникюр"),
            # Слово человека в той же фразе, что и «мастера», — законный фрагмент.
            ("ищу мастера маникюра", "маникюра"),
            # «ист» внутри слова услуги — не хвост специалиста.
            ("чистка лица", "чистка"),
        ],
    )
    def test_a_service_word_still_gives_the_fragment(self, turn, fragment):
        assert service_fragment(turn) == fragment

    def test_digits_never_reach_the_line(self):
        assert service_fragment("массаж 89991234567 срочно") == "массаж"

    def test_long_fragment_is_clipped_by_word(self):
        clipped = search_recap._clip_by_word("массаж, маникюр, педикюр, стрижка, окрашивание", 25)
        assert len(clipped) <= 25
        assert clipped == "массаж, маникюр, педикюр"

    def test_the_line_never_claims_a_recommendation(self):
        assert RECAP_PREFIX == "Искала по твоим словам: "
        for word in ("подобрала", "подходит", "рекоменд", "важн"):
            assert word not in RECAP_PREFIX.lower()


class TestEmptyMeansNoLine:
    def test_nothing_the_person_said_means_no_line(self):
        assert render_search_recap("покажи", city="Пенза", specialization=None) is None

    def test_a_failing_read_costs_the_line_not_the_turn(self, monkeypatch):
        def _boom(_text):
            raise RuntimeError("catalog down")

        monkeypatch.setattr(search_recap, "service_fragment", _boom)
        assert render_search_recap("хочу массаж", specialization="массаж") is None


class TestPlacement:
    def test_direct_path_puts_the_line_directly_above_the_list(self, monkeypatch):
        monkeypatch.setattr(concierge, "claims_direct_show_masters", lambda _text: True)
        monkeypatch.setattr(concierge, "clarifying_question", lambda **_kwargs: None)
        monkeypatch.setattr(concierge, "discover_masters", lambda **_kwargs: [_card()])

        reply = concierge.generate_direct_show_masters_reply(
            "массаж в пензе", conversation=_conversation()
        )

        lines = reply.text.split("\n")
        assert lines[0] == "Искала по твоим словам: массаж · Пенза"
        assert lines[1] == "Вот мастера, которые могут подойти:"
        assert lines[2].startswith("• Анна")

    def _model(self, monkeypatch, *results):
        from unittest.mock import AsyncMock, Mock

        provider = AsyncMock()
        provider.complete.side_effect = list(results)
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        monkeypatch.setattr(concierge, "discover_masters", lambda **_kwargs: [_card()])

    def _show_masters(self):
        from apps.llm.protocol import CompletionResult, ToolCall

        return CompletionResult(
            text="",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="show_masters",
                    arguments={"city": "Пенза", "specialization": "массаж"},
                )
            ],
            prompt_tokens=10,
            completion_tokens=5,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="tool_calls",
        )

    def test_model_prose_keeps_its_words_and_the_line_is_last_above_the_keyboard(self, monkeypatch):
        from apps.llm.protocol import CompletionResult

        prose = CompletionResult(
            text="Нашла Анну — она делает массаж.",
            prompt_tokens=20,
            completion_tokens=8,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="stop",
        )
        self._model(monkeypatch, self._show_masters(), prose)
        conversation = _conversation()

        reply = concierge.generate_concierge_reply(
            "хочу массаж в Пензе после работы",
            bot_user=conversation.bot_user,
            conversation=conversation,
            trace_id="00000000-0000-4000-8000-000000001908",
        )

        assert reply.text.startswith("Нашла Анну — она делает массаж.")
        assert reply.text.split("\n")[-1] == (
            "Искала по твоим словам: массаж · Пенза · после работы"
        )
        assert reply.action_data["attachments"][0]["payload"]["buttons"]

    def test_deterministic_render_carries_the_line_above_the_list(self, monkeypatch):
        self._model(monkeypatch, self._show_masters(), self._show_masters())
        conversation = _conversation()

        reply = concierge.generate_concierge_reply(
            "хочу массаж в Пензе после работы",
            bot_user=conversation.bot_user,
            conversation=conversation,
            trace_id="00000000-0000-4000-8000-000000001909",
        )

        lines = reply.text.split("\n")
        at = lines.index("Вот мастера, которые могут подойти:")
        assert lines[at - 1] == "Искала по твоим словам: массаж · Пенза · после работы"

    def test_no_line_leaves_the_render_byte_identical(self, monkeypatch):
        from apps.orchestrator.discovery import _render_master_cards

        assert _render_master_cards([_card()]) == _render_master_cards([_card()], recap=None)
        assert _render_master_cards([_card()]).text.startswith("Вот мастера")
