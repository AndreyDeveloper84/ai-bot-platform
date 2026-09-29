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


@pytest.fixture
def mark_welcomed():
    """Return a callable that pre-marks a BotUser as welcomed (inside tenant_scope)."""

    def _mark(*, user_id, chat_id, food_consent: bool = False):
        from django.utils import timezone

        from apps.identity.services import resolve_or_create_bot_user

        bu = resolve_or_create_bot_user(
            channel="max", channel_user_id=str(user_id), chat_id=str(chat_id)
        )
        bu.welcomed_at = timezone.now()
        bu.save(update_fields=["welcomed_at"])
        if food_consent:
            # DRF-1948: сканер пишет в дневник только при PERSONAL_DATA — согласие
            # сканера стоит поверх него. Без этой строки фото-тесты проверяли бы
            # отказ PERSONAL_DATA, а не то, ради чего написаны.
            from apps.consent.nutrition import DIARY, FOOD_DIARY_CONSENT_DOCUMENT_VERSION
            from apps.consent.services import record_global_consent

            record_global_consent(bu, source="test:mark_welcomed")
            # DRF-1963 (M1): согласие дневника/сканера — строка реестра.
            record_global_consent(
                bu,
                consent_type=DIARY,
                source="test:mark_welcomed",
                document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
            )
        return bu

    return _mark
