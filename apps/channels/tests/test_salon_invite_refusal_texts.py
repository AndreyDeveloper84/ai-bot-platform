"""Два отказа приглашения получают СВОИ слова, а не общую кучу.

DRF-1647 и DRF-1650. До этой правки оба попадали в `except InviteError` и
отвечали `CODE_NOT_ACCEPTED` — тем же словом, что «неверный код»,
«истёк» и «чужой салон». Молчание к тому моменту уже вылечили, но куча
осталась, а лекарства у этих двоих разные: одному нужен СВОЙ код, другому
никакой код не поможет.
"""

from __future__ import annotations

import pytest

from apps.channels.max import salon_handler
from apps.identity.services.staff_invites import (
    MasterAlreadyLinked,
    PersonAlreadyMaster,
)


class _Event:
    text = "AYLA-7K3M"
    chat_id = "1"
    channel_user_id = "42"
    channel = "max"


@pytest.fixture
def said(monkeypatch):
    out = []
    monkeypatch.setattr(salon_handler, "_reply", lambda ev, text, *a, **k: out.append(text))
    return out


def _redeem_raising(exc):
    def _raise(**kwargs):
        raise exc

    return _raise


class TestEachRefusalGetsItsOwnWords:
    def test_a_card_that_belongs_to_someone_else(self, monkeypatch, said):
        monkeypatch.setattr(
            salon_handler,
            "redeem_staff_invite",
            _redeem_raising(MasterAlreadyLinked("занято")),
        )

        salon_handler._redeem_and_greet(_Event(), object(), "AYLA-7K3M", object(), object())

        assert said == [salon_handler.WRONG_RECIPIENT]
        assert said[0] != salon_handler.CODE_NOT_ACCEPTED

    def test_a_person_who_already_has_a_card(self, monkeypatch, said):
        monkeypatch.setattr(
            salon_handler,
            "redeem_staff_invite",
            _redeem_raising(PersonAlreadyMaster("занято")),
        )

        salon_handler._redeem_and_greet(_Event(), object(), "AYLA-7K3M", object(), object())

        assert said == [salon_handler.PERSON_ALREADY_MASTER]
        assert said[0] != salon_handler.CODE_NOT_ACCEPTED

    def test_the_two_are_not_the_same_sentence(self):
        """Разные лекарства — разные слова.

        Один идёт к администратору за своим кодом, другой — снимать
        прежнюю связь. Совпади тексты, различие исчезло бы для того
        единственного, кому оно адресовано.
        """
        assert salon_handler.WRONG_RECIPIENT != salon_handler.PERSON_ALREADY_MASTER

    def test_neither_promises_the_admin_will_be_told(self):
        """При обоих отказах администратору не приходит ничего.

        Обещанное и не случившееся хуже неназванного: человек будет
        ждать, вместо того чтобы написать сам.
        """
        for text in (salon_handler.WRONG_RECIPIENT, salon_handler.PERSON_ALREADY_MASTER):
            # Присутствие — впереди отсутствия. Оба текста отправляют
            # человека к администратору САМОГО; без этой строки «не
            # обещает» зеленело бы и на пустой строке, и на тексте,
            # который вообще ничего не советует.
            assert "администратор" in text.lower()
            assert "уведом" not in text.lower()
            assert "сообщим" not in text.lower()
            assert "придёт" not in text.lower()


class TestTheGeneralBranchStillCatchesTheRest:
    def test_an_unknown_invite_error_still_gets_the_hedge(self, monkeypatch, said):
        """Положительная стража: общая ветка не сломана.

        Без неё оба теста выше зеленели бы и на коде, который перестал
        отвечать вообще всем остальным отказам.
        """
        from apps.identity.services.staff_invites import InviteError

        monkeypatch.setattr(
            salon_handler,
            "redeem_staff_invite",
            _redeem_raising(InviteError("прочее")),
        )

        salon_handler._redeem_and_greet(_Event(), object(), "AYLA-7K3M", object(), object())

        assert said == [salon_handler.CODE_NOT_ACCEPTED]
