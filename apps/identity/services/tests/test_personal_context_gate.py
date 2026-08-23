"""The memory_green gate on the personal-context service (DRF-1311).

``BLOCKED_CONSENT`` is one status covering two very different worlds — «this
user has no Ayla identity yet» and «this user has an identity but never
granted memory_green». On 23.08 the pilot log showed five
``identity.personal_context.gate_closed`` lines and the reason had to be
reconstructed from the database; these tests pin the reason into the line.

No HTTP client is constructed on either path under test: the gate
short-circuits before the wire, so nothing here needs a client double.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.identity.services.personal_context import (
    GateStatus,
    get_declared_prefs,
    patch_declared_prefs,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CT = ConsentRecord.ConsentType


def _bot_user(slug: str, *, ayla_user_id=None) -> BotUser:
    tenant = Tenant.objects.create(slug=slug, name=slug)
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=slug, ayla_user_id=ayla_user_id
    )


def _grant(bu: BotUser, ctype: str) -> None:
    ConsentRecord.all_tenants.create(
        tenant=bu.tenant, bot_user=bu, consent_type=ctype, granted=True, source="test"
    )


def test_unlinked_user_is_blocked_and_says_so(caplog) -> None:
    bu = _bot_user("pcg-unlinked")  # no ayla_user_id
    with caplog.at_level(logging.INFO, logger="apps.identity.services.personal_context"):
        result = get_declared_prefs(bu)
    assert result.status is GateStatus.BLOCKED_CONSENT
    assert "reason=unlinked" in caplog.text


def test_linked_without_memory_green_is_blocked_and_says_so(caplog) -> None:
    """The 23.08 pilot state, exactly: linked, personal_data granted, no green."""
    bu = _bot_user("pcg-nogreen", ayla_user_id=uuid.uuid4())
    _grant(bu, CT.PERSONAL_DATA)
    with caplog.at_level(logging.INFO, logger="apps.identity.services.personal_context"):
        result = get_declared_prefs(bu)
    assert result.status is GateStatus.BLOCKED_CONSENT
    assert "reason=no_memory_green" in caplog.text
    assert "reason=unlinked" not in caplog.text


def test_personal_data_alone_does_not_open_the_read_gate() -> None:
    """The two bases are separate BY DESIGN — this is the contract, not the bug.

    The bug was that nothing ever granted the second one (fixed at the
    welcome S2 tap + migration consent/0003). Should anyone later be tempted
    to «unify» the gates by pointing the read side at PERSONAL_DATA, this
    test says that is a Decision-Log call, not a refactor.
    """
    bu = _bot_user("pcg-bases", ayla_user_id=uuid.uuid4())
    _grant(bu, CT.PERSONAL_DATA)
    assert patch_declared_prefs(bu, [{"field": "diet_type", "value": "vegan"}]).status is (
        GateStatus.BLOCKED_CONSENT
    )


def test_memory_green_opens_the_gate_past_the_consent_check() -> None:
    """With memory_green granted the gate stops short-circuiting.

    Asserted on ``_gate`` itself rather than through a stubbed HTTP client:
    a hand-rolled client double would only prove that the double was called,
    and a double that drifts from the real client is how DRF-1310 happened.
    ``_gate`` IS the enforcement point this module exists for.
    """
    from apps.identity.services.personal_context import _gate

    bu = _bot_user("pcg-green", ayla_user_id=uuid.uuid4())
    _grant(bu, CT.PERSONAL_DATA)
    assert _gate(bu) is None  # personal_data alone: still shut

    _grant(bu, CT.MEMORY_GREEN)
    assert _gate(bu) == bu.ayla_user_id  # open, and it hands back the subject
