"""DRF-2697 — persistent memory reaches the concierge only under a live consent.

STORE != PERMISSION TO USE. One and the same green fact sits in storage in
every case below; what differs is the consent state AT THE MOMENT OF USE, and
with it what the concierge is handed for the prompt.

Driven end-to-end through the tenant-less global MAX handler and observed at
the seam into the concierge: ``extra_system`` carries the
``render_current_personal_context`` paragraph. Two gates stand on this path —
the handler's own (``ayla_user_id`` is set only under a live consent) and the
reader's (DRF-2697) — and these nodes hold with EITHER one removed; only with
both gone does the fact reach the prompt. The reader's gate alone is pinned in
``apps/persona/tests/test_memory_surface_consent_2697.py``. The valid-consent
case is the positive control: without it «nothing reached the prompt» would
also be true of a dead surface.

The sibling surface, ``memory_block``, gates inside its own builder and is
covered in ``apps/orchestrator/tests/test_memory_block.py``. Fixtures are
synthetic — no personal data.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.consent.services import record_global_consent, withdraw_personal_data_for_bot_users
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

#: The one synthetic fact every case stores, and the word it renders as.
FACT = {"key": "diet", "value": "vegan"}
FACT_WORD = "веган"


def _payload(*, text: str, mid: str, user_id: int, chat_id: int) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Тест"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _entry(payload: dict) -> dict:
    return {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}


@pytest.fixture(autouse=True)
def _harness(monkeypatch, settings):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    settings.STRICT_TENANT_SCOPE = "strict"
    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    monkeypatch.setattr(
        max_handler,
        "send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: {"ok": True},
    )
    # Post-reply intent resolution calls the LLM provider; not this node's subject.
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())


@pytest.fixture
def concierge_input(monkeypatch):
    """What the concierge was handed for the prompt, one dict per turn."""
    from apps.orchestrator.discovery import DiscoveryReply

    seen: list[dict] = []

    def _capture(*args, **kwargs):
        seen.append({"extra_system": kwargs.get("extra_system")})
        return DiscoveryReply(text="__CONCIERGE__")

    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", _capture)
    return seen


def _person_with_the_fact(user_id: int):
    ayla_uid = uuid.uuid4()
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), ayla_user_id=ayla_uid
    )
    upc = UserPersonalContext.objects.create(user_id=ayla_uid)
    MemoryEntry.objects.create(
        user_id=ayla_uid,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content=dict(FACT),
    )
    return bot_user, ayla_uid


def _turn(user_id: int, mid: str) -> None:
    GlobalMaxHandler()(
        _entry(_payload(text="подбери мне что-нибудь", mid=mid, user_id=user_id, chat_id=user_id))
    )


def _fact_is_stored(ayla_uid: uuid.UUID) -> bool:
    return MemoryEntry.objects.filter(
        user_id=ayla_uid,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        soft_deleted_at__isnull=True,
    ).exists()


class TestTheSameFactUnderDifferentConsent:
    def test_a_valid_consent_lets_the_fact_reach_the_prompt(self, concierge_input):
        bot_user, _ = _person_with_the_fact(26971)
        record_global_consent(bot_user, source="welcome")

        _turn(26971, "v1")

        (handed,) = concierge_input
        assert FACT_WORD in handed["extra_system"].lower()

    def test_an_absent_consent_keeps_the_fact_out(self, concierge_input):
        _, ayla_uid = _person_with_the_fact(26972)

        _turn(26972, "a1")

        (handed,) = concierge_input
        assert handed["extra_system"] == ""
        assert _fact_is_stored(ayla_uid)

    def test_a_revoked_consent_keeps_the_fact_out_while_it_stays_in_storage(self, concierge_input):
        bot_user, ayla_uid = _person_with_the_fact(26973)
        record_global_consent(bot_user, source="welcome")
        _turn(26973, "r1")
        assert FACT_WORD in concierge_input[0]["extra_system"].lower()

        assert withdraw_personal_data_for_bot_users([bot_user], source="test") >= 1
        _turn(26973, "r2")

        assert concierge_input[1]["extra_system"] == ""
        assert _fact_is_stored(ayla_uid)  # retention is not this gate's business

    def test_a_consent_that_could_not_be_checked_keeps_the_fact_out(
        self, concierge_input, monkeypatch
    ):
        bot_user, ayla_uid = _person_with_the_fact(26974)
        record_global_consent(bot_user, source="welcome")

        def _unavailable(*args, **kwargs):
            raise RuntimeError("consent registry unavailable")

        monkeypatch.setattr("apps.consent.memory.has_global_consent", _unavailable)
        _turn(26974, "u1")

        (handed,) = concierge_input
        assert handed["extra_system"] == ""
        assert _fact_is_stored(ayla_uid)
