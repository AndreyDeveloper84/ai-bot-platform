"""DRF-1512 — обещающий текст не уезжает от провайдера как обычный ответ.

# Дефект

``apps/orchestrator/llm/openai_provider.py`` при ОТКРЫТОМ предохранителе
возвращал::

    template = get_fallback(self.fallback_lang)
    return LLMResponse(content=template, model=chosen_model, is_fallback=True)

То есть настоящий сбой сервиса приезжал вызывающему как обычный успешный
ответ модели, и нёс единственную строку продукта, которая обещает, что бот
вернётся: «Извини, у меня сейчас короткий технический сбой — отвечу через
минуту». Флага, по которому канал рисует «Повторить» (макет C01), в
``LLMResponse`` нет и быть не может — значит обещание уехало бы к человеку
без единственного способа его исполнить.

Сегодня этого не видно клиенту только потому, что провайдер используется
одним ``intent_router``'ом — для классификации, не на пути формирования
ответа. Никакой другой защиты здесь нет.

# Почему это мина, а не косметика

DRF-1489 свёл к нулю число мест, где обещание отдаётся без кнопки, — замером
по коду, разбором AST ``apps/orchestrator/concierge.py``. Провайдер в тот
замер не входил. Подключи его кто-нибудь к клиентским ответам — и дыра
открылась бы ЗАНОВО И МОЛЧА: настоящий outage приехал бы с ``outage=False``,
человек прочитал бы обещание без кнопки, а замер DRF-1489 по-прежнему
показывал бы ноль.

# Что доказывают эти тесты — обеими половинами (DRF-1411)

* **Отказ.** Предохранитель открыт → ``complete()`` поднимает
  :class:`LLMOutageError`, не отдаёт ``LLMResponse``, и отказ несёт
  ``outage=True``.
* **Парная положительная, на тех же данных и через тот же вход.**
  Предохранитель закрыт → обычный ответ модели проходит нетронутым.
* **Стража на клиентской границе.** Провайдер прогоняется через
  вызывающего той формы, какая формирует ответ человеку. Ни один исход
  этого хода не даёт обещающий текст с ``outage=False`` — и при этом один
  из исходов обещающий текст ВСЁ ЖЕ несёт, иначе проверка была бы пустой.
  Это и есть страж, который переживёт подключение провайдера к клиентским
  ответам: он проверяет не строку внутри модуля, а исход вызова.
* **Замер по коду — тем же способом, что в DRF-1489**, чтобы числа были
  сопоставимы: возвратов с предлагающим текстом и без ``outage=True`` в
  провайдере ноль (было одно), в консьерже — по-прежнему два, и оба с
  флагом. Плюс калибровка самого детектора: на до-правочном исходнике он
  ОБЯЗАН срабатывать, иначе ноль ничего не значит.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from apps.orchestrator import concierge
from apps.orchestrator.llm import openai_provider as provider_module
from apps.orchestrator.llm import templates
from apps.orchestrator.llm.breaker import BreakerOpenError, reset_breaker
from apps.orchestrator.llm.openai_provider import LLMOutageError, LLMResponse, OpenAIProvider

#: Обещающая строка. Её единственное законное место — ход с «Повторить».
PROMISE = templates.OUTAGE_RU

#: Живой ответ модели, который обязан доехать нетронутым.
LIVE_ANSWER = "Здравствуйте! Чем могу помочь?"

_MSG = {"role": "user", "content": "мне бы совет"}


@pytest.fixture(autouse=True)
def _clear_openai_breaker():
    reset_breaker("openai.complete")
    yield
    reset_breaker("openai.complete")


@pytest.fixture(autouse=True)
def _audit_without_db(monkeypatch):
    """Журнал отказа проверяется на живой БД в ``test_openai_provider.py``.

    Здесь он подменён нарочно: этот файл — про исход вызова и про исходник,
    и должен гоняться без Postgres, чтобы страж читался и запускался в один
    шаг кем угодно.
    """

    monkeypatch.setattr(provider_module, "write_audit", Mock())


async def _open_the_breaker(provider: OpenAIProvider) -> None:
    """Уронить пять вызовов подряд — ровно так открывается предохранитель."""

    with patch.object(
        provider,
        "_call_openai",
        AsyncMock(side_effect=RuntimeError("vendor 503")),
    ):
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await provider.complete(messages=[_MSG])


async def _client_turn(provider: OpenAIProvider) -> dict[str, object]:
    """Вызывающий той формы, какая формирует ответ ЧЕЛОВЕКУ.

    Ровно то, что делает ``generate_concierge_reply``: сбой модели ловится и
    превращается в ответ с обещающей строкой, а признак ``outage`` берётся у
    самого сбоя — по нему канал рисует «Повторить».

    Здесь и живёт ценность задачи. Провайдер сегодня к клиентским ответам не
    подключён; этот ход показывает, что произойдёт в тот день, когда его
    подключат. До правки он вернул бы обещание с ``outage=False``.
    """

    try:
        response = await provider.complete(messages=[_MSG])
    except BreakerOpenError as exc:
        return {
            "text": templates.get_fallback("ru"),
            "outage": bool(getattr(exc, "outage", False)),
        }
    return {"text": response.content, "outage": False}


# --------------------------------------------------------------------------- #
# Открытый предохранитель — отказ, а не текст                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestOpenBreakerIsAFailureNotAnAnswer:
    async def test_open_breaker_raises_a_typed_outage(self):
        """Отрицательная половина: ответа нет вовсе."""

        provider = OpenAIProvider(api_key="fake")
        await _open_the_breaker(provider)

        async def must_not_call(*args, **kwargs):
            raise AssertionError("предохранитель открыт — вызова быть не должно")

        with patch.object(provider, "_call_openai", must_not_call):
            with pytest.raises(LLMOutageError) as excinfo:
                await provider.complete(messages=[_MSG])

        assert excinfo.value.outage is True
        assert excinfo.value.reason == "breaker_open"
        message = str(excinfo.value)
        # Стража присутствия на тех же данных: сообщение об отказе не пустое и
        # говорит о предохранителе. Без неё «обещания нет» прошло бы и на
        # пустой строке.
        assert "breaker" in message
        # Обещающего текста в отказе нет вообще: слова — не дело этого модуля.
        assert PROMISE not in message

    async def test_closed_breaker_answer_passes_through_untouched(self):
        """Парная положительная, тот же провайдер и тот же вход.

        Без неё проверка выше доказывала бы лишь, что провайдер перестал
        отвечать вообще.
        """

        provider = OpenAIProvider(api_key="fake")
        expected = LLMResponse(content=LIVE_ANSWER, model="gpt-4o-mini")

        with patch.object(provider, "_call_openai", AsyncMock(return_value=expected)):
            response = await provider.complete(messages=[_MSG])

        assert response is expected
        assert response.content == LIVE_ANSWER
        assert response.is_fallback is False


# --------------------------------------------------------------------------- #
# Страж клиентской границы                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestPromiseNeverArrivesWithoutTheFlag:
    """Главная ценность задачи.

    Точечная правка внутри ``complete()`` не переживёт переключения
    провайдера на клиентские ответы — этот класс переживёт: он смотрит на
    ИСХОД вызова глазами того, кто формирует ответ человеку, а не на строку
    внутри модуля.
    """

    async def test_outage_carries_the_flag_that_draws_the_button(self):
        provider = OpenAIProvider(api_key="fake")
        await _open_the_breaker(provider)

        with patch.object(provider, "_call_openai", AsyncMock(side_effect=AssertionError)):
            reply = await _client_turn(provider)

        assert reply["text"] == PROMISE
        assert reply["outage"] is True

    async def test_a_live_answer_is_not_the_promise(self):
        """Парная положительная на тех же данных: рабочий ход не трогается."""

        provider = OpenAIProvider(api_key="fake")
        expected = LLMResponse(content=LIVE_ANSWER, model="gpt-4o-mini")

        with patch.object(provider, "_call_openai", AsyncMock(return_value=expected)):
            reply = await _client_turn(provider)

        assert reply["text"] == LIVE_ANSWER
        assert reply["outage"] is False
        assert reply["text"] != PROMISE

    async def test_no_outcome_serves_the_promise_without_the_flag(self):
        """Оба исхода разом — и утверждение присутствия перед отрицанием.

        До правки открытый предохранитель дал бы здесь ровно запрещённую
        пару: ``text == PROMISE`` при ``outage is False``.
        """

        outages = OpenAIProvider(api_key="fake")
        await _open_the_breaker(outages)
        with patch.object(outages, "_call_openai", AsyncMock(side_effect=AssertionError)):
            broken = await _client_turn(outages)

        healthy_provider = OpenAIProvider(api_key="fake")
        healthy_response = LLMResponse(content=LIVE_ANSWER, model="gpt-4o-mini")
        with patch.object(
            healthy_provider, "_call_openai", AsyncMock(return_value=healthy_response)
        ):
            healthy = await _client_turn(healthy_provider)

        replies = [broken, healthy]

        # Стража присутствия на тех же данных: обещающий текст в выборке
        # ЕСТЬ. Без этой строки «нигде нет обещания без флага» проходило бы
        # и на выборке, где обещания нет вовсе.
        assert any(reply["text"] == PROMISE for reply in replies)
        # И утверждение отсутствия, которое теперь что-то значит.
        assert [r for r in replies if r["text"] == PROMISE and r["outage"] is False] == []


# --------------------------------------------------------------------------- #
# Замер по коду — числом, тем же способом, что в DRF-1489                     #
# --------------------------------------------------------------------------- #
def _source_of(module) -> str:
    path = inspect.getsourcefile(module)
    assert path is not None
    return Path(path).read_text(encoding="utf-8")


#: Имена аргументов, через которые текст уезжает вызывающему.
_TEXT_KWARGS = frozenset({"text", "content"})


def _getter_name(call: ast.Call) -> str | None:
    """Имя предлагающего геттера, если вызов — это он.

    Список берётся из :data:`apps.orchestrator.llm.templates.BUTTON_BEARING_GETTERS`,
    то есть из модуля, который владеет строками, — а не пересказывается здесь.
    """

    fn = call.func
    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
    return name if name in templates.BUTTON_BEARING_GETTERS else None


def _offering_returns(source: str) -> list[tuple[int, str, bool]]:
    """Возвраты, чей человеку показываемый текст берётся из предлагающего шаблона.

    Возвращает ``(строка, имя геттера, стоит ли outage=True)`` — та же тройка,
    что в замере DRF-1489, чтобы числа были сопоставимы.

    Разбор по AST, а не по регулярке, и с учётом ПРОМЕЖУТОЧНОГО ИМЕНИ: до
    правки провайдер клал строку в локальную ``template`` и только потом
    отдавал её как ``content=template``. Детектор, который смотрит лишь на
    прямой вызов в аргументе, тот самый дефект бы и проглядел.
    """

    found: dict[tuple[int, str], tuple[int, str, bool]] = {}
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        aliases: dict[str, str] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                getter = _getter_name(node.value)
                if getter is None:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = getter
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            getter = None
            for kw in call.keywords:
                if kw.arg not in _TEXT_KWARGS:
                    continue
                if isinstance(kw.value, ast.Call):
                    getter = _getter_name(kw.value)
                elif isinstance(kw.value, ast.Name):
                    getter = aliases.get(kw.value.id)
                if getter is not None:
                    break
            if getter is None:
                continue
            outage_kw = next((kw for kw in call.keywords if kw.arg == "outage"), None)
            flagged = (
                outage_kw is not None
                and isinstance(outage_kw.value, ast.Constant)
                and outage_kw.value.value is True
            )
            found[(node.lineno, getter)] = (node.lineno, getter, flagged)
    return sorted(found.values())


def _referenced_names(source: str) -> set[str]:
    """Все идентификаторы модуля — импорты, обращения, атрибуты.

    Текст в докстроке идентификатором не является, поэтому рассказ о старом
    поведении в комментарии эту проверку не ломает.
    """

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
    return names


#: Исходник провайдера ДО правки — ровно той формы, что была на dev.
#: Нужен как калибровка детектора: ноль ниже значит что-то лишь потому,
#: что на этом куске детектор срабатывает.
_PRE_FIX_SOURCE = """
async def complete(self, messages, *, model=None, **kwargs):
    try:
        return await with_circuit_breaker(_BREAKER_NAME, self._call_openai, messages)
    except BreakerOpenError:
        template = get_fallback(self.fallback_lang)
        return LLMResponse(content=template, model=chosen_model, is_fallback=True)
