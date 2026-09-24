"""Подметальщик добивает привязку к каталогу — и умеет вовремя замолчать (DRF-2379).

Предмет — ``apps.catalog.tasks.link_unlinked_salon_masters``.

### Зачем он вообще

Приглашение зовёт каталог сразу и **не падает**, когда тот недоступен. Значит
кто-то обязан вернуться к таким строкам — иначе «привязка происходит сама»
держится на том, что сеть не подводит, а она подводит.

### Предел — срок, а не число попыток

Счётчику попыток негде жить, кроме кэша, а кэш теряется при перезапуске:
предел, который сам себя обнуляет, пределом не является. Поэтому граница —
``SALON_CATALOG_LINK_DEADLINE_DAYS`` от ``invited_at``.

**Что проверяется про «долго недоступный каталог»** — не то, что журнал
молчит, а то, что молчание наступает **после громкой строки** и **при живом
видимом состоянии**: строка остаётся без ``catalog_specialist_id``, то есть
``catalog_unlinked`` для студии. Тишина в журнале при видимом состоянии на
экране — это не молчание системы.

### Названный предел охвата

Берутся только строки с ``invited_at``. У ``mode=catalog_only`` и у
приехавшей синком даты рождения нет (``synced_at`` — ``auto_now``, он про
последнее касание), а срок нельзя отсчитать от того, чего нет. Узел на это
есть: предел проверен, а не подразумевается.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.catalog.services import http_client as http_client_mod
from apps.catalog.services.http_client import (
    CatalogSalonSpecialistDoorAbsent,
    CatalogSalonSpecialistRefused,
    CatalogTransportError,
    ProvisionedSalonSpecialistDTO,
)
from apps.catalog.tasks import link_unlinked_salon_masters
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class _Door:
    """Подмена каталога: считает вызовы, отвечает заданным исходом.

    ``outcomes`` — очередь исходов по вызовам; когда она кончается,
    отвечает успехом. Так узел про «тик остановился» может задать две
    контурные неудачи подряд и убедиться, что до третьей строки не дошло.
    """

    def __init__(self, outcomes: list[BaseException] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcomes = list(outcomes or [])

    def __enter__(self) -> "_Door":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_salon_specialist(self, **kwargs: Any) -> ProvisionedSalonSpecialistDTO:
        self.calls.append(kwargs)
        if self.outcomes:
            raise self.outcomes.pop(0)
        return ProvisionedSalonSpecialistDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            specialist_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            status="draft",
            created=True,
        )


@pytest.fixture
def salon(db: Any) -> Tenant:
    return Tenant.objects.create(slug="link-sweep-2379", name="Салон подметальщика")


def _master(salon: Tenant, *, invited_ago: timedelta | None, **extra: Any) -> CatalogMaster:
    now = timezone.now()
    return CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=None,
        external_updated_at=now,
        name=f"Мастер {uuid.uuid4().hex[:6]}",
        is_active=False,
        invited_at=None if invited_ago is None else now - invited_ago,
        **extra,
    )


def _door(monkeypatch: pytest.MonkeyPatch, outcomes: list[BaseException] | None = None) -> _Door:
    door = _Door(outcomes)
    monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: door)
    return door


class TestПодметальщикДобивает:
    def test_непривязанная_строка_получает_ключ(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        master = _master(salon, invited_ago=timedelta(hours=3))
        _door(monkeypatch)

        counters = link_unlinked_salon_masters()

        master.refresh_from_db(fields=["catalog_specialist_id"])
        assert master.catalog_specialist_id is not None
        assert counters["linked"] == 1

    def test_уже_привязанную_строку_не_берёт(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Иначе подметальщик ходил бы в каталог за тем, что уже есть."""
        _master(
            salon,
            invited_ago=timedelta(hours=3),
            catalog_specialist_id=uuid.uuid4(),
        )
        door = _door(monkeypatch)

        counters = link_unlinked_salon_masters()

        assert counters["candidates"] == 0
        assert door.calls == []

    def test_архивную_строку_не_берёт(self, salon: Tenant, monkeypatch: pytest.MonkeyPatch) -> None:
        _master(salon, invited_ago=timedelta(hours=3), archived_at=timezone.now())
        door = _door(monkeypatch)

        link_unlinked_salon_masters()

        assert door.calls == []


