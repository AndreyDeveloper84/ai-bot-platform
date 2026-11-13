"""Принятый мастер салона доезжает до продажи: синк находит строку приглашения.

Путь салона: «Добавить человека» заводит ``CatalogMaster`` с ``uuid4`` в ``id``
и сразу привязывает его к каталогу — id профиля каталога ложится в
``catalog_specialist_id``. ``ayla_user_id`` у строки пуст, пока личность не
связана. До правки синк искал строку по ``pk`` и по ``ayla_user_id``, не
находил, заводил вторую и падал на ``uq_catalog_master_tenant_catalog_specialist_id``
— каждый удар, с ошибочной причиной ``held_by_other_tenant`` (пилот 13.11:
81 ошибка за сутки), а строка мастера так и не получала данных каталога.

Здесь три узла:

* синк находит строку приглашения по ``catalog_specialist_id``;
* связь личности (приём, подметальщик, команда) записывает ``ayla_user_id``
  только в пустое поле;
* подметальщик, дозаведший id каталога мастеру, который УЖЕ принял
  приглашение, тут же связывает его личность.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from django.utils import timezone as dj_timezone
from pytest_httpx import HTTPXMock

from apps.catalog.identity import remember_catalog_user_id
from apps.catalog.master_state import ACCEPTED
from apps.catalog.models import CatalogMaster
from apps.catalog.services import http_client as http_client_mod
from apps.catalog.services.http_client import (
    CatalogSpecialistDTO,
    ProvisionedSalonSpecialistDTO,
)
from apps.catalog.services.upserter import upsert_specialists
from apps.catalog.tasks import link_unlinked_salon_masters
from apps.identity.models import BotUser
from apps.identity.services.specialist_identity_link import ACTOR_LINK_SWEEP
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BASE = "https://ayla.test"
AYLA_USER = "d0a1c2d3-0000-4000-8000-00000000a111"


@pytest.fixture
def salon(db: Any) -> Tenant:
    return Tenant.objects.create(slug="salon-invite-sync", name="Салон приглашений")


@pytest.fixture
def person(salon: Tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id="7700101")


def _invite_row(salon: Tenant, **extra: Any) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=salon,
        external_updated_at=dj_timezone.now(),
        name="Мастер (приглашение)",
        is_active=False,
        invited_at=dj_timezone.now() - timedelta(hours=2),
        **extra,
    )


def _dto(mid: str, *, user_id: str | None) -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=mid,
        user_id=user_id,
        name="Мастер из каталога",
        external_updated_at=datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc),
        tenant=None,
        raw={"id": mid},
    )


class TestСинкНаходитСтрокуПриглашения:
    def test_строка_с_id_каталога_обновляется_а_не_дублируется(self, salon: Tenant) -> None:
        specialist = str(uuid.uuid4())
        row = _invite_row(salon, catalog_specialist_id=specialist)

        result = upsert_specialists(salon, [_dto(specialist, user_id=str(uuid.uuid4()))])

        assert result.errors == []
        assert result.updated == 1 and result.created == 0
        assert CatalogMaster.all_tenants.filter(tenant=salon).count() == 1
        row.refresh_from_db()
        assert row.name == "Мастер из каталога"
        assert str(row.catalog_specialist_id) == specialist

    def test_повторный_удар_тоже_без_ошибок(self, salon: Tenant) -> None:
        specialist = str(uuid.uuid4())
        _invite_row(salon, catalog_specialist_id=specialist)
        dto = _dto(specialist, user_id=str(uuid.uuid4()))

        upsert_specialists(salon, [dto])
        result = upsert_specialists(salon, [dto])

        assert result.errors == []
        assert CatalogMaster.all_tenants.filter(tenant=salon).count() == 1

    def test_пустой_пользователь_в_фиде_не_стирает_записанный_связью(self, salon: Tenant) -> None:
        specialist = str(uuid.uuid4())
        row = _invite_row(
            salon, catalog_specialist_id=specialist, ayla_user_id=uuid.UUID(AYLA_USER)
        )

        result = upsert_specialists(salon, [_dto(specialist, user_id=None)])

        assert result.errors == []
        row.refresh_from_db()
        assert str(row.ayla_user_id) == AYLA_USER


class TestСвязьЗапоминаетПользователяКаталога:
    def test_пустое_поле_заполняется(self, salon: Tenant) -> None:
        row = _invite_row(salon, catalog_specialist_id=uuid.uuid4())

        assert remember_catalog_user_id(row, AYLA_USER) is True

        row.refresh_from_db()
        assert str(row.ayla_user_id) == AYLA_USER

    def test_непустое_не_перезаписывается(self, salon: Tenant) -> None:
        own = uuid.uuid4()
        row = _invite_row(salon, catalog_specialist_id=uuid.uuid4(), ayla_user_id=own)

        assert remember_catalog_user_id(row, AYLA_USER) is False

        row.refresh_from_db()
        assert row.ayla_user_id == own

    def test_дубль_в_салоне_не_пишется_и_не_роняет(self, salon: Tenant) -> None:
        _invite_row(salon, ayla_user_id=uuid.UUID(AYLA_USER))
        row = _invite_row(salon, catalog_specialist_id=uuid.uuid4())

        assert remember_catalog_user_id(row, AYLA_USER) is False

        row.refresh_from_db()
        assert row.ayla_user_id is None


class _ProvisionDoor:
    def __enter__(self) -> "_ProvisionDoor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_salon_specialist(self, **kwargs: Any) -> ProvisionedSalonSpecialistDTO:
        return ProvisionedSalonSpecialistDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            specialist_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            status="draft",
            created=True,
        )


class TestПодметальщикСвязываетПринявшего:
    @pytest.fixture(autouse=True)
    def _catalog(self, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        settings.AYLA_BASE_URL = BASE
        settings.AYLA_INTERNAL_API_TOKEN = "general-bearer-sweep"  # noqa: S105
        settings.AYLA_SPECIALIST_IDENTITY_LINK_TOKEN = "identity-secret-sweep"  # noqa: S105
        # Заведение профиля — подменой; дверь связи личности — проводом.
        real = http_client_mod.CatalogHttpClient
        door = _ProvisionDoor()

        class _Client(real):  # type: ignore[misc, valid-type]
            def provision_salon_specialist(self, **kwargs: Any) -> Any:
                return door.provision_salon_specialist(**kwargs)

        monkeypatch.setattr(http_client_mod, "CatalogHttpClient", _Client)

    def test_принявший_мастер_получает_связь_и_ключ(
        self, salon: Tenant, person: BotUser, httpx_mock: HTTPXMock
    ) -> None:
        row = _invite_row(salon, linked_bot_user=person, invite_status=ACCEPTED)
        httpx_mock.add_response(
            method="POST",
            json={
                "data": {
                    "specialist_id": str(uuid.uuid4()),
                    "ayla_user_id": AYLA_USER,
                    "status": "created",
                }
            },
            status_code=201,
        )

        counters = link_unlinked_salon_masters()

        assert counters["linked"] == 1
        assert counters["identity_linked"] == 1
        row.refresh_from_db()
        assert row.catalog_specialist_id is not None
        assert str(row.ayla_user_id) == AYLA_USER
        request = httpx_mock.get_requests()[0]
        assert str(row.catalog_specialist_id) in str(request.url)
        assert ACTOR_LINK_SWEEP.encode() in request.content

    def test_непринятое_приглашение_личность_не_связывает(
        self, salon: Tenant, httpx_mock: HTTPXMock
    ) -> None:
        _invite_row(salon)

        counters = link_unlinked_salon_masters()

        assert counters["linked"] == 1
        assert counters["identity_linked"] == 0
        assert httpx_mock.get_requests() == []

    def test_отказ_связи_не_роняет_тик(
        self, salon: Tenant, person: BotUser, httpx_mock: HTTPXMock
    ) -> None:
        row = _invite_row(salon, linked_bot_user=person, invite_status=ACCEPTED)
        httpx_mock.add_response(
            method="POST",
            json={
                "error": {
                    "code": "SPECIALIST_IDENTITY_LINK_REFUSED",
                    "message": "x",
                    "details": {"reason": "identity_already_bound"},
                }
            },
            status_code=409,
        )

        counters = link_unlinked_salon_masters()

        assert counters["linked"] == 1
        assert counters["identity_refused"] == 1
        row.refresh_from_db()
        assert row.catalog_specialist_id is not None
        assert row.ayla_user_id is None

    def test_временный_отказ_повторяется_следующим_тиком(
        self, salon: Tenant, person: BotUser, httpx_mock: HTTPXMock
    ) -> None:
        """Иначе после привязки строка уходила из выборки, и отказ становился вечным."""
        row = _invite_row(salon, linked_bot_user=person, invite_status=ACCEPTED)
        httpx_mock.add_response(
            method="POST",
            json={
                "error": {
                    "code": "SPECIALIST_IDENTITY_LINK_REFUSED",
                    "message": "x",
                    "details": {"reason": "rate_limited"},
                }
            },
            status_code=429,
        )
        first = link_unlinked_salon_masters()
        httpx_mock.add_response(
            method="POST",
            json={
                "data": {
                    "specialist_id": str(uuid.uuid4()),
                    "ayla_user_id": AYLA_USER,
                    "status": "created",
                }
            },
            status_code=201,
        )

        second = link_unlinked_salon_masters()

        assert first["identity_refused"] == 1
        assert second["identity_linked"] == 1
        row.refresh_from_db()
        assert str(row.ayla_user_id) == AYLA_USER

    def test_непредвиденная_ошибка_связи_не_роняет_тик(
        self, salon: Tenant, person: BotUser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.identity.services import specialist_identity_link as link_mod

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(link_mod, "bind_master_identity_in_catalog", _boom)
        _invite_row(salon, linked_bot_user=person, invite_status=ACCEPTED)
        _invite_row(salon)

        counters = link_unlinked_salon_masters()

        assert counters["linked"] == 2
        assert counters["identity_refused"] == 1
