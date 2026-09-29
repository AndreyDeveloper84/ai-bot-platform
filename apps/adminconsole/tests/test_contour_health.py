"""Экран здоровья контура видит то, что сломалось молча (DRF-1500).

04.09.2026 каталог отставал двенадцать дней — 94 услуги в зеркале
против 265 в бэкенде, ноль маникюров против четырнадцати, — и нашлось
это потому, что владелец вспомнил про маникюр. Экран обязан кричать на
таком состоянии; исторический случай здесь воспроизводится буквально.

**Отрицательное утверждение не живёт без парной положительной стражи
на тех же данных** (negative_assert_guard, DRF-1411): каждое «метки
нет» стоит рядом с «на этих же данных экран посмотрел и показал» —
«свежий каталог не помечен» зеленеет и на пустой странице, поэтому
рядом доказывается, что салон на странице есть.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.adminconsole import health
from apps.adminconsole.health import collect_report, handoff_summary, surface_flags
from apps.adminconsole.tests.conftest import make_client_thread
from apps.catalog.models import CatalogMaster, CatalogService
from apps.handoff.models import AdminTask
from apps.tenancy.models import Tenant

HEALTH_URL = "/admin/health/"


class FakeCounter:
    """Бэкенд без сети: отдаёт заранее заданные полные числа."""

    def __init__(self, *, services: int | None, masters: int | None) -> None:
        self._services = services
        self._masters = masters

    def count(self, *, tenant_id: str, resource: str) -> int | None:
        return self._services if resource == "services" else self._masters


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001
    Tenant.objects.all().delete()
    return Tenant.objects.create(slug="formula-tela", name="Формула Тела")


def _sync_age(tenant: Tenant, **delta) -> None:
    tenant.last_catalog_sync_ok_at = timezone.now() - timedelta(**delta)
    tenant.save(update_fields=["last_catalog_sync_ok_at"])


def _mirror_services(tenant: Tenant, count: int) -> None:
    moment = timezone.now()
    CatalogService.all_tenants.bulk_create(
        CatalogService(
            tenant=tenant,
            slug=f"svc-{i}",
            name=f"Услуга {i}",
            external_updated_at=moment,
        )
        for i in range(count)
    )


def _mirror_masters(tenant: Tenant, count: int) -> None:
    moment = timezone.now()
    CatalogMaster.all_tenants.bulk_create(
        CatalogMaster(tenant=tenant, name=f"Мастер {i}", external_updated_at=moment)
        for i in range(count)
    )


def _admitted_masters(tenant: Tenant, count: int, *, linked: bool) -> None:
    """Мастера, принятые салоном; ``linked`` — есть ли канонический ключ Ayla.

    Без ключа строка административно в полном порядке и всё равно не
    продаётся (``ayla_unlinked``, DRF-1540) — это форма, в которой
    ``solo_onboarding`` создаёт соло-мастера.
    """
    moment = timezone.now()
    CatalogMaster.all_tenants.bulk_create(
        CatalogMaster(
            tenant=tenant,
            name=f"Мастер {i}",
            external_updated_at=moment,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
            ayla_user_id=uuid.uuid4() if linked else None,
            accepted_at=moment if linked else None,
        )
        for i in range(count)
    )


def _view_with_counter(monkeypatch: pytest.MonkeyPatch, client: Client, counter: FakeCounter):  # noqa: ANN202
    monkeypatch.setattr(health, "build_default_counter", lambda: counter)
    return client.get(HEALTH_URL)


class TestCatalogFreshness:
    """Отставание больше порога видно; свежий каталог отметки не получает."""

    def test_stale_catalog_is_flagged(self, salon: Tenant) -> None:
        _sync_age(salon, hours=25)

        report = collect_report(counter=FakeCounter(services=0, masters=0))

        assert [a.slug for a in report.catalog_ages] == ["formula-tela"]
        assert report.catalog_ages[0].is_stale
        assert any("formula-tela" in p and "не синхронизировался" in p for p in report.problems)

    def test_fresh_catalog_is_not_flagged(self, salon: Tenant) -> None:
        _sync_age(salon, minutes=5)

        report = collect_report(counter=FakeCounter(services=0, masters=0))

        # Стража присутствия: отчёт посмотрел на салон, прежде чем промолчать.
        assert [a.slug for a in report.catalog_ages] == ["formula-tela"]
        assert not report.catalog_ages[0].is_stale
        assert report.problems == []

    def test_stale_mark_on_screen(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, hours=25)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=0))
        page = response.content.decode()

        assert response.status_code == 200
        assert "formula-tela" in page
        assert "ОТСТАЁТ" in page

    def test_fresh_mark_on_screen(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=0))
        page = response.content.decode()

        # Парная положительная стража: салон на экране есть, пометки нет.
        assert "formula-tela" in page
        assert "свежий" in page
        assert "ОТСТАЁТ" not in page


class TestMirrorDivergence:
    """Расхождение зеркала с источником считается и показывается числом."""

    def test_divergence_numbers_on_screen(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        _mirror_services(salon, 3)
        _mirror_masters(salon, 1)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=265, masters=14))
        page = response.content.decode()

        assert "formula-tela" in page
        assert "265" in page  # бэкенд
        assert ">3</td>" in page  # зеркало
        assert ">262</span>" in page  # зазор услуг
        assert ">13</span>" in page  # зазор мастеров
        assert "расходится с бэкендом" in page

    def test_matching_counts_are_quiet(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        _mirror_services(salon, 3)
        _mirror_masters(salon, 1)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=3, masters=1))
        page = response.content.decode()

        # Стража присутствия: числа посчитаны и показаны, тревоги нет.
        assert "formula-tela" in page
        assert ">3</td>" in page
        assert "расходится с бэкендом" not in page

    def test_unqueried_upstream_is_labelled_not_zero(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Бэкенд молчит → экран помечает источник, а не рисует нули."""
        _sync_age(salon, minutes=5)
        _mirror_services(salon, 3)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=None, masters=None))
        page = response.content.decode()

        # Стража присутствия: числа зеркала показаны.
        assert "formula-tela" in page
        assert ">3</td>" in page
        assert "Бэкенд не опрошен" in page
        assert "расходится с бэкендом" not in page


