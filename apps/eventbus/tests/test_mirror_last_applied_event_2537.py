"""DRF-2537 — зеркало броней помнит, какое событие канона применило последним.

Все пути, которые пишут ``RemoteBookingProxy`` по событию канона, пишут одну
отметку: ``last_synced_event_id`` + имя события + ``occurred_at`` конверта.
Собственные записи бота (``skills/booking/tools.py``) ставят «неизвестно».

Сторож не на «столбец заполнен», а на «после применения события отметка
равна применённому событию» — по узлу на каждый из восьми путей. Плюс
перепись по исходнику: каждое место, пишущее ``last_synced_event_id`` в
потребителе, делает это через ``_applied_event_marks``; подмена — путь,
вписавший поле руками, — обязана краснеть и назвать строку.

Предел (назван и в модели): это НЕ свежесть. Пропущенное событие отметку не
двигает; «протухшую копию» отсюда не видно.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import pytest

import apps.eventbus.consumers.booking as consumer_module
from apps.booking.models import RemoteBookingProxy
from apps.eventbus import ingest_dispatcher
from apps.eventbus.consumers.booking import (
    handle_appointment_rescheduled_canonical,
    handle_booking_cancelled,
    handle_booking_completed,
    handle_booking_confirmed,
    handle_booking_created,
    handle_booking_no_show,
    handle_booking_rescheduled,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.tests.test_booking_consumer import (  # noqa: F401 — autouse fixtures
    APPOINTMENT_ID,
    AYLA_USER_ID,
    TENANT_ID,
    _booking_created_data,
    _freeze_time_for_reminder_tests,
    _pilot_allowlist,
    _pilot_tenant_row,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    """Тенант соседнего набора — строку заводит его autouse ``_pilot_tenant_row``."""
    return Tenant.objects.get(id=TENANT_ID)


#: Время выпуска события — нарочно не совпадает с «сейчас» замороженных часов
#: и с временем фикстур соседнего набора: отметка обязана взять его из конверта.
OCCURRED_AT = dt.datetime(2026, 5, 19, 8, 7, 6, tzinfo=dt.UTC)
SEED_EVENT = "evt-seed-2537"
SEED_AT = dt.datetime(2026, 5, 1, 0, 0, tzinfo=dt.UTC)


def _env(event_name: str, data: dict[str, Any], event_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=1,
        occurred_at=OCCURRED_AT,
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def _seed(tenant: Tenant, status: str) -> RemoteBookingProxy:
    """Строка зеркала с ЧУЖОЙ отметкой — чтобы «записал» отличалось от «оставил»."""
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=None,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.UTC),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.UTC),
        status=status,
        last_synced_event_id=SEED_EVENT,
        last_applied_event_name="booking.seed",
        last_applied_event_at=SEED_AT,
    )


# Восемь путей потребителя: (имя пути, начальное состояние, конверт, обработчик).
_PATHS: dict[str, tuple[str | None, Callable[[], IngestEnvelope], Callable[..., None]]] = {
    "created:insert": (
        None,
        lambda: _env("booking.created", _booking_created_data(), "evt-2537-created-ins"),
        handle_booking_created,
    ),
    "created:update": (
        "pending_payment",  # не «продвинутое» состояние → ветка обновления
        lambda: _env(
            "booking.created", _booking_created_data(status="confirmed"), "evt-2537-created-upd"
        ),
        handle_booking_created,
    ),
    "cancelled": (
        "confirmed",
        lambda: _env(
            "booking.cancelled",
            {"appointment_id": APPOINTMENT_ID, "cancelled_by": "client", "reason_code": "x"},
            "evt-2537-cancelled",
        ),
        handle_booking_cancelled,
    ),
    "confirmed": (
        "pending_payment",
        lambda: _env("booking.confirmed", {"appointment_id": APPOINTMENT_ID}, "evt-2537-confirmed"),
        handle_booking_confirmed,
    ),
    "rescheduled:legacy": (
        "confirmed",
        lambda: _env(
            "booking.rescheduled",
            {
                "appointment_id": APPOINTMENT_ID,
                "old_start_at": "2026-05-22T15:00:00+00:00",
                "new_start_at": "2026-05-23T11:00:00+00:00",
                "rescheduled_by": "admin",
            },
            "evt-2537-resched-legacy",
        ),
        handle_booking_rescheduled,
    ),
    "rescheduled:canonical": (
        "confirmed",
        lambda: _env(
            "appointment.rescheduled",
            {
                "appointment_id": APPOINTMENT_ID,
                "version": 1,
                "previous_version": 0,
                "revision_id": "rev-2537",
                "changed_fields": ["starts_at"],
                "actor": "admin",
                "starts_at": "2026-05-23T11:00:00+00:00",
                "previous_starts_at": "2026-05-22T15:00:00+00:00",
            },
            "evt-2537-resched-canon",
        ),
        handle_appointment_rescheduled_canonical,
    ),
    "completed": (
        "confirmed",
        lambda: _env(
            "booking.completed",
            {"appointment_id": APPOINTMENT_ID, "completed_at": "2026-05-22T16:30:00Z"},
            "evt-2537-completed",
        ),
        handle_booking_completed,
    ),
    "no_show": (
        "confirmed",
        lambda: _env("booking.no_show", {"appointment_id": APPOINTMENT_ID}, "evt-2537-no-show"),
        handle_booking_no_show,
    ),
}


class TestEveryConsumerPathWritesTheMark:
    @pytest.mark.parametrize("path", sorted(_PATHS))
    def test_mark_equals_the_applied_event(self, path: str, tenant: Tenant) -> None:
        seed_status, build, handler = _PATHS[path]
        if seed_status is not None:
            _seed(tenant, seed_status)
        env = build()

        handler(env)

        row = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        # Отметка — ровно то событие, которое применено, а не «что-то есть».
        assert (
            row.last_synced_event_id,
            row.last_applied_event_name,
            row.last_applied_event_at,
        ) == (
            env.event_id,
            env.event_name,
            OCCURRED_AT,
        ), path

    def test_paths_cover_every_registered_mirror_writer(self) -> None:
        # Новый обработчик, пишущий зеркало, без узла здесь — красно: перепись
        # путей идёт по реестру диспетчера, а не по памяти.
        handlers_here = {handler for _, _, handler in _PATHS.values()}
        registered = {
            ingest_dispatcher._REGISTRY[(name, 1)]
            for name in (
                "booking.created",
                "booking.confirmed",
                "booking.cancelled",
                "booking.rescheduled",
                "appointment.rescheduled",
                "booking.completed",
                "booking.no_show",
            )
        }
        assert handlers_here == registered
        # Восемь путей на семь обработчиков: у created — вставка и обновление.
        assert len(_PATHS) == 8


# ─── перепись по исходнику: одна функция отметки на все пути ─────────────────

_FIELD = "last_synced_event_id"
_HELPER = "_applied_event_marks"


def _manual_writes(source: str) -> list[int]:
    """Строки, где потребитель пишет ``last_synced_event_id`` МИМО функции отметки."""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _HELPER:
            continue
        if isinstance(node, ast.keyword) and node.arg == _FIELD:
            lines.append(node.value.lineno)
        elif isinstance(node, ast.Dict):
            lines.extend(
                k.lineno for k in node.keys if isinstance(k, ast.Constant) and k.value == _FIELD
            )
        elif isinstance(node, ast.Assign):
            lines.extend(
                t.lineno for t in node.targets if isinstance(t, ast.Attribute) and t.attr == _FIELD
            )
    helper = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == _HELPER)
    inside = set(range(helper.lineno, (helper.end_lineno or helper.lineno) + 1))
    return sorted(line for line in lines if line not in inside)


def _helper_calls(source: str) -> int:
    return sum(
        1
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == _HELPER
    )


class TestCensusBySource:
    source = Path(consumer_module.__file__).read_text(encoding="utf-8")

    def test_no_path_writes_the_event_id_by_hand(self) -> None:
        # Наличие впереди: функция отметки вызывается на всех семи местах записи.
        assert _helper_calls(self.source) == 7
        assert _manual_writes(self.source) == []

    def test_substitution_a_path_that_forgot_is_named(self) -> None:
        # Подмена: один путь (no_show) пишет поле руками, как было до DRF-2537 —
        # перепись обязана краснеть и назвать строку.
        forgot = self.source.replace(
            "        status=RemoteBookingProxy.Status.NO_SHOW,\n        **_applied_event_marks(envelope),",
            "        status=RemoteBookingProxy.Status.NO_SHOW,\n        last_synced_event_id=envelope.event_id,",
        )
        assert forgot != self.source, "подмена не нашла место — перепись проверяет не то"
        manual = _manual_writes(forgot)
        assert len(manual) == 1
        assert "NO_SHOW" in forgot.splitlines()[manual[0] - 2]
        assert _helper_calls(forgot) == 6


# ─── собственные записи бота: «неизвестно», а не унаследованное ──────────────


class _Record:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw


class TestBotOwnWritesMarkUnknown:
    def test_upsert_resets_mark_to_unknown(self, tenant: Tenant, monkeypatch) -> None:
        from apps.skills.booking.tools import _upsert_remote_booking_proxy

        monkeypatch.setattr("apps.skills.booking.tools._booking_via_ayla", lambda: True)
        seeded = _seed(tenant, "pending_payment")
        # Наличие впереди: у строки была чужая отметка.
        assert seeded.last_applied_event_at == SEED_AT

        _upsert_remote_booking_proxy(
            tenant=tenant,
            bot_user=None,
            record=_Record({"ayla_appointment_id": APPOINTMENT_ID}),
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.UTC),
        )

        row = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert row.status == "confirmed"  # бот записал константу…
        assert (row.last_applied_event_name, row.last_applied_event_at) == (
            "",
            None,
        )  # …без подписи

    def test_mirror_cancel_resets_mark_to_unknown(self, tenant: Tenant, monkeypatch) -> None:
        from apps.skills.booking.tools import _mirror_cancel

        monkeypatch.setattr("apps.skills.booking.tools._booking_via_ayla", lambda: True)
        seeded = _seed(tenant, "confirmed")
        assert seeded.last_applied_event_name == "booking.seed"

        _mirror_cancel(tenant=tenant, record_id=APPOINTMENT_ID)

        row = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert row.status == "cancelled"
        assert (row.last_applied_event_name, row.last_applied_event_at) == ("", None)
