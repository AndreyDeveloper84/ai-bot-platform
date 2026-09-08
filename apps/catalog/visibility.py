"""Есть ли у салона хоть один мастер, которого клиент может увидеть.

DRF-1540 ввёл в гейт продажи ``ayla_user_id IS NOT NULL``: строка без
канонического ключа не продаётся, потому что уведомления о записи до
человека всё равно не дойдут. Решение владельца, и оно правильное.
У него есть цена, и её никто не считал.

### Чего не хватало

:func:`apps.catalog.master_state.sale_block` отвечает ПОСТРОЧНО и
отвечает хорошо: у каждого отказа своё слово, и владелица салона видит
это слово в ростере. Но вопрос «а осталось ли в этом салоне хоть кто-то,
кого клиенту показывают» построчный ответ не задаёт, и не задавал никто:
ни тест, ни экран здоровья. Сумма тридцати одного честного «эта не
продаётся» — это «клиент не увидит никого», и произносить её было
некому.

Молчаливость здесь полная, и это не фигура речи — каждый из уже
работающих сигналов на этом состоянии спокоен:

* **свежесть каталога** (DRF-1494) зелёная: синхронизация ходит вовремя
  и привозит строки;
* **расхождение зеркала с бэкендом** (DRF-1500 п. 2) нулевое: в зеркале
  ровно столько мастеров, сколько в источнике. Гейт продажи стоит ПОСЛЕ
  зеркала, и до чисел бэкенда ему нет дела;
* **ростер владелицы** покажет ``ayla_unlinked`` — но в него надо зайти,
  и он про людей по одному, а не про «салон целиком пуст для клиента»;
* **клиент** получает пустую выдачу. Ему причину не называет никто, и он
  единственная сторона, которая эту цену платит.

### Как рождается салон, целиком невидимый

Не гипотеза. ``apps.identity.services.solo_onboarding`` создаёт
соло-мастера ``ACCEPTED`` + ``is_active=True`` и **без**
``ayla_user_id``: приглашения она не принимала (значит, blank-fill в
``master_api/views.py`` по ``bot_user.ayla_user_id`` не сработает), а
синхронизация по её ``_solo_external_id`` не придёт. Для
:func:`~apps.catalog.master_state.is_admitted` она в полном порядке, для
:func:`~apps.catalog.master_state.sale_block` — ``ayla_unlinked``, для
клиента — пусто. В салоне на одного человека это ровно «арендатор
целиком невидим».

Настоящая починка этого случая — DRF-1541 (проставить ключ). Этот модуль
её не заменяет и не пытается: он делает так, чтобы состояние было ВИДНО,
пока починки нет, и чтобы следующее ужесточение гейта нельзя было выкатить
вслепую.

### Почему счётчик собирается ``sale_block``, а не своим ``Q``

Соблазн посчитать через :data:`~apps.catalog.master_state.ADMITTED` и
:data:`~apps.catalog.master_state.AVAILABLE` есть, и он дешевле по SQL.
Отказ намеренный: тогда рядом с построчным гейтом появился бы второй,
считающий, и следующее условие (DRF-1521 дописывает
``profile_incomplete``) попало бы в один и не попало в другой. Экран
здоровья начал бы отвечать «все продаются» о салоне, где витрина уже не
продаёт никого — то есть ровно ту ложь, ради отсутствия которой заведён
сам ``sale_block``. Определение остаётся одно; строк на салоне десятки,
и цена этого решения — один запрос ``.values()`` на арендатора.

### Персональных данных наружу не выходит

Гейт читает ``name`` и ``linked_bot_user_id`` — иначе ветка
``profile_incomplete`` не отличит «сняли с витрины» от «профиль не
дозаполнен». Ни то, ни другое из функции не возвращается:
:class:`TenantVisibility` несёт только числа и коды причин, и экран
здоровья остаётся экраном агрегатов.

### Замер на пилоте 08.09.2026 11:11 MSK

``admitted 31 / available 31`` из 34 строк; блокировано три, все —
``revoked`` (решение салона). **Невидимых целиком арендаторов сегодня
нет**, и это тот случай, когда сигнал заводят до происшествия, а не
после: ноль сегодня ничего не обещает про завтра, а узнать о переходе
через ноль было бы неоткуда.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from apps.catalog.master_state import SaleBlock, is_admitted, sale_block
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

#: Столбцы, которые читает построчный гейт.
#:
#: Список явный, а не ``.values()`` без аргументов: ``sale_block``
#: спрашивает строку строго (``KeyError`` на забытом ключе), и это
#: замысел — молчаливое умолчание читалось бы как «условие не
#: применилось». Тот же список собирает ростер владелицы
#: (``apps.admin_api.services.staff_roster``), и по той же причине.
_GATE_COLUMNS = (
    "name",
    "linked_bot_user_id",
    "is_active",
    "archived_at",
    "invite_status",
    "ayla_user_id",
    "accepted_at",
)


@dataclass(frozen=True)
class TenantVisibility:
    """Сколько мастеров этого салона клиент может увидеть — и почему не всех."""

    slug: str
    tenant_id: str
    mirrored: int
    admitted: int
    available: int
    blocks: Mapping[SaleBlock, int]

    @property
    def invisible_whole(self) -> bool:
        """Салон принят, но клиенту не показывает никого.

        Условие двойное намеренно. ``available == 0`` в одиночку кричало
        бы на салоне, которого ещё не заполнили: ноль строк, ноль продаж,
        и это не поломка, а состояние подключения — постоянная краснота,
        которая приучает читателя не смотреть на сигнал (тот же довод,
        по которому :func:`apps.catalog.staleness.sync_ages` исключает
        ``global_bot``).

        ``admitted > 0`` отсекает и второй непоказательный случай: салон,
        где все строки отозваны или ждут приглашения. Это решения
        владелицы, у них есть свои слова в ростере, и они не молчаливы.
        Кричать здесь стоит ровно о том, что выглядит рабочим и не
        продаётся: мастера приняты, активны, не в архиве — и ни один не
        доходит до клиента.
        """

        return self.admitted > 0 and self.available == 0

    @property
    def unlinked(self) -> int:
        """Сколько строк не продаётся из-за пустого ``ayla_user_id`` (DRF-1540)."""

        return self.blocks.get("ayla_unlinked", 0)

    @property
    def blocks_human(self) -> str:
        """Причины отказа — кодами гейта, а не третьим переводом на русский.

        Слова для человека уже живут в двух местах:
        ``apps.catalog.admin._BOOKABLE_NOTES`` (список оператора) и
        ``AdminPeopleScreen.tsx`` (кабинет владелицы). Третья копия
        разъехалась бы с ними на первом же расширении гейта — ровно то,
        что запрещает докстринг :mod:`apps.catalog.master_state`. Экран
        здоровья — экран разработчика и оператора, он рядом показывает
        сырые имена флагов; здесь код причины и есть точное слово, и он
        же ищется в логах.

        Пустая строка, если не продающихся нет: «—» и прочие прочерки
        рисует шаблон, а не эта строка.
        """

        return ", ".join(f"{reason}: {count}" for reason, count in sorted(self.blocks.items()))


def tenant_visibilities(*, tenants: Iterable[Tenant] | None = None) -> list[TenantVisibility]:
    """По каждому салону: сколько строк в зеркале, принято и продаётся.

    ``global_bot`` исключён по той же причине, что и в
    :func:`apps.catalog.staleness.sync_ages`: он владеет бестенантными
    ``BotUser`` и подбором, а не каталогом салона, и его вечный ноль был
    бы вечной краснотой.

    Чтение скоплено через ``tenant_scope`` — кросс-тенантный менеджер
    каталога зарезервирован за marketplace discovery (import_boundaries
    MKT1, #1018), и это ровно те строки, которые видит бот.
    """

    rows = (
        tenants
        if tenants is not None
        else Tenant.objects.exclude(slug=GLOBAL_BOT_TENANT_SLUG).order_by("slug")
    )

    # Импорт внутри функции: ``apps.catalog.models`` импортирует
    # ``master_state``, чтобы собрать ``bookable()``, и модуль сигнала,
    # затянутый в модели через шапку, замкнул бы загрузку приложения.
    from apps.catalog.models import CatalogMaster

    result: list[TenantVisibility] = []
    for tenant in rows:
        with tenant_scope(tenant):
            master_rows = list(CatalogMaster.objects.values(*_GATE_COLUMNS))

        blocks: Counter[SaleBlock] = Counter()
        admitted = 0
        available = 0
        for row in master_rows:
            if is_admitted(row):
                admitted += 1
            block = sale_block(row)
            if block is None:
                available += 1
            else:
                blocks[block] += 1

        result.append(
            TenantVisibility(
                slug=tenant.slug,
                tenant_id=str(tenant.id),
                mirrored=len(master_rows),
                admitted=admitted,
                available=available,
                blocks=dict(blocks),
            )
        )
    return result


def invisible_tenants(*, tenants: Iterable[Tenant] | None = None) -> list[TenantVisibility]:
    """Только те салоны, которые клиенту не показывают никого."""

    return [v for v in tenant_visibilities(tenants=tenants) if v.invisible_whole]