class TestTenantVisibility:
    """Салон, принявший мастеров и не показывающий клиенту никого, кричит.

    Пятый молчаливый сбой (DRF-1540). Он не ловится ни одним из четырёх
    предыдущих: синхронизация свежая, зеркало сходится с бэкендом строка
    в строку — гейт продажи стоит ПОСЛЕ зеркала, и до чисел источника
    ему дела нет. Поэтому оба теста ниже держат остальные сигналы
    зелёными: если бы кричал сосед, крик читался бы как чужой.
    """

    def test_admitted_but_unlinked_tenant_screams(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        _admitted_masters(salon, 2, linked=False)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=2))
        page = response.content.decode()

        assert "НЕВИДИМ ЦЕЛИКОМ" in page
        assert "клиент не увидит никого" in page
        # Причина, а не только число: оператору незачем искать её заново.
        assert "ayla_unlinked" in page
        # Соседние сигналы молчат — крик именно про видимость.
        assert "ОТСТАЁТ" not in page
        assert "расходится с бэкендом" not in page

    def test_one_linked_master_keeps_the_tenant_visible(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        _admitted_masters(salon, 1, linked=False)
        _admitted_masters(salon, 1, linked=True)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=2))
        page = response.content.decode()

        # Парная положительная стража: салон на экране есть, обе строки
        # посчитаны, и одна непроданная сама по себе не крик.
        assert "formula-tela" in page
        assert "ayla_unlinked" in page
        assert "виден" in page
        assert "НЕВИДИМ ЦЕЛИКОМ" not in page
        assert "клиент не увидит никого" not in page

    def test_empty_tenant_does_not_scream(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Незаполненный салон — не поломка; вечная краснота обесценила бы экран."""
        _sync_age(salon, minutes=5)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=0))
        page = response.content.decode()

        # Стража присутствия: салон на экране есть и помечен пустым.
        assert "formula-tela" in page
        assert "пуст" in page
        assert "НЕВИДИМ ЦЕЛИКОМ" not in page
        assert "Молчаливых сбоев не видно" in page


class TestHistoricalCase0409:
    """Состояние на 04.09: 12 дней отставания, 94 против 265 — кричит."""

    def test_twelve_days_and_94_of_265_screams(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, days=12)
        _mirror_services(salon, 94)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=265, masters=14))
        page = response.content.decode()

        assert "молчаливые сбои" in page
        assert "12d00h" in page
        assert "94" in page
        assert "265" in page
        assert ">171</span>" in page  # зазор услуг
        assert "ОТСТАЁТ" in page
        assert "расходится с бэкендом" in page

    def test_same_salon_healthy_is_calm(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Парная положительная: тот же салон в здоровом виде не кричит."""
        _sync_age(salon, minutes=10)
        _mirror_services(salon, 94)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=94, masters=0))
        page = response.content.decode()

        # Стража присутствия: салон и числа на экране есть.
        assert "formula-tela" in page
        assert "94" in page
        assert "Молчаливых сбоев не видно" in page
        assert "ОТСТАЁТ" not in page


