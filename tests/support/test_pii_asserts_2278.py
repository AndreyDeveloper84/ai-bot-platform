"""DRF-2278 — хвост телефона ищется в видимом тексте ответа, а не по сырому JSON.

Прежние проверки ``assert "5544" not in raw`` падали без утечки: в сыром ответе
лежат случайные UUID и токены, и четыре цифры хвоста изредка оказывались внутри
них (~4·10⁻⁴ на UUID). ``visible_text`` отбрасывает только значения случайной
формы целиком; всё, что может прочитать человек, остаётся. Узлы в обе стороны:
ложная тревога уходит, настоящая утечка — ловится.
"""

from __future__ import annotations

import json

from tests.support.pii_asserts import visible_text

UUID_WITH_TAIL = "0f3c5544-9a1b-4c2d-8e7f-a1b2c3d4e5f6"
TOKEN_WITH_TAIL = "eyJhbGciOiJIUzI1NiJ9.x5544Qw_Zk-7Lp"


class TestRandomIdsAreNotText:
    def test_a_tail_inside_a_uuid_is_not_a_leak(self) -> None:
        payload = {"booking_id": UUID_WITH_TAIL, "answer": "Записала на завтра"}
        assert "5544" in json.dumps(payload)  # старая проверка упала бы здесь
        assert "5544" not in visible_text(payload)
        assert "Записала на завтра" in visible_text(payload)  # положительно: текст на месте

    def test_a_tail_inside_an_opaque_token_is_not_a_leak(self) -> None:
        payload = {"pending_action": {"token": TOKEN_WITH_TAIL}}
        assert "5544" not in visible_text(payload)
        assert "pending_action" in visible_text(payload)  # положительно: ключи на месте

    def test_a_long_hex_digest_is_not_text(self) -> None:
        payload = {"correlation_id": "a1b2c3d4e5f60718293a4b5c6d7e5544"}
        assert "5544" not in visible_text(payload)


class TestALeakIsStillCaught:
    def test_a_masked_tail_in_a_reply(self) -> None:
        assert "5544" in visible_text({"answer": "Клиент +7 *** *** 55 44 / +7***5544"})

    def test_a_tail_in_a_json_key(self) -> None:
        assert "5544" in visible_text({"clients": {"5544": "Анна"}})

    def test_a_tail_in_a_long_sentence_with_spaces(self) -> None:
        """Длиннее 24 символов, но с пробелами — это текст, не токен."""
        sentence = "Позвоните клиенту на номер, оканчивающийся на 5544, завтра утром"
        assert len(sentence) > 24
        assert "5544" in visible_text({"answer": sentence})

    def test_a_long_run_of_digits_is_text(self) -> None:
        """Одни цифры — не токен: номер с хвостом не прячется за длиной."""
        assert "5544" in visible_text({"note": "799977755440000000000000001"})

    def test_a_number_value_is_text(self) -> None:
        assert "5544" in visible_text({"tail": 5544})


class TestTheGapIsClosedByTheRawCheck:
    def test_a_full_number_glued_into_a_token_is_caught_on_raw(self) -> None:
        """Склеенный в токен номер ``visible_text`` не видит — по построению:
        случайную строку от утечки внутри неё не отличить. Поэтому полные
        номера (``+7…`` и 10 цифр) вызывающие проверяют по сырому ответу,
        где случайное совпадение 10 цифр пренебрежимо; этот узел держит
        разделение ролей."""
        payload = {"token": "abcDEF9997775544xyzTOKEN123456"}
        assert "5544" not in visible_text(payload)
        assert "9997775544" in json.dumps(payload)
