"""Tests for the tenant-less global MAX path (#1019 / EPIC #1014).

Covers the GlobalMaxHandler (requires_tenant=False) + handle_global_max_event,
and the headline acceptance invariant: any commercial-model read attempted on
the tenant-less discovery path raises CrossTenantError in strict mode.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.exceptions import CrossTenantError

pytestmark = pytest.mark.django_db


def _payload(*, text: str = "Привет", user_id: int = 7777, chat_id: int = 8888) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": "m-1", "seq": 1, "text": text, "attachments": []},
        },
    }


def _raw_entry(payload: dict) -> dict:
    return {
        "data": json.dumps(payload),
        "trace_id": str(uuid.uuid4()),
        "resolved_tenant_id": "",  # tenant-less by design
    }


def test_global_handler_resolves_under_sentinel_no_tenant_required(settings) -> None:
    """requires_tenant=False → no TenantRequiredButMissing even with strict refuse;
    identity resolved under the global_bot sentinel at current_tenant()=None.
    """
    # Even with the strict-refuse flag ON, the global handler must not refuse —
    # discovery is tenant-less by design.
    settings.STRICT_TENANT_REFUSE = True

    GlobalMaxHandler()(_raw_entry(_payload(user_id=7777, chat_id=8888)))

    bot_user = BotUser.all_tenants.get(channel="max", channel_user_id="7777")
    assert bot_user.tenant.slug == GLOBAL_BOT_TENANT_SLUG
    # The handler must not have leaked a tenant scope.
    assert current_tenant() is None


def test_global_handler_skips_unsupported_update_type(settings) -> None:
    settings.STRICT_TENANT_REFUSE = True
    # An unsupported lifecycle update must be tolerated (no raise, no PEL storm).
    GlobalMaxHandler()(_raw_entry({"update_type": "bot_started"}))
    assert BotUser.all_tenants.filter(channel="max").count() == 0


def test_commercial_read_before_booking_raises_crosstenanterror(settings) -> None:
    """Acceptance invariant guard: a commercial TenantScoped read at
    ``current_tenant()=None`` (strict) raises CrossTenantError.

    This guards the *manager* invariant the global handler relies on — the
    handler itself (``handle_global_max_event``) deliberately performs NO
    commercial reads, so this proves the safety net is armed for the discovery
    context, not that the handler trips it.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    from apps.booking.models import BookingRequest

    with tenant_scope(None):
        assert current_tenant() is None
        with pytest.raises(CrossTenantError):
            list(BookingRequest.objects.all())
