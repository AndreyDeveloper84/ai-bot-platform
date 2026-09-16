"""Идемпотентная гарантия catalog identity: reuse / heal / create / именованный отказ.

Предмет — ``apps.catalog.identity.ensure_catalog_specialist_identity``.
Стенд продолжает идиому ``apps/identity/tests/test_solo_catalog_provisioning_1830.py``
(``_FakeCatalog`` как контекстный менеджер, подмена через
``monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", ...)``),
второго стенда для того же предмета не заводит.

Что здесь доказывается и чего НЕЛЬЗЯ доказать:

* «повтор дубля не создаёт» проверяется **счётчиком вызовов каталога**: при
  непустой колонке вызова не происходит вовсе. Это то, чем владеет код;
* «``created=True`` ⇒ в каталоге появилась новая строка» проверить **нельзя**:
  провижининг возвращает «причина или ``None``» и 201 от 200 не отличает.
  Поэтому ``created`` здесь означает «этот вызов выполнял провижининг», и
  узлы утверждают ровно это;
* щель между двумя успешными записями проверяется **падением между ними**, а
  не отсутствием слова ``transaction`` в модуле: второе верно и дефекта не
  адресует.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Any

import pytest

from apps.catalog.identity import (
    ALL_IDENTITY_REASONS,
    REASON_CREATION_UNAVAILABLE,
    CatalogIdentityUnavailable,
    ensure_catalog_specialist_identity,
)
from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import (
    CatalogClientError,
    CatalogProvisioningRefused,
    CatalogProvisioningTokenMissing,
    CatalogSoloProvisioningRefused,
    CatalogTransportError,
    ProvisionedSoloWorkspaceDTO,
)
from apps.identity.models import BotUser, SoloIdentityLink
from apps.identity.services import solo_catalog_provisioning
from apps.identity.services.solo_identity_link import open_link
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SPECIALIST_ID = uuid.UUID("c0a10600-0000-4000-8000-000000000001")


def _ts() -> datetime:
    return datetime(2026, 9, 16, 12, 0, tzinfo=dt_timezone.utc)


class _FakeCatalog:
    """Подмена ``CatalogHttpClient``: считает вызовы, отвечает заданным исходом."""

    def __init__(self, outcome: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome

    def __enter__(self) -> "_FakeCatalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_solo_workspace(self, **kwargs: Any) -> ProvisionedSoloWorkspaceDTO:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return ProvisionedSoloWorkspaceDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            slug=kwargs["slug"],
            specialist_id=SPECIALIST_ID,
            user_id=uuid.uuid4(),
            status="draft",
            created=True,
        )


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ensure-identity", name="Ensure Identity")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"ensure-{uuid.uuid4().hex[:8]}",
        ayla_user_id=str(uuid.uuid4()),
    )


@pytest.fixture
def salon_master(tenant: Tenant) -> CatalogMaster:
    """Мастер, заведённый админом салона: связи нет и быть не должно."""
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-9001,
        external_updated_at=_ts(),
        name="Анна Салонная",
    )


@pytest.fixture
def solo_master(tenant: Tenant, bot_user: BotUser) -> CatalogMaster:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-9002,
        external_updated_at=_ts(),
        name="Ольга Соло",
        linked_bot_user=bot_user,
    )
    open_link(master, bot_user=bot_user, tenant=tenant)
    return master


@pytest.fixture
def catalog(monkeypatch) -> _FakeCatalog:
    fake = _FakeCatalog()
    monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: fake)
    return fake


class TestReuseAsksTheCatalogNothing:
    def test_a_mirror_that_already_knows_its_id_is_returned_as_is(
        self, salon_master: CatalogMaster, catalog: _FakeCatalog
    ):
        salon_master.catalog_specialist_id = SPECIALIST_ID
        salon_master.save(update_fields=["catalog_specialist_id"])

        identity = ensure_catalog_specialist_identity(salon_master)

        assert identity.specialist_id == str(SPECIALIST_ID)
        assert identity.created is False
        assert catalog.calls == [], "reuse обязан обойтись без каталога"

    def test_a_repeat_call_creates_no_second_identity(
        self, solo_master: CatalogMaster, bot_user: BotUser, catalog: _FakeCatalog
    ):
        """ACCEPTANCE: повтор на том же человеке дубля не создаёт."""
        first = ensure_catalog_specialist_identity(solo_master, bot_user=bot_user)
        second = ensure_catalog_specialist_identity(solo_master, bot_user=bot_user)

        assert first.specialist_id == second.specialist_id
        assert second.created is False, "второй вызов провижининг не выполнял"
        assert len(catalog.calls) == 1, "каталог позван ровно один раз"
        assert (
            CatalogMaster.all_tenants.filter(
                tenant=solo_master.tenant, catalog_specialist_id=SPECIALIST_ID
            ).count()
            == 1
        )


class TestHealRepairsWhatTheMirrorLost:
    def test_the_link_knows_the_id_and_the_mirror_gets_it_back(
        self, solo_master: CatalogMaster, catalog: _FakeCatalog
    ):
        """Факт есть на связи — это reuse, а не create: каталог не зовём.

        Ровно состояние, которое оставляла разорванная пара ``save()``.
        Без этой ветви операция ответила бы ``creation_unavailable`` там, где
        identity существует, — устойчивая неправда на живых данных.
        """
        link = solo_master.identity_link
        link.catalog_specialist_id = SPECIALIST_ID
        link.save(update_fields=["catalog_specialist_id"])
        assert solo_master.catalog_specialist_id is None  # POSITIVE: зеркало пусто

        identity = ensure_catalog_specialist_identity(solo_master)

        assert identity.specialist_id == str(SPECIALIST_ID)
        assert identity.created is False
        assert catalog.calls == [], (
            "id уже выдан каталогом — второй вызов завёл бы второй workspace"
        )
        solo_master.refresh_from_db(fields=["catalog_specialist_id"])
        assert solo_master.catalog_specialist_id == SPECIALIST_ID


class TestCreateTakesTheAnswerFromTheCatalog:
    def test_the_readback_lands_in_both_stores(
        self, solo_master: CatalogMaster, bot_user: BotUser, catalog: _FakeCatalog
    ):
        identity = ensure_catalog_specialist_identity(solo_master, bot_user=bot_user)

        assert identity.specialist_id == str(SPECIALIST_ID)
        assert identity.created is True, "этот вызов выполнял провижининг"
        solo_master.refresh_from_db(fields=["catalog_specialist_id"])
        assert solo_master.catalog_specialist_id == SPECIALIST_ID
        link = SoloIdentityLink.objects.get(master=solo_master)
        assert link.catalog_specialist_id == SPECIALIST_ID
        assert link.catalog_provisioned_at is not None


class TestSalonMasterHasNoDoorAtAll:
    def test_no_link_means_creation_unavailable_not_a_silent_none(
        self, salon_master: CatalogMaster, catalog: _FakeCatalog
    ):
        """У салонного мастера двери создания нет ПО УСТРОЙСТВУ, и это названо."""
        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(salon_master)

        assert exc.value.reason == REASON_CREATION_UNAVAILABLE
        assert catalog.calls == [], "двери нет — звонить некуда"
        assert not SoloIdentityLink.objects.filter(master=salon_master).exists()


class TestEveryRefusalHasItsOwnName:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (CatalogProvisioningTokenMissing("no token"), "token_missing"),
            (CatalogProvisioningRefused("403"), "refused"),
            (CatalogClientError("422"), "client_error"),
            (CatalogTransportError("boom"), "transport_error"),
        ],
        ids=["token_missing", "refused", "client_error", "transport_error"],
    )
    def test_the_catalog_refusal_surfaces_under_its_own_name(
        self,
        solo_master: CatalogMaster,
        bot_user: BotUser,
        monkeypatch,
        error: Exception,
        expected: str,
    ):
        fake = _FakeCatalog(outcome=error)
        monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: fake)

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(solo_master, bot_user=bot_user)

        assert exc.value.reason == expected

    def test_a_conflict_keeps_the_catalogs_own_tail(
        self, solo_master: CatalogMaster, bot_user: BotUser, monkeypatch
    ):
        # ``reason`` — keyword-only (``http_client.py:352``), и прод поднимает
        # его именно так (``:877-879``). Позиционный вызов упал бы TypeError
        # ещё на сборе параметров, а не на предмете.
        fake = _FakeCatalog(
            outcome=CatalogSoloProvisioningRefused(
                "Ayla solo-workspaces: refused (slug_taken)", reason="slug_taken"
            )
        )
        monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: fake)

        with pytest.raises(CatalogIdentityUnavailable) as exc:
            ensure_catalog_specialist_identity(solo_master, bot_user=bot_user)

        assert exc.value.reason.startswith("conflict:")

    def test_the_written_reason_survives_the_refusal(
        self, solo_master: CatalogMaster, bot_user: BotUser, monkeypatch
    ):
        """ACCEPTANCE: отказ не стирает объяснение, ради которого туда смотрят.

        Обёрни мы вызов в транзакцию, исключение откатило бы запись причины
        на связь — дефект, который прошёл бы все остальные узлы: путь отказа
        редко проверяют на то, что после него ОСТАЛОСЬ.
        """
        fake = _FakeCatalog(outcome=CatalogTransportError("boom"))
        monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: fake)

        with pytest.raises(CatalogIdentityUnavailable):
            ensure_catalog_specialist_identity(solo_master, bot_user=bot_user)

        link = SoloIdentityLink.objects.get(master=solo_master)
        assert link.catalog_provisioning_refusal == "transport_error"


class TestTheTwoWritesAreOneWrite:
    def test_a_failure_between_them_leaves_neither(
        self, solo_master: CatalogMaster, bot_user: BotUser, catalog: _FakeCatalog, monkeypatch
    ):
        """Щель проверяется ПАДЕНИЕМ МЕЖДУ записями, а не отсутствием слова.

        Красный до правки: связь сохраняла ``catalog_specialist_id``, а
        зеркало нет — и читатели (``specialist_ref``) видели «профиля нет»
        при живом профиле.
        """
        link = SoloIdentityLink.objects.get(master=solo_master)
        mirror = link.master  # populate the FK cache: тот же экземпляр внутри

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("torn between the two saves")

        monkeypatch.setattr(mirror, "save", boom)

        with pytest.raises(RuntimeError):
            solo_catalog_provisioning.provision_catalog_workspace(
                link,
                tenant=solo_master.tenant,
                bot_user=bot_user,
                display_name="Ольга Соло",
            )

        link.refresh_from_db()
        assert link.catalog_specialist_id is None, "первая запись пережила падение второй"
        assert link.catalog_provisioned_at is None


class TestTheReasonListCannotDriftSilently:
    def test_the_registry_and_the_literal_agree(self):
        """Узел полноты по образцу ``ALL_SALE_BLOCKS``.

        Четыре потока кодируют против этого набора; без сторожа он
        разъехался бы молча.
        """
        declared = {
            "token_missing",
            "refused",
            "conflict",
            "client_error",
            "transport_error",
            "creation_unavailable",
        }
        assert set(ALL_IDENTITY_REASONS) == declared
