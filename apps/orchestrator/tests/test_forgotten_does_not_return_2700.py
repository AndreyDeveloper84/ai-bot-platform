"""DRF-2700 — забытое не возвращается к модели через историю, окно Redis и анкету.

Решение владельца 07.10.2026 (лист решений, п.23): после «забудь X» факт и его
следы исключаются из будущего контекста; после отзыва согласия на предположения
прошлые ответы не используются; остальная переписка остаётся видимой человеку;
если запрещённое нельзя надёжно отделить — затронутая история модели не
передаётся.

Замер до правки (``4d41806f``):

* реплика с предположением отдавалась модели после отзыва согласия на них;
* после «забудь про бюджет» Ayla отвечала «забыла», а цена оставалась в анкете,
  уходила модели и показывалась в «покажи, что знаешь».

Узлы:

* k1 — отсечка: из чего складывается, чего не захватывает, назад не идёт;
* k2 — отсечку ставят двери поштучного стирания по просьбе и только они;
* k3 — четыре читателя истории из базы: до отсечки не отдают, после — отдают,
  близнец без отсечки отдаёт всё;
* k4 — отказ чтения отсечки закрывает каждого читателя;
* k5 — сквозной, через ``GlobalMaxHandler``: после «забудь, что я …» ни слова
  человека, ни ответ «забыла, что ты …» модели не уходят; строки переписки целы;
* k6 — отзыв согласия на предположения и стирание с экрана памяти очищают окно
  Redis и состояние подбора;
* k7 — анкета: забытая цена не читается ни блоком памяти, ни показом; названная
  заново — читается; хранится имя поля, без значения.

Данные синтетические.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.consent.models import ConsentRecord
from apps.consent.services import model_history_cutoff, record_global_consent
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.models import BotUser, MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services import model_history_cutoff as memory_cutoff
from apps.identity.services.memory_deleter import soft_delete_green_entries
from apps.identity.services.memory_reader import read_green_entries
from apps.identity.services.red_zone_reader import RedZoneReader
from apps.integrations.ayla.personal_context_client import DeclaredContext
from apps.orchestrator import concierge, memory_block
from apps.orchestrator.concierge import GlobalConversationStore
from apps.orchestrator.memory.personal_context import record_explicit_green_facts
from apps.orchestrator.tests.test_history_consent_cutoff_2700 import (
    _Chat,
    _entry,
    _grant,
    _person,
    _say,
    _withdrawn,
)
from apps.persona.memory_commands import handle_memory_command

CT = ConsentRecord.ConsentType
PI = CT.PREFERENCE_INFERENCE.value

T_SAID = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
T_CUT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
T_LATER = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
BEFORE = "ZETA-2700 сказано до отсечки"
AFTER = "ZETA-2700 сказано после отсечки"


def _link(person: BotUser) -> uuid.UUID:
    """Связать человека с памятью: ``ayla_user_id`` и строка ``UserPersonalContext``."""
    user_id = uuid.uuid4()
    BotUser.all_tenants.filter(pk=person.pk).update(ayla_user_id=user_id)
    person.ayla_user_id = user_id
    UserPersonalContext.objects.create(user_id=user_id)
    return user_id


def _uid(person: BotUser) -> uuid.UUID:
    assert person.ayla_user_id is not None
    return person.ayla_user_id


def _green(user_id: uuid.UUID, value: str = "vegan") -> MemoryEntry:
    return MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=UserPersonalContext.objects.get(user_id=user_id),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": value},
    )


# ─── k1: отсечка ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCutoff:
    def test_k1_nothing_withdrawn_or_forgotten_means_no_cutoff(self) -> None:
        person = _person("2700-k1-none")
        _link(person)
        _grant(person)

        assert model_history_cutoff(person) is None

    def test_k1_a_withdrawn_inference_consent_is_a_cutoff(self) -> None:
        person = _person("2700-k1-pi")
        _withdrawn(person, T_CUT, consent_type=PI)

        assert model_history_cutoff(person) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_k1_a_piecewise_forget_is_a_cutoff(self) -> None:
        person = _person("2700-k1-forget")
        user_id = _link(person)

        assert memory_cutoff.stamp(user_id, at=T_CUT) is True

        assert model_history_cutoff(person) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_k1_the_latest_of_the_three_wins(self) -> None:
        person = _person("2700-k1-max")
        user_id = _link(person)
        _withdrawn(person, T_SAID)
        _withdrawn(person, T_CUT, consent_type=PI)
        memory_cutoff.stamp(user_id, at=T_LATER)

        assert model_history_cutoff(person) == datetime(2026, 9, 12, 9, 0, tzinfo=UTC)

    def test_k1_the_cutoff_never_moves_back(self) -> None:
        person = _person("2700-k1-back")
        user_id = _link(person)
        memory_cutoff.stamp(user_id, at=T_LATER)

        assert memory_cutoff.stamp(user_id, at=T_CUT) is False

        assert model_history_cutoff(person) == datetime(2026, 9, 12, 9, 0, tzinfo=UTC)

    def test_k1_a_withdrawn_marketing_consent_is_still_not_a_cutoff(self) -> None:
        person = _person("2700-k1-marketing")
        _withdrawn(person, T_CUT, consent_type=CT.MARKETING.value)

        assert model_history_cutoff(person) is None

    def test_k1_another_persons_forget_is_not_mine(self) -> None:
        mine, other = _person("2700-k1-mine"), _person("2700-k1-other")
        _link(mine)
        memory_cutoff.stamp(_link(other), at=T_CUT)

        assert model_history_cutoff(other) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        assert model_history_cutoff(mine) is None


# ─── k2: кто ставит отсечку ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestWhoStampsTheCutoff:
    @pytest.mark.parametrize(
        "reason",
        [
            MemoryEntry.DELETION_REASON_USER_DELETE,
            MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
        ],
    )
    def test_k2_forgetting_one_fact_on_request_stamps_it(self, reason: str) -> None:
        person = _person(f"2700-k2-{reason}")
        user_id = _link(person)
        entry = _green(user_id)
        assert model_history_cutoff(person) is None

        assert soft_delete_green_entries(user_id, [entry.id], reason=reason) == 1

        entry.refresh_from_db()
        assert model_history_cutoff(person) == entry.soft_deleted_at

    @pytest.mark.parametrize(
        "reason",
        [MemoryEntry.DELETION_REASON_FORGET_ALL, MemoryEntry.DELETION_REASON_TTL_PURGE],
    )
    def test_k2_forget_all_and_expiry_do_not(self, reason: str) -> None:
        person = _person(f"2700-k2-{reason}")
        user_id = _link(person)
        entry = _green(user_id)

        assert soft_delete_green_entries(user_id, [entry.id], reason=reason) == 1

        assert model_history_cutoff(person) is None

    def test_k2_deleting_nothing_stamps_nothing(self) -> None:
        person = _person("2700-k2-nothing")
        user_id = _link(person)

        assert soft_delete_green_entries(user_id, [uuid.uuid4()]) == 0

        assert model_history_cutoff(person) is None

    def test_k2_forgetting_a_health_row_stamps_it(self) -> None:
        person = _person("2700-k2-red")
        user_id = _link(person)
        red = MemoryEntry.objects.create(
            user_id=user_id,
            personal_context=UserPersonalContext.objects.get(user_id=user_id),
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            consent_at=timezone.now(),
            kind="contraindication",
            content={"key": "allergy", "value": "probe"},
        )

        assert RedZoneReader.soft_delete_for_subject(
            entry_id=red.id,
            user_id=user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
            request_id=uuid.uuid4(),
            purpose="test:2700",
            reason=MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
            accessor_principal=f"bot_user:{person.pk}",
        )

        assert model_history_cutoff(person) is not None


# ─── k3, k4: четыре читателя истории из базы ─────────────────────────────────


def _load_recent(conversation: Any) -> str:
    return "\n".join(m.content for m in GlobalConversationStore().load_recent_history(conversation))


def _said_evidence(conversation: Any) -> str:
    return concierge._conversation_text(conversation, "")


def _retry_text(conversation: Any) -> str:
    return max_handler._last_user_content(None, conversation) or ""


def _clarify_offer(conversation: Any) -> str:
    question, options = max_handler._last_clarification_offer(conversation)
    return f"{question} {' '.join(options)}"


READERS = [_load_recent, _said_evidence, _retry_text, _clarify_offer]


def _thread(person: BotUser) -> Any:
    """Разговор с репликами обеих ролей до и после ``T_CUT``; у ответов Ayla — вопрос с выбором."""
    conversation = resolve_active_global_conversation(person)
    for text, at in ((BEFORE, T_SAID), (AFTER, T_LATER)):
        _say(conversation, text, at, role="user")
        answer = _say(conversation, text, at + timedelta(seconds=1))
        Message.all_tenants.filter(pk=answer.pk).update(
            action_data={"clarification": {"question": text, "options": ["да", "нет"]}}
        )
    return conversation


@pytest.mark.django_db
class TestEveryDatabaseReader:
    @pytest.mark.parametrize("read", READERS)
    def test_k3_twin_without_a_cutoff_the_reader_sees_the_thread(self, read: Any) -> None:
        person = _person(f"2700-k3-twin-{read.__name__}")
        _link(person)

        # Каждый читатель видит свежее; «до отсечки» отдельно ниже.
        assert AFTER in read(_thread(person))

    @pytest.mark.parametrize("read", [_load_recent, _said_evidence])
    def test_k3_twin_the_earlier_line_is_there_without_a_cutoff(self, read: Any) -> None:
        person = _person(f"2700-k3-twin-early-{read.__name__}")
        _link(person)

        assert BEFORE in read(_thread(person))

    @pytest.mark.parametrize("read", READERS)
    def test_k3_after_a_forget_the_earlier_line_is_withheld(self, read: Any) -> None:
        person = _person(f"2700-k3-cut-{read.__name__}")
        memory_cutoff.stamp(_link(person), at=T_CUT)

        seen = read(_thread(person))

        assert AFTER in seen
        assert BEFORE not in seen

    @pytest.mark.parametrize("read", [_retry_text, _clarify_offer])
    def test_k3_a_reader_of_the_last_row_finds_nothing_when_it_is_before_the_cutoff(
        self, read: Any
    ) -> None:
        person = _person(f"2700-k3-last-{read.__name__}")
        user_id = _link(person)
        conversation = _thread(person)
        assert AFTER in read(conversation)  # близнец: до отсечки последняя строка читается

        memory_cutoff.stamp(user_id, at=T_LATER + timedelta(days=1))

        assert read(conversation).strip() == ""

    @pytest.mark.parametrize("read", READERS)
    def test_k4_a_failed_cutoff_read_closes_the_reader(
        self, read: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        person = _person(f"2700-k4-{read.__name__}")
        _link(person)
        conversation = _thread(person)
        seen = read(conversation)
        assert AFTER in seen  # близнец: пока отсечка читается, читатель отдаёт

        def boom(bot_user: Any) -> datetime | None:
            raise RuntimeError("consent store down")

        monkeypatch.setattr("apps.consent.services.model_history_cutoff", boom)

        seen = read(conversation)

        assert BEFORE not in seen
        assert AFTER not in seen

    def test_k3_the_rows_the_person_sees_are_untouched(self) -> None:
        person = _person("2700-k3-visible")
        memory_cutoff.stamp(_link(person), at=T_CUT)
        conversation = _thread(person)

        visible = list(
            Message.all_tenants.filter(conversation=conversation)
            .order_by("created_at")
            .values_list("content", flat=True)
        )

        assert visible == [BEFORE, BEFORE, AFTER, AFTER]


# ─── k5, k6: сквозной через обработчик канала ────────────────────────────────

MARK = "ZETA-2700-E"
FACT = f"я веган, пишу с пометкой {MARK}"


def _remembering_chat(monkeypatch: pytest.MonkeyPatch, user_id: int) -> _Chat:
    """Человек со связкой и согласием, сказавший факт: он в памяти и в истории."""
    chat = _Chat(monkeypatch, user_id)
    chat.say("привет")
    bot_user = chat.bot_user
    _link(bot_user)
    record_global_consent(bot_user, source="welcome")
    chat.say(FACT, answer="Хорошо.")
    assert [e.content.get("value") for e in read_green_entries(_uid(bot_user))] == ["vegan"]
    return chat


def _command(chat: _Chat, text: str) -> None:
    """Ход-команда: отвечает без модели, поэтому ``_Chat.say`` для него не годится."""
    GlobalMaxHandler()(_entry(text, chat.user_id))


@pytest.mark.django_db(transaction=True)
class TestThroughTheChannelHandler:
    def test_k5_twin_without_a_forget_the_fact_line_reaches_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _remembering_chat(monkeypatch, 2700501)

        sent = chat.say("что посоветуешь на ужин?")

        assert MARK in sent

    def test_k5_after_forget_x_neither_the_words_nor_the_confirmation_reach_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _remembering_chat(monkeypatch, 2700502)
        bot_user = chat.bot_user

        _command(chat, "забудь что я веган")

        # Стража: команда сработала и ответила тем самым текстом, что повторяет факт.
        left = read_green_entries(_uid(bot_user))
        assert left == []  # empty-assert-ok: «запись была» утверждает _remembering_chat
        confirmation = (
            Message.all_tenants.filter(conversation__bot_user=bot_user, role="assistant")
            .order_by("-created_at")
            .values_list("content", flat=True)
            .first()
        )
        assert confirmation is not None and "забыла, что ты" in confirmation

        sent = chat.say("что посоветуешь на ужин?")

        assert "на ужин" in sent  # ход дошёл до модели со своей репликой
        assert MARK not in sent
        # Слово «веган» само по себе не судится: оно стоит в постоянной части
        # подсказки как пример. Судятся пометка из реплики человека и ответ Ayla.
        assert "забыла, что ты" not in sent

    def test_k5_the_person_still_sees_the_whole_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _remembering_chat(monkeypatch, 2700503)
        bot_user = chat.bot_user

        _command(chat, "забудь что я веган")

        texts = list(
            Message.all_tenants.filter(conversation__bot_user=bot_user, role="user")
            .order_by("created_at")
            .values_list("content", flat=True)
        )
        assert texts == ["привет", FACT, "забудь что я веган"]

    def test_k5_what_is_said_after_the_forget_reaches_the_model_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _remembering_chat(monkeypatch, 2700504)
        _command(chat, "забудь что я веган")
        chat.say("хочу записаться с пометкой ZETA-2700-NEW")

        sent = chat.say("что посоветуешь на ужин?")

        assert "ZETA-2700-NEW" in sent
        assert MARK not in sent

    def test_k6_withdrawing_the_inference_consent_clears_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.consent import preference_inference

        chat = _remembering_chat(monkeypatch, 2700601)
        bot_user = chat.bot_user
        preference_inference.grant(
            bot_user, document_version=preference_inference.PREFERENCE_INFERENCE_DOCUMENT_VERSION
        )
        assert MARK in chat.say("что посоветуешь на ужин?")  # близнец: до отзыва уходит

        preference_inference.withdraw(bot_user)

        assert MARK not in chat.say("а на обед?")

    def test_k6_forgetting_from_the_memory_screen_clears_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.conversations.model_history import clear_model_context_for_person

        chat = _remembering_chat(monkeypatch, 2700602)
        bot_user = chat.bot_user
        (entry,) = read_green_entries(_uid(bot_user))

        # То, что делает ручка экрана памяти: дверь стирания, затем окно Redis.
        soft_delete_green_entries(
            _uid(bot_user),
            [entry.id],
            reason=MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
        )
        assert clear_model_context_for_person(bot_user) >= 1

        sent = chat.say("что посоветуешь на ужин?")

        assert "на ужин" in sent
        assert MARK not in sent


# ─── k7: анкета Ayla ─────────────────────────────────────────────────────────


class _Profile:
    """Анкета Ayla в памяти: PATCH применяется, GET отдаёт текущее. Цену очистить нельзя."""

    def __init__(self) -> None:
        self.fields: dict[str, Any] = {}

    def get_context(self, *, ayla_user_id: str, external_user_id: str) -> DeclaredContext:
        return DeclaredContext(ayla_user_id=ayla_user_id, context=dict(self.fields))

    def patch_context(
        self, *, ayla_user_id: str, external_user_id: str, updates: list[dict[str, Any]]
    ) -> DeclaredContext:
        for update in updates:
            if update["value"] in ("", []):
                self.fields.pop(update["field"], None)
            else:
                self.fields[update["field"]] = update["value"]
        return DeclaredContext(ayla_user_id=ayla_user_id, context=dict(self.fields))

    def close(self) -> None:
        return None


@pytest.fixture
def budget_person(settings: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[BotUser, _Profile]:
    settings.STRICT_TENANT_SCOPE = "strict"
    person = _person("2700-k7")
    _link(person)
    record_global_consent(person, source="welcome")
    ConsentRecord.all_tenants.create(
        tenant=person.tenant,
        bot_user=person,
        consent_type=CT.MEMORY_GREEN,
        granted=True,
        source="test",
    )
    profile = _Profile()
    monkeypatch.setattr(
        "apps.identity.services.personal_context.PersonalContextHttpClient", lambda: profile
    )
    monkeypatch.setattr(memory_block, "concierge_memory_enabled", lambda: True)
    assert record_explicit_green_facts(person, "ориентируюсь на бюджет от 1500 до 3000") == 1
    assert sorted(profile.fields) == ["price_range_max", "price_range_min"]
    return person, profile


def _show(person: BotUser) -> str:
    result = handle_memory_command(
        user_id=_uid(person), text="покажи что знаешь обо мне", bot_user=person
    )
    return result.text if result is not None else ""


@pytest.mark.django_db(transaction=True)
class TestTheProfileDoesNotBringItBack:
    def test_k7_twin_before_the_forget_the_budget_is_used(
        self, budget_person: tuple[BotUser, _Profile]
    ) -> None:
        person, _ = budget_person

        assert "3000" in memory_block.build_concierge_memory_block(person)
        assert "3 000" in _show(person)

    def test_k7_after_forget_the_budget_reaches_neither_the_model_nor_the_list(
        self, budget_person: tuple[BotUser, _Profile]
    ) -> None:
        person, profile = budget_person

        result = handle_memory_command(
            user_id=_uid(person), text="забудь про мой бюджет", bot_user=person
        )

        assert result is not None and result.erased is True
        # Анкета очистить цену не умеет — значение там лежит, но не читается.
        assert sorted(profile.fields) == ["price_range_max", "price_range_min"]
        block = memory_block.build_concierge_memory_block(person)
        shown = _show(person)
        assert "3000" not in block  # empty-assert-ok: «до команды цена в блоке» держит близнец k7
        assert "1500" not in block  # empty-assert-ok: то же — блок после команды может быть пуст
        assert "3 000" not in shown  # empty-assert-ok: «до команды цена в показе» держит близнец k7
        assert "3000" not in shown  # empty-assert-ok: то же — показ после команды может быть пуст

    def test_k7_the_mark_holds_field_names_and_no_value(
        self, budget_person: tuple[BotUser, _Profile]
    ) -> None:
        person, _ = budget_person
        handle_memory_command(user_id=_uid(person), text="забудь про мой бюджет", bot_user=person)

        upc = UserPersonalContext.objects.get(user_id=_uid(person))

        assert upc.declared_fields_withheld == ["price_range_max", "price_range_min"]

    def test_k7_a_budget_named_again_is_used_again(
        self, budget_person: tuple[BotUser, _Profile]
    ) -> None:
        person, _ = budget_person
        handle_memory_command(user_id=_uid(person), text="забудь про мой бюджет", bot_user=person)

        assert record_explicit_green_facts(person, "мне комфортно до 2500") == 1

        assert "2500" in memory_block.build_concierge_memory_block(person)
        upc = UserPersonalContext.objects.get(user_id=_uid(person))
        assert "price_range_max" not in upc.declared_fields_withheld

    def test_k7_other_profile_fields_are_still_read(
        self, budget_person: tuple[BotUser, _Profile]
    ) -> None:
        person, profile = budget_person
        profile.fields["diet_type"] = "vegan"
        handle_memory_command(user_id=_uid(person), text="забудь про мой бюджет", bot_user=person)

        from apps.identity.services.personal_context import get_declared_prefs

        context = get_declared_prefs(person).context

        assert context is not None
        assert dataclasses.asdict(context)["context"] == {"diet_type": "vegan"}


# ─── k6 (окно Redis напрямую) ────────────────────────────────────────────────
#
# Узлы k6 выше судят по тому, что ушло модели, — а в этом пути историю модели
# даёт читатель базы, и подмена «окно не чистится» их не краснила. Поэтому окно
# и состояние подбора проверяются здесь по самому хранилищу.


def _conversation_id(chat: _Chat) -> uuid.UUID:
    conversation = resolve_active_global_conversation(chat.bot_user)
    assert conversation is not None
    return conversation.id


def _window(chat: _Chat) -> str:
    from apps.orchestrator.memory import short_term

    return "\n".join(str(item.get("content")) for item in short_term.recall(_conversation_id(chat)))


def _selection_state_exists(chat: _Chat) -> bool:
    from apps.orchestrator.decision_readiness import state as dre_state

    return dre_state._state_key(str(_conversation_id(chat))) in chat.redis.store


def _seed_selection_state(chat: _Chat) -> None:
    from apps.orchestrator.decision_readiness import state as dre_state

    chat.redis.store[dre_state._state_key(str(_conversation_id(chat)))] = '{"probe": "2700"}'
    assert _selection_state_exists(chat)


@pytest.mark.django_db(transaction=True)
class TestTheRedisWindowItself:
    def test_k6_twin_the_window_holds_the_fact_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chat = _remembering_chat(monkeypatch, 2700611)

        assert MARK in _window(chat)

    def test_k6_the_forget_turn_leaves_an_empty_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chat = _remembering_chat(monkeypatch, 2700612)
        _seed_selection_state(chat)
        assert MARK in _window(chat)  # близнец: до команды реплика в окне есть

        _command(chat, "забудь что я веган")

        window = _window(chat)
        assert MARK not in window
        assert "забыла, что ты" not in window
        assert _selection_state_exists(chat) is False

    def test_k6_withdrawing_the_inference_consent_empties_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.consent import preference_inference

        chat = _remembering_chat(monkeypatch, 2700613)
        _seed_selection_state(chat)
        preference_inference.grant(
            chat.bot_user,
            document_version=preference_inference.PREFERENCE_INFERENCE_DOCUMENT_VERSION,
        )

        preference_inference.withdraw(chat.bot_user)

        assert _window(chat) == ""
        assert _selection_state_exists(chat) is False

    def test_k6_forgetting_from_the_memory_screen_empties_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.conversations.model_history import clear_model_context_for_person

        chat = _remembering_chat(monkeypatch, 2700614)
        _seed_selection_state(chat)

        assert clear_model_context_for_person(chat.bot_user) >= 1

        assert _window(chat) == ""
        assert _selection_state_exists(chat) is False
