"""§83 / DRF-1521 п. 6 — подтверждение расписания: отпечаток и правила.

Каждый класс здесь — targeted proof одного правила решения владельца от
09.09.2026. Правила 1-6 его собственные, 7 и 8 добавлены при разборе
провода и утверждены главным окном.

Самые ценные тесты — не те, что проверяют «подтверждение записалось»:

* :class:`TestRule4AnyChangeOfHoursDropsTheConfirmation` — без него
  «подтверждено» означает «кто-то когда-то нажал»;
* :class:`TestTheFingerprintIsTakenFromTheWireNotTheParsedFrame` —
  ловит ровно тот способ, которым правило 4 могло бы молча не сработать:
  перерыв есть на проводе и теряется в разборе;
* :class:`TestRule6NobodyIsConfirmedWholesale` — сторож, краснеющий на
  дешёвом обходе, и **счётчик по салону**, потому что сумма построчных
  «не продаётся» это «клиент не увидит никого».
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.test import override_settings

from apps.catalog.models import CatalogMaster
from apps.catalog.services import schedule_confirmation as sc
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonNotConfigured, SalonUnavailable
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


# ── стенд ────────────────────────────────────────────────────────────────


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        slug="confirm-salon", name="Формула тела", timezone="Europe/Moscow"
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=11,
        external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
        name="Тихонова Ольга",
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def owner(tenant: Tenant) -> BotUser:
    """Владелец салона: и актор чтения для Ayla, и автор подтверждения."""

    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"owner-{uuid.uuid4().hex[:8]}",
        ayla_user_id=str(uuid.uuid4()),
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER)
    return bot_user


def wire_week(**overrides) -> list[dict]:
    """Семь строк недельного шаблона в форме ПРОВОДА, не разбора.

    Форма взята с фикстуры ``apps/master_api/tests/test_schedule_frame.py``
    — та собрана по живому контракту Ayla, и семь полей здесь стоят
    ровно потому, что их семь на проводе.
    """

    rows = [
        {
            "day_of_week": i,
            "day_name": "",
            "is_working_day": i != 6,
            "start_time": "10:00" if i != 6 else None,
            "end_time": "19:00" if i != 6 else None,
            "break_start": None,
            "break_end": None,
        }
        for i in range(7)
    ]
    for day, patch in overrides.items():
        rows[int(day.removeprefix("day"))].update(patch)
    return rows


class FakeSalonClient:
    def __init__(self, template: list[dict], exc: Exception | None = None) -> None:
        self.template = template
        self.exc = exc
        self.calls = 0

    def get_master_schedule(self, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.template


@pytest.fixture
def ayla(monkeypatch):
    """Флаг ВКЛЮЧЁН — живая конфигурация пилота (замер 09.09.2026).

    Возвращает установщик: `ayla(rows)` подсовывает ответ провода.
    """

    def use(template: list[dict], exc: Exception | None = None) -> FakeSalonClient:
        client = FakeSalonClient(template, exc)
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: client)
        return client

    with override_settings(BOOKING_VIA_AYLA_REST=True):
        yield use


# ── правило 1 ────────────────────────────────────────────────────────────


class TestRule1NothingIsConfirmedByDefault:
    """«По умолчанию расписание не подтверждено» — и для новой строки тоже."""

    def test_a_fresh_master_carries_no_confirmation(self, master: CatalogMaster) -> None:
        assert master.schedule_confirmed_at is None
        assert master.schedule_confirmed_by_id is None
        assert master.schedule_fingerprint == ""

    def test_a_synced_master_gets_no_confirmation_from_sync(
        self, tenant: Tenant, master: CatalogMaster
    ) -> None:
        """Синхронизация подтверждения не приносит — она его и не знает.

        Столбцы платформенные: ``upsert_specialists`` пишет узкий список
        зеркальных полей. Тест ловит обратное — попытку однажды завести
        подтверждение «из источника», то есть без человека.
        """

        from apps.catalog.services.upserter import upsert_specialists
        from apps.catalog.services.http_client import CatalogSpecialistDTO

        dto = CatalogSpecialistDTO(
            ayla_master_id=str(master.id),
            name="Тихонова Ольга",
            bio="",
            experience="",
            rating=None,
            review_count=0,
            is_active=True,
            user_id=str(master.ayla_user_id),
            address=None,
            location_lat=None,
            location_lng=None,
            external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
            raw={},
            tenant=str(tenant.id),
        )
        upsert_specialists(tenant, [dto])

        master.refresh_from_db()
        assert master.schedule_confirmed_at is None
        assert master.schedule_fingerprint == ""


# ── правила 2 и 3: подтверждение существует и имеет автора ───────────────


class TestRule2TheOwnerConfirmsAndItIsRecorded:
    def test_confirmation_records_when_who_and_what(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        ayla(wire_week())
        template = sc.confirm_schedule(master, by=owner)

        master.refresh_from_db()
        assert master.schedule_confirmed_at is not None
        assert master.schedule_confirmed_by_id == owner.id
        assert master.schedule_fingerprint == template.fingerprint
        assert master.schedule_fingerprint.startswith("v1:ayla:")

    def test_a_confirmation_without_an_author_is_refused(self, master: CatalogMaster, ayla) -> None:
        """Автор обязателен: без него это «кто-то когда-то нажал»."""

        ayla(wire_week())
        with pytest.raises(ValueError):
            sc.confirm_schedule(master, by=None)
        master.refresh_from_db()
        assert master.schedule_confirmed_at is None

    def test_the_confirmation_signs_what_the_service_read_itself(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Подписывается прочитанное СЕЙЧАС, а не показанное экраном.

        Между показом и нажатием проходит время. Сервис обязан сходить
        к источнику сам — иначе владелец подписывает вчерашний экран.
        """

        client = ayla(wire_week())
        sc.confirm_schedule(master, by=owner)
        assert client.calls == 1


