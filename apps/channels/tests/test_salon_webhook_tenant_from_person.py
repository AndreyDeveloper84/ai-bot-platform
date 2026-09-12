"""Вебхук салонного бота: тенант — от человека, не от записи бота (DRF-1783, срез 4a).

Замер `docs/WEBHOOK_TENANTLESS_MEASUREMENT_DRF1757.md`. До этого среза
обработчик брал тенант из ``tenant_scope``, в который консумер входил по
``entry.tenant_slug`` (= ``MAX_BOT_SALON_TENANT_SLUG``), и **первым же
шагом** создавал ``BotUser`` в этом тенанте любому написавшему — так у
соло-мастера появлялась вторая, салонная строка ``customer``, и бот отвечал
ему «введите код». Без тенанта записи обработчик молчал (``no_tenant_scope``).

Правило 4a: сначала **рабочая строка личности** (``resolve_working_bot_user``,
DRF-1755) → её тенант → штатный поток сотрудника. Нет рабочей строки →
путь незнакомца: до среза 4b (DRF-1784) он остаётся прежним и требует
тенанта записи; без тенанта — ERROR с именем следующего среза, не молчание
без имени. Ingress в этом срезе не меняется: пока переменная стоит,
тенант записи приезжает как раньше, и незнакомцы обслуживаются как раньше;
переменную снимает срез 4c.

Каждый тест называет, красный ли он до правки.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.handlers import SalonMaxHandler
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "4004004"
CHAT_ID = "556"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    miniapp_url="https://app.example/staff",
)
SALON_BOT_TENANTLESS = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="",
    stream="max_salon",
    miniapp_url="https://app.example/staff",
)


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)


@pytest.fixture
def salon() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


@pytest.fixture
def solo() -> Tenant:
    return Tenant.all_objects.create(slug="solo-max-4a", name="Кабинет Ольги", is_active=True)


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
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Ольга", "is_bot": False},
            "recipient": {"chat_id": int(CHAT_ID), "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-4a-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _handle(text: str, tenant: Tenant | None, *, update_id: int = 1) -> None:
    # Свободный текст мастера уходит ассистенту (LLM); здесь он не предмет —
    # сломанный ассистент по контракту падает в меню, и меню называет салон.
    with (
        tenant_scope(tenant),
        patch(
            "apps.master_api.services.assistant.answer_master_question",
            side_effect=RuntimeError("no assistant in this test"),
        ),
    ):
        handle_salon_max_event(_payload(text, update_id=update_id))


def _rows() -> list[BotUser]:
    return list(BotUser.all_tenants.filter(channel="max", channel_user_id=CHANNEL_USER_ID))


def _solo_master(solo: Tenant) -> BotUser:
    row = BotUser.all_tenants.create(tenant=solo, channel="max", channel_user_id=CHANNEL_USER_ID)
    TenantStaff.all_tenants.create(
        tenant=solo, bot_user=row, role=TenantStaff.Role.OWNER, created_by=row
    )
    CatalogMaster.all_tenants.create(
        tenant=solo,
        name="Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=row,
    )
    return row


class TestTheWorkingRowDecidesTheTenant:
    def test_a_solo_master_is_served_in_her_own_tenant_and_gets_no_salon_row(
        self, salon, solo, sent
    ):
        """Красный до правки: строк становилось 2 (салонная customer), ответ — «введите код»."""

        _solo_master(solo)

        _handle("привет", salon)

        assert len(_rows()) == 1, [r.tenant.slug for r in _rows()]
        assert _rows()[0].tenant == solo
        text = sent.call_args.kwargs["text"]
        assert "Кабинет Ольги" in text, text
        assert "код приглашения" not in text

    def test_the_same_without_any_tenant_scope(self, salon, solo, sent, settings):
        """Красный до правки: без тенанта записи обработчик молчал (no_tenant_scope)."""

        settings.MAX_BOT_REGISTRY = (SALON_BOT_TENANTLESS,)
        _solo_master(solo)

        _handle("привет", None)

        sent.assert_called()
        assert "Кабинет Ольги" in sent.call_args.kwargs["text"]
        assert len(_rows()) == 1

    def test_a_salon_master_is_served_as_before(self, salon, sent):
        """Отрицательный контроль: мастер салона — в салоне, новых строк нет. Зелёный до и после."""

        row = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID
        )
        TenantStaff.all_tenants.create(
            tenant=salon, bot_user=row, role=TenantStaff.Role.ADMIN, created_by=row
        )

        _handle("привет", salon)

        assert len(_rows()) == 1
        assert "Формула тела" in sent.call_args.kwargs["text"]


class TestTheStrangerPathIsHandedToSliceFourB:
    def test_a_stranger_under_the_entry_tenant_is_served_as_before(self, salon, sent):
        """До 4b (DRF-1784) незнакомец — как раньше: строка в тенанте записи, «введите код».

        Зелёный до и после. Эталон, который 4b перевернёт: строк станет 0.
        """

        _handle("привет", salon)

        assert len(_rows()) == 1 and _rows()[0].tenant == salon
        assert "код" in sent.call_args.kwargs["text"].lower()

    def test_a_stranger_without_any_tenant_is_refused_by_name_not_in_silence(
        self, salon, sent, settings, caplog
    ):
        """Красный до правки: тот же исход — молчание, — но под именем no_tenant_scope.

        Имя важно: оно говорит, чего не хватает (среза 4b), а не «консумер
        забыл войти в scope». Строк не создаётся.
        """

        settings.MAX_BOT_REGISTRY = (SALON_BOT_TENANTLESS,)

        with caplog.at_level(logging.ERROR, logger="apps.channels.max.salon_handler"):
            _handle("привет", None)

        sent.assert_not_called()
        assert _rows() == []
        assert any("stranger_without_tenant_until_4b" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]
        assert not any("no_tenant_scope" in r.getMessage() for r in caplog.records)


class TestTheConsumerNoLongerRequiresATenant:
    def test_salon_handler_opts_out_of_requires_tenant(self):
        """Красный до правки: ``requires_tenant`` наследовал True («tenant-bound by construction»)."""

        assert SalonMaxHandler.__dict__.get("requires_tenant") is False
