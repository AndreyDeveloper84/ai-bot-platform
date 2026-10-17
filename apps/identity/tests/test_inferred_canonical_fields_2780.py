"""DRF-2780 — умная память Ф4a-2: выводимая запись несёт канонические поля.

Решение владельца 05.10.2026: «каждое предположение — источник, дата, статус
подтверждения, срок актуальности; предположение НЕ становится фактом без
подтверждения». До этого листа писатель штамповал канонические поля только
у ``explicit``; у ``inferred`` они оставались NULL — без срока, без статуса,
без основания.

* i1 — статус ``active``, но ``provenance`` NULL: предположение, не факт;
* i2 — срок неподтверждённого — 30 дней от записи (литералом), один момент
  на ``effective_from`` / ``updated_at`` / базу срока;
* i3 — меньший срок вызывающего сильнее 30 дней; больший — нет;
* i4 — основание записи — согласие на предположения;
* i5 — источник хранится так, как дан, и не выдумывается, когда не дан;
  событийный ключ писатель не пишет вовсе (ноль держит сторож 2513);
* i6 — ``explicit`` прежний: ``user_stated``, своё основание не получает.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_writer import write_entry

pytestmark = pytest.mark.django_db


def _upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _inferred(upc, **extra) -> MemoryEntry:
    entry = write_entry(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_INFERRED,
        kind="preference",
        content={"key": "visit_time", "value": "after_18"},
        request_id=uuid.uuid4(),
        purpose="test:2780",
        last_inferred_at=timezone.now(),
        **extra,
    )
    assert entry is not None
    entry.refresh_from_db()
    return entry


def test_i1_an_inference_is_active_but_not_a_fact() -> None:
    entry = _inferred(_upc())

    assert entry.status == MemoryEntry.STATUS_ACTIVE
    assert entry.provenance is None
    assert entry.source == MemoryEntry.SOURCE_INFERRED


def test_i2_the_unconfirmed_term_is_thirty_days_from_one_write_instant() -> None:
    entry = _inferred(_upc())

    assert entry.effective_from is not None
    assert entry.updated_at == entry.effective_from
    assert entry.expires_at == entry.effective_from + timedelta(days=30)


@pytest.mark.parametrize(("ttl_days", "term"), [(7, 7), (90, 30)])
def test_i3_a_shorter_caller_term_wins_a_longer_one_does_not(ttl_days: int, term: int) -> None:
    entry = _inferred(_upc(), ttl_days=ttl_days)

    assert entry.effective_from is not None
    assert entry.expires_at == entry.effective_from + timedelta(days=term)


def test_i4_the_write_is_authorised_by_the_preference_inference_consent() -> None:
    entry = _inferred(_upc())

    assert entry.consent_scope == "preference_inference"


def test_i5_the_source_is_stored_as_given_and_never_fabricated() -> None:
    given = _inferred(
        _upc(),
        derivation_method="repeated_choice",
        evidence_refs=["booking:a", "booking:b"],
    )
    assert given.derivation_method == "repeated_choice"
    assert given.evidence_refs == ["booking:a", "booking:b"]
    assert given.source_event_id is None

    bare = _inferred(_upc())
    assert bare.derivation_method is None
    assert bare.evidence_refs == []


def test_i6_explicit_writes_are_unchanged() -> None:
    upc = _upc()
    entry = write_entry(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        kind="preference",
        content={"key": "visit_time", "value": "after_18"},
        request_id=uuid.uuid4(),
        purpose="test:2780",
    )
    assert entry is not None
    entry.refresh_from_db()

    assert entry.provenance == MemoryEntry.PROVENANCE_USER_STATED
    assert entry.status == MemoryEntry.STATUS_ACTIVE
    assert entry.expires_at is None  # green без ttl_days — без срока, как было
    assert entry.consent_scope is None
