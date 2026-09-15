"""Каким id строку зеркала знает каталог — одно место ответа (DRF-1933).

Первичный ключ ``CatalogMaster`` совпадает с каталожным
``SpecialistProfile.id`` только у строки, которую завела синхронизация.
Соло-мастер (``solo_onboarding``) и салонное приглашение, склеенное синком
(DRF-1507), живут под своим ``uuid4``. До DRF-1933 вызовы каталога слали
``str(master.id)`` — у таких мастеров каталог отвечал бы 404.

Факт хранится в колонке ``CatalogMaster.catalog_specialist_id``: её пишут
синхронизация (новые и склеенные строки) и провижининг соло-кабинета, а
заполнила для существующих строк миграция ``catalog/0022``. Здесь — только
чтение.

Пустая колонка — отказ по имени, а **не** первичный ключ: подставленный
``uuid4`` уходил бы в каталог и возвращался чужой ошибкой («мастер не
найден»), а не своей («у строки нет профиля в каталоге»).

Стережёт ``apps/catalog/tests/test_catalog_specialist_id_guard_1933.py``:
вызов клиента каталога с ``specialist_id`` обязан пройти здесь или быть
назван поимённо.
"""

from __future__ import annotations

from typing import Any


class CatalogSpecialistUnresolved(Exception):
    """У строки зеркала нет id профиля в каталоге — звать каталог не с чем."""

    reason = "catalog_specialist_unresolved"

    def __init__(self, master_pk: Any) -> None:
        super().__init__(f"{self.reason}: mirror master {master_pk} has no catalog specialist id")
        self.master_pk = master_pk


def catalog_specialist_id(master: Any) -> str:
    """Id ``SpecialistProfile`` в каталоге для строки зеркала; пусто — отказ."""

    value = getattr(master, "catalog_specialist_id", None)
    if not value:
        raise CatalogSpecialistUnresolved(getattr(master, "pk", None))
    return str(value)


__all__ = ["CatalogSpecialistUnresolved", "catalog_specialist_id"]
