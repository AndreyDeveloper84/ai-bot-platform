"""DRF-2829 — предпочтение мастера в чате (решение владельца 06.10).

* «предпочитаю Анну» — мягко: Анна первой, остальные остаются;
* «только Анна» — жёстко: другим мастером не отвечать;
* у Анны нет подходящего — объяснить и СПРОСИТЬ разрешения на других, кнопкой.

Каталог настоящий (Postgres): кто есть и кто что делает, решает
``find_masters_by_name`` / ``_bookable_qs``, а не подмена. Модель — заскриптованный
вызов инструмента, потому что вопрос узлов не «что выберет модель», а «что
платформа сделает с её выбором».
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, master_preference
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.discovery import CALLBACK_DISCOVER_MORE_PREFIX, decode_more_ref
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; on SQLite the negative "
        "assertions would pass vacuously.",
    ),
]

TRACE_ID = str(uuid.uuid4())


def _ts() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid4(),
    )


def _does(tenant: Tenant, master: CatalogMaster, service_name: str) -> None:
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        slug=f"svc-{uuid4().hex[:8]}",
        name=service_name,
        is_active=True,
        external_updated_at=_ts(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def salon() -> Tenant:
    """Анна делает маникюр, но не педикюр; Алла и Алина — маникюр и педикюр."""

    tenant = Tenant.objects.create(slug="salon-2829", name="Salon 2829", city="Пенза")
    anna = _master(tenant, "Анна Смирнова")
    _does(tenant, anna, "Маникюр классический")
    for name in ("Алла Ветрова", "Алина Котова"):
        other = _master(tenant, name)
        _does(tenant, other, "Маникюр классический")
        _does(tenant, other, "Педикюр")
    return tenant


def _buttons(reply) -> list[dict]:
    """Все кнопки ответа в обеих формах: следующий шаг (``buttons``) и
    клавиатура карточек (``attachments[].payload.buttons``)."""

    data = reply.action_data or {}
    out = list(data.get("buttons") or [])
    for attachment in data.get("attachments") or []:
        out.extend((attachment.get("payload") or {}).get("buttons") or [])
    return out


def _names(reply) -> list[str]:
    """Мастера на кнопках записи — то, к кому человек может записаться нажатием."""

    return [
        b["label"].removeprefix("Записаться к ")
        for b in _buttons(reply)
        if str(b.get("callback", "")).startswith("cb:discover:book:")
    ]


def _callbacks(reply) -> list[str]:
    return [str(b.get("callback", "")) for b in _buttons(reply)]


def _other_masters_named(reply, *others: str) -> list[str]:
    """Чужие имена где угодно в ответе — в тексте или на кнопках."""

    blob = reply.text + " ".join(str(b) for b in _buttons(reply))
    return [name for name in others if name.split()[0] in blob]


@pytest.mark.django_db
class TestHard:
    def test_only_anna_who_does_it_is_the_only_master_shown(self, salon) -> None:
        reply = master_preference.hard_reply("Анне", city=None, specialization="маникюр")
        assert _names(reply) == ["Анна Смирнова"]
        assert _other_masters_named(reply, "Алла Ветрова", "Алина Котова") == []

    def test_only_anna_for_a_service_she_does_not_do_asks_instead_of_booking(self, salon) -> None:
        """«только к Анне на педикюр»: Анна есть, педикюра у неё нет."""
        reply = master_preference.hard_reply("Анне", city=None, specialization="педикюр")

        assert (
            reply.text
            == "У мастера «Анне» не нашла «педикюр». Показать других мастеров по «педикюр»?"
        )
        assert _names(reply) == [], "записи к Анне без услуги быть не должно"
        assert _other_masters_named(reply, "Алла Ветрова", "Алина Котова") == []
        more = [c for c in _callbacks(reply) if c.startswith(CALLBACK_DISCOVER_MORE_PREFIX)]
        assert len(more) == 1, "разрешение на других — одна кнопка"
        assert decode_more_ref(more[0][len(CALLBACK_DISCOVER_MORE_PREFIX) :]) == (
            0,
            None,
            "педикюр",
        )

    def test_nobody_of_that_name_asks_and_shows_nobody(self, salon) -> None:
        reply = master_preference.hard_reply("Кире", city=None, specialization="маникюр")

        assert reply.text == "Не нашла мастера «Кире». Показать других мастеров по «маникюр»?"
        assert _names(reply) == []
        assert _other_masters_named(reply, "Анна Смирнова", "Алла Ветрова", "Алина Котова") == []

    def test_nobody_and_nothing_to_search_by_offers_salons_not_the_directory(self, salon) -> None:
        reply = master_preference.hard_reply("Кире", city=None, specialization=None)

        assert _callbacks(reply), "без тупика: салоны и меню (§72)"
        assert not any(c.startswith(CALLBACK_DISCOVER_MORE_PREFIX) for c in _callbacks(reply))

    def test_two_annas_is_a_question_not_a_choice(self, salon) -> None:
        second = _master(salon, "Анна Белова")
        _does(salon, second, "Маникюр классический")

        reply = master_preference.hard_reply("Анну", city=None, specialization="маникюр")

        assert reply.text.startswith(master_preference.SEVERAL_TEXT)
        assert sorted(_names(reply)) == ["Анна Белова", "Анна Смирнова"]


@pytest.mark.django_db
class TestSoft:
    def test_preferred_anna_goes_first_and_the_others_stay(self, salon) -> None:
        from apps.marketplace.discovery import discover_masters

        cards = discover_masters(specialization="маникюр", limit=5)
        # Без предпочтения Анна НЕ первая (порядок каталога — по имени), иначе
        # узел прошёл бы и без поднятия.
        assert [c.name for c in cards][0] != "Анна Смирнова"

        lifted = master_preference.lift_named(
            cards, "Анну", city=None, specialization="маникюр", limit=5
        )

        assert [c.name for c in lifted][0] == "Анна Смирнова"
        assert sorted(c.name for c in lifted) == ["Алина Котова", "Алла Ветрова", "Анна Смирнова"]

    def test_soft_does_not_lift_a_master_who_does_not_do_it(self, salon) -> None:
        from apps.marketplace.discovery import discover_masters

        cards = discover_masters(specialization="педикюр", limit=5)
        lifted = master_preference.lift_named(
            cards, "Анну", city=None, specialization="педикюр", limit=5
        )
        assert [c.name for c in lifted] == [c.name for c in cards]
        assert "Анна Смирнова" not in [c.name for c in lifted]


class TestStrength:
    @pytest.mark.parametrize(
        "text", ["только к Анне на маникюр", "хочу именно Анну", "Исключительно к Анне"]
    )
    def test_the_persons_word_makes_it_hard_whatever_the_model_set(self, text: str) -> None:
        assert master_preference.is_hard({"master_strength": "soft"}, text)

    def test_preference_words_stay_soft(self) -> None:
        assert not master_preference.is_hard({}, "лучше бы к Анне, предпочитаю её")

    def test_the_model_can_mark_it_hard(self) -> None:
        assert master_preference.is_hard({"master_strength": "hard"}, "к Анне")


# ─── через ход консьержа ─────────────────────────────────────────────────────


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


def _tool_result(name: str, args: dict) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _bot_user_and_conversation():
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="drf2829-uid", chat_id="drf2829-chat"
    )
    return bot_user, resolve_active_global_conversation(bot_user)


@pytest.mark.django_db(transaction=True)
class TestThroughTheConciergeTurn:
    def test_show_masters_only_anna_for_pedicure_is_answered_without_a_second_pass(
        self, salon, monkeypatch
    ) -> None:
        """Пустой жёсткий результат не уходит модели: проход над ним и был
        местом, где «Анны нет» становилось чужими карточками."""
        provider = AsyncMock()
        provider.complete.return_value = _tool_result(
            "show_masters", {"specialization": "педикюр", "master": "Анне"}
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "только к Анне на педикюр",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert provider.complete.await_count == 1
        assert reply.text.startswith("У мастера «Анне» не нашла «педикюр»")
        assert _other_masters_named(reply, "Алла Ветрова", "Алина Котова") == []

    def test_start_booking_for_a_service_she_does_not_do_asks_instead_of_booking(
        self, salon, monkeypatch
    ) -> None:
        provider = AsyncMock()
        provider.complete.return_value = _tool_result(
            "start_booking", {"master": "Анне", "service": "педикюр"}
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        handoff = Mock()
        monkeypatch.setattr(concierge, "handoff_to_booking", handoff)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "запиши к Анне на педикюр",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        handoff.assert_not_called()
        assert reply.text.startswith("У мастера «Анне» не нашла «педикюр»")
        assert any(c.startswith(CALLBACK_DISCOVER_MORE_PREFIX) for c in _callbacks(reply))
