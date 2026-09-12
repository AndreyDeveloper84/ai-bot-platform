"""Operator-assisted identity linking — Phase 0 (§6 пакета владельца 12.09).

Identity-токен боту не выдаётся. Регистрация → PENDING с аудит-пакетом →
контролируемое действие оператора → LINKED (provenance OPERATOR_VERIFIED,
operator, время) или REJECTED (таксономия, безопасное сообщение человеку).
Публикация требует LINKED — через тот же `sale_block`, что и раньше.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.audit.models import AuditLog
from apps.identity.models import BotUser, SoloIdentityLink
from apps.identity.services import solo_identity_link as svc
from apps.identity.services.solo_onboarding import SoloSetupState, create_solo_provider

pytestmark = pytest.mark.django_db


@pytest.fixture
def result(db):
    return create_solo_provider(channel="max", channel_user_id="solo-s6-1", display_name="Ольга")


@pytest.fixture
def bot_user(result):
    return BotUser.all_tenants.get(tenant=result.tenant, channel="max", channel_user_id="solo-s6-1")


@pytest.fixture
def operator(db):
    return get_user_model().objects.create_user(username="operator-s6", password="x")  # noqa: S106


class _Identity:
    def __init__(self, ayla_user_id, is_proxy):
        self.ayla_user_id = ayla_user_id
        self.is_proxy = is_proxy


def _patch_resolver(monkeypatch, *, returns=None, raises=None):
    from apps.integrations.ayla import identity_client

    def _fake(external_user_id):
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(identity_client, "resolve_identity", _fake)


class TestRegistrationOpensPendingWithTheAuditPackage:
    def test_open_link_is_pending_and_carries_the_package(self, result, bot_user):
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)

        assert link.status == SoloIdentityLink.Status.PENDING
        assert link.provenance == ""
        assert link.solo_registration_id == result.tenant.id
        assert link.channel == "max"
        assert link.channel_user_id == "solo-s6-1"
        assert link.tenant_id_snapshot == result.tenant.id
        assert link.requested_at is not None
        row = AuditLog.all_tenants.filter(action="identity.solo_link.pending").first()
        assert row is not None
        assert row.payload["master_id"] == str(result.master.pk)
        assert row.payload["channel"] == "max"
        # Телефона нет — сказано флагом, не пустой строкой в аудите.
        assert row.payload["phone_present"] is False

    def test_open_link_is_idempotent(self, result, bot_user):
        first = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        second = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        assert first.pk == second.pk
        assert AuditLog.all_tenants.filter(action="identity.solo_link.pending").count() == 1

    def test_pending_is_not_ready(self, result, bot_user):
        """§6: IDENTITY_LINK_PENDING — не полный успех; публикации нет."""
        svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        assert result.setup_state is SoloSetupState.SETUP_PENDING
        assert result.blocked_by == "ayla_unlinked"


class TestOperatorConfirmsThroughTheCatalogAnswer:
    def test_proxy_answer_keeps_pending_and_names_why(
        self, monkeypatch, result, bot_user, operator
    ):
        """Оператор нажал раньше, чем связал в каталоге: остаётся PENDING,
        причина названа — proxy_identity."""
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        _patch_resolver(monkeypatch, returns=_Identity(uuid.uuid4(), is_proxy=True))

        outcome = svc.confirm_by_operator(link, bot_user=bot_user, operator=operator)

        assert outcome.linked is False
        assert outcome.refusal == "proxy_identity"
        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.PENDING
        assert link.last_attempt_refusal == "proxy_identity"
        assert link.operator_id is None

    def test_real_answer_links_with_operator_provenance(
        self, monkeypatch, result, bot_user, operator
    ):
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        key = uuid.uuid4()
        _patch_resolver(monkeypatch, returns=_Identity(key, is_proxy=False))

        outcome = svc.confirm_by_operator(link, bot_user=bot_user, operator=operator)

        assert outcome.linked is True
        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.LINKED
        assert link.provenance == SoloIdentityLink.Provenance.OPERATOR_VERIFIED
        assert link.operator_id == operator.pk
        assert link.operator_username == "operator-s6"
        assert link.decided_at is not None
        assert link.ayla_user_id == key
        result.master.refresh_from_db()
        assert result.master.ayla_user_id == key
        # Публикация: тот же sale_block, что и раньше, теперь открыт.
        assert result.setup_state is SoloSetupState.READY
        audit = AuditLog.all_tenants.filter(action="identity.solo_link.linked").first()
        assert audit is not None
        assert audit.payload["provenance"] == "OPERATOR_VERIFIED"
        assert audit.payload["operator"] == "operator-s6"

    def test_the_bot_never_writes_a_proxy_key_even_for_the_operator(
        self, monkeypatch, result, bot_user, operator
    ):
        """Положительная стража сторожа: действие оператора не обходит
        единственную дверь — прокси-ключ в карточку не попадает."""
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        _patch_resolver(monkeypatch, returns=_Identity(uuid.uuid4(), is_proxy=True))

        svc.confirm_by_operator(link, bot_user=bot_user, operator=operator)

        result.master.refresh_from_db()
        assert result.master.ayla_user_id is None


class TestOperatorRejectsWithATaxonomyAndASafeMessage:
    def test_reject_records_reason_operator_and_audit(self, result, bot_user, operator):
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)

        svc.reject_by_operator(
            link, operator=operator, reason="not_a_master", note="салон, не мастер"
        )

        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.REJECTED
        assert link.reject_reason == "not_a_master"
        assert link.operator_id == operator.pk
        assert link.decided_at is not None
        assert AuditLog.all_tenants.filter(action="identity.solo_link.rejected").exists()
        # Отклонённый не связан — и не продаётся.
        assert result.setup_state is SoloSetupState.SETUP_PENDING

    def test_reject_reason_must_be_from_the_taxonomy(self, result, bot_user, operator):
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        with pytest.raises(ValueError):
            svc.reject_by_operator(link, operator=operator, reason="потому что")
        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.PENDING

    def test_confirm_after_reject_does_not_link(self, monkeypatch, result, bot_user, operator):
        link = svc.open_link(result.master, bot_user=bot_user, tenant=result.tenant)
        svc.reject_by_operator(link, operator=operator, reason="fraud_suspected")
        _patch_resolver(monkeypatch, returns=_Identity(uuid.uuid4(), is_proxy=False))

        outcome = svc.confirm_by_operator(link, bot_user=bot_user, operator=operator)

        assert outcome.linked is False
        assert outcome.refusal == "rejected"
        result.master.refresh_from_db()
        assert result.master.ayla_user_id is None

    def test_the_recovery_text_names_no_reason(self):
        low = svc.REJECTED_RECOVERY_TEXT.lower()
        assert "поддержк" in low
        for word in ("мошен", "злоупотреб", "не мастер", "дубл", "not_a_master", "fraud"):
            assert word not in low, word
