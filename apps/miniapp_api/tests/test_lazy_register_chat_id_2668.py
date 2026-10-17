"""DRF-2668 — ``chat.id = null`` в initData не становится строкой ``"None"``.

``_lazy_register_bot_user`` писал ``str(chat.get("id"))`` — при ``id: null``
это строка ``"None"``. Резолвер личностей дописывает только ПУСТЫЕ поля
(«never overwrite a non-blank value»), поэтому ``"None"`` застревала навсегда:
первое сообщение боту уже не ставило настоящий ``chat_id``.

Пары, которые обязаны различаться:

* ``chat.id = null`` → пусто; ``chat.id = 500000004`` → ``"500000004"``;
* после входа с ``id: null`` первое сообщение боту ЗАПОЛНЯЕТ ``chat_id`` —
  это и есть починка необратимости, а не косметика.

Замер пилота 29.09 (главное окно): у 28 личностей ``"None"`` — 0. Правка
предупредительная.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest

from apps.identity.services.resolver import resolve_or_create_bot_user
from apps.miniapp_api.auth import verify_init_data
from apps.miniapp_api.views import _lazy_register_bot_user
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2668"  # pragma: allowlist secret
MAX_USER_ID = 26680001
REAL_CHAT_ID = 500000004


def _verified(chat: dict | None):
    params = {
        "user": json.dumps({"id": MAX_USER_ID, "first_name": "Проба2668"}),
        "auth_date": str(int(time_module.time())),
    }
    if chat is not None:
        params["chat"] = json.dumps(chat)
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return verify_init_data(urlencode({**params, "hash": digest}), bot_token=BOT_TOKEN)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        slug="lazy-chat-2668", name="Lazy Chat 2668", timezone="Europe/Moscow"
    )


class TestAbsentChatIdStaysAbsent:
    @pytest.mark.parametrize(
        ("chat", "expected"),
        [
            ({"id": None, "type": "dialog"}, ""),
            (None, ""),
            ({"type": "dialog"}, ""),
            ({"id": REAL_CHAT_ID, "type": "dialog"}, str(REAL_CHAT_ID)),
        ],
        ids=["id-null", "no-chat", "no-id", "real-id"],
    )
    def test_null_is_empty_a_real_id_is_kept(self, tenant, chat, expected):
        bot_user = _lazy_register_bot_user(tenant, _verified(chat))
        assert bot_user.chat_id == expected
        assert bot_user.chat_id != "None"


class TestTheFirstBotMessageCanStillFillIt:
    def test_after_a_null_chat_the_first_bot_message_sets_the_real_chat_id(self, tenant):
        created = _lazy_register_bot_user(tenant, _verified({"id": None, "type": "dialog"}))
        assert created.chat_id == ""

        with tenant_scope(tenant):
            again = resolve_or_create_bot_user(
                channel="max", channel_user_id=str(MAX_USER_ID), chat_id=str(REAL_CHAT_ID)
            )

        assert again.pk == created.pk
        again.refresh_from_db()
        assert again.chat_id == str(REAL_CHAT_ID)
