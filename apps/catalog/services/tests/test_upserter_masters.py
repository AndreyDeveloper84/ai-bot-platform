"""Catalog masters upserter tests — Ayla specialists → CatalogMaster (S3B).

Pins the mapping (id←Ayla id, user_id→ayla_user_id, display_name→name,
bio→bio, experience_years→str, rating decimal, reviews_count→count,
is_active←status==active AND is_available), rerun idempotency, the
upsert-only missing-row policy, and tenant isolation. Platform-owned
fields (invite_status, photo_url) must survive sync untouched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import (
    CatalogSpecialistDTO,
    CatalogSpecialistServiceDTO,
)
from apps.catalog.services.upserter import (
    UpsertResult,
    upsert_master_services,
    upsert_specialists,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cat-mst", name="Cat Masters")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="cat-mst-b", name="Cat Masters B")


def _dto(
    ayla_master_id: str | None = None,
    *,
    user_id: str | None = None,
    name: str = "Анна Иванова",
    bio: str = "Топ-мастер",
    experience_years: int | None = 5,
    rating: str = "4.90",
    reviews_count: int = 42,
    status: str = "active",
    is_available: bool = True,
    tenant: str | None = None,
) -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=ayla_master_id or str(uuid.uuid4()),
        user_id=user_id or str(uuid.uuid4()),
        name=name,
        tenant=tenant,
        external_updated_at=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        bio=bio,
        experience=str(experience_years) if experience_years is not None else "",
        rating=Decimal(rating) if rating else None,
        review_count=reviews_count,
        is_active=(status == "active" and is_available),
        raw={"id": ayla_master_id, "display_name": name},
    )


def _service(tenant: Tenant, ayla_service_id: str) -> CatalogService:
    """Услуга, на которую ссылается ребро мастер↔услуга."""

    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        ayla_service_id=ayla_service_id,
    )


def _edge_dto(
    tenant: Tenant,
    ayla_service_id: str,
    specialist: str,
    user_id: str,
) -> CatalogSpecialistServiceDTO:
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=str(uuid.uuid4()),
        salon_service=ayla_service_id,
        specialist=specialist,
        external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        tenant=str(tenant.id),
        user_id=user_id,
    )


class TestCreate:
    def test_creates_row_keyed_by_ayla_uuid(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        res = upsert_specialists(tenant, [_dto(mid, user_id=uid)])

        assert isinstance(res, UpsertResult)
        assert (res.created, res.updated, res.errors) == (1, 0, [])
        m = CatalogMaster.all_tenants.get(tenant=tenant, id=mid)
        assert m.name == "Анна Иванова"
        assert str(m.ayla_user_id) == uid
        assert m.bio == "Топ-мастер"
        assert m.experience == "5"
        assert m.rating == Decimal("4.90")
        assert m.review_count == 42
        assert m.is_active is True

    def test_mapping_inactive_when_not_available(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, status="active", is_available=False)])
        assert CatalogMaster.all_tenants.get(id=mid).is_active is False

    def test_experience_none_maps_empty_string(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, experience_years=None)])
        assert CatalogMaster.all_tenants.get(id=mid).experience == ""

    def test_platform_fields_untouched_on_create(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        m = CatalogMaster.all_tenants.get(id=mid)
        # Platform-owned defaults must not be overwritten by sync.
        assert m.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert m.photo_url == ""


class TestUpdate:
    def test_second_upsert_updates_same_row(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, name="Старое имя", rating="4.10")])
        res = upsert_specialists(tenant, [_dto(mid, name="Новое имя", rating="4.95")])

        assert (res.created, res.updated) == (0, 1)
        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1
        m = CatalogMaster.all_tenants.get(id=mid)
        assert m.name == "Новое имя"
        assert m.rating == Decimal("4.95")

    def test_platform_fields_survive_update(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        m = CatalogMaster.all_tenants.get(id=mid)
        m.photo_url = "https://cdn.test/photo.jpg"
        m.save(update_fields=["photo_url"])

        upsert_specialists(tenant, [_dto(mid, name="Обновлён")])

        m.refresh_from_db()
        assert m.photo_url == "https://cdn.test/photo.jpg"
        assert m.name == "Обновлён"


class TestIdempotency:
    def test_rerun_same_data_updates_not_duplicates(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        res = upsert_specialists(tenant, [_dto(mid)])
        assert (res.created, res.updated) == (0, 1)
        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1

    def test_missing_from_feed_row_kept(self, tenant: Tenant) -> None:
        """Upsert-only policy (same as salon-services): a master that
        disappears from the feed is NOT deactivated or deleted by sync."""
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        res = upsert_specialists(tenant, [])  # empty feed
        assert (res.created, res.updated) == (0, 0)
        m = CatalogMaster.all_tenants.get(id=mid)
        assert m.is_active is True


class TestTenantIsolation:
    def test_same_ayla_id_second_tenant_cannot_hijack(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        """``CatalogMaster.id`` is the canonical Ayla UUID and the GLOBAL
        primary key — a specialist lives in exactly one tenant of the
        mirror (pilot single-tenant). Upserting the same id into a second
        tenant must NOT steal or duplicate the row: it lands as a per-row
        error, the first tenant's row is untouched."""
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, name="В салоне А")])

        res = upsert_specialists(tenant_b, [_dto(mid, name="В салоне Б")])

        assert (res.created, res.updated) == (0, 0)
        assert len(res.errors) == 1  # PK collision, isolated per-row
        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1
        m = CatalogMaster.all_tenants.get(id=mid)
        assert m.tenant_id == tenant.id
        assert m.name == "В салоне А"

    def test_pk_collision_names_the_cause(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """DRF-1313 — the collision above is not hypothetical any more.

        A tenant-blind pull left five pilot masters under the wrong salon, and
        because ``CatalogMaster.id`` is the global PK those rows now hold the
        ids hostage: the *corrected* sync still cannot create each master under
        its real salon while the wrong salon owns the row. Every beat fails
        here until someone removes it — so the failure has to say so, not
        surface as a bare duplicate-key string.

        This is the seam between the code fix and the data cleanup. The upsert
        deliberately does not re-parent or delete: whose row that is, is an
        owner decision.
        """
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, name="В салоне А")])

        res = upsert_specialists(tenant_b, [_dto(mid, name="В салоне Б")])

        assert res.errors[0]["reason"] == "held_by_other_tenant"
        assert res.errors[0]["ayla_master_id"] == mid
        assert CatalogMaster.all_tenants.get(id=mid).tenant_id == tenant.id


