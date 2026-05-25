"""Tests for payment_failed skill (α-mode, W2/Epsilon pre-flip).

Контракт pin-ится:

* ``on_payment_failed_event`` принимает D3 consumer-enriched dict
  (НЕ envelope). Шлёт client DM с retry-кнопкой + пишет audit row для
  master DM skip (α-mode: bridge ``CatalogMaster.ayla_user_id``
  отсутствует — wire-up в follow-up PR после W1).
* ``PaymentRetryCallbackSkill`` обрабатывает ``cb:payment:retry:<id>``:
  matches/handle, auth-fail для unbridged BotUser, malformed callbacks,
  graceful stub ответа пока Alpha task #66 не закрыт.

Mocking strategy:

* ``apps.channels.max.outbound.send_message`` → spy через monkeypatch.
* ``apps.audit.services.write_audit`` → spy для audit-row assertions.
* ``BotUser.all_tenants`` — реальный ORM через ``django_db`` fixture.

Audit row schema (tech-lead 2026-05-25):

    {
        "event": "payment_failed.master_dm_skipped_no_bridge",
        "payment_event_id": <uuid|null>,
        "payment_id": <uuid>,
        "master_user_id": <ayla_uuid>,
        "tenant_id": <uuid|null>,
        "yclients_staff_id": <int|null>,
        "timestamp": <iso>,
        "reason": "catalogmaster_ayla_user_id_missing",
    }
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from apps.skills.base import SkillContext


pytestmark = pytest.mark.django_db


# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def tenant(db):
    from apps.tenancy.models import Tenant

    return Tenant.objects.create(slug="pf-test", name="Payment-Failed Test")


@pytest.fixture
def make_bot_user(tenant):
    """Factory — создаёт BotUser с заданным ayla_user_id и chat_id."""
    from apps.identity.models import BotUser

    def _factory(*, ayla_user_id, chat_id="max-12345", display_name=""):
        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=chat_id,
            chat_id=chat_id,
            ayla_user_id=ayla_user_id,
            display_name=display_name,
        )

    return _factory


@pytest.fixture
def sent_dms(monkeypatch):
    """Spy для send_message — собирает все calls."""
    calls: list[dict[str, Any]] = []

    def _spy(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", _spy)
    return calls


@pytest.fixture
def written_audits(monkeypatch):
    """Spy для write_audit — собирает все calls."""
    calls: list[dict[str, Any]] = []

    def _spy(action, *, target="", target_id=None, payload=None, actor_id=None):
        calls.append(
            {
                "action": action,
                "target": target,
                "target_id": target_id,
                "payload": payload,
                "actor_id": actor_id,
            }
        )

    monkeypatch.setattr("apps.audit.services.write_audit", _spy)
    return calls


# ───────────────────────────────────────────────────────────────────────
# Test data
# ───────────────────────────────────────────────────────────────────────


CLIENT_AYLA = uuid.UUID("11111111-1111-1111-1111-111111111111")
MASTER_AYLA = uuid.UUID("22222222-2222-2222-2222-222222222222")
PAYMENT_ID = "33333333-3333-3333-3333-333333333333"
APPT_ID = "44444444-4444-4444-4444-444444444444"
TENANT_ID = "55555555-5555-5555-5555-555555555555"
EVENT_ID = "66666666-6666-6666-6666-666666666666"


def _enriched_data(**override):
    """D3 consumer-enriched dict (НЕ envelope — это уже data после consumer)."""
    base = {
        "payment_id": PAYMENT_ID,
        "appointment_id": APPT_ID,
        "client_user_id": str(CLIENT_AYLA),
        "master_user_id": str(MASTER_AYLA),
        "amount": 1500,
        "service_name": "Маникюр",
        "appointment_date": "15 мая 14:00",
        "master_name": "Таня",
        "client_name": "Анна",
        "reason": "card_declined",
        "failed_at": "2026-05-25T10:00:00+03:00",
        "payment_event_id": EVENT_ID,
        "tenant_id": TENANT_ID,
        "yclients_staff_id": None,  # α-mode: bridge через yclients_staff_id отсутствует в D3 dict
    }
    base.update(override)
    return base


# ───────────────────────────────────────────────────────────────────────
# on_payment_failed_event — happy + edge paths
# ───────────────────────────────────────────────────────────────────────


class TestOnPaymentFailedEvent:
    def test_client_dm_with_retry_button(self, make_bot_user, sent_dms, written_audits):
        """Happy path α: client BotUser существует → DM с inline-кнопкой
        [Оплатить]. Master DM skip-ается с audit row (α-mode: no bridge)."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        # Master BotUser не нужен в α-mode (master DM всегда skip).

        on_payment_failed_event(_enriched_data())

        # Один DM — клиенту.
        assert len(sent_dms) == 1
        client_dm = sent_dms[0]
        assert client_dm["chat_id"] == "max-client"
        assert "не прошёл" in client_dm["text"]
        # Inline-кнопка [Оплатить] с deterministic callback.
        buttons = client_dm["attachments"][0]["payload"]["buttons"]
        assert buttons == [
            {
                "label": "Оплатить",
                "callback": f"cb:payment:retry:{PAYMENT_ID}",
            }
        ]

        # Audit row для master DM skip присутствует.
        audit_rows = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped_no_bridge"
        ]
        assert len(audit_rows) == 1

    def test_master_dm_audit_row_schema(self, make_bot_user, sent_dms, written_audits):
        """α-mode: audit row для master DM skip следует exact schema
        от tech-lead 2026-05-25 — позволяет backfill walker найти
        пропущенные нотификации когда W1 bridge merge-нут."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        on_payment_failed_event(_enriched_data(yclients_staff_id=42))

        master_skip = next(
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped_no_bridge"
        )
        payload = master_skip["payload"]
        # Все обязательные поля schema присутствуют.
        assert payload["event"] == "payment_failed.master_dm_skipped_no_bridge"
        assert payload["payment_event_id"] == EVENT_ID
        assert payload["payment_id"] == PAYMENT_ID
        assert payload["appointment_id"] == APPT_ID
        assert payload["master_user_id"] == str(MASTER_AYLA)
        assert payload["tenant_id"] == TENANT_ID
        assert payload["yclients_staff_id"] == 42  # null или int — оба OK
        assert payload["reason"] == "catalogmaster_ayla_user_id_missing"
        # timestamp — ISO 8601 string.
        assert "T" in payload["timestamp"]

    def test_master_skip_audit_skipped_when_no_master_user_id(
        self,
        make_bot_user,
        sent_dms,
        written_audits,
    ):
        """Edge: если в payload нет master_user_id (consumer enrichment fail),
        audit-row тоже не пишется — нечем backfill-ить."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        on_payment_failed_event(_enriched_data(master_user_id=None))

        master_skip = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped_no_bridge"
        ]
        assert master_skip == []

    def test_client_dm_skipped_when_bot_user_missing(
        self,
        sent_dms,
        written_audits,
    ):
        """Клиент никогда не писал в бот → BotUser отсутствует.
        Skill graceful skip без raise. Audit row для master DM всё равно
        пишется (backfill replay будет полезен)."""
        from apps.skills.payment_failed import on_payment_failed_event

        # Никаких BotUser-ов.
        on_payment_failed_event(_enriched_data())

        assert sent_dms == []
        # Master skip audit row всё равно пишется.
        master_skip = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped_no_bridge"
        ]
        assert len(master_skip) == 1

    def test_no_payment_id_logs_warning_and_returns(self, sent_dms, written_audits, caplog):
        """data без payment_id → ничего не делаем + WARNING."""
        from apps.skills.payment_failed import on_payment_failed_event

        with caplog.at_level(logging.WARNING, logger="apps.skills.payment_failed.skill"):
            on_payment_failed_event({})

        assert sent_dms == []
        assert written_audits == []
        assert any("payload_missing_payment_id" in r.message for r in caplog.records)

    def test_does_not_raise_on_send_failure(
        self,
        make_bot_user,
        monkeypatch,
        written_audits,
        caplog,
    ):
        """Если MAX API throw-ит (5xx, timeout) — skill log-ает но не raise.
        Иначе Gamma's consumer fail-нул бы dedupe и event переобрабатывался
        бы на каждом retry с лишним audit row."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")

        def _throws(**_kwargs):
            raise RuntimeError("MAX 503")

        monkeypatch.setattr("apps.channels.max.outbound.send_message", _throws)
        with caplog.at_level(logging.ERROR, logger="apps.skills.payment_failed.skill"):
            on_payment_failed_event(_enriched_data())  # не должен raise

        assert any("dm_send_failed" in r.message for r in caplog.records)


# ───────────────────────────────────────────────────────────────────────
# PaymentRetryCallbackSkill
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def callback_context(make_bot_user):
    """SkillContext с client BotUser (ayla_user_id bridge-нут)."""
    from apps.conversations.models import Conversation

    client_bot_user = make_bot_user(
        ayla_user_id=CLIENT_AYLA,
        chat_id="max-client",
    )
    conv = Conversation.all_tenants.create(
        tenant=client_bot_user.tenant,
        bot_user=client_bot_user,
    )

    def _build(text: str):
        return SkillContext(
            conversation=conv,
            bot_user=client_bot_user,
            message_text=text,
        )

    return _build


@pytest.fixture
def unbridged_callback_context(tenant):
    """SkillContext с BotUser у которого нет ayla_user_id."""
    from apps.conversations.models import Conversation
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="max-unbridged",
        chat_id="max-unbridged",
        ayla_user_id=None,  # explicit: bridge не установлен
    )
    conv = Conversation.all_tenants.create(tenant=bot_user.tenant, bot_user=bot_user)

    def _build(text: str):
        return SkillContext(
            conversation=conv,
            bot_user=bot_user,
            message_text=text,
        )

    return _build


class TestPaymentRetryCallbackSkill:
    def test_matches_prefix(self, callback_context):
        from apps.skills.payment_failed import PaymentRetryCallbackSkill

        skill = PaymentRetryCallbackSkill()
        assert skill.matches(callback_context(f"cb:payment:retry:{PAYMENT_ID}")) is True
        # Не matches на других namespace-ах.
        assert skill.matches(callback_context("cb:book:pick_master:11")) is False
        assert skill.matches(callback_context("hello")) is False

    def test_handle_stubbed_pending_endpoint(self, callback_context):
        """Сейчас (до Alpha task #66) handle отвечает заглушкой —
        не делает HTTP, возвращает graceful PENDING-text."""
        from apps.skills.payment_failed import PaymentRetryCallbackSkill

        result = PaymentRetryCallbackSkill().handle(
            callback_context(f"cb:payment:retry:{PAYMENT_ID}"),
        )
        text = result.reply_text.lower()
        assert "недоступна" in text or "временно" in text
        assert result.should_handoff is False
        assert result.action_type == "payment_retry"

    def test_unbridged_bot_user_refused(self, unbridged_callback_context):
        """BotUser без ayla_user_id → не можем verify ownership →
        graceful refuse + log."""
        from apps.skills.payment_failed import PaymentRetryCallbackSkill

        result = PaymentRetryCallbackSkill().handle(
            unbridged_callback_context(f"cb:payment:retry:{PAYMENT_ID}"),
        )
        text = result.reply_text.lower()
        assert "не для тебя" in text or "свой чат" in text

    def test_malformed_callback_refused(self, callback_context):
        """Пустой payload после префикса → ругаемся вежливо."""
        from apps.skills.payment_failed import PaymentRetryCallbackSkill

        result = PaymentRetryCallbackSkill().handle(
            callback_context("cb:payment:retry:"),
        )
        text = result.reply_text.lower()
        assert "не удалось распознать" in text or "проблема" in text
