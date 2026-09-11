"""Как причина «мастер не продаётся» становится слагом отказа при записи.

DRF-1548, решение владельца ``docs/OPEN_DECISIONS.md`` §32 пункт 1:
мастер без канонической связи с Ayla не становится видимым **и
доступным для записи**. Видимость закрыла DRF-1544; запись — здесь.

Две построчные перепроверки под локом —
``apps.booking.services.create.create_customer_booking`` и
``apps.booking.services.transitions.commit_reschedule`` — набирали
условия продажи руками (``is_active`` + ``invite_status``) и потому не
знали ни про ``archived_at``, ни про ``ayla_user_id``. Бронь на
несвязанного мастера они пропускали, а дальше ``resolve_master`` не
находил строку: уведомление не доходило, клиент приходил, мастера не
было.

Почему :func:`~apps.catalog.master_state.sale_block`, а не ``is_available``
------------------------------------------------------------------------
Обе точки возвращают **типизированный** отказ (``master_archived`` /
``master_not_bookable``), то есть им нужна ПРИЧИНА, а не булево.
``is_available`` схлопнул бы три разных отказа в один и отнял бы то,
что эти места умеют сегодня.

Почему таблица здесь, а не по копии в каждом файле
--------------------------------------------------
Слаг один на обе точки, и связывает их именно он, а не файл. Копия в
каждом файле дала бы состояние, где новая причина названа в одной
половине и молчит в другой — ровно тот тихий отказ, который задача
убирает.

Почему таблица обязана быть полной
----------------------------------
Слаг едет наружу к клиенту, а обе таблицы статусов в
``apps/miniapp_api/views.py`` разбирают его через ``.get(slug, ...)``
с умолчанием: неизвестный слаг НЕ падает и НЕ логируется — он получает
правдоподобный статус и уезжает клиенту. Поэтому:

* пропуск в :data:`SALE_BLOCK_SLUG` — это ``KeyError``, то есть громко,
  а не тихо;
* полноту таблицы держит тест ``test_every_sale_block_has_a_slug``:
  DRF-1521 добавила ``profile_incomplete`` в ``SaleBlock``, и забыть про
  него здесь было нельзя.

Статус (404 на создании, 409 на переходах) живёт не тут, а в таблицах
вида: слаг отвечает «почему», статус — «что человек может сделать», и
на эти два вопроса у создания и у переноса разные ответы при одном и
том же слаге.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final, Mapping, get_args

from apps.catalog.master_state import SaleBlock, sale_block

#: Причина «не продаётся» → стабильный слаг отказа брони.
#:
#: ``revoked`` и ``pending`` названы теми же слагами, которыми обе точки
#: отвечали до DRF-1548, — контракт наружу не менялся. Новых здесь два:
#: ``master_ayla_unlinked`` (DRF-1548) и ``master_profile_incomplete``
#: (DRF-1521). Четыре причины — четыре слага, ни одного общего.
#:
#: ``master_profile_incomplete`` обязан отличаться от
#: ``master_ayla_unlinked``, хотя клиенту оба означают «к этому мастеру
#: не записаться»: слаг едет и в аудит, и на экран владелицы салона, а
#: там это два разных следующих шага — «профиль не заполнен» отправляет
#: её к мастеру, «не удалось связать с Ayla» отправляет к нам.
SALE_BLOCK_SLUG: Final[Mapping[SaleBlock, str]] = MappingProxyType(
    {
        "revoked": "master_archived",
        "pending": "master_not_bookable",
        "ayla_unlinked": "master_ayla_unlinked",
        "profile_incomplete": "master_profile_incomplete",
        "schedule_unconfirmed": "master_schedule_unconfirmed",
    }
)

#: Все значения :data:`SaleBlock` — для теста полноты таблицы.
ALL_SALE_BLOCKS: Final[tuple[str, ...]] = get_args(SaleBlock)


def master_sale_refusal(master: Any) -> tuple[str, str] | None:
    """``(слаг, detail)`` отказа для этого мастера; ``None`` — продаётся.

    Тонкая обёртка над :func:`apps.catalog.master_state.sale_block`:
    предикат отвечает «почему», а эта функция переводит ответ на язык
    слагов брони. Свою копию условий не набирает и набирать не должна —
    предикат один на весь продукт.

    ``detail`` — для нас и для фронта, не для человека: клиентские
    экраны разбирают слаг, а текст показывают свой.
    """

    block = sale_block(master)
    if block is None:
        return None
    slug = SALE_BLOCK_SLUG[block]
    if block == "pending":
        return slug, f"master invite_status={master.invite_status}"
    if block == "ayla_unlinked":
        return slug, "master has no canonical ayla_user_id; booking notification would not arrive"
    if block == "profile_incomplete":
        return slug, "master accepted the invite but her profile is not ready for sale"
    if block == "schedule_unconfirmed":
        return slug, "the salon owner has not confirmed this master's current working hours"
    return slug, "master deactivated before booking confirmed"


__all__ = ["ALL_SALE_BLOCKS", "SALE_BLOCK_SLUG", "master_sale_refusal"]
