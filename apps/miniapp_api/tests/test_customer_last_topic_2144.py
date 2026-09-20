"""«Продолжить разговор с Ayla» — последняя тема для главного экрана (DRF-2144).

    GET customer/last-topic/ → {"last_topic": {"text": "…", "at": "<iso>"} | null}

Источник — последний ход АССИСТЕНТА в разговоре этого же человека (клиентский
бот), детерминированно: первые 80 знаков ответа Ayla, обрезка по слову с «…».
Ничего не генерируется и не «резюмируется» — фриз 25.08 п.4: превью показывает
только реальный последний контекст, при его отсутствии экран говорит
нейтрально «Продолжить разговор».

Сторожа из листа:
  - чужой разговор не читается (subject — request.bot_user);
  - обезличенный ход (после «забудь всё» тело пустое) темой не становится;
  - канонный ответ safety-предпроверки темой не становится;
  - салонная/мастерская/админская поверхности маршрут не видят
    (тот же сторож, что у памяти 2133);
  - реестр 2094 — запись `own` (см. test_pii_route_registry_2094).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-last-topic-2144"  # noqa: S105 — test fixture  # pragma: allowlist secret

pytestmark = pytest.mark.django_db


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(channel_user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(channel_user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="last-topic-2144", name="Last Topic 2144", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "last-topic-2144"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="21440001", display_name="Анна"
    )


@pytest.fixture
def stranger(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="21440002", display_name="Ольга"
    )


def _conversation(tenant: Tenant, bot_user: BotUser, **overrides) -> Conversation:
    kwargs = dict(tenant=tenant, bot_user=bot_user, is_active=True)
    kwargs.update(overrides)
    return Conversation.all_tenants.create(**kwargs)


def _turn(
    conv: Conversation,
    role: str,
    text: str,
    *,
    minutes_ago: int,
    action_type: str = "",
    rendered: str | None = None,
) -> Message:
    msg = Message.all_tenants.create(
        tenant=conv.tenant,
        conversation=conv,
        role=role,
        content=text,
        rendered_text=text if rendered is None else rendered,
        action_type=action_type,
    )
    Message.all_tenants.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    msg.refresh_from_db()
    return msg


def _get(client: Client, bot_user: BotUser):
    return client.get(
        reverse("miniapp_api:customer_last_topic"),
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


class TestLastTopic:
    def test_last_assistant_turn_is_the_topic_verbatim(self, client, tenant, bot_user):
        conv = _conversation(tenant, bot_user)
        _turn(conv, Message.Role.USER, "хочу массаж", minutes_ago=30)
        _turn(conv, Message.Role.ASSISTANT, "Подобрала лимфодренаж у Екатерины на завтра.", minutes_ago=29)
        _turn(conv, Message.Role.USER, "спасибо", minutes_ago=28)

        res = _get(client, bot_user)

        assert res.status_code == 200
        body = res.json()
        assert body["last_topic"]["text"] == "Подобрала лимфодренаж у Екатерины на завтра."
        assert body["last_topic"]["at"]  # ISO-строка времени хода

    def test_long_reply_is_cut_at_80_by_word_with_ellipsis(self, client, tenant, bot_user):
        conv = _conversation(tenant, bot_user)
        long = " ".join(["слово"] * 30)  # 179 знаков
        _turn(conv, Message.Role.ASSISTANT, long, minutes_ago=1)

        text = _get(client, bot_user).json()["last_topic"]["text"]

        assert text.endswith("…")
        assert len(text) <= 80
        assert "слово…" == text[-6:]  # обрезка по границе слова, не посреди

    def test_rendered_text_wins_over_content(self, client, tenant, bot_user):
        """Человек видел `rendered_text`; тема — то, что он видел."""
        conv = _conversation(tenant, bot_user)
        _turn(conv, Message.Role.ASSISTANT, "**Жирно**", minutes_ago=1, rendered="Жирно")

        assert _get(client, bot_user).json()["last_topic"]["text"] == "Жирно"

    def test_no_conversation_is_null_not_error(self, client, bot_user):
        res = _get(client, bot_user)
        assert res.status_code == 200
        assert res.json() == {"last_topic": None}

    def test_only_user_turns_is_null(self, client, tenant, bot_user):
        conv = _conversation(tenant, bot_user)
        _turn(conv, Message.Role.USER, "привет", minutes_ago=1)

        assert _get(client, bot_user).json() == {"last_topic": None}

    def test_anonymised_turn_is_not_a_topic(self, client, tenant, bot_user):
        """После «забудь всё» тело хода пустое — темы нет, а не пустая строка."""
        conv = _conversation(tenant, bot_user, anonymized_through=timezone.now())
        _turn(conv, Message.Role.ASSISTANT, "", minutes_ago=1, rendered="")

        assert _get(client, bot_user).json() == {"last_topic": None}

    def test_safety_canned_reply_is_skipped_for_the_previous_real_turn(self, client, tenant, bot_user):
        conv = _conversation(tenant, bot_user)
        _turn(conv, Message.Role.ASSISTANT, "Записала завтрак: овсянка.", minutes_ago=5)
        _turn(
            conv,
            Message.Role.ASSISTANT,
            "Если вам тяжело — позвоните 112.",
            minutes_ago=1,
            action_type="safety_pre_check",
        )

        assert _get(client, bot_user).json()["last_topic"]["text"] == "Записала завтрак: овсянка."

    def test_strangers_conversation_is_never_read(self, client, tenant, bot_user, stranger):
        theirs = _conversation(tenant, stranger)
        _turn(theirs, Message.Role.ASSISTANT, "Ольга, ваш массаж в 12:00.", minutes_ago=1)

        assert _get(client, bot_user).json() == {"last_topic": None}

    def test_shadow_and_deleted_conversations_do_not_count(self, client, tenant, bot_user):
        shadow = _conversation(tenant, bot_user, is_shadow=True)
        _turn(shadow, Message.Role.ASSISTANT, "тень", minutes_ago=1)
        gone = _conversation(tenant, bot_user, is_active=False, deleted_at=timezone.now())
        _turn(gone, Message.Role.ASSISTANT, "удалено", minutes_ago=2)

        assert _get(client, bot_user).json() == {"last_topic": None}

    def test_latest_across_conversations(self, client, tenant, bot_user):
        old = _conversation(tenant, bot_user, is_active=False)
        _turn(old, Message.Role.ASSISTANT, "старое", minutes_ago=60)
        new = _conversation(tenant, bot_user)
        _turn(new, Message.Role.ASSISTANT, "новое", minutes_ago=1)

        assert _get(client, bot_user).json()["last_topic"]["text"] == "новое"


class TestSurface:
    def test_route_exists_only_on_the_customer_surface(self):
        assert reverse("miniapp_api:customer_last_topic").startswith("/api/v1/customer/")
        for ns in ("master_api", "admin_api"):
            with pytest.raises(NoReverseMatch):
                reverse(f"{ns}:customer_last_topic")

    def test_unauthenticated_is_refused(self, client, bot_user):
        res = client.get(reverse("miniapp_api:customer_last_topic"))
        assert res.status_code == 401
