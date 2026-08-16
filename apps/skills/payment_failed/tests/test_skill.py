"""Tests для payment_failed skill (Sequence #4 master DM wire-up, 2026-05-27).

Контракт pin-ится:

* ``on_payment_failed_event`` принимает D3 consumer-enriched dict
  (НЕ envelope). Шлёт client DM с retry-кнопкой + master DM via
  full lookup chain (RemoteBookingProxy → CatalogMaster.ayla_user_id
  → linked_bot_user → BotUser.chat_id).
* All edge cases в lookup chain → graceful skip + audit row (no batch
  break per defensive #851/#874 pattern).
* ``PaymentRetryCallbackSkill`` обрабатывает ``cb:payment:retry:<id>``:
  matches/handle, auth-fail для unbridged BotUser, malformed callbacks,
  graceful stub ответа пока Alpha task #66 не закрыт.

Mocking strategy:

* ``apps.channels.max.outbound.send_message`` → spy via monkeypatch.
* ``apps.audit.services.write_audit`` → spy для audit-row assertions.
* All mirror models (BotUser, RemoteBookingProxy, CatalogMaster,
  CatalogService) → real ORM через ``django_db`` fixture.

Audit row schemas:

* Success: ``payment_failed.master_dm_sent`` — emit on dispatch.
* Skip:    ``payment_failed.master_dm_skipped`` с reason slug:
  - ``remote_booking_proxy_missing``
  - ``proxy_no_specialist_id``
  - ``catalog_master_missing``
  - ``master_not_onboarded``
  - ``master_no_chat_id``
"""

from __future__ import annotations

import hashlib
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


# `external_id` on the catalog mirrors is a Django `IntegerField`
# (`apps/catalog/models.py`, `_MirrorBase`) → Postgres `integer`, i.e.
# signed 32-bit, max 2 147 483 647. This helper exists because the two
# call sites below used `hash(str(x)) & 0xFFFFFFFF`, which is wrong twice:
#
#   1. `0xFFFFFFFF` = 4 294 967 295 — roughly half the values overflow
#      `integer` and Postgres rejects them with `DataError: integer out
#      of range`. SQLite (the no-docker local default) does not check
#      integer width, so this was invisible outside CI.
#   2. Python's `hash()` is salt-randomised per process (`PYTHONHASHSEED`),
#      so whether a given run overflowed was a coin flip — a flaky test,
#      not a stably red one. Same reasoning as `apps.skills.faq.tools
#      ._cache_key`: when a value must be reproducible, use a digest.
#
# SHA-256 truncated into the signed-32-bit range is deterministic across
# processes and always in range, and keeps the original semantics that
# equal inputs map to equal `external_id`s (the mirrors carry
# `unique_together = (("tenant", "external_id"),)`).
def _stable_external_id(value: object) -> int:
    """Deterministic, `integer`-safe surrogate id for mirror fixtures."""
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _enriched_data(*, tenant_id_override=None, **override):
    """Phase 1 Option C consumer-enriched dict (Sequence #4).

    Reflects ACTUAL payload Gamma's consumer ships per
    ``apps/eventbus/consumers/payment.py`` lines 427-437:

        payment_id, appointment_id, client_user_id, tenant_id,
        failure_code, consecutive_failures, failed_at,
        payment_event_id, client_name

    Master DM enrichment comes from local mirror tables; nothing
    в the payload identifies the master. ``tenant_id_override`` lets
    test cases swap in a per-test tenant UUID so lookup chain
    queries match the actual fixture-created tenant.
    """
    base = {
        "payment_id": PAYMENT_ID,
        "appointment_id": APPT_ID,
        "client_user_id": str(CLIENT_AYLA),
        "tenant_id": tenant_id_override or TENANT_ID,
        "failure_code": "card_declined",
        "consecutive_failures": 3,
        "failed_at": "2026-05-25T10:00:00+03:00",
        "payment_event_id": EVENT_ID,
        "client_name": "Анна",
    }
    base.update(override)
    return base


