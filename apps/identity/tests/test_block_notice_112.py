"""CD §76 №35 — фраза блокировки прямо называет 112 при угрозе жизни.

Заблокированному кризис и неотложка отвечаются (N-1, DRF-2276), но фраза
блокировки — первое и часто единственное, что он читает. Владелец: при угрозе
жизни указание звонить 112 должно стоять в самой фразе, а не только в ответе,
который придёт, если человек напишет ещё раз. Голос бота — на «ты».
"""

from __future__ import annotations

from apps.identity.services.blocking import BLOCK_NOTICE_TEXT

APPROVED_TEXT = (
    "Сейчас я не могу продолжить этот разговор. "
    "Если есть угроза жизни — звони 112. "
    "Если что-то срочное со здоровьем — напиши, я подскажу, куда обратиться."
)


def test_the_notice_is_the_approved_text() -> None:
    assert BLOCK_NOTICE_TEXT == APPROVED_TEXT


def test_112_comes_before_the_offer_to_write() -> None:
    """Угроза жизни — раньше «напиши»: звонок не ждёт переписки."""
    assert "112" in BLOCK_NOTICE_TEXT
    assert BLOCK_NOTICE_TEXT.index("112") < BLOCK_NOTICE_TEXT.index("напиши")
