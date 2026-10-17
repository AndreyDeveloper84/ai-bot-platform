"""DRF-2728 — сестринские формы к тем, что сторожа уже ловили.

Аудит нашёл не новые темы, а дыры СИММЕТРИИ: правило ловило одну форму
фразы и пропускало соседнюю — «у вас диабет» ловилось, «у вас явно диабет»
нет; «вернём деньги» ловилось, «верну вам деньги» нет.

Каждая группа ниже держит три вещи сразу, и все три нужны:

* **прежняя форма по-прежнему ловится** — иначе «добавили сестру» могло
  значить «заменили»;
* **новая форма ловится** — литералом, репликой из листа;
* **безобидная речь того же контекста проходит** — иначе правка расширила бы
  сторожа за пределы того, что в ней заявлено. Именно эта треть вынесла из
  правки две формы (см. ``TestWhatWasLeftOutAndWhy``).

Реплики — литералы, не выведенные из шаблонов.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.safety.outbound import evaluate_outbound
from apps.orchestrator.safety.pre_check import SafetyVerdict, pre_check


def _blocked_as(text: str) -> list[str]:
    verdict = evaluate_outbound(text)
    return [] if verdict.allowed else list(verdict.categories)


def _goes_out_as_written(text: str) -> bool:
    """Сторож пропустил реплику и отдал её дословно — положительное утверждение.

    Не «список категорий пуст»: пустой список даёт и сторож, который ничего не
    проверил. Пропущенная реплика возвращается тем же текстом, подменённая —
    другим.
    """
    verdict = evaluate_outbound(text)
    return verdict.allowed is True and verdict.text == text


class TestStrengthenedDiagnosis:
    """H1 — «это точно …», «у вас явно …» называют те же болезни, что «у вас …»."""

    @pytest.mark.parametrize(
        "text",
        ["Это точно аллергия.", "У вас явно инфекция.", "У тебя явно аллергия."],
    )
    def test_the_three_conditions_it_knew_are_still_caught(self, text: str) -> None:
        assert _blocked_as(text) == ["medical"]

    @pytest.mark.parametrize(
        "text",
        [
            "Это точно рак.",
            "У вас явно диабет.",
            "У тебя явно дерматит.",
            "Это точно экзема.",
            "Это точно псориаз.",
            "У вас явно грибок.",
            "Это точно воспаление.",
        ],
    )
    def test_the_other_seven_are_caught_too(self, text: str) -> None:
        assert _blocked_as(text) == ["medical"]

    @pytest.mark.parametrize(
        "text",
        [
            "Это точно брак, переделаем.",
            "Это точно раковина, а не ванна.",
            "Это точно ракурс для фото.",
            "Это точно подойдёт.",
            "У тебя явно хороший вкус.",
            "Это точно тот мастер, которого ты искала.",
        ],
    )
    def test_ordinary_certainty_is_not_a_diagnosis(self, text: str) -> None:
        assert _goes_out_as_written(text)


class TestPromiseInTheFirstPerson:
    """H3 — ассистент говорит «я»; ловилось только «мы»."""

    @pytest.mark.parametrize(
        "text",
        ["Мы вернём деньги.", "Сделаем скидку.", "Дам скидку.", "Бесплатно переделаем."],
    )
    def test_the_plural_is_still_caught(self, text: str) -> None:
        assert _blocked_as(text) == ["promise"]

    @pytest.mark.parametrize(
        "text",
        [
            "Верну вам деньги.",
            "Я верну деньги.",
            "Возвращу деньги.",
            "Верну тебе полную стоимость.",
            "Вернём вам деньги.",
            "Сделаю скидку.",
            "Бесплатно переделаю.",
        ],
    )
    def test_the_singular_is_caught_too(self, text: str) -> None:
        assert _blocked_as(text) == ["promise"]

    @pytest.mark.parametrize(
        "text",
        [
            "Вернусь к тебе с ответом.",
            "Верну тебя в меню.",
            "Сделаю подборку мастеров.",
            "Сделаю запись на завтра.",
            "Переделаю подборку, если не подойдёт.",
            "Скидку уточни у администратора.",
        ],
    )
    def test_what_the_assistant_really_does_is_not_a_promise(self, text: str) -> None:
        assert _goes_out_as_written(text)


class TestDiagnosisRequest:
    """H5 — входящая просьба поставить диагноз, как её на самом деле говорят."""

    def test_the_form_it_knew_is_still_blocked(self) -> None:
        assert pre_check("Поставьте диагноз").verdict is SafetyVerdict.BLOCK

    @pytest.mark.parametrize(
        "text",
        [
            "Поставь диагноз",
            "Поставь мне диагноз",
            "Поставьте мне диагноз",
            "поставь мне, пожалуйста, диагноз",
            "Посмотри фото и поставь мне диагноз",
        ],
    )
    def test_the_informal_form_and_the_word_in_between_are_blocked(self, text: str) -> None:
        assert pre_check(text).verdict is SafetyVerdict.BLOCK

    @pytest.mark.parametrize(
        "text",
        [
            "Поставь напоминание на завтра",
            "Поставь мне запись на пятницу",
            "Мне поставили диагноз, можно ли на массаж?",
            "Диагноз мне поставили давно",
        ],
    )
    def test_asking_for_something_else_is_not_a_diagnosis_request(self, text: str) -> None:
        assert pre_check(text).verdict is SafetyVerdict.ALLOW


class TestNormWordedAsNado:
    """M2 — «надо» рядом с «нужно»: то же слово в разговорной форме."""

    @pytest.mark.parametrize(
        "text",
        [
            "Между курсами нужно три-четыре недели.",
            "Следует приходить через месяц.",
            "Необходимо 10 процедур.",
        ],
    )
    def test_the_modal_words_it_knew_are_still_caught(self, text: str) -> None:
        assert _blocked_as(text) == ["planning"]

    @pytest.mark.parametrize(
        "text",
        ["Между курсами надо три-четыре недели.", "Надо приходить через месяц."],
    )
    def test_nado_is_caught_too(self, text: str) -> None:
        assert _blocked_as(text) == ["planning"]

    @pytest.mark.parametrize(
        "text",
        ["Надо записаться заранее.", "Надо уточнить у мастера.", "Надо выбрать удобное время."],
    )
    def test_nado_without_a_norm_is_ordinary_speech(self, text: str) -> None:
        assert _goes_out_as_written(text)


class TestNormFromTheOtherEdge:
    """M3 — «максимум» рядом с «минимум»: та же норма с другого края."""

    def test_minimum_is_still_caught(self) -> None:
        assert _blocked_as("Минимум три недели между сеансами.") == ["planning"]

    @pytest.mark.parametrize(
        "text",
        ["Максимум три процедуры в месяц.", "Максимум два сеанса в неделю."],
    )
    def test_maximum_is_caught_too(self, text: str) -> None:
        assert _blocked_as(text) == ["planning"]

    @pytest.mark.parametrize(
        "text",
        [
            "Максимум удобно вечером.",
            "Постараюсь по максимуму помочь.",
            "В группе максимум шесть человек.",
        ],
    )
    def test_maximum_without_a_plan_unit_is_ordinary_speech(self, text: str) -> None:
        assert _goes_out_as_written(text)


class TestPreparationCountedInHours:
    """Подготовка «за N до …» — та же, счёт в часах и минутах."""

    @pytest.mark.parametrize(
        "text",
        ["За два дня до процедуры не загорай.", "За сутки до процедуры нельзя пить."],
    )
    def test_days_are_still_caught(self, text: str) -> None:
        assert _blocked_as(text) == ["planning"]

    @pytest.mark.parametrize(
        "text",
        [
            "За два часа до процедуры не ешь.",
            "За 30 минут до сеанса не пей.",
            "За два часа до процедуры нельзя есть.",
            "За час до сеанса воздержись от кофе.",
            "За полчаса до процедуры не пейте воду.",
        ],
    )
    def test_hours_and_minutes_are_caught_too(self, text: str) -> None:
        assert _blocked_as(text) == ["planning"]

    @pytest.mark.parametrize(
        "text",
        [
            # Салон о себе: длительность, приход, отмена, напоминание, окна.
            "Сеанс длится 60 минут.",
            "Процедура занимает два часа.",
            "Приходи за 10 минут до начала.",
            "Нужно прийти за 10 минут до начала.",
            "За 10 минут до начала нужно быть на месте.",
            "Лучше прийти за 15 минут, чтобы спокойно переодеться.",
            "Отменить запись можно не позднее чем за 2 часа.",
            "Запись нужно отменить минимум за 2 часа.",
            "Отменить запись можно за 2 часа до визита, позже нельзя.",
            "Перенести можно не позднее чем за 3 часа до сеанса, потом нельзя.",
            "За час до визита напомню.",
            "Напомню за два часа до визита.",
            "За два часа до визита не забудь подтвердить запись.",
            "За 15 минут до сеанса не опаздывай.",
            "Есть окно через час.",
            "Мастер освободится через 30 минут.",
            "Слоты идут каждые 30 минут — нужно выбрать удобный.",
            "Массаж 90 минут стоит 3500 рублей.",
        ],
    )
    def test_what_a_salon_says_about_itself_in_hours_passes(self, text: str) -> None:
        assert _goes_out_as_written(text)


class TestWhatWasLeftOutAndWhy:
    """Две формы из листа в правку НЕ вошли — и это держится узлом, не словом.

    Обе измерены на этих самых репликах: с ними блокировалась обычная речь
    салона. Вносить их можно только уже́, чем «добавить слово в список», и это
    решение не этой правки.
    """

    @pytest.mark.parametrize(
        "text",
        [
            # «стоит» в _MODAL: у слова второе значение — цена.
            "Массаж стоит 2500 рублей.",
            "Курс стоит 15000 рублей за 10 процедур.",
            "Процедура стоит пять тысяч, в абонементе восемь сеансов.",
            "Сколько стоит 5 сеансов?",
            "Стоит записаться заранее.",
        ],
    )
    def test_a_price_is_not_a_norm(self, text: str) -> None:
        assert _goes_out_as_written(text)

    @pytest.mark.parametrize(
        "text",
        [
            # час и минута в общем списке единиц (_UNIT): любое «нужно … N минут».
            "Нужно прийти за 10 минут до начала.",
            "Запись нужно отменить минимум за 2 часа.",
            "Обязательно предупреди за 2 часа, если не сможешь прийти.",
            "Рекомендую это окно: следующее будет только через 3 часа.",
        ],
    )
    def test_a_duration_next_to_a_modal_word_is_not_a_norm(self, text: str) -> None:
        assert _goes_out_as_written(text)
