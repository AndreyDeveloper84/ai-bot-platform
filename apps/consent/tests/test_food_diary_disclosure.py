"""Раскрытие дневника питания: слова владельца и совпадение поверхностей — Z9.

## Зачем этот файл существует

Gate A требует, чтобы текст раскрытия совпадал **на всех поверхностях**.
Поверхностей две — питоновский модуль (источник истины) и его копия в
TypeScript, потому что Python и TS не делят модуль. Копия, которая молча
разошлась с оригиналом, — самый тихий из возможных дефектов: никто не
падает, просто человеку в чате и в приложении обещают разное.

## Почему сверка живёт ЗДЕСЬ, а не в моём скрипте

Она у меня была — и работала — во временном скрипте. Сторож во временном
каталоге не сторожит ничего после того, как окно закрылось: он не
запускается в CI, его не увидит следующий читатель, и его отсутствие будет
неотличимо от его молчания.

## Почему слова сверяются ЛИТЕРАЛАМИ

Проба показала слепое место в соседнем наборе: узел, который сверял экран
с константой того же модуля, оставался зелёным, когда менялась сама
константа, — он сравнивал модуль с самим собой. Слова владельца здесь
записаны буквами. Если текст решат менять, этот файл обязан покраснеть и
потребовать решения, а не подстроиться.

## Чего этот файл НЕ проверяет

Значение версии. Схема (``food-diary-v<N>``) проверяется, конкретный номер
— нет: он принадлежит существующей схеме версий согласий, а не этому
тексту, и меняется вместе с ней.
"""

from __future__ import annotations

import pathlib
import re

from apps.consent import food_diary_disclosure as fd

#: Копия текста для мини-приложения. Адрес заявлен здесь, потому что
#: молчание про отсутствующий файл и молчание про совпадающий текст
#: выглядят одинаково — тест ниже требует, чтобы файл существовал.
TS_COPY = (
    pathlib.Path(__file__).resolve().parents[3]
    / "apps"
    / "miniapp"
    / "src"
    / "lib"
    / "food-diary-disclosure.ts"
)

#: Клиент половины 1 — единственное место, где версия объявлена литералом
#: на стороне TypeScript.
TS_CLIENT = TS_COPY.with_name("food-scanner.ts")


def _ts_body(text: str) -> list[str]:
    """Абзацы из TS-копии.

    Литералы склеиваются через ``+``, и в тексте раскрытия кавычек нет —
    поэтому деление по кавычке однозначно: нечётные куски суть содержимое.
    """
    block = text.split("DISCLOSURE_BODY: readonly string[] = [", 1)[1]
    block = block.split("\n];", 1)[0]
    parts = block.split('"')
    elements: list[str] = []
    current = ""
    for i in range(1, len(parts), 2):
        current += parts[i]
        tail = parts[i + 1] if i + 1 < len(parts) else ""
        if "," in tail:
            elements.append(current)
            current = ""
    if current:
        elements.append(current)
    return elements


class TestTheOwnersWords:
    def test_the_buttons_are_worded_exactly_as_decided(self) -> None:
        """«Разрешить» и «Не сейчас» — дословно, а не пересказом."""
        assert fd.BUTTON_GRANT == "Разрешить"
        assert fd.BUTTON_DECLINE == "Не сейчас"

    def test_no_statement_is_empty(self) -> None:
        """Пустой абзац — не утверждение, а место, где его нет."""
        assert fd.DISCLOSURE_BODY
        assert all(p.strip() for p in fd.DISCLOSURE_BODY)

    def test_the_version_follows_the_existing_scheme(self) -> None:
        """Схема — та же, что у соседних согласий. Номер здесь не предмет."""
        assert re.fullmatch(r"food-diary-v\d+", fd.FOOD_DIARY_DISCLOSURE_VERSION)

    def test_the_version_is_the_registry_constant_not_a_second_definition(self) -> None:
        """Одна версия на реестр и раскрытие — псевдоним, а не своё значение.

        ``is`` намеренно: равенство двух строк зеленело бы и на двух
        литералах, которые сегодня случайно совпали. Здесь утверждается,
        что объекта два быть не может.
        """
        from apps.consent.nutrition import FOOD_DIARY_CONSENT_DOCUMENT_VERSION

        assert fd.FOOD_DIARY_DISCLOSURE_VERSION is FOOD_DIARY_CONSENT_DOCUMENT_VERSION

    def test_the_text_says_both_text_and_photo_are_kept(self) -> None:
        """Первое обязательное утверждение: и текст, и фотография."""
        joined = fd.disclosure_text()
        assert "текстом" in joined
        assert "фотограф" in joined

    def test_the_text_does_not_promise_immediate_deletion(self) -> None:
        """Запрет владельца: application-side purge не заявлять, пока его нет.

        ПРИСУТСТВИЕ впереди: текст собран и не пуст — иначе утверждение об
        отсутствии было бы зелено и на пустой строке.
        """
        joined = fd.disclosure_text()
        assert "дневник" in joined
        assert len(joined) > 200

        low = joined.lower()
        for forbidden in (
            "сразу после распозна",
            "удаляю фото сразу",
            "немедленно удал",
        ):
            assert forbidden not in low


