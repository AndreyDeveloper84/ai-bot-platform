"""Автоматическая попытка связывания: падает честно и по имени (§148, S2).

Владелец: «пробуем автоматически, падаем в `SETUP_PENDING`, оператор
добивает». Падение — правильный исход, и проверяется здесь именно как
исход, а не как сбой.

На пилоте попытка будет отказывать **всегда**: `resolve_external_user`
заводит прокси лениво, и для несвязанной MAX-личности вернётся
`is_proxy=true`. Записать такой ключ запрещено — он занял бы колонку
значением, по которому совпадения не будет никогда.
"""

from __future__ import annotations

import uuid

import pytest

from apps.identity.services.solo_ayla_link import SoloLinkRefused
from apps.identity.services.solo_link_attempt import (
    AYLA_UNREACHABLE,
    attempt_solo_link,
)
from apps.identity.services.solo_onboarding import (
    SoloSetupState,
    create_solo_provider,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def result(db):
    return create_solo_provider(
        channel="max", channel_user_id="solo-attempt-1", display_name="Ольга"
    )


class _Identity:
    def __init__(self, ayla_user_id, is_proxy):
        self.ayla_user_id = ayla_user_id
        self.is_proxy = is_proxy


def _patch_resolver(monkeypatch, *, returns=None, raises=None):
    from apps.integrations.ayla import identity_client

    def _fake(external_user_id):
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(identity_client, "resolve_identity", _fake)


class TestTheProxyAnswerIsRefusedByName:
    def test_a_proxy_answer_leaves_the_provider_pending(self, monkeypatch, result):
        """Сегодняшний путь пилота, целиком.

        Ключ не записан, состояние не сдвинулось, причина названа
        машинным именем — всё, что нужно оператору, чтобы понять, что
        делать дальше.
        """
        _patch_resolver(monkeypatch, returns=_Identity(uuid.uuid4(), is_proxy=True))

        reason = attempt_solo_link(result.master, result.bot_user)

        assert reason == SoloLinkRefused.PROXY
        result.master.refresh_from_db()
        assert result.master.ayla_user_id is None
        assert result.setup_state is SoloSetupState.SETUP_PENDING

    def test_the_refusal_is_not_the_same_name_as_unreachable(self, monkeypatch, result):
        """«Не ответила» и «ответила прокси» требуют разного.

        Первое повторить осмысленно, второе — нет. Одно слово на двоих
        отняло бы у оператора это различие, и повторы сыпались бы туда,
        где повтор не поможет никогда.
        """
        from apps.integrations.ayla.identity_client import IdentityResolveError

        _patch_resolver(monkeypatch, raises=IdentityResolveError("нет связи"))

        assert attempt_solo_link(result.master, result.bot_user) == AYLA_UNREACHABLE
        assert AYLA_UNREACHABLE != SoloLinkRefused.PROXY


class TestARealAnswerLinksAndFrees:
    """Положительная стража: попытка не отказывает ВСЕГДА.

    Без неё все тесты выше зеленели бы и на реализации, которая не
    связывает никогда, — то есть на автоматике, неисполнимой в принципе,
    вместо автоматики, которой сегодня нечем сработать.
    """

    def test_a_real_identity_links_and_the_state_moves_to_ready(self, monkeypatch, result):
        key = uuid.uuid4()
        _patch_resolver(monkeypatch, returns=_Identity(key, is_proxy=False))

        reason = attempt_solo_link(result.master, result.bot_user)

        assert reason is None
        result.master.refresh_from_db()
        assert result.master.ayla_user_id == key
        assert result.setup_state is SoloSetupState.READY


class TestRegistrationSurvivesTheAttempt:
    def test_an_unreachable_ayla_does_not_raise(self, monkeypatch, result):
        """Человек получает кабинет, даже если внешняя система лежит.

        Регистрация не должна падать оттого, что Ayla недоступна:
        кабинет заведён, состояние честное, причина записана.
        """
        from apps.integrations.ayla.identity_client import IdentityResolveError

        _patch_resolver(monkeypatch, raises=IdentityResolveError("таймаут"))

        reason = attempt_solo_link(result.master, result.bot_user)

        assert reason == AYLA_UNREACHABLE
        assert result.setup_state is SoloSetupState.SETUP_PENDING

    def test_a_second_attempt_after_a_real_answer_is_harmless(self, monkeypatch, result):
        """Повтор после успеха — не инцидент.

        Оператор может нажать дважды, ретрай может прийти вторым; отказ
        на идентичном повторе превратил бы обычный случай в тревогу.
        """
        key = uuid.uuid4()
        _patch_resolver(monkeypatch, returns=_Identity(key, is_proxy=False))

        assert attempt_solo_link(result.master, result.bot_user) is None
        assert attempt_solo_link(result.master, result.bot_user) is None

        result.master.refresh_from_db()
        assert result.master.ayla_user_id == key
