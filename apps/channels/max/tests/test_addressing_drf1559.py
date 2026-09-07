"""Адреса из конфига: человек вытесняет диалог (DRF-1559).

**Что чинится.** DRF-1558 перевёл на ``user_id`` всё, чей адрес приходит из
нашей базы. Остались восемь отправок, адресуемых из конфига —
``Tenant.manager_chat_id`` и ``HANDOFF_NOTIFY_MAX_CHAT_IDS``. Это диалоговые
идентификаторы, а диалог осмыслен только вместе с тем ботом, который его
завёл: салонный бот, отправляя туда, получает 404 ``dialog.not.found``. В
замере §55 упал не только ``channel=master``, но и ``channel=fallback`` —
последний адресуется ровно из этой настройки, то есть отказ здесь уже
наблюдался на бою, а не предполагается.

**Что здесь проверяется и почему именно это.** Не «дошло ли» — доставка
зависит от состава окружения и прогоном не ловится (иллюстрация §47.7).
Проверяется **какой ключ ушёл на провод**: единственное, что отличает
исправный код от сломанного независимо от окружения.

**Почему тесты умеют падать.** Значения ``…_USER_ID`` и ``…_CHAT_ID`` в каждом
случае РАЗНЫЕ. Возврат к диалоговому идентификатору даёт другое значение, а не
то же самое — и пройти зелёным не может. Совпадение этих двух величин и было
тем ложным равенством, из-за которого хранение одного адреса на человека
выглядело безопасным.
"""

from __future__ import annotations

import pytest

from apps.channels.max.addressing import (
    MaxAddress,
    manager_address,
    operator_addresses,
    split_addresses,
)
from apps.handoff.notify import get_notify_addresses, send_max_notification
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

# Числа из живого замера §55/§56: один человек, один салонный бот.
MEASURED_USER_ID = "260237491"
MEASURED_CHAT_ID = "518410834"


@pytest.fixture(autouse=True)
def _max_token(settings):
    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []
    settings.HANDOFF_NOTIFY_MAX_USER_IDS = []
    return settings


class TestMaxAddress:
    """Значение выбирается один раз и само знает, каким ключом уходит."""

    def test_person_wins_over_dialog(self):
        address = MaxAddress.resolve(user_id=MEASURED_USER_ID, chat_id=MEASURED_CHAT_ID)
        assert address.key == "user_id"
        assert address.value == MEASURED_USER_ID
        assert address.send_kwargs() == {"user_id": MEASURED_USER_ID}

    def test_dialog_when_no_person(self):
        address = MaxAddress.resolve(chat_id=MEASURED_CHAT_ID)
        assert address.key == "chat_id"
        assert address.send_kwargs() == {"chat_id": MEASURED_CHAT_ID}

    def test_empty_address_is_falsey(self):
        """Вызывающий пишет ``if not address:``, а не сравнивает строки."""
        assert not MaxAddress.resolve()
        assert not MaxAddress.resolve(user_id="   ", chat_id="")

    def test_whitespace_is_not_an_address(self):
        """Пробелы в поле админки — это «не настроено», а не адрес « »."""
        assert MaxAddress.resolve(user_id="  ", chat_id=" 42 ").send_kwargs() == {"chat_id": "42"}

    def test_send_kwargs_never_carries_both(self):
        """``send_message`` отказывается с обоими сразу — развести обязаны здесь."""
        both = MaxAddress(user_id=MEASURED_USER_ID, chat_id=MEASURED_CHAT_ID)
        assert both.send_kwargs() == {"user_id": MEASURED_USER_ID}

    def test_split_sorts_by_key(self):
        chat_ids, user_ids = split_addresses(
            [MaxAddress(user_id="u1"), MaxAddress(chat_id="c1"), MaxAddress()]
        )
        assert (chat_ids, user_ids) == (["c1"], ["u1"])


@pytest.mark.django_db
class TestManagerAddress:
    """Адрес салона: заполненный ``manager_user_id`` вытесняет ``manager_chat_id``."""

    @staticmethod
    def _tenant(**kwargs) -> Tenant:
        return Tenant.objects.create(slug="drf1559", name="Салон DRF-1559", **kwargs)

    def test_user_id_wins_when_both_are_set(self):
        tenant = self._tenant(
            manager_user_id=MEASURED_USER_ID,
            manager_chat_id=MEASURED_CHAT_ID,
        )
        assert manager_address(tenant).send_kwargs() == {"user_id": MEASURED_USER_ID}

    def test_falls_back_to_chat_id_so_configured_salons_do_not_go_silent(self):
        """Откат — не недоделка: салон, настроенный вчера, не должен замолчать.

        Это ровно прежнее поведение с прежним ограничением: диалоговый
        идентификатор верен только для бота, из чьей переписки он взят.
        """
        tenant = self._tenant(manager_chat_id=MEASURED_CHAT_ID)
        assert manager_address(tenant).send_kwargs() == {"chat_id": MEASURED_CHAT_ID}

    def test_neither_configured_is_no_address(self):
        assert not manager_address(self._tenant())

    def test_object_without_the_fields_reads_as_not_configured(self):
        """Лёгкий двойник тенанта не должен ронять отправителя."""
        assert not manager_address(object())


