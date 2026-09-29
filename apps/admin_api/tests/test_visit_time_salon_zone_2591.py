"""DRF-2591 — время визита на проводе и в словах admin_api — час салона.

Экран дня салона берёт часы из строки (``SalonPilotScheduleScreen``), и
администратор видел визит на 3 часа раньше: база отдаёт UTC. Узлы — на ЧАСЫ,
а не на «время непустое» и «момент верный»: оба проходят и при дефекте.

Ручка ``/day/`` держится в ``test_salon_day.py``; здесь — ``day-schedule``
(записи и блоки из ``build_schedule``) и текст клиенту при увольнении мастера.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from apps.admin_api.services.master_deactivation import _render_customer_notification
from apps.admin_api.views_master_schedule import _in_salon_zone

MSK = ZoneInfo("Europe/Moscow")


def test_day_schedule_bookings_and_blocks_carry_the_salon_hour() -> None:
    body = {
        "days": [
            {
                "date": "2026-08-20",
                "bookings": [{"booking_id": "b1", "visit_at": "2026-08-20T07:00:00+00:00"}],
                "blocks": [
                    {"start": "2026-08-20T10:00:00+00:00", "end": "2026-08-20T11:00:00+00:00"}
                ],
                "free_windows": [{"start": "12:00", "end": "13:00"}],
            }
        ]
    }

    out = _in_salon_zone(body, MSK)

    day = out["days"][0]
    visit = day["bookings"][0]["visit_at"]
    assert visit[11:16] == "10:00", visit
    assert datetime.fromisoformat(visit) == datetime(2026, 8, 20, 7, 0, tzinfo=UTC)
    assert day["blocks"][0]["start"][11:16] == "13:00"
    assert day["blocks"][0]["end"][11:16] == "14:00"
    # Время суток (окна) — не момент, остаётся как есть.
    assert day["free_windows"][0] == {"start": "12:00", "end": "13:00"}


def test_day_schedule_leaves_non_moments_alone() -> None:
    body = {
        "days": [
            {
                "bookings": [{"visit_at": ""}, {"visit_at": None}],
                "blocks": [{"start": "обед", "end": None}],
            }
        ]
    }
    out = _in_salon_zone(body, MSK)
    assert out["days"][0]["bookings"] == [{"visit_at": ""}, {"visit_at": None}]
    assert out["days"][0]["blocks"] == [{"start": "обед", "end": None}]


def test_deactivation_message_names_the_salon_hour() -> None:
    """Текст уходит КЛИЕНТУ: «по вашей записи на 20.08.2026 10:00», а не 07:00."""
    # Заглушки вместо моделей: рендер читает только эти поля.
    booking: Any = SimpleNamespace(
        visit_at=datetime(2026, 8, 20, 7, 0, tzinfo=UTC),
        client_name="Мария Иванова",
        service_name="Маникюр",
    )
    old_master: Any = SimpleNamespace(
        name="Анна Петрова", tenant=SimpleNamespace(timezone="Europe/Moscow")
    )

    text = _render_customer_notification(
        booking, old_master=old_master, new_master=None, template="{visit_at_human}"
    )

    assert text == "20.08.2026 10:00"
