"""DRF-2887 — команда забывания не зависит от запятой и не срабатывает на «не забудь».

Замер на ``eac09fc6`` (каждая реплика — человеку с одной записью «веган»):

* «забудь, что я веган» — с запятой, как требует пунктуация, — командой не
  считалась: запись оставалась, реплика уходила модели, человек не узнавал, что
  просьба не выполнена. То же «удали,», «сотри,», «забудь: …», «забудь,
  пожалуйста, …», «покажи, что знаешь обо мне»;
* «не забудь что я веган» (без запятой) СТИРАЛА запись и получала ответ
  «забыла» — просьба помнить исполнялась наоборот. С запятой не срабатывала
  только потому, что запятая ломала шаблон.

Узлы:

* p1 — формы «забудь X» со знаками и вежливостью стирают запись;
* p2 — близнец: те же формы без знаков стирали и раньше, и стирают сейчас;
* p3 — отрицание перед глаголом — не команда: запись цела, ход идёт дальше;
* p4 — «покажи, что …» показывает и ничего не стирает;
* p5 — «забудь всё» со знаками только спрашивает подтверждение;
* p6 — ложные входы по-прежнему не команды;
* p7 — слово-подтверждение сверяется как раньше.
"""

from __future__ import annotations

import uuid

import pytest

from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_reader import read_green_entries
from apps.persona import memory_commands
from apps.persona.memory_commands import MemoryCommandResult, handle_memory_command

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def person(settings: object, monkeypatch: pytest.MonkeyPatch) -> BotUser:
    """Человек с одной записью «веган». Анкета Ayla не предмет — мост заглушён."""
    settings.STRICT_TENANT_SCOPE = "strict"  # type: ignore[attr-defined]
    monkeypatch.setattr(memory_commands, "_bridge_clear", lambda *args, **kwargs: None)
    user_id = uuid.uuid4()
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=f"2887-{user_id.hex[:10]}", ayla_user_id=user_id
    )
    record_global_consent(bot_user, source="welcome")
    MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=UserPersonalContext.objects.create(user_id=user_id),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    return bot_user


def _uid(person: BotUser) -> uuid.UUID:
    assert person.ayla_user_id is not None
    return person.ayla_user_id


def _say(person: BotUser, text: str) -> MemoryCommandResult | None:
    return handle_memory_command(user_id=_uid(person), text=text, bot_user=person)


def _rows(person: BotUser) -> int:
    return len(read_green_entries(_uid(person)))


PUNCTUATED_FORGET = [
    "забудь, что я веган",
    "Забудь, что я веган.",
    "забудь: я веган",
    "забудь; что я веган",
    "забудь, пожалуйста, что я веган",
    "пожалуйста, забудь, что я веган",
    "удали, что я веган",
    "сотри, что я веган",
    "забудь, про питание",
    "забудь всё, про моё питание",
]

PLAIN_FORGET = [
    "забудь что я веган",
    "забудь — что я веган",
    "забудь про то, что я веган",
    "забудь всё про моё питание",
    "забудь про питание, пожалуйста",
]

NEGATED = [
    "не забудь что я веган",
    "не забудь, что я веган",
    "Не забудь: я веган",
    "пожалуйста, не забудь, что я веган",
    "не забывай, что я веган",
    "не удали случайно, что я веган",
    "не сотри, что я веган",
    "не забудь меня",
    "не забудь всё",
]

SHOW = [
    "покажи что знаешь обо мне",
    "покажи, что знаешь обо мне",
    "покажи, что ты знаешь обо мне",
    "покажи, пожалуйста, что знаешь обо мне",
    "скажи, что ты обо мне помнишь",
]

FORGET_ALL = [
    "забудь всё",
    "забудь, всё",
    "забудь всё, что знаешь обо мне",
    "пожалуйста, забудь всё",
]

NOT_COMMANDS = [
    "забудь это",
    "забудь, это",
    "удали меня из рассылки",
    "удали, пожалуйста, меня из рассылки",
    "хочу записаться на массаж, пожалуйста",
]


@pytest.mark.parametrize("text", PUNCTUATED_FORGET)
def test_p1_a_forget_command_with_punctuation_erases_the_fact(person: BotUser, text: str) -> None:
    assert _rows(person) == 1

    result = _say(person, text)

    assert result is not None
    assert result.text.startswith("Готово — забыла")
    assert _rows(person) == 0  # empty-assert-ok: строкой выше «запись была» — rows == 1


@pytest.mark.parametrize("text", PLAIN_FORGET)
def test_p2_twin_the_forms_that_worked_before_still_work(person: BotUser, text: str) -> None:
    assert _rows(person) == 1

    result = _say(person, text)

    assert result is not None
    assert result.text.startswith("Готово — забыла")
    assert _rows(person) == 0  # empty-assert-ok: строкой выше «запись была» — rows == 1


@pytest.mark.parametrize("text", NEGATED)
def test_p3_a_request_to_remember_is_not_a_forget_command(person: BotUser, text: str) -> None:
    result = _say(person, text)

    assert result is None
    assert _rows(person) == 1


@pytest.mark.parametrize("text", SHOW)
def test_p4_a_show_command_with_punctuation_shows_and_erases_nothing(
    person: BotUser, text: str
) -> None:
    result = _say(person, text)

    assert result is not None
    assert "веганского питания" in result.text
    assert _rows(person) == 1


@pytest.mark.parametrize("text", FORGET_ALL)
def test_p5_forget_all_with_punctuation_only_asks_to_confirm(person: BotUser, text: str) -> None:
    result = _say(person, text)

    assert result is not None
    assert result.action_type == "memory_forget_all_prompt"
    assert _rows(person) == 1


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_p6_what_was_never_a_memory_command_still_is_not(person: BotUser, text: str) -> None:
    result = _say(person, text)

    assert result is None
    assert _rows(person) == 1


def test_p7_the_confirmation_word_is_still_matched_as_one_word(person: BotUser) -> None:
    prompt = _say(person, "забудь всё")
    assert prompt is not None

    # Слово с запятой — не подтверждение: оно сверяется с исходной строкой.
    handle_memory_command(
        user_id=_uid(person),
        text="удалить, наверное",
        bot_user=person,
        last_assistant_text=prompt.text,
    )

    assert _rows(person) == 1
