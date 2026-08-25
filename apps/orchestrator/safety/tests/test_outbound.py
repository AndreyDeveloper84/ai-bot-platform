"""Checking the reply before a person reads it (DRF-1061 step 1).

There was no outbound check anywhere in the project — `gate.py` held
`evaluate_inbound` and nothing else. Every boundary the prompts describe
rested on the model choosing to obey, and the failure was silent: a person
reads a confident medical claim, and nothing in the logs says so.

The tests weigh two things equally. The catches, obviously. But also the
**passes**: a filter that mangles ordinary replies gets switched off within
a week, and then there is no filter at all. Half of these pin sentences the
assistant must still be allowed to say.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT, evaluate_outbound


class TestMedicalClaims:
    @pytest.mark.parametrize(
        "text",
        [
            "У вас аллергия на этот состав, лучше не делать.",
            "Примите ибупрофен за час до процедуры.",
            "У клиентки грибок, ей нельзя.",
            "Выпейте что-нибудь обезболивающее перед визитом.",
        ],
    )
    def test_a_diagnosis_or_a_prescription_is_stopped(self, text):
        verdict = evaluate_outbound(text)

        assert verdict.blocked
        assert verdict.text == REPLACEMENT_TEXT
        assert "medical" in verdict.categories

    @pytest.mark.parametrize(
        "text",
        [
            "Противопоказания лучше обсудить с врачом.",
            "Если есть аллергия, предупредите мастера заранее.",
            "Завтра три записи: 10:00, 13:00 и 16:30.",
            "Свободное окно на два часа есть в четверг с 14:00.",
        ],
    )
    def test_ordinary_replies_pass(self, text):
        """The half that keeps the filter alive."""

        verdict = evaluate_outbound(text)

        assert verdict.allowed
        assert verdict.text == text


class TestPromisesOnTheSalonsBehalf:
    @pytest.mark.parametrize(
        "text",
        [
            "Гарантирую, что результат вам понравится.",
            "Вернём деньги, если не понравится.",
            "Обещаю, всё будет отлично.",
            "Сделаем скидку 20% за ожидание.",
        ],
    )
    def test_a_promise_is_stopped(self, text):
        verdict = evaluate_outbound(text)

        assert verdict.blocked
        assert "promise" in verdict.categories

    def test_describing_the_salons_policy_is_not_a_promise(self):
        # Reporting what the salon does ≠ committing the salon to it.
        verdict = evaluate_outbound("Условия возврата уточните у администратора.")

        assert verdict.allowed


class TestContactDetails:
    @pytest.mark.parametrize(
        "text",
        [
            "Телефон клиентки +7 999 123-45-67.",
            "Позвоните по 8(999)1234567.",
            "Напишите ей на olga@example.com.",
        ],
    )
    def test_contacts_never_go_out(self, text):
        verdict = evaluate_outbound(text)

        assert verdict.blocked
        assert "contact" in verdict.categories

    def test_a_time_is_not_a_phone_number(self):
        verdict = evaluate_outbound("Запись в 10:00, следующая в 13:30.")

        assert verdict.allowed


class TestPartialPhoneNumbers:
    """The four-digit tail is still a phone (DRF-1039 / OD-W2-2, DRF-1209).

    The live leak this comes from: a truncated excerpt left the last four
    digits of a customer's number readable, and the owner decision allows
    no "identifier" exception for a partial number. But a bare four-digit
    group is also a price, a year, a duration — so the tail only counts
    when the sentence itself says it is a phone.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Записала вас, ваш номер 4567.",
            "Подтверждение придёт на номер 45-67.",
            "Перезвоните мне, тел. 1234.",
            "Телефон: 1234.",
            "Номер телефона клиентки 1234.",
            "Телефон клиентки 1234.",
            "Номер клиентки заканчивается на 4567.",
            "Помню последние 4 цифры: 4567.",
        ],
    )
    def test_a_marked_tail_is_stopped(self, text):
        verdict = evaluate_outbound(text)

        assert verdict.blocked
        assert "contact" in verdict.categories

    @pytest.mark.parametrize(
        "text",
        [
            # digits that are not phones — the class that decides whether
            # this guard survives a real pilot
            "Стоимость процедуры 1500 ₽, оплата на месте.",
            "Салон открыт с 9 до 21 ежедневно.",
            "В 2024 году салон открыл второй филиал.",
            "Сегодня вы записали 1 200 ккал, это ниже вашей нормы.",
            "Курс из 10 сеансов обычно берут, чтобы эффект держался.",
            # numbers that are not phones even next to the word «номер»
            "Ваш номер заказа 1234 сохранён.",
            "Номер записи 1234 уточните у администратора.",
            # a one-time code is not a phone tail
            "Код подтверждения 4521 введите в приложении.",
            # a four-digit group inside an identifier is not a phone either
            "Идентификатор c4202567-6706-417c-affe-1234567890ab.",
        ],
    )
    def test_digits_without_a_phone_marker_pass(self, text):
        verdict = evaluate_outbound(text)

        assert verdict.allowed
        assert verdict.text == text


class TestBehaviour:
    def test_the_reply_is_replaced_not_edited(self):
        """Cutting a sentence can invert what is left of the paragraph."""

        verdict = evaluate_outbound("Завтра два клиента. Гарантирую отличный результат.")

        assert verdict.text == REPLACEMENT_TEXT
        assert "Завтра два клиента" not in verdict.text

    def test_several_categories_are_all_reported(self):
        verdict = evaluate_outbound("Гарантирую результат, звоните +7 999 123-45-67.")

        assert set(verdict.categories) == {"promise", "contact"}

    def test_empty_text_is_left_alone(self):
        assert evaluate_outbound("").allowed
        assert evaluate_outbound("   ").allowed

    def test_a_broken_check_never_eats_the_answer(self, monkeypatch):
        """A crashing safety check must not be what costs someone a reply."""

        import apps.orchestrator.safety.outbound as mod

        def boom(*_a, **_kw):
            raise RuntimeError("regex engine on fire")

        monkeypatch.setattr(mod.re, "search", boom)
        verdict = evaluate_outbound("Завтра три записи.")

        assert verdict.allowed
        assert verdict.text == "Завтра три записи."
