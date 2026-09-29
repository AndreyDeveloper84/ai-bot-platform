"""Seed a LEGACY ``HEALTH`` consent row in tests (DRF-2100).

Production no longer writes ``ConsentType.HEALTH`` — the profile handle
grants the diary consent ``food-diary-v1`` instead, and
``apps/consent/tests/test_health_writers_census_2100.py`` keeps «HEALTH
writers outside tests = 0». Rows granted before 18.09.2026 still exist and
still open the nutrition surfaces (owner ruling §48 п.8б: «сохранить
совместимость со старыми»), so tests need a way to put such a row under a
person. This is it — the one place under ``tests/`` that writes the type,
through the same person-level primitive the old path used.
"""

from __future__ import annotations

from typing import Any

from apps.consent.health import GRANT_SOURCE, HEALTH_CONSENT_DOCUMENT_VERSION
from apps.consent.models import ConsentRecord
from apps.consent.services import record_person_consent

LEGACY_SOURCE = f"test:legacy:{GRANT_SOURCE}"


def seed_legacy_health(
    bot_user: Any,
    *,
    document_version: str = HEALTH_CONSENT_DOCUMENT_VERSION,
    source: str = LEGACY_SOURCE,
) -> int:
    """Grant a legacy HEALTH row to every shell of this person; returns rows written."""

    return record_person_consent(
        bot_user,
        consent_type=ConsentRecord.ConsentType.HEALTH.value,
        source=source,
        document_version=document_version,
    )


__all__ = ["LEGACY_SOURCE", "seed_legacy_health"]
