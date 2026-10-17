"""Sentinel-scoped global conversation persistence (#1026 / EPIC #1014).

The siblings of resolve_active_conversation / record_message for the tenant-less
discovery bot persist under the `global_bot` sentinel at current_tenant()=None
without entering a tenant_scope. The per-tenant functions stay untouched.
"""

from __future__ import annotations

import uuid

import pytest

from apps.conversations.models import Conversation, Message
from apps.conversations.services import (
    amend_global_assistant_text,
    record_global_message,
    record_message,
    resolve_active_conversation,
    resolve_active_global_conversation,
)
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.tenancy.context import current_tenant
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def test_resolve_and_record_under_sentinel_at_no_tenant(settings) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    assert current_tenant() is None

    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-100")
    conv = resolve_active_global_conversation(bot_user)
    assert conv is not None
    assert conv.tenant.slug == GLOBAL_BOT_TENANT_SLUG

    msg = record_global_message(conv, role="user", content="привет")
    assert msg.conversation_id == conv.id
    assert msg.tenant_id == conv.tenant_id  # message tenant == sentinel

    # Re-resolve returns the same active conversation (cross-turn).
    again = resolve_active_global_conversation(bot_user)
    assert again is not None
    assert again.id == conv.id
    # Never leaked a tenant scope.
    assert current_tenant() is None


def test_idempotent_single_active_conversation(settings) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-101")
    a = resolve_active_global_conversation(bot_user)
    b = resolve_active_global_conversation(bot_user)
    assert a is not None and b is not None
    assert a.id == b.id
    assert Conversation.all_tenants.filter(bot_user=bot_user, is_active=True).count() == 1


def test_defence_in_depth_rejects_non_sentinel(settings) -> None:
    """A non-global BotUser / Conversation must be rejected by the siblings."""
    settings.STRICT_TENANT_SCOPE = "strict"
    other = Tenant.objects.create(slug="not-sentinel", name="Other")
    foreign_bu = BotUser.all_tenants.create(tenant=other, channel="max", channel_user_id="g-102")

    with pytest.raises(ValueError, match="sentinel"):
        resolve_active_global_conversation(foreign_bu)

    # Build a global conversation, then try to record under a foreign-tenant one.
    global_bu = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-103")
    global_conv = resolve_active_global_conversation(global_bu)
    assert global_conv is not None
    foreign_conv = Conversation.all_tenants.create(tenant=other, bot_user=foreign_bu)
    with pytest.raises(ValueError, match="sentinel"):
        record_global_message(foreign_conv, role="user", content="x")
    # Sanity: the global path itself works.
    record_global_message(global_conv, role="user", content="ok")


def test_per_tenant_functions_left_untouched(settings) -> None:
    """The per-tenant resolver still fails loud at current_tenant()=None."""
    settings.STRICT_TENANT_SCOPE = "strict"
    global_bu = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-104")
    with pytest.raises(ValueError, match="tenant in scope"):
        resolve_active_conversation(global_bu)
    conv = resolve_active_global_conversation(global_bu)
    assert conv is not None
    with pytest.raises(ValueError, match="tenant in scope"):
        record_message(conv, role="user", content="x")


def test_global_input_channel_default_voice_and_rejects(settings) -> None:
    """DRF-2488 — ``input_channel`` на глобальном пути: по умолчанию ``text``,
    ``voice`` у реплики человека пишется, неизвестное значение и ``voice``
    у ответа бота — ``ValueError`` без записи.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-105")
    conv = resolve_active_global_conversation(bot_user)
    assert conv is not None

    typed = record_global_message(conv, role="user", content="привет")
    voiced = record_global_message(
        conv, role="user", content="расшифровка", input_channel=Message.InputChannel.VOICE
    )
    typed.refresh_from_db()
    voiced.refresh_from_db()
    assert (typed.input_channel, voiced.input_channel) == ("text", "voice")

    with pytest.raises(ValueError, match="is not one of"):
        record_global_message(conv, role="user", content="x", input_channel="audio")
    with pytest.raises(ValueError, match="only valid for role='user'"):
        record_global_message(conv, role="assistant", content="x", input_channel="voice")
    assert Message.all_tenants.filter(conversation=conv).count() == 2


def test_amend_rewrites_exactly_the_assistant_row_of_the_turn(settings) -> None:
    """DRF-2686 — правка на месте: одна строка хода, токены и действие целы."""
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-2686")
    conv = resolve_active_global_conversation(bot_user)
    assert conv is not None
    trace, other = str(uuid.uuid4()), str(uuid.uuid4())
    record_global_message(conv, role="user", content="привет", trace_id=trace)
    mine = record_global_message(
        conv, role="assistant", content="Ответ", rendered_text="Ответ", trace_id=trace,
        action_type="concierge", tokens_in=7, tokens_out=3,
    )  # fmt: skip
    earlier = record_global_message(conv, role="assistant", content="Ответ", trace_id=other)

    amended = amend_global_assistant_text(
        conv, trace_id=trace, expected="Ответ", text="Я услышала: «привет»\n\nОтвет"
    )

    assert amended == 1
    mine.refresh_from_db()
    assert mine.content == mine.rendered_text == "Я услышала: «привет»\n\nОтвет"
    assert (mine.action_type, mine.tokens_in, mine.tokens_out) == ("concierge", 7, 3)
    earlier.refresh_from_db()
    assert earlier.content == "Ответ"  # тот же текст, другой ход — не тронут
    assert Message.all_tenants.filter(conversation=conv, role="user").get().content == "привет"


def test_amend_leaves_a_row_whose_text_has_moved_on(settings) -> None:
    """Сверка прежнего текста: обезличенную («забудь всё») строку текстом не заливаем."""
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-2687")
    conv = resolve_active_global_conversation(bot_user)
    assert conv is not None
    trace = str(uuid.uuid4())
    row = record_global_message(conv, role="assistant", content="Ответ", trace_id=trace)
    Message.all_tenants.filter(pk=row.pk).update(content="", rendered_text="")

    assert amend_global_assistant_text(conv, trace_id=trace, expected="Ответ", text="эхо") == 0
    row.refresh_from_db()
    assert row.content == ""
    assert amend_global_assistant_text(conv, trace_id=None, expected="", text="эхо") == 0


def test_amend_rejects_a_non_sentinel_conversation(settings) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    tenant = Tenant.objects.create(slug="amend-2686", name="Amend 2686")
    bot_user = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="t-2686")
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    with pytest.raises(ValueError, match="not the global_bot sentinel"):
        amend_global_assistant_text(conv, trace_id=str(uuid.uuid4()), expected="a", text="b")
