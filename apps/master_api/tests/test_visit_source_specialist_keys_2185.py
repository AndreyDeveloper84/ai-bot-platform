"""Читатели зеркала ищут мастера по обоим ключам (DRF-2185).

Дыра: событие Ayla пишет ``RemoteBookingProxy.specialist_id`` =
``catalog_specialist_id`` (id профиля в каталоге), а ``visit_source``,
``schedule`` и ``master_deactivation`` фильтровали по ``master.id``. У строки
синка это одно и то же; у соло-мастера и склеенного приглашения (DRF-1507)
первичный ключ — uuid4 → визитов 0: дашборд «Сегодня» и «Расписание» пусты
при живых записях, счётчик деактивации отвечает «переносить нечего».
М-2 (DRF-2154) закрыл только свои ручки через ``specialist_keys``.

* h1 — узел: мастер с pk ≠ catalog_specialist_id и визитом по каталожному
  id → ``master_visits`` видит, дашборд показывает следующий визит и день,
  расписание рисует день с визитом; положительная пара — визит по pk у
  строки синка тоже виден; чужой каталожный id — не виден;
* h2 — ``master_visit_count`` / ``master_client_ids`` /
  ``_build_returning_customer_index`` — по обоим ключам;
* h3 — ``master_deactivation``: живая запись по каталожному id считается
  (касание admin_api вне master_api — одна строка);
* h4 — ``specialist_keys``: pk == catalog → один ключ; пусто → один; разные → два;
* h5 — AST-гард класса: любой ``filter/exclude/get/Q(... specialist_id=…)``
  или ``specialist_id__in=…`` вне tests обязан брать значение из
  ``specialist_keys(...)`` или стоять в списке исключений с причиной.
"""

from __future__ import annotations

import ast
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.catalog.specialist_ref import specialist_keys
from apps.identity.models import BotUser
from apps.master_api.services import visit_source
from apps.master_api.services.dashboard import build_dashboard
from apps.master_api.services.schedule import _build_returning_customer_index
from apps.master_api.tests.conftest import make_master
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

REPO_ROOT = Path(__file__).resolve().parents[3]


# ─── фикстуры ───────────────────────────────────────────────────────────────


@pytest.fixture
def solo_master(tenant: Tenant, bot_user: BotUser) -> CatalogMaster:
    """Соло/склеенный мастер: pk — uuid4, каталожный id — другой (DRF-1933)."""

    master = make_master(
        tenant,
        name="Соло Мастер",
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        invite_token=None,
        expires_in_days=None,
        linked_bot_user=bot_user,
    )
    master.catalog_specialist_id = uuid.uuid4()
    master.save(update_fields=["catalog_specialist_id"])
    assert master.catalog_specialist_id != master.id
    return master


@pytest.fixture
def customer(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="424242",
        display_name="Ксения",
        client_name="Ксения Хмель",
        chat_id="424242",
    )


def _mirror_row(
    tenant: Tenant,
    specialist_id: uuid.UUID,
    *,
    start: datetime,
    minutes: int = 60,
    status: str = "confirmed",
    bot_user: BotUser | None = None,
) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=specialist_id,
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        status=status,
        bot_user=bot_user,
    )


def _today_at(tenant: Tenant, hour: int) -> datetime:
    """Сегодня по часам салона, ``hour:00`` — визит попадает в «Сегодня»."""

    tz = ZoneInfo(tenant.timezone)
    local_today = dj_timezone.now().astimezone(tz).date()
    return datetime.combine(local_today, datetime.min.time(), tzinfo=tz).replace(hour=hour)


# ─── h1: узел — визит по каталожному id виден ───────────────────────────────


