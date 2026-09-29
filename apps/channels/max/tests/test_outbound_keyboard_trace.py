"""DRF-1781 — след отправки клавиатуры в MAX.

Диалог владельца 12.09 01:33 UTC: у реплики ``anketa_step_screening`` в
``Message.action_data`` лежали четыре кнопки (замер DRF-1780), на экране
вариантов не было. Что ушло на провод — не знал никто: ``send_message``
писал в лог только отказы, лог контейнера не переживает выкладку, а база
хранит ``action_data`` ДО сборки вложений.

Здесь два предмета:

1. **Провод.** Реплика анкеты на шаге здоровья проходит весь путь —
   ``_render_step`` скилла → ``_build_attachments`` MAX-обработчика →
   ``send_message`` — и в ТЕЛЕ ЗАПРОСА к MAX (граница httpx, не мок
   ``send_message``) лежит ``inline_keyboard`` с четырьмя кнопками.
2. **След.** Успешная отправка пишет ``channels.max.outbound.sent`` с
   ``mid`` и подписями кнопок; отказ MAX — ``http_error`` с той же
   клавиатурой, чтобы «отверг кнопки» отличалось от «отверг текст».
"""

from __future__ import annotations

import json
import logging

import pytest

from apps.channels.max.outbound import (
    MaxAPIError,
    keyboard_trace,
    make_inline_keyboard_attachment,
    make_inline_keyboard_attachment_rows,
    send_message,
)

_SCREENING_LABELS = [
    "Ничего из этого",
    "Беременность или кормление",
    "Расстройство пищевого поведения",
    "Заболевание, влияющее на питание",
]


@pytest.fixture(autouse=True)
def _max_token(settings):
    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"
    return settings


def _screening_attachments() -> list[dict]:
    """Вложения ровно так, как их собирает живой путь MAX для шага здоровья."""
    from apps.channels.max.handler import _build_attachments
    from apps.skills.nutrition_anketa.fsm import AnketaFSM
    from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill

    result = NutritionAnketaSkill()._render_step("screening", AnketaFSM.STEPS["screening"].prompt)
    assert result.action_type == "anketa_step_screening"
    attachments = _build_attachments(result.action_data)
    assert attachments is not None
    return attachments


class TestScreeningKeyboardReachesTheMaxWire:
    def test_request_body_carries_four_buttons(self, httpx_mock, caplog) -> None:
        httpx_mock.add_response(
            json={"message": {"body": {"mid": "mid-screening-1"}}}, status_code=200
        )
        attachments = _screening_attachments()

        with caplog.at_level(logging.INFO, logger="apps.channels.max.outbound"):
            send_message(chat_id="777", text="Перед ростом и весом…", attachments=attachments)

        req = httpx_mock.get_request()
        assert req is not None
        body = json.loads(req.content)
        assert body["attachments"][0]["type"] == "inline_keyboard"
        rows = body["attachments"][0]["payload"]["buttons"]
        flat = [b for row in rows for b in row]
        assert [b["text"] for b in flat] == _SCREENING_LABELS
        assert flat[0]["payload"] == "cb:anketa:choice:screening:none"

        # POSITIVE: след записан — mid из конверта MAX и все четыре подписи.
        sent = [
            r for r in caplog.records if r.getMessage().startswith("channels.max.outbound.sent")
        ]
        assert len(sent) == 1, [r.getMessage() for r in caplog.records]
        line = sent[0].getMessage()
        assert "mid=mid-screening-1" in line
        assert "клавиатура=4[" in line
        for label in _SCREENING_LABELS:
            assert f"«{label}»" in line
        # Текст сообщения в след НЕ попадает (DRF-1039).
        assert "Перед ростом" not in line

    def test_rejected_keyboard_is_named_as_a_keyboard(self, httpx_mock, caplog) -> None:
        httpx_mock.add_response(status_code=400, json={"code": "attachment.not.ready"})
        attachments = _screening_attachments()

        with caplog.at_level(logging.WARNING, logger="apps.channels.max.outbound"):
            with pytest.raises(MaxAPIError):
                send_message(chat_id="777", text="x", attachments=attachments)

        errors = [r.getMessage() for r in caplog.records if "outbound.http_error" in r.getMessage()]
        assert len(errors) == 1, errors
        assert "status=400" in errors[0]
        assert "клавиатура=4[«Ничего из этого»" in errors[0]


class TestKeyboardTrace:
    def test_no_attachments(self) -> None:
        assert keyboard_trace(None) == "клавиатура=нет"
        assert keyboard_trace([]) == "клавиатура=нет"

    def test_flat_form_counts_and_names(self) -> None:
        att = make_inline_keyboard_attachment(
            [{"label": "Да", "callback": "cb:x:yes"}, {"label": "Нет", "callback": "cb:x:no"}]
        )
        assert keyboard_trace([att]) == "клавиатура=2[«Да»|«Нет»]"

    def test_rows_form_counts_across_rows(self) -> None:
        att = make_inline_keyboard_attachment_rows(
            [
                [{"label": "A", "callback": "cb:a"}, {"label": "B", "callback": "cb:b"}],
                [{"label": "C", "callback": "cb:c"}],
            ]
        )
        assert keyboard_trace([att]) == "клавиатура=3[«A»|«B»|«C»]"

    def test_media_only_attachments_are_counted_not_named(self) -> None:
        assert (
            keyboard_trace([{"type": "image", "payload": {"url": "u"}}])
            == "клавиатура=нет вложений_без_клавиатуры=1"
        )

    def test_sent_line_without_keyboard_says_so(self, httpx_mock, caplog) -> None:
        httpx_mock.add_response(json={"message": {"body": {"mid": "m-2"}}}, status_code=200)
        with caplog.at_level(logging.INFO, logger="apps.channels.max.outbound"):
            send_message(user_id="42", text="привет")
        sent = [r.getMessage() for r in caplog.records if "outbound.sent" in r.getMessage()]
        assert sent == ["channels.max.outbound.sent user_id=42 status=200 mid=m-2 клавиатура=нет"]
