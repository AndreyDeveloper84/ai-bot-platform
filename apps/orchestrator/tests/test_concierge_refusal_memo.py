"""DRF-1474 — the bot may not offer to find what it just said it has not.

## The transcript this file replays

Live pilot, 04.09, one minute (bot DB conversation
``6e8fdde2-d13b-4c3c-b064-842573e5fb9c``)::

    12:12:07  чел  Маникюр
    12:12:15  бот  «маникюр» в городе Пенза — такого у наших мастеров сейчас
                   нет. Назовите другую услугу или другой город, и я поищу ещё.
    12:12:16  чел  Маникюр
    12:12:20  бот  Помогу найти мастера по маникюру! Уточните, пожалуйста, в
                   каком городе вы находитесь?
    12:12:42  чел  пенза
    12:12:50  бот  «маникюр» в городе Пенза — такого у наших мастеров сейчас нет.

Both refusals were TRUE — the contour has no nail service and no master who
performs one. The middle sentence is the whole defect: it promises the thing
just refused and asks back the city the refusal itself had named. The person
supplied it and hit the same wall.

## Why the middle turn did not know — from the worker log, not from a guess

    12:12:12  concierge.show_masters count=0 city='Пенза' spec='маникюр' pass=1
    12:12:15  concierge.show_masters count=0 city='Пенза' spec='маникюр' pass=2
    12:12:15  concierge.multipass_budget_exhausted passes=2
    12:12:20  ai_concierge: action=text          ← no tool call anywhere

Two independent paths. The refusal is deterministic
(``render_no_match`` through the budget-exhausted render); the promise is the
model writing prose on a turn where it chose no tool at all. The zero was
measured, logged, and then dropped on the floor — nothing carried it into the
next turn but the transcript, and the transcript was not enough.

So the tests below pin the LINK, in the two forms the fix takes:

* :class:`TestTheRepeatIsAnsweredWithoutTheModel` — the exact repeat never
  reaches an LLM, so no prompt can be talked out of it;
* :class:`TestTheModelIsToldWhatWasRefused` — every other turn carries the
  fact into the system prompt, where it reads as an instruction rather than as
  one more message in a dozen.

Plus the two smaller halves of the same ticket:

* :class:`TestTheRefusalNamesTheAlternative` — «предлагаем другое» said in
  words, so a list of massage masters after a nail request can never read as a
  silent swap;
* :class:`TestTheRepeatIsNarrow` — the ledger answers only the question it was
  asked, or this fix becomes the defect with the sign flipped.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.discovery import render_alternatives, render_no_match
from apps.orchestrator.refusal_memo import (
    STATE_TTL_SECONDS,
    recall_refusals,
    remember_refusal,
    render_refusal_block,
)
from apps.tenancy.models import Tenant

TRACE_ID = str(uuid.uuid4())

# What the person typed, twice, eight seconds apart.
_ASKED = "Маникюр"


def _ts() -> datetime:
    return datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


def _show_masters_call(city: str = "Пенза", spec: str = "маникюр") -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[
            ToolCall(id="c1", name="show_masters", arguments={"city": city, "specialization": spec})
        ],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _text_result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _bot_user_and_conversation(suffix: str = "drf1474"):
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=f"{suffix}-uid",
        chat_id=f"{suffix}-chat",
    )
    return bot_user, resolve_active_global_conversation(bot_user)


@pytest.fixture
def penza_massage_only() -> Tenant:
    """The contour as it really was on 04.09: massage in Пенза, no nails.

    Two masters and two services, because ``city_service_samples`` ranks by
    how many bookable masters perform each — a fixture where every service has
    one master would pass whatever the ordering did.
    """
    tenant = Tenant.objects.create(slug="salon-penza-1474", name="SPAtrium", city="Пенза")
    masters = [
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            name=name,
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            external_updated_at=_ts(),
        )
        for name in ("Архипкин Денис", "Сазонова Инна")
    ]
    both = CatalogService.all_tenants.create(
        tenant=tenant,
        slug="klassika",
        name="Классический массаж",
        is_active=True,
        external_updated_at=_ts(),
    )
    one = CatalogService.all_tenants.create(
        tenant=tenant,
        slug="spina",
        name="Массаж спины",
        is_active=True,
        external_updated_at=_ts(),
    )
    for master in masters:
        MasterService.all_tenants.create(tenant=tenant, master=master, service=both)
    MasterService.all_tenants.create(tenant=tenant, master=masters[0], service=one)
    return tenant


# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheLedger:
    def test_a_refusal_is_remembered_and_read_back(self) -> None:
        _, conversation = _bot_user_and_conversation("ledger-1")
        remember_refusal(conversation, specialization="маникюр", city="Пенза")

        entries = recall_refusals(conversation)
        assert [(e.specialization, e.city) for e in entries] == [("маникюр", "Пенза")]

    def test_refusing_the_same_thing_twice_stores_one_entry(self) -> None:
        _, conversation = _bot_user_and_conversation("ledger-2")
        remember_refusal(conversation, specialization="маникюр", city="Пенза")
        remember_refusal(conversation, specialization="Маникюр", city="пенза")

        assert len(recall_refusals(conversation)) == 1

    def test_a_stale_entry_is_not_read_back(self) -> None:
        """The catalog may have grown since. A refusal is a hint, not a law."""
        _, conversation = _bot_user_and_conversation("ledger-3")
        remember_refusal(conversation, specialization="маникюр", city="Пенза")
        # The entry really is there before it is aged out — otherwise the
        # assertion below would pass on a ledger that never worked at all.
        assert len(recall_refusals(conversation)) == 1
        state = dict(conversation.skill_state)
        stale = datetime.now(timezone.utc) - timedelta(seconds=STATE_TTL_SECONDS + 60)
        state["no_match"][0]["at"] = stale.isoformat()
        conversation.skill_state = state
        conversation.save(update_fields=["skill_state"])

        assert recall_refusals(conversation) == []

    def test_a_refusal_with_no_service_is_not_recorded(self) -> None:
        """«В Пензе никого нет» is about the city — no query to repeat.

        Written as «one real refusal, then a blank one» so the assertion is
        about the blank being dropped rather than about a ledger that might
        simply never write anything.
        """
        _, conversation = _bot_user_and_conversation("ledger-4")
        remember_refusal(conversation, specialization="маникюр", city="Пенза")
        remember_refusal(conversation, specialization="", city="Пенза")

        entries = recall_refusals(conversation)
        assert [e.specialization for e in entries] == ["маникюр"]

    def test_the_block_states_the_fact_and_forbids_both_defects(self) -> None:
        _, conversation = _bot_user_and_conversation("ledger-5")
        remember_refusal(conversation, specialization="маникюр", city="Пенза")

        block = render_refusal_block(conversation)

        assert "«маникюр» в городе Пенза" in block
        assert "нет ни одного мастера" in block
        # The two sentences the live turn produced, forbidden by name.
        assert "Не обещай найти" in block
        assert "Не спрашивай город" in block

    def test_no_refusals_means_no_block(self) -> None:
        _, conversation = _bot_user_and_conversation("ledger-6")

        assert render_refusal_block(conversation) == ""


# ---------------------------------------------------------------------------
# Naming the substitution
# ---------------------------------------------------------------------------


class TestTheRefusalNamesTheAlternative:
    """Beда №3 of the ticket, restated as what the code can actually fix.

    The ticket reads «после отказа показал мастеров по массажу, не сказав, что
    это замена». The bot DB says otherwise: at 12:13:21 the PERSON typed
    «массаж», and the massage list is the answer to that word — the two rows
    carry the same second. There was no silent substitution; the transcript in
    the ticket simply omits the user turn between them.

    What IS real is the reason it read that way. The refusal ended at «назовите
    другую услугу или другой город» — a dead end that hands the person the job
    of guessing what this marketplace does, and gives the list they eventually
    reach nothing to be an alternative TO. So the fix is not a guard against a
    swap that never happened: it is the refusal naming, in words, what it can
    offer instead.
    """

    def test_alternatives_are_labelled_as_something_else(self) -> None:
        line = render_alternatives(
            ["Классический массаж", "Массаж спины"], service="маникюр", city="Пенза"
        )

        assert "а не «маникюр»" in line
        assert "в городе Пенза" in line
        assert "«Классический массаж»" in line

    def test_no_alternatives_means_no_sentence(self) -> None:
        """Inventing one is worse than admitting there is none."""
        assert render_alternatives([]) == ""
        assert render_alternatives(None) == ""

    def test_the_refusal_keeps_its_old_wording_without_alternatives(self) -> None:
        """The pre-DRF-1474 line where nothing can be offered — plus, since
        DRF-1492, the one move that is always available.

        The refusal itself is unchanged, word for word: it still names back
        what was searched for and asks only for what was not given. What the
        later ticket added is the tail and the chip behind it, because
        «назовите другой город» on its own is a request to guess which cities
        this marketplace is in.
        """
        reply = render_no_match(city="Пенза", specialization="маникюр")

        assert reply.text == (
            "«маникюр» в городе Пенза — такого у наших мастеров сейчас нет. "
            "Назовите другую услугу или другой город, и я поищу ещё."
            " Или посмотрите, какие салоны есть."
        )
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert [b["callback"] for b in buttons] == ["cb:catalog:salons"]

    def test_the_refusal_carries_the_alternative_when_there_is_one(self) -> None:
        reply = render_no_match(
            city="Пенза",
            specialization="маникюр",
            alternatives=["Классический массаж"],
        )

        assert reply.text.startswith("«маникюр» в городе Пенза — такого")
        assert "Это другие услуги, а не «маникюр»" in reply.text


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTheRepeatIsAnsweredWithoutTheModel:
    def test_the_live_scenario_end_to_end(self, monkeypatch, penza_massage_only) -> None:
        """«Маникюр» → refusal. «Маникюр» again → NOT «помогу найти».

        Turn one is the live one, model call for model call: two
        ``show_masters`` passes over an empty catalog result, ending in the
        budget-exhausted deterministic render. Turn two is the defect.
        """
        provider = AsyncMock()
        provider.complete.side_effect = [_show_masters_call(), _show_masters_call()]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [])
        bot_user, conversation = _bot_user_and_conversation("live-1")

        first = generate_concierge_reply(
            _ASKED, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        assert "«маникюр» в городе Пенза" in first.text
        assert "нет" in first.text
        assert provider.complete.await_count == 2

        conversation.refresh_from_db()
        second = generate_concierge_reply(
            _ASKED, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        # No third model call: the repeat never reaches a model, so no prompt
        # can be talked out of the answer.
        assert provider.complete.await_count == 2
        # What the second answer IS, asserted first: every «not in» below is
        # about this text, and a check of absence over an empty reply would
        # pass while proving nothing.
        assert "я уже ответил" in second.text
        assert "«маникюр»" in second.text
        assert second.persisted is True
        # And what it is not — the sentence the owner read, plus the two
        # things that turned it into a circle.
        assert "Помогу найти" not in second.text
        assert "в каком городе" not in second.text.lower()
        assert "уточните" not in second.text.lower()

    def test_the_repeat_still_offers_the_alternative(self, monkeypatch, penza_massage_only) -> None:
        """A repeat must go somewhere, or it is just a wall said twice."""
        provider = AsyncMock()
        provider.complete.side_effect = [_show_masters_call(), _show_masters_call()]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [])
        bot_user, conversation = _bot_user_and_conversation("live-2")

        generate_concierge_reply(
            _ASKED, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )
        conversation.refresh_from_db()
        second = generate_concierge_reply(
            "маникюр", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        assert "Классический массаж" in second.text
        assert "Это другие услуги" in second.text


@pytest.mark.django_db(transaction=True)
class TestTheModelIsToldWhatWasRefused:
    def test_the_next_prompt_carries_the_refusal(self, monkeypatch, penza_massage_only) -> None:
        """The link, for every turn the deterministic branch does not own.

        A turn that is NOT a repeat still goes to the model — and the model
        now reads the refusal as a system-prompt fact, which is the difference
        between «two paths that never meet» and one that does.
        """
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_call(),
            _show_masters_call(),
            _text_result("Хорошо, посмотрим что-нибудь другое."),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [])
        bot_user, conversation = _bot_user_and_conversation("prompt-1")

        generate_concierge_reply(
            _ASKED, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )
        conversation.refresh_from_db()
        generate_concierge_reply(
            "а что вы вообще умеете",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        system = provider.complete.call_args_list[-1].args[0][0]
        assert system["role"] == "system"
        assert "«маникюр» в городе Пенза" in system["content"]
        assert "Не спрашивай город" in system["content"]

    def test_a_clean_conversation_adds_nothing_to_the_prompt(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = [_text_result("Привет!")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation("prompt-2")

        generate_concierge_reply(
            "привет", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        system = provider.complete.call_args_list[0].args[0][0]
        # A real system prompt was built — otherwise «not in ""» would pass
        # over an empty string and this test would guard nothing.
        assert "Ответ не длиннее" in system["content"]
        assert "Уже проверено по каталогу" not in system["content"]


@pytest.mark.django_db(transaction=True)
class TestTheRepeatIsNarrow:
    """The ledger may answer only the question it was actually asked."""

    def _refuse_manicure(self, monkeypatch, suffix: str):
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_call(),
            _show_masters_call(),
            _text_result("что-то ещё"),
            _text_result("и ещё"),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [])
        bot_user, conversation = _bot_user_and_conversation(suffix)
        generate_concierge_reply(
            _ASKED, bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )
        conversation.refresh_from_db()
        return provider, bot_user, conversation

    def test_a_different_service_still_reaches_the_model(
        self, monkeypatch, penza_massage_only
    ) -> None:
        """«массаж» after a refused «маникюр» is a new question."""
        provider, bot_user, conversation = self._refuse_manicure(monkeypatch, "narrow-1")

        generate_concierge_reply(
            "массаж", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        assert provider.complete.await_count == 3

    def test_a_different_city_still_reaches_the_model(
        self, monkeypatch, penza_massage_only
    ) -> None:
        """The catalog was asked about Пенза. Самара was never asked."""
        Tenant.objects.create(slug="salon-samara-1474", name="Salon", city="Самара")
        CatalogMaster.all_tenants.create(
            tenant=Tenant.objects.get(slug="salon-samara-1474"),
            name="Мастер Самара",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            external_updated_at=_ts(),
        )
        provider, bot_user, conversation = self._refuse_manicure(monkeypatch, "narrow-2")

        generate_concierge_reply(
            "маникюр в самаре",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert provider.complete.await_count == 3
