"""Провести ресепшна в каталог из Admin Mini App (DRF-2833, B3).

Решение владельца 07.10 (лист решений п.22, вариант B): ресепшна назначают
отдельным действием в Mini App. Механика — главное окно 20.10: только
человек с активной ``TenantStaff`` ``receptionist`` в ЭТОМ салоне; только
владелец (ruling 22.09); снятие — существующий ``staff/revoke/``.

  - l1: владелец проводит ресепшна — каталог спрошен о НЁМ, назначающий —
    сам владелец из init-data; аудит успеха с ``role=receptionist``;
  - l2: повтор — 200 ``created: false``, второй строки аудита нет;
  - l3: администратор и ресепшн не проводят — 403, каталог не спрошен;
  - l4: не ресепшн (администратор, клиент, отозванный ресепшн) — 409
    ``not_a_receptionist``, каталог не спрошен;
  - l5: человек чужого салона — 404, каталог не спрошен (протечка между
    салонами);
  - l6: себя — 403;
  - l7: каталог отказал ``assigner_not_admin`` (владелец не admin салона в
    каталоге) — 409 с подсказкой по имени, отказ в аудите;
  - m1: узел из листа — ресепшн в ``masters_list`` не видит мастеров
    чужого салона.

Каталог — заглушка на шве ``ensure_catalog_salon_receptionist``: файл
доказывает ручку. Проводная форма — ``apps/identity/tests/test_receptionist_link_2833.py``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.events.vocabulary import STAFF_SALON_ADMIN_LINK_REFUSED, STAFF_SALON_ADMIN_LINKED
from apps.identity.services import salon_admin_link
from apps.tenancy.models import TenantStaff

from .conftest import _make_bot_user, init_data_header, make_master

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog_admin_link_stub")]


@dataclass
class _DeskStub:
    #: (tenant slug, person pk, assigned_by pk, actor_label)
    calls: list[tuple[str, str, str, str]] = field(default_factory=list)
    refuse_with: str | None = None
    created: bool = True


@pytest.fixture
def desk_stub(monkeypatch) -> _DeskStub:
    stub = _DeskStub()

    def _fake(*, tenant, bot_user, assigned_by, actor_label, http_client=None):
        stub.calls.append((tenant.slug, str(bot_user.pk), str(assigned_by.pk), actor_label))
        if stub.refuse_with is not None:
            raise salon_admin_link.CatalogAdminLinkRefused(
                stub.refuse_with,
                correlation_id="stub-corr",
                tenant=tenant,
                bot_user=bot_user,
                actor_label=actor_label,
                role="receptionist",
            )
        return salon_admin_link.CatalogAdminLinkOutcome(
            ayla_user_id=uuid.uuid4(),
            relationship_id=uuid.uuid4(),
            created=stub.created,
            correlation_id="stub-corr",
            idempotency_key="stub-key",
        )

    monkeypatch.setattr(salon_admin_link, "ensure_catalog_salon_receptionist", _fake)
    return stub


def _link(client: Client, bot_user_id, *, user_id: str = "5001"):
    return client.post(
        reverse("admin_api:staff_receptionist_link"),
        data=json.dumps({"bot_user_id": str(bot_user_id)}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _audit(action: str, person) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=action, target_id=person.pk))


class TestOwnerLinksTheDesk:
    def test_l1_owner_links_a_receptionist_as_herself(
        self, client, owner_bot_user, tenant, receptionist_bot_user, desk_stub
    ):
        resp = _link(client, receptionist_bot_user.id)

        assert resp.status_code == 200, resp.content
        assert resp.json() == {"linked": True, "created": True}
        assert desk_stub.calls == [
            (
                tenant.slug,
                str(receptionist_bot_user.pk),
                str(owner_bot_user.pk),
                f"salon_miniapp:bot_user:{owner_bot_user.pk}",
            )
        ]
        (row,) = _audit(STAFF_SALON_ADMIN_LINKED, receptionist_bot_user)
        assert row.payload["role"] == "receptionist"
        assert row.payload["surface"] == "salon_miniapp"
        # Роль в боте не тронута — действие дозаводит только каталог.
        assert set(
            TenantStaff.all_tenants.filter(
                bot_user=receptionist_bot_user, deactivated_at__isnull=True
            ).values_list("role", flat=True)
        ) == {"receptionist"}

    def test_l2_a_repeat_answers_calmly_and_audits_once(
        self, client, owner_bot_user, tenant, receptionist_bot_user, desk_stub
    ):
        assert _link(client, receptionist_bot_user.id).json()["created"] is True
        desk_stub.created = False

        again = _link(client, receptionist_bot_user.id)

        assert again.status_code == 200
        assert again.json() == {"linked": True, "created": False}
        assert len(_audit(STAFF_SALON_ADMIN_LINKED, receptionist_bot_user)) == 1


class TestWhoMayNot:
    @pytest.mark.parametrize("caller", ["5002", "5003"])
    def test_l3_admin_and_receptionist_may_not(
        self,
        client,
        owner_bot_user,
        tenant,
        admin_bot_user,
        receptionist_bot_user,
        desk_stub,
        caller,
    ):
        target = _make_bot_user(tenant, channel_user_id="5010", display_name="Новенькая")
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=target, role="receptionist")

        resp = _link(client, target.id, user_id=caller)

        assert resp.status_code == 403
        assert desk_stub.calls == []

    def test_l6_not_yourself(self, client, owner_bot_user, tenant, desk_stub):
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=owner_bot_user, role="receptionist")

        resp = _link(client, owner_bot_user.id)

        assert resp.status_code == 403
        assert desk_stub.calls == []


class TestWhomNot:
    def test_l4_an_admin_is_not_a_receptionist(
        self, client, owner_bot_user, tenant, admin_bot_user, desk_stub
    ):
        resp = _link(client, admin_bot_user.id)

        assert resp.status_code == 409
        assert resp.json()["error"] == "not_a_receptionist"
        assert desk_stub.calls == []

    def test_l4_a_customer_is_not_a_receptionist(
        self, client, owner_bot_user, tenant, customer_bot_user, desk_stub
    ):
        resp = _link(client, customer_bot_user.id)

        assert resp.status_code == 409
        assert resp.json()["error"] == "not_a_receptionist"
        assert desk_stub.calls == []

    def test_l4_a_revoked_receptionist_is_not_linked(
        self, client, owner_bot_user, tenant, receptionist_bot_user, desk_stub
    ):
        revoked = client.post(
            reverse("admin_api:staff_revoke"),
            data=json.dumps({"bot_user_id": str(receptionist_bot_user.id)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert revoked.status_code == 200, revoked.content

        resp = _link(client, receptionist_bot_user.id)

        assert resp.status_code == 409
        assert resp.json()["error"] == "not_a_receptionist"
        assert desk_stub.calls == []

    def test_l5_a_receptionist_of_another_salon_is_not_found(
        self, client, owner_bot_user, tenant, other_tenant, desk_stub
    ):
        stranger = _make_bot_user(other_tenant, channel_user_id="5020", display_name="Чужая")
        TenantStaff.all_tenants.create(tenant=other_tenant, bot_user=stranger, role="receptionist")

        resp = _link(client, stranger.id)

        assert resp.status_code == 404
        assert desk_stub.calls == []


class TestCatalogRefusal:
    def test_l7_owner_without_catalog_admin_is_refused_by_name(
        self, client, owner_bot_user, tenant, receptionist_bot_user, desk_stub
    ):
        desk_stub.refuse_with = "assigner_not_admin"

        resp = _link(client, receptionist_bot_user.id)

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "catalog_admin_link_refused"
        assert body["detail"] == "assigner_not_admin"
        assert "администратору салона нужна связь с каталогом" in body["details"]["hint"]
        (refused,) = _audit(STAFF_SALON_ADMIN_LINK_REFUSED, receptionist_bot_user)
        assert refused.payload["role"] == "receptionist"
        assert refused.payload["reason"] == "assigner_not_admin"
        # empty-assert-ok: the refusal row for this same person is asserted just above; a success row next to it would be a desk the catalog never opened
        assert _audit(STAFF_SALON_ADMIN_LINKED, receptionist_bot_user) == []


class TestDeskSeesOnlyItsSalon:
    def test_m1_receptionist_does_not_see_another_salons_masters(
        self, client, owner_bot_user, tenant, other_tenant, receptionist_bot_user
    ):
        ours = make_master(tenant, name="Наша Мастер")
        theirs = make_master(other_tenant, name="Чужая Мастер", external_id=4242)

        resp = client.get(
            reverse("admin_api:masters_list"), HTTP_AUTHORIZATION=init_data_header("5003")
        )

        assert resp.status_code == 200, resp.content
        ids = {str(m["id"]) for m in resp.json()["items"]}
        assert str(ours.id) in ids  # присутствие: ресепшн видит своих
        assert str(theirs.id) not in ids
