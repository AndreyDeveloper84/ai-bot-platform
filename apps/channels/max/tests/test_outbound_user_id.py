"""Адресация MAX по ``user_id`` для проактивных отправок (DRF-1558).

Что здесь закреплено и почему именно так.

**Дефект.** ``chat_id`` в MAX — идентификатор ДИАЛОГА, осмысленный только
вместе с одним конкретным ботом. ``BotUser`` поля бота не имеет, поэтому
все строки одного человека несут ОДИН ``chat_id`` — диалог с тем ботом,
который завёл его первым. Салонный бот, отправляющий туда, получает
404 ``dialog.not.found``. Замер на боевом контуре 07.09.2026, один
человек и один салонный бот (`docs/OPEN_DECISIONS.md` §55)::

    POST /messages?user_id=260237491   → 200, доставлено
    POST /messages?chat_id=518410834   → 404 dialog.not.found

**Почему тест здесь может краснеть, а прогон на боевом — нет.** Отказ
воспроизводится только при определённом составе окружения (`MAX_BOTS`
с салонным ботом). Поэтому тесты ниже проверяют не «дошло ли», а
**какой ключ ушёл на провод** — единственное, что отличает исправный код
от сломанного независимо от окружения.

**Парная стража к блокировке (DRF-1497, #1399).** ``_recipient_blocked``
искал строку по ``chat_id``. Если ключ отправки уехал на ``user_id``, а
ключ проверки остался на ``chat_id``, проверка перестаёт находить кого бы
то ни было и возвращает ``False`` — блокировка, выложенная 07.09.2026,
отменяется МОЛЧА. Пара «заблокированный подавлен / незаблокированный
доходит» ниже — то, что не даёт такой правке пройти зелёной. Fail-open в
докстринге ``_recipient_blocked`` — про сбой базы, а не про смену ключа,
и здесь не оспаривается.
"""

from __future__ import annotations

import pytest

from apps.channels.max.outbound import MaxAPIError, send_message
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _max_token(settings):
    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"
    return settings


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="drf1558-salon", name="Салон DRF-1558")


# Числа взяты из живого замера §55, а не выдуманы: несовпадение двух
# идентификаторов ОДНОГО человека в личном диалоге — и есть предмет теста.
MEASURED_USER_ID = "260237491"
MEASURED_CHAT_ID = "518410834"


class TestProactiveAddressing:
    """Проактивная отправка адресуется человеком, а не диалогом."""

    def test_user_id_goes_on_the_wire_and_chat_id_does_not(self, httpx_mock):
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        send_message(user_id=MEASURED_USER_ID, text="запись создана")

        url = str(httpx_mock.get_request().url)
        assert f"user_id={MEASURED_USER_ID}" in url
        assert "chat_id" not in url, (
            "оба ключа сразу — MAX возьмёт какой-то один, и мы никогда не узнаем какой"
        )

    def test_reply_path_still_addresses_the_event_dialog(self, httpx_mock):
        """Положительная стража (DRF-1411): ответ на входящее НЕ трогается.

        ``event.chat_id`` пришёл из диалога того самого бота, который
        сейчас отвечает, — он корректен по построению. Замена его на
        ``user_id`` была бы регрессией риска на ровном месте.
        """
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        send_message(chat_id=MEASURED_CHAT_ID, text="ответ")

        url = str(httpx_mock.get_request().url)
        assert f"chat_id={MEASURED_CHAT_ID}" in url
        assert "user_id" not in url

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({}, id="neither"),
            pytest.param(
                {"chat_id": MEASURED_CHAT_ID, "user_id": MEASURED_USER_ID},
                id="both",
            ),
        ],
    )
    def test_exactly_one_address_required(self, kwargs):
        """Молчаливый выбор по умолчанию — это ровно то, как берут не тот."""
        with pytest.raises(ValueError, match="exactly one"):
            send_message(text="x", **kwargs)


class TestBlockingFollowsTheAddressKey:
    """Ключ проверки блокировки едет вместе с ключом отправки."""

    @staticmethod
    def _bot_user(tenant, *, user_id: str, chat_id: str, blocked: bool) -> BotUser:
        from django.utils import timezone

        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=user_id,
            chat_id=chat_id,
            blocked_at=timezone.now() if blocked else None,
        )

    def test_blocked_recipient_is_suppressed_when_addressed_by_user_id(self, httpx_mock, tenant):
        """Заблокированный НЕ получает после смены ключа адресации.

        Это и есть та стража, без которой правка отправителя тихо отменяет
        DRF-1497: проверка по ``chat_id`` при отправке по ``user_id``
        ничего не находит и пропускает сообщение.
        """
        self._bot_user(tenant, user_id="blk-1", chat_id="dialog-blk-1", blocked=True)

        result = send_message(user_id="blk-1", text="напоминание")

        assert result == {"blocked": True}
        assert not httpx_mock.get_requests(), "до MAX запрос уходить не должен"

    def test_unblocked_recipient_still_gets_through_by_user_id(self, httpx_mock, tenant):
        """Парная положительная: подавляется заблокированный, а не все."""
        self._bot_user(tenant, user_id="ok-1", chat_id="dialog-ok-1", blocked=False)
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        result = send_message(user_id="ok-1", text="напоминание")

        assert result == {"ok": True}
        assert "user_id=ok-1" in str(httpx_mock.get_request().url)

    def test_block_on_one_row_does_not_leak_across_channels(self, httpx_mock, tenant):
        """``channel_user_id`` уникален внутри канала, а не между каналами.

        Без сужения по ``channel="max"`` телеграмный идентификатор с тем же
        числом заглушил бы постороннего человека в MAX.
        """
        from django.utils import timezone

        BotUser.all_tenants.create(
            tenant=tenant,
            channel="telegram",
            channel_user_id="777",
            chat_id="tg-777",
            blocked_at=timezone.now(),
        )
        self._bot_user(tenant, user_id="777", chat_id="max-777", blocked=False)
        httpx_mock.add_response(json={"ok": True}, status_code=200)

        assert send_message(user_id="777", text="напоминание") == {"ok": True}

    def test_reply_path_blocking_key_unchanged(self, httpx_mock, tenant):
        """Ветка ``chat_id`` работает как раньше — предмет правки не она."""
        self._bot_user(tenant, user_id="blk-2", chat_id="dialog-blk-2", blocked=True)

        assert send_message(chat_id="dialog-blk-2", text="ответ") == {"blocked": True}
        assert not httpx_mock.get_requests()


class TestErrorsCarryTheAddressThatWasUsed:
    def test_http_error_still_raises_with_user_id_addressing(self, httpx_mock):
        httpx_mock.add_response(
            json={"code": "dialog.not.found"},
            status_code=404,
        )
        with pytest.raises(MaxAPIError) as exc:
            send_message(user_id=MEASURED_USER_ID, text="x")
        assert exc.value.status_code == 404