class TestHandoffSummary:
    """Открытые обращения и возраст самого старого — сводкой, без содержимого."""

    def test_open_task_and_its_age(self, salon: Tenant) -> None:
        _, _, _, task = make_client_thread(
            salon,
            channel_user_id="u-1",
            display_name="Клиент",
            text="хочу записаться",
        )
        moment = timezone.now()
        AdminTask.all_tenants.filter(pk=task.pk).update(created_at=moment - timedelta(minutes=84))

        summary = handoff_summary(now=moment)

        assert summary.open_count == 1
        assert summary.oldest_open_age_seconds == pytest.approx(84 * 60, abs=5)
        assert summary.oldest_age_human == "1ч24м"

    def test_resolved_task_is_not_counted(self, salon: Tenant) -> None:
        _, _, _, task = make_client_thread(
            salon,
            channel_user_id="u-2",
            display_name="Клиент",
            text="вопрос решён",
        )
        task.status = AdminTask.Status.RESOLVED
        task.save(update_fields=["status"])

        summary = handoff_summary()

        # Стража присутствия: очередь опрошена, структура сводки на месте.
        assert summary.in_progress_count == 0
        assert summary.open_count == 0
        assert summary.oldest_open_age_seconds is None


class TestSleepingSurfaces:
    """Какие флаги выключены и что из-за этого недоступно."""

    def test_unset_flag_is_marked_unset(self, settings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NUTRITION_ENABLED", raising=False)
        settings.NUTRITION_ENABLED = False

        flags = {f.name: f for f in surface_flags()}

        assert flags["NUTRITION_ENABLED"].state_label == "не задана (по умолчанию выключена)"
        assert flags["NUTRITION_ENABLED"].consequence  # следствие названо

    def test_set_flag_is_marked_set(self, settings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUTRITION_ENABLED", "false")
        settings.NUTRITION_ENABLED = False

        flags = {f.name: f for f in surface_flags()}

        # Стража присутствия: флаг в реестре есть и читается.
        assert "NUTRITION_ENABLED" in flags
        assert flags["NUTRITION_ENABLED"].state_label == "выключена"

    def test_screen_shows_consequence_for_sleeping_surface(
        self, salon: Tenant, login_as, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        monkeypatch.delenv("NUTRITION_ENABLED", raising=False)
        settings.NUTRITION_ENABLED = False
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=0))
        page = response.content.decode()

        assert "NUTRITION_ENABLED" in page
        assert "не задана" in page
        assert "дневник" in page  # следствие: что недоступно

    def test_screen_does_not_dwell_on_awake_surface(
        self, salon: Tenant, login_as, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        monkeypatch.setenv("NUTRITION_ENABLED", "true")
        settings.NUTRITION_ENABLED = True
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=0))
        page = response.content.decode()

        # Парная положительная стража: флаг на экране есть, состояние иное —
        # следствие именно этой поверхности (слово «дневник») не показано.
        assert "NUTRITION_ENABLED" in page
        assert "включена" in page
        assert "дневник" not in page


class TestAccess:
    """Экран — страница админки: сотруднику обеих ролей доступен, остальным 403."""

    def test_anonymous_is_forbidden(self, db) -> None:
        response = Client().get(HEALTH_URL)

        assert response.status_code == 403
        assert response.content == b"forbidden"

    def test_non_staff_is_forbidden(self, db, django_user_model) -> None:
        user = django_user_model.objects.create_user(username="ne-sotrudnik", password="x")
        client = Client()
        client.force_login(user)

        response = client.get(HEALTH_URL)

        # Стража присутствия: вход состоялся, отказ именно в статусе.
        assert user.is_authenticated and not user.is_staff
        assert response.status_code == 403

    def test_viewer_role_sees_the_screen(
        self, salon: Tenant, login_as, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sync_age(salon, minutes=5)
        client = login_as("smotryashchiy", "viewer")

        response = _view_with_counter(monkeypatch, client, FakeCounter(services=0, masters=0))

        assert response.status_code == 200
        assert "Здоровье контура" in response.content.decode()
