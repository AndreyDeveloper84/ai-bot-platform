"""Салонная дверь catalog identity: она есть, она идемпотентна, её отсутствие безопасно (DRF-2379).

Предмет — салонная ветвь ``apps.catalog.identity.ensure_catalog_specialist_identity``
(``_create_for_salon``). Соседний файл
``test_ensure_catalog_specialist_identity.py`` держит соло-путь и общие
ветви; второго стенда для того же предмета здесь не заводится — только
салонная дверь, которой до этого листа не существовало.

### Что доказывается

* дверь **есть**: строка салонного мастера получает ``catalog_specialist_id``,
  и он взят из **ответа** каталога, а не из того, что мы послали;
* claim — **ключ строки зеркала, не личность**: ``bot:master:<pk>``;
* **повтор после принятия приглашения не провижинит второй раз** — требование
  главного окна. Механизм (колонка полна → ``reuse`` замыкает первым
  условием) держится узлом, а не рассуждением;
* **отсутствие двери безопасно**: 404 без нашего тела читается как
  ``creation_unavailable`` — ровно то имя и то поведение, что были у
  салонного мастера ДО этого листа. Это свойство выкладки: пока каталожная
  половина не слита, ничего не ломается;
* отказы каталога приходят **по именам**, и они не те же, что «двери нет»;
* строка с соло-связью в салонную дверь **не уходит**: её тенант —
  соло-кабинет, каталог ответил бы ``tenant_is_solo``, и мы обменяли бы
  честное имя на чужое.

### Чего доказать нельзя

«В каталоге появилась ровно одна строка» — это утверждение о каталоге, и его
держат узлы каталожной половины (``tenants/tests/test_salon_specialist_*``).
Здесь доказывается то, чем владеет бот: сколько раз он позвал и что записал.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Any

import pytest

from apps.catalog import identity as identity_mod
from apps.catalog.identity import (
    REASON_CLIENT_ERROR,
    REASON_CREATION_UNAVAILABLE,
    REASON_REFUSED,
    REASON_TOKEN_MISSING,
    REASON_TRANSPORT_ERROR,
    SALON_CLAIM_PREFIX,
    CatalogIdentityUnavailable,
    ensure_catalog_specialist_identity,
    salon_provisioning_claim,
)
from apps.catalog.models import CatalogMaster
from apps.catalog.services import http_client as http_client_mod
from apps.catalog.services.http_client import (
    CatalogClientError,
    CatalogProvisioningRefused,
    CatalogProvisioningTokenMissing,
    CatalogSalonSpecialistDoorAbsent,
    CatalogSalonSpecialistRefused,
    CatalogTransportError,
    ProvisionedSalonSpecialistDTO,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Намеренно НЕ равен pk строки зеркала: узлы обязаны различать «записали
#: ответ каталога» и «записали то, что сами же придумали».
SPECIALIST_ID = uuid.UUID("d0a20600-0000-4000-8000-000000000379")


def _ts() -> datetime:
    return datetime(2026, 9, 24, 12, 0, tzinfo=dt_timezone.utc)


class _FakeDoor:
    """Подмена ``CatalogHttpClient`` для салонной ручки: считает и отвечает."""

    def __init__(self, outcome: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome

    def __enter__(self) -> "_FakeDoor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_salon_specialist(self, **kwargs: Any) -> ProvisionedSalonSpecialistDTO:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return ProvisionedSalonSpecialistDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            specialist_id=SPECIALIST_ID,
            user_id=uuid.uuid4(),
            status="draft",
            created=True,
        )


@pytest.fixture
def tenant(db: Any) -> Tenant:
    return Tenant.objects.create(slug="salon-door-2379", name="Салон 2379")


@pytest.fixture
def salon_master(tenant: Tenant) -> CatalogMaster:
    """Мастер, которого завёл салон: соло-связи нет и быть не должно."""
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-2379,
        external_updated_at=_ts(),
        name="Лера Салонная",
    )


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> _FakeDoor:
    fake = _FakeDoor()
    monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: fake)
    return fake


def _with_outcome(monkeypatch: pytest.MonkeyPatch, outcome: Exception) -> _FakeDoor:
    fake = _FakeDoor(outcome=outcome)
    monkeypatch.setattr(http_client_mod, "CatalogHttpClient", lambda: fake)
    return fake


class TestДверьЗаводитПрофильИПишетОтвет:
    def test_колонка_заполняется_id_из_ответа_каталога(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        identity = ensure_catalog_specialist_identity(salon_master)

        assert identity.specialist_id == str(SPECIALIST_ID)
        assert identity.created is True
        salon_master.refresh_from_db(fields=["catalog_specialist_id"])
        assert salon_master.catalog_specialist_id == SPECIALIST_ID

    def test_записан_ответ_каталога_а_не_первичный_ключ_строки(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        """Readback, а не «мы послали, значит получилось».

        Подставленный ``pk`` уходил бы в каталог и возвращался чужой ошибкой
        «мастер не найден» — ровно тот дефект, из-за которого завели
        ``catalog_specialist_id`` отдельной колонкой (DRF-1933).
        """
        ensure_catalog_specialist_identity(salon_master)

        salon_master.refresh_from_db(fields=["catalog_specialist_id"])
        assert salon_master.catalog_specialist_id != salon_master.pk

    def test_имя_мастера_едет_в_каталог_как_отображаемое(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        ensure_catalog_specialist_identity(salon_master)

        assert door.calls[0]["display_name"] == "Лера Салонная"
        assert str(door.calls[0]["tenant_id"]) == str(salon_master.tenant_id)


class TestClaimЭтоКлючСтрокиАНеЛичность:
    def test_claim_выведен_из_первичного_ключа_зеркала(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        ensure_catalog_specialist_identity(salon_master)

        assert door.calls[0]["external_user_id"] == f"{SALON_CLAIM_PREFIX}{salon_master.pk}"

    def test_одно_место_ответа_на_вопрос_как_он_пишется(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        """Подметальщик и приглашение зовут одну дверь — написание одно.

        Разойдясь в написании, они завели бы ДВУХ специалистов на одного
        человека: каталог идемпотентен по claim, и два разных claim — это
        два разных человека в его глазах.
        """
        ensure_catalog_specialist_identity(salon_master)

        assert door.calls[0]["external_user_id"] == salon_provisioning_claim(salon_master)

    def test_claim_не_зависит_от_того_принял_ли_мастер_приглашение(
        self, salon_master: CatalogMaster
    ) -> None:
        """На моменте заведения MAX-id ещё нет — ключ строки есть всегда."""
        before = salon_provisioning_claim(salon_master)
        salon_master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        salon_master.save(update_fields=["invite_status"])

        assert salon_provisioning_claim(salon_master) == before


class TestПовторНеПровижинитВторойРаз:
    """Требование главного окна: механизм держится узлом, а не рассуждением."""

    def test_второй_вызов_каталог_не_зовёт(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        ensure_catalog_specialist_identity(salon_master)
        ensure_catalog_specialist_identity(salon_master)

        assert len(door.calls) == 1, "колонка полна — reuse замыкает вызов первым условием"

    def test_после_принятия_приглашения_провижининга_не_происходит(
        self, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        """Личность приезжает позже и отдельно — второго ребра не появляется.

        Именно здесь была бы легко пропущенная ошибка: claim строки и claim
        человека разные, и наивный код провижинил бы второй раз «уже под
        настоящей личностью», заведя салону второго мастера.
        """
        ensure_catalog_specialist_identity(salon_master)
        first_id = CatalogMaster.all_tenants.get(pk=salon_master.pk).catalog_specialist_id

        salon_master.refresh_from_db()
        salon_master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        salon_master.save(update_fields=["invite_status"])
        identity = ensure_catalog_specialist_identity(salon_master)

        assert len(door.calls) == 1
        assert identity.created is False, "этот вызов провижининга не выполнял"
        assert identity.specialist_id == str(first_id)


class TestОтсутствиеДвериБезопасно:
    """Свойство ВЫКЛАДКИ: пока каталожная половина не слита, ничего не ломается."""

    def test_404_без_нашего_тела_это_creation_unavailable(
        self, salon_master: CatalogMaster, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_outcome(monkeypatch, CatalogSalonSpecialistDoorAbsent("no such route"))

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(salon_master)

        assert exc.value.reason == REASON_CREATION_UNAVAILABLE

    def test_колонка_остаётся_пустой_а_не_получает_мусор(
        self, salon_master: CatalogMaster, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_outcome(monkeypatch, CatalogSalonSpecialistDoorAbsent("no such route"))

        with pytest.raises(CatalogIdentityUnavailable):
            ensure_catalog_specialist_identity(salon_master)

        salon_master.refresh_from_db(fields=["catalog_specialist_id"])
        assert salon_master.catalog_specialist_id is None

    def test_третьего_поведения_не_заведено(
        self, salon_master: CatalogMaster, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Двери нет» отвечает тем же именем, каким салонный мастер отвечал ДО листа.

        Три поведения там, где нужно два, — источник расхождений: вызывающие
        уже умеют читать ``creation_unavailable`` и не должны учить четвёртое
        слово ради невыложенной половины.
        """
        _with_outcome(monkeypatch, CatalogSalonSpecialistDoorAbsent("no such route"))
        with pytest.raises(CatalogIdentityUnavailable) as absent:
            ensure_catalog_specialist_identity(salon_master)

        homeless_master = CatalogMaster(name="Без тенанта")
        with pytest.raises(CatalogIdentityUnavailable) as homeless:
            ensure_catalog_specialist_identity(homeless_master)

        assert absent.value.reason == REASON_CREATION_UNAVAILABLE
        assert homeless.value.reason == REASON_CREATION_UNAVAILABLE