"""

#: И вторая калибровка — законный вид того же места: предложение с флагом.
_FLAGGED_SOURCE = """
def _degraded():
    return _reply(text=get_fallback("ru"), outage=True)
"""


class TestTheDetectorCanFail:
    """Калибровка замера.

    Замер, который не умеет срабатывать, отчитывается нулём о чём угодно —
    ровно та ловушка, ради которой DRF-1489 держит свои стражи на проверках.
    """

    def test_pre_fix_provider_source_is_reported(self):
        found = _offering_returns(_PRE_FIX_SOURCE)

        assert len(found) == 1
        _line, getter, flagged = found[0]
        assert getter == "get_fallback"
        assert flagged is False

    def test_a_flagged_site_is_seen_and_not_reported_as_a_violation(self):
        found = _offering_returns(_FLAGGED_SOURCE)

        assert len(found) == 1
        assert found[0][1] == "get_fallback"
        assert found[0][2] is True

    def test_the_name_check_sees_a_module_that_does_speak(self):
        """Парная к проверке «провайдер не трогает шаблоны»: на модуле,
        который их трогает, она обязана срабатывать."""

        assert templates.BUTTON_BEARING_GETTERS <= _referenced_names(_source_of(concierge))


class TestZeroOfferingReturnsInTheProvider:
    def test_provider_returns_no_offering_text_at_all(self):
        """До правки таких возвратов был один. Должно стать ноль."""

        offering = [
            (line, getter)
            for line, getter, flagged in _offering_returns(_source_of(provider_module))
            if not flagged
        ]

        assert offering == [], f"предложение без кнопки: {offering}"

    def test_provider_does_not_touch_the_client_facing_templates(self):
        """Сильнее предыдущего и переживает переписывание ``complete()``.

        Слова, обращённые к человеку, в этом модуле не рождаются вовсе —
        ни импортом, ни через ``templates.``. Значит и обещание здесь
        неоткуда взять.
        """

        touched = templates.BUTTON_BEARING_GETTERS & _referenced_names(_source_of(provider_module))

        assert touched == set(), f"провайдер снова говорит с человеком: {sorted(touched)}"

    def test_the_offering_lines_are_still_shipped_where_the_button_is(self):
        """Замер не должен проходить оттого, что обещание вычищено из продукта.

        Число из DRF-1489, тем же разбором: в консьерже два предлагающих
        возврата, и оба с ``outage=True``.
        """

        concierge_returns = _offering_returns(_source_of(concierge))

        assert len(concierge_returns) == 2
        assert all(flagged for _line, _getter, flagged in concierge_returns)
        assert {getter for _line, getter, _flagged in concierge_returns} == set(
            templates.BUTTON_BEARING_GETTERS
        )
