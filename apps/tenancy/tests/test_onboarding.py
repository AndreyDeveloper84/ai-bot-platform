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
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import (
    CatalogProvisioningRefused,
    CatalogProvisioningTokenMissing,
    CatalogSalonServiceDTO,
    CatalogSlugTaken,
    CatalogSpecialistDTO,
    EdgeSnapshot,
    EnsuredTenantDTO,
)
from apps.catalog.services.sync import CatalogSyncService
from apps.tenancy.admin import SalonConnectForm
from apps.tenancy.models import Tenant
from apps.tenancy.onboarding import (
    REASON_CITY_MISSING,
    REASON_NEVER_SYNCED,
    REASON_NO_ACTIVE_SERVICES,
    REASON_MASTERS_AWAIT_VERIFICATION,
    REASON_NO_BOOKABLE_MASTERS,
    REASON_TENANT_INACTIVE,
    PENDING_PROVISIONING_REFUSED,
    PENDING_PROVISIONING_TOKEN_MISSING,
    ConnectError,
    ConnectPending,
    ConnectResult,
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
    # ``user_id`` заполнен, потому что так выглядит боевая строка. DRF-1540
    # (#1413) сделал связь с каноническим пользователем Ayla условием
    # продаваемости: мастер без неё не показывается клиенту, потому что
    # уведомление о записи до него не дойдёт. Замер контура 06.09.2026 —
    # 31 бронируемый мастер, ни одного без ``ayla_user_id``.
    #
    # ``None`` здесь стоял с DRF-1525 и был законной формой провода
    # (``CatalogSpecialistDTO.user_id`` объявлен ``str | None``), но не той
    # формой, которую отдаёт Ayla. После #1413 фикстура начала рисовать
    # салон, который не может быть виден клиенту, и три теста видимости
    # стали падать на ``dev``.
    return CatalogSpecialistDTO(
        ayla_master_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        name="Анна",
        external_updated_at=_ts(),
        is_active=active,
    )


def _verify_synced_masters(tenant: Tenant) -> int:
    """Пройти за оператора ручную верификацию — как действие в админке.

    DRF-1496: мастер, приехавший синхронизацией, рождается ``pending`` и
    клиенту не продаётся, пока оператор не подтвердит её вручную.
    Приглашения ей никто не слал, и прежнее умолчание ``accepted`` было
    неправдой — ровно тот дефект, ради которого задача заведена.

    Следствие для подключения салона: сразу после ``connect_salon``
    бронируемых мастеров ноль, и ``no_bookable_masters`` — честный
    ответ, а не сбой. Поэтому тесты видимости проходят этот шаг явно и
    видимость получают им, а не побочным эффектом умолчания.

    Ходит через ``save``, а не ``update``: это ровно то, что делает
    действие ``verify_masters`` в ``apps/catalog/admin.py``.
    """

    verified = 0
    for master in CatalogMaster.all_tenants.filter(tenant=tenant):
        master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        master.save(update_fields=["invite_status"])
        verified += 1
    return verified


class FakeAylaHttp:
    """Тот же контракт, что у ``CatalogHttpClient``: три выборки + CM.

    DRF-1525 (11.09.2026): плюс ``ensure_tenant`` — «салон по slug» в
    каталоге. По умолчанию отвечает «найден, id = ``_AYLA_ID``»;
    ``ensure_created=True`` — «заведён» (201), ``raise_on_ensure`` —
    названный отказ каталога, ``ensure_id`` — какой UUID вернуть.
    """

    def __init__(
        self,
        *,
        services: list | None = None,
        specialists: list | None = None,
        raise_on_fetch: Exception | None = None,
        ensure_id: str = _AYLA_ID,
        ensure_created: bool = False,
        raise_on_ensure: Exception | None = None,
    ) -> None:
        self._services = services or []
        self._specialists = specialists or []
        self._raise = raise_on_fetch
        self._ensure_id = ensure_id
        self._ensure_created = ensure_created
        self._raise_on_ensure = raise_on_ensure
        self.ensure_calls: list[dict[str, str]] = []

    def ensure_tenant(self, *, slug: str, name: str, city: str = "") -> EnsuredTenantDTO:
        self.ensure_calls.append({"slug": slug, "name": name, "city": city})
        if self._raise_on_ensure is not None:
            raise self._raise_on_ensure
        return EnsuredTenantDTO(
            id=uuid.UUID(self._ensure_id),
            slug=slug,
            name=name,
            city=city or None,
            is_active=True,
            created=self._ensure_created,
        )

    def __enter__(self) -> FakeAylaHttp:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch_salon_services(self, *, tenant_id: str) -> list:
        if self._raise is not None:
            raise self._raise
        return self._services

    def fetch_specialists(self, *, tenant_id: str) -> list:
        return self._specialists

    def fetch_specialist_services(self, *, tenant_id: str) -> EdgeSnapshot:
        return EdgeSnapshot(edges=[])


def _connect(
    http: FakeAylaHttp,
    *,
    slug: str = "mednyy-kovsh",
    name: str = "Медный ковш",
    city: str = "Пенза",
    sync_service: Any | None = None,
) -> ConnectResult:
    return connect_salon(
        slug=slug,
        name=name,
        city=city,
        http_client=http,
        sync_service=sync_service,
    )


# ---------------------------------------------------------------------------
# Отказы — с объяснением, не тихие
# ---------------------------------------------------------------------------


class TestRefusalsAreLoud:
    def test_the_uuid_comes_from_ayla_not_from_the_operator(self):
        """Главное утверждение DRF-1525 (11.09.2026): UUID не вводится.

        Строка получает первичный ключ, который вернула Ayla по slug, —
        и ровно с теми slug/название/город, что были на форме.
        """
        http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])

        result = _connect(http)

        assert result.tenant.id == uuid.UUID(_AYLA_ID)
        assert http.ensure_calls == [
            {"slug": "mednyy-kovsh", "name": "Медный ковш", "city": "Пенза"}
        ]
        assert result.created_in_ayla is False
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1

    def test_a_salon_created_in_ayla_by_this_press_is_connected_empty(self):
        """Салона в Ayla не было → заведён (201) и подключён.

        Мастеров и услуг у него нет по определению, и прежний отказ «по
        UUID ничего нет» здесь был бы отказом заводить салоны вообще.
        Пустота названа причиной невидимости, а не отказом.
        """
        http = FakeAylaHttp(ensure_created=True)

        result = _connect(http, sync_service=CatalogSyncService(http_client=http))

        assert result.created_in_ayla is True
        assert result.tenant.id == uuid.UUID(_AYLA_ID)
        assert REASON_NO_ACTIVE_SERVICES in result.assessment.reasons
        assert result.assessment.is_visible is False

    def test_missing_token_on_our_side_is_pending_not_error_and_no_row(self):
        """§11 свода: «отсутствие токена — понятный SETUP_PENDING, не ложный успех»."""
        http = FakeAylaHttp(raise_on_ensure=CatalogProvisioningTokenMissing("empty"))

        with pytest.raises(ConnectPending) as info:
            _connect(http)

        assert info.value.blocked_by == PENDING_PROVISIONING_TOKEN_MISSING
        assert not isinstance(info.value, ConnectError)
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0

        # Парная положительная стража на тех же данных: с ответившей Ayla
        # тот же вызов создаёт строку — остановка была именно в токене.
        result = _connect(FakeAylaHttp(services=[_service()]))
        assert result.tenant.slug == "mednyy-kovsh"

    def test_refused_by_ayla_is_pending_with_its_own_name(self):
        """403 от каталога — другой контейнер, другое имя причины."""
        http = FakeAylaHttp(raise_on_ensure=CatalogProvisioningRefused("403"))

        with pytest.raises(ConnectPending) as info:
            _connect(http)

        assert info.value.blocked_by == PENDING_PROVISIONING_REFUSED
        assert info.value.blocked_by != PENDING_PROVISIONING_TOKEN_MISSING
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0

    def test_slug_taken_in_ayla_by_another_name_is_refused_naming_it(self):
        """409 → отказ с именем занявшего, строки нет (требование главного окна)."""
        http = FakeAylaHttp(
            raise_on_ensure=CatalogSlugTaken(
                "409",
                slug="mednyy-kovsh",
                existing_name="Медный Ковшъ",
                requested_name="Медный ковш",
            )
        )

        with pytest.raises(ConnectError, match="Медный Ковшъ"):
            _connect(http)

        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0

        # Парная положительная: то же название, что в Ayla, — салон найден
        # и подключён.
        result = _connect(FakeAylaHttp(services=[_service()]), name="Медный Ковшъ")
        assert result.tenant.name == "Медный Ковшъ"

    def test_connect_when_ayla_unreachable_refused(self):
        """Бэкенд не отвечает → вслепую не подключаем, строки нет."""
        http = FakeAylaHttp(raise_on_ensure=ConnectionError("down"))
        with pytest.raises(ConnectError, match="Не удалось найти или завести"):
            _connect(http)
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0

        result = _connect(FakeAylaHttp(services=[_service()]))
        assert result.tenant.slug == "mednyy-kovsh"
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1

    def test_connect_duplicate_id_refused(self):
        """Ayla вернула UUID, который здесь уже держит другой slug."""
        other = Tenant.objects.create(id=uuid.UUID(_AYLA_ID), slug="uzhe-zanyat", name="Уже занят")
        http = FakeAylaHttp(services=[_service()])
        with pytest.raises(ConnectError, match="уже занят"):
            _connect(http)
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0

        # Парная стража: свободный идентификатор на том же slug создаётся.
        result = _connect(FakeAylaHttp(services=[_service()], ensure_id=str(uuid.uuid4())))
        assert result.tenant.slug == "mednyy-kovsh"
        assert Tenant.all_objects.filter(slug=other.slug).count() == 1

    def test_connect_existing_slug_refused(self):
        _connect(FakeAylaHttp(services=[_service()]))
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 1
        second = FakeAylaHttp(services=[_service()])
        with pytest.raises(ConnectError, match="уже подключён"):
            _connect(second)
        # Локальный отказ — ДО похода в Ayla: заводить салон в источнике
        # ради того, чтобы тут же отказать, нельзя.
        assert second.ensure_calls == []
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
        # DRF-1496: синхронизация приводит мастера в ``pending``, и до
        # ручной верификации салон клиенту не виден. Это поведение
        # задачи: приглашения этому мастеру никто не слал.
        #
        # DRF-1553 назвал этот подслучай отдельно: причина «ждут
        # верификации», а не общее «нет бронируемых мастеров». Разница
        # несущая — на первой экран даёт кнопку, на второй предлагать
        # нечего.
        assert result.assessment.bookable_masters == 0
        assert REASON_MASTERS_AWAIT_VERIFICATION in result.assessment.reasons

        # Верификация оператором — и тот же салон на тех же данных
        # становится видимым. Парная положительная стража к отрицанию
        # выше: «ноль» получен состоянием приглашения, а не сломанной
        # синхронизацией.
        assert _verify_synced_masters(result.tenant) == 1
        verified = assess_salon(result.tenant)
        assert verified.active_services == 1
        assert verified.bookable_masters == 1
        assert verified.reasons == ()
        assert verified.is_visible is True

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
        http_ok = FakeAylaHttp(
            services=[_service()], specialists=[_specialist()], ensure_id=str(uuid.uuid4())
        )
        ok = _connect(http_ok, slug="drugoy-salon")
        # DRF-1496: активного мастера мало — его ещё надо верифицировать.
        assert _verify_synced_masters(ok.tenant) == 1
        ok_verified = assess_salon(ok.tenant)
        assert ok_verified.bookable_masters == 1
        assert REASON_NO_BOOKABLE_MASTERS not in ok_verified.reasons

    def test_sync_failure_is_not_connection_failure(self):
        """«Каталог не доехал» ≠ «салон не подключён» (HTTP 429 и т.п.)."""
        # Салон в Ayla нашёлся, а у синхронизации свой клиент, который
        # падает — модель rate-limit Ayla на прогоне.
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
        connected = _connect(http, sync_service=CatalogSyncService(http_client=http))
        # DRF-1496: до верификации причина названа, а не молчит.
        # DRF-1553: и названа подслучаем — «ждут верификации».
        assert REASON_MASTERS_AWAIT_VERIFICATION in connected.assessment.reasons

        assert _verify_synced_masters(connected.tenant) == 1
        result = assess_salon(connected.tenant)
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
            ensure_id=str(uuid.uuid4()),
        )
        existing = _connect(
            existing_http,
            slug="pervyy-salon",
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
    def test_form_has_no_secret_fields_and_no_uuid(self):
        """На форме нет токенов/секретов (DRF-1495) и нет поля UUID.

        Владелец 11.09.2026: «человек UUID не вводит и не видит». Ровно
        три поля — и это утверждение, а не наблюдение: четвёртое поле
        вернуло бы ввод ключа на глаз.
        """
        fields = set(SalonConnectForm().fields)
        assert fields == {"slug", "name", "city"}

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

    def test_409_from_ayla_shows_the_reason_and_creates_no_row(
        self, django_user_model, monkeypatch
    ):
        """Требование главного окна: каталог ответил 409 → форма показывает
        причину, строка не создана. Через настоящий экран и шаблон, а не
        через функцию: то, что видит оператор, и есть предмет."""
        monkeypatch.setattr(
            "apps.catalog.services.http_client.CatalogHttpClient",
            lambda **_kw: FakeAylaHttp(
                raise_on_ensure=CatalogSlugTaken(
                    "409",
                    slug="mednyy-kovsh",
                    existing_name="Медный Ковшъ",
                    requested_name="Медный ковш",
                )
            ),
        )
        client = Client()
        client.force_login(django_user_model.objects.create_superuser(username="vladelets"))

        page = client.post(
            reverse("admin:tenancy_tenant_connect"),
            {"slug": "mednyy-kovsh", "name": "Медный ковш", "city": "Пенза"},
        )

        assert page.status_code == 200
        content = page.content.decode()
        assert "Подключение отклонено" in content
        assert "Медный Ковшъ" in content
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0
        # Форма осталась на экране — оператор правит название и жмёт снова.
        assert 'name="slug"' in content

    def test_pending_is_shown_as_pending_not_as_error(self, django_user_model, monkeypatch):
        """SETUP_PENDING на экране — своим именем, не «отклонено»."""
        monkeypatch.setattr(
            "apps.catalog.services.http_client.CatalogHttpClient",
            lambda **_kw: FakeAylaHttp(raise_on_ensure=CatalogProvisioningRefused("403")),
        )
        client = Client()
        client.force_login(django_user_model.objects.create_superuser(username="vladelets"))

        page = client.post(
            reverse("admin:tenancy_tenant_connect"),
            {"slug": "mednyy-kovsh", "name": "Медный ковш", "city": "Пенза"},
        )

        content = page.content.decode()
        assert "SETUP_PENDING" in content
        assert PENDING_PROVISIONING_REFUSED in content
        assert "Подключение отклонено" not in content
        assert Tenant.all_objects.filter(slug="mednyy-kovsh").count() == 0

    def test_the_result_screen_does_not_print_the_uuid(self, django_user_model, monkeypatch):
        """«Не вводит и не видит»: UUID на экране итога не печатается."""
        http = FakeAylaHttp(services=[_service()], specialists=[_specialist()])
        monkeypatch.setattr(
            "apps.catalog.services.http_client.CatalogHttpClient", lambda **_kw: http
        )
        client = Client()
        client.force_login(django_user_model.objects.create_superuser(username="vladelets"))

        page = client.post(
            reverse("admin:tenancy_tenant_connect"),
            {"slug": "mednyy-kovsh", "name": "Медный ковш", "city": "Пенза"},
        )

        content = page.content.decode()
        assert "найден в Ayla и подключён" in content
        assert Tenant.all_objects.get(slug="mednyy-kovsh").id == uuid.UUID(_AYLA_ID)
        # Ссылка на карточку несёт id в URL — это адрес, а не сведение;
        # проверяется ТЕКСТ экрана: UUID не показан как значение.
        assert f"<code>{_AYLA_ID}</code>" not in content
