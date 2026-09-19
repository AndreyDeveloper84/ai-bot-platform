"""Примеры голоса и стоп-группы §7.1 — признание + мягкий стоп, не расчёт (DRF-2104).

Пример «у меня анорексия в анамнезе» отвечал «программа поддержания, без
счёта калорий … пройдёмся по анкете и подберём аккуратно» — против решения
владельца 09.09 §7.1: расстройство пищевого поведения — стоп-сценарий,
Ayla ориентиры не считает и в анкету не ведёт; «РПП → maintain» снята.
Пример учит того, кто подключит примеры к промпту: модуль
``apps.promptreg.voice_examples`` в рантайме сегодня не импортируется —
канонический артефакт, не живой промпт.

Словарь стоп-групп здесь — свой, по §7.1 анкеты (``fsm.py``
``_SCREENING_CHOICES``, ``ADULT_AGE``): РПП, несовершеннолетие,
беременность / кормление. Это НЕ S1 классификатора (``health_screening``,
G1–G7 — острые неотложные состояния): там РПП группы нет.

* s1 — страж-перепись всех пулов: реплика человека из стоп-группы →
  ответ без обещания расчёта, со «специалист» и «дневник»; положительно —
  словарь РПП находит ≥ 1 живой пример, иначе квантор пуст;
* s2 — ложный вход: старый текст примера → нарушение; новый → 0; пробы
  для несовершеннолетия и беременности с обещанием расчёта → нарушение
  (в живых пулах таких примеров нет — правило держится пробами);
* s3 — переписанный пример: текст выбран главным окном (V1 без родовой
  формы), без «поддержан» / «анкет» / цифр, со «специалист» и «Дневник»;
* s4 — пример booking «варикоз и второй триместр» — стоп-слово в реплике
  человека, ответ передаёт менеджеру подбор ПРОЦЕДУРЫ (S5, не расчёт):
  не нарушение — для пулов записи страж требует только отсутствия
  обещания расчёта, маркеры «специалист» / «дневник» — про питание;
* s5 — пул анкеты не менялся в составе: три примера, РПП-пример на месте.
"""

from __future__ import annotations

import re

from ayla_ai_core import Example

from apps.promptreg import voice_examples

#: §7.1: расстройство пищевого поведения. Не S1.
_EATING_DISORDER = re.compile(
    r"анорекс|булим|\bрпп\b|расстройств\w*\s+пищев|компульсивн\w*\s+переедан|орторекс", re.I
)
#: §7.1: несовершеннолетие (``ADULT_AGE = 18``).
_MINOR = re.compile(r"\bмне\s+1[0-7]\b|\b1[0-7]\s+лет\b|несовершеннолетн", re.I)
#: §7.1: беременность / кормление — стоп, не «maintain».
_PREGNANCY_NURSING = re.compile(r"беремен|кормлю|грудн\w*\s+вскармл|лактац", re.I)
_STOP_GROUPS = {
    "eating_disorder": _EATING_DISORDER,
    "minor": _MINOR,
    "pregnancy_nursing": _PREGNANCY_NURSING,
}

#: Обещание продолжить расчёт — то, чего в стоп-ветке быть не может: «программа
#: поддержания», «пройдёмся по анкете», «в спокойном темпе», числа ккал. Слов
#: «ориентир» / «считать» / «подобрать» здесь нет намеренно: стоп формулируется
#: через первые два в отрицании («ориентир считать не буду»), а «подберёт
#: процедуру» — законный ответ записи; страж синтаксис не разбирает.
_CALCULATION_PROMISE = re.compile(r"поддержан|анкет|программ|темп|ккал", re.I)
#: Что обязан сказать стоп о питании: кто называет числа и что дневник остаётся.
_STOP_MARKERS = ("специалист", "дневник")

#: Пулы, где стоп — про расчёт питания (маркеры обязательны). Остальные пулы
#: (запись, боль) — только без обещания расчёта.
_NUTRITION_INTENTS = frozenset(
    {
        "food_scanner",
        "food_recognition",
        "food_correction",
        "water",
        "water_tracker",
        "nutrition_anketa",
    }
)


def stop_group_violations(examples: list[Example], *, nutrition: bool = True) -> list[str]:
    """Нарушения §7.1 в примерах: стоп-группа в реплике → ответ обязан быть стопом."""
    violations: list[str] = []
    for example in examples:
        groups = [name for name, rx in _STOP_GROUPS.items() if rx.search(example.user)]
        if not groups:
            continue
        low = example.assistant.lower()
        promise = _CALCULATION_PROMISE.search(example.assistant)
        missing = [m for m in _STOP_MARKERS if m not in low] if nutrition else []
        if promise or missing:
            violations.append(
                f"{example.user!r} [{','.join(groups)}]: "
                f"обещание={promise.group(0) if promise else None}, нет={missing}"
            )
    return violations


