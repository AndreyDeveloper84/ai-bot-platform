"""DRF-2594 — подписанный токен действия не «видимый текст», но его нагрузка — да.

Сторож PII в ``test_assistant_context_2153`` краснел без утечки: хвост номера
«5544» нашёлся внутри случайного UUID в нагрузке токена ``prepare_booking``.
Токен — ``TimestampSigner.sign(json.dumps(...))``: сырой JSON, метка, подпись.
Из-за ``"``/``{``/``,`` строка не подходила под форму токена, и
``visible_text`` отдавал её целиком.

Узлы — на паре, которая обязана различаться: те же четыре цифры в видимом
ответе краснеют, внутри случайного id подписанной нагрузки — нет; а читаемое
поле нагрузки остаётся под проверкой. Подпись — настоящим ``TimestampSigner``,
тем же способом, что ``assistant_actions._sign``.
"""

from __future__ import annotations

import json

from django.core.signing import TimestampSigner

from tests.support.pii_asserts import visible_text

UUID_WITH_TAIL = "0f3c5544-9a1b-4c2d-8e7f-a1b2c3d4e5f6"


def _sign(payload: dict) -> str:
    signer = TimestampSigner(key="drf-2594-test-key-" + "x" * 40, salt="drf-2594")
    return signer.sign(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


class TestSignedTokenIsNotVisibleText:
    def test_a_tail_inside_a_random_id_of_the_signed_payload_is_not_a_leak(self) -> None:
        token = _sign({"booking_id": UUID_WITH_TAIL, "client_name": "Анна"})
        body = {"answer": "Записать Анну на завтра?", "pending_action": {"token": token}}

        assert "5544" in json.dumps(body)  # старая проверка упала бы здесь
        assert "5544" not in visible_text(body)
        # Положительно: нагрузка разобрана, а не выброшена.
        assert "Анна" in visible_text(body)
        assert "booking_id" in visible_text(body)

    def test_the_shape_that_went_red_in_ci(self) -> None:
        """Форма из прогона 36434195786: UUID с «5544» внутри и хвост подписи."""
        token = _sign(
            {
                "action": "prepare_booking",
                "service_id": "3c1e0b2a-7d4f-4af0-9f15-78a5544b0c11",
                "tenant_id": "de750102-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
            }
        )
        assert '","tenant_id":"' in token and token.count(":") >= 2
        assert "5544" not in visible_text({"pending_action": {"token": token}})

    def test_timestamp_and_signature_are_dropped(self) -> None:
        token = _sign({"note": "ok"})
        _payload, stamp, signature = token.rsplit(":", 2)
        text = visible_text({"token": token})

        assert "ok" in text.split("\n")  # положительно: нагрузка видна
        assert stamp not in text.split("\n")
        assert signature not in text


class TestALeakIsStillCaught:
    def test_the_same_tail_in_the_visible_answer_next_to_a_token(self) -> None:
        """Пара к первому узлу: те же четыре цифры, но в видимом ответе."""
        token = _sign({"booking_id": UUID_WITH_TAIL})
        body = {"answer": "Клиент +7***5544", "pending_action": {"token": token}}
        assert "5544" in visible_text(body)

    def test_a_tail_in_a_readable_field_of_the_signed_payload(self) -> None:
        """Фильтр по форме, не по полю: выбросить токен целиком ослепило бы
        сторожа к телефону в открытом поле нагрузки."""
        token = _sign({"booking_id": "b-1", "note": "перезвонить на 5544 вечером"})
        assert "5544" in visible_text({"pending_action": {"token": token}})

    def test_a_sentence_with_colons_is_not_a_signed_value(self) -> None:
        sentence = "Запись: завтра в 10:30: клиент 5544"
        assert "5544" in visible_text({"answer": sentence})


class TestTheGapStaysNamed:
    def test_a_full_number_glued_into_a_random_id_of_the_payload_is_caught_on_raw(
        self,
    ) -> None:
        """Предел не снят: номер, склеенный внутрь случайной строки нагрузки,
        ``visible_text`` не видит. Его ловит проверка полного номера по сыром
        ответу — разделение ролей остаётся."""
        token = _sign({"ref": "abcDEF9997775544xyzTOKEN123456"})
        body = {"pending_action": {"token": token}}
        assert "ref" in visible_text(body)  # положительно: нагрузка разобрана
        assert "5544" not in visible_text(body)
        assert "9997775544" in json.dumps(body, ensure_ascii=False)