class TestОтказыКаталогаИмеютСвоиИмена:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (CatalogProvisioningTokenMissing("no token"), REASON_TOKEN_MISSING),
            (CatalogProvisioningRefused("403"), REASON_REFUSED),
            (CatalogClientError("422"), REASON_CLIENT_ERROR),
            (CatalogTransportError("boom"), REASON_TRANSPORT_ERROR),
        ],
        ids=["token_missing", "refused", "client_error", "transport_error"],
    )
    def test_каждый_отказ_под_своим_именем(
        self,
        salon_master: CatalogMaster,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        expected: str,
    ) -> None:
        _with_outcome(monkeypatch, error)

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(salon_master)

        assert exc.value.reason == expected

    @pytest.mark.parametrize(
        "reason", ["tenant_not_found", "tenant_is_solo", "claim_bound_elsewhere"]
    )
    def test_отказ_каталога_сохраняет_его_собственный_хвост(
        self, salon_master: CatalogMaster, monkeypatch: pytest.MonkeyPatch, reason: str
    ) -> None:
        _with_outcome(
            monkeypatch,
            CatalogSalonSpecialistRefused(f"refused ({reason})", reason=reason),
        )

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(salon_master)

        assert exc.value.reason == f"conflict:{reason}"

    def test_отказ_каталога_не_путается_с_отсутствием_двери(
        self, salon_master: CatalogMaster, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Два 404 с одного адреса означают противоположное.

        «Салона нет» чинит тот, кто прислал чужой ``tenant_id``; «маршрута
        нет» чинит выкладка. Слитые в одно имя, они дают журнал, полный
        «салон не найден» на салонах, которые есть.
        """
        _with_outcome(
            monkeypatch,
            CatalogSalonSpecialistRefused("refused", reason="tenant_not_found"),
        )

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(salon_master)

        assert exc.value.reason != REASON_CREATION_UNAVAILABLE


class TestСолоСтрокаВСалоннуюДверьНеУходит:
    def test_связь_есть_а_человека_нет_остаётся_creation_unavailable(
        self, tenant: Tenant, salon_master: CatalogMaster, door: _FakeDoor
    ) -> None:
        """Иначе мы обменяли бы честное имя на чужое (``tenant_is_solo``)."""
        from apps.identity.models import BotUser
        from apps.identity.services.solo_identity_link import open_link

        person = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=f"s-{uuid.uuid4().hex[:8]}"
        )
        open_link(salon_master, bot_user=person, tenant=tenant)
        salon_master.linked_bot_user = None
        salon_master.save(update_fields=["linked_bot_user"])

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(salon_master)

        assert exc.value.reason == REASON_CREATION_UNAVAILABLE
        assert door.calls == [], "соло-строка салонную дверь не трогает"


class TestСловарьПричинНеРазъехался:
    def test_все_имена_салонной_ветви_есть_в_перечне(self) -> None:
        """Без этого узла ветвь могла бы вернуть имя, которого нет в словаре."""
        used = {
            REASON_TOKEN_MISSING,
            REASON_REFUSED,
            REASON_CLIENT_ERROR,
            REASON_TRANSPORT_ERROR,
            REASON_CREATION_UNAVAILABLE,
        }

        assert used <= set(identity_mod.ALL_IDENTITY_REASONS)
