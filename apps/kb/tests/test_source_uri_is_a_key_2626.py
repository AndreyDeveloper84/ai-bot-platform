"""The KB document key is built from an identity the row really has (DRF-2626).

``_project_one_mirror`` finds a document by ``(tenant, source_uri, version=1)``.
The address used to be ``mysite://services/{external_id}``, and the sync
(S3B, #1044/#1122) keys the service mirror on the Ayla UUID and leaves
``external_id`` NULL. So every service of a salon projected to
``mysite://services/None``: the first created the document, each next one
overwrote its body, and the salon's knowledge held ONE service while every
write reported success.

The pre-existing tests never saw it: their fixtures set ``external_id=1`` by
hand — the legacy form, not the form the sync writes. The services here are
written by the sync's own upserter (``upsert_salon_services``), so NULL is what
the writer leaves, not what the test claims.

Nodes — pairs that must differ (a node "the document was created" passes on
the defect itself: it is created every time, over the previous one):

* k1 — two services of one salon → TWO documents, TWO addresses, each body its
  own service;
* k2 — the same for masters synced from Ayla (``catalog_specialist_id``);
* k3 — a row with neither the Ayla id nor the legacy pk gets its own mirror
  key — two such rows, two documents;
* k4 — the legacy pk keeps its old address byte for byte (documents projected
  under it keep their key);
* k5 — a second pass is idempotent under the new keys;
* k6 — no address of a mixed salon ends in ``/None``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogFaq, CatalogMaster, CatalogService
from apps.catalog.services.http_client import CatalogSalonServiceDTO
from apps.catalog.services.upserter import upsert_salon_services
from apps.kb.models import KbDocType, KbDocument
from apps.kb.projectors import project_tenant_catalog
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SVC_A = "3f1b2c4d-5e6f-4a70-8b91-a2b3c4d5e6f7"
SVC_B = "9a8b7c6d-5e4f-4321-9abc-def012345678"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="kb-key-2626", name="Салон")


def _ts() -> datetime:
    return datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _synced_services(tenant: Tenant) -> None:
    dtos = [
        CatalogSalonServiceDTO(
            ayla_service_id=SVC_A, external_updated_at=_ts(), name="Массаж спины"
        ),
        CatalogSalonServiceDTO(ayla_service_id=SVC_B, external_updated_at=_ts(), name="Маникюр"),
    ]
    with tenant_scope(tenant):
        result = upsert_salon_services(tenant, dtos)
    assert result.created == 2 and not result.errors


def _docs(tenant: Tenant, doc_type: str) -> dict[str, str]:
    return dict(
        KbDocument.all_tenants.filter(tenant=tenant, doc_type=doc_type).values_list(
            "source_uri", "content"
        )
    )


class TestK1TwoServicesTwoDocuments:
    def test_each_service_keeps_its_own_document(self, tenant: Tenant) -> None:
        _synced_services(tenant)
        # The writer leaves the legacy pk empty — the condition of the defect.
        assert list(
            CatalogService.all_tenants.filter(tenant=tenant).values_list("external_id", flat=True)
        ) == [None, None]

        result = project_tenant_catalog(tenant)

        docs = _docs(tenant, KbDocType.SERVICE)
        assert sorted(docs) == sorted([f"ayla://services/{SVC_A}", f"ayla://services/{SVC_B}"])
        assert "Массаж спины" in docs[f"ayla://services/{SVC_A}"]
        assert "Маникюр" in docs[f"ayla://services/{SVC_B}"]
        assert result[KbDocType.SERVICE].created == 2
        assert result[KbDocType.SERVICE].updated == 0


class TestK2TwoMastersTwoDocuments:
    def test_masters_from_ayla(self, tenant: Tenant) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        for name, cid in zip(("Анна", "Ольга"), ids, strict=True):
            CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=None,
                external_updated_at=_ts(),
                name=name,
                catalog_specialist_id=cid,
            )
        project_tenant_catalog(tenant)
        docs = _docs(tenant, KbDocType.MASTER)
        assert sorted(docs) == sorted(f"ayla://masters/{cid}" for cid in ids)
        assert "Анна" in docs[f"ayla://masters/{ids[0]}"]
        assert "Ольга" in docs[f"ayla://masters/{ids[1]}"]


class TestK3NoIdentityStillAKey:
    def test_two_rows_without_any_upstream_id(self, tenant: Tenant) -> None:
        rows = [
            CatalogFaq.all_tenants.create(
                tenant=tenant, external_id=None, external_updated_at=_ts(), question=q, answer="a"
            )
            for q in ("Где вы?", "Когда открыты?")
        ]
        project_tenant_catalog(tenant)
        docs = _docs(tenant, KbDocType.FAQ)
        assert sorted(docs) == sorted(f"mirror://faqs/{r.pk}" for r in rows)


class TestK4LegacyKeyUnchanged:
    def test_legacy_pk_keeps_its_address(self, tenant: Tenant) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant, external_id=7, external_updated_at=_ts(), slug="s", name="Старая"
        )
        project_tenant_catalog(tenant)
        assert list(_docs(tenant, KbDocType.SERVICE)) == ["mysite://services/7"]


class TestK5SecondPassIdempotent:
    def test_nothing_moves_on_the_second_pass(self, tenant: Tenant) -> None:
        _synced_services(tenant)
        project_tenant_catalog(tenant)
        again = project_tenant_catalog(tenant)
        assert again[KbDocType.SERVICE].created == 0
        assert again[KbDocType.SERVICE].updated == 0
        assert again[KbDocType.SERVICE].unchanged == 2


class TestK6NoDegenerateAddress:
    def test_mixed_salon_has_no_none_address(self, tenant: Tenant) -> None:
        _synced_services(tenant)
        CatalogService.all_tenants.create(
            tenant=tenant, external_id=7, external_updated_at=_ts(), slug="s", name="Старая"
        )
        CatalogMaster.all_tenants.create(
            tenant=tenant, external_id=None, external_updated_at=_ts(), name="Без ключа"
        )
        project_tenant_catalog(tenant)
        uris = list(
            KbDocument.all_tenants.filter(tenant=tenant).values_list("source_uri", flat=True)
        )
        assert len(uris) == 4
        assert len(set(uris)) == 4
        assert not [u for u in uris if u.endswith("/None")]
