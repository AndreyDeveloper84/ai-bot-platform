"""DRF-2569 — строка визита глобального бота: слова владельца 28.09, п.1–2.

Один дом формы — ``apps.booking.visit_words``: навык записи и глобальный бот
пишут под одной шапкой «Ваши предстоящие записи:» и обязаны давать одну
строку на одни данные.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.booking.services.records import _visit_from_record
from apps.integrations.ayla.booking_client import AylaUserRecord
from apps.orchestrator.visits import _visit_line
from apps.skills.booking.tools import BookingRow, _format_booking_line
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TENANT_ID = "9e3a0000-0000-4000-8000-000000000004"


def _record(tenant: dict | None) -> AylaUserRecord:
    raw: dict[str, object] = {"id": "a1"}
    if tenant is not None:
        raw["tenant"] = tenant
    return AylaUserRecord(
        appointment_id="a1",
        services=[{"name": "Массаж"}],
        master={"display_name": "Марина"},
        datetime="2026-09-25T06:00:00+00:00",
        duration_s=3600,
        raw=raw,
        price=3200.0,
    )


def test_salon_known_locally_gives_its_zone_two_salons_two_times() -> None:
    """Пара: один момент UTC, салоны в разных поясах — разное местное время."""
    Tenant.objects.create(
        id=TENANT_ID, slug="formula-tela", name="Формула тела", timezone="Europe/Moscow"
    )
    Tenant.objects.create(slug="lumina-ekb", name="Люмина", timezone="Asia/Yekaterinburg")

    msk = _visit_line(_visit_from_record(_record({"id": TENANT_ID, "name": "Формула тела"})))
    ekb = _visit_line(
        _visit_from_record(_record({"id": "", "slug": "lumina-ekb", "name": "Люмина"}))
    )

    assert msk == "Массаж — мастер Марина · Формула тела, 25.09.2026 в 09:00 — 3200 ₽"
    assert ekb == "Массаж — мастер Марина · Люмина, 25.09.2026 в 11:00 — 3200 ₽"


def test_unknown_salon_falls_back_to_the_pilot_zone_named_as_a_limit() -> None:
    visit = _visit_from_record(_record({"id": "not-a-uuid", "name": "Чужой салон"}))
    assert visit.salon_tz == ""
    assert "25.09.2026 в 09:00" in _visit_line(visit)


def test_both_chat_carriers_render_one_line_for_one_booking() -> None:
    Tenant.objects.create(
        id=TENANT_ID, slug="formula-tela", name="Формула тела", timezone="Europe/Moscow"
    )
    visit = _visit_from_record(_record({"id": TENANT_ID, "name": "Формула тела"}))
    global_bot = _visit_line(visit).removesuffix(" — 3200 ₽")
    skill = _format_booking_line(
        BookingRow(
            record_id="a1",
            visit_at=visit.start_at,
            master_name=visit.master_name,
            service_name=visit.service_name,
            status="CONFIRMED",
            salon_name=visit.salon_name,
            salon_tz=visit.salon_tz,
        )
    ).removeprefix("• ")
    assert global_bot == skill


def test_price_is_kept_after_the_canonical_part() -> None:
    visit = _visit_from_record(_record(None))
    assert visit.price == Decimal("3200.0")
    assert _visit_line(visit).endswith(" — 3200 ₽")


def test_a_bad_id_does_not_cancel_the_slug_lookup() -> None:
    """Ревью: невалидный id раньше обрывал и поиск по slug."""
    Tenant.objects.create(slug="lumina-ekb", name="Люмина", timezone="Asia/Yekaterinburg")
    visit = _visit_from_record(
        _record({"id": "not-a-uuid", "slug": "lumina-ekb", "name": "Люмина"})
    )
    assert visit.salon_tz == "Asia/Yekaterinburg"


def test_a_deactivated_salon_keeps_its_zone_for_history() -> None:
    Tenant.all_objects.create(
        slug="closed-ekb", name="Закрытый", timezone="Asia/Yekaterinburg", is_active=False
    )
    visit = _visit_from_record(_record({"slug": "closed-ekb", "name": "Закрытый"}))
    assert visit.salon_tz == "Asia/Yekaterinburg"


def test_one_visit_one_time_in_the_list_and_in_the_card_prompts() -> None:
    """Ревью: карточка/отмена/отзыв брали пилотный пояс, список — салона."""
    from apps.orchestrator.visits import _format_when

    Tenant.objects.create(slug="lumina-ekb", name="Люмина", timezone="Asia/Yekaterinburg")
    visit = _visit_from_record(_record({"slug": "lumina-ekb", "name": "Люмина"}))
    assert "в 11:00" in _visit_line(visit)
    assert _format_when(visit.start_at, visit.salon_tz).endswith("11:00")
