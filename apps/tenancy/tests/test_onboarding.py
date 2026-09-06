"""Тесты экрана и пути подключения салона (DRF-1525).

Проверяемое требование: подключение салона — не создание строки. Путь
обязан отказать с объяснением (не тихо) там, где прежний ручной
``create_tenant`` рапортовал «успех», и назвать причину невидимости из
закрытого перечня DRF-1511 там, где салон клиенту не виден.

Каждому отрицательному утверждению — парная положительная стража на тех
же данных, впереди него (DRF-1411, negative_assert_guard).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.catalog.services.http_client import (
    CatalogSalonServiceDTO,
    CatalogSpecialistDTO,
    EdgeSnapshot,
)
from apps.catalog.services.sync import CatalogSyncService
from apps.tenancy.admin import SalonConnectForm
from apps.tenancy.models import Tenant
from apps.tenancy.onboarding import (
    REASON_CITY_MISSING,
    REASON_NEVER_SYNCED,
    REASON_NO_ACTIVE_SERVICES,
    REASON_NO_BOOKABLE_MASTERS,
    REASON_TENANT_INACTIVE,
    ConnectError,
    assess_salon,
    connect_salon,
)

pytestmark = pytest.mark.django_db

_AYLA_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


def _ts() -> datetime:
    return datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)


def _service(name: str = "Стрижка") -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=str(uuid.uuid4()),
        external_updated_at=_ts(),
        name=name,
    )


def _specialist(*, active: bool = True) -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=str(uuid.uuid4()),
        user_id=None,
        name="Анна",
        external_updated_at=_ts(),
        is_active=active,
    )


class FakeAylaHttp:
    """Тот же контракт, что у ``CatalogHttpClient``: три выборки + CM."""

    def __init__(
        self,
        *,
        services: list | None = None,
        specialists: list | None = None,
        raise_on_fetch: Exception | None = None,
    ) -> None:
        self._services = services or []
        self._specialists = specialists or []
        self._raise = raise_on_fetch

    def __enter__(self) -> FakeAylaHttp:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def fetch_salon_services(self, *, tenant_id: str) -> list:
        if self._raise is not None:
            raise self._raise
        return self._services

    def fetch_specialists(self, *, tenant_id: str) -> list:
        return self._specialists

    def fetch_specialist_services(self, *, tenant_id: str) -> EdgeSnapshot:
        return EdgeSnapshot(edges=[])


def _connect(http: FakeAylaHttp, **overrides):
    kwargs = {
        "slug": "mednyy-kovsh",
        "name": "Медный ковш",
        "tenant_id": _AYLA_ID,
        "city": "Пенза",
        "http_client": http,
    }
    kwargs.update(overrides)
    return connect_salon(**kwargs)


# ---------------------------------------------------------------------------
# Отказы — с объяснением, не тихие
# ---------------------------------------------------------------------------


class TestRefusalsAreLoud:
    def test_connect_without_tenant_id_refused(self):
        """Салон без идентификатора → отказ с объяснением, строки нет."""
        http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        with pytest.raises(ConnectError, match="не задан"):
            _connect(http, tenant_id=None)

        # Парная положительная стража на тех же данных: тот же вызов с
        # идентификатором создаёт строку — отказ был именно в нём.
        result = _connect(http)
        assert result.tenant.slug == "mednyy-kovsh"
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1

    def test_connect_with_malformed_id_refused(self):
        http = FakeAylaHttp(services=[_service()])
        with pytest.raises(ConnectError, match="не является UUID"):
            _connect(http, tenant_id="formula-tela")

        result = _connect(http)
        assert result.tenant.id == uuid.UUID(_AYLA_ID)
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1

    def test_connect_with_id_unknown_to_ayla_refused(self):
        """По UUID ничего нет в Ayla → отказ ДО сохранения, строки нет."""
        http = FakeAylaHttp(services=[], specialists=[])
        with pytest.raises(ConnectError, match="ни одной услуги"):
            _connect(http)

        # Те же данные, но Ayla знает идентификатор → строка создаётся.
        http_live = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        result = _connect(http_live)
        assert result.probe.services == 1
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1

    def test_connect_when_ayla_unreachable_refused(self):
        """Бэкенд не отвечает → вслепую не подключаем."""
        http = FakeAylaHttp(raise_on_fetch=ConnectionError("down"))
        with pytest.raises(ConnectError, match="Не удалось проверить"):
            _connect(http)

        result = _connect(FakeAylaHttp(services=[_service()]))
        assert result.tenant.slug == "mednyy-kovsh"
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1

    def test_connect_duplicate_id_refused(self):
        other = Tenant.objects.create(id=uuid.UUID(_AYLA_ID), slug="uzhe-zanyat", name="Уже занят")
        http = FakeAylaHttp(services=[_service()])
        with pytest.raises(ConnectError, match="уже занят"):
            _connect(http)

        # Парная стража: свободный идентификатор на том же slug создаётся.
        result = _connect(http, tenant_id=str(uuid.uuid4()))
        assert result.tenant.slug == "mednyy-kovsh"
        assert Tenant.all_objects.filter(slug=other.slug).count() == 1

    def test_connect_existing_slug_refused(self):
        _connect(FakeAylaHttp(services=[_service()]))
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1
        with pytest.raises(ConnectError, match="уже подключён"):
            _connect(FakeAylaHttp(services=[_service()]))
        # Повторное подключение не создало вторую строку и не переключило id.
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1
        assert Tenant.all_objects.get(slug="mednyy-kovsh").id == uuid.UUID(_AYLA_ID)


# ---------------------------------------------------------------------------
# Исход глазами клиента
# ---------------------------------------------------------------------------


class TestOutcomeIsClientVisibility:
    def test_happy_path_salon_is_visible(self):
        """Полный путь: строка, id, город, синхронизация, бронируемый мастер."""
        http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        result = _connect(http, sync_service=CatalogSyncService(http_client=http))

        assert result.sync_error is None
        assert result.assessment.active_services == 1
        assert result.assessment.bookable_masters == 1
        assert result.assessment.reasons == ()
        assert result.assessment.is_visible is True

    def test_salon_without_bookable_masters_reason_named(self):
        """Услуги есть, мастер приехал неактивным → причина названа.

        Такой салон не появляется в поиске вовсе — это безопасно, но
        обязано быть объяснено, а не выглядеть сбоем (DRF-1511).
        """
        http = FakeAylaHttp(services=[_service()], specialists=[_specialist(active=False)])
        result = _connect(http, sync_service=CatalogSyncService(http_client=http))

        # Стражи присутствия на тех же данных: витрина и мастер доехали.
        assert result.assessment.active_services == 1
        assert result.tenant.last_catalog_sync_ok_at is not None
        assert REASON_NO_BOOKABLE_MASTERS in result.assessment.reasons
        assert result.assessment.is_visible is False

        # Парная положительная: с активным мастером той же витрины причина уходит.
        http_ok = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        ok = _connect(http_ok, slug="drugoy-salon", tenant_id=str(uuid.uuid4()))
        assert ok.assessment.bookable_masters == 1
        assert REASON_NO_BOOKABLE_MASTERS not in ok.assessment.reasons

    def test_sync_failure_is_not_connection_failure(self):
        """«Каталог не доехал» ≠ «салон не подключён» (HTTP 429 и т.п.)."""
        # Проверка идентификатора прошла, а у синхронизации свой клиент,
        # который падает — модель rate-limit Ayla на прогоне.
        probe_http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        failing_sync = CatalogSyncService(
            http_client=FakeAylaHttp(raise_on_fetch=ConnectionError("429"))
        )
        result = _connect(probe_http, sync_service=failing_sync)

        assert result.tenant.slug == "mednyy-kovsh"  # строка осталась
        assert result.sync_error is not None
        # Причина невидимости названа честно — синхронизация не проходила.
        assert REASON_NEVER_SYNCED in result.assessment.reasons


# ---------------------------------------------------------------------------
# Карточка состояния — закрытый перечень причин
# ---------------------------------------------------------------------------


def _tenant(**overrides) -> Tenant:
    kwargs = {"slug": "karta", "name": "Карточка", "city": "Пенза"}
    kwargs.update(overrides)
    return Tenant.objects.create(**kwargs)


class TestAssessSalon:
    def test_never_synced_named(self):
        tenant = _tenant()
        assert tenant.last_catalog_sync_ok_at is None
        result = assess_salon(tenant)
        assert REASON_NEVER_SYNCED in result.reasons
        assert result.is_visible is False

    def test_city_missing_named(self):
        tenant = _tenant(city="", last_catalog_sync_ok_at=_ts())
        assert tenant.city == ""
        result = assess_salon(tenant)
        assert REASON_CITY_MISSING in result.reasons

    def test_inactive_named(self):
        tenant = _tenant(is_active=False, last_catalog_sync_ok_at=_ts())
        assert tenant.is_active is False
        result = assess_salon(tenant)
        assert REASON_TENANT_INACTIVE in result.reasons

    def test_no_active_services_named(self):
        tenant = _tenant(last_catalog_sync_ok_at=_ts())
        # Синхронизация проходила, а услуг нет — это отдельная причина.
        assert tenant.last_catalog_sync_ok_at is not None
        result = assess_salon(tenant)
        assert result.active_services == 0
        assert REASON_NO_ACTIVE_SERVICES in result.reasons

    def test_healthy_salon_has_no_reasons(self):
        """Парная положительная: полностью здоровый салон виден клиенту."""
        http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        result = _connect(http, sync_service=CatalogSyncService(http_client=http))
        result = assess_salon(result.tenant)
        assert result.active_services == 1
        assert result.bookable_masters == 1
        assert result.reasons == ()
        assert result.is_visible is True

    def test_existing_salons_keep_every_service(self):
        """Подключение нового салона не трогает витрины уже подключённых.

        Парная положительная проверка задачи — числом: после работы
        существующие салоны не потеряли ни одной услуги.
        """
        existing_http = FakeAylaHttp(
            services=[_service("А"), _service("Б"), _service("В")],
            specialists=[_specialist()],
        )
        existing = _connect(
            existing_http,
            slug="pervyy-salon",
            tenant_id=str(uuid.uuid4()),
            sync_service=CatalogSyncService(http_client=existing_http),
        )
        before = assess_salon(existing.tenant)
        assert before.active_services == 3

        new_http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        _connect(new_http)

        after = assess_salon(Tenant.all_objects.get(slug="pervyy-salon"))
        assert after.active_services == before.active_services
        assert after.bookable_masters == before.bookable_masters


# ---------------------------------------------------------------------------
# Экран
# ---------------------------------------------------------------------------


class TestConnectScreen:
    def test_form_has_no_secret_fields(self):
        """На форме подключения нет и не появится токенов/секретов (DRF-1495)."""
        fields = set(SalonConnectForm().fields)
        assert fields == {"slug", "name", "tenant_id", "city"}

    def test_view_requires_superuser(self, django_user_model):
        staff = django_user_model.objects.create_user(username="smotryashchiy", is_staff=True)
        client = Client()
        client.force_login(staff)
        url = reverse("admin:tenancy_tenant_connect")

        denied = client.get(url)
        assert denied.status_code == 403

        # Парная положительная: суперпользователю экран открывается.
        root = django_user_model.objects.create_superuser(username="vladelets")
        client.force_login(root)
        allowed = client.get(url)
        assert allowed.status_code == 200
        content = allowed.content.decode()
        assert "Подключить салон" in content
