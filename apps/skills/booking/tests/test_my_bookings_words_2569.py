"""DRF-2569 — строка «мои записи» в чате: слова владельца 28.09, п.1–2.

``docs/OWNER_WORDS_DECISIONS_2026-09-28.md``: «Массаж — мастер Марина ·
Формула тела, 25.09.2026 в 09:00». Время — в поясе салона записи; сырое
ISO/UTC человеку не показывается; имя мастера не склоняется.

До правки человек читал «• Массаж с Марина в 2026-09-25T06:00:00+00:00» —
UTC буквально и имя в неверном падеже.
"""

from __future__ import annotations

from apps.skills.booking.tools import BookingRow, _format_bookings_text


def _row(**kw) -> BookingRow:
    base = {
        "record_id": "a1",
        "visit_at": "2026-09-25T06:00:00+00:00",
        "master_name": "Марина",
        "service_name": "Массаж",
        "status": "CONFIRMED",
        "salon_name": "Формула тела",
        "salon_tz": "Europe/Moscow",
    }
    base.update(kw)
    return BookingRow(**base)


def test_the_owners_sample_line_verbatim() -> None:
    text = _format_bookings_text([_row()])
    assert text.splitlines() == [
        "Ваши предстоящие записи:",
        "• Массаж — мастер Марина · Формула тела, 25.09.2026 в 09:00",
    ]


def test_time_is_the_salons_not_utc_two_salons_two_zones() -> None:
    """Пара, которая обязана различаться: один и тот же момент в UTC —
    разное местное время у салонов в разных поясах."""
    msk = _format_bookings_text([_row()])
    ekb = _format_bookings_text([_row(salon_name="Люмина", salon_tz="Asia/Yekaterinburg")])
    assert "25.09.2026 в 09:00" in msk
    assert "25.09.2026 в 11:00" in ekb


def test_no_raw_iso_and_no_declined_preposition() -> None:
    text = _format_bookings_text([_row()])
    # Присутствие впереди: строка собрана словами владельца. Без неё «нет
    # сырого ISO» прошло бы и на пустом ответе.
    line = text.splitlines()[1]
    assert line == "• Массаж — мастер Марина · Формула тела, 25.09.2026 в 09:00"
    assert "2026-09-25T" not in line
    assert "+00:00" not in line
    assert " с Марина" not in line


def test_unparseable_time_is_dropped_not_printed() -> None:
    text = _format_bookings_text([_row(visit_at="завтра утром")])
    assert text.splitlines()[1] == "• Массаж — мастер Марина · Формула тела"


def test_without_master_the_salon_still_names_the_place() -> None:
    line = _format_bookings_text([_row(master_name="")]).splitlines()[1]
    assert line == "• Массаж — Формула тела, 25.09.2026 в 09:00"
