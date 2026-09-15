"""База знаний: цена ниже 1 ₽ не попадает в текст услуги (DRF-1989).

``service_to_body`` писал «Цена от: 0.00» — модель читала это как
«бесплатно» и могла так и ответить. Только показ: поле не меняется.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogService
from apps.kb.projectors import service_to_body
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def test_price_below_one_rouble_is_not_projected() -> None:
    tenant = Tenant.objects.create(slug="kb-1989", name="KB 1989")
    row = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1989,
        external_updated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
        slug="piling-1989",
        name="Пилинг",
        price_from=Decimal("0.00"),
    )

    body = service_to_body(row)

    assert body.startswith("Услуга: Пилинг")
    assert "Цена" not in body
