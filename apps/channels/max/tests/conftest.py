"""Shared fixtures for the MAX handler test suite.

`mark_welcomed` isolates handler tests from WelcomeSkill's task-#85
auto-trigger, which greets the FIRST message from any BotUser with
``welcomed_at IS NULL`` and thereby intercepts every skill below it (echo,
food_scanner, human-handoff, …). Tests that exercise a POST-welcome skill path
call this up front so the intended skill actually runs — an isolation shim, not
a behaviour change. Optionally stamps the food_scanner feature-consent too.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_auto_draft_enqueue(monkeypatch):
    """Stub the auto-draft Celery enqueue so handler tests need no broker.

    ``record_message`` (apps/conversations/services) enqueues
    ``auto_generate_draft_for_inbound.delay(...)`` for every USER message via
    ``transaction.on_commit``. Under ``django_db(transaction=True)`` (the skills
    tests) that callback actually fires → a Celery ``.delay()`` publish to a
    broker that isn't running in tests (kombu ConnectionRefused). These handler
    tests don't exercise the auto-draft path, so stub the enqueue to a no-op.
    (Non-transactional tests roll the on_commit back, so this is a no-op there.)
    """
    from apps.master_api import tasks as _mt

    monkeypatch.setattr(_mt.auto_generate_draft_for_inbound, "delay", lambda **kw: None)


@pytest.fixture
def mark_welcomed():
    """Return a callable that pre-marks a BotUser as welcomed (inside tenant_scope)."""

    def _mark(*, user_id, chat_id, food_consent: bool = False):
        from django.utils import timezone

        from apps.identity.services import resolve_or_create_bot_user

        bu = resolve_or_create_bot_user(
            channel="max", channel_user_id=str(user_id), chat_id=str(chat_id)
        )
        now = timezone.now()
        bu.welcomed_at = now
        fields = ["welcomed_at"]
        if food_consent:
            bu.food_scanner_consent_at = now
            fields.append("food_scanner_consent_at")
        bu.save(update_fields=fields)
        return bu

    return _mark