class TestSoloMasterSeesTheirDay:
    def test_master_visits_finds_the_row_keyed_on_the_catalog_id(
        self, tenant, bot_user, solo_master, customer
    ):
        by_catalog = _mirror_row(
            tenant,
            solo_master.catalog_specialist_id,
            start=_today_at(tenant, 23),
            bot_user=customer,
        )
        rows = visit_source.master_visits(solo_master)
        assert [r.id for r in rows] == [str(by_catalog.appointment_id)]
        assert rows[0].client_name == "Ксения Хмель"

    def test_a_sync_row_keyed_on_the_pk_is_still_found(self, tenant, bot_user, accepted_master):
        """Положительная пара: у строки синка pk == catalog id — как раньше."""

        assert accepted_master.catalog_specialist_id == accepted_master.id
        by_pk = _mirror_row(tenant, accepted_master.id, start=_today_at(tenant, 23))
        assert [r.id for r in visit_source.master_visits(accepted_master)] == [
            str(by_pk.appointment_id)
        ]

    def test_someone_elses_catalog_id_is_not_mine(self, tenant, bot_user, solo_master):
        mine = _mirror_row(tenant, solo_master.catalog_specialist_id, start=_today_at(tenant, 22))
        _mirror_row(tenant, uuid.uuid4(), start=_today_at(tenant, 23))
        assert [r.id for r in visit_source.master_visits(solo_master)] == [str(mine.appointment_id)]

    def test_dashboard_shows_the_solo_masters_next_visit(
        self, tenant, bot_user, solo_master, customer
    ):
        """Красное листа: «Сегодня» соло-мастера был пуст при живой записи."""

        start = dj_timezone.now() + timedelta(hours=1)
        row = _mirror_row(tenant, solo_master.catalog_specialist_id, start=start, bot_user=customer)
        with tenant_scope(tenant):
            snapshot = build_dashboard(solo_master, dj_timezone.now())
        payload = snapshot.to_dict()
        assert payload["next_visit"] is not None, payload
        assert payload["next_visit"]["booking_id"] == str(row.appointment_id)
        assert payload["next_visit"]["client_first_name"] == "Ксения"

    def test_schedule_shows_the_solo_masters_day(self, tenant, bot_user, solo_master, customer):
        from apps.master_api.services.schedule import build_schedule

        now = dj_timezone.now()
        start = now + timedelta(hours=2)
        row = _mirror_row(tenant, solo_master.catalog_specialist_id, start=start, bot_user=customer)
        tz = ZoneInfo(tenant.timezone)
        with tenant_scope(tenant):
            payload = build_schedule(
                solo_master,
                from_date=(now - timedelta(days=1)).astimezone(tz).date(),
                to_date=(now + timedelta(days=1)).astimezone(tz).date(),
                now=now,
            )
        bookings = [b for day in payload.days for b in day.bookings]
        assert [b.booking_id for b in bookings] == [str(row.appointment_id)]


# ─── h2: счётчики и индексы ─────────────────────────────────────────────────


class TestCountsAndIndexesUseBothKeys:
    def test_visit_count_and_client_ids(self, tenant, bot_user, solo_master, customer):
        now = dj_timezone.now()
        _mirror_row(
            tenant,
            solo_master.catalog_specialist_id,
            start=now - timedelta(days=20),
            status="completed",
            bot_user=customer,
        )
        _mirror_row(
            tenant,
            solo_master.id,
            start=now - timedelta(days=10),
            status="completed",
            bot_user=customer,
        )
        assert visit_source.master_visit_count(solo_master, bot_user_id=customer.id) == 2
        assert visit_source.master_client_ids(solo_master) == [customer.id]

    def test_returning_customer_index(self, tenant, bot_user, solo_master, customer):
        now = dj_timezone.now()
        for days in (30, 15):
            _mirror_row(
                tenant,
                solo_master.catalog_specialist_id,
                start=now - timedelta(days=days),
                status="completed",
                bot_user=customer,
            )
        assert _build_returning_customer_index(solo_master, [customer.id]) == {customer.id}


# ─── h3: деактивация (admin_api, одна строка) ───────────────────────────────


class TestDeactivationCountsLiveBookings:
    def test_a_live_booking_keyed_on_the_catalog_id_counts(self, tenant, bot_user, solo_master):
        from apps.admin_api.services import master_deactivation as mod

        _mirror_row(
            tenant,
            solo_master.catalog_specialist_id,
            start=dj_timezone.now() + timedelta(days=1),
        )
        func_name = _mirror_counter_name(Path(mod.__file__).read_text(encoding="utf-8"))
        count = getattr(mod, func_name)(solo_master)
        assert count == 1


