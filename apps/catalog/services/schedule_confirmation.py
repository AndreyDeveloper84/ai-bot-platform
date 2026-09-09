"""Подтверждение расписания мастера — отпечаток, подтверждение, сброс.

§83 (решение владельца 09.09.2026), отвечает DRF-1521 п. 6. Третье и
последнее из условий готовности мастера к продаже: до сегодня из трёх
было выразимо одно.

Подтверждение — **отдельное бизнес-решение**, а не свойство часов:
«этого мастера можно показывать клиентам и принимать к нему записи».
Поэтому три столбца живут на :class:`~apps.catalog.models.CatalogMaster`
(состояние допуска к продаже), а не на ``scheduling.WorkingHours``.

Правила владельца, которые исполняет этот модуль:

1. по умолчанию расписание НЕ подтверждено;
2. владелец салона видит часы мастера и нажимает «Расписание верно»;
3. после подтверждения мастер может пройти гейт продажи;
4. любое изменение часов отменяет старое подтверждение;
5. повторное подтверждение требуется уже для НОВОЙ версии расписания;
6. нельзя проставить подтверждение всем миграцией.

Плюс два, добавленных при разборе провода и утверждённых главным окном:

7. подтверждать нечего, пока нет ни одного рабочего дня;
8. короткий ответ провода — сбой, а не расписание.

Почему отпечаток снимается с СЫРЫХ строк ответа
------------------------------------------------
Соблазн взять готовый разобранный кадр
(:func:`apps.master_api.services.schedule_frame.load_day_frame` →
``FrameHours``) реален: он рядом, он типизирован и он уже ходит в Ayla.
**Брать его нельзя.**

``FrameHours`` несёт три поля — ``is_working``, ``start_time``,
``end_time``. Провод недельного шаблона несёт **семь**: к ним ещё
``day_of_week``, ``day_name`` и **``break_start`` / ``break_end``**.
Перерыв теряет разбор, а не провод (см. фикстуру
``apps/master_api/tests/test_schedule_frame.py``). Отпечаток, снятый с
``FrameHours``, не изменился бы при смене перерыва — и правило 4 молча
не сработало бы: часы поменялись, подтверждение осталось.

Это тот же класс, что «пересказ источника — не источник»: разбор — не
провод, и читать надо провод.

Что входит в отпечаток и что не входит
---------------------------------------
Входит: ``day_of_week``, ``is_working_day``, ``start_time``,
``end_time``, ``break_start``, ``break_end`` — по всем семи дням,
отсортированным по номеру дня. **Порядок строк на отпечаток не влияет**:
сортировка своя, а не как пришло.

Не входит:

* ``day_name`` — подпись для экрана. Смена локали не должна выглядеть
  как смена расписания;
* всё, чего мы не назвали. Список полей **явный**, а не «весь словарь»:
  иначе одно новое поле, добавленное сверху, обнулило бы все
  подтверждения разом — сброс без причины;
* исключения по датам и отпуска (``schedule-exceptions``, ``time-off``).
  Подтверждается НЕДЕЛЬНЫЙ ШАБЛОН, как и назвал владелец: «дни,
  интервалы, перерывы». Включи мы отпуска — подтверждение слетало бы
  каждый раз, когда мастер берёт день, и правило 5 стало бы
  еженедельной повинностью;
* **часовой пояс — и это названный разрыв, а не забывчивость.**
  Владелец назвал пояс частью подтверждаемого. На проводе недельного
  шаблона пояса нет ни одним полем, а локальный ``Tenant.timezone`` под
  ``BOOKING_VIA_AYLA_REST`` часами не распоряжается: ими распоряжается
  Ayla. Вдобавок умолчание той колонки (``Europe/Moscow``) делает
  «никто не выбирал» неотличимым от «выбрали Москву» — DRF-1606.
  Положить её в отпечаток значило бы **заверить не тот пояс**, то есть
  ровно «значение есть, смысла за ним нет». Решение главного окна
  09.09.2026: пояс вне отпечатка; **смена часового пояса салона — повод
  для новой кампании подтверждений, а не тихий сброс.**

Формат отпечатка
----------------
``<версия>:<источник>:<sha256 канонической строки>``, например
``v1:ayla:3f8a…``. Версия и источник стоят в значении, а не только в
коде: смена алгоритма или переключение ``BOOKING_VIA_AYLA_REST`` обязаны
делать все прежние подтверждения неактуальными **видимо**, а не
совпадать хешом с новыми. Подтверждали другое расписание — значит
подтверждение к нему и относится.

Почему гейт сверяет только столбец, а не отпечаток
---------------------------------------------------
:func:`apps.catalog.master_state.sale_block` построчный и зовётся в
цикле по ростеру. Сверка отпечатка означала бы HTTP-запрос на человека —
тот самый N+1, ради отсутствия которого ростер читает ``.values()``.
Поэтому гейт спрашивает ``schedule_confirmed_at``, а актуальность
держит сброс (:func:`clear_confirmation`) в момент изменения часов.
Отпечаток — это то, чем ловится расхождение, если сброс не доехал.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Final, Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Версия алгоритма отпечатка. Меняется ОСОЗНАННО и обесценивает все
#: прежние подтверждения — они относились к другому способу считать.
FINGERPRINT_VERSION: Final[str] = "v1"

#: Сколько строк обязан вернуть недельный шаблон. Ровно семь, всегда:
#: Ayla добивает дни, для которых строки не заведено, — см. докстринг
#: ``salon_client.get_master_schedule``. Значит короткий ответ это сбой
#: провода, а НЕ «мастер работает четыре дня» (правило 8).
WEEK_LENGTH: Final[int] = 7

#: Поля строки провода, которые входят в отпечаток. Список ЯВНЫЙ:
#: «весь словарь» означал бы, что новое поле сверху обнуляет все
#: подтверждения разом. ``day_name`` намеренно отсутствует.
FINGERPRINTED_FIELDS: Final[tuple[str, ...]] = (
    "day_of_week",
    "is_working_day",
    "start_time",
    "end_time",
    "break_start",
    "break_end",
)

#: Почему подтвердить НЕЛЬЗЯ. ``None`` — можно.
#:
#: Слаг, а не булево, по тому же доводу, по которому
#: :data:`apps.catalog.master_state.SaleBlock` — словарь причин: два
#: отказа требуют двух разных действий владельца салона. «Нет ни одного
#: рабочего дня» — она идёт заводить часы; «провод отдал не то» — она
#: идёт к нам.
ConfirmBlock = Literal["no_working_day", "wire_incomplete"]


class ScheduleConfirmationError(Exception):
    """Подтвердить нельзя, и причина названа слагом.

    Attributes:
      slug: стабильная причина из :data:`ConfirmBlock`.
      detail: объяснение для нас и для фронта; текст человеку — на экране.
    """

    def __init__(self, slug: ConfirmBlock, detail: str) -> None:
        super().__init__(detail)
        self.slug: ConfirmBlock = slug
        self.detail = detail


@dataclass(frozen=True)
class WeeklyTemplate:
    """Недельный шаблон, прочитанный у источника, плюс его отпечаток.

    ``rows`` — сырые строки провода, отсортированные по дню недели;
    экран показывает их, отпечаток считается по ним же. Один источник
    для показа и для подписи — чтобы владелец подтверждал ровно то, что
    видит.
    """

    source: str
    rows: list[dict[str, Any]]
    fingerprint: str

    @property
    def has_working_day(self) -> bool:
        """Есть ли хоть один рабочий день (правило 7)."""

        return any(bool(row.get("is_working_day")) for row in self.rows)


def _source_name() -> str:
    """Кто сегодня источник часов — и это НЕ стилистический выбор.

    Под ``BOOKING_VIA_AYLA_REST`` часы клиенту продаёт Ayla, а локальная
    ``apps.scheduling`` — зеркало, которого не читает никто (замер
    09.09.2026: флаг на пилоте включён). Подтверждать зеркало значило бы
    подтвердить не то расписание, по которому продают.

    Имя источника едет в отпечаток: если флаг однажды переключат,
    подтверждения обязаны стать неактуальными видимо.
    """

    return "ayla" if bool(getattr(settings, "BOOKING_VIA_AYLA_REST", False)) else "local"


def fingerprint_rows(rows: list[dict[str, Any]], *, source: str) -> str:
    """Отпечаток недельного шаблона по сырым строкам провода.

    Сортировка по ``day_of_week`` своя: порядок, в котором строки
    приехали, на отпечаток влиять не должен — иначе сброс срабатывал бы
    на ровном месте.

    Значения берутся как есть, включая ``None``: «перерыв не задан» и
    «перерыв с 13:00» — разные расписания, и ``None`` здесь несёт смысл,
    а не отсутствие данных.
    """

    canonical = [
        [_normalise(row.get(field)) for field in FINGERPRINTED_FIELDS]
        for row in sorted(rows, key=lambda r: _day_index(r))
    ]
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{FINGERPRINT_VERSION}:{source}:{digest}"


def _day_index(row: dict[str, Any]) -> int:
    """Номер дня для сортировки; нераспознанный день — в конец.

    Не бросает: строку с битым днём поймает :func:`read_weekly_template`
    (правило 8) с внятным слагом, а сортировка не то место, где отказ
    читается понятно.
    """

    value = row.get("day_of_week")
    return value if isinstance(value, int) else WEEK_LENGTH


def _normalise(value: Any) -> Any:
    """Значение в форме, устойчивой к косметике провода.

    Время приходит строкой ``"HH:MM"``; ``"10:00"`` и ``"10:00:00"`` —
    одно и то же время и обязаны давать один отпечаток. Всё остальное
    отдаётся как есть: подменять здесь ничего нельзя, иначе отпечаток
    начнёт скрывать различия, ради которых он заведён.
    """

    if isinstance(value, str) and len(value) == 8 and value.endswith(":00") and value[2] == ":":
        return value[:5]
    return value


def read_weekly_template(master: Any) -> WeeklyTemplate:
    """Прочитать недельный шаблон у ЖИВОГО источника и снять отпечаток.

    Читает сам, а не принимает то, что показал экран: между показом и
    нажатием проходит время, и подписывать надо прочитанное сейчас.

    Правило 8 держится здесь: ответ короче семи строк — сбой провода.
    ``get_master_schedule`` обязан вернуть ровно семь строк, Ayla сама
    добивает дни без записи. Принять короткий ответ значило бы прочитать
    «этих дней нет» как «в эти дни не работает» и подтвердить расписание,
    которого никто не задавал.

    Недоступность источника наверх не глотается: ``SalonUnavailable`` /
    ``SalonNotConfigured`` доходят до вызывающего. Подтверждение по
    неизвестному — это подпись под неизвестным.
    """

    source = _source_name()
    rows = _read_ayla(master) if source == "ayla" else _read_local(master)

    if len(rows) != WEEK_LENGTH:
        raise ScheduleConfirmationError(
            "wire_incomplete",
            f"weekly template returned {len(rows)} rows, expected {WEEK_LENGTH}",
        )
    days = {_day_index(row) for row in rows}
    if days != set(range(WEEK_LENGTH)):
        raise ScheduleConfirmationError(
            "wire_incomplete",
            f"weekly template does not cover every weekday: {sorted(days)}",
        )

    ordered = sorted(rows, key=_day_index)
    return WeeklyTemplate(
        source=source,
        rows=ordered,
        fingerprint=fingerprint_rows(ordered, source=source),
    )


def _read_ayla(master: Any) -> list[dict[str, Any]]:
    """Недельный шаблон из Ayla — источника, по которому продают.

    Актор тот же, что у экрана расписания мастера
    (:func:`apps.master_api.services.schedule_frame._ayla_read_actor`):
    Ayla проверяет права человека, названного в ``X-External-User-ID``,
    и салон без активного владельца или администратора прочитать нельзя.
    Это факт конфигурации, а не сбой, и он не имеет права выглядеть как
    пустое (то есть полностью свободное) расписание.
    """

    from apps.integrations.ayla.salon_client import SalonNotConfigured, get_salon_client
    from apps.integrations.ayla.user_proxy import external_user_id_for
    from apps.master_api.services.schedule_frame import _ayla_read_actor

    tenant = master.tenant
    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        raise SalonNotConfigured(
            f"no active owner/admin staff to read the weekly template for {tenant.slug}"
        )

    return get_salon_client().get_master_schedule(
        actor_external_id=external_user_id_for(actor_user),
        tenant_slug=tenant.slug,
        specialist_id=str(master.id),
    )


def _read_local(master: Any) -> list[dict[str, Any]]:
    """Недельный шаблон из локальной ``WorkingHours`` — флаг ВЫКЛЮЧЕН.

    Живой путь пилота не этот (09.09.2026 флаг включён), но выключение
    флага — объявленный аварийный откат, и на нём подтверждение обязано
    считаться от того же, от чего в этом режиме считаются слоты клиента.

    Строки приводятся к ФОРМЕ ПРОВОДА, а не наоборот: отпечаток один и
    считается по одному набору полей. Дни без строки добиваются
    нерабочими — здесь это делаем мы, потому что локальная таблица, в
    отличие от Ayla, хранит только заведённые дни.
    """

    from apps.scheduling.models import WorkingHours

    stored = {
        row.day_of_week: row
        for row in WorkingHours.all_tenants.filter(tenant_id=master.tenant_id, master_id=master.id)
    }
    rows: list[dict[str, Any]] = []
    for day in range(WEEK_LENGTH):
        row = stored.get(day)
        rows.append(
            {
                "day_of_week": day,
                "is_working_day": bool(row.is_working) if row is not None else False,
                "start_time": _hhmm(row.start_time) if row is not None else None,
                "end_time": _hhmm(row.end_time) if row is not None else None,
                "break_start": _hhmm(row.lunch_start) if row is not None else None,
                "break_end": _hhmm(row.lunch_end) if row is not None else None,
            }
        )
    return rows


def _hhmm(value: Any) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def confirm_schedule(master: Any, *, by: Any) -> WeeklyTemplate:
    """«Расписание верно»: подписать ТЕ часы, которые читаются сейчас.

    Возвращает подтверждённый шаблон. Поднимает
    :class:`ScheduleConfirmationError` с причиной, если подтверждать
    нечего, и пропускает наверх недоступность источника.

    **Правило 7 держится здесь.** Расписание без единого рабочего дня —
    валидный отпечаток и ложное подтверждение: мастер прошёл бы гейт
    продажи с нулевой сеткой, то есть продавался бы, не имея ни одного
    часа. Отказ с именем, а не молчаливое разрешение.

    Автор обязателен и не имеет умолчания. Подтверждение без автора —
    это «кто-то когда-то нажал», ровно то состояние, ради ухода от
    которого заведён отпечаток.
    """

    if by is None:
        raise ValueError(
            "confirm_schedule requires an author; a confirmation with nobody "
            "behind it is the state the fingerprint exists to prevent"
        )

    template = read_weekly_template(master)
    if not template.has_working_day:
        raise ScheduleConfirmationError(
            "no_working_day",
            "the weekly template has no working day at all; there is nothing to confirm",
        )

    from apps.audit.services import write_audit
    from apps.events.vocabulary import MASTER_SCHEDULE_CONFIRMED

    with transaction.atomic():
        master.schedule_confirmed_at = timezone.now()
        master.schedule_confirmed_by = by
        master.schedule_fingerprint = template.fingerprint
        master.save(
            update_fields=[
                "schedule_confirmed_at",
                "schedule_confirmed_by",
                "schedule_fingerprint",
            ]
        )
        write_audit(
            MASTER_SCHEDULE_CONFIRMED,
            target="catalog.CatalogMaster",
            target_id=master.pk,
            payload={
                "master_id": str(master.pk),
                "source": template.source,
                "fingerprint": template.fingerprint,
            },
            actor_id=getattr(by, "pk", None),
        )

    logger.info(
        "catalog.schedule_confirmation.confirmed master=%s tenant=%s source=%s by=%s",
        master.pk,
        master.tenant_id,
        template.source,
        getattr(by, "pk", by),
    )
    return template


def clear_confirmation(*, tenant_id: Any, ayla_user_id: Any, reason: str) -> int:
    """Снять подтверждение — правило 4. Возвращает число снятых строк.

    Зовётся, когда часы изменились: под ``BOOKING_VIA_AYLA_REST`` это
    событие ``master.schedule.updated`` (``apps.eventbus.consumers.schedule``),
    единственный живой хук — по локальной таблице на пилоте не продают.

    Адресация по ``(tenant_id, ayla_user_id)``, а не по ``CatalogMaster.id``,
    и это не описка: ``master_id`` события — идентификатор Ayla-**пользователя**
    (``event-contract.md`` §3.11: «``user_id`` here echoes the master
    themselves»), тогда как ``CatalogMaster.id`` — идентификатор
    специалиста. Тот же ключ, что уже использует бамп ``cache_version``
    рядом.

    Снимается ВСЁ ТРИ столбца сразу. Оставить автора или отпечаток от
    снятого подтверждения значило бы держать след, который читается как
    действующее подтверждение.

    ``update()`` мимо ``save()`` здесь уместен и нужен: это снятие, а не
    установка. Установка идёт только через :func:`confirm_schedule` — с
    живым чтением, автором и отпечатком.

    Пишет через **тенантный** менеджер внутри :func:`tenant_scope`, а не
    через ``all_tenants``. Соблазн взять ``all_tenants`` реален — у
    консьюмера на входе только строка ``tenant_id``, — и он же роняет
    сторож границ (`MKT1`): межсалонное чтение каталога живёт только в
    ``apps/marketplace``. Лишний запрос за салоном дешевле дыры в
    изоляции, а ``tenant_scope`` для того и заведён — «use this in
    worker consumer entry points».

    Салона нет локально — снимать нечего, возвращается ноль. Это НЕ
    молчание: у нуля здесь одна причина и она названа в логе, а вторая
    («строки зеркала нет») отвечается вызывающим отдельно.
    """

    from apps.catalog.models import CatalogMaster
    from apps.tenancy.context import tenant_scope
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        logger.info(
            "catalog.schedule_confirmation.no_tenant tenant=%s ayla_user_id=%s reason=%s",
            tenant_id,
            ayla_user_id,
            reason,
        )
        return 0

    from apps.audit.services import write_audit
    from apps.events.vocabulary import MASTER_SCHEDULE_CONFIRMATION_CLEARED

    with tenant_scope(tenant):
        affected = CatalogMaster.objects.filter(
            ayla_user_id=ayla_user_id,
            schedule_confirmed_at__isnull=False,
        )
        # Отпечаток читается ДО снятия: без него запись аудита скажет
        # «подтверждение снято» и не скажет, какое именно расписание было
        # заверено. Лишний запрос платится один раз на изменение часов у
        # одного мастера — не горячий путь.
        previous = list(affected.values_list("id", "schedule_fingerprint"))
        cleared = affected.update(
            schedule_confirmed_at=None,
            schedule_confirmed_by=None,
            schedule_fingerprint="",
        )

        for master_pk, previous_fingerprint in previous:
            write_audit(
                MASTER_SCHEDULE_CONFIRMATION_CLEARED,
                target="catalog.CatalogMaster",
                target_id=master_pk,
                payload={
                    "master_id": str(master_pk),
                    "reason": reason,
                    "previous_fingerprint": previous_fingerprint,
                },
            )

    if cleared:
        logger.info(
            "catalog.schedule_confirmation.cleared tenant=%s ayla_user_id=%s rows=%d reason=%s",
            tenant_id,
            ayla_user_id,
            cleared,
            reason,
        )
    return cleared


def confirmation_is_current(master: Any) -> bool:
    """Совпадает ли подтверждённый отпечаток с нынешними часами.

    **Гейт продажи это НЕ зовёт** — см. докстринг модуля: построчный вызов
    в цикле по ростеру означал бы HTTP-запрос на человека. Это средство
    для подметания и для экрана, где один мастер и одно чтение: им
    ловится расхождение, если сброс по событию не доехал.
    """

    if master.schedule_confirmed_at is None:
        return False
    return read_weekly_template(master).fingerprint == master.schedule_fingerprint


__all__ = [
    "FINGERPRINTED_FIELDS",
    "FINGERPRINT_VERSION",
    "WEEK_LENGTH",
    "ConfirmBlock",
    "ScheduleConfirmationError",
    "WeeklyTemplate",
    "clear_confirmation",
    "confirm_schedule",
    "confirmation_is_current",
    "fingerprint_rows",
    "read_weekly_template",
]
