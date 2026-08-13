"""Consent revoke does not depend on the Ayla link (DRF-1035, §15 rows 11-12).

The DRF-1035 investigation initially flagged «revoke returns 0 and looks like a
success» as a live legal risk. Checking the call sites showed it is not: the
production revoke path (``privacy.py`` step 3) is keyed on the ``bot_user`` FK
and was deliberately decoupled from ``ayla_user_id`` by the DRF-956 / T-05
ruling. The uuid-keyed :func:`withdraw_personal_data` has no production caller.

So there was nothing to fix — but there IS something to lock, because the
obvious «tidy-up» during this work would have been to route revoke through the
now-populated ``ayla_user_id``. That would reintroduce the exact failure mode
for anyone still unlinked. These tests are the regression lock.
"""

from __future__ import annotations

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import (
    has_global_consent,
    record_global_consent,
    withdraw_personal_data,
    withdraw_personal_data_for_bot_users,
)
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user

pytestmark = pytest.mark.django_db


PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value


@pytest.fixture
def unlinked_user(settings) -> BotUser:
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="drf1035-cons")
    assert bot_user.ayla_user_id is None
    return bot_user


def test_consent_can_be_granted_without_an_ayla_link(unlinked_user: BotUser) -> None:
    # §15.11 — grant is keyed on bot_user, so it never needed the UUID.
    record_global_consent(unlinked_user, source="welcome")
    assert has_global_consent(unlinked_user, PERSONAL_DATA) is True


def test_revoke_actually_revokes_for_an_unlinked_user(unlinked_user: BotUser) -> None:
    """§15.12 — the important one: a real withdrawal, not a silent 0."""
    record_global_consent(unlinked_user, source="welcome")

    withdrawn = withdraw_personal_data_for_bot_users(
        BotUser.all_tenants.filter(id=unlinked_user.id), source="test"
    )

    assert withdrawn > 0
    assert has_global_consent(unlinked_user, PERSONAL_DATA) is False


def test_uuid_keyed_revoke_is_a_no_op_when_unlinked(unlinked_user: BotUser) -> None:
    """Documents the trap rather than pretending it does not exist.

    ``withdraw_personal_data(None)`` returns 0 having withdrawn nothing. That
    is safe today ONLY because nothing in production calls it; the function is
    slated for removal (follow-up). Should someone wire it up, this test says
    plainly what they would be shipping.
    """
    record_global_consent(unlinked_user, source="welcome")

    assert withdraw_personal_data(unlinked_user.ayla_user_id, source="test") == 0
    # Consent is still standing — the "successful" revoke did nothing.
    assert has_global_consent(unlinked_user, PERSONAL_DATA) is True