class TestEveryObligatoryStatementIsPresent:
    """По узлу на каждое требование владельца — по СМЫСЛУ, а не по счёту.

    Число абзацев было прокси для содержания и ошибалось в обе стороны:
    сведи два утверждения в один абзац — текст верен, а сторож по числу
    краснеет; разбей одно надвое — сторож молчит, а требование могло
    потеряться. Предмет требования — «текст честно говорит вот это», и
    проверяется здесь именно он.

    Каждое утверждение ищется во ВСЁМ тексте, а не в назначенном абзаце:
    перегруппировка абзацев требования не отменяет, а привязка к номеру
    абзаца сделала бы сторож зеркалом сегодняшней вёрстки.

    Спор «пять пунктов §5 против шести переданных» для этих узлов не
    существует вовсе: они не считают, они спрашивают. Вопрос о составе
    остаётся, но он о содержании и решается не сторожем.
    """

    @staticmethod
    def _text() -> str:
        return fd.disclosure_text().lower()

    def test_says_food_is_taken_from_text_and_from_photo(self) -> None:
        """(1) И введённое текстом, и присланное фотографией."""
        text = self._text()
        assert "текстом" in text
        assert "фотограф" in text

    def test_says_the_photo_is_used_to_recognise_the_dish(self) -> None:
        """(2) Фотография нужна, чтобы распознать еду."""
        assert "распозна" in self._text()

    def test_says_the_photo_may_be_stored_up_to_thirty_days(self) -> None:
        """(3) Срок хранения назван, и назван честно — до 30 дней."""
        text = self._text()
        assert "30 дней" in text
        assert "хранит" in text or "хранил" in text

    def test_says_the_record_is_kept_apart_from_the_photo(self) -> None:
        """(4) Запись о еде и снимок хранятся раздельно."""
        assert "отдельно" in self._text()

    def test_says_consent_can_be_withdrawn(self) -> None:
        """(5) Право на отзыв названо прямо."""
        assert "отозвать" in self._text()

    def test_says_nothing_is_processed_anew_after_withdrawal(self) -> None:
        """(6) Отзыв — граница БУДУЩЕЙ обработки, и это сказано.

        Уточнение владельца: после отзыва ни данные дневника, ни
        фотография в новую обработку не идут. Прежняя редакция говорила
        только про срок хранения — то есть про наше бездействие, а не про
        нашу обязанность.
        """
        text = self._text()
        assert "после отзыва" in text
        assert "не использую" in text
        assert "новой обработки" in text


class TestBothSurfacesSayTheSame:
    def test_the_typescript_copy_exists(self) -> None:
        """Отсутствующий файл и совпадающий текст молчат одинаково."""
        assert TS_COPY.is_file(), f"нет копии раскрытия: {TS_COPY}"

    def test_the_two_copies_are_identical_paragraph_by_paragraph(self) -> None:
        """Условие Gate A: текст совпадает на ВСЕХ поверхностях."""
        text = TS_COPY.read_text(encoding="utf-8")
        ts_body = _ts_body(text)

        # ПРИСУТСТВИЕ: копия разобралась и непуста — иначе равенство двух
        # пустых списков объявило бы поверхности совпавшими.
        #
        # Непустота, а НЕ «ровно шесть»: счёт абзацев — прокси для
        # содержания, и здесь он не нужен вовсе. Равенство списков ниже
        # ловит и расхождение длины, и расхождение текста, ничего не
        # утверждая про число, которое владелец не называл свойством.
        assert ts_body, "копия не разобралась: абзацев не найдено"
        assert list(fd.DISCLOSURE_BODY) == ts_body

    def test_the_version_matches_on_both_surfaces(self) -> None:
        """Версия — тоже текст, и расходится она так же молча.

        Клиентская копия раскрытия НЕ объявляет версию сама (иначе их стало
        бы три), а берёт её из ``food-scanner.ts`` — литерала половины 1.
        Сверяется тот литерал: он обязан быть равен константе реестра.
        """
        copy = TS_COPY.read_text(encoding="utf-8")
        # ПРИСУТСТВИЕ: копия ссылается на константу половины 1 по имени.
        assert "FOOD_DIARY_CONSENT_DOCUMENT_VERSION as FOOD_DIARY_DISCLOSURE_VERSION" in copy
        # ОТСУТСТВИЕ: своего литерала версии в копии нет.
        assert 'FOOD_DIARY_DISCLOSURE_VERSION = "' not in copy

        client = TS_CLIENT.read_text(encoding="utf-8")
        ts_version = client.split('FOOD_DIARY_CONSENT_DOCUMENT_VERSION = "', 1)[1].split('"', 1)[0]
        assert ts_version == fd.FOOD_DIARY_DISCLOSURE_VERSION

    def test_the_buttons_match_on_both_surfaces(self) -> None:
        """Кнопка, названная на экране иначе, — уже другое согласие."""
        text = TS_COPY.read_text(encoding="utf-8")
        grant = text.split('BUTTON_GRANT = "', 1)[1].split('"', 1)[0]
        decline = text.split('BUTTON_DECLINE = "', 1)[1].split('"', 1)[0]
        assert grant == fd.BUTTON_GRANT
        assert decline == fd.BUTTON_DECLINE