class TestCrossTenantGuard:
    """DRF-1313 — the payload's own tenant is re-checked before any write.

    ``fetch_specialists`` sends ``?tenant=`` now, so in principle the feed is
    already scoped. In practice the whole defect was a filter that did not
    apply and said nothing about it, so the mirror verifies rather than trusts
    — the same guard the edge upsert has carried since DRF-945.
    """

    def test_foreign_tenant_payload_is_skipped_not_written(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        mid = str(uuid.uuid4())
        res = upsert_specialists(
            tenant,
            [_dto(mid, name="Чужой мастер", tenant=str(tenant_b.id))],
        )

        assert (res.created, res.updated, res.skipped) == (0, 0, 1)
        assert res.errors == []
        assert not CatalogMaster.all_tenants.filter(id=mid).exists()

    def test_matching_tenant_payload_is_written(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        res = upsert_specialists(tenant, [_dto(mid, tenant=str(tenant.id))])

        assert (res.created, res.skipped) == (1, 0)
        assert CatalogMaster.all_tenants.get(id=mid).tenant_id == tenant.id

    def test_absent_tenant_payload_is_written(self, tenant: Tenant) -> None:
        """No ``tenant`` on the row means *unverifiable*, not *foreign*.

        An Ayla deployed before the field exists must keep mirroring. Treating
        a missing value as a mismatch would turn a deploy-order skew into an
        outage — and the ordering (Ayla first, bot second) exists precisely so
        this window is survivable.
        """
        mid = str(uuid.uuid4())
        res = upsert_specialists(tenant, [_dto(mid, tenant=None)])

        assert (res.created, res.skipped) == (1, 0)
        assert CatalogMaster.all_tenants.get(id=mid).tenant_id == tenant.id

    def test_foreign_row_does_not_abort_the_batch(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """One bad row must not cost the salon its other masters."""
        good = _dto(str(uuid.uuid4()), tenant=str(tenant.id))
        foreign = _dto(str(uuid.uuid4()), tenant=str(tenant_b.id))

        res = upsert_specialists(tenant, [foreign, good])

        assert (res.created, res.skipped) == (1, 1)
        assert CatalogMaster.all_tenants.get(id=good.ayla_master_id)
        assert not CatalogMaster.all_tenants.filter(id=foreign.ayla_master_id).exists()


class TestErrorIsolation:
    def test_bad_row_does_not_abort_batch(self, tenant: Tenant) -> None:
        good = _dto(str(uuid.uuid4()))
        bad = CatalogSpecialistDTO(
            ayla_master_id=str(uuid.uuid4()),
            user_id=None,
            name="Bad",
            external_updated_at=None,  # type: ignore[arg-type]  # violates NOT NULL
        )
        res = upsert_specialists(tenant, [good, bad])
        assert res.created == 1
        assert len(res.errors) == 1
        assert res.errors[0]["ayla_master_id"] == bad.ayla_master_id


class TestDedupKeys:
    """DRF-1507 — один человек даёт одну строку мастера.

    Боевой отказ, ради которого эти тесты существуют: приглашение заводит
    строку с ``uuid4`` первичным ключом, синхронизация — вторую, с
    канонической Ayla ``SpecialistProfile.id``. ``resolve_master``
    (``apps/booking/master_notify.py``) ищет по ``id`` ИЛИ ``ayla_user_id`` и
    не находит ту, к которой привязан живой ``BotUser``, — мастер не получает
    ни одного уведомления о записи.

    Каждое отрицание здесь стоит рядом со своей положительной стражей: «двух
    строк не появилось» бессмысленно без «двое разных людей по-прежнему дают
    две» на тех же данных (DRF-1411).

    Второй ключ объёма, ``(tenant, max_handle)``, здесь не проверяется, потому
    что его в схеме нет: его писатель — ``apps/admin_api/views_invite.py`` —
    намеренно заводит вторую строку с тем же handle на протухшем приглашении,
    и ограничение без правки того файла превращает повторное приглашение в
    500. Файл занят DRF-1505, ключ уезжает туда.
    """

    def _invite_row(
        self,
        tenant: Tenant,
        *,
        user_id: str | None = None,
        max_handle: str = "",
        name: str = "Анна Иванова",
    ) -> CatalogMaster:
        """Строка, какой её заводит приглашение: свой uuid4, не Ayla id."""

        return CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=1_000_001,
            external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            name=name,
            ayla_user_id=user_id,
            max_handle=max_handle,
            invite_status=CatalogMaster.InviteStatus.PENDING,
            invite_token=uuid.uuid4(),
        )

    def test_sync_adopts_the_invite_row_instead_of_creating_a_second(
        self,
        tenant: Tenant,
    ) -> None:
        user_id = str(uuid.uuid4())
        invite = self._invite_row(tenant, user_id=user_id, max_handle="@anna")

        res = upsert_specialists(tenant, [_dto(str(uuid.uuid4()), user_id=user_id)])

        assert (res.created, res.updated) == (0, 1)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1
        invite.refresh_from_db()
        assert invite.name == "Анна Иванова"
        assert str(invite.ayla_user_id) == user_id
        # Платформенная половина строки синхронизацией не тронута — это она
        # делает мастера findable для resolve_master.
        assert invite.max_handle == "@anna"
        assert invite.invite_status == CatalogMaster.InviteStatus.PENDING

    def test_two_different_people_still_get_two_rows(self, tenant: Tenant) -> None:
        """Положительная стража к предыдущему на тех же данных."""

        first = _dto(str(uuid.uuid4()), user_id=str(uuid.uuid4()), name="Анна")
        second = _dto(str(uuid.uuid4()), user_id=str(uuid.uuid4()), name="Ирина")

        res = upsert_specialists(tenant, [first, second])

        assert (res.created, res.updated) == (2, 0)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2
        names = set(CatalogMaster.all_tenants.filter(tenant=tenant).values_list("name", flat=True))
        assert names == {"Анна", "Ирина"}

    def test_dedup_does_not_reach_across_salons(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """Один человек в двух салонах — две строки, по одной в каждом."""

        user_id = str(uuid.uuid4())
        self._invite_row(tenant_b, user_id=user_id, max_handle="@anna")

        res = upsert_specialists(tenant, [_dto(str(uuid.uuid4()), user_id=user_id)])

        assert (res.created, res.updated) == (1, 0)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1
        assert CatalogMaster.all_tenants.filter(tenant=tenant_b).count() == 1

    def test_canonical_id_still_wins_over_the_glue_key(self, tenant: Tenant) -> None:
        """Девять пилотных строк приехали по id — их путь не меняется."""

        user_id = str(uuid.uuid4())
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, user_id=user_id, name="Анна")])

        res = upsert_specialists(tenant, [_dto(mid, user_id=user_id, name="Анна Петрова")])

        assert (res.created, res.updated) == (0, 1)
        assert CatalogMaster.all_tenants.get(id=mid).name == "Анна Петрова"

    def test_row_without_ayla_user_id_is_not_glued_to_anything(self, tenant: Tenant) -> None:
        """Пустой ключ склейки не склеивает: NULL не равен NULL.

        Инвайт-строка без ``ayla_user_id`` — та самая, что сегодня остаётся
        невидимой. До заполнения ключа синхронизация про неё ничего не знает
        и заводит свою; это граница задачи (заполнение живёт в DRF-1505), и
        тест фиксирует её честно, а не делает вид, что её нет.
        """

        self._invite_row(tenant, user_id=None, max_handle="@anna")

        res = upsert_specialists(tenant, [_dto(str(uuid.uuid4()), user_id=str(uuid.uuid4()))])

        assert (res.created, res.updated) == (1, 0)
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2

    def test_database_refuses_a_second_row_on_the_same_ayla_user_id(
        self,
        tenant: Tenant,
    ) -> None:
        """Ключ склейки держится на уровне БД, а не только в коде пути."""

        user_id = str(uuid.uuid4())
        self._invite_row(tenant, user_id=user_id, max_handle="@anna")

        with pytest.raises(IntegrityError), transaction.atomic():
            CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=1_000_002,
                external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                name="Она же, второй раз",
                ayla_user_id=user_id,
                max_handle="@anna-2",
            )

        # Положительная стража: другой человек в том же салоне пишется.
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=1_000_003,
            external_updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            name="Ирина",
            ayla_user_id=str(uuid.uuid4()),
            max_handle="@irina",
        )
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2

    def test_edges_reach_the_adopted_row(self, tenant: Tenant) -> None:
        """Склейка не должна оставить мастера без услуг.

        Рёбра приезжают с ``specialist`` == каноническим Ayla id, которого у
        принятой строки в первичном ключе нет. Без запасного ключа мастер
        получил бы уведомления и потерял бронируемость — обмен одной поломки
        на другую.
        """

        user_id = str(uuid.uuid4())
        ayla_master_id = str(uuid.uuid4())
        invite = self._invite_row(tenant, user_id=user_id, max_handle="@anna")
        upsert_specialists(tenant, [_dto(ayla_master_id, user_id=user_id)])

        ayla_service_id = str(uuid.uuid4())
        _service(tenant, ayla_service_id)
        edge = _edge_dto(tenant, ayla_service_id, ayla_master_id, user_id)

        res = upsert_master_services(tenant, [edge])

        assert res.skipped == 0, res.errors
        assert MasterService.all_tenants.filter(master=invite).count() == 1

    def test_edges_still_refuse_a_specialist_nobody_mirrors(self, tenant: Tenant) -> None:
        """Положительная стража к предыдущему: запасной ключ не всеяден."""

        ayla_service_id = str(uuid.uuid4())
        _service(tenant, ayla_service_id)
        edge = _edge_dto(tenant, ayla_service_id, str(uuid.uuid4()), str(uuid.uuid4()))

        res = upsert_master_services(tenant, [edge])

        assert res.skipped == 1
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 0
