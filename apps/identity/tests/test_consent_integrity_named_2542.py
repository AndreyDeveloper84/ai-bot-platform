"""DRF-2542 §2 — отказ базы по согласию называется там, где рождается.

В день снятия заглушки #597 единственной защитой жёлтой и красной зоны от
записи без согласия останется CHECK ``memory_entry_yellow_red_requires_consent``
(миграция 0007). Его ``IntegrityError`` раньше уходил из ``write_entry`` безымянным,
а у всех вызывающих широкий ``except Exception`` («память не ломает ход») —
тихий пропуск вернулся бы, но уже без намерения.

Теперь ``write_entry`` ловит именно это нарушение, пишет durable-строку аудита
``write_rejected_no_consent`` (тем же приёмом, что и отказ по возрасту) и
возвращает ``None``. Узлы:

* продуктовый путь записи (``write_entry``, заглушка подменена на «взрослый»):
  жёлтая/красная без согласия → база отказала → отказ назван;
* широкий ``except`` вызывающего отказ НЕ проглатывает: след уже записан;
* любое ДРУГОЕ нарушение целостности пробрасывается, как было.

Только Postgres: CHECK создаётся ``RunSQL`` и на SQLite его нет — как у
``test_memory_entry_constraints.py``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services import memory_writer
from apps.identity.services.memory_writer import write_entry

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="CHECK memory_entry_yellow_red_requires_consent существует только в Postgres (RunSQL, 0007)",
    ),
]

NO_CONSENT = RedZoneAccessLog.ACCESS_WRITE_REJECTED_NO_CONSENT


def _adult():
    """Подмена #597: возраст подтверждён — до базы доходит сама запись."""
    return patch.object(memory_writer, "_check_minor_protection", return_value=None)


def _rejections() -> int:
    return RedZoneAccessLog.objects.filter(access_type=NO_CONSENT).count()


def _write(upc: UserPersonalContext, zone: str, **overrides):
    kwargs: dict[str, Any] = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        source=MemoryEntry.SOURCE_EXPLICIT,
        kind="lifestyle",
        content={"probe": "DRF-2542 §2"},
        request_id=uuid.uuid4(),
        purpose="DRF-2542 §2: запись без согласия",
        consent_at=None,
    )
    kwargs.update(overrides)
    return write_entry(**kwargs)


@pytest.fixture
def upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


class TestRefusalIsNamed:
    @pytest.mark.parametrize("zone", [MemoryEntry.SENSITIVITY_YELLOW, MemoryEntry.SENSITIVITY_RED])
    def test_no_consent_is_refused_by_the_db_and_named(self, upc, zone) -> None:
        before = _rejections()
        with _adult():
            result = _write(upc, zone)

        assert result is None
        assert _rejections() == before + 1
        assert not MemoryEntry.objects.filter(user_id=upc.user_id, sensitivity_zone=zone).exists()

    def test_with_consent_the_row_is_written(self, upc) -> None:
        # Положительная пара: согласие есть — строка ложится, отказа нет. Иначе
        # «отказ назван» проходил бы и у писателя, который отказывает всем.
        with _adult():
            entry = _write(upc, MemoryEntry.SENSITIVITY_YELLOW, consent_at=timezone.now())

        assert entry is not None
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_YELLOW
        assert _rejections() == 0  # empty-assert-ok: строкой выше строка записана — отказов не было


class TestBroadExceptDoesNotSwallow:
    def test_caller_with_broad_except_leaves_the_named_trace(self, upc) -> None:
        swallowed: list[BaseException] = []

        def caller() -> None:
            # Как у всех четырёх вызывающих сегодня: память не ломает ход.
            try:
                _write(upc, MemoryEntry.SENSITIVITY_RED)
            except Exception as exc:  # noqa: BLE001
                swallowed.append(exc)

        before = _rejections()
        with _adult():
            caller()

        # До широкого перехвата ничего не долетело, а отказ записан по имени.
        assert _rejections() == before + 1
        assert swallowed == [], swallowed  # empty-assert-ok: строкой выше отказ записан


class TestOtherIntegrityErrorsPropagate:
    def test_a_different_check_is_not_swallowed_by_the_writer(self, upc) -> None:
        # CHECK 1 той же миграции: inferred без last_inferred_at. Это не про
        # согласие — писатель обязан пробросить, а не переименовать.
        with pytest.raises(IntegrityError):
            _write(
                upc,
                MemoryEntry.SENSITIVITY_GREEN,
                source=MemoryEntry.SOURCE_INFERRED,
                last_inferred_at=None,
            )
        assert (
            _rejections() == 0
        )  # empty-assert-ok: исключение строкой выше — отказ не назван чужим именем
