"""Тексты «забудь всё» — по слову владельца (CD §76, №30; DRF-2214).

Владелец: «разделить управление избранным в BeautyGO и настройками в профиле;
использовать уточнённый текст». После #526 / #530 / #541 / #1980 «забудь всё»
стирает не только память из разговоров, но и цели, план, профиль питания,
дневник, карточки рекомендаций — формулировка «всё, что запомнила о тебе из
наших разговоров, и анкету предпочтений» стала обещать меньше, чем делается.
Избранные мастера (``FavoriteSpecialist``, «сердечки») «забудь всё» оставляет,
и живут они в мобильном приложении BeautyGO, а не в профиле Mini App.

Узлы пинят тексты дословно: слово пользователю меняется только словом
владельца. Маркер подтверждения — отдельный узел: по нему распознаётся
«удалить» (``memory_commands._FORGET_ALL_MARKER``), и его потеря молча
сломала бы подтверждение.
"""

from __future__ import annotations

from apps.persona.memory_commands import _FORGET_ALL_DONE, _FORGET_ALL_MARKER, FORGET_ALL_PROMPT

FIRST_LINE = (
    "Это серьёзный шаг: я забуду всё, что знаю о тебе, кроме бронирований и оплат, "
    "— вернуть будет нельзя."
)
DIALOGUE_LINE = (
    "Саму переписку я обезличу: текст останется без твоих контактов — только на "
    "случай спора о записи, и удалится через 90 дней."
)
WHAT_STAYS_LINE = (
    "Останутся бронирования и оплаты (это по закону), избранные мастера — они в "
    "приложении BeautyGO, а также настройки уведомлений с датой рождения — их ты "
    "меняешь сам на экране профиля."
)
DONE = (
    "Готово — я забыла всё, что о тебе помнила. Избранные мастера остались в "
    "приложении BeautyGO, настройки уведомлений и дата рождения — на экране "
    "профиля: их меняешь ты, не я 🙂"
)


class TestThePromptIsTheOwnersText:
    def test_the_prompt_reads_exactly(self) -> None:
        assert FORGET_ALL_PROMPT == "\n".join(
            [
                FIRST_LINE,
                DIALOGUE_LINE,
                WHAT_STAYS_LINE,
                f"Чтобы подтвердить — {_FORGET_ALL_MARKER}",
            ]
        )

    def test_the_old_narrow_promise_is_gone(self) -> None:
        """«Из наших разговоров» обещало меньше, чем делает стирание (#526 и дальше)."""
        assert "бронирований и оплат" in FORGET_ALL_PROMPT  # наличие: новый текст
        assert "из наших разговоров" not in FORGET_ALL_PROMPT
        assert "анкету предпочтений" not in FORGET_ALL_PROMPT

    def test_the_confirmation_marker_survives(self) -> None:
        assert _FORGET_ALL_MARKER in FORGET_ALL_PROMPT


class TestTheDoneLineIsTheOwnersText:
    def test_the_done_line_reads_exactly(self) -> None:
        assert _FORGET_ALL_DONE == DONE

    def test_the_done_line_keeps_its_opening(self) -> None:
        """Узлы стирания (``test_forget_all_erasure_started``) опираются на это начало."""
        assert _FORGET_ALL_DONE.startswith("Готово — я забыла всё, что о тебе помнила.")
