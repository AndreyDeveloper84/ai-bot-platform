"""Приглашение мастера само привязывает его к каталогу — и не падает, если не вышло (DRF-2379).

Предмет — ``apps/admin_api/views_invite.py::_link_to_catalog``, вызов после
коммита. Соседний ``test_invite.py`` держит сам эндпоинт (право, проверки,
идемпотентность, аудит); здесь — только привязка, второго стенда на
приглашение не заводится.

### Что было и что стало

Решение владельца §77 п.27 (24.09): привязка мастера к каталогу должна
происходить **сама**, когда салон заводит мастера. До этого листа она была
тремя шагами в двух системах с двумя правами: профиль специалиста заводился
руками в админке каталога, ключ приезжал синком, действие повторялось в
админке бота.

### Два свойства, и второе важнее первого

1. приглашение **привязывает**: у строки появляется
   ``catalog_specialist_id`` — тот ключ, без которого часы отвечали 403;
2. приглашение **не падает**, что бы ни ответил каталог. Это свойство
   ВЫКЛАДКИ: каталожная половина уезжает отдельным PR, и до её слияния
   каталог такой ручки не знает вовсе. Мастер обязан завестись всё равно,
   а состояние ``catalog_unlinked`` студия видит и сейчас
   (``apps/admin_api/services/salon_readiness.py``).

Второе проверяется **тремя** разными несчастьями, а не одним: «ручки нет»,
«каталог отказал» и «внутри всё сломалось». Один стенд доказал бы, что
обёрнут один конкретный случай, а нужно — что обёрнуты все.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.catalog.models import CatalogMaster
from apps.catalog.services import http_client as http_client_mod
from apps.catalog.services.http_client import (
    CatalogSalonSpecialistDoorAbsent,
    CatalogSalonSpecialistRefused,
    ProvisionedSalonSpecialistDTO,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SPECIALIST_ID = uuid.UUID("d0a20600-0000-4000-8000-000000002379")


class _DoorAnswers:
    """Каталог знает ручку и отвечает подтверждённым id."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_DoorAnswers":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_salon_specialist(self, **kwargs: Any) -> ProvisionedSalonSpecialistDTO:
        self.calls.append(kwargs)
        return ProvisionedSalonSpecialistDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            specialist_id=SPECIALIST_ID,
            user_id=uuid.uuid4(),
            status="draft",
            created=True,
        )


class _DoorRaises:
    """Каталог отвечает несчастьем — каким именно, задаёт узел."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def __enter__(self) -> "_DoorRaises":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_salon_specialist(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.error


def _invite_url() -> str:
    return reverse("admin_api:master_invite_create")


def _body(name: str = "Лера Салонная") -> dict[str, Any]:
    return {
        "name": name,
        "contact_method": "max_username",
        "contact_value": f"@{uuid.uuid4().hex[:10]}",
        "services": [],
        "schedule_preset": "default_mon_fri_10_19",
        "mode": "invite",
    }


def _invite(client: Client, owner_id: str = "5001", **body: Any):  # noqa: ANN003, ANN202
    return client.post(
        _invite_url(),
        data={**_body(), **body},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(owner_id),
    )


@pytest.fixture
def client() -> Client:
    return Client()


class TestПриглашениеПривязываетСамо:
    def test_у_заведённого_мастера_появился_каталожный_ключ(
        self,
        client: Client,
        tenant: Tenant,
        owner_bot_user: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        door = _DoorAnswers()
        monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: door)

        resp = _invite(client)

        assert resp.status_code == 201
        master = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert master.catalog_specialist_id == SPECIALIST_ID

    def test_каталог_позван_один_раз_и_за_нужный_салон(
        self,
        client: Client,
        tenant: Tenant,
        owner_bot_user: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        door = _DoorAnswers()
        monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: door)

        resp = _invite(client)

        assert len(door.calls) == 1
        assert str(door.calls[0]["tenant_id"]) == str(tenant.id)
        assert door.calls[0]["external_user_id"] == f"bot:master:{resp.json()['master_id']}"


class TestПриглашениеНеПадаетНиОтЧего:
    """Свойство выкладки: каталожная половина уезжает отдельным PR."""

    @pytest.mark.parametrize(
        ("error", "case"),
        [
            (CatalogSalonSpecialistDoorAbsent("no such route"), "ручки ещё нет"),
            (
                CatalogSalonSpecialistRefused("refused", reason="tenant_not_found"),
                "каталог отказал",
            ),
            (RuntimeError("что-то совсем неожиданное"), "внутри всё сломалось"),
        ],
        ids=["door_absent", "refused", "unexpected"],
    )
    def test_мастер_заводится_несмотря_на(
        self,
        client: Client,
        tenant: Tenant,
        owner_bot_user: Any,
        monkeypatch: pytest.MonkeyPatch,
        error: BaseException,
        case: str,
    ) -> None:
        door = _DoorRaises(error)
        monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: door)

        resp = _invite(client)

        assert resp.status_code == 201, case
        master = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert master.catalog_specialist_id is None
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_строка_мастера_не_откатывается_вместе_с_походом_в_каталог(
        self,
        client: Client,
        tenant: Tenant,
        owner_bot_user: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Именно поэтому вызов стоит ПОСЛЕ коммита, а не внутри ``atomic``.

        Внутри транзакции обрыв сети унёс бы вместе с собой человека,
        которого салон только что завёл: салон не смог бы принять мастера
        из-за того, что недоступна чужая система.
        """
        door = _DoorRaises(CatalogSalonSpecialistDoorAbsent("no such route"))
        monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: door)
        before = CatalogMaster.all_tenants.filter(tenant=tenant).count()

        _invite(client)

        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == before + 1
        assert door.calls == 1, "каталог всё-таки был позван — молчания нет"

    def test_приглашение_с_привязкой_и_без_отвечает_одинаково(
        self,
        client: Client,
        tenant: Tenant,
        owner_bot_user: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ответ экрану не зависит от того, дошли ли мы до каталога.

        Иначе «привязка не удалась» просочилась бы во владельца салона как
        непонятная ошибка вместо состояния, которое студия уже показывает.
        """
        monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: _DoorAnswers())
        linked = _invite(client)

        monkeypatch.setattr(
            http_client_mod,
            "CatalogHttpClient",
            lambda: _DoorRaises(CatalogSalonSpecialistDoorAbsent("no such route")),
        )
        unlinked = _invite(client)

        assert linked.status_code == unlinked.status_code == 201
        assert set(linked.json()) == set(unlinked.json())
