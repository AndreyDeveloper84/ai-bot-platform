"""Обед перестал показываться свободным временем — DRF-1638.

# Что было

Провод Ayla несёт ``break_start`` / ``break_end``. Разбор кадра их ронял:
``FrameHours`` нёс три поля из семи, и до ``_compute_free_windows`` перерыв
не доезжал. Окна вычитают записи и блоки — перерыв не был ни тем, ни другим,
и попадал в «свободное время».

Цепь была замкнута с обеих сторон: каталог считает перерыв занятым и на
чтении, и на записи (``slot_builder``), то есть экран предлагал время,
которое запись отклоняла — 409 на том, что сам же и показал.

# Почему это не заметили

Экспозиция нулевая: замер каталога 10.09.2026 — 63 строки часов у девяти
мастеров пилота, ``break_start`` заполнен у **нуля**. Дефект не спал, а ждал
первой строки: колонка есть и заполняема, и первый салон, поставивший обед,
получил бы его показанным свободным.

# Чем закрыт

Перерыв доезжает до кадра и вычитается ТЕМ ЖЕ механизмом, что недоступность
— как блок. Своей арифметики ему не заводили: второй способ «убрать кусок из
рамки» рядом с существующим означал бы второе определение занятости.

Приоритет («исключение на дату бьёт недельный шаблон») остался ОДИН:
``_working_block_for_day`` отдаёт окно и перерыв вместе, а не двумя
функциями с одинаковым правилом внутри.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings

from apps.catalog.models import CatalogMaster
from apps.master_api.services import schedule_frame
from apps.master_api.services.schedule import build_schedule
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
#: Вторник — рабочий день в шаблоне ниже.
DAY = dt.date(2026, 9, 15)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="lunch-salon", name="Формула тела", timezone="Europe/Moscow")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=71,
        external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
        name="Ольга",
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture(autouse=True)
def _staff(tenant: Tenant) -> None:
    """Активный владелец — человек, чьи права Ayla проверяет при чтении."""
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"owner-{uuid.uuid4().hex[:8]}",
        ayla_user_id=str(uuid.uuid4()),
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER)


class _FakeSalonClient:
    def __init__(self, *, template, exceptions=None):
        self._template = template
        self._exceptions = exceptions or []

    def get_master_schedule(self, **kwargs):
        return self._template

    def list_schedule_exceptions(self, **kwargs):
        return self._exceptions

    def list_time_off(self, **kwargs):
        return []


def _template(*, break_start=None, break_end=None) -> list[dict]:
    """Недельный шаблон в форме провода. Перерыв — единственная переменная."""
    return [
        {
            "day_of_week": i,
            "day_name": "",
            "is_working_day": True,
            "start_time": "10:00",
            "end_time": "19:00",
            "break_start": break_start,
            "break_end": break_end,
        }
        for i in range(7)
    ]


def _windows(monkeypatch, master, template, exceptions=None):
    monkeypatch.setattr(
        schedule_frame,
        "get_salon_client",
        lambda: _FakeSalonClient(template=template, exceptions=exceptions),
    )
    with override_settings(BOOKING_VIA_AYLA_REST=True):
        payload = build_schedule(master, from_date=DAY, to_date=DAY)
    day = payload.days[0]
    return day, [(w.start, w.end) for w in day.free_windows]


class TestTheLunchBreakIsSubtracted:
    def test_a_named_break_splits_the_day(self, monkeypatch, master) -> None:
        day, windows = _windows(
            monkeypatch, master, _template(break_start="13:00", break_end="14:00")
        )

        # Смена 10:00–19:00 с обедом 13:00–14:00 — два окна, не одно.
        assert windows == [("10:00", "13:00"), ("14:00", "19:00")]

    def test_without_a_break_the_day_stays_whole(self, monkeypatch, master) -> None:
        # Положительный контроль к тесту выше. Без него «обеда нет в окнах»
        # зеленело бы и на дне, где окон нет вовсе: единственная разница
        # между двумя тестами — две строки провода.
        _, windows = _windows(monkeypatch, master, _template())

        assert windows == [("10:00", "19:00")]

    def test_the_break_is_shown_as_a_block_not_only_hidden(self, monkeypatch, master) -> None:
        # Вычесть из окон мало: администратор должен видеть, ПОЧЕМУ в
        # середине дня дыра. Иначе «окно кончилось в 13:00» читается как
        # чужая запись, которой нет в списке.
        day, _ = _windows(monkeypatch, master, _template(break_start="13:00", break_end="14:00"))

        reasons = [b.reason for b in day.blocks]
        assert "lunch" in reasons, f"перерыв не показан среди блоков: {reasons}"

    def test_a_half_named_break_is_not_invented(self, monkeypatch, master) -> None:
        # Половина перерыва — не перерыв: вычитать нечего. Но и выдумывать
        # вторую границу нельзя, поэтому день остаётся целым, а в лог уходит
        # имя — «назван наполовину» и «не назван» разные состояния данных.
        _, windows = _windows(monkeypatch, master, _template(break_start="13:00", break_end=None))

        assert windows == [("10:00", "19:00")]


class TestTheDateOverrideWinsForTheBreakToo:
    def test_an_exception_brings_its_own_break(self, monkeypatch, master) -> None:
        # Правило приоритета одно на окно и на перерыв. Если бы перерыв
        # разрешался отдельной функцией, эти два ответа однажды разошлись
        # бы: окно из исключения, перерыв из шаблона.
        day, windows = _windows(
            monkeypatch,
            master,
            _template(break_start="13:00", break_end="14:00"),
            exceptions=[
                {
                    "id": "e1",
                    "date": DAY.isoformat(),
                    "is_working_day": True,
                    "start_time": "12:00",
                    "end_time": "18:00",
                    "break_start": "15:00",
                    "break_end": "16:00",
                }
            ],
        )

        # Окно исключения 12:00–18:00, перерыв ЕГО — 15:00–16:00, а не
        # шаблонный 13:00–14:00.
        assert windows == [("12:00", "15:00"), ("16:00", "18:00")]

    def test_an_exception_without_a_break_does_not_borrow_the_weekly_one(
        self, monkeypatch, master
    ) -> None:
        # Обратная сторона того же правила: исключение заменяет строку
        # целиком, и «перерыва нет» в нём значит «нет», а не «возьми из
        # шаблона».
        _, windows = _windows(
            monkeypatch,
            master,
            _template(break_start="13:00", break_end="14:00"),
            exceptions=[
                {
                    "id": "e1",
                    "date": DAY.isoformat(),
                    "is_working_day": True,
                    "start_time": "12:00",
                    "end_time": "18:00",
                    "break_start": None,
                    "break_end": None,
                }
            ],
        )

        assert windows == [("12:00", "18:00")]
