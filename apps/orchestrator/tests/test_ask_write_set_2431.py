"""Поток вопросов пишет семь полей — и это половина границы риска (DRF-2431).

## Зачем узел существует

Прежние строки с пометкой ``conversational`` ночной вывод каталога вправе
перезаписать: там охраняются только ``explicit`` и ``erased``. Звучит это
широко, но риск узок, и держат его **два** независимых факта из **разных**
репозиториев:

* в каталоге: вывод пишет только ``favorite_masters`` и ``busy_days``
  (``users/tests/test_inference_write_set_2431.py``);
* здесь: поток вопросов **не умеет** писать ``favorite_masters`` — и не умел
  никогда, начиная с рождения модуля (коммит ``7b55e828``). Ответ на такой
  вопрос нечем привязать к значениям контракта, поэтому его и не задают.

Пересечение двух наборов — **одно поле, ``busy_days``**. Оно и обсуждается
с владельцем.

**Расширь любой из наборов — пересечение вырастет молча**, и цена листа
изменится, никого не предупредив. Узел ниже держит здешнюю половину.

## Что узел НЕ утверждает

Что писать ``favorite_masters`` из чата нельзя в принципе. Можно — но это
решение, у которого есть цена в DRF-2431, и принимать его надо глазами,
а не мимоходом добавив разборщик.
"""

from __future__ import annotations

from apps.orchestrator import memory_ask

#: Поля, ответ на которые поток вопросов умеет разобрать и сохранить.
ASKABLE = {
    "preferred_time_slots",
    "price_range_max",
    "workplace_district",
    "home_district",
    "busy_days",
    "min_rating_preference",
    "diet_type",
}

#: Поле, которое ночной вывод каталога пересобирает целиком. Пересечение с
#: :data:`ASKABLE` обязано оставаться пустым.
REBUILT_BY_NIGHTLY_INFERENCE = {"favorite_masters"}


class TestTheAskFlowWriteSetIsHalfTheBoundary:
    def test_the_set_has_not_grown(self) -> None:
        """Наличие раньше отсутствия: набор непуст и равен ожидаемому."""
        assert set(memory_ask._FIELD_PARSERS) == ASKABLE

    def test_the_rebuilt_field_is_never_askable(self) -> None:
        """Главный узел: пересечение с пересобираемым полем — пусто.

        Появись у ``favorite_masters`` разборщик — человек начнёт называть
        список, который ночной проход собирает заново целиком, и «сказанное»
        с «выведенным» столкнутся на одном поле.
        """
        assert not (set(memory_ask._FIELD_PARSERS) & REBUILT_BY_NIGHTLY_INFERENCE)

    def test_what_the_flow_writes_is_stated_not_inferred(self) -> None:
        """И вторая половина того же: ответы уходят как «сказал сам».

        До DRF-2397 поток писал ``conversational`` — имя, которое этот
        репозиторий трижды объявил выводом. Именно те строки и оставил
        DRF-2431; новых больше не появляется.
        """
        from apps.orchestrator.memory_block import BACKEND_STATED_SOURCE

        assert BACKEND_STATED_SOURCE == "explicit"

        import inspect

        source = inspect.getsource(memory_ask)
        assert "BACKEND_STATED_SOURCE" in source
        assert '"source": "conversational"' not in source