# ── правило 4 ────────────────────────────────────────────────────────────


class TestRule4AnyChangeOfHoursDropsTheConfirmation:
    """Изменились часы — подтверждение слетело. Без этого правило 5 пусто."""

    def _confirm(self, master, owner, ayla, rows):
        ayla(rows)
        return sc.confirm_schedule(master, by=owner)

    def test_the_event_clears_the_confirmation(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Живой хук: ``master.schedule.updated`` снимает все три столбца."""

        self._confirm(master, owner, ayla, wire_week())
        master.refresh_from_db()
        assert master.schedule_confirmed_at is not None

        cleared = sc.clear_confirmation(
            tenant_id=master.tenant_id,
            ayla_user_id=master.ayla_user_id,
            reason="test",
        )

        assert cleared == 1
        master.refresh_from_db()
        assert master.schedule_confirmed_at is None
        assert master.schedule_confirmed_by_id is None
        assert master.schedule_fingerprint == ""

    def test_a_changed_hour_makes_the_stored_fingerprint_stale(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Сброс не доехал — расхождение всё равно видно по отпечатку.

        Это вторая линия: столбец говорит «подтверждено», а часы уже
        другие. Ровно то, ради чего отпечаток и заведён.
        """

        self._confirm(master, owner, ayla, wire_week())
        master.refresh_from_db()
        assert sc.confirmation_is_current(master) is True

        ayla(wire_week(day0={"end_time": "20:00"}))
        assert sc.confirmation_is_current(master) is False

    def test_a_changed_BREAK_also_makes_it_stale(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Перерыв — часть расписания, и он ОБЯЗАН двигать отпечаток.

        Тест краснеет, если отпечаток снимать с разобранного кадра:
        ``FrameHours`` перерыва не несёт, и смена перерыва прошла бы
        незамеченной — подтверждение осталось бы «актуальным» на
        изменившихся часах.
        """

        self._confirm(master, owner, ayla, wire_week())
        master.refresh_from_db()

        ayla(wire_week(day0={"break_start": "13:00", "break_end": "14:00"}))
        assert sc.confirmation_is_current(master) is False

    def test_an_unconfirmed_master_is_never_current(self, master: CatalogMaster) -> None:
        """«Не подтверждено» не имеет права читаться как «актуально»."""

        assert sc.confirmation_is_current(master) is False


# ── правило 5 ────────────────────────────────────────────────────────────


class TestRule5ReconfirmationIsForTheNewVersion:
    def test_confirming_again_stores_the_new_fingerprint(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        ayla(wire_week())
        first = sc.confirm_schedule(master, by=owner)

        ayla(wire_week(day0={"end_time": "20:00"}))
        second = sc.confirm_schedule(master, by=owner)

        assert first.fingerprint != second.fingerprint
        master.refresh_from_db()
        assert master.schedule_fingerprint == second.fingerprint
        assert sc.confirmation_is_current(master) is True

    def test_confirmed_once_is_not_confirmed_for_any_hours(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """«Подтверждено для ЭТОЙ версии» ≠ «подтверждено когда-то».

        Без этого различия отпечаток бесполезен, и это ровно тот тест,
        которого передача требовала отдельной строкой.
        """

        ayla(wire_week())
        sc.confirm_schedule(master, by=owner)
        master.refresh_from_db()
        stamped_at = master.schedule_confirmed_at

        ayla(wire_week(day3={"is_working_day": False, "start_time": None, "end_time": None}))

        assert master.schedule_confirmed_at == stamped_at  # столбец на месте
        assert sc.confirmation_is_current(master) is False  # смысла за ним больше нет


# ── правило 6 ────────────────────────────────────────────────────────────


class TestRule6NobodyIsConfirmedWholesale:
    """Дешёвый обход запрещён, и сторож краснеет на попытке."""

    def test_no_catalog_migration_stamps_a_confirmation(self) -> None:
        """Миграции каталога не проставляют подтверждение — ни одна.

        Сторож текстовый и намеренно грубый: любая миграция, которая
        одновременно упоминает столбец подтверждения и умеет исполнять
        код или писать данные, обязана быть замечена человеком, а не
        проехать в общем потоке.
        """

        from pathlib import Path

        import apps.catalog.migrations as migrations_pkg

        folder = Path(migrations_pkg.__file__).parent
        offenders = []
        for path in sorted(folder.glob("0*.py")):
            body = path.read_text(encoding="utf-8")
            names = ("schedule_confirmed_at", "schedule_confirmed_by", "schedule_fingerprint")
            if not any(name in body for name in names):
                continue
            for forbidden in ("RunPython", "RunSQL", "bulk_update", ".update("):
                if forbidden in body:
                    offenders.append(f"{path.name}: {forbidden}")

        assert offenders == [], (
            "правило 6 §83: подтверждение расписания нельзя проставить миграцией — "
            f"нашлось: {offenders}"
        )

    def test_the_whole_salon_does_not_get_confirmed_by_one_call(
        self, tenant: Tenant, owner: BotUser, ayla
    ) -> None:
        """Счётчик по салону: подтверждают поимённо, а не оптом.

        Сумма построчных «подтверждено» — это «салон целиком объявлен
        проверенным». Считает тот же гейт, а не человек глазами.
        """

        masters = [
            CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=100 + i,
                external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
                name=f"Мастер {i}",
                ayla_user_id=uuid.uuid4(),
            )
            for i in range(5)
        ]
        ayla(wire_week())

        sc.confirm_schedule(masters[0], by=owner)

        confirmed = CatalogMaster.all_tenants.filter(
            tenant=tenant, schedule_confirmed_at__isnull=False
        ).count()
        assert confirmed == 1, "подтверждение одного мастера не имеет права задеть остальных"


# ── правило 7 ────────────────────────────────────────────────────────────


class TestRule7ThereIsNothingToConfirmWithoutAWorkingDay:
    """Расписание без единого рабочего дня подтвердить нельзя.

    Замер главного окна 09.09.2026 сделал этот случай основным, а не
    краевым: строки локальных часов есть у 4 из 31 продаваемых мастеров.
    Отпечаток пустой недели технически валиден и по смыслу лжив —
    мастер прошёл бы гейт продажи с нулевой сеткой.
    """

    def test_a_week_with_no_working_day_is_refused_by_name(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        empty = [
            {
                "day_of_week": i,
                "day_name": "",
                "is_working_day": False,
                "start_time": None,
                "end_time": None,
                "break_start": None,
                "break_end": None,
            }
            for i in range(7)
        ]
        ayla(empty)

        with pytest.raises(sc.ScheduleConfirmationError) as exc:
            sc.confirm_schedule(master, by=owner)

        assert exc.value.slug == "no_working_day"
        master.refresh_from_db()
        assert master.schedule_confirmed_at is None

    def test_one_working_day_is_enough(self, master: CatalogMaster, owner: BotUser, ayla) -> None:
        """Порог — «хотя бы один», а не «полная неделя».

        Мастер, работающий по субботам, — рабочий случай, а не ошибка.
        """

        rows = wire_week()
        for row in rows:
            row.update(is_working_day=False, start_time=None, end_time=None)
        rows[5].update(is_working_day=True, start_time="10:00", end_time="16:00")
        ayla(rows)

        sc.confirm_schedule(master, by=owner)
        master.refresh_from_db()
        assert master.schedule_confirmed_at is not None


# ── правило 8 ────────────────────────────────────────────────────────────


class TestRule8AShortWireAnswerIsAFailureNotASchedule:
    """Короткий ответ — сбой провода, а не «мастер работает четыре дня».

    Дословно из докстринга ``salon_client.get_master_schedule``: Ayla
    добивает дни, для которых строки не заведено, поэтому семь строк
    приходят всегда, а короткий список — «a wire problem, never «this
    master works four days»».
    """

    def test_a_four_row_answer_is_refused(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        ayla(wire_week()[:4])

        with pytest.raises(sc.ScheduleConfirmationError) as exc:
            sc.confirm_schedule(master, by=owner)

        assert exc.value.slug == "wire_incomplete"
        master.refresh_from_db()
        assert master.schedule_confirmed_at is None

    def test_seven_rows_that_skip_a_weekday_are_refused(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Семь строк — ещё не семь ДНЕЙ. Воскресенье подменено вторым понедельником."""

        rows = wire_week()
        rows[6]["day_of_week"] = 0
        ayla(rows)

        with pytest.raises(sc.ScheduleConfirmationError) as exc:
            sc.confirm_schedule(master, by=owner)

        assert exc.value.slug == "wire_incomplete"

    def test_an_eighth_row_is_refused_even_though_every_day_is_covered(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Восемь строк: покрытие полное, а расписание неоднозначное.

        Тест заведён по итогу targeted proof: снятие проверки длины НЕ
        роняло прогон, потому что короткий ответ ловила проверка
        покрытия. То есть у проверки длины доказательства не было, хотя
        ловит она свой случай — дубль дня поверх полной недели. Какая из
        двух строк правит понедельником, ответить нечем, и подтверждать
        такое нельзя.
        """

        rows = wire_week()
        rows.append(dict(rows[0], start_time="08:00", end_time="12:00"))
        ayla(rows)

        with pytest.raises(sc.ScheduleConfirmationError) as exc:
            sc.confirm_schedule(master, by=owner)

        assert exc.value.slug == "wire_incomplete"

    def test_an_unreachable_source_refuses_instead_of_confirming(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Источник недоступен — отказ, а не подпись под неизвестным."""

        ayla([], exc=SalonUnavailable("upstream down"))

        with pytest.raises(SalonUnavailable):
            sc.confirm_schedule(master, by=owner)
        master.refresh_from_db()
        assert master.schedule_confirmed_at is None

    def test_a_salon_with_no_owner_cannot_be_read(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Нет активного владельца или админа — читать нечем.

        Это факт конфигурации, и он не имеет права выглядеть как пустое,
        то есть полностью свободное, расписание.
        """

        TenantStaff.all_tenants.filter(tenant=master.tenant).update(
            deactivated_at=dt.datetime.now(tz=dt.timezone.utc)
        )
        ayla(wire_week())

        with pytest.raises(SalonNotConfigured):
            sc.confirm_schedule(master, by=owner)


# ── отпечаток: устройство ────────────────────────────────────────────────


class TestTheFingerprintIsTakenFromTheWireNotTheParsedFrame:
    """Состав отпечатка — то, из-за чего правило 4 работает или молчит."""

    def test_row_order_does_not_change_the_fingerprint(self) -> None:
        """Порядок строк на отпечаток не влияет — требование передачи.

        Иначе сброс срабатывал бы на ровном месте: провод не обещает
        порядок, а перестановка — не изменение расписания.
        """

        rows = wire_week()
        assert sc.fingerprint_rows(rows, source="ayla") == sc.fingerprint_rows(
            list(reversed(rows)), source="ayla"
        )

    def test_day_name_is_not_part_of_it(self) -> None:
        """Подпись дня — оформление. Смена локали не есть смена часов."""

        base = wire_week()
        renamed = [dict(row, day_name="Понедельник") for row in base]
        assert sc.fingerprint_rows(base, source="ayla") == sc.fingerprint_rows(
            renamed, source="ayla"
        )

    def test_an_unknown_extra_wire_field_does_not_reset_everybody(self) -> None:
        """Новое поле сверху не имеет права обнулить все подтверждения.

        Список полей явный именно поэтому: «весь словарь» превратил бы
        любое расширение контракта Ayla в тихий массовый сброс.
        """

        base = wire_week()
        extended = [dict(row, some_new_upstream_field="whatever") for row in base]
        assert sc.fingerprint_rows(base, source="ayla") == sc.fingerprint_rows(
            extended, source="ayla"
        )

    @pytest.mark.parametrize(
        "patch",
        [
            {"is_working_day": False},
            {"start_time": "11:00"},
            {"end_time": "18:00"},
            {"break_start": "13:00", "break_end": "14:00"},
        ],
        ids=["рабочий день", "начало", "конец", "перерыв"],
    )
    def test_every_fingerprinted_field_moves_it(self, patch: dict) -> None:
        """Каждое поле состава обязано двигать отпечаток.

        Параметр «перерыв» — тот самый, который потерялся бы при работе
        через ``FrameHours``.
        """

        base = wire_week()
        changed = wire_week(day0=patch)
        assert sc.fingerprint_rows(base, source="ayla") != sc.fingerprint_rows(
            changed, source="ayla"
        )

    def test_the_source_is_part_of_the_value(self) -> None:
        """Переключение источника обязано обесценить подтверждения ВИДИМО.

        Совпади отпечатки локальных и Ayla-часов — подтверждение,
        снятое с зеркала, молча зачлось бы за подтверждение того, по
        чему продают.
        """

        rows = wire_week()
        assert sc.fingerprint_rows(rows, source="ayla") != sc.fingerprint_rows(rows, source="local")

    def test_seconds_on_the_wire_do_not_change_the_hours(self) -> None:
        """``"10:00"`` и ``"10:00:00"`` — одно время, один отпечаток."""

        base = wire_week()
        with_seconds = [
            dict(
                row,
                start_time=None if row["start_time"] is None else row["start_time"] + ":00",
                end_time=None if row["end_time"] is None else row["end_time"] + ":00",
            )
            for row in base
        ]
        assert sc.fingerprint_rows(base, source="ayla") == sc.fingerprint_rows(
            with_seconds, source="ayla"
        )


class TestTheLocalSourceIsShapedLikeTheWire:
    """Флаг ВЫКЛЮЧЕН — аварийный откат, и он тоже обязан считаться."""

    def test_days_with_no_row_come_back_as_days_off(
        self, master: CatalogMaster, tenant: Tenant
    ) -> None:
        """Локальная таблица хранит только заведённые дни, Ayla — все семь.

        Добивать недостающие нерабочими — наша работа в этом режиме, и
        без неё правило 8 отвергало бы каждого локального мастера.
        """

        from apps.scheduling.models import WorkingHours

        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=0,
            is_working=True,
            start_time=dt.time(10, 0),
            end_time=dt.time(19, 0),
        )

        with override_settings(BOOKING_VIA_AYLA_REST=False):
            template = sc.read_weekly_template(master)

        assert len(template.rows) == 7
        assert template.source == "local"
        assert template.fingerprint.startswith("v1:local:")
        assert template.has_working_day is True
        assert [row["is_working_day"] for row in template.rows] == [True] + [False] * 6

    def test_a_master_with_no_hours_at_all_has_no_working_day(self, master: CatalogMaster) -> None:
        with override_settings(BOOKING_VIA_AYLA_REST=False):
            template = sc.read_weekly_template(master)

        assert len(template.rows) == 7
        assert template.has_working_day is False

    def test_a_stored_day_off_row_is_not_a_working_day(
        self, master: CatalogMaster, tenant: Tenant
    ) -> None:
        """Строка ЕСТЬ, а рабочего дня нет — и это не одно и то же.

        ``WorkingHours`` с ``is_working=False`` означает выходной: времена
        обязаны быть null, и это держит CHECK-constraint модели. Значит
        «у мастера есть строки расписания» и «у мастера есть рабочий день»
        — разные утверждения, и правило 7 спрашивает второе.

        Тест заведён потому, что соседние два проверяют ОТСУТСТВИЕ строк, а
        не наличие нерабочих. Считай мы строки вместо рабочих дней —
        подтверждать было бы «можно» у любого, кому завели семь выходных,
        и оба прежних теста остались бы зелёными.
        """

        from apps.scheduling.models import WorkingHours

        for day in range(7):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=day,
                is_working=False,
            )

        with override_settings(BOOKING_VIA_AYLA_REST=False):
            template = sc.read_weekly_template(master)

        assert len(template.rows) == 7, "семь строк на месте"
        assert template.has_working_day is False, "и ни одного рабочего дня"


class TestTheSweepIsTheOnlyResetThatActuallyRuns:
    """§83, правило 4 — обход снимает подтверждения, под которыми часы ушли.

    Событие ``master.schedule.updated`` за всю историю не приходило ни разу
    (замер пилота 09.09.2026), поэтому обход — не запасной путь, а
    единственный работающий. И он даёт сброс с задержкой до одного цикла,
    а не мгновенно: это названо и здесь, и в докстринге задачи.
    """

    def _confirm(self, master, owner, ayla, rows=None):
        ayla(rows or wire_week())
        return sc.confirm_schedule(master, by=owner)

    def test_a_changed_week_clears_the_confirmation(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        from apps.catalog.tasks import sweep_schedule_confirmations

        self._confirm(master, owner, ayla)
        ayla(wire_week(day1={"end_time": "21:00"}))

        counters = sweep_schedule_confirmations()

        assert counters == {"checked": 1, "cleared": 1, "unreadable": 0}
        master.refresh_from_db()
        assert master.schedule_confirmed_at is None
        assert master.schedule_fingerprint == ""

    def test_an_unchanged_week_is_left_alone(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Положительная стража: обход снимает НЕ ВСЁ подряд.

        Без этой половины предыдущий тест зеленел бы и на задаче, которая
        сбрасывает каждое подтверждение, до которого дотянется.
        """

        from apps.catalog.tasks import sweep_schedule_confirmations

        self._confirm(master, owner, ayla)

        counters = sweep_schedule_confirmations()

        assert counters == {"checked": 1, "cleared": 0, "unreadable": 0}
        master.refresh_from_db()
        assert master.schedule_confirmed_at is not None

    def test_an_unreadable_source_does_not_count_as_changed_hours(
        self, master: CatalogMaster, owner: BotUser, ayla
    ) -> None:
        """Ayla не ответила — подтверждение остаётся.

        Обратное решение сняло бы с витрины всех подтверждённых разом на
        первой же сетевой ошибке, и владелица прочла бы это как «у всех
        изменилось расписание». Отсутствие ответа доезжает отсутствием.
        """

        from apps.catalog.tasks import sweep_schedule_confirmations

        self._confirm(master, owner, ayla)
        ayla([], exc=SalonUnavailable("upstream down"))

        counters = sweep_schedule_confirmations()

        assert counters == {"checked": 1, "cleared": 0, "unreadable": 1}
        master.refresh_from_db()
        assert master.schedule_confirmed_at is not None

    def test_unconfirmed_masters_are_not_even_read(
        self, tenant: Tenant, master: CatalogMaster, ayla
    ) -> None:
        """Цена обхода — по подтверждённым, а не по всему пулу.

        У неподтверждённого сбрасывать нечего, и HTTP-вызов на него был бы
        платой ни за что. Сегодня это разница между девятью строками и
        тридцатью одной.
        """

        from apps.catalog.tasks import sweep_schedule_confirmations

        for i in range(4):
            CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=300 + i,
                external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
                name=f"Неподтверждённая {i}",
                ayla_user_id=uuid.uuid4(),
            )
        client = ayla(wire_week())

        counters = sweep_schedule_confirmations()

        assert counters == {"checked": 0, "cleared": 0, "unreadable": 0}
        assert client.calls == 0, "неподтверждённых обход не читает вовсе"