def _mirror_counter_name(source: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and "RemoteBookingProxy" in ast.unparse(node):
            if "specialist_keys" in ast.unparse(node):
                return node.name
    raise AssertionError("master_deactivation: счётчик по зеркалу не найден")


# ─── h4: резолвер ───────────────────────────────────────────────────────────


class TestSpecialistKeys:
    def test_sync_row_has_one_key(self, tenant, bot_user, accepted_master):
        assert specialist_keys(accepted_master) == [accepted_master.id]

    def test_empty_catalog_id_has_one_key(self, tenant, bot_user, accepted_master):
        accepted_master.catalog_specialist_id = None
        assert specialist_keys(accepted_master) == [accepted_master.id]

    def test_solo_row_has_both(self, tenant, bot_user, solo_master):
        assert specialist_keys(solo_master) == [solo_master.id, solo_master.catalog_specialist_id]


# ─── h5: AST-гард класса ────────────────────────────────────────────────────

#: Запросы к зеркалу по ``specialist_id`` не через ``specialist_keys`` —
#: каждый с причиной. Пусто: причин нет. Запись без причины — красное.
MIRROR_KEY_EXCEPTIONS: dict[tuple[str, int], str] = {}

#: Сколько вызовов через ``specialist_keys`` гард обязан видеть — фактическое
#: число после правки (visit_source ×3, schedule, master_deactivation,
#: bookings ×2), не «≥1»: пустой скан не читается как «нарушителей нет».
EXPECTED_RESOLVED_SITES = 7

_QUERY_ATTRS = {"filter", "exclude", "get"}
_KEYS = {"specialist_id", "specialist_id__in"}


def _is_specialist_keys_call(value: ast.expr) -> bool:
    if isinstance(value, ast.Call):
        name = getattr(value.func, "id", getattr(value.func, "attr", ""))
        return name == "specialist_keys"
    return False


def _mirror_key_sites() -> list[tuple[str, int, bool]]:
    """(файл, строка, через резолвер) для каждого ``specialist_id[__in]=`` в
    ``filter/exclude/get`` или ``Q(...)`` вне tests/migrations.

    Предел гарда (назван, не спрятан): он видит только ключевой аргумент
    в этих четырёх формах. Слепые зоны — распаковка ``**{"specialist_id": …}``,
    имя ключа, собранное в переменную (``.filter(**kw)``), ``related__specialist_id``
    через связь, и ``.values_list``/``.annotate`` без фильтра. Такой обход
    — осознанный, и его увидит ревью, не гард.
    """

    found: list[tuple[str, int, bool]] = []
    for path in sorted((REPO_ROOT / "apps").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_query = isinstance(func, ast.Attribute) and func.attr in _QUERY_ATTRS
            is_q = isinstance(func, ast.Name) and func.id == "Q"
            if not (is_query or is_q):
                continue
            for kw in node.keywords:
                if kw.arg in _KEYS:
                    found.append((rel, node.lineno, _is_specialist_keys_call(kw.value)))
    return found


class TestMirrorReadersGoThroughSpecialistKeys:
    def test_the_scan_sees_the_class(self):
        """Положительная стража: гард видит ровно столько переведённых мест,
        сколько есть; меньше — гард ослеп, больше — список устарел."""

        resolved = Counter(rel for rel, _l, ok in _mirror_key_sites() if ok)
        assert sum(resolved.values()) == EXPECTED_RESOLVED_SITES, dict(resolved)

    def test_every_site_resolves_or_is_named(self):
        unresolved = {(rel, line) for rel, line, ok in _mirror_key_sites() if not ok}
        named = set(MIRROR_KEY_EXCEPTIONS)
        assert unresolved == named, {
            "по pk, не через specialist_keys и не названы": sorted(unresolved - named),
            "названы, но уже переведены (сузьте список)": sorted(named - unresolved),
        }

    def test_exceptions_carry_a_reason(self):
        for key, reason in MIRROR_KEY_EXCEPTIONS.items():
            assert isinstance(reason, str) and len(reason.strip()) >= 20, key

    def test_guard_catches_a_planted_site(self, tmp_path, monkeypatch):
        """Гард не декорация: подсаженный ``filter(specialist_id=master.id)``
        под ``apps/`` он находит."""

        planted_root = tmp_path / "apps" / "planted"
        planted_root.mkdir(parents=True)
        (planted_root / "reader.py").write_text(
            "def f(master):\n"
            "    return RemoteBookingProxy.all_tenants.filter(specialist_id=master.id)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "apps.master_api.tests.test_visit_source_specialist_keys_2185.REPO_ROOT", tmp_path
        )
        sites = _mirror_key_sites()
        assert sites == [("apps/planted/reader.py", 2, False)]
