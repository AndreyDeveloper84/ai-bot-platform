"""Салонный бот при двух рабочих тенантах спрашивает, а не выбирает (DRF-1766, срез 5).

После 4a (DRF-1783) обработчик ведёт человека по рабочей строке; при двух
рабочих строках резолвер до этого среза брал старейший тенант молча
(WARNING). Теперь: кнопки с именами салонов, ответ человека — в кэш по
личности на время разговора, дальше — меню выбранного салона. Владелец
(DRF-1705): выбор, не страж.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_handler
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "5600561"
CHAT_ID = "558"


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token="token-salon",  # pragma: allowlist secret
            tenant_slug="",
            stream="max_salon",
            miniapp_url="https://app.example/staff",
        ),
    )
    cache.clear()


@pytest.fixture
def two_salons() -> tuple[Tenant, Tenant]:
    older = Tenant.all_objects.create(slug="bot-choice-older", name="Первый салон", is_active=True)
    younger = Tenant.all_objects.create(
        slug="bot-choice-younger", name="Второй салон", is_active=True
    )
    Tenant.all_objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=30))
    older.refresh_from_db()
    for t in (older, younger):
        row = BotUser.all_tenants.create(tenant=t, channel="max", channel_user_id=CHANNEL_USER_ID)
        TenantStaff.all_tenants.create(
            tenant=t, bot_user=row, role=TenantStaff.Role.ADMIN, created_by=row
        )
    return older, younger


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _payload(text: str, *, update_id: int = 1) -> dict:
    return {
        "update_type": "message_created",
        "update_id": update_id,
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Ира", "is_bot": False},
            "recipient": {"chat_id": int(CHAT_ID), "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-ch-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _handle(text: str, *, update_id: int = 1) -> None:
    with tenant_scope(None):
        handle_salon_max_event(_payload(text, update_id=update_id))


def _buttons(sent) -> list[dict]:
    out: list[dict] = []
    for att in sent.call_args.kwargs.get("attachments") or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            out.extend(row)
    return out


class TestTwoWorkingTenantsAskTheperson:
    def test_hello_gets_two_buttons_and_no_menu(self, two_salons, sent):
        """Красный до правки: меню старейшего салона молча."""

        older, younger = two_salons

        _handle("привет")

        text = sent.call_args.kwargs["text"]
        assert salon_handler.SALON_CHOICE_PROMPT in text
        labels = [b.get("text") for b in _buttons(sent)]
        assert labels == [older.name, younger.name]
        payloads = [b.get("payload") for b in _buttons(sent)]
        assert payloads == [
            f"{salon_handler.CB_SALON_CHOOSE_PREFIX}{older.slug}",
            f"{salon_handler.CB_SALON_CHOOSE_PREFIX}{younger.slug}",
        ]

    def test_the_tap_serves_the_chosen_salon_and_is_remembered(self, two_salons, sent):
        older, younger = two_salons

        _handle(f"{salon_handler.CB_SALON_CHOOSE_PREFIX}{younger.slug}", update_id=1)
        assert younger.name in sent.call_args.kwargs["text"]
        assert older.name not in sent.call_args.kwargs["text"]
        sent.reset_mock()

        _handle("привет", update_id=2)

        assert younger.name in sent.call_args.kwargs["text"]
        assert salon_handler.SALON_CHOICE_PROMPT not in sent.call_args.kwargs["text"]

    def test_a_tap_on_a_salon_without_a_role_asks_again(self, two_salons, sent):
        """Отрицательный контроль: чужой слаг в кнопке — снова вопрос, не чужой салон."""

        Tenant.all_objects.create(slug="bot-choice-foreign", name="Чужой", is_active=True)

        _handle(f"{salon_handler.CB_SALON_CHOOSE_PREFIX}bot-choice-foreign")

        assert salon_handler.SALON_CHOICE_PROMPT in sent.call_args.kwargs["text"]
        assert "Чужой" not in sent.call_args.kwargs["text"]
        assert BotUser.all_tenants.filter(channel_user_id=CHANNEL_USER_ID).count() == 2

    def test_one_working_tenant_is_served_without_a_question(self, sent):
        """Контроль среза 4a."""

        salon = Tenant.all_objects.create(
            slug="bot-choice-single", name="Единственный", is_active=True
        )
        row = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID
        )
        TenantStaff.all_tenants.create(
            tenant=salon, bot_user=row, role=TenantStaff.Role.ADMIN, created_by=row
        )

        _handle("привет")

        assert "Единственный" in sent.call_args.kwargs["text"]
        assert salon_handler.SALON_CHOICE_PROMPT not in sent.call_args.kwargs["text"]