class TestСрокЗаканчиваетсяГромкоИОдинРаз:
    def test_за_сроком_строка_больше_не_берётся(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        days = settings.SALON_CATALOG_LINK_DEADLINE_DAYS
        _master(salon, invited_ago=timedelta(days=days + 1))
        door = _door(monkeypatch)

        counters = link_unlinked_salon_masters()

        assert counters["candidates"] == 0
        assert door.calls == [], "срок вышел — в каталог больше не ходим"

    def test_о_наступлении_срока_сказано_громко(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Одна строка ERROR — это и есть ответ «что будет, когда каталог лежит долго»."""
        days = settings.SALON_CATALOG_LINK_DEADLINE_DAYS
        master = _master(salon, invited_ago=timedelta(days=days, minutes=30))
        _door(monkeypatch)

        with caplog.at_level(logging.ERROR, logger="apps.catalog.tasks"):
            counters = link_unlinked_salon_masters()

        assert counters["expired"] == 1
        loud = [r for r in caplog.records if "deadline_passed" in r.getMessage()]
        assert len(loud) == 1
        assert str(master.pk) in loud[0].getMessage()

    def test_громко_ровно_один_раз_а_не_каждый_час(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Окно громкой строки равно такту, поэтому строка попадает в него однажды.

        Без этого узла «одна строка» держалась бы на слове: строка, у
        которой срок истёк неделю назад, кричала бы каждый час, и операторы
        научились бы не читать этот журнал.
        """
        days = settings.SALON_CATALOG_LINK_DEADLINE_DAYS
        _master(salon, invited_ago=timedelta(days=days + 5))
        _door(monkeypatch)

        with caplog.at_level(logging.ERROR, logger="apps.catalog.tasks"):
            counters = link_unlinked_salon_masters()

        assert counters["expired"] == 0
        assert [r for r in caplog.records if "deadline_passed" in r.getMessage()] == []

    def test_состояние_остаётся_видимым_и_после_срока(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Молчит журнал, а не система: ``catalog_unlinked`` никуда не делся."""
        days = settings.SALON_CATALOG_LINK_DEADLINE_DAYS
        master = _master(salon, invited_ago=timedelta(days=days + 3))
        _door(monkeypatch)

        link_unlinked_salon_masters()

        master.refresh_from_db(fields=["catalog_specialist_id"])
        assert master.catalog_specialist_id is None


class TestНазванныйПределОхвата:
    def test_строка_без_даты_приглашения_не_берётся(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Срок нельзя отсчитать от того, чего нет — и это сказано вслух.

        ``synced_at`` в роли даты рождения не годится: он ``auto_now`` и
        меняется при каждом касании строки, так что «возраст» по нему всегда
        был бы нулевым, а предел — фикцией.
        """
        _master(salon, invited_ago=None)
        door = _door(monkeypatch)

        counters = link_unlinked_salon_masters()

        assert counters["candidates"] == 0
        assert door.calls == []


class TestКонтурнаяПричинаОстанавливаетТик:
    def test_две_подряд_останавливают_обход(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Каталог лежит — ходить остальными строками трата, а не настойчивость."""
        for _ in range(4):
            _master(salon, invited_ago=timedelta(hours=1))
        door = _door(
            monkeypatch,
            [CatalogTransportError("down"), CatalogTransportError("down")],
        )

        counters = link_unlinked_salon_masters()

        assert counters["candidates"] == 4
        assert len(door.calls) == 2, "третью строку не трогали"
        assert counters["stopped"] == 1

    def test_отказ_про_строку_обход_не_останавливает(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``conflict:`` — про эту строку; следующая может пройти.

        Останавливаться на нём значило бы, что одна больная строка держит
        весь салон непривязанным.
        """
        for _ in range(3):
            _master(salon, invited_ago=timedelta(hours=1))
        door = _door(
            monkeypatch,
            [
                CatalogSalonSpecialistRefused("nope", reason="claim_bound_elsewhere"),
                CatalogSalonSpecialistRefused("nope", reason="claim_bound_elsewhere"),
            ],
        )

        counters = link_unlinked_salon_masters()

        assert len(door.calls) == 3
        assert counters["stopped"] == 0
        assert counters["linked"] == 1

    def test_отсутствие_двери_тоже_останавливает_тик(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """До слияния каталожной половины подметальщик не молотит впустую."""
        for _ in range(4):
            _master(salon, invited_ago=timedelta(hours=1))
        door = _door(
            monkeypatch,
            [
                CatalogSalonSpecialistDoorAbsent("no route"),
                CatalogSalonSpecialistDoorAbsent("no route"),
            ],
        )

        counters = link_unlinked_salon_masters()

        assert len(door.calls) == 2
        assert counters["stopped"] == 1


class TestОднаСтрокаНеВалитТик:
    def test_неожиданная_ошибка_не_мешает_следующей(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _master(salon, invited_ago=timedelta(hours=2))
        _master(salon, invited_ago=timedelta(hours=1))
        door = _door(monkeypatch, [RuntimeError("совсем неожиданное")])

        counters = link_unlinked_salon_masters()

        assert len(door.calls) == 2
        assert counters["linked"] == 1
        assert counters["unavailable"] == 1


class TestПодметальщикСтоитВРасписании:
    """Без этого узла его могла бы убрать посторонняя правка — молча."""

    def test_задача_запланирована(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["catalog_link_unlinked_masters_hourly"]
        assert entry["task"] == "apps.catalog.tasks.link_unlinked_salon_masters"

    def test_такт_часовой_потому_что_на_нём_держится_одна_громкая_строка(self) -> None:
        """Такт несущий, а не косметический: окно истёкшего срока равно ему.

        Поменяв такт и не поменяв окно, мы получили бы либо молчание о
        наступившем сроке, либо крик о нём каждый тик.
        """
        entry = settings.CELERY_BEAT_SCHEDULE["catalog_link_unlinked_masters_hourly"]
        assert entry["schedule"].hour == set(range(24))
        assert len(entry["schedule"].minute) == 1

    def test_минута_не_делится_с_теми_кто_тоже_ходит_в_ayla(self) -> None:
        mine = settings.CELERY_BEAT_SCHEDULE["catalog_link_unlinked_masters_hourly"]
        others = (
            "catalog_sync_every_15min",
            "catalog_sync_staleness_hourly",
            "schedule_confirmation_sweep_every_15min",
        )
        for key in others:
            assert not (
                mine["schedule"].minute & settings.CELERY_BEAT_SCHEDULE[key]["schedule"].minute
            ), key


class TestГромкийПроходНеЗависитОтОстановки:
    def test_срок_прокричан_даже_когда_тик_остановился(
        self, salon: Tenant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Проходов два, и громкий идёт первым — иначе строка молчала бы навсегда.

        Красный до разделения проходов: рабочий проход останавливается по
        контурной причине, и строка, чьё часовое окно пришлось на этот тик,
        своего единственного крика не получала — а следующий тик её окно уже
        не захватывал.
        """
        days = settings.SALON_CATALOG_LINK_DEADLINE_DAYS
        _master(salon, invited_ago=timedelta(days=days, minutes=30))
        for _ in range(3):
            _master(salon, invited_ago=timedelta(hours=1))
        _door(monkeypatch, [CatalogTransportError("down"), CatalogTransportError("down")])

        with caplog.at_level(logging.ERROR, logger="apps.catalog.tasks"):
            counters = link_unlinked_salon_masters()

        assert counters["stopped"] == 1, "тик действительно остановился"
        assert counters["expired"] == 1
        assert [r for r in caplog.records if "deadline_passed" in r.getMessage()]
