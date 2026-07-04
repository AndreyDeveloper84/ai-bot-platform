"""Tests для глобального memory-consent (MEMORY_CONSENT_SPEC, B3).

Память глобальна на человека (ayla_user_id) → согласие проверяется
кросс-тенантно. Один grant в любом тенанте покрывает человека везде.
"""

from __future__ import annotations

import uuid

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import has_memory_consent, withdraw_personal_data
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CT = ConsentRecord.ConsentType


@pytest.fixture
def two_tenants() -> tuple[Tenant, Tenant]:
    return (
        Tenant.objects.create(slug="mem-a", name="A"),
        Tenant.objects.create(slug="mem-b", name="B"),
    )


def _bot_user(tenant: Tenant, ayla_user_id, cuid: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=cuid, ayla_user_id=ayla_user_id
    )


def _grant(bu: BotUser, ctype: str, *, version: str = "") -> None:
    ConsentRecord.all_tenants.create(
        tenant=bu.tenant,
        bot_user=bu,
        consent_type=ctype,
        granted=True,
        source="test",
        document_version=version,
    )


def test_green_consent_granted_is_active(two_tenants) -> None:
    a, _ = two_tenants
    uid = uuid.uuid4()
    bu = _bot_user(a, uid, "a1")
    _grant(bu, CT.MEMORY_GREEN)
    assert has_memory_consent(uid, "green") is True


def test_no_consent_returns_false(two_tenants) -> None:
    a, _ = two_tenants
    uid = uuid.uuid4()
    _bot_user(a, uid, "a1")  # существует, но без grant
    assert has_memory_consent(uid, "green") is False


def test_consent_is_global_across_tenants(two_tenants) -> None:
    """Grant в тенанте A → активно даже если у человека есть BotUser в B без grant."""
    a, b = two_tenants
    uid = uuid.uuid4()
    bu_a = _bot_user(a, uid, "a1")
    _bot_user(b, uid, "b1")  # тот же человек, другой тенант, без grant
    _grant(bu_a, CT.MEMORY_GREEN)
    # Глобально — активно (grant в любом тенанте покрывает человека).
    assert has_memory_consent(uid, "green") is True


def test_unknown_uid_returns_false() -> None:
    assert has_memory_consent(uuid.uuid4(), "green") is False


def test_withdraw_personal_data_cascades_to_memory(two_tenants) -> None:
    a, _ = two_tenants
    uid = uuid.uuid4()
    bu = _bot_user(a, uid, "a1")
    _grant(bu, CT.PERSONAL_DATA)
    _grant(bu, CT.MEMORY_GREEN)
    assert has_memory_consent(uid, "green") is True

    withdrawn = withdraw_personal_data(uid, source="test:exit")
    assert withdrawn >= 2  # personal_data + memory_green
    assert has_memory_consent(uid, "green") is False


def test_document_version_bump_forces_reprompt(two_tenants) -> None:
    a, _ = two_tenants
    uid = uuid.uuid4()
    bu = _bot_user(a, uid, "a1")
    _grant(bu, CT.MEMORY_GREEN, version="privacy-v1")
    assert has_memory_consent(uid, "green", document_version="privacy-v1") is True
    # Политика памяти обновилась → старое согласие неактуально.
    assert has_memory_consent(uid, "green", document_version="privacy-v2") is False


def test_unknown_zone_raises() -> None:
    with pytest.raises(ValueError):
        has_memory_consent(uuid.uuid4(), "purple")