# ───────────────────────────────────────────────────────────────────────
# Lookup-chain fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def make_remote_proxy(tenant):
    """Factory — creates RemoteBookingProxy mirror row."""
    from datetime import datetime, timezone

    from apps.booking.models import RemoteBookingProxy

    def _factory(
        *,
        appointment_id=APPT_ID,
        specialist_id=MASTER_AYLA,
        service_id=None,
        start_at=None,
    ):
        return RemoteBookingProxy.all_tenants.create(
            appointment_id=appointment_id,
            tenant=tenant,
            specialist_id=specialist_id,
            service_id=service_id,
            start_at=start_at or datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
            status=RemoteBookingProxy.Status.CONFIRMED,
        )

    return _factory


@pytest.fixture
def make_master(tenant, make_bot_user):
    """Factory — creates CatalogMaster с optional linked BotUser."""
    from apps.catalog.models import CatalogMaster

    def _factory(
        *,
        ayla_user_id=MASTER_AYLA,
        linked=True,
        chat_id="max-master",
        name="Таня",
    ):
        from datetime import datetime, timezone as dt_tz

        master_bot_user = None
        if linked:
            master_bot_user = make_bot_user(
                ayla_user_id=ayla_user_id,
                chat_id=chat_id,
                display_name=name,
            )
        return CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_stable_external_id(ayla_user_id),
            external_updated_at=datetime.now(dt_tz.utc),
            name=name,
            ayla_user_id=ayla_user_id,
            linked_bot_user=master_bot_user,
            is_active=True,
        )

    return _factory


@pytest.fixture
def make_service(tenant):
    """Factory — creates CatalogService mirror."""
    from apps.catalog.models import CatalogService

    def _factory(*, ayla_service_id, name="Маникюр"):
        from datetime import datetime, timezone as dt_tz

        return CatalogService.all_tenants.create(
            tenant=tenant,
            slug=f"svc-{ayla_service_id}",
            external_id=_stable_external_id(ayla_service_id),
            external_updated_at=datetime.now(dt_tz.utc),
            name=name,
            ayla_service_id=ayla_service_id,
            is_active=True,
            duration_min=60,
        )

    return _factory


# ───────────────────────────────────────────────────────────────────────
# on_payment_failed_event — happy + edge paths
# ───────────────────────────────────────────────────────────────────────


class TestOnPaymentFailedEvent:
    def test_no_payment_id_logs_warning_and_returns(self, sent_dms, written_audits, caplog):
        """data без payment_id → ничего не делаем + WARNING."""
        from apps.skills.payment_failed import on_payment_failed_event

        with caplog.at_level(logging.WARNING, logger="apps.skills.payment_failed.skill"):
            on_payment_failed_event({})

        assert sent_dms == []
        assert written_audits == []
        assert any("payload_missing_payment_id" in r.message for r in caplog.records)


