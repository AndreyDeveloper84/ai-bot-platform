"""DRF-2705: строку salon-services читают по РАЗРЕШЁННОЙ длительности.

Контракт каталога (``beautygo_backend/docs/CATALOG_INTERNAL_API_CONTRACT.md`` §1):
``duration_minutes`` — «raw salon-level default», ``null`` у каждой услуги,
которая берёт длительность из шаблона; ``resolved_duration`` — «effective …
salon → template. Use this» (поле появилось 01.10.2026).

До правки оба читателя строки брали сырое поле:

* зеркало (``http_client._parse_salon_service``) писало ``null`` в
  ``CatalogService.duration_min`` — и мини-апп отвечал «service has no duration
  configured» на услугу, которую каталог продаёт;
* клиент записи (``booking_client._service_from_wire``) превращал ``null`` в
  ``0`` — нулевую длительность вместо неизвестной.

Здесь — сами читатели, без базы. Путь «строка → зеркало → слоты» держит
``apps/miniapp_api/tests/test_slots_resolved_duration_2705.py``.

В таблице случаев три разных числа — разрешённое 60, сырое 45, равные 30, —
чтобы ни один узел не прошёл совпадением полей.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.catalog.services.http_client import _parse_salon_service
from apps.integrations.ayla.booking_client import _service_from_wire
from apps.integrations.ayla.salon_service_duration import salon_service_duration_field

_ABSENT = object()


def _row(*, raw: Any, resolved: Any = _ABSENT) -> dict[str, Any]:
    """Строка по примеру §1 контракта; ``resolved`` можно не слать вовсе —
    каталог старше этого поля. Идентификаторы выдуманные."""
    row: dict[str, Any] = {
        "id": "5a100000-0000-4000-8000-000000002705",
        "tenant": "7e000000-0000-4000-8000-000000002705",
        "template": "7e3b0000-0000-4000-8000-000000002705",
        "category": None,
        "name": "Маникюр классический",
        "duration_minutes": raw,
        "base_price": None,
        "requires_health_check": False,
        "is_active": True,
        "source": "manual",
        "goals": [],
        "created_at": "2026-07-09T18:31:00Z",
        "updated_at": "2026-07-09T18:31:00Z",
    }
    if resolved is not _ABSENT:
        row["resolved_duration"] = resolved
    return row


# (подпись, сырое, разрешённое, ждём минут)
CASES = [
    # Живой случай листа: салон промолчал, каталог разрешил из шаблона.
    ("salon-null-resolved-from-template", None, 60, 60),
    # Ключ есть — отвечает он, даже когда сырое поле говорит другое.
    ("key-present-differs-from-raw", 45, 60, 60),
    # Контроль: равные поля ничего не меняют.
    ("fields-equal", 30, 30, 30),
    # Ключ есть и равен null — это ответ «разрешать нечем», а не повод
    # подставить сырое число.
    ("key-present-null-raw-set", 45, None, None),
    ("key-present-null-raw-null", None, None, None),
    # Каталог старше поля: ключа нет вовсе — читаем сырое, как раньше.
    ("key-absent-raw-set", 45, _ABSENT, 45),
    ("key-absent-raw-null", None, _ABSENT, None),
]
_IDS = [c[0] for c in CASES]


@pytest.mark.parametrize(("label", "raw", "resolved", "minutes"), CASES, ids=_IDS)
def test_the_field_that_answers(label: str, raw: Any, resolved: Any, minutes: int | None) -> None:
    assert salon_service_duration_field(_row(raw=raw, resolved=resolved)) == minutes


@pytest.mark.parametrize(("label", "raw", "resolved", "minutes"), CASES, ids=_IDS)
def test_the_mirror_stores_the_resolved_duration(
    label: str, raw: Any, resolved: Any, minutes: int | None
) -> None:
    dto = _parse_salon_service(_row(raw=raw, resolved=resolved))
    assert dto.duration_min == minutes
    # Строка едет в ``raw`` целиком: сырое слово салона не потеряно.
    assert dto.raw["duration_minutes"] == raw


@pytest.mark.parametrize(("label", "raw", "resolved", "minutes"), CASES, ids=_IDS)
def test_the_booking_client_reports_unknown_as_none_not_zero(
    label: str, raw: Any, resolved: Any, minutes: int | None
) -> None:
    service = _service_from_wire(_row(raw=raw, resolved=resolved))
    expected = None if minutes is None else minutes * 60
    assert service.duration_s == expected
    # Отдельно и буквально: неизвестная длительность — не ноль секунд.
    if minutes is None:
        assert service.duration_s is None
        assert service.duration_s != 0


@pytest.mark.parametrize(
    "junk",
    ["60", "60 мин", 60.0, True, 0, -5],
    ids=["numeric-string", "string-with-unit", "float", "bool", "zero", "negative"],
)
def test_the_booking_client_does_not_coerce_a_non_duration(junk: Any) -> None:
    """Не положительное целое — неизвестно, а не «примерно столько».

    То же правило, что у ``duration_from_edge`` (DRF-2678). ``True`` назван
    отдельно: ``isinstance(True, int)`` истинно, и без явной проверки он
    стал бы одной минутой.
    """
    assert _service_from_wire(_row(raw=None, resolved=junk)).duration_s is None


def test_the_mirror_still_drops_a_malformed_row_loudly() -> None:
    """Политика зеркала не изменилась (DRF-1494): нечитаемое значение роняет
    СТРОКУ исключением — обход страницы ловит его и считает строку потерянной,
    а не пишет в зеркало «длительности нет»."""
    with pytest.raises(ValueError):
        _parse_salon_service(_row(raw=None, resolved="60 мин"))
