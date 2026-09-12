"""Незнакомец в салонном боте не получает ``BotUser`` до доказанного пути (DRF-1784, срез 4b).

Решение владельца D2 (12.09.2026) → (б): строка ``BotUser`` незнакомцу не
создаётся, пока он не доказал путь — кодом приглашения либо «Я работаю сам».
До этого среза обработчик создавал строку в тенанте записи бота первым же
сообщением любого написавшего (после 4a — только тем, у кого нет рабочей
строки), и без тенанта записи отказывал под именем ``…_until_4b``.

Правило 4b:

* нет рабочей строки → путь незнакомца идёт **без строки**: «введите код /
  Я работаю сам», ``/whoami`` по личности, счётчик попыток — по личности;
* код → тенант **из самого кода** (``StaffInvite`` по хэшу, без фильтра по
  тенанту записи), строка создаётся **в тенанте кода** перед ``TenantStaff``;
* «Я работаю сам» → ``create_solo_provider`` из данных события; единственная
  строка личности — в соло-тенанте;
* приглашение мастера по ссылке → тенант из карточки, к которой привязан
  токен (``validate_invite_token`` без тенанта; файл в реестре MKT1).

Тенант записи бота (``MAX_BOT_SALON_TENANT_SLUG``) на этом пути больше не
читается вовсе — срез 4c снимает переменную, и здесь уже нечему ломаться.
Каждый тест называет, красный ли он до правки.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_handler
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "4004114"
CHAT_ID = "557"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    miniapp_url="https://app.example/staff",
)


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)
    cache.clear()


@pytest.fixture
def salon() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


@pytest.fixture
def other_salon() -> Tenant:
    return Tenant.all_objects.create(slug="salon-y-4b", name="Салон Игрек", is_active=True)


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
            "body": {"mid": f"mid-4b-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _handle(text: str, tenant: Tenant | None, *, update_id: int = 1) -> None:
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


def _text(sent) -> str:
    return sent.call_args.kwargs["text"]


class TestAStrangerLeavesNoRow:
    def test_hello_under_the_entry_tenant_creates_nothing(self, salon, sent):
        """Красный до правки (эталон 4a перевёрнут): строка в тенанте записи создавалась."""

        _handle("привет", salon)

        assert _rows() == []
        assert "код" in _text(sent).lower()
        assert salon_handler.SOLO_OFFER.strip() in _text(sent)

    def test_hello_without_any_tenant_is_answered_not_refused(self, salon, sent, settings, caplog):
        """Красный до правки: ERROR stranger_without_tenant_until_4b и молчание."""

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

        with caplog.at_level(logging.ERROR, logger="apps.channels.max.salon_handler"):
            _handle("привет", None)

        assert "код" in _text(sent).lower()
        assert _rows() == []
        assert [
            r.getMessage() for r in caplog.records
        ] == []  # присутствие ответа выше; ошибок — ни одной

    def test_a_wrong_code_refuses_and_still_creates_nothing(self, salon, sent):
        """Красный до правки: строка создавалась до проверки кода."""

        _handle("AYLA-ZZZZ", salon)

        assert _text(sent) == salon_handler.CODE_NOT_ACCEPTED
        assert _rows() == []

    def test_whoami_works_by_identity_without_a_row(self, salon, sent):
        """Красный до правки: строка создавалась ради карточки."""

        _handle(salon_handler.WHOAMI_COMMAND, salon)

        sent.assert_called()
        assert _rows() == []

    def test_attempts_are_counted_by_identity_without_a_row(self, salon, sent):
        """Лимит подбора живёт на личности, не на строке — строки нет, лимит есть."""

        from apps.identity.services.staff_invites import MAX_ATTEMPTS

        for i in range(MAX_ATTEMPTS + 1):
            _handle("AYLA-ZZZZ", salon, update_id=10 + i)

        assert _text(sent) == salon_handler.TOO_MANY_ATTEMPTS
        assert _rows() == []


class TestTheCodeDecidesTheTenant:
    def test_a_code_creates_the_row_in_the_codes_tenant(self, salon, sent):
        """Красный до правки только без scope; под scope салона — зелёный (тот же тенант)."""

        _, code = issue_staff_invite(tenant=salon, role=StaffInvite.Role.ADMIN)

        _handle(code, None)

        rows = _rows()
        assert len(rows) == 1 and rows[0].tenant == salon
        assert TenantStaff.all_tenants.filter(
            tenant=salon, bot_user=rows[0], role=TenantStaff.Role.ADMIN, deactivated_at__isnull=True
        ).exists()
        assert "Формула тела" in _text(sent)

    def test_a_code_of_another_salon_is_redeemed_there_not_refused(self, salon, other_salon, sent):
        """Красный до правки: код чужого салона под scope записи — «не подошёл» (фильтр по тенанту записи).

        Решение 12.09: бот не принадлежит салону, тенант — из кода. Эталон
        staff_invites «код салона B в боте салона A не находится» переворачивается.
        """

        _, code = issue_staff_invite(tenant=other_salon, role=StaffInvite.Role.ADMIN)

        _handle(code, salon)

        rows = _rows()
        assert len(rows) == 1 and rows[0].tenant == other_salon, [r.tenant.slug for r in rows]
        assert TenantStaff.all_tenants.filter(tenant=other_salon, bot_user=rows[0]).exists()
        assert "Салон Игрек" in _text(sent)
        assert not BotUser.all_tenants.filter(
            tenant=salon, channel_user_id=CHANNEL_USER_ID
        ).exists()

    def test_the_second_message_after_a_code_is_served_by_the_working_row(
        self, salon, other_salon, sent
    ):
        """После выкупа человек — сотрудник; 4a ведёт его по рабочей строке."""

        _, code = issue_staff_invite(tenant=other_salon, role=StaffInvite.Role.ADMIN)
        _handle(code, salon, update_id=1)
        sent.reset_mock()

        _handle("привет", salon, update_id=2)

        assert len(_rows()) == 1
        assert "Салон Игрек" in _text(sent)
        assert "код приглашения" not in _text(sent)


class TestSoloRegistrationFromTheEventAlone:
    def test_i_work_alone_creates_exactly_one_row_in_the_solo_tenant(self, salon, sent):
        """Красный до правки: две строки — салонная customer и соло."""

        _handle(salon_handler.SOLO_REGISTER_CALLBACK, salon)

        rows = _rows()
        assert len(rows) == 1, [r.tenant.slug for r in rows]
        assert rows[0].tenant.slug.startswith("solo-")
        assert _text(sent) == salon_handler.SOLO_CREATED_PENDING

    def test_a_returning_solo_master_is_served_in_her_workspace(self, salon, sent):
        """Второй визит после «Я работаю сам»: рабочая строка есть — 4a ведёт в кабинет."""

        _handle(salon_handler.SOLO_REGISTER_CALLBACK, salon, update_id=1)
        sent.reset_mock()

        _handle("привет", salon, update_id=2)

        assert len(_rows()) == 1
        assert _rows()[0].tenant.slug.startswith("solo-")
        assert (_rows()[0].tenant.name or _rows()[0].tenant.slug) in _text(
            sent
        )  # меню называет кабинет
        assert "код приглашения" not in _text(sent)


class TestStaffAreUnchanged:
    def test_an_admin_of_the_salon_still_gets_the_menu(self, salon, sent):
        """Контроль: зелёный до и после."""

        row = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID
        )
        TenantStaff.all_tenants.create(
            tenant=salon, bot_user=row, role=TenantStaff.Role.ADMIN, created_by=row
        )

        _handle("привет", salon)

        assert len(_rows()) == 1
        assert "Формула тела" in _text(sent)