class TestOperatorAddresses:
    """Настройка операторов: список людей вытесняет список диалогов целиком."""

    def test_user_ids_displace_chat_ids_rather_than_joining_them(self, settings):
        """Объединение слало бы одному человеку по два сообщения на событие.

        На время переноса обе настройки называют одного и того же оператора,
        и «безопасное» объединение обернулось бы дублями на каждое событие.
        """
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = [MEASURED_CHAT_ID]
        settings.HANDOFF_NOTIFY_MAX_USER_IDS = [MEASURED_USER_ID]

        assert [a.send_kwargs() for a in operator_addresses()] == [{"user_id": MEASURED_USER_ID}]

    def test_chat_ids_still_read_while_the_new_setting_is_empty(self, settings):
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = [MEASURED_CHAT_ID]

        assert [a.send_kwargs() for a in operator_addresses()] == [{"chat_id": MEASURED_CHAT_ID}]

    def test_nothing_configured_means_the_mechanism_is_off(self, settings):
        assert operator_addresses() == ()
        assert get_notify_addresses() == ()

    def test_blank_entries_are_not_recipients(self, settings):
        settings.HANDOFF_NOTIFY_MAX_USER_IDS = ["", "  ", MEASURED_USER_ID]

        assert [a.value for a in operator_addresses()] == [MEASURED_USER_ID]


@pytest.mark.django_db
class TestWhatGoesOnTheWire:
    """Сквозь фан-аут: ключ, ушедший в MAX, — тот, что выбран в конфиге."""

    def test_configured_person_is_addressed_as_a_person(self, settings, httpx_mock):
        settings.HANDOFF_NOTIFY_MAX_USER_IDS = [MEASURED_USER_ID]
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        assert send_max_notification(text="эскалация", addresses=get_notify_addresses()) == 0

        url = str(httpx_mock.get_request().url)
        assert f"user_id={MEASURED_USER_ID}" in url
        assert "chat_id" not in url

    def test_configured_dialog_still_goes_by_chat_id(self, settings, httpx_mock):
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = [MEASURED_CHAT_ID]
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        assert send_max_notification(text="эскалация", addresses=get_notify_addresses()) == 0

        url = str(httpx_mock.get_request().url)
        assert f"chat_id={MEASURED_CHAT_ID}" in url
        assert "user_id" not in url

    def test_mixed_recipients_each_keep_their_own_key(self, httpx_mock):
        """Один фан-аут может нести и людей, и диалоги — не сваливая в один ключ."""
        httpx_mock.add_response(json={"ok": True}, status_code=200)
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        failures = send_max_notification(
            text="сводка",
            addresses=[MaxAddress(user_id="u-7"), MaxAddress(chat_id="c-7")],
        )

        assert failures == 0
        urls = sorted(str(r.url) for r in httpx_mock.get_requests())
        assert any("user_id=u-7" in u for u in urls)
        assert any("chat_id=c-7" in u for u in urls)


@pytest.mark.django_db
class TestBlockingFollowsTheConfiguredKey:
    """Парная стража к смене ключа — та же, что защитила DRF-1497 в DRF-1558.

    Проверка блокировки ключуется тем же, чем адресация. Если бы адрес
    менеджера уехал на ``user_id``, а проверка осталась на ``chat_id``,
    блокировка перестала бы находить кого бы то ни было и отменилась бы
    молча. Здесь это закреплено на КОНФИГУРИРУЕМОМ адресе — том, который
    DRF-1558 не покрывал.
    """

    @staticmethod
    def _blocked(user_id: str, chat_id: str) -> BotUser:
        from django.utils import timezone

        tenant = Tenant.objects.create(slug="drf1559-blocked", name="Салон")
        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=user_id,
            chat_id=chat_id,
            blocked_at=timezone.now(),
        )

    def test_blocked_operator_addressed_by_user_id_is_suppressed(self, settings, httpx_mock):
        self._blocked(user_id=MEASURED_USER_ID, chat_id=MEASURED_CHAT_ID)
        settings.HANDOFF_NOTIFY_MAX_USER_IDS = [MEASURED_USER_ID]

        assert send_max_notification(text="x", addresses=get_notify_addresses()) == 0
        assert not httpx_mock.get_requests(), "до MAX запрос уходить не должен"

    def test_unblocked_operator_still_gets_through(self, settings, httpx_mock):
        """Парная положительная: подавляется заблокированный, а не все."""
        tenant = Tenant.objects.create(slug="drf1559-ok", name="Салон")
        BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="ok-9",
            chat_id="dialog-ok-9",
        )
        settings.HANDOFF_NOTIFY_MAX_USER_IDS = ["ok-9"]
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        assert send_max_notification(text="x", addresses=get_notify_addresses()) == 0
        assert "user_id=ok-9" in str(httpx_mock.get_request().url)
