"""Каким id строку зеркала знает каталог — одно место ответа (DRF-1933).

Первичный ключ ``CatalogMaster`` совпадает с каталожным
``SpecialistProfile.id`` только у строки, которую завела синхронизация.
Соло-мастер (``solo_onboarding``) и салонное приглашение, склеенное синком
(DRF-1507), живут под своим ``uuid4``. До DRF-1933 вызовы каталога слали
``str(master.id)`` — у таких мастеров каталог отвечал бы 404.

Факт хранится в колонке ``CatalogMaster.catalog_specialist_id``: её пишут
синхронизация (новые и склеенные строки), провижининг соло-кабинета и —
с DRF-2379 — салонный провижининг (``apps/catalog/identity.py``, ветвь
``create`` для салонной строки: приглашение зовёт её сразу, подметальщик
добивает). Для существующих строк колонку заполнила миграция
``catalog/0022``. Здесь — только чтение.

Пустая колонка — отказ по имени, а **не** первичный ключ: подставленный
``uuid4`` уходил бы в каталог и возвращался чужой ошибкой («мастер не
найден»), а не своей («у строки нет профиля в каталоге»).

Стережёт ``apps/catalog/tests/test_catalog_specialist_id_guard_1933.py``:
вызов клиента каталога с ``specialist_id`` обязан пройти здесь или быть
назван поимённо.

Обратная сторона той же монеты — :func:`specialist_keys` (DRF-2185):
событие Ayla пишет в зеркало ``RemoteBookingProxy.specialist_id`` тот же
каталожный id, поэтому читатель зеркала, ищущий строки мастера по
``master.id``, у соло/склеенного мастера не находит ничего — дашборд и
расписание пусты при живых записях. Читатели зеркала фильтруют по
``specialist_id__in=specialist_keys(master)``; стережёт
``apps/master_api/tests/test_visit_source_specialist_keys_2185.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


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


def specialist_keys(master: Any) -> list[UUID]:
    """Под какими id зеркало ``RemoteBookingProxy`` знает этого мастера.

    ``specialist_id`` строки зеркала — ``SpecialistProfile.id`` каталога, как
    его прислало событие. У строки синка он равен первичному ключу; у
    соло-мастера и склеенного приглашения (DRF-1507) первичный ключ —
    uuid4, а каталожный id лежит в ``catalog_specialist_id`` (DRF-1933).
    Оба ключа — иначе соло-мастер создаёт запись (в Ayla уходит
    каталожный id) и тут же видит пустой день. Пустая колонка — один
    ключ, не отказ: чтение зеркала не зовёт каталог.
    """

    keys = [master.id]
    catalog_id = getattr(master, "catalog_specialist_id", None)
    if catalog_id and catalog_id != master.id:
        keys.append(catalog_id)
    return keys


__all__ = ["CatalogSpecialistUnresolved", "catalog_specialist_id", "specialist_keys"]
