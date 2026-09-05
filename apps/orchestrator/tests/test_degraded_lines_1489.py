"""DRF-1489 — «отвечу через минуту» без кнопки, которая это обещание держит.

Дефект, который здесь закрыт, был не в тексте и не в кнопке по отдельности,
а в том, что они разъехались. Экран «AI недоступна» с «Повторить» (макет C01)
рисуется каналом ровно по флагу ``DiscoveryReply.outage``, а флаг ставился на
одном сбойном возврате консьержа из семи. Остальные шесть отдавали ту же
строку «Извини, у меня сейчас короткий технический сбой — отвечу через
минуту» с ``outage=False``: обещание вернуться и никакого способа его
исполнить — ни автоматического (отложенного повтора за этим ответом нет), ни
ручного (кнопки нет). Человек читал «отвечу через минуту», ждал минуту, и
ничего не происходило.

Что доказывают эти тесты — обе половины, потому что отрицание без
положительной стражи не значит ничего (DRF-1411):

* **Отрицательная.** По каждому из шести сбоев человек получает СВОЙ текст,
  который ничего не обещает, и экрана «AI недоступна» на нём нет.
* **Положительная, на тех же данных и через тот же вход.** Сбой класса
  outage — модель недоступна — по-прежнему даёт экран с «Повторить», и
  обещающая строка живёт только там, где эта кнопка есть.
* **Замер по коду.** Число возвратов с обещающим текстом и ``outage=False``
  равно нулю — числом, разбором исходника, а не на глаз.

Экран читается там, где его видит человек: ``handle_global_max_event``
прогоняется целиком, а подменена только модель. Проверяется ровно то, что
ушло в MAX — текст и кнопки под ним.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.quick_actions import AI_UNAVAILABLE_TEXT, RETRY_CALLBACK, RETRY_LABEL
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.llm import templates
from apps.orchestrator.memory import short_term

# ``transaction=True``: ход консьержа пишет в БД из другого потока
# (``asyncio.run`` внутри ``generate_concierge_reply``), и обёрнутая в одну
# транзакцию сессия его не видит.
pytestmark = pytest.mark.django_db(transaction=True)

#: Обещающая строка. Её единственное законное место — ответ с ``outage=True``.
PROMISE = templates.OUTAGE_RU


# --------------------------------------------------------------------------- #
# Обвязка канала — копия минимума из test_first_contact_c01, чтобы «экран»    #
# здесь означал то же, что там: реально отправленное сообщение.               #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


@pytest.fixture(autouse=True)
def _no_second_model_calls(monkeypatch):
    """Разбор намерения и вопрос памяти — свои вызовы модели.

    На сбойном ходу они не должны ни падать, ни дописывать в ответ: тест про
    то, ЧТО увидел человек вместо ответа, а не про то, что сверху приклеилось.
    """
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


def _welcomed_user(user_id: int):
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1489",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": 8899, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _buttons(attachments) -> list[dict]:
    if not attachments:
        return []
    rows = attachments[0].get("payload", {}).get("buttons", [])
    return [b for row in rows for b in row]


# --------------------------------------------------------------------------- #
# Обвязка модели                                                              #
# --------------------------------------------------------------------------- #
def _tool(name: str, arguments: dict) -> CompletionResult:
    """Ответ модели, который ВЫЗВАЛ инструмент и не сказал ни слова."""
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name=name, arguments=arguments)],
        prompt_tokens=30,
        completion_tokens=6,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _empty_text() -> CompletionResult:
    """Модель ответила ПУСТОТОЙ: ни инструмента, ни слова."""
    return CompletionResult(
        text="",
        tool_calls=[],
        prompt_tokens=20,
        completion_tokens=0,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _card() -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id="t1",
        master_id="m1",
        name="Анна",
        specialization="Массаж",
        rating=4.9,
        city="Пенза",
        service_id=None,
        service_name="",
    )


def _model(monkeypatch, *results, raises: BaseException | None = None) -> AsyncMock:
    """Подменить ТОЛЬКО модель. Всё остальное в ходе — настоящее."""
    provider = AsyncMock()
    if raises is not None:
        provider.complete.side_effect = raises
    elif len(results) == 1:
        provider.complete.return_value = results[0]
    else:
        provider.complete.side_effect = list(results)
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    return provider


_UID = iter(range(71001, 71999))


def _screen(sent, *, text: str = "мне бы совет") -> dict:
    """Прогнать настоящий ход через канал и вернуть то, что увидел человек."""
    user_id = next(_UID)
    _welcomed_user(user_id)
    max_handler.handle_global_max_event(_msg(text=text, user_id=user_id, mid=f"m{user_id}"))
    last = sent[-1]
    return {"text": last["text"], "buttons": [b["text"] for b in _buttons(last["attachments"])]}


# --------------------------------------------------------------------------- #
# Шесть сбоев, у которых кнопки не было и не будет                            #
# --------------------------------------------------------------------------- #
class TestSixDegradedSitesPromiseNothing:
    """По каждому из шести — какой экран получает человек.

    Общее для всех шести: модель БЫЛА доступна. Она ответила — просто так,
    что показать нечего. Экран «AI недоступна» здесь врал бы про причину, а
    «Повторить» — про исход: на всех шести повтор той же реплики упирается в
    тот же отказ.
    """

    def test_nutrition_parser_refusal_asks_for_other_words(self, monkeypatch, sent, fake_redis):
        """Разбор фразы дневника не удался — модель ни при чём.

        Повтор тех же слов даст тот же выбор инструмента с теми же
        аргументами и тот же отказ парсера: кнопка «Повторить» здесь была бы
        кнопкой-петлёй.
        """
        _model(monkeypatch, _tool("log_water", {"ml": 200}))
        monkeypatch.setattr(concierge, "execute_nutrition_tool", lambda *a, **kw: None)

        screen = _screen(sent, text="выпил стакан воды")

        assert screen["text"] == templates.NOT_PARSED_RU
        assert screen["buttons"] == []
        assert screen["text"] != PROMISE
        assert screen["text"] != AI_UNAVAILABLE_TEXT

    def test_personal_tool_declined_says_so_without_promising(self, monkeypatch, sent, fake_redis):
        """Диспетчер личных данных вернул пустоту — это наш дефект.

        Диспетчеризация детерминирована: та же реплика приедет сюда же.
        """
        _model(monkeypatch, _tool("show_my_records", {}))
        monkeypatch.setattr(concierge, "execute_personal_tool", lambda *a, **kw: None)

        # Текст нарочно нейтральный: «покажи мои записи» перехватывает ветка
        # визитов ДО консьержа, а проверяется здесь именно консьерж.
        screen = _screen(sent)

        assert screen["text"] == templates.NO_ANSWER_RU
        assert screen["buttons"] == []
        assert screen["text"] != PROMISE
        assert screen["text"] != AI_UNAVAILABLE_TEXT

    def test_booking_without_a_name_asks_for_the_name(self, monkeypatch, sent, fake_redis):
        """``start_booking`` приехал, не назвав никого.

        Единственное недостающее известно поимённо — значит спрашиваем ЕГО,
        а не извиняемся вообще.
        """
        _model(monkeypatch, _tool("start_booking", {"master": ""}))

        screen = _screen(sent, text="запиши меня")

        assert screen["text"] == templates.BOOKING_NEEDS_NAME_RU
        assert screen["buttons"] == []
        assert screen["text"] != PROMISE
        assert screen["text"] != AI_UNAVAILABLE_TEXT

    def test_catalog_tool_declined_says_so_without_promising(self, monkeypatch, sent, fake_redis):
        _model(monkeypatch, _tool("show_salons", {}))
        monkeypatch.setattr(concierge, "execute_catalog_tool", lambda *a, **kw: None)

        screen = _screen(sent, text="какие есть салоны")

        assert screen["text"] == templates.NO_ANSWER_RU
        assert screen["buttons"] == []
        assert screen["text"] != PROMISE
        assert screen["text"] != AI_UNAVAILABLE_TEXT

    def test_blank_clarification_says_so_without_promising(self, monkeypatch, sent, fake_redis):
        """Уточняющий вопрос приехал пустым — ход взят, показать нечего."""
        _model(monkeypatch, _tool("ask_clarification", {"question": "   "}))

        screen = _screen(sent)

        assert screen["text"] == templates.NO_ANSWER_RU
        assert screen["buttons"] == []
        assert screen["text"] != PROMISE
        assert screen["text"] != AI_UNAVAILABLE_TEXT

    def test_unknown_tool_degrade_says_so_without_promising(self, monkeypatch, sent, fake_redis):
        """Тот же экран приезжает и вторым путём — через деградацию
        диспетчера (инструмент, которого мы не знаем): ``_dispatch_tool``
        сводит её к уточнению без вопроса. Оба входа в одну ветку, чтобы
        второй не остался непроверенным."""
        _model(monkeypatch, _tool("teleport_client", {"to": "Марс"}))

        screen = _screen(sent)

        assert screen["text"] == templates.NO_ANSWER_RU
        assert screen["buttons"] == []
        assert screen["text"] != PROMISE

    def test_empty_completion_holding_cards_shows_the_cards(self, monkeypatch, sent, fake_redis):
        """Шестой сбой, но с данными на руках.

        Пустой follow-up после сработавшего ``show_masters`` — это не
        «AI недоступна»: карточки уже есть, и предлагать «Повторить» поверх
        готового ответа было бы той же ложью, только наоборот.
        """
        _model(
            monkeypatch,
            _tool("show_masters", {"city": "Пенза", "specialization": "массаж"}),
            _empty_text(),
        )
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card()])

        screen = _screen(sent, text="массаж в пензе")

        assert "Анна" in screen["text"]
        assert screen["text"] != PROMISE
        assert screen["text"] != AI_UNAVAILABLE_TEXT
        assert RETRY_LABEL not in screen["buttons"]


# --------------------------------------------------------------------------- #
# Парная положительная стража (DRF-1411)                                      #
# --------------------------------------------------------------------------- #
class TestOutageStillGetsTheButton:
    """Тот же вход, тот же канал — и кнопка на месте.

    Без этой половины все проверки выше доказывали бы только, что кнопка
    исчезла везде.
    """

    def test_unreachable_model_shows_the_screen_with_retry(self, monkeypatch, sent, fake_redis):
        """Классический outage: до модели не дошли."""
        _model(monkeypatch, raises=RuntimeError("vendor 500"))

        screen = _screen(sent)

        assert screen["text"] == AI_UNAVAILABLE_TEXT
        assert screen["buttons"] == [RETRY_LABEL]

    def test_retry_button_payload_is_the_live_one(self, monkeypatch, sent, fake_redis):
        _model(monkeypatch, raises=RuntimeError("vendor 500"))
        user_id = next(_UID)
        _welcomed_user(user_id)

        max_handler.handle_global_max_event(_msg(text="мне бы совет", user_id=user_id, mid="rp"))

        buttons = _buttons(sent[-1]["attachments"])
        assert buttons[0]["payload"] == RETRY_CALLBACK

    def test_empty_completion_with_nothing_in_hand_is_an_outage(
        self, monkeypatch, sent, fake_redis
    ):
        """Модель ответила пустотой — ход не состоялся.

        Оценивать тут нечего: ответа нет вовсе. Лекарство ровно одно — та же
        реплика ещё раз, и это ровно то, что делает «Повторить». Поэтому
        единственный из шести, который классифицирован как outage.
        """
        _model(monkeypatch, _empty_text())

        screen = _screen(sent)

        assert screen["text"] == AI_UNAVAILABLE_TEXT
        assert screen["buttons"] == [RETRY_LABEL]

    def test_a_normal_answer_is_untouched(self, monkeypatch, sent, fake_redis):
        """Стража на исправность обвязки: обычный ответ проходит как был."""
        _model(
            monkeypatch,
            CompletionResult(
                text="Здравствуйте! Чем могу помочь?",
                tool_calls=[],
                prompt_tokens=10,
                completion_tokens=5,
                model="gpt-4o-mini",
                provider="openai",
                finish_reason="stop",
            ),
        )

        screen = _screen(sent)

        assert screen["text"] == "Здравствуйте! Чем могу помочь?"
        assert screen["buttons"] == []


# --------------------------------------------------------------------------- #
# Замер по коду — числом                                                      #
# --------------------------------------------------------------------------- #
def _concierge_source() -> str:
    """Исходник консьержа — читается с диска, а не пересказывается здесь."""
    path = inspect.getsourcefile(concierge)
    assert path is not None
    return Path(path).read_text(encoding="utf-8")


def _promise_returns(source: str) -> list[tuple[int, bool]]:
    """Все ``return``'ы, чей текст берётся из обещающего шаблона.

    Возвращает ``(номер строки, стоит ли outage=True)``. Разбор идёт по AST,
    а не по регулярке: переименование обёртки ``_reply`` или перенос строки
    не должны бесшумно вывести возврат из-под замера.
    """
    found: list[tuple[int, bool]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        text_kw = next((kw for kw in call.keywords if kw.arg == "text"), None)
        if text_kw is None or not isinstance(text_kw.value, ast.Call):
            continue
        fn = text_kw.value.func
        if not (isinstance(fn, ast.Name) and fn.id == "get_fallback"):
            continue
        outage_kw = next((kw for kw in call.keywords if kw.arg == "outage"), None)
        flagged = (
            outage_kw is not None
            and isinstance(outage_kw.value, ast.Constant)
            and outage_kw.value.value is True
        )
        found.append((node.lineno, flagged))
    return found


class TestNoPromiseWithoutTheButton:
    def test_zero_promise_returns_without_the_outage_flag(self) -> None:
        """Замер, ради которого заведена задача.

        Число возвратов с обещающим текстом и ``outage=False`` — ноль. До
        правки их было шесть.
        """
        source = _concierge_source()
        unflagged = [line for line, flagged in _promise_returns(source) if not flagged]

        assert unflagged == [], f"промис без кнопки на строках: {unflagged}"

    def test_the_promise_is_still_shipped_somewhere(self) -> None:
        """Парная положительная: замер не должен проходить оттого, что
        обещающий текст вычищен вообще. Он остаётся — там, где кнопка есть."""
        source = _concierge_source()
        flagged = [line for line, is_outage in _promise_returns(source) if is_outage]

        assert len(flagged) >= 1

    @pytest.mark.parametrize(
        "line",
        [
            templates.NOT_PARSED_RU,
            templates.NO_ANSWER_RU,
            templates.BOOKING_NEEDS_NAME_RU,
            templates.NOT_PARSED_EN,
            templates.NO_ANSWER_EN,
            templates.BOOKING_NEEDS_NAME_EN,
        ],
    )
    def test_degraded_lines_promise_nothing(self, line: str) -> None:
        """Ни одна строка без кнопки не обещает, что бот вернётся сам."""
        lowered = line.lower()
        # Стража присутствия на тех же данных: строке есть что читать.
        assert lowered
        # И стража на саму проверку: на обещающей строке она СРАБАТЫВАЕТ.
        # Без этого «обещания нет» проходило бы и на пустой фразе, и на
        # переписанном шаблоне, в котором обещание просто сформулировано
        # иначе.
        assert "через минуту" in PROMISE.lower()
        assert "in a moment" in templates.OUTAGE_EN.lower()
        assert "через минуту" not in lowered
        assert "in a moment" not in lowered
        assert "вернусь" not in lowered