def _all_violations() -> tuple[int, list[str]]:
    """(сколько примеров прочитано, нарушения) по всем пулам роутера."""
    seen = 0
    violations: list[str] = []
    for intent, pool in voice_examples._INTENT_POOL.items():
        seen += len(pool)
        violations += [
            f"{intent}: {v}"
            for v in stop_group_violations(pool, nutrition=intent in _NUTRITION_INTENTS)
        ]
    return seen, violations


_OLD_TEXT = (
    "Спасибо, что доверилась. С таким анамнезом нормы на снижение "
    "не ставлю — программа поддержания, без счёта калорий, с акцентом "
    "на стабильности. Пройдёмся по анкете и подберём аккуратно."
)
_NEW_TEXT = (
    "Спасибо, что говоришь об этом — это важно. Тогда персональный ориентир "
    "считать не буду: в таких случаях числа должен называть специалист, который "
    "тебя ведёт. Дневник остаётся — записывай еду и воду, я посчитаю, сколько "
    "вышло за день, без дневной цели и без процентов."
)


class TestS1EveryStopGroupExampleStops:
    def test_no_calculation_promise_after_a_stop_group_utterance(self) -> None:
        seen, violations = _all_violations()
        assert seen >= 21  # положительно: пулы прочитаны
        # Квантор непустой: словарь РПП находит живой пример.
        assert any(_EATING_DISORDER.search(e.user) for e in voice_examples.ANKETA_EMPATHY_EXAMPLES)
        assert violations == []


class TestS2TheGuardCatchesBothDirections:
    def test_the_old_text_is_a_violation_and_the_new_one_is_not(self) -> None:
        user = "у меня анорексия в анамнезе"
        old = stop_group_violations([Example(user=user, assistant=_OLD_TEXT)])
        assert len(old) == 1 and "eating_disorder" in old[0]
        # Та же реплика, тот же словарь — стоп-группа найдена строкой выше
        # (старый текст даёт нарушение); пустота здесь — свойство нового текста.
        new = stop_group_violations([Example(user=user, assistant=_NEW_TEXT)])
        assert new == []  # empty-assert-ok: the old text on the same user is a violation above

    def test_minor_and_pregnancy_probes(self) -> None:
        promise = "Отлично, пройдёмся по анкете и подберём программу."
        # Пул записи: то же обещание — нарушение и там (без маркеров).
        assert stop_group_violations(
            [Example(user="я беременна", assistant=promise)], nutrition=False
        )
        stop = "Тогда ориентир считать не буду — числа назовёт специалист, который тебя ведёт. Дневник остаётся."
        for user in ("мне 16, хочу похудеть", "я беременна, хочу нормы", "кормлю грудью"):
            assert stop_group_violations([Example(user=user, assistant=promise)]), user
            assert stop_group_violations([Example(user=user, assistant=stop)]) == [], user
        # Стоп без маркеров — тоже нарушение: «не буду» без «специалист» и «дневник»
        # оставляет человека ни с чем.
        bare = "Тогда считать не буду."
        assert stop_group_violations([Example(user="мне 16", assistant=bare)])


class TestS3TheRewrittenExample:
    def test_text_is_the_chosen_variant(self) -> None:
        example = next(
            e for e in voice_examples.ANKETA_EMPATHY_EXAMPLES if _EATING_DISORDER.search(e.user)
        )
        assert example.assistant == _NEW_TEXT
        low = example.assistant.lower()
        assert "поддержан" not in low and "анкет" not in low
        assert not re.search(r"\d", example.assistant)
        assert "специалист" in low and "Дневник" in example.assistant


class TestS4AStopWordWithoutAPromiseIsNotAViolation:
    def test_the_booking_pregnancy_example_passes_as_is(self) -> None:
        example = next(
            e for e in voice_examples.BOOKING_EXAMPLES if _PREGNANCY_NURSING.search(e.user)
        )
        assert "антицеллюлитный нельзя" in example.assistant  # положительно: тот самый пример
        assert stop_group_violations([example], nutrition=False) == []
        # Тот же ответ в пуле питания был бы нарушением: без «специалист» и «дневник».
        assert stop_group_violations([example], nutrition=True)


class TestS5ThePoolKeepsItsShape:
    def test_three_anketa_examples_with_the_stop_one_last(self) -> None:
        pool = voice_examples.ANKETA_EMPATHY_EXAMPLES
        assert len(pool) == 3
        assert _EATING_DISORDER.search(pool[-1].user)
        assert voice_examples._INTENT_POOL["nutrition_anketa"] is pool
