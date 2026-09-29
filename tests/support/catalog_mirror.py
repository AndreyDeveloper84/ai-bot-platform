"""Строка зеркала мастера «как у синка» для тестов (DRF-1933).

У строки, которую завела синхронизация, ``CatalogMaster.catalog_specialist_id``
равен первичному ключу. Тест, заводящий мастера напрямую, получает пустую
колонку — это форма строки приглашения или соло-мастера до провижининга, и
вызовы каталога отвечают на неё отказом (``catalog_profile_unresolved`` /
``master_unbookable``).

``sync_shaped`` — явная пометка «эта фикстура — мастер синка». Не глобальное
умолчание: отказ на пустой колонке — поведение, которое тесты DRF-1933 обязаны
видеть, и умолчание в плагине спрятало бы его во всём наборе.
"""

from __future__ import annotations

from typing import TypeVar

from apps.catalog.models import CatalogMaster

RowT = TypeVar("RowT", bound=CatalogMaster)


def sync_shaped(row: RowT) -> RowT:
    """Проставить строке id каталога, равный первичному ключу; вернуть её же."""

    CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=row.pk)
    row.catalog_specialist_id = row.pk
    return row
