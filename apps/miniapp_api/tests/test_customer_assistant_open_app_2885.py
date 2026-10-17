"""DRF-2885 (часть Б) — кнопка «открыть Mini App» доезжает до чата Mini App.

Решение владельца 07.10 (лист решений, п.5): план создаётся и обсуждается в
чате — и в боте, и в чате Mini App. Ход у них один, а кнопки расходились:
``open_app`` («Мой план» под карточкой плана) в MAX открывает Mini App на
нужном экране, а ответ чата Mini App такие кнопки отбрасывал.

Сервер отдаёт слаг как есть; в какой экран он ведёт, решает карта маршрутов
клиента — та же, что читает стартовый параметр MAX.
"""

from __future__ import annotations

from typing import Any

from apps.miniapp_api.views_customer_assistant import _answer_from, _buttons


def _keyboard(*buttons: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "inline_keyboard", "payload": {"buttons": [list(buttons)]}}]


OPEN_PLAN = {"type": "open_app", "text": "Мой план", "web_app": "ayla_bot", "payload": "open_plan"}
CALLBACK = {"type": "callback", "text": "📅 Записаться", "payload": "cb:welcome:book"}
LINK = {"type": "link", "text": "Сайт салона", "url": "https://example.org"}


def test_b1_open_app_button_reaches_the_app_with_its_slug() -> None:
    assert _buttons(_keyboard(OPEN_PLAN)) == [{"label": "Мой план", "open_app": "open_plan"}]


def test_b2_order_and_the_other_kinds_are_unchanged() -> None:
    assert _buttons(_keyboard(CALLBACK, OPEN_PLAN, LINK)) == [
        {"label": "📅 Записаться", "payload": "cb:welcome:book"},
        {"label": "Мой план", "open_app": "open_plan"},
        {"label": "Сайт салона", "url": "https://example.org"},
    ]


def test_b3_open_app_without_a_slug_is_dropped() -> None:
    """Такая кнопка открывает Mini App «вообще» — человеку внутри него нажимать нечего."""
    bare = {"type": "open_app", "text": "Открыть приложение", "web_app": "ayla_bot"}
    assert _buttons(_keyboard(bare, OPEN_PLAN)) == [{"label": "Мой план", "open_app": "open_plan"}]


def test_b4_a_contact_request_is_still_dropped() -> None:
    contact = {"type": "request_contact", "text": "Поделиться номером"}
    assert _buttons(_keyboard(contact, CALLBACK)) == [
        {"label": "📅 Записаться", "payload": "cb:welcome:book"}
    ]


def test_b5_the_turn_answer_carries_the_button_of_the_last_reply() -> None:
    answer, buttons = _answer_from(
        [
            {"text": "Сейчас посмотрю."},
            {"text": "Вот твой план.", "attachments": _keyboard(OPEN_PLAN)},
        ]
    )
    assert answer == "Сейчас посмотрю.\n\nВот твой план."
    assert buttons == [{"label": "Мой план", "open_app": "open_plan"}]