class TestMasterDMDispatch:
    """Sequence #4 active master DM dispatch (replaces α-mode skip).

    Phase D scenarios (per founder handoff 2026-05-26):
    1. Happy path full envelope → master DM sent
    2. Amount absent → line dropped, DM still sent
    3. RemoteBookingProxy missing → graceful skip + audit
    4. CatalogMaster missing → graceful skip + audit
    5. CatalogMaster.linked_bot_user NULL → graceful skip + audit
    6. send_message raises → caught, no batch break
    7. Existing client DM still works (no regression)

    Phase F adversarial:
    8. Cross-tenant lookup attempt → blocked by tenant scope
    """

    def test_happy_path_full_chain_dispatches_master_dm(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        make_master,
        make_service,
        sent_dms,
        written_audits,
    ):
        """Full chain: appointment_id → RemoteBookingProxy →
        CatalogMaster.ayla_user_id → linked_bot_user → BotUser.chat_id.
        Plus CatalogService enrichment for service_name."""
        from apps.skills.payment_failed import on_payment_failed_event

        # Client side
        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        # Master side
        service_id = uuid.uuid4()
        make_service(ayla_service_id=service_id, name="Маникюр")
        make_remote_proxy(specialist_id=MASTER_AYLA, service_id=service_id)
        make_master(ayla_user_id=MASTER_AYLA, chat_id="max-master")

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        # Two DMs — client + master.
        chat_ids = sorted(d["chat_id"] for d in sent_dms)
        assert chat_ids == ["max-client", "max-master"]

        master_dm = next(d for d in sent_dms if d["chat_id"] == "max-master")
        assert "⚠ Платёж не прошёл" in master_dm["text"]
        assert "Клиент: Анна" in master_dm["text"]
        assert "Маникюр" in master_dm["text"]
        # Counter line (N=3 default).
        assert "3-я попытка оплаты подряд" in master_dm["text"]
        assert master_dm["attachments"] is None  # info-only, no buttons

        # Forensic audit for successful dispatch.
        sent_audits = [a for a in written_audits if a["action"] == "payment_failed.master_dm_sent"]
        assert len(sent_audits) == 1

    def test_amount_line_dropped_when_missing(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        make_master,
        sent_dms,
    ):
        """Q3 verdict: Phase 1 envelope has no amount field, line dropped
        entirely (cleaner than rendering «—»)."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        make_remote_proxy(specialist_id=MASTER_AYLA)
        make_master(ayla_user_id=MASTER_AYLA, chat_id="max-master")

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        master_dm = next(d for d in sent_dms if d["chat_id"] == "max-master")
        # No «Сумма» line at all when amount missing.
        assert "Сумма:" not in master_dm["text"]
        # But the rest of the template is intact.
        assert "Клиент:" in master_dm["text"]
        assert "Услуга:" in master_dm["text"]
        assert "попытка оплаты подряд" in master_dm["text"]

    def test_skip_when_remote_booking_proxy_missing(
        self,
        tenant,
        make_bot_user,
        sent_dms,
        written_audits,
    ):
        """Mirror lag race: payment.failed arrived before booking.confirmed.
        No RemoteBookingProxy row → graceful skip + audit."""
        from apps.skills.payment_failed import on_payment_failed_event

        # Client side OK; no proxy / master fixtures.
        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        # Only client DM fires.
        chat_ids = [d["chat_id"] for d in sent_dms]
        assert chat_ids == ["max-client"]

        skip_audits = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped"
        ]
        assert len(skip_audits) == 1
        assert skip_audits[0]["payload"]["reason"] == "remote_booking_proxy_missing"

    def test_skip_when_catalog_master_missing(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        sent_dms,
        written_audits,
    ):
        """RemoteBookingProxy.specialist_id points к Ayla Master,
        но local CatalogMaster mirror hasn't synced yet (mirror lag)."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        make_remote_proxy(specialist_id=MASTER_AYLA)
        # No CatalogMaster fixture.

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        skip_audits = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped"
        ]
        assert len(skip_audits) == 1
        assert skip_audits[0]["payload"]["reason"] == "catalog_master_missing"

    def test_skip_when_linked_bot_user_null(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        make_master,
        sent_dms,
        written_audits,
    ):
        """Master существует в catalog но не onboarded к bot — no MAX DM
        possible. Graceful skip + audit."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        make_remote_proxy(specialist_id=MASTER_AYLA)
        # linked=False → no linked_bot_user on master row.
        make_master(ayla_user_id=MASTER_AYLA, linked=False)

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        skip_audits = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped"
        ]
        assert len(skip_audits) == 1
        assert skip_audits[0]["payload"]["reason"] == "master_not_onboarded"

    def test_send_message_raise_caught_no_batch_break(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        make_master,
        monkeypatch,
        caplog,
    ):
        """MAX API throws (5xx, timeout) — skill logs but не raises.
        Иначе Gamma's consumer fail-нул бы dedupe + переобработал."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        make_remote_proxy(specialist_id=MASTER_AYLA)
        make_master(ayla_user_id=MASTER_AYLA, chat_id="max-master")

        def _throws(**_kwargs):
            raise RuntimeError("MAX 503")

        monkeypatch.setattr("apps.channels.max.outbound.send_message", _throws)
        with caplog.at_level(logging.ERROR, logger="apps.skills.payment_failed.skill"):
            on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        # Both DMs attempted, both failed, neither raised.
        assert any("dm_send_failed" in r.message for r in caplog.records)

    def test_client_dm_still_sent_when_master_lookup_fails(
        self,
        tenant,
        make_bot_user,
        sent_dms,
    ):
        """Regression guard: master DM skip MUST NOT block client DM.
        Independent code paths."""
        from apps.skills.payment_failed import on_payment_failed_event

        # Only client side; nothing for master lookup chain.
        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant.pk)))

        # Client DM с inline button still arrives.
        assert len(sent_dms) == 1
        client_dm = sent_dms[0]
        assert client_dm["chat_id"] == "max-client"
        buttons = client_dm["attachments"][0]["payload"]["buttons"]
        assert buttons[0]["callback"] == f"cb:payment:retry:{PAYMENT_ID}"

    def test_non_numeric_consecutive_failures_does_not_break_dispatch(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        make_master,
        sent_dms,
        caplog,
    ):
        """CR #881 M1 regression guard: string-shaped or garbage value
        в ``consecutive_failures`` must NOT raise + escape skill (would
        break Gamma's consumer batch). Defensive int-coerce с sane
        default = threshold (3)."""
        from apps.skills.payment_failed import on_payment_failed_event

        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        make_remote_proxy(specialist_id=MASTER_AYLA)
        make_master(ayla_user_id=MASTER_AYLA, chat_id="max-master")

        # Garbage non-numeric value — would raise ValueError pre-fix.
        with caplog.at_level(logging.WARNING, logger="apps.skills.payment_failed.skill"):
            on_payment_failed_event(
                _enriched_data(
                    tenant_id_override=str(tenant.pk),
                    consecutive_failures="not_a_number",
                )
            )

        # No raise — master DM still fires.
        master_dm = next(d for d in sent_dms if d["chat_id"] == "max-master")
        # Falls back to default 3 in template.
        assert "Это 3-я попытка оплаты подряд" in master_dm["text"]
        # Defensive warn logged.
        assert any("bad_consecutive_failures" in r.message for r in caplog.records)

    def test_cross_tenant_lookup_blocked_by_tenant_scope(
        self,
        tenant,
        make_bot_user,
        make_remote_proxy,
        make_master,
        sent_dms,
        written_audits,
    ):
        """Adversarial: RemoteBookingProxy + CatalogMaster в tenant A;
        payment event arrives с tenant_id of tenant B. Query MUST NOT
        return tenant A's rows. Phase F #1 защита."""
        from apps.tenancy.models import Tenant
        from apps.skills.payment_failed import on_payment_failed_event

        # Tenant A — has full chain fixtures.
        make_bot_user(ayla_user_id=CLIENT_AYLA, chat_id="max-client")
        make_remote_proxy(specialist_id=MASTER_AYLA)
        make_master(ayla_user_id=MASTER_AYLA, chat_id="max-master")

        # Tenant B — separate; payload references it though APPT_ID
        # «belongs» к tenant A's proxy. With tenant-scoping, query
        # finds nothing.
        tenant_b = Tenant.objects.create(slug="other", name="Other Salon")

        on_payment_failed_event(_enriched_data(tenant_id_override=str(tenant_b.pk)))

        # Master DM MUST NOT fire — cross-tenant query blocked.
        chat_ids = [d["chat_id"] for d in sent_dms]
        assert "max-master" not in chat_ids
        skip_audits = [
            a for a in written_audits if a["action"] == "payment_failed.master_dm_skipped"
        ]
        # Skip с reason=remote_booking_proxy_missing (query failed
        # cross-tenant predicate). Crucially: NOT sent.
        assert len(skip_audits) == 1
        assert skip_audits[0]["payload"]["reason"] == "remote_booking_proxy_missing"


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
