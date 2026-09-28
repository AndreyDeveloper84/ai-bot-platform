"""DRF-2544 — у факта памяти есть происхождение, и три его значения различимы.

Правило владельца 24.08 (``docs/OD_MEMORY.md`` §3) делает различие по
``source_tenant_id``. До правки поле не заполнял никто (стенд 28.09: пусто у
5 из 5), и «сказано глобальной Ayla» было неотличимо от «не заполнили».

Узлы стоят на тройку, которая обязана различаться: салон разговора ≠ сентинел
глобальной поверхности ≠ неизвестно. Узел «запись проходит» был бы зелёным
при полностью сломанном правиле.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.identity.services.memory_origin import (
    ORIGIN_UNKNOWN,
    global_surface_scope,
    resolve_source_tenant_id,
)
from apps.identity.services.memory_writer import write_entry
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _write(upc: UserPersonalContext, **extra) -> MemoryEntry:
    entry = write_entry(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        kind="preference",
        content={"key": "diet", "value": "vegan"},
        request_id=uuid.uuid4(),
        purpose="drf-2544 test",
        **extra,
    )
    assert entry is not None
    entry.refresh_from_db()
    return entry


@pytest.fixture
def upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="origin-salon", name="Салон А")


class TestTheThreeOriginsDiffer:
    def test_said_in_a_salon_carries_that_salon(self, upc, salon) -> None:
        with tenant_scope(salon):
            entry = _write(upc)
        assert entry.source_tenant_id == salon.id

    def test_said_to_global_ayla_carries_the_sentinel(self, upc) -> None:
        sentinel = get_global_bot_tenant()
        with global_surface_scope():
            entry = _write(upc)
        assert entry.source_tenant_id == sentinel.id

    def test_global_surface_wins_over_a_salon_scope_inside_the_turn(self, upc, salon) -> None:
        # Передача записи на глобальном пути входит в tenant_scope мастера, но
        # человек говорил с Ayla — факт не становится «сказанным в салоне».
        sentinel = get_global_bot_tenant()
        with global_surface_scope(), tenant_scope(salon):
            entry = _write(upc)
        assert entry.source_tenant_id == sentinel.id

    def test_a_path_that_declared_nothing_is_unknown_not_global(self, upc) -> None:
        sentinel = get_global_bot_tenant()
        entry = _write(upc)
        assert entry.source_tenant_id is ORIGIN_UNKNOWN
        assert sentinel.id is not None

    def test_the_three_never_coincide(self, upc, salon) -> None:
        with tenant_scope(salon):
            in_salon = _write(upc).source_tenant_id
        with global_surface_scope():
            global_ = _write(upc).source_tenant_id
        unknown = _write(upc).source_tenant_id
        assert len({in_salon, global_, unknown}) == 3

    def test_an_explicit_value_wins(self, upc, salon) -> None:
        other = uuid.uuid4()
        with global_surface_scope():
            entry = _write(upc, source_tenant_id=other)
        assert entry.source_tenant_id == other

    def test_the_scope_does_not_leak_past_its_block(self) -> None:
        get_global_bot_tenant()
        with global_surface_scope():
            inside = resolve_source_tenant_id()
        assert inside is not ORIGIN_UNKNOWN
        assert resolve_source_tenant_id() is ORIGIN_UNKNOWN


class TestTheGlobalHandlerDeclaresItsSurface:
    """Граница, которая знает ответ, объявляет его: ход глобального бота пишет
    факты с сентинелом. Подмена «убрать global_surface_scope у обработчика»
    обязана краснеть здесь."""

    def test_the_inner_turn_runs_on_the_global_surface(self, monkeypatch) -> None:
        sentinel = get_global_bot_tenant()
        seen: list[uuid.UUID | None] = []
        monkeypatch.setattr(
            max_handler,
            "_handle_global_max_event_inner",
            lambda event, trace_id: seen.append(resolve_source_tenant_id()),
        )
        payload = {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": 72544, "name": "Иван"},
                "recipient": {"chat_id": 82544, "chat_type": "dialog"},
                "body": {"mid": "origin-2544", "seq": 1, "text": "я веган", "attachments": []},
            },
        }
        max_handler.handle_global_max_event(json.loads(json.dumps(payload)), trace_id="t-2544")
        assert seen == [sentinel.id]
