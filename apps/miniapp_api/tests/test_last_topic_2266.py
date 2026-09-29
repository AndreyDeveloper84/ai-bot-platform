"""DRF-2266 — «Последняя тема» на Главной: кракозябры и не та тема.

Скрин владельца 21.09: «Последняя тема · 6 авг., 02:41», текст — сплошные
U+FFFD, по одному на букву, пробелы на месте.

Замер (без печати текста):

* транспорт порчу не вносит: ``JsonResponse`` сериализует с ``ensure_ascii``
  — каждая кириллическая буква уходит как ``\\uXXXX``, битых байтов на проводе
  быть не может; срез превью — по символам ``str``, не по байтам; клиент
  рендерит строку как есть. Значит U+FFFD лежат в самом сохранённом ходе —
  версия (а) листа. Один U+FFFD на букву при целых пробелах — однобайтовый
  текст (cp1251), прочитанный как UTF-8 при записи;
* дата 6 авг. — потому что тема читается со строки ``BotUser`` Mini App
  (``MAX_BOT_TENANT_SLUG``), а живой разговор человека с Ayla идёт на строке
  чата (сентинел ``global_bot``) — тот же раскол оболочек, что в DRF-2230.
  Главная показывала старый ход салонной эпохи вместо последнего разговора.
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.conversations.models import Message
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_customer_last_topic_2144 import (
    BOT_TOKEN,
    _conversation,
    _get,
    _turn,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="last-topic-2266", name="Last Topic 2266")
    settings.MAX_BOT_TENANT_SLUG = "last-topic-2266"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="22660001")


A = Message.Role.ASSISTANT
#: Символ замены — константой: литерал в исходнике неотличим глазом.
FFFD = chr(0xFFFD)


class TestTopicComesFromThePersonsConversation:
    def test_the_chat_shell_conversation_is_the_last_topic(
        self,
        client: Client,
        tenant,
        bot_user: BotUser,
    ) -> None:
        """Живой разговор — на строке чата (``global_bot``), не на строке Mini App."""
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services.resolver import resolve_or_create_global_bot_user

        old = _conversation(tenant, bot_user)
        _turn(old, A, "Старый ход салонной эпохи про массаж спины", minutes_ago=60 * 24 * 40)

        chat_row = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=bot_user.channel_user_id
        )
        live = resolve_active_global_conversation(chat_row)
        assert live is not None  # разговор на строке чата правда заведён
        _turn(live, A, "Вчера мы говорили про воду и сон", minutes_ago=60)

        body = _get(client, bot_user).json()
        assert body["last_topic"] is not None, body
        assert body["last_topic"]["text"] == "Вчера мы говорили про воду и сон"


class TestReplacementCharactersAreNotAPreview:
    def test_a_turn_full_of_u_fffd_is_not_shown(
        self,
        client: Client,
        tenant,
        bot_user: BotUser,
    ) -> None:
        """Версия (а): испорчено при записи. Экран показывает блок без превью."""
        conv = _conversation(tenant, bot_user)
        _turn(conv, A, f"{FFFD * 4} {FFFD * 5}", minutes_ago=5)
        body = _get(client, bot_user).json()
        assert body["last_topic"] is None, body

    def test_an_earlier_clean_turn_is_used_instead(
        self,
        client: Client,
        tenant,
        bot_user: BotUser,
    ) -> None:
        """Положительная пара: чистый ход чуть раньше — он и тема."""
        conv = _conversation(tenant, bot_user)
        _turn(conv, A, "Подобрала мастера на субботу", minutes_ago=30)
        _turn(conv, A, f"{FFFD * 3} {FFFD * 4}", minutes_ago=5)
        body = _get(client, bot_user).json()
        assert body["last_topic"]["text"] == "Подобрала мастера на субботу"


class TestChatLinkForTheButtons:
    """«Продолжить разговор» ведёт в чат того бота, из которого открыт Mini App.

    web.max.ru (скрины владельца 21.09): моста ``close()`` там может не быть,
    и кнопка молча ничего не делала. Ссылка на диалог бота — из его записи
    реестра (``MAX_BOT_<S>_LINK``), бот — тот, что подписал initData.
    """

    @pytest.fixture
    def two_bots(self, settings):
        from dataclasses import replace

        from apps.miniapp_api.tests.test_auth_multi_bot import REGISTRY

        client, salon = REGISTRY
        settings.MAX_BOT_REGISTRY = (
            replace(client, link="https://max.ru/ayla_client_bot"),
            replace(salon, link="https://max.ru/ayla_salon_bot"),
        )
        return settings

    def _get_signed_by(self, client: Client, token: str, user_id: int):
        from django.urls import reverse

        from apps.miniapp_api.tests.test_auth_multi_bot import make_init_data

        return client.get(
            reverse("miniapp_api:customer_last_topic"),
            HTTP_AUTHORIZATION=f"MaxInitData {make_init_data(token, user_id=user_id)}",
        )

    def test_link_of_the_bot_that_opened_the_app(self, client: Client, tenant, two_bots) -> None:
        from apps.miniapp_api.tests.test_auth_multi_bot import CLIENT_TOKEN, SALON_TOKEN

        assert self._get_signed_by(client, CLIENT_TOKEN, 22660002).json()["chat_link"] == (
            "https://max.ru/ayla_client_bot"
        )
        assert self._get_signed_by(client, SALON_TOKEN, 22660003).json()["chat_link"] == (
            "https://max.ru/ayla_salon_bot"
        )

    def test_no_link_configured_is_null_not_a_guess(self, client: Client, bot_user) -> None:
        body = _get(client, bot_user).json()
        assert "chat_link" in body, body  # ключ есть всегда — контракт экрана
        assert body["chat_link"] is None
