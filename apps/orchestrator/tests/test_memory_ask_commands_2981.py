"""DRF-2981 — команда и вопрос не сохраняются ответом на вопрос памяти.

Замер на dev ``d52b1c37`` (висящий вопрос, затем реплика): «отмени запись на
завтра», «покажи мои записи», «а сколько стоит маникюр?» сохранялись районом с
ответом «Записала, спасибо 🤍»; «отмени запись на вечер» — удобным временем;
«удали мою запись к мастеру Ольге за 2000» — бюджетом 2000 и рейтингом 2.0;
«сотри бронь на субботу» — занятым днём; «забудь что я веган» — диетой.

Какие из этих реплик на живом пути доходят до вопроса памяти: «покажи мои
записи» перехватывает список визитов выше по лестнице, «забудь что я веган»
при согласии — команда памяти. Узлы держат и их: порядок лестницы может
поменяться, а вопрос памяти не должен на это полагаться.

Остаток, названный, а не спрятанный: «не смогу прийти в субботу» после
вопроса о районе по-прежнему сохраняется районом (не команда и не вопрос), а
после вопроса о занятых днях — субботой (это может быть и настоящим ответом).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.orchestrator import memory_ask
from apps.orchestrator.memory_ask import read_pending, try_handle_answer
from apps.orchestrator.tests.test_memory_ask import _FakeRedis

FIELDS = sorted(memory_ask._FIELD_PARSERS)


@pytest.fixture
def ask(monkeypatch):
    """Задать вопрос о ``field`` и вернуть (ответ, что сохранено, вызван ли skip)."""
    fake = _FakeRedis()
    monkeypatch.setattr("apps.orchestrator.memory.short_term._redis_client", lambda: fake)
    monkeypatch.setattr(memory_ask, "concierge_memory_enabled", lambda: True)
    conversation = SimpleNamespace(id="conv-2981")
    bot_user = SimpleNamespace(id="bu-2981")

    def run(field: str, text: str):
        patch = Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.OK))
        skip = Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.OK, skip_count=1))
        monkeypatch.setattr(memory_ask, "patch_declared_prefs", patch)
        monkeypatch.setattr(memory_ask, "skip", skip)
        memory_ask._write_pending(conversation.id, {"field": field, "hint": "?"})
        assert read_pending(conversation.id) is not None

        reply = try_handle_answer(conversation, bot_user, text)

        saved = patch.call_args[0][1][0]["value"] if patch.called else None
        pending = read_pending(conversation.id)
        return reply, saved, skip.called, pending

    return run


MEASURED = [
    "отмени запись на завтра",
    "удали запись на завтра",
    "удали, пожалуйста, запись на завтра",
    "забудь про запись",
    "сотри бронь на субботу",
    "перенеси запись на пятницу вечером",
    "отмени запись на вечер",
    "отменить запись в центре",
    "забудь что я веган",
    "удали мою запись к мастеру Ольге за 2000",
    "покажи мои записи",
    "а сколько стоит маникюр?",
    "запиши меня на вечер",
]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("text", MEASURED)
def test_p1_a_command_or_question_is_not_saved_as_an_answer(ask, field, text) -> None:
    reply, saved, skipped, pending = ask(field, text)

    assert reply is None  # ход идёт дальше — консьержу
    assert saved is None
    assert not skipped
    assert pending is None  # empty-assert-ok: в ask() вопрос был — read_pending до хода


#: Настоящие ответы — сохраняются, как и раньше.
TWINS = [
    ("home_district", "в Ленинском районе", "в Ленинском районе"),
    ("home_district", "живу у метро Пролетарская", "живу у метро Пролетарская"),
    ("home_district", "запишите: Ленинский", "запишите: Ленинский"),
    ("home_district", "Субботинский район", "Субботинский район"),
    ("home_district", "не забудь: живу в Заречном", "не забудь: живу в Заречном"),
    ("workplace_district", "удалённо работаю, офис в центре", "удалённо работаю, офис в центре"),
    ("preferred_time_slots", "вечером", ["evening"]),
    ("preferred_time_slots", "удобнее записываться вечером", ["evening"]),
    ("preferred_time_slots", "вечером, покажи что есть", ["evening"]),
    ("price_range_max", "до 2000", "2000.00"),
    ("price_range_max", "до 2000, покажи что есть", "2000.00"),
    ("busy_days", "по субботам занята", ["sat"]),
    ("diet_type", "я веган", "vegan"),
    ("diet_type", "не забудь, я веган", "vegan"),
]


@pytest.mark.parametrize(("field", "text", "value"), TWINS)
def test_p2_twin_a_real_answer_is_still_saved(ask, field, text, value) -> None:
    reply, saved, _skipped, _pending = ask(field, text)

    assert reply is not None and "Записала" in reply.text
    assert saved == value


#: Вопрос — не ответ ни для одного поля: иначе разбор вынимает из вопроса день,
#: цену или диету — у занятых дней со смыслом наоборот.
QUESTIONS = [
    ("busy_days", "а в субботу вы работаете?"),
    ("busy_days", "а в пятницу есть окна?"),
    ("preferred_time_slots", "а вечером есть окна?"),
    ("price_range_max", "а маникюр за 2000 есть?"),
    ("min_rating_preference", "а у Ольги рейтинг 4.8?"),
    ("diet_type", "а для веганов меню есть?"),
    ("home_district", "в Ленинском?"),  # цена правила: такой ответ тоже не сохранится
]


@pytest.mark.parametrize(("field", "text"), QUESTIONS)
def test_p3_a_question_is_not_an_answer(ask, field, text) -> None:
    reply, saved, _skipped, pending = ask(field, text)

    assert reply is None
    assert saved is None
    assert pending is None  # empty-assert-ok: в ask() вопрос был — read_pending до хода


@pytest.mark.parametrize(
    "text",
    [
        "пропусти, отмени запись на завтра",
        "отмени запись, не скажу почему",
        "не сейчас, запиши меня на завтра",
    ],
)
def test_p4_a_command_with_a_skip_word_is_not_a_skip(ask, text) -> None:
    """Ложный «пропустить» липкий — счётчик пропусков в Ayla."""
    reply, saved, skipped, _pending = ask("diet_type", text)

    assert reply is None
    assert saved is None
    assert not skipped


def test_p4_twin_a_plain_skip_is_still_a_skip(ask) -> None:
    reply, saved, skipped, _pending = ask("diet_type", "не хочу отвечать")

    assert skipped
    assert reply is not None
    assert saved is None


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("home_district", "на завтра"),
        ("busy_days", "в субботу"),
        ("preferred_time_slots", "завтра вечером"),
    ],
)
def test_p5_a_booking_in_flight_wins_over_the_question(ask, monkeypatch, field, text) -> None:
    """После тапа по мастеру «в субботу» — день записи, а не занятый день."""
    monkeypatch.setattr(
        "apps.orchestrator.booking_context.load_booking_context", lambda conversation: object()
    )

    reply, saved, _skipped, pending = ask(field, text)

    assert reply is None
    assert saved is None
    assert pending is None  # empty-assert-ok: в ask() вопрос был — read_pending до хода
